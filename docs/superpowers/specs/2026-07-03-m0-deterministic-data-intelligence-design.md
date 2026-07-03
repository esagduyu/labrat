# M0 — Deterministic Data-Intelligence Pack — Design

**Date:** 2026-07-03
**Status:** Design — awaiting user review before writing-plans
**Branch:** `feat/deterministic-levers` (proposed)
**Source:** Milestone M0 of `docs/superpowers/plans/2026-07-03-competitive-build-milestones.md`, itself derived from `docs/competitive-analysis-2026-07-03.md` §4 (features 1.2, 1.3, 1.4, 3.1, 3.3, 3.4, 3.5).

## Motivation

The competitive deep-dive found that several leaderboard competitors get durable value from **deterministic data-intelligence** — pre-execution SQL validation (Altimate), join-transform detection with prescribed normalization SQL (DataBridge), value-range/format-aware profiling (Pi, SCRIBE), and text-normalization SQL functions (DataBridge). These are structure-only, GT-firewalled, and — unlike prompt/process levers, which the 2026-07-03 ablation showed are **model-dependent** (neutral/negative on Sonnet 4.6, +7.5pp on Sonnet 5) — they behave like the Cartographer (which lifted Sonnet 4.6 +8pp) and, more importantly, are **genuine product improvements** that help the TUI and any model.

M0 ships seven such features as one **Deterministic Data-Intelligence Pack**. They are designed and validated as durable tool wins (fixture tests + the ADE 9-task smoke), not as DAB-tuning levers — and they become Sonnet-5 submission levers when that run happens.

**Non-negotiables (all seven):** no LLM calls; GT-firewalled by construction (read only DB metadata/sampled rows, never validator/answer-key files); benchmark-safe (structure/process only, no answer content); each independently testable with zero API calls.

## Current-state anchors (code-verified 2026-07-03)

- `sqlglot` 30.8.0 is already a dependency and already used in `agent/tools/run_sql.py` (`import sqlglot`, `from sqlglot import exp`, `ParseError`) — no new dep for `check_sql`.
- Tools follow `Tool[InputT]` (`agent/tools/base.py`); `verify_join.py` is the reference for a pure/deterministic tool with `_Input`/`_Output`; registered in `agent/data_tools.py::build_data_tools_registry()`.
- Cartographer: `maze/cartographer.py` has `_candidate_joins(profile)` (line 73), `build_dimensions(profile, conn, *, cap=25)` (118), `generate_scent(..., distinct_cap=25)` (202), `cartograph_prepass(..., distinct_cap=25)` (283). It already `verify_join`-confirms candidate joins and emits a Dimensions section — the natural home for join-transform + format-sampling + compaction.
- DuckDB adapter: `db/duckdb_engine.py::connect()` (line 36); `self._connection` is a live `duckdb.DuckDBPyConnection` supporting `create_function`/`CREATE MACRO`.
- Contamination: `detect_contamination` imported as `_detect_contamination` from `maze/scent_audit.py`, used per-trial at `dab/suite.py:568` (→ `reason="contaminated:<tag>"`, excluded by `aggregate()`); `scripts/eval_dab.py` assembles submissions.
- Prompt levers: single-sourced in `agent/prompts/levers.py` (`EXECUTION_LEVERS`, `PARSE_ROBUSTNESS_LEVERS`, `all_levers`) — from the DAB parse-robustness build.

## The five units

### Unit 1 — `check_sql` tool (feature 1.2)

**Files:** `agent/tools/check_sql.py` (new), registered in `data_tools.py`.

Deterministic pre-execution validator. Parse the input SQL with `sqlglot` (dialect from the target connection); resolve every referenced table and column against `ctx.catalogs[db]`; return the unresolved references, each with the Levenshtein-closest real name(s) as a suggestion. No execution, no LLM.

- `_Input`: `sql: str`, `database: str | None` (route via `ctx.connections[db or ctx.primary]`, mirroring `verify_join`).
- `_Output`: `valid: bool`, `unknown_tables: list[{ref, suggestions}]`, `unknown_columns: list[{table, ref, suggestions}]`, `parse_error: str | None`.
- On a `sqlglot` `ParseError`, return `valid=False` + `parse_error` (don't raise). Qualify unqualified columns against the FROM/JOIN tables in scope; if a column is genuinely ambiguous (in >1 in-scope table), don't flag it.
- Suggestions: closest names by Levenshtein over the catalog's table/column names, cap ~3, only when distance is below a small threshold (avoid noise).

### Unit 2 — `normalize_text()` SQL function (feature 3.1)

**Files:** `db/duckdb_engine.py` (register in `connect()`), a small helper for the SQL body.

Register a DuckDB **`MACRO`** `normalize_text(x)` = lowercase → strip accents/diacritics → collapse/trim whitespace → drop non-alphanumeric (tunable), on every `DuckDBConnection.connect()`. Idempotent (`CREATE OR REPLACE MACRO`).

**Scope decision (deliberate limitation):** DuckDB-only. This covers the entire DAB path (SQLite/Postgres/Mongo all attach or materialize *into* the DuckDB session) and the primary session DB. Remote warehouses (Postgres/Snowflake/BigQuery/…) would require DDL privileges we frequently lack and that a read-only posture blocks; registering there is out of scope. Documented in the adapter and surfaced to the agent (a one-line note in the tool/prompt that `normalize_text(col)` is available on the DuckDB session for case/diacritic/whitespace-insensitive matching). The other six adapters are unchanged.

### Unit 3 — Cartographer enrichments (features 1.3, 1.4, 3.4)

**Files:** `maze/cartographer.py` (extend `_candidate_joins`, `build_dimensions`/profiling render, and the doc assembly); size-budgeted throughout.

1. **Join-transform detection (1.3).** For a candidate join that `verify_join` reports as *not* cleanly matching, sample a bounded set of key values from both sides and test a fixed library of deterministic transforms — zero-pad-to-width, strip-leading-`\d+[-.]`, extract-digits (`regexp_replace('[^0-9]','')`), lower+trim, strip-parentheticals/subtitles. If a transform lifts the match-rate above a threshold, emit a **`## Join Keys`** line with the exact normalization SQL for each side (e.g. `left: REGEXP_REPLACE(CAST(a AS VARCHAR),'[^0-9]','','g')`). Deterministic; extends the existing `verify_join`-confirmed-joins pass from *diagnose* to *prescribe*.
2. **Value-ranges + stratified format-sampling (1.4).** In the per-column profiling that feeds Scent: add min/max (numeric/date), distinct-count/cardinality, and top-N values (reusing/extending `build_dimensions`' cap). Additionally, when sampling example rows, deliberately include rows whose values have **unusual structure** — embedded separators (`>`, `::`, `|`), delimiter-encoded paths, very long or JSON-in-text values — not just the first N. Surfaces parsing traps (the github_repos text-count, deps `>`-path classes from the autopsy) up front. Budget: a `format_sample_cap` (default small, e.g. 3) per qualifying column, folded into the existing `distinct_cap` budget accounting.
3. **Wide-DB schema compaction (3.4).** When the schema has many structurally-identical tables (same column set — e.g. per-ticker stockmarket/stockindex tables), collapse them in the Scent structure section to one representative schema + a compact name list ("⚠ N tables share this structure: [t1, t2, …]") instead of N full renders. Threshold on identical-column-signature count.

All three are GT-firewalled (DB metadata + sampled values only) and run through the existing `scent_audit.py` contamination guard on the assembled doc.

### Unit 4 — `top_n_with_ties` lever (feature 3.5)

**Files:** `agent/prompts/levers.py`.

One process line — "For 'top N' questions, remember `LIMIT N` silently truncates ties; if the Nth value can repeat, rank with ties (e.g. a window `RANK()`/`DENSE_RANK()` or fetch the tie band) rather than a bare `LIMIT`." Added to the lever set (its own group or `EXECUTION_LEVERS`, TBD in the plan). Note: this is a prompt lever, so its benchmark value is model-dependent (per the ablation) — included for completeness and product value; near-zero cost.

### Unit 5 — Taint-audit-as-gate (feature 3.3)

**Files:** a small audit module (e.g. `eval/benchmarks/dab/taint.py`) + `scripts/eval_dab.py` submission-assembly path.

Upgrade contamination handling from per-trial *detect-and-withdraw* to a **pre-submission gate**. A function that scans a run's trials + trace files (`mcp_tool_calls.jsonl`/`agent_tool_calls.jsonl`) and classifies each trial as `clean` / `external-oracle-cheating` (answer-key/validator/HF-label access) / `audit-error` (missing/unreadable trace), writes `taint.json` (per-trial verdicts), and returns a status. `eval_dab.py`'s submission assembly calls it and **refuses to emit `submission.json` (non-zero exit) if any trial is not `clean` and not already withdrawn** — so a submission can never be assembled from an unaudited run. Reuses `detect_contamination`'s pattern list (single source). Detection-only backstop at `suite.py:568` stays; this adds the *gate*.

## Testing

Every unit is unit-testable with fixture DBs and **zero LLM/API calls**:
- **Unit 1:** fixture DuckDB with a known schema; assert `check_sql` flags a typo'd column with the correct suggestion, passes clean SQL, and returns `parse_error` (not raise) on malformed SQL; ambiguous-column case not flagged.
- **Unit 2:** register the macro; assert `SELECT normalize_text('Café  Del  Mar')` → `cafedelmar` (or the chosen normal form); idempotent re-register.
- **Unit 3:** fixture with (a) a format-shifted join key (`12345` ↔ `CUST-0012345`) → assert the emitted normalization SQL; (b) a prose column with embedded separators → assert a format-sample row is surfaced + value-ranges present; (c) N identical-structure tables → assert compaction render. Assert `audit_scent_doc` still passes on the enriched doc.
- **Unit 4:** assert the lever string is present in the rendered prompt surfaces (mirrors the parse-robustness lever tests).
- **Unit 5:** a trace with an injected `ground_truth.csv` read → assert `taint.json` classifies it `external-oracle-cheating` and the gate exits non-zero; a clean trace → exit zero.
- **Regression:** ADE 9-task smoke (`run_smoke_regression.py check`) after the Cartographer + lever changes (product-path gate; needs Docker).

## Decomposition into plan phases

- **Phase 1 — `check_sql` tool** (Unit 1). Self-contained, highest product value.
- **Phase 2 — `normalize_text` macro** (Unit 2). Self-contained.
- **Phase 3 — Cartographer enrichments** (Unit 3). The largest unit; internally ordered join-transform → value-ranges/format-sampling → compaction.
- **Phase 4 — `top_n_with_ties` lever** (Unit 4). Trivial.
- **Phase 5 — Taint-audit gate** (Unit 5). Eval-infra; independent of 1–4.

Each phase is independently testable and mergeable. The plan will TDD them in this order.

## Open questions for the plan

1. `check_sql` Levenshtein threshold + suggestion cap (pick concrete constants).
2. `normalize_text` exact normal form (drop-non-alphanumeric vs keep-spaces) — pick one; align with the entity-resolution levers.
3. Cartographer `format_sample_cap` default and the "unusual structure" detection predicates (separator chars, length threshold).
4. Wide-DB compaction: identical-column-signature threshold (how many tables trigger collapse).
5. `top_n_with_ties`: extend `EXECUTION_LEVERS` vs a new group (either fine; single-source in `levers.py`).
6. Taint module location (`eval/benchmarks/dab/taint.py`) + exact `taint.json` schema.

## Non-goals (M0)

- Column-level lineage / `explain_lineage` (M3), per-row LLM primitives / program mode (M4), verification-v2 (M1), semantic-Scent redo (M2) — later milestones.
- `normalize_text` on remote warehouses (documented limitation).
- Any prompt/process lever beyond `top_n_with_ties` (model-dependent; deferred).
