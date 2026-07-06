# M5 — Compounding Memory Moat: Scent-Provenance Foundation + T2b Correction-Harvesting v1 — Design

**Date:** 2026-07-06
**Status:** Adopted + refreshed for build (autonomous M5 run; the underlying design was user-reviewed 2026-07-02)
**Branch:** `feat/memory-moat`
**Adopts:** `docs/superpowers/specs/2026-07-02-moat-roadmap-design.md` (moat-roadmap spec — Plan 0 + Plan 1), refreshed against the post-M3/M4 codebase. Milestone **M5** of `docs/superpowers/plans/2026-07-03-competitive-build-milestones.md`.

## 1. Motivation & scope

LabRat's benchmark position is strong (DAB #8/21, 60.88% on a single mid-tier model) but benchmark-chasing has a ceiling. The durable game is the **moat no leaderboard measures and no build-time-only competitor can copy**: a knowledge library (the Rat Maze / Scent) that **compounds per-user and per-team over time**. Today LabRat has all the raw material for this loop — `QueryEvent.edit_diff`, a `MemoryStore`, and two extraction primitives (`EditExtractor`, `ChatCorrectionExtractor`) — but the extractors have **zero callers**: the compounding loop is unbuilt.

M5 ships the **first, shippable moat increment**: the shared Scent-provenance **foundation** (Plan 0) + **T2b correction-harvesting v1** (Plan 1) — wire the dormant extractors into a **human-gated** loop that promotes recurring corrections into Scent, with schema-staleness detection.

**Autonomous scope decision (documented):** M5 = Plan 0 + Plan 1 only.
- It is the coherent, already-reviewed, unblocked first increment (~80% of primitives already idle in `memory/`).
- Increment 2 (T1b column-lineage) is **partly already shipped** — `explain_lineage` + column-level lineage landed in **M3** (`ad125e0`); the remaining dbt semantic-layer/metric ingestion is a separate follow-on plan.
- Increment 3 (T3c provenance footer + T2c first-connect) and the milestones-doc extras (2.3 git-team memory, 2.4 customer-facing evals, 2.5 decision-trail harvesting) are follow-ons, each getting its own spec + plan when reached. The foundation (`source_rank`, section freshness metadata) is built here so those follow-ons don't re-migrate the Scent model.

## 2. Non-negotiables (from the moat spec — unchanged)

1. Every Scent write runs through `maze/scent_audit.py` (`audit_scent_doc` / `detect_contamination`, fail-loud) — **including harvested content**.
2. Harvested content is **drafted then human-approved**, never auto-written/frozen.
3. Harvesting is **disabled on benchmark paths** (DAB/ADE) — TUI/product path only; `SessionHarvester` must be unreachable from `eval/` (grep-gated in the regression task).
4. No `Date.now()`-style impurity inside pure functions: `generated_at` is passed in by callers, never generated inside promotion/serialization code.
5. Back-compat: every new `Section`/source field is optional; existing serialized Scent docs parse unchanged (round-trip test required).
6. `Memory.embedding` stays unused in v1 (clustering is by `table_scope`).

## 3. Refreshed current-state anchors (code-verified 2026-07-06)

The underlying plan is 4 days old and predates M3/M4. The drift that matters:

- **`_RECOGNIZED_SOURCES`** (`maze/document.py:17`) already `= {"verified","draft","human","lineage"}` — **`lineage` was already added by M3**. The foundation ADDS `harvested` + `semantic_layer`.
- Document (de)serialize functions are **`parse_document(text, *, domain, scope="")`** and **`render_document(doc)`** — NOT `serialize_scent_doc`/`parse_scent_doc`. Source marker lifted by `_extract_source` (`document.py:44`), sections split by `_split_sections` (`:64`), rendered at `:110` (`**Source:** {s.source}` at `:128`).
- `Section(heading, body, source="human")` — **no metadata fields yet** (`document.py:21`).
- **`MemoryStore(memory_dir: Path | None = None)`** — NOT `root=`; `append(memory)`, `read_profile(profile) -> list[Memory]`.
- **`QueryEvent` requires** `profile, thread_id, version_id, sql_final` (optional: `sql_initial`, `edit_diff`, `error_message`). Test construction must supply all required fields.
- Extractors match the plan exactly: `EditExtractor`/`ChatCorrectionExtractor`, `__init__(profile, llm_fn)`, `async extract(...) -> list[Memory]`; `LLMFn = Callable[[str], Awaitable[str]]`.
- `MemoryKind` = `edit_derived`, `chat_correction`, `explicit_user_rule`. Correction kinds = the first two.
- scent_audit exports `detect_contamination(text) -> str | None` (truthy tag on hit), `audit_scent_doc(doc) -> str | None`, `ScentContaminationError(RuntimeError)`.
- **`MazeStore` is READ-ONLY today** — `MazeStore(project_root, home, profile)`, `.from_env(profile)`, `.docs(kind="scent") -> list[ScentDoc]` merging user+project layers (project wins). **No write path exists.** T2b must add one → `<project_root>/labrat_maze/scent/<domain>.md`. **This is the largest refresh vs the 2026-07-02 plan, whose Task 7 assumed a nonexistent `MazeStore` load/save API.**
- `llm_fn` is now genuinely injectable (M4 2.1 `ctx.llm_fn` via `provider_llm_fn`); the TUI provider supplies the harvest `llm_fn`. Not a benchmark lever.
- Test baseline is now **~1061** (the old plan said 714).

## 4. Components (units)

### Plan 0 — Shared foundation
- **U1 — provenance ladder** (`src/labrat/maze/provenance.py`, NEW). `SOURCE_TIERS = [semantic_layer, lineage, verified, harvested, draft, human]`; `source_rank(source) -> int` (0 = highest; unknown → `len(SOURCE_TIERS)`); `best_source(sources) -> str` ("human" if empty). Widen `_RECOGNIZED_SOURCES` to add `harvested` + `semantic_layer`.
- **U2 — section metadata** (`maze/document.py`). `Section` gains optional `generated_at/schema_hash/model_id/git_sha` (all `str | None = None`). `render_document` emits a `**Meta:** key=value; …` line after `**Source:**` when any is set; a new `_extract_meta` helper (mirroring `_extract_source`) lifts it in `_split_sections`. Absent metadata → all None; existing docs round-trip byte-stable.
- **U3 — doc-correction**. Fix the stale "LabRat does not verify" claim (`docs/superpowers/specs/2026-06-01-labrat-north-star-design.md:212` + any §5/§9 hits) — T1a consensus/re-derive shipped 2026-06-25 (`agent/verification/`, default-off).

### Plan 1 — T2b correction-harvesting v1
- **U4 — SessionHarvester** (`src/labrat/memory/harvest.py`, NEW). `__init__(profile, llm_fn, store, enabled=True)`; `async harvest_events(events) -> list[Memory]` (runs `EditExtractor` on events with a non-empty `edit_diff`, appends, returns; `[]` when disabled); `async harvest_correction(user_message, context_sql) -> list[Memory]` (runs `ChatCorrectionExtractor`). Benchmark paths construct it `enabled=False`.
- **U5 — promotion pass** (`src/labrat/maze/harvest.py`, NEW). `cluster_corrections(memories) -> dict[str, list[Memory]]` (correction kinds only, `table_scope or "__global__"`). `draft_harvested_sections(clusters, *, generated_at, model_id=None) -> list[Section]` — one `## Gotchas` Section per cluster, `source="harvested"` + metadata, deduped `- bullet` per `Memory.text`, each drafted body run through `detect_contamination` (**raise `ScentContaminationError` on a hit — fail-loud**), draft-only. `[]` for empty input.
- **U6 — MazeStore write path** (`maze/store.py`) + apply helper (`maze/harvest.py`). `MazeStore.load_domain(domain, kind="scent") -> ScentDoc | None`; `MazeStore.write_doc(doc, *, scope="project") -> Path` (render via `render_document`, write to the project layer `<root>/labrat_maze/<kind>/<domain>.md`, `mkdir -p`). `apply_approved_sections(store, domain, approved) -> None` — load-or-create the domain doc, append approved sections (dedup against existing bodies), persist via `write_doc`.
- **U7 — staleness** (`src/labrat/maze/staleness.py`, NEW). `schema_fingerprint(tables: dict[str, list[str]]) -> str` (sha256 of canonical `{table: sorted(cols)}` JSON, sorted keys). `is_stale(section_schema_hash, current_fingerprint) -> bool` (None baseline → False).
- **U8 — TUI wiring** (`src/labrat/screens/`). Thread-close harvest trigger (gated `enabled` off under benchmark/no-TUI) + a "harvest corrections" review action: a thin Textual shell over `cluster_corrections` → `draft_harvested_sections` → per-bullet approve/reject → `apply_approved_sections`. Logic lives in the tested helpers (`screens/` is pyright-strict-exempt); the screen is a shell.
- **U9 — regression + gates**. Full suite green; `grep -rn "SessionHarvester" src/labrat/eval/` returns nothing; `decisions.md` entry.

## 5. Testing (fixtures; stub `llm_fn`; no live LLM)

- **U1:** ladder order + `source_rank("lineage") < ("verified") < ("harvested") < ("human")`; unknown → lowest; `best_source` picks highest tier; `_RECOGNIZED_SOURCES` accepts new tokens.
- **U2:** `Section` metadata round-trips through `render_document`/`parse_document`; a legacy doc with no `**Meta:**` line parses with all-None metadata (back-compat).
- **U4:** `harvest_events` appends edit-derived memories (persisted, retrievable via `read_profile`); `enabled=False` → no-op, store empty. (`QueryEvent` built with all required fields.)
- **U5:** cluster groups by `table_scope`; draft produces one audited `harvested` Gotchas section per cluster with metadata; a memory text tripping the contamination guard **raises**, not drafts.
- **U6:** `write_doc` round-trips a `ScentDoc` through disk + `docs()`; `apply_approved_sections` writes only approved sections into the domain doc (rejected omitted), merging into an existing doc without dropping prior sections.
- **U7:** fingerprint is order-independent; `is_stale` True on drift, False when equal, False when baseline None.
- **U8:** headless test of the glue — harvester constructed disabled under a benchmark flag; approved sections land in a temp `MazeStore`, rejected omitted.
- **U9:** full suite (1061 baseline + new) green; benchmark-path exclusion holds.

## 6. Non-goals (this milestone)

Autonomous scheduled harvesting, dbt-CI at-source pairing, embedding-based clustering (T2b v2); T1b dbt semantic-layer/metric ingestion; T3c provenance footer + T2c first-connect Cartographer; git-versioned team memory (2.3); customer-facing evals (2.4); decision-trail harvesting (2.5). Each is a later, separately-spec'd increment.

## 7. Build decomposition (plan phases)

- **Plan 0:** U1 → U2 → U3.
- **Plan 1:** U4 → U5 → U6 → U7 → U8 → U9.
