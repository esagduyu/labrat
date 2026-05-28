# DAB-Bench Integration Design

**Status:** Draft (2026-05-27, revised 2026-05-28 to align with unified-suite layout)
**Branch:** `feat/dab-integration` (fully isolated; abandonable if regressions occur)
**Author:** Ege (via Claude brainstorm)
**Related:** [`2026-05-28-unified-benchmark-suite-design.md`](2026-05-28-unified-benchmark-suite-design.md) — file layout and protocol come from there; this spec is the DAB-specific contract on top.

## Goals

1. **Competitive positioning.** Score competitively on the DAB (Data Agent Bench) public leaderboard. Target: top-3 (beat Altimate at 60.4%, ideally approach MinusX at 63.1%).
2. **Expand LabRat's agent infrastructure.** Build the multi-DB tools as first-class LabRat capabilities — usable by the TUI, ADE-bench, future benchmarks, and a potential standalone data-agent SDK/plugin.
3. **Lay groundwork for a reusable benchmark framework.** Don't over-engineer it up front, but make the second benchmark (DAB after ADE-bench) close enough in shape that the third one becomes a ~100-line addition.
4. **No API costs.** Inference through `ClaudeCodeProvider` (Mac OAuth via `claude` CLI on the Max plan).

## Working Agreement

- All implementation work happens on the `feat/dab-integration` branch. Master is not touched until Phase 1c exits successfully.
- The branch is **abandonable**: if ADE-bench regresses irrecoverably, or the score plateau is uncompetitive, we can delete the branch with zero damage to master.
- Each phase exit requires the **ADE-bench smoke regression check** to pass (see Testing section).
- The spec is the contract — material design changes require updating this doc before code changes.

## Background

DAB (UC Berkeley × Hasura PromptQL) is the first benchmark for evaluating data agents on realistic, complex, multi-database tasks. Key facts:

- **54 queries across 12 datasets**, 4 DBMSes (PostgreSQL, MongoDB, SQLite, DuckDB)
- Every dataset spans 2–6 databases (crmarenapro spans 6)
- **Stratified scoring**: each of the 12 datasets weighted equally regardless of query count
- **Pass@1 metric**, estimated from 5 trials per query (270 trials total)
- **String-match validation**: per-query `validate.py` checks substring presence in agent's text answer
- **Submission**: PR to DataAgentBench repo with a `submission.json` file
- **Current leaderboard**: MinusX 63.1% · Altimate 60.4% · Spacedock 57.7% · Pi 56.0% · PromptQL 54.3%

DAB's defining challenges (relative to single-DB SQL benchmarks):
- **Multi-database integration** — must join across heterogeneous systems
- **Ill-formatted key joins** — fuzzy entity resolution across system boundaries
- **Unstructured text transformation** — parsing text/JSON/HTML at query time
- **Domain knowledge** — schema conventions, MongoDB syntax, etc.

LabRat currently has none of these capabilities in its agent loop. The integration work is the value, not just plumbing for a score.

## Architecture

### Module layout

```
src/labrat/
├── db/
│   ├── mongodb_engine.py         # NEW — MongoDBConnection adapter
│   └── federation.py             # NEW — DuckDB ATTACH helpers for cross-DB SQL
├── agent/
│   ├── context.py                # CHANGED — ToolContext.connections: dict[str, Connection]
│   └── tools/
│       ├── list_databases.py     # NEW
│       ├── attach_database.py    # NEW
│       ├── execute_python.py     # NEW (Tier 1: subprocess; Tier 2 Docker deferred)
│       ├── load_mongo_collection.py  # NEW
│       ├── run_sql.py            # CHANGED — optional `database` param
│       ├── list_tables.py        # CHANGED — same
│       └── describe_table.py     # CHANGED — same
└── eval/
    ├── types.py                  # NEW — BenchmarkTask / TrialResult / AggregateScore / BenchmarkReport / BenchmarkSuite protocol (per unified-suite spec)
    ├── smoke.py                  # NEW — SubsetSuite + ade_smoke_suite()  (replaces the originally-proposed eval/suites/ade_smoke.py)
    └── benchmarks/
        ├── dab/                  # NEW
        │   ├── suite.py          # DabSuite implements BenchmarkSuite
        │   ├── env.py            # multi-DB ToolContext factory
        │   ├── scorer.py         # wraps per-query validate.py  (was: eval/validators/dab.py)
        │   └── reporter.py       # submission.json writer
        └── ade_bench/            # NEW — port from legacy eval/suites/ade_bench.py + eval/runners/ade_bench_runner.py
            ├── suite.py
            ├── external_runner.py
            └── reporter.py

scripts/
├── dab_setup.py                  # NEW — one-time PG/Mongo data loader
├── eval_dab.py                   # NEW — CLI entrypoint; hosts DAB's interim runner (concurrency / jsonl / resumability) in Phase 1; switches to BenchmarkOrchestrator in Phase 4
└── run_smoke_regression.py       # NEW — runs ade_smoke_suite() and diffs against tests/baselines/ade_smoke_baseline.json
```

**Layout note:** the originally-proposed `eval/suites/dab.py`, `eval/runners/dab_runner.py`, `eval/validators/dab.py`, and `eval/suites/ade_smoke.py` were superseded by the unified-suite layout (see related spec). DAB's per-trial logic lives in `benchmarks/dab/suite.py`; concurrency / jsonl / resumability live inline in `scripts/eval_dab.py` during Phase 1 and migrate to `eval/orchestrator.py` in Phase 4.

### Multi-DB ToolContext

Single breaking change with a backwards-compat shim:

```python
@dataclass
class ToolContext:
    connections: dict[str, Connection]    # was: connection: Connection
    catalogs: dict[str, Catalog]          # was: catalog: Catalog
    primary: str                          # NEW — default connection name
    history: QueryHistoryLog
    memory: MemoryStore
    # ...

    @property
    def connection(self) -> Connection:   # shim — preserves existing TUI/test usage
        return self.connections[self.primary]

    @property
    def catalog(self) -> Catalog:         # shim — same
        return self.catalogs[self.primary]
```

The shim is permanent (not deprecated) — single-DB use is a legitimate ongoing pattern, not a transition state.

### Inference backend

`ClaudeCodeProvider` shells out to the `claude` CLI for inference. Zero API cost on the Max plan.

**Critical assumption to validate in Phase 0:** `ClaudeCodeProvider` correctly exposes LabRat's tool registry to the model (the `claude` CLI's own Bash/Read/Edit tools should not interfere with LabRat's `run_sql` / `list_databases` / etc.). If this assumption is wrong, the entire design pivots to a Bash-tool-prompting approach (the LabratLocalAgent pattern, adapted for query-answering).

### Tool-registry scaling (note, not built now)

Anticipating the Altimate-100-tool world: every new tool gets a `category` field (`db_ops`, `analysis`, `exec`, `io`). Once the registry grows past ~25 tools, we can introduce a `pack` selector to filter what's exposed to the model per task. For now, flat registry is fine — DAB needs ~6 new tools, well under the threshold.

## New Tools

Each tool is a first-class LabRat tool. Inputs are Pydantic `BaseModel`s, outputs are JSON-serializable dicts wrapped in `DispatchResult`.

### `list_databases`

- **Input:** none
- **Output:** `[{name: str, type: str, description: str | None}]` — one entry per connection in `ctx.connections`
- **Used by:** any multi-DB task (the agent's first call to discover available DBs)

### `attach_database` (DuckDB federation — LabRat's secret weapon)

- **Input:** `database: str` (named connection), optional `alias: str`
- **Output:** `{attached_as: str, tables: list[str]}`
- **Behavior:**
  - For PostgreSQL connections: `INSTALL postgres_scanner; LOAD postgres_scanner; ATTACH 'postgresql://...' AS <alias>;`
  - For SQLite connections: `ATTACH '<path>' AS <alias>;` (built-in)
  - Idempotent: re-attaching is a no-op
- **Used by:** 10 of 12 DAB datasets (covers all PG + SQLite joins). The agent typically calls `list_databases` → `attach_database` for each non-DuckDB source → `run_sql` with cross-DB SQL.

### `load_mongo_collection`

- **Input:** `database: str` (a Mongo connection), `collection: str`, optional `filter: dict`, optional `projection: dict`, optional `limit: int`, optional `target_table: str`
- **Output:** `{target_table: str, row_count: int, columns: list[str]}`
- **Behavior:**
  - Query Mongo via `pymongo` with provided filter/projection
  - Flatten nested docs one level (deeper nesting → JSON-string column)
  - Write to DuckDB temp table (`temp.<target_table>`, auto-named if absent)
- **Used by:** 2 DAB datasets (agnews + yelp). After this call, the agent can join Mongo data with relational data via standard `run_sql`.

### `execute_python` (Tier 1 — subprocess sandbox)

- **Input:** `code: str`, optional `timeout_seconds: int` (default 60, max 300)
- **Output:** `{stdout: str, stderr: str, exit_code: int, files_created: list[str], spool_path: str | None}`
- **Behavior:**
  - Run `python3 -c <code>` in subprocess
  - `cwd=<scratch_dir>` (per-trial, persistent across calls within a trial)
  - `env=` restricted to `PATH`, `HOME=<scratch_dir>`, `PYTHONDONTWRITEBYTECODE=1` — strips API keys and other secrets
  - `resource.setrlimit`: address space cap 4 GB, file size cap 1 GB, CPU time = timeout
  - Stdout truncated at 10k chars; full output spooled to `<scratch_dir>/exec_<id>.out`
- **Pre-flight code check:** regex reject for `subprocess`, `os.system`, hardcoded absolute paths outside scratch dir. Not bulletproof; catches obvious mistakes.
- **Available packages** (pinned in `pyproject.toml` `[project.optional-dependencies]`):
  `pandas`, `pyarrow`, `duckdb`, `polars`, `pymongo`, `psycopg2-binary`, `numpy`, `requests`
- **Tier 2 (deferred):** Docker sandbox matching DAB reference scaffold. Only build if Tier 1 produces an incident or before extracting LabRat for external users.
- **Used by:** DAB tasks needing fuzzy entity resolution or unstructured-text parsing. Estimated ~20% of queries.

### `run_sql` (extended)

- **Input change:** add optional `database: str | None`. Absent → use `ctx.primary`.
- **Backwards compat:** unchanged for existing call sites; existing TUI and ADE-bench paths never set `database`.

### `list_tables`, `describe_table` (extended)

Same pattern as `run_sql`. Optional `database` param, defaults to primary.

### `MongoDBConnection` adapter (`src/labrat/db/mongodb_engine.py`)

Implements the `Connection` ABC:
- `connect`: `pymongo.MongoClient(uri)`
- `disconnect`: close client
- `introspect_catalog`: list collections; sample 100 docs per collection to infer columns; nested fields → JSON-string columns. Returns the standard `Catalog / Schema / Table / Column` shape.
- `execute(query)`: `query` is a JSON pipeline string (e.g., `'[{"$match": {"x": 1}}, {"$group": ...}]'`). Returns a Polars DataFrame (flattened, JSON-string for deep nesting).
- `explain(query)`: returns `db.command('explain', ...)` output as a Polars frame.

This is **non-trivial**: Mongo's lack of schema is a real impedance mismatch. The introspection is best-effort — agent prompts will need to acknowledge that Mongo column lists are samples, not authoritative.

## DAB Eval Infrastructure

### `DabSuite` (`src/labrat/eval/benchmarks/dab/suite.py`)

- **Enumeration:** walks `<dab_dir>/query_*/query*/` to build an `EvalCase` per (dataset × query) pair
- **EvalCase fields:** `id` (`"<dataset>:<query_n>"`), `question`, `dataset`, `db_config` (parsed YAML), `db_description`, `validator_path`
- **Hint flag:** `DabSuite(hints=True)` reads `db_description_withhint.txt` (preferred for competitive submission); `hints=False` reads `db_description.txt`
- **Filtering:** `datasets=[...]`, `queries=[...]` (subset selection for dev iteration)

### DAB interim runner (in `scripts/eval_dab.py` during Phase 1; → `eval/orchestrator.py` in Phase 4)

`DabSuite` implements `BenchmarkSuite.run_trial`, which is the per-trial flow below. The surrounding interim runner (concurrency, jsonl streaming, resumability) lives inline in `scripts/eval_dab.py` during Phase 1 and migrates to the shared `BenchmarkOrchestrator` in Phase 4. The interim runner must contain no DAB-specific logic — see the unified-suite spec's Phase 4 commitment.

Per-trial flow (= `DabSuite.run_trial`):
1. Build `ToolContext` with `connections` populated from `db_config.yaml` (DuckDB always present as primary; PG/Mongo/SQLite added as named connections)
2. System prompt: `db_description` text + standard LabRat agent instructions + DAB-specific guidance ("answer in plain text, return your final answer once the question is fully resolved")
3. Run `AgentLoop` with `max_turns=100`
4. Capture: final answer, tool call count, runtime, model usage, failure mode
5. Pass answer to `DabValidator` → `(passed: bool, reason: str)`

Pass@5 and submission JSON:
- Each trial's `(dataset, query, run, answer)` appended to `runs/dab/<run_id>/trials.jsonl` (streaming, crash-resistant)
- Final `submission.json` materialized in DAB's required format
- `EvalReport` aggregates: per-query pass-rate, per-dataset average (stratified), overall Pass@1 (Chen et al. estimator)

Concurrency:
- `n_concurrent_trials` (default 3) — per-trial isolation via separate scratch dirs + DuckDB sessions

Resumability:
- On startup, read existing `trials.jsonl` and skip already-recorded `(dataset, query, run)` tuples
- Resume is a first-class flow, not a recovery edge case

### DAB scorer (`src/labrat/eval/benchmarks/dab/scorer.py`)

Thin wrapper that imports each `validate.py` as a module and calls `validate(llm_output: str) -> (bool, str)`. Catches import/runtime errors, treats as `(False, "validator_error: <msg>")`.

### Database lifecycle

`scripts/dab_setup.py` — one-time per workstation:
- PostgreSQL: `psql -c "CREATE DATABASE ..."` + `psql -f <sql_file>` for each PG dataset
- MongoDB: `mongorestore --db <name> <dump_folder>` for each Mongo dataset
- DuckDB/SQLite: file-based, no load step
- Idempotent: skips already-loaded DBs (checks `pg_database` / `db.adminCommand('listDatabases')`)

### Runner output structure

```
runs/dab/<run_id>/
├── config.json          # suite config, hints flag, model, seed
├── trials.jsonl         # streaming per-trial records
├── submission.json      # final DAB-format submission
├── report.md            # EvalReport markdown
└── failures/<dataset>/<query>/run<n>.log  # detailed logs for failed trials
```

Mirrors the shape of `~/repos/ade-bench/experiments/<run_id>/` for analyst-tool reuse.

## Testing & Validation Strategy

Three layers with different cadences.

### Layer 1: Unit tests (every commit)

| File | Coverage |
|---|---|
| `tests/unit/test_mongodb_engine.py` | Connect, introspect (nested-doc inference), execute pipeline, disconnect |
| `tests/unit/test_attach_database_tool.py` | DuckDB ATTACH of in-memory SQLite + PG (PG case skips if no local server) |
| `tests/unit/test_execute_python_tool.py` | Success, timeout, restricted env (no `ANTHROPIC_API_KEY` leaks), rlimit caps, scratch-dir persistence |
| `tests/unit/test_load_mongo_collection_tool.py` | Flatten nested docs, write to DuckDB, missing fields |
| `tests/unit/test_dab_suite.py` | Enumeration over fixture, hints flag, dataset filtering |
| `tests/unit/test_dab_runner.py` | Pass@5 accounting, resumability, submission JSON shape, score aggregator |

**Fixtures:** mini DAB-shaped fixtures under `tests/fixtures/dab/` — 1 synthetic dataset, 2 queries (one designed to pass, one to fail), covering DuckDB + SQLite. MongoDB unit tests use `mongomock`. PG unit tests use `pytest.importorskip` for clean skip when no local server.

### Layer 2: ADE-bench regression smoke (phase boundaries)

**File:** `src/labrat/eval/smoke.py` — `SubsetSuite` over `AdeBenchSuite` with a fixed 9-task ID list. Composition, not a hardcoded suite class. See unified-suite spec's "Smoke Regression" section.

Composition (selected in Phase 0, then immutable):
- **3 easy** — one per family among `analytics_engineering`, `asana`, `f1` (different hint-injection paths)
- **3 medium** — diverse table-shape complexity, exercising `run_sql` / `list_tables` / `describe_table`
- **3 hard** — at least one currently-passing, at least one currently-failing

**Baseline capture (Phase 0):** run the smoke set 3 times on current master, record per-task pass counts (n_attempts=3 × 3 runs = 9 attempts per task). Stored at `tests/baselines/ade_smoke_baseline.json`.

**Regression threshold:**
- **Hard fail** (halt phase): any task previously at ≥7/9 drops below 4/9
- **Soft signal** (investigate, proceed): aggregate resolved-task count drops by 1
- **Pass:** all task pass-rates within historical envelope

**Smoke run cost:** ~30–60 min for 27 trials (9 tasks × 3 attempts, 3 concurrent). Cheap enough to gate every phase boundary.

**Escalation:** on hard fail or two consecutive soft signals, run the full ADE-bench (`uv run scripts/eval_ade_bench.py`) to localize.

### Layer 3: DAB integration sanity check (after Phase 1b)

3-query subset (one DuckDB-only, one PG, one Mongo) end-to-end. Asserts `submission.json` shape and validators fire. "Does the pipeline work" check, separate from "does Claude answer well".

### What we explicitly do NOT do

- Run full DAB (270 trials) in CI
- Run full ADE-bench (180 trials) on every commit
- Run any LLM-gated test in CI without `LABRAT_RUN_LLM_TESTS=1`

## Phased Build Plan

Each phase has explicit entry/exit gates. Regression check at every boundary.

### Phase 0 — Pre-flight (1–2 days)

**Entry:** spec approved, branch created.

1. **`ClaudeCodeProvider` compatibility spike** — wire a 2-tool toy registry, confirm tool calls round-trip. If this fails, **halt and pivot** the entire design to a Bash-tool-prompting approach.
2. Install PostgreSQL 17 + MongoDB 8 via brew. Start as services.
3. (Optional, deferred) Build `python-data:3.12` Docker image — only if Tier 2 sandbox needed.
4. Write `scripts/dab_setup.py` and load all 12 DAB datasets. Verify each DB is queryable.
5. Select the 9 ADE smoke tasks (review `docs/ade_bench_failure_analysis.md` for representatives across difficulty + family hints + currently-passing/failing mix).
6. Baseline the smoke set: 3 runs on current master, write `tests/baselines/ade_smoke_baseline.json`.
7. Sanity: `uv run pytest && uv run scripts/eval_ade_bench.py --tasks <one_easy>` still passes.

**Exit:** spike works (or pivot decision made), all DBs loaded, smoke baseline recorded.

### Phase 1a — Multi-DB foundation + unified-suite scaffolding (1 week)

**Entry:** Phase 0 exit met.

1. **Unified-suite scaffolding** (per [`2026-05-28-unified-benchmark-suite-design.md`](2026-05-28-unified-benchmark-suite-design.md)):
   - Create `src/labrat/eval/types.py` with `BenchmarkTask`, `TrialResult`, `AggregateScore`, `BenchmarkReport`, `BenchmarkSuite` protocol.
   - Port `eval/suites/ade_bench.py` + `eval/runners/ade_bench_runner.py` to `eval/benchmarks/ade_bench/`. Pass port-acceptance test (run one easy task on legacy and new shapes; results match). Delete legacy paths.
   - Create `eval/smoke.py` with `SubsetSuite` + `ade_smoke_suite()`. Populate `ADE_SMOKE_TASK_IDS` from Phase 0 selection.
   - Recapture `tests/baselines/ade_smoke_baseline.json` on the new shape (replaces any baseline captured on legacy code).
   - Create `eval/benchmarks/spider2_dbt/README.md` referencing the unified-suite spec.
2. `ToolContext` change with backwards-compat shims
3. `DabSuite` enumeration (no runner yet) — implements `BenchmarkSuite.tasks()`
4. `DabSuite.run_trial` v0 — single-trial-per-query, no pass@5, no resumability. Surrounding interim runner is inline in `scripts/eval_dab.py`, must contain no DAB-specific logic.
5. End-to-end run on the 5 DuckDB+SQLite-only datasets (~17 queries × 1 trial)
6. Record per-query results, eyeball answer quality

**Exit gate:**
- All 17 queries complete without runner crashes
- `submission.json` shape validates against DAB schema
- AdeBenchSuite port-acceptance test passes (legacy vs new produce matching results)
- **ADE smoke regression check passes** (running through the new shape)

### Phase 1b — All datasets, full submission (2 weeks)

**Entry:** Phase 1a exit met.

1. `MongoDBConnection` adapter
2. Tools: `list_databases`, `attach_database`, `load_mongo_collection`, `execute_python` (Tier 1)
3. Extend `run_sql`, `list_tables`, `describe_table` with optional `database` param
4. `DabRunner` v1 — pass@5, resumability, parallel trials, scratch-dir isolation
5. Full 270-trial run without hints — record baseline score

**Exit gate:**
- All 270 trials complete (or fail with logged reasons)
- Submission JSON validates against DAB schema
- **ADE smoke regression check passes**
- Baseline score recorded (likely 35–50% without hints/tuning)

### Phase 1c — Hints, prompting, competitive score (1 week)

**Entry:** Phase 1b exit met.

1. Flip `hints=True` (read `db_description_withhint.txt`)
2. Failure analysis on baseline run — categorize by dataset + root cause (same playbook as `docs/ade_bench_failure_analysis.md`)
3. Iterate on:
   - DAB-specific system prompt (cross-DB query patterns, attach-then-join idiom, when to escape to Python)
   - Family-specific hints for poorly-performing datasets (crmarenapro has 13 queries → big leverage)
4. 2–3 full re-runs with prompt iteration between each
5. Submit final results — PR to DataAgentBench repo

**Exit gate:**
- Pass@1 ≥ 55% (top-5 territory)
- **ADE smoke regression check passes**
- `docs/dab_failure_analysis.md` written
- PR opened on DataAgentBench repo
- Branch merged to master

**Stretch goal:** Pass@1 ≥ 61% (beat Altimate).

### Phase 2 — Generalize & extract (deferred, post-Phase 1c)

Not specified in detail until Phase 1c lessons land. Likely scope:
- Refactor toward a generic `BenchmarkSuite` protocol now that we've seen DAB + ADE-bench patterns
- Docker sandbox for `execute_python` if any incidents in Phase 1
- Extract LabRat's agent core (`agent/`, `db/`, `tools/`) as a separable package, TUI-independent
- Begin growing the tool registry toward the Altimate-100-tool vision (analysis, validation, semantic-layer, lineage tools)

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `ClaudeCodeProvider` doesn't support custom tool registries cleanly | Medium | Phase 0 spike validates before committing. Pivot to Bash-tool-prompting if needed. |
| Subprocess sandbox: Claude writes destructive code | Low | Restricted env, rlimits, regex pre-flight check. Escalate to Docker if any incident. |
| ADE-bench regresses from multi-DB ToolContext change | Medium | Backwards-compat shims preserve `ctx.connection` / `ctx.catalog`. Smoke check at every phase boundary. |
| 270-trial run latency unworkable on `ClaudeCodeProvider` | Medium | Resumability built-in. Parallel trials (default 3 concurrent). Estimate first full run honestly before committing to multiple re-runs. |
| Score plateaus below Altimate's 60.4% | Medium | Iterate on prompts and hints (Phase 1c playbook). If we plateau at 50%, document learnings, ship submission, move to Phase 2. |
| Patents + deps_dev_v1 datasets score 0% for all agents | High (already observed) | Acknowledge as unreachable. They cap our score at ~95% in theory; we're not aiming there anyway. |
| Mongo schema inference is unreliable | Medium | Prompts will warn the agent that Mongo column lists are samples. The agent can use `execute_python` to sample more aggressively if needed. |
| Branch goes stale vs master during 4-week build | Medium | Rebase from master at each phase boundary. Phase exits are natural rebase points. |

## Decisions Made (and rationale)

- **Approach B over A and C.** B builds reusable LabRat capability without the over-engineering risk of C. Decision rationale: balance score-velocity with infrastructure investment.
- **`ClaudeCodeProvider`, not API.** Hard requirement (Max plan, no API budget). Subject to Phase 0 spike confirmation.
- **DuckDB as federation engine.** LabRat's secret weapon — covers 10 of 12 datasets without leaving SQL. Plays to existing strength.
- **Subprocess `execute_python` (not Docker) for Phase 1.** Faster iteration, acceptable risk on dev workstation. Docker available as Phase 2 upgrade.
- **9-task ADE smoke set, not full bench.** Cheap enough to run at every phase boundary (~30–60 min vs ~3 hours).
- **Branch isolation; abandonable.** No master changes until Phase 1c exits. Rollback by deleting the branch.
- **Resumability is first-class.** At 270-trial scale, mid-run crashes are inevitable. Restart-from-scratch is unacceptable.
- **`load_mongo_collection` materializes to DuckDB**, rather than teaching `execute_python` to handle Mongo directly. Gives the agent a clean SQL-only path for 11/12 datasets.

## Open Questions

- **Specific ADE smoke task IDs.** Picked in Phase 0 after reviewing failure analysis. Codified before Phase 1a starts.
- **Whether to skip patents + deps_dev_v1 in Phase 1b's first run.** They cost trial budget for ~0 score. Currently lean toward running them (completeness for submission), but reconsider if total wall time is prohibitive.
- **Tool-pack filter timing.** Not in Phase 1 scope, but the trigger (registry size ~25) might hit faster than expected if we add more LabRat tools in parallel. Re-evaluate at start of Phase 2.
