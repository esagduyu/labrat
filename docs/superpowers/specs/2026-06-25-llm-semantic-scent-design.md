# LLM-Semantic Scent (FEATURE_ROADMAP T1c) — Design

**Status:** Spec'd 2026-06-25, branch `feat/llm-semantic-scent`.
**Roadmap:** `FEATURE_ROADMAP.md` T1c (Impact 5 · Difficulty 3) — the recommended next item after T1a (verification, shipped). Closes the Cartographer-vs-AutoContext grounding gap.
**Memory:** `project_competitive_position`, `project_verification_layer_next`, `feedback_tools_first_class`.

## 1. Problem & goal

LabRat's Cartographer writes **structure-only** Scent docs today (deterministic skeleton: quick-reference, key-tables, dimensions). That alone is worth +8pp on DAB (Sonnet/claude-mcp). The remaining grounding lift lives in the **interpretive** layer — gotchas, dirty-data warnings, canonical metric definitions, format quirks — exactly what Altimate's LLM-authored AutoContext encodes for its ~+8pp and its #2 leaderboard standing. The canonical example: the **stockindex mixed-date-format** trap (three date formats silently dropping rows), which a one-line `## Gotchas` bullet would pre-empt.

**Goal:** turn on the LLM "semantics" pass of the Cartographer (`with_semantics`) as a **first-class, general capability** — trustworthy enough for product use and reproducible enough for the leaderboard — and prove it on DAB.

## 2. Critical context: most of the engine already exists

This is **not** a build-the-engine project. The following are already shipped (in `src/labrat/maze/cartographer.py` and `scripts/cartograph.py`):

- `generate_scent(..., with_semantics: bool, llm_fn: LLMFn | None)` — builds the deterministic skeleton (sections tagged `Source: verified`), then, when `with_semantics` and an `llm_fn` are supplied, calls `draft_semantics` and merges via `merge_sections` so **drafted sections can never override or contradict a verified heading**.
- `draft_semantics(skeleton, llm_fn)` — single LLM pass; output sections tagged `Source: draft`.
- `_SEMANTICS_INSTRUCTION` / `_semantics_prompt(skeleton)` — already targets the right taxonomy: `## Gotchas` (wrong-answer modes, dirty-data warnings), `## Best Practices` (canonical metric defs, preferred columns), `## Cross-References`; instructs "if unsure, say so rather than invent"; the LLM receives **only `render_document(skeleton)`** (structure + sampled rows) → **GT-firewalled by construction at the input.**
- `cartograph_prepass(...)` — **idempotent**: if `scent_dir` already holds `*.md` docs it reuses them; otherwise generates and writes. This *is* the author-once → freeze → consume cache.
- `scripts/cartograph.py` — general standalone CLI: `--with-semantics --provider <p> --model <m>` (default `anthropic` / `claude-sonnet-4-6`), builds `llm_fn = provider_llm_fn(build_provider(provider, model))`. The **selectable authoring model, Sonnet default, independent of any agent model** already exists here.

The general consumption path also already works: authored docs land in the Scent store and the agent retrieves them via the `search_reference_docs` tool (registered in `build_data_tools_registry()` → available to `run_agent_task`, the MCP server, and the TUI).

**Therefore T1c = make it trustworthy, turn it on for DAB, and prove it.** Four pieces (§5).

## 3. Design decisions (locked in brainstorming 2026-06-25)

1. **Scope: general-first, DAB as one consumer.** The semantics pass is a general Cartographer capability; DAB consumes the same authored docs. The general CLI path already exists; this slice makes it *trustworthy* and adds DAB as a consumer. TUI auto-grounding-on-connect stays deferred (roadmap T2c).
2. **Lifecycle: author-once → freeze → consume.** Semantics authored once per database, written to the Scent store as a static artifact, then all consumers read the frozen doc. (Already the `cartograph_prepass` idempotent-cache behavior.) For DAB the frozen docs are **committed** so the leaderboard run is deterministic and auditable.
3. **Trust: by-construction firewall + automated audit, auto-use.** Input firewall (LLM sees only the deterministic skeleton) **plus** an automated contamination audit of every authored doc before it is frozen/consumed; **fail loud** on a hit. No mandatory human-review gate (reproducible + fast); human review remains optional for product use.
4. **Authoring model: an independent, selectable parameter, default Sonnet.** Decoupled from the agent/trial model. General/product authors with the user's configured provider+model; DAB defaults the authoring model to `claude-sonnet-4-6` (preserves the "best single mid-tier model" story and the Sonnet-authored Altimate-precedent legality) via a **separate flag** from `--agent-model`.

## 4. Architecture

```
                    ┌─────────────────────────── authoring (build-time, once per DB) ───────────────────────────┐
 connections ──▶ generate_scent(with_semantics=True, llm_fn=<authoring model>)
                    │   skeleton (Source: verified)  ─┐
                    │   draft_semantics(skeleton)     ─┤─▶ merge_sections ─▶ audit_scent_doc(doc) ──(clean)──▶ write_docs ─▶ Scent store (frozen)
                    │                                                              └──(hit)──▶ raise (fail loud, nothing frozen)
                    └──────────────────────────────────────────────────────────────────────────────────────────┘
                                                                                                   │
                            ┌──────────────────────────────────────────────────────────────────────┘
                            ▼ consume (read frozen docs)
   general:  search_reference_docs tool  ◀─ Scent store (./labrat_maze/scent + ~/.labrat/maze/<profile>/scent)
   DAB:      per-trial hermetic scratch  ◀─ committed frozen docs (copied in per trial; no re-authoring)
```

The authoring LLM's only input is `render_document(skeleton)`; the contamination audit is the freeze-time backstop; consumers only ever read frozen, audited docs.

## 5. Components & changes

### 5.1 Contamination audit at freeze-time (general guard) — primary new safety code
- **Promote** the contamination detector out of the DAB suite into a shared module so it is a general capability (tools-first-class), e.g. `src/labrat/maze/scent_audit.py` exposing `audit_scent_doc(doc: ScentDoc) -> str | None` (returns a tag on a hit, else `None`). Reuse the existing `_CONTAMINATION_PATTERNS` set (answer-key / gold-answer / external-dataset substrings, case-insensitive).
- **Refactor, don't fork:** `DabSuite._detect_contamination` should call the promoted shared function so there is one pattern list. (The DAB *trial-output* contamination scan and the *Scent-doc* audit share the same patterns; keep one source of truth.)
- **Wire into authoring:** `generate_scent` (and therefore `cartograph_prepass`) runs `audit_scent_doc` on each doc **after** `merge_sections` and **before** returning/writing, only when `with_semantics` produced drafted content. On a hit, **raise** a clear error (`ScentContaminationError`) naming the doc + tag; nothing is frozen. Deterministic skeleton-only docs (no semantics) skip the audit (nothing LLM-authored to vet) but it is harmless to run.
- **Rationale:** makes decision §3.3 real for *every* consumer, not just DAB; a future prompt/skeleton change that leaked something answer-shaped fails loudly instead of silently poisoning a frozen doc.

### 5.2 DAB author-once → freeze → consume integration — primary new wiring
- Today: `suite.py` calls `cartograph_prepass(with_semantics=False)` per-trial into each trial's hermetic scratch HOME.
- Change: when semantics are enabled, **author once per dataset** into a persistent, committed location (proposed: `dab_scent/<dataset>/` at repo root, gitignored-by-default override so it can be force-committed for a submission, mirroring how DAB artifacts are handled). The pre-pass authors enriched docs there, audits them (§5.1), and freezes them. Each trial then **consumes the frozen doc** by copying it into the trial's scratch Scent dir (the existing per-trial consumption seam is unchanged — only the *source* changes from "regenerate" to "copy frozen").
- New `scripts/eval_dab.py` flags, **independent of `--agent-model`**:
  - `--cartograph-semantics` (store_true, default None) — enable the semantics pass for the cartograph pre-pass. Requires `--agent-cartograph`.
  - `--cartograph-semantics-model` (default `claude-sonnet-4-6`).
  - `--cartograph-semantics-provider` (choices = `PROVIDER_NAMES`, default chosen so it authenticates on the Max-plan path — see open question §8).
  - Plumb all three through the resume-conflict guard / `effective_*` / `config.json` round-trip exactly like `--agent-cartograph` (4-site pattern).
- `DabSuite.__init__` gains `cartograph_semantics: bool = False`, `cartograph_semantics_model: str = "claude-sonnet-4-6"`, `cartograph_semantics_provider: str = <default>`; the pre-pass builds `llm_fn = provider_llm_fn(build_provider(provider, model))` from these, **not** from `self._agent_provider`/`self._agent_model`.
- **Reproducibility:** the frozen docs are committed for a leaderboard submission; a re-run reads them rather than re-authoring → deterministic + GT-firewall-auditable (anyone can read the committed Scent docs).

### 5.3 Ablation
- Structure-only Scent (`--agent-cartograph`) vs semantics-enriched (`--agent-cartograph --cartograph-semantics`) on the DAB tuning subset `deps_dev_v1,music_brainz_20k,stockindex`, n=3, claude-mcp/Sonnet, on top of `--hints`. Same harness/methodology as the verification ablation.
- Decision rule: keep `--cartograph-semantics` for the full run only if net-positive (target: approach Altimate's ~+8pp). Report per-dataset deltas; watch for noise (stockindex).
- Controller activity, not a code task (mirrors the verification-layer ablation section).

### 5.4 Quality validation + tests
- **Audit guard unit tests:** a `ScentDoc` containing an answer-key/gold-answer/external-dataset phrase → `audit_scent_doc` returns the tag and `generate_scent` raises `ScentContaminationError`; a clean doc → returns `None`, authoring proceeds.
- **Merge invariant test:** drafted sections never override a verified heading (extend/confirm coverage of `merge_sections`).
- **End-to-end smoke (LLM-gated):** author semantics on the `ecommerce` fixture DB (`tests/fixtures/sample_dbs/ecommerce.duckdb` via the conftest `ecommerce_db` fixture — never depend on the gitignored file directly) with a real `llm_fn`; assert a `## Gotchas` (or `## Best Practices`) section appears, is tagged `Source: draft`, and passes the audit. Gated on `ANTHROPIC_API_KEY`/`LABRAT_RUN_LLM_TESTS` like other LLM tests.
- **Prompt tuning:** if the smoke output is weak/generic, lightly tune `_SEMANTICS_INSTRUCTION` toward the dirty-data/format-quirk class (stockindex mixed-date example as the canonical target). Keep it retrieval-oriented and "say so if unsure."

## 6. Data flow & GT-firewall (consolidated)
1. Authoring input = `render_document(skeleton)` only → structure + sampled rows; the LLM never receives `validate.py`/`ground_truth.csv`/external-label files (firewalled by construction).
2. Merge keeps verified facts authoritative; drafted content is additive and provenance-tagged.
3. Freeze-time audit (`audit_scent_doc`) scans the merged doc for answer-shaped content; a hit aborts loudly.
4. Consumers read only frozen, audited docs. For DAB the frozen docs are committed → publicly auditable for the leaderboard.

## 7. Scope / non-goals
- **In scope:** the freeze-time audit guard (general), the DAB author-once-freeze-consume wiring + independent authoring-model flags, the ablation, and tests/prompt-validation.
- **Out of scope / deferred:** TUI auto-grounding-on-connect (roadmap **T2c**, gated on this); staleness/schema-change re-author triggers (possible later enhancement on top of the frozen model); lineage/dbt-semantic ingestion (**T1b**, separate); any change to the verification layer or the deferred `run_agent_task` verification product params (§6 of the verification spec).

## 8. Open questions (resolve during planning)
- **Authoring provider default for DAB:** the judge-auth lesson from the verification layer — the Max-plan claude-mcp path strips `ANTHROPIC_API_KEY`, so an in-process `anthropic` provider can't authenticate there. The DAB semantics-authoring `llm_fn` must use a Max-plan-capable provider (likely `claude-code`) when the run is on the Max-plan path, mirroring `_verify_llm_fn`'s `claude-code`-on-claude-mcp routing. Confirm the default `--cartograph-semantics-provider` accordingly (general CLI default stays `anthropic` for BYO-API users).
- **Frozen-doc location & commit policy for DAB:** `dab_scent/<dataset>/` vs inside the run `--output-dir`; and the gitignore/force-commit policy for making a submission's grounding docs publicly auditable.

## 9. Success criteria
- Authoring with semantics produces useful `## Gotchas`/`## Best Practices` sections on a real DB, provenance-tagged `draft`, that pass the contamination audit.
- The audit guard demonstrably fails loud on a planted answer-shaped phrase.
- The general path (CLI → store → `search_reference_docs`) and the DAB path both consume the same frozen, audited docs.
- DAB ablation produces a clear keep/drop signal vs structure-only on the tuning subset.
- Full gate clean (ruff/pyright/pytest); off path (semantics disabled) byte-identical to today.
