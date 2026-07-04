# Cartographer Attached-DB Grounding + Code/Name Detection + Beefed-Up Semantics — Design

**Date:** 2026-07-04
**Status:** Design — awaiting user review before writing-plans
**Branch:** `feat/cartographer-attached-db-grounding` (proposed)
**Source:** Trace-level forensic autopsy `docs/pancancer-cartographer-autopsy-2026-07-04.md` (Fable + code-verified). Overturns the "LLM-semantic Scent is dead" verdict (memory `project_llm_semantic_scent`) to **confounded**: the T1c (−3.7pp) / M2 (−7.8pp) semantics ablations ran against a Cartographer that could not see the table the grounding was built for.

## Motivation

The Sonnet-5 M2 ablation scored pancancer_atlas **baseline 0.67 → +semantics 0.44**, and we concluded LLM-semantic Scent is net-harmful. The autopsy found the negative is a **measurement artifact of a DB-coverage bug**:

- **Root cause (code-verified, `env.py:74-75,100-107`):** only DuckDB databases enter `ctx.connections`; Postgres/SQLite become `AttachSpec`s in a separate `attachable` list ("*dropped silently until adapters land*"). `generate_scent`/`cartograph_prepass` iterate `ctx.connections`, so the Cartographer **never profiles attached Postgres/SQLite tables.** On pancancer, the code column `icd_o_3_histology` lives in the Postgres `clinical_info` table → the Cartographer (and the M2 code-vs-name ROLE mechanism built to fix pancancer) never ran on the table that needs it. The LLM authored guidance only for the DuckDB `molecular_database` — the wrong DB.
- **pancancer:1 (fails 0/3 in *both* arms):** agent `GROUP BY histological_type` (name column, 3 distinct) instead of `icd_o_3_histology` (code column); arithmetic byte-identical to GT but emits a name where the validator wants a code. A grounding **gap**, invisible because of the DB bug.
- **pancancer:3 (M2 regressed 3/3→1/3):** half an independent chi-square variance bug (dropped zero cell — not semantics), half a real-but-small semantics over-restriction (a Best-Practice nudged a cohort-narrowing JOIN).

This build fixes the DB-coverage bug, adds the deterministic grounding that was supposed to fix pancancer:1, tightens the semantics instruction, and beefs up the LLM authoring so a re-ablation is finally a *valid* test of semantic Scent.

**Non-negotiables:** the benchmarked **agent runtime is unchanged** (the re-ablation baseline must stay comparable to the old 0.773 — C1 touches only the Cartographer's profiling path, never the agent's `ctx.connections`); GT-firewall preserved (Cartographer reads only DB metadata + sampled rows, never validator/answer-key files); every frozen doc still passes `audit_scent_doc` (fail-loud); deterministic path stays default; C1 adds nothing for DuckDB-only datasets (byte-identical w.r.t. C1), while C2 deliberately adds a code/name section where a code+name pair exists (an intentional deterministic-output change, updated golden tests); semantics stays default-off until the re-ablation proves it net-positive.

## Current-state anchors (code-verified 2026-07-04)

- `eval/benchmarks/dab/env.py`: `AttachSpec{alias, path, db_type}` (`path` is the DuckDB-ATTACH arg — a libpq string for postgres, a filesystem path for sqlite); `build_dab_task_env` returns `DabTaskEnv{ctx, attachable, mongo}`; only DuckDB DBs go into `ctx.connections`.
- `eval/benchmarks/dab/suite.py:361 _run_cartographer` builds `connections/catalogs/primary` and calls `cartograph_prepass(...)` at :395. This is the DAB-layer seam where AttachSpec→profiling-connection translation belongs (keeps the Cartographer core DB-agnostic).
- `db/duckdb_engine.py:61 DuckDBConnection.attach(path, alias, db_type)` — the exact ATTACH mechanism the agent uses at runtime; `:155 introspect_catalog()` reads the live schema.
- `maze/cartographer.py`: `generate_scent(connections, catalogs, primary, with_semantics, llm_fn, ...)` (`:409`) iterates connections, one `ScentDoc` per connection; `cartograph_prepass(...)` (`:506`) caches-or-generates; `build_quick_reference`/`build_key_tables` (`:75`)/`build_dimensions` (`:220`)/`discover_joins` (`:297`); `_SEMANTICS_INSTRUCTION` + `draft_semantics` + `merge_sections`; audit via `audit_scent_doc` (fail-loud).
- `maze/semantic_claims.py`: `_CODE_SHAPE_RE`, `_looks_like_code`, `verify_role_claim`, `RoleClaim` — the code-shape logic C2 repurposes from *verifier* to *detector*.

## The four units

### Unit C1 — Profile attached Postgres/SQLite in the Cartographer

The DAB prepass gains a read path to profile attachables, **without touching the agent's `ctx.connections`** (agent runtime unchanged).

- New helper (DAB layer, e.g. `env.py` or a new `env_profiling.py`): `build_profiling_connections(attachable: list[AttachSpec]) -> tuple[dict[str, Connection], dict[str, Catalog]]`. For each `AttachSpec` (postgres|sqlite), create a fresh `:memory:` `DuckDBConnection`, call `.attach(spec.path, spec.alias, spec.db_type)`, `.connect()`, and `.introspect_catalog()` so the attached tables are visible **attach-qualified** (`<alias>.<table>`) — matching the agent's runtime access pattern. Returns profiling-only connections keyed by `spec.alias`.
- `_run_cartographer` (suite.py) merges these profiling connections/catalogs into the dicts passed to `cartograph_prepass` (in addition to the real DuckDB connections). Result: one Scent doc per attached DB alongside the DuckDB docs, via the normal `generate_scent` loop — so **C2 and C4 apply to attached DBs automatically.**
- The Cartographer core (`generate_scent`/`cartograph_prepass`) is **unchanged** — it profiles whatever connections it is handed; it never learns about `AttachSpec`. Postgres profiling requires the DAB-local Postgres to be running (it is, during trials — the agent attaches it). If an attach fails (server down, missing extension), the helper **skips that spec with a logged warning** and continues (never aborts the prepass).
- Scope: **postgres + sqlite** (both ATTACH-able via one DuckDB mechanism). Mongo deferred (materialized via `load_mongo_collection`, a separate path).

### Unit C2 — Deterministic code/name-pair detector (no LLM, baseline path)

A new author-time Cartographer builder that emits **verified** grounding notes, running on the deterministic (no-LLM) path so pancancer:1 is fixable without semantics.

- New `build_code_name_notes(profile, connection, ...) -> Section | None` in `cartographer.py`. Per table, scan column pairs for a **code column** (code-shaped by `_CODE_SHAPE_RE`/`_looks_like_code`, ≥ threshold) that **co-varies** with a lower-code-shape **display-name column** (each code maps to ≤ small number of names; the name column is not itself code-shaped — reuse the M2 `_NAME_CEILING` idea). Emit one bullet per confirmed pair: *"For coded values in `<table>`, group/filter by `<code_col>` (the code); `<name_col>` is the display label — grouping by the name column collapses distinct codes."*
- Repurposes the M2 role logic from **verifier** (checks one LLM claim) to **detector** (scans all pairs). Conservative: when ambiguous, emit nothing (false-silence is safe; a wrong note is the failure to avoid). Section tagged `source="verified"`. Rendered into every doc (deterministic + attached-DB), retrieved via `search_reference_docs`.
- Wire into `generate_scent`'s per-connection section list (alongside `build_dimensions` etc.), so it runs on **both** the DuckDB and attached-DB connections and on the `with_semantics=False` baseline.

### Unit C3 — Tighten `_SEMANTICS_INSTRUCTION` (cohort-vs-filter)

Add a rule to the existing instruction: a quality/status filter scopes **which rows count as positive** (the numerator), never the **cohort denominator** (the population). Explicitly forbid authoring a Best-Practice that narrows the population (e.g. "restrict to the FILTER='PASS' / sequenced subset"). Small prompt change + golden-string test. Composes with C4.1. Targets pancancer:3's over-restriction.

### Unit C4 — Beef up the LLM authoring option

Two levers, both attacking the "misleading, unsupported prose" failure mode that sank T1c/M2. (Expanded claim grammar — grain/precision claims — is **out of scope** here, deferred to a follow-up gated on the re-ablation.)

- **C4.1 — Ground the author in the verified skeleton.** The authoring prompt already includes the deterministic skeleton (Quick Ref/Key Tables/Dimensions). After C1+C2 that skeleton also contains the **attached-DB tables** and the **verified code/name notes**. Update `_SEMANTICS_INSTRUCTION` (and `_semantics_prompt`) to instruct the author to **build conditional routing guidance on top of these verified facts and never introduce a claim the facts don't support** — annotator, not inventor. (No new data flow; the richer skeleton flows in for free once C1+C2 land.)
- **C4.2 — Self-critique / prune pass.** After `draft_semantics` produces prose, run a **bounded LLM self-critique pass**: given the verified skeleton and the drafted bullets, return only the bullets each *fully supported by a verified fact*, verbatim (this is what catches the T1c/M2 failure mode — a misleading bullet often *names* real columns but makes an unsupported claim, so a deterministic "mentions a real column" filter would miss it; the critique judges the claim, not the vocabulary). **Fail-open:** any error or unparseable critique keeps the original draft (never worse than today). Prose stays `source="draft"`. The critique reuses the same authoring `llm_fn` (same model/billing).

## Data flow (DAB claude-mcp prepass, with attachables + semantics)

1. `build_dab_task_env` → `DabTaskEnv{ctx (DuckDB), attachable (PG/SQLite), mongo}` (unchanged).
2. `_run_cartographer`: `build_profiling_connections(env.attachable)` → profiling connections/catalogs; **merge** with `env.ctx.connections`/catalogs (agent's `ctx` untouched).
3. `cartograph_prepass(merged_connections, merged_catalogs, primary, scent_dir, with_semantics, llm_fn)` → `generate_scent`:
   - per connection (DuckDB **and** attached): Quick Ref / Key Tables / Dimensions / **C2 code-name notes** (deterministic, always).
   - if `with_semantics`: `draft_semantics` (skeleton now includes attached tables + code-name notes; **C4.1** grounding instruction; **C3** cohort rule) → **C4.2** prune → merge → `audit_scent_doc` (fail-loud).
4. Write docs; agent retrieves via `search_reference_docs` at runtime (agent attaches the same DBs under the same aliases → Scent names match).

## Testing (fixture-based; authoring LLM stubbed)

- **C1:** primary test uses a **sqlite fixture file** (no server needed): `build_profiling_connections` on a sqlite `AttachSpec` yields a connection whose `introspect_catalog` lists the attach-qualified tables, and the generated Scent doc for the attached DB contains those tables. Assert a failed attach (bad path/unreachable server) is **skipped with a logged warning, no exception**. Postgres profiling is exercised end-to-end by the re-ablation (DAB-local PG); no unit-test PG server is assumed.
- **C2:** fixture table with a code+name pair (e.g. `icd_o_3_histology` `9382/3…` + `histological_type` `Astrocytoma…`) → note emitted naming the code column as the grouping key; a name-only table → no note; a reversed/ambiguous pair (name column mis-cast as code) → dropped (reuse M2's `_NAME_CEILING`/digit-shape guards). Assert C2 runs on the `with_semantics=False` path.
- **C3:** golden-string assertions on `_SEMANTICS_INSTRUCTION` (contains the cohort-vs-filter rule; forbids population-narrowing Best-Practices).
- **C4.1:** golden-string on the grounding instruction (author builds on verified facts / does not introduce unsupported claims).
- **C4.2:** stub `draft_semantics` output with one supported + one unsupported bullet → prune keeps the supported, drops the unsupported; a prune-step error keeps the full draft (fail-open).
- **Regression:** full suite green. **Byte-identity scope:** C1 adds no docs for a DuckDB-only dataset (identical w.r.t. C1). **C2 intentionally changes deterministic Scent** — it adds a code/name section wherever a code+name pair exists — so it is NOT byte-identical by design; update the affected golden tests deliberately and document the change in the commit. A DuckDB-only dataset with **no** code/name pair remains byte-identical (proves C2 is silent when nothing to say). GT-firewall: `audit_scent_doc` still fail-loud on the merged doc (now including attached-DB + code-name content).

## Re-ablation (after build — one run, as requested)

Same 5-dataset subset (`deps_dev_v1, music_brainz_20k, stockindex, pancancer_atlas, yelp`), Sonnet 5, claude-mcp, `--hints`, n=3, **2 new arms** vs the existing old-baseline **0.773**:

- **baseline-fixed** = deterministic Cartographer + C1 (attached-DB profiling) + C2 (code/name notes). Measures whether fixing the DB gap + code/name grounding lifts the *deterministic* baseline — esp. **pancancer:1 recovering** (emits the code column).
- **semantics-fixed** = + C3 + C4 semantics on top of baseline-fixed, authored on Sonnet 5. Measures whether semantic Scent finally clears structure-only now it sees the right table and is grounded + pruned — esp. **pancancer:3 not regressing.**

Keep-if-net-positive; both stay default-off until proven. This is the single "run ablation for all 3 after" run. (n=3 → treat as directional; a clean pancancer recovery is the primary signal.)

## Non-goals

- Changing the agent's runtime `ctx.connections` / attach flow (would break baseline comparability).
- Mongo profiling (deferred; separate `load_mongo_collection` path).
- Expanded structured-claim grammar (grain/precision claims — C4 lever 3, deferred to a re-ablation-gated follow-up).
- New product/`run_agent_task` params (DAB path first).

## Decomposition into plan phases

- **Phase 1 — C2 detector** (`build_code_name_notes`, pure/deterministic, fixture-tested). Independent of C1.
- **Phase 2 — C1 attached-DB profiling** (`build_profiling_connections` + `_run_cartographer` wiring; Cartographer core untouched). Un-blinds the prepass; C2 then runs on attached DBs.
- **Phase 3 — C3 + C4.1 instruction changes** (`_SEMANTICS_INSTRUCTION` cohort rule + grounding rule; golden-string tests).
- **Phase 4 — C4.2 prune pass** (`draft_semantics` → prune; fail-open; stubbed-LLM tests).
- **Phase 5 — regression + byte-identity (DuckDB-only) + audit fail-loud + DAB path smoke.**
