# Decision-Trail Harvesting (moat extra 2.5) — Design

**Date:** 2026-07-10 · **Status:** approved-autonomously along the recommended path (autonomous weekend run; D1 "what is a decision" surfaced for review); build-now (opt-in/default-off, no benchmark-path change, no dependency, no LLM).
**Thesis:** extend the shipped, validated harvest loop from **corrections** (fixing the agent's SQL) to **decisions** (durable analytical choices the analyst wants future sessions to respect). A decision becomes a `## Decisions` Scent section, promoted human-gated, retrieved automatically by `search_reference_docs` as future grounding — the compounding-memory loop applied to institutional knowledge, not just error-correction.

## 1. What already holds (verified this session)

- **The correction-harvest loop is shipped end-to-end** (M5 T2b + TUI-M3): `memory/correction_buffer.py::CorrectionBuffer` captures during a session; `ctrl+shift+h` → `SessionHarvester` drains + extracts → `Memory`s → `maze/harvest.py::cluster_corrections` (by `table_scope`) → `draft_harvested_sections` (heading `## Gotchas`, `source="harvested"`, contamination-audited fail-loud) → `HarvestReviewScreen` (approve/skip) → `apply_approved_sections` (project-layer, dedup-by-body, git-sha stamped, audit-before-write). Gated on `Profile.harvest_opt_in` (default False) via `harvest_controller.harvesting_enabled(is_interactive, profile_opt_in)`.
- **`MemoryKind.explicit_user_rule` EXISTS but has ZERO producers** (`memory/model.py:21`) — a dormant kind, exactly the T2b-extractor situation. It is the natural home for a decision. `Memory(id, profile, scope: MemoryScope{global_,table_,thread_}, kind, text, table_scope, source, created_at, ...)`; `MemoryStore.append`; `resolve_table_scope(sql, known_tables)` (sqlglot single-table attribution).
- `apply_approved_sections`, `HarvestReviewScreen`, and `search_reference_docs` are all **section-generic** — a `## Decisions` section flows through promotion, review, and retrieval with no changes to those components.

## 2. Decisions (each weighed; D1 surfaced for user review)

- **D1 — What a decision IS [SURFACED FOR REVIEW].** A decision = a **durable analytical choice, definition, or business rule the analyst wants future sessions to respect**, distinct from a *correction* (fixing the agent's mistake). Examples: "attribute revenue at order time, not ship time", "exclude `is_test` accounts from all metrics", "WAU = distinct users with ≥1 core action in the window". This is the product definition; if the user wants a broader/narrower notion (e.g. include per-Finding rationale), it adjusts D2's capture surface.
- **D2 — Capture is EXPLICIT, not LLM-guessed.** LLM-extracting "decisions" from chat has a high false-positive rate (most chat isn't a decision) and would add an LLM call + cost + contamination surface. v1 captures decisions explicitly: a `ctrl+shift+d` action opens a one-field `RecordDecisionScreen`; the analyst types the decision; on save it is stored verbatim as `Memory(kind=explicit_user_rule, text=<typed>, table_scope=resolve_table_scope(_last_sql, known_tables), scope=global_)` via `MemoryStore.append`. **No LLM** — the analyst's words are the decision. `table_scope` is auto-attributed from the last executed SQL when it resolves to a single known table, else global.
- **D3 — Promotion mirrors corrections exactly.** `maze/harvest.py` gains `draft_decision_sections(clusters) -> dict[str, list[Section]]` — heading `## Decisions`, `source="harvested"`, one bullet per decision, contamination-audited fail-loud (identical structure to `draft_harvested_sections`). `cluster_corrections` is generalized (or a sibling `cluster_by_scope` added) to cluster `explicit_user_rule` memories by `table_scope`. Decisions are drafted **alongside** Gotchas in the same `HarvestReviewScreen` (a labeled section per draft), approved/skipped independently, and written via the existing `apply_approved_sections`.
- **D4 — Gated + fail-closed + never on benchmark.** Reuses `Profile.harvest_opt_in` (decisions are part of the harvest loop) — default False; the `ctrl+shift+d` capture and the decision-drafting both no-op when off. Never runs on `run_agent_task`/benchmark paths (same posture as harvesting).
- **D5 — Retrieval is automatic, zero benchmark risk.** A `## Decisions` section lives in the same Scent doc and is retrieved by the existing `search_reference_docs` (it retrieves all non-Quick-Reference sections). **No change to the retrieval scorer** → no change to the benchmark/default retrieval path (unlike Q3). A future session grounding against that domain sees the decision as a matched section.

## 3. Components

### 3.1 Capture (`screens/`)
- `RecordDecisionScreen(ModalScreen)` — one `TextArea` + save/cancel; mirrors the small-modal pattern. On save → `MemoryStore(...).append(Memory(kind=explicit_user_rule, ...))` + a "🧭 Decision recorded" notify.
- `MainScreen`: `Binding("ctrl+shift+d", "record_decision", ...)` (fallback plain key if the terminal swallows the chord, per the M2/M3 precedent) → gated on `harvest_opt_in` (notify to enable in Settings when off) → push `RecordDecisionScreen`. `table_scope` computed from `self._last_sql` via `resolve_table_scope`.

### 3.2 Draft + cluster (`maze/harvest.py`, `memory/`)
- `draft_decision_sections(clusters, *, generated_at, model_id=None) -> dict[str, list[Section]]` — `## Decisions` heading, `source="harvested"`, deduped bullets, contamination-audited (raise `ScentContaminationError` on a tripped draft), exactly paralleling `draft_harvested_sections`.
- Clustering: reuse `cluster_corrections` generalized to accept a kind filter (corrections vs decisions), or add `cluster_memories(memories, kinds)`. Both correction-kinds and `explicit_user_rule` cluster by `table_scope` / `__global__`.

### 3.3 Review integration (`screens/harvest_controller.py`, `harvest_review.py`)
- `review_corrections` (or a generalized `review_session`) collects BOTH the drafted Gotchas and drafted Decisions and hands them to `HarvestReviewScreen`, which already approves/skips a list of `(domain, Section)` drafts. Each draft shows its heading so the analyst sees Gotcha vs Decision. Apply path unchanged (`apply_approved_sections`).

## 4. Non-negotiables

1. **No LLM in decision capture or drafting** — the analyst's text is stored and drafted verbatim; deterministic; no extraction model, no cost, no false positives.
2. **Human-gated + contamination-audited promotion, fail-closed, never on benchmark** — decisions reach a project-layer/team-shared Scent doc only through `HarvestReviewScreen` approval + `audit_scent_doc` (fail-loud); gated on `harvest_opt_in` (default False); never on `run_agent_task`/benchmark paths.
3. **No retrieval-scorer change** — decisions surface via the existing `search_reference_docs` section retrieval; the benchmark/default retrieval path is byte-identical (this is what keeps Q4 benchmark-safe where Q3 was not).
4. **Reuse, don't fork** — `MemoryKind.explicit_user_rule`, `MemoryStore`, `resolve_table_scope`, `HarvestReviewScreen`, `apply_approved_sections`, `Profile.harvest_opt_in` consumed as-is; `draft_decision_sections`/clustering mirror the correction path.
5. **Provenance honest** — decision sections are `source="harvested"` (human-gated, human-authored); no fabricated higher tier.
6. Pyright strict for `maze/`, `memory/`; `screens/` exempt; repo gates per commit; `test_app_renders` env flake non-signal.

## 5. Testing

- Capture: `RecordDecisionScreen` save → a `Memory(kind=explicit_user_rule)` with the typed text + correct `table_scope` lands in the store; gate off → `ctrl+shift+d` no-ops with a notify.
- Draft: `draft_decision_sections` produces a `## Decisions` section per cluster with verbatim bullets, `source="harvested"`; a contamination-tripping decision raises fail-loud.
- Cluster: `explicit_user_rule` memories cluster by `table_scope`; global vs table attribution.
- Review integration: the harvest review shows both Gotchas and Decisions; approving a Decision writes a `## Decisions` section to the project-layer doc (dedup, git-sha, audit-before-write); skipping writes nothing.
- Retrieval: a written `## Decisions` section is returned by `search_reference_docs` for a matching query (proves the compounding loop end-to-end) — no scorer change.
- TUI pilot: `ctrl+shift+d` → record → `ctrl+shift+h` → approve → decision on disk; fresh session retrieves it.
- Manual gate (pty): record a decision, promote it, retrieve it in a new session.

## 6. Out of scope (v1)

- **LLM decision-extraction from chat** (false-positive risk; the explicit-capture decision in D2 is deliberate) — a later, carefully-gated increment.
- **Finding-note-derived decisions** (a pinned Finding's note as an implicit decision) — a clean secondary source, deferred to v2.
- A separate `decisions.jsonl` file format — v1 reuses the `Memory` store (the competitive-analysis "decisions.jsonl" framing is satisfied by `kind=explicit_user_rule` records).
- Decision editing after promotion beyond re-recording; any retrieval-scorer change (that's Q3, deferred).
