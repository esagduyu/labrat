# Trail v1 — "Scent for Procedures" — Design

**Date:** 2026-07-10 · **Status:** approved-autonomously along the recommended path (user directive "keep going, no check-in until the manual gate"); the core-definition decision (D1) is surfaced for veto at the manual gate.
**Thesis:** a **Trail** is a named, intent-retrieved, provenance-stamped **analysis SOP** — ordered steps + the canonical reference SQL + the validations that apply + gotchas — promoted **human-gated** from real completed work and read by the agent as **structured guidance, never auto-executed**. It is the Maze layer above Scent (north-star §8: "a reusable learned path other rats follow = Meta Recipe; from `history/` + `validations/`"). It directly attacks the measured ablation finding (north-star §8:189): 80% of wrong answers had correct SQL somewhere in the corpus but the agent didn't reuse it — "the bottleneck was structure," so distill history into structured, intent-keyed procedures rather than exposing raw grep.

## 1. What already holds (verified this session via a subsystem map)

- `MazeStore` (`maze/store.py`) is **`kind`-parameterized** (two-layer user `~/.labrat/maze/<profile>/<kind>/*.md` + project `<root>/labrat_maze/<kind>/*.md`); it accepts `kind="trail"` today with **zero store changes**. `maze/__init__.py` already reserves "trail".
- `maze/document.py` `parse_document`/`render_document` is a `kind`-agnostic round-trip; `Section` carries `source` (provenance token) + freshness meta (`generated_at`/`schema_hash`/`model_id`/`git_sha`).
- `SOURCE_TIERS` trust ladder (`maze/provenance.py`): `semantic_layer > lineage > verified > harvested > draft > human`.
- `agent/tools/search_reference_docs.py` hardcodes `kind="scent"` (:86); its `_lexical` tokenizer/stemmer/scorer + `DocResult`/`SectionMatch` output are the reusable retrieval pattern.
- `maze/harvest.py` is the reusable **human-gated promotion template**: `cluster_corrections` → `draft_*_sections` (contamination-audited, `source`-tagged, draft-only) → `apply_approved_sections` (audits merged doc BEFORE write, project-layer, dedup-by-body, git-sha stamped, fail-loud). `screens/harvest_review.py` is the review-screen template.
- `agent/program/dsl.py` `Program`/`ProgramStep` is the only executable multi-step shape — **ephemeral, no name/params/persistence** (this is what Trail v2 would marry; see §6).
- `validations/model.py` `ValidationRule(natural_language_rule, severity, table_scope, enabled)` is the "business rules" half a Trail references.
- `thread/` stores **end-state only** (`Finding` = question+sql+results tuple; `Version.chat_history` = raw message dicts). No structured multi-step representation exists — that gap is what Trail fills.
- Benchmark posture precedent: Cartographer is deterministic-only + GT-firewalled on DAB; harvesting is fail-closed OFF on benchmark paths.

## 2. Decisions (each weighed; recorded for review)

- **D1 — Trail v1 is a READ-AS-GUIDANCE reference procedure, NOT an executable saved program. [SURFACED FOR VETO AT THE GATE]**
  Options: (a) **reference SOP doc** the agent retrieves and reads while planning; (b) a saved/parameterized `Program` the agent *invokes* (auto-executes). **Chosen (a).** Why: it reuses ALL existing machinery (MazeStore kind=trail, document.py, retrieval pattern, harvest promotion) with near-zero new infrastructure; it honors the ablation lesson (structure + reuse beats execution-cleverness — the agent's gap was *finding and adapting* the right SQL, not *running* a pipeline); it avoids the large trust/safety surface of auto-executing a saved multi-step **mutating** program without a per-invocation human gate; and it is the north-star's own framing ("= Meta Recipe," a recipe you *read and adapt*, not a macro). Option (b) is deferred to v2 (§6) where it gets its own execution-gate design.
- **D2 — A Trail is distinct from a Scent section by four properties** (else it is just a "Best Practices" blurb): (1) **procedure-shaped** — ordered steps, not a flat reference; (2) carries **canonical reference SQL** — the copy-and-adapt query the ablation said the agent failed to reuse; (3) **keyed by analysis INTENT** ("compute retention", "attribute revenue by region"), not by table/domain — you retrieve it by *what you're trying to do*; (4) **references the validations** that apply to this analysis type.
- **D3 — Trail doc schema** (a `kind="trail"` markdown doc parsed by the existing `document.py`): frontmatter `kind: trail` · `intent` (the retrieval key, a short imperative phrase) · `title` · `tables: list[str]` · `applies_validations: list[str]` (validation ids/names) · `confidence`. Sections (each with `**Source:**` + freshness meta): **When to use · Steps · Reference SQL · Validations · Gotchas**. "When to use" is the intent-match surface (parallels Scent's "Quick Reference" — prepended to every hit, excluded from body scoring).
- **D4 — Retrieval = a new `search_trails` tool** (sibling to `search_reference_docs`, not a `kind` param on it). Why separate: Trails are retrieved by **intent** ("how do I compute X") vs Scent by **table/domain**; different scoring emphasis (intent/When-to-use weighted), different agent mental model, and the system prompt routes them differently ("before planning a known analysis type, `search_trails`"). Reuses `_lexical` + a `DocResult`-shaped output. Deterministic, no LLM. Self-consistent with the 26→27-tool inventory.
- **D5 — Promotion v1 = MANUAL, from a completed thread/Finding.** Options: (a) manual "save this analysis as a Trail"; (b) auto-harvest from history/recurrence clusters. **Chosen (a)** — it is the concrete authoring path that makes retrieval testable end-to-end immediately, and it mirrors two shipped patterns (Cheese pin→capture, harvest→review→apply). The draft pulls: question → `intent`+`title`, final SQL → Reference SQL, `FindingProvenance` (if the source is a Cheese-pinned Finding) → the Trail's provenance tier. **Applicable validations are derived deterministically by table-scope match** — the `ValidationRule`s whose `table_scope` is in the Finding's referenced tables (parsed from the SQL via the existing sqlglot table extraction), NOT by session-touch tracking (Findings don't carry that). The Steps and When-to-use sections are seeded from the question + a light structural summary of the SQL and are **human-editable in review** (the analyst owns the prose). Auto-harvest = v2 (§6).
- **D6 — Promotion is contamination-audited + human-gated + fail-closed on benchmark**, identical posture to harvest: `scent_audit.audit_scent_doc` runs on the drafted Trail BEFORE any write (fail-loud); apply is project-layer via the `apply_approved_sections` pattern (dedup-by-body, git-sha stamped); a `Profile.trail_opt_in` field (default **False**) gates the whole authoring loop; Trail promotion NEVER runs on benchmark/`run_agent_task` paths.
- **D7 — Provenance tier for a promoted Trail:** `source="verified"` when promoted from a Finding that itself carried a verifier-sufficient provenance snapshot; otherwise `source="draft"`. Never `semantic_layer`/`lineage` (those are reserved for their generators). Freshness `schema_hash` stamped from the live catalog at promotion, so a Trail whose reference SQL predates a schema change surfaces as stale in `search_trails`.
- **D8 — Out of scope v1** (§6): executable/parameterized Trails; auto-harvest from history/context_engine recurrence; decision-trail harvesting (2.5); Warren (Trail+Scent domain bundles); any team-sharing beyond what git-versioned project-layer Trails already give for free (team-Scent's git mechanism already covers it).

## 3. Components

### 3.1 `maze/trail.py` (new — model + draft + promote, mirrors `maze/harvest.py`)
- `TrailDraft` (Pydantic): `intent`, `title`, `tables`, `applies_validations`, and the five section bodies (when_to_use, steps, reference_sql, validations, gotchas).
- `draft_trail_from_finding(finding, *, all_validations, generated_at, model_id, schema_hash, git_sha) -> ScentDoc` — extracts the Finding's tables (sqlglot), selects `applies_validations` = the `all_validations` whose `table_scope` matches, builds a `kind="trail"` `ScentDoc` with the five sections, `source` per D7, freshness stamped; contamination-audited (fail-loud) before returning.
- `apply_trail(store, doc, *, scope="project", git_root) -> None` — audits + writes via `MazeStore.write_doc(doc, scope, kind="trail")` (dedup-by-body, git-sha stamped). Thin wrapper reusing the harvest apply invariants.

### 3.2 `agent/tools/search_trails.py` (new — sibling of `search_reference_docs`)
- Input: `intent: str` (natural-language "what I'm trying to do"), optional `tables: list[str]`.
- Reads `MazeStore.from_env(profile).docs(kind="trail")`; scores with `_lexical` weighting the `intent` frontmatter + "When to use" section; returns a `TrailResult`-shaped output (intent/title/when_to_use/sections/best_source/stale), "When to use" prepended to each hit. Deterministic, no LLM; self-errors gracefully when the Trail store is empty (returns `results=[]`).

### 3.3 `Profile.trail_opt_in: bool = False` (`profile/model.py`) + a Settings row.

### 3.4 TUI surface (`screens/`)
- A **"save as Trail"** action on the findings viewer (per-Finding) — drafts via `draft_trail_from_finding`, opens a **`TrailReviewScreen`** (mirrors `HarvestReviewScreen`: shows the drafted sections, edit/approve/skip, fail-loud audited apply). Gated on `Profile.trail_opt_in`.
- `search_trails` registered in the TUI + labrat-agent registries; a system-prompt line: "Before planning a recognizable analysis type (retention, attribution, funnels, cohorts…), call `search_trails` with your intent and adapt the canonical Reference SQL if a Trail matches."

## 4. Non-negotiables

1. **Read-as-guidance only:** v1 Trails are retrieved and read; nothing in v1 auto-executes a Trail's Reference SQL. (D1)
2. **Human-gated + contamination-audited promotion, fail-closed on benchmark:** no Trail is written without passing `audit_scent_doc` and an explicit human approve; `Profile.trail_opt_in` defaults False; Trail authoring never runs on `run_agent_task`/benchmark paths. (D6)
3. **Benchmark isolation:** nothing under `eval/` or `mcp/` imports `maze/trail.py` or `search_trails` in a way that writes; `search_trails` on a benchmark reads the (empty, GT-firewalled) Trail store exactly as `search_reference_docs` does.
4. **Provenance never fabricated:** tier per D7 from real capture; freshness `schema_hash` from the live catalog; a Trail promoted from an unverified Finding is `draft`, never `verified`.
5. **Reuse, don't fork:** the store, parser, provenance ladder, retrieval tokenizer, and promotion/audit flow are the existing ones parameterized by `kind="trail"` — no parallel copies.
6. Pyright strict for `maze/` and `agent/tools/`; `screens/` exempt; repo gates per commit; `test_app_renders` env flake non-signal.

## 5. Testing

- Model/draft: `draft_trail_from_finding` produces a well-formed `kind="trail"` doc with all five sections, correct `source` per D7 (verified-Finding → verified; plain → draft), freshness stamped; a contamination-tripping reference SQL fails loud.
- Store round-trip: write + read a `kind="trail"` doc through `MazeStore`; two-layer merge; git-sha stamp on apply; dedup-by-body.
- `search_trails`: intent-match ranks the right Trail; empty store → `results=[]`; stale flag when `schema_hash` diverges from live catalog; benchmark path reads empty store without error.
- Promotion gate: `trail_opt_in=False` blocks authoring; audit fail-loud on a contaminated draft; apply is project-layer + git-sha stamped.
- TUI: pilot test the save-as-Trail → TrailReviewScreen → approve → doc-on-disk loop; `search_trails` reachable in the registry.
- Manual gate (pty + artifact inspect): author a Trail from a real Finding, approve it, then in a fresh session confirm `search_trails` retrieves it and the agent's system prompt routes to it.

## 6. Out of scope (v1, explicit — the v2 backlog)

- **Executable/parameterized Trails** (marry the `run_program` DSL: a saved `Program` with free input params + a name/intent envelope the agent can *invoke*) — needs a per-invocation execution/trust gate design of its own; the single biggest deferral.
- **Auto-harvesting Trails** from `history/` + `context_engine` recurrence clusters (distill, don't grep — the ablation constraint) — v1 is manual-promotion-only.
- **Decision-trail harvesting** (extra 2.5, `decisions.jsonl`) — a complementary raw source, separately specced.
- **Warren** — bundling Trails + Scent into installable domain packages.
- **Team Trail sharing** beyond git-versioned project-layer (already free via team-Scent's git mechanism).
- Auto-execution of Reference SQL; Trail editing after apply beyond re-promotion; embedding-based Trail retrieval.
