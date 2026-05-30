# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Test
uv run pytest                                    # full suite (~490 tests)
uv run pytest tests/unit/test_agent_loop.py      # single file
uv run pytest -k "test_smoke"                    # by name
uv run pytest --co -q                            # list tests without running

# Lint / format / types — run all three before committing
uv run ruff format .          # auto-fixes formatting (run this first)
uv run ruff check .           # linting (must be clean)
uv run pyright                # type checking (must be clean)

# Run the app
uv run labrat

# Evals
uv run python scripts/eval_duckdb.py             # no API key needed
uv run scripts/eval_ade_bench.py --tasks helixops_saas001   # wrapper; needs ADE_BENCH_DIR + Docker
cd ~/repos/ade-bench && uv run ade run helixops_saas001 --db duckdb --project-type dbt --agent labrat_local --no-diffs

# DAB eval (needs DataAgentBench at ~/repos/DataAgentBench)
uv run python scripts/eval_dab.py --datasets deps_dev_v1,github_repos,music_brainz_20k,stockindex,stockmarket
uv run python scripts/eval_dab.py --n-trials 1  # quick single-trial run (default is 5)
uv run python scripts/eval_dab.py --output-dir runs/dab/dab-<id>  # resume a crashed run
```

`asyncio_mode = "auto"` is set globally — no `@pytest.mark.asyncio` needed.
LLM-gated tests are skipped unless `ANTHROPIC_API_KEY` or `LABRAT_RUN_LLM_TESTS=1` is set.

## Architecture

### Agent loop (`src/labrat/agent/`)

`AgentLoop` in `loop.py` drives tool-use round-trips. It accepts a `ToolRegistry` and an LLM provider, sends messages, receives `TextBlock | ToolUseBlock` responses, dispatches tools, and feeds `ToolResultBlock`s back until the model stops calling tools.

Every tool subclasses `Tool[InputT]` (`tools/base.py`). It declares `name`, `description`, and `input_model` (a Pydantic model). The registry validates inputs, calls `execute(ctx, input)`, and wraps results in `DispatchResult`. `ToolContext` carries the live `Connection` and `Catalog`.

Current tools: `list_tables`, `describe_table`, `sample_rows`, `search_columns`, `column_stats`, `draft_sql`, `run_sql`, `explain_sql`, `search_query_history`, `recall_memories`, `create_chart`, `run_validations`.

### Database layer (`src/labrat/db/`)

`Connection` ABC defines `connect`, `disconnect`, `introspect_catalog`, `execute`, and `explain`. Seven concrete adapters: `DuckDBConnection`, `PostgresConnection`, `SnowflakeConnection`, `BigQueryConnection`, `RedshiftConnection`, `TrinoConnection`, `MySQLConnection`. All return Polars DataFrames. `catalog.py` defines `Catalog / Schema / Table / Column / ForeignKey` — the in-memory schema representation passed in `ToolContext`.

### LLM providers (`src/labrat/agent/providers/`)

`BaseProvider` ABC. `AnthropicDirectProvider` uses the Anthropic SDK. `ClaudeCodeProvider` shells out to the `claude` CLI (used when running under Mac OAuth with no API credits). `OpenAICompatibleProvider` covers Azure, LiteLLM, Ollama, etc.

### TUI (`src/labrat/screens/`, `src/labrat/widgets/`)

Built on Textual. `app.py` is the root `App`. The main screen is a 3-pane layout: chat widget, SQL editor (`QueryEditor` extending `TextArea` with tree-sitter-sql highlighting), and schema browser. `styles.tcss` holds all Textual CSS. Pyright strict mode is **not** applied to `src/labrat/screens/` due to incomplete Textual stubs.

### Supporting subsystems

| Package | Purpose |
|---------|---------|
| `catalog/` | External catalog adapters: `DbtLoader` (reads manifest.json/schema.yml) and `McpCatalogAdapter` |
| `context_engine/` | Personal domain: table relevance scoring (frequency × recency), `ContextBundle`, `ContextAnalyzer` |
| `history/` | Always-on `QueryHistoryLog` (JSONL, PII-redacted). Singleton in `run_sql.py`, monkeypatched in tests |
| `memory/` | Self-healing memories: global/table/thread scopes, JSONL store, LLM-driven extraction |
| `validations/` | Per-rule LLM checks returning `"pass"` / `"warn: ..."` / `"block: ..."` |
| `eval/` | Two coexisting shapes. Legacy `EvalCase`/`EvalRunner`/`EvalReport` for internal SQL-correctness evals (`bird.py`, `latency.py`, `custom_scenarios.py`). New unified `BenchmarkSuite` protocol (`types.py`) for benchmark integrations — DAB and ADE-bench live under `benchmarks/<bench>/{suite,external_runner,scorer,reporter}.py`. `smoke.py` provides `SubsetSuite` + `ade_smoke_suite()`. `reporting.py` renders `BenchmarkReport` to markdown. See `docs/superpowers/specs/2026-05-28-unified-benchmark-suite-design.md` for the contract. |
| `audit/` | JSONL event sourcing for every interaction |
| `dspy_opt/` | DSPy-based prompt optimisation utilities. Pyright strict excluded here (no dspy stubs) |

### ADE-bench integration (`~/repos/ade-bench`)

`LabratLocalAgent` (in the ade-bench repo at `ade_bench/agents/installed_agents/labrat_local/`) extends `BaseAgent` directly. It runs `claude` locally via `subprocess` using Mac OAuth, and bridges into the Docker container via `docker exec` / `docker cp`. See `decisions.md` for the auth rationale.

`LabratLocalAgent.__init__` pins `model_name="claude-sonnet-4-6"` by default — passes `--model` to the `claude` subprocess explicitly so it doesn't silently fall through to whatever the user's CLI session is configured to (which can be Opus, burns Max plan budget ~5x faster). Override by passing `model_name=None` to use CLI default.

LabRat-side integration lives at `src/labrat/eval/benchmarks/ade_bench/`:
- `suite.py` — `AdeBenchSuite` implements `BenchmarkSuite`
- `external_runner.py` — shells `uv run ade run <task_id> ...`, parses `experiments/<exp_id>/results.json`
- `reporter.py` — maps trial dict → `TrialResult`

Run command:
```bash
# Single task via the new entrypoint:
uv run python scripts/eval_ade_bench.py --tasks <task_id> --n-attempts 3

# Full benchmark (still uses ade CLI directly):
cd ~/repos/ade-bench && uv run ade run <task_ids> --db duckdb --project-type dbt --agent labrat_local --no-diffs --n-concurrent-trials 3 --n-attempts 3

# Analyse a completed run's failures:
uv run scripts/analyze_ade_failures.py ~/repos/ade-bench/experiments/<run_id>/
```

Current score (2026-05-27, claude-sonnet-4-6): **80% overall** (48/60 tasks) — 100% easy, 80% medium, 60% hard.
Roadmap and remaining failures: `docs/ade_bench_failure_analysis.md`

### DAB integration (`src/labrat/eval/benchmarks/dab/`)

[DataAgentBench](https://ucbepic.github.io/DataAgentBench/) — 12 official datasets, 54 queries, multi-DB (DuckDB, SQLite, PostgreSQL, MongoDB). Note: local repo (`~/repos/DataAgentBench`) has 17 directories — 5 are unofficial extras (civic_unstructured, cve, imdb, krama, usaspending) not in the official benchmark.

LabRat-side integration lives at `src/labrat/eval/benchmarks/dab/`:
- `suite.py` — `DabSuite` implements `BenchmarkSuite`; `run_trial` builds a db-access preamble (per-DB connection examples + DuckDB ATTACH idiom for cross-DB joins) and calls `claude --print` directly with Bash tool
- `scorer.py` — imports each query's `validate.py`, adds DAB repo root to `sys.path` for `common_scaffold`
- `reporter.py` — writes `submission.json` in DAB leaderboard format

**DAB agent design:** uses `claude --print --disable-slash-commands --dangerously-skip-permissions --max-turns 15` with native Bash tool + Python+DuckDB. Does **not** use `AgentLoop`/`ClaudeCodeProvider` — the text-protocol conflicted with claude CLI's built-in tool handling.

**Cross-DB ATTACH:** when a dataset has both DuckDB and SQLite connections, `run_trial` injects the ATTACH idiom into the prompt:
```python
conn.execute("ATTACH '/path/to/other.db' AS alias (TYPE SQLITE)")
# then: SELECT ... FROM duck_table JOIN alias.sqlite_table ON ...
```
This enables cross-DB JOINs in a single DuckDB session (needed for `deps_dev_v1`, `music_brainz_20k`, `stockindex`, `stockmarket`).

**Scoring:** stratified — mean of per-dataset pass rates. Each dataset contributes equally regardless of query count. Per-query rate = `passes / n_trials` (not binary pass@5), so a query with 1/5 passes scores 0.2, not 1.0.

**Phase 1a baseline (2026-05-29):** 43% overall on 5 DuckDB+SQLite datasets, n_trials=1. Details: `docs/dab_phase1a_results.md`.

**Phase 1b (DONE 2026-05-30):** 48.5% overall on 5 DuckDB+SQLite datasets (17 queries, pass@5, n_trials=5). Covers deps_dev_v1 (10%), github_repos (50%), music_brainz_20k (7%), stockindex (100%), stockmarket (76%). This is the raw Claude + prompt engineering floor — no LabRat tools. ADE smoke check passed at Phase 1b exit gate. See `scripts/eval_dab.py`.

### Smoke regression (`scripts/run_smoke_regression.py`)

Fixed 9-task ADE subset (`src/labrat/eval/smoke.py::ADE_SMOKE_TASK_IDS`, frozen — see `docs/superpowers/notes/2026-05-29-ade-smoke-selection.md`). Run at every DAB phase boundary:

```bash
# One-time baseline capture (n_runs × n_attempts trials):
uv run python scripts/run_smoke_regression.py capture --n-runs 3 --n-attempts 3

# Check current state against the captured baseline (exit 1 on hard fail):
uv run python scripts/run_smoke_regression.py check --n-attempts 3
```

Baseline lives at `tests/baselines/ade_smoke_baseline.json`. Capture aborts with `InfraFailureError` if any trial returns `reason.startswith("infra:")` — prevents budget-exhaustion runs from silently corrupting the baseline with zero-time fake failures.

## Gotchas

**HTML tour files** — `docs/index.html` and `labrat_tour.html` are 2.2MB and exceed the Read tool's token limit. Use `grep`/`sed` for inspection; spawn a subagent for edits. The two files are always byte-identical — every edit must be applied to both.

**ADE-bench task.yaml** — difficulty field is `difficulty` (not `tier`); variant db field is `db_type` (not `db`). Enumerate tasks with:
```bash
cd ~/repos/ade-bench && uv run python -c "
import yaml; from pathlib import Path
for d in sorted(Path('tasks').iterdir()):
    f = d / 'task.yaml'
    if not f.exists(): continue
    data = yaml.safe_load(f.read_text())
    if data.get('difficulty')=='easy' and data.get('status')=='ready' and any(v.get('db_type')=='duckdb' and v.get('project_type')=='dbt' for v in data.get('variants',[])):
        print(d.name)
"
```

**ADE-bench known failures** — 12 tasks currently fail consistently. `helixops_saas010` fails 9/11 tests every run (was flaky; now a consistent failure). `helixops_saas015` fails 3/4 tests; `.low` variant passes. Full list and root causes: `docs/ade_bench_failure_analysis.md`.

**DAB Phase 1b covers 17/54 official queries** — only the 5 DuckDB+SQLite-only datasets. The remaining 37 official queries (agnews, bookreview, crmarenapro, googlelocal, pancancer_atlas, patents, yelp) require PostgreSQL and/or MongoDB preamble support not yet built in `run_trial`.

**DAB `eval_dab.py` default n_trials is 5 (Phase 1b)** — pass `--n-trials 1` for a quick single-trial run. Resuming a crashed run: `--output-dir runs/dab/dab-<id>` reads the existing `trials.jsonl` and skips already-completed `(task_id, trial_num)` pairs.

**music_brainz_20k fast-fail pattern** — consistently returns in 7-10s with wrong answers across all trials (Phase 1b: 7%). Sub-10s times indicate the model is answering from prompt context without actually querying the DB. Root cause unknown; the ATTACH preamble did not fix it.

**deps_dev_v1:1 persistent failure** — fails 0/5 across all Phase 1b trials (60-170s each) even with the ATTACH preamble. deps_dev_v1:2 improved marginally (1/5). Query 1 likely has a more complex cross-DB requirement than the ATTACH idiom covers.

**DAB cross-DB ATTACH idiom** — datasets with DuckDB+SQLite require `ATTACH` to join them. The preamble in `run_trial` auto-injects this when both DB types are present. If adding new dataset support, check `db_config.yaml` `db_clients` keys and ensure all db types are handled in the preamble builder.

**ADE experiment results file is `results.json`, not `results_metadata.jsonl`** — the file has a top-level `{"results": [...], ...}` shape. The port-acceptance spike caught a 0% pass rate when `external_runner.py` read the wrong filename. If you write new ADE-results parsing code, read `results.json` and iterate `data["results"]`.

**Model pin in ADE harness** — `LabratLocalAgent` defaults to `claude-sonnet-4-6`. Without this, the `claude` CLI subprocess uses session defaults (often Opus on `opusplan`), and a smoke baseline capture can burn through Max plan budget mid-run and leave bogus zero-time `unknown_agent_error` trials in the baseline. The `InfraFailureError` fail-fast in `run_smoke_regression.py` is the safety net.

**`_DOCKER_PREAMBLE` is a Python format string** — called with `.format(container_name=..., task_prompt=...)`. Any literal `{` must be `{{`. Dbt Jinja `{{ ref('x') }}` must be written `{{{{ ref('x') }}}}` in the source so it survives `.format()`. Same applies to `_FAMILY_HINTS` values. Verify with: `python3 -c "open('labrat_local_agent.py').read()" | grep -A2 'format('`.

**`_FAMILY_HINTS` injects by `task_name.startswith(prefix)`** — rules added to `analytics_engineering` never fire for `asana` tasks, even if the issue is identical. When adding a new rule, verify the correct family prefix. Next known gap: JOIN grain rules need copying to the `asana` family (T1.5a in `docs/ade_bench_failure_analysis.md`).

**decisions.md** is the living design log — add a dated entry for every significant architectural decision made in this repo.

**TESTING.md** is the manual TUI testing guide — step-by-step commands and verification conditions for milestones, using `tests/fixtures/sample_dbs/ecommerce.duckdb`. Consult it before manual UI testing rather than improvising.

## Before every commit

Run in this order — CI enforces all three:

```bash
uv run ruff format .   # must run first; fixes formatting in-place
uv run ruff check .    # must be clean
uv run pyright         # must be clean
uv run pytest -q       # must pass
```

`ruff format` must come before `ruff check` — format violations are check failures too.

## Key conventions

- Pyright strict applies to all of `src/labrat/` except `dspy_opt/` and `screens/`.
- `Connection` adapter names: use `duckdb_engine.py` (not `duckdb.py`) to avoid shadowing the library.
- Profile credentials live in the OS keyring via `keyring` — never logged or printed.
- `QueryEvent` never stores result rows (security decision).
- `asyncio_mode = "auto"` — no decorator needed on async tests.
- Tool `name`, `description`, and `input_model` must be `@property` methods, not class attributes.
