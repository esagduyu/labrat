# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Test
uv run pytest                                    # full suite (~504 tests)
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

# DAB driver selection (post-substrate, 2026-05-30):
uv run python scripts/eval_dab.py --driver raw-bash         # Phase 1b baseline (48.5% reproducible)
uv run python scripts/eval_dab.py --driver labrat-agent     # AgentLoop + LabRat tools (default provider: anthropic, metered API)
uv run python scripts/eval_dab.py --driver claude-mcp       # claude --print + LabRat MCP server (Max-plan, recommended Phase 4 path)
uv run python scripts/eval_dab.py --driver labrat-agent --agent-provider anthropic --agent-model claude-sonnet-4-6
uv run python scripts/eval_dab.py --driver labrat-agent --max-turns 10 --max-tool-calls 30   # bound the loop

# Standalone LabRat agent on any query (any provider):
uv run python scripts/run_task.py --prompt "..." \
    --connections '{"main":{"db_type":"duckdb","db_path":"/path.duckdb"}}' \
    --provider anthropic --model claude-sonnet-4-6

# Run the LabRat MCP server (mount inside any MCP-supporting host):
LABRAT_MCP_CONNECTIONS='{"main":{"db_type":"duckdb","db_path":"/path.duckdb"}}' \
    uv run python -m labrat.mcp.server
```

`asyncio_mode = "auto"` is set globally — no `@pytest.mark.asyncio` needed.
LLM-gated tests are skipped unless `ANTHROPIC_API_KEY` or `LABRAT_RUN_LLM_TESTS=1` is set.

## Architecture

### Agent loop (`src/labrat/agent/`)

`AgentLoop` in `loop.py` drives tool-use round-trips. It accepts a `ToolRegistry` and an LLM provider, sends messages, receives `TextBlock | ToolUseBlock` responses, dispatches tools, and feeds `ToolResultBlock`s back until the model stops calling tools. Optional `max_turns` and `max_tool_calls` cap the loop (both default `None` = unbounded). After `run()`, `loop.turns_used` and `loop.tool_calls_used` report what actually fired.

`run_agent_task` in `runner.py` is the in-process wrapper that turns a one-shot prompt into an `AgentTaskResult(final_text, tool_calls, latency_seconds)`. Used by the DAB `labrat-agent` driver, `scripts/run_task.py`, and (eventually) the TUI chat path. The standard data tools come from `data_tools.py::build_data_tools_registry()` — `list_tables`, `describe_table`, `search_columns`, `sample_rows`, `column_stats`, `run_sql`, `explain_sql`, `attach_database`. TUI-callback tools (`draft_sql`, `create_chart`) and profile-keyed tools (`run_validations`, `recall_memories`, `search_query_history`) are registered separately by the TUI.

Every tool subclasses `Tool[InputT]` (`tools/base.py`). It declares `name`, `description`, and `input_model` (a Pydantic model). The registry validates inputs, calls `execute(ctx, input)`, and wraps results in `DispatchResult`. `ToolContext` supports **multi-DB construction** — `connections: dict[str, Connection]` + `catalogs: dict[str, object]` + `primary: str`. Single-DB construction (`connection=`, `catalog=`) is preserved as a back-compat shim that wraps the value under the `primary` name. Tools that accept an optional `database: str | None` field (most data-access tools) route via `ctx.connections[args.database or ctx.primary]`.

Current tools (13): `list_tables`, `describe_table`, `sample_rows`, `search_columns`, `column_stats`, `draft_sql`, `run_sql`, `explain_sql`, `search_query_history`, `recall_memories`, `create_chart`, `run_validations`, `attach_database`.

### Database layer (`src/labrat/db/`)

`Connection` ABC defines `connect`, `disconnect`, `introspect_catalog`, `execute`, and `explain`. Seven concrete adapters: `DuckDBConnection`, `PostgresConnection`, `SnowflakeConnection`, `BigQueryConnection`, `RedshiftConnection`, `TrinoConnection`, `MySQLConnection`. All return Polars DataFrames. `catalog.py` defines `Catalog / Schema / Table / Column / ForeignKey` — the in-memory schema representation passed in `ToolContext`.

### LLM providers (`src/labrat/agent/providers/`)

`ModelProvider` ABC. `AnthropicProvider` uses the Anthropic SDK. `ClaudeCodeProvider` shells out to the `claude` CLI (Mac OAuth, Max plan; **fragile under tool round-trips** — see DAB integration below). `OpenAICompatibleProvider` covers Azure, LiteLLM, Ollama, etc. `providers/__init__.py::build_provider(name, model)` is the shared string-to-provider factory used by the DAB harness and `scripts/run_task.py`. `PROVIDER_NAMES = ("anthropic", "claude-code", "openai")`.

### MCP server (`src/labrat/mcp/`)

`labrat.mcp.server` mounts the data-tools registry over MCP stdio (`mcp.server.Server` low-level API). Reads `LABRAT_MCP_CONNECTIONS` (JSON: `{name: {db_type: "duckdb", db_path: "..."}}`) + optional `LABRAT_MCP_PRIMARY`. Each LabRat tool is exposed via its `anthropic_schema()`; results are serialised via Pydantic `model_dump_json()` or `json.dumps` fallback. The DAB `claude-mcp` driver writes a per-trial mcp-config and shells `claude --print --mcp-config <file>`. The TUI product can mount the same server in Claude Code / Codex / Cursor for harness-agnostic data access. `labrat.mcp.toy` is the 2-tool spike server kept around for MCP compatibility checks.

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

[DataAgentBench](https://ucbepic.github.io/DataAgentBench/) — 12 official datasets, 54 queries, multi-DB (DuckDB, SQLite, PostgreSQL, MongoDB). Local repo (`~/repos/DataAgentBench`) has 17 directories — 5 are unofficial extras (civic_unstructured, cve, imdb, krama, usaspending) not in the official benchmark.

**Files:**
- `suite.py` — `DabSuite` implements `BenchmarkSuite`. Three drivers (see below). `Driver = Literal["raw-bash", "labrat-agent", "claude-mcp"]`
- `env.py` — `build_dab_task_env(db_config_path) → DabTaskEnv(ctx, attachable)`. DuckDB clients become real `DuckDBConnection`s in `ctx.connections`; SQLite clients become `AttachSpec(alias, path, db_type)` entries the agent uses via the `attach_database` tool. Connections are not pre-`connect()`ed — the driver does that at trial start.
- `scorer.py` — imports each query's `validate.py`, adds DAB repo root to `sys.path` for `common_scaffold`
- `reporter.py` — writes `submission.json` in DAB leaderboard format

**Three drivers (selected via `--driver` on `scripts/eval_dab.py`):**

| Driver | Loop owner | Billing | Reliability | Use for |
|---|---|---|---|---|
| `raw-bash` (default) | claude CLI native Bash | Max plan | high | Reproducing Phase 1b 48.5% baseline |
| `labrat-agent` | `AgentLoop` + LabRat tools | depends on `--agent-provider` | high w/ anthropic; **fragile** w/ claude-code | Phase 4 measurement on metered API; cross-provider matrix |
| `claude-mcp` | claude CLI + LabRat MCP server | Max plan | high | **Recommended Phase 4 path** — LabRat tools, free per run |

The `labrat-agent` driver builds the `DabTaskEnv`, registers `data_tools` (`list_tables`, `describe_table`, `search_columns`, `sample_rows`, `column_stats`, `run_sql`, `explain_sql`, `attach_database`), and routes through `run_agent_task` in-process. The `claude-mcp` driver writes a per-trial `mcp-config.json` to the scratch dir and shells `claude --print --strict-mcp-config --mcp-config <file> --model <agent_model> --permission-mode bypassPermissions`. `ANTHROPIC_API_KEY` / `CLAUDECODE` / `CLAUDE_CODE_*` are stripped from the subprocess env so the CLI falls through to Max-plan OAuth.

**Cross-DB ATTACH:** under `raw-bash` the ATTACH idiom is injected into the prompt (text). Under `labrat-agent` and `claude-mcp` the model uses the `attach_database` tool against the primary DuckDB; SQLite paths are surfaced from `DabTaskEnv.attachable` so the model knows what's available.

**`ClaudeCodeProvider` fragility:** the text protocol convention (`{"call":"<tool>","input":{...}}`) works when the model emits it cleanly. On harder queries the model falls back to native `{"type":"tool_use",...}` blocks and the CLI returns `error_max_turns` with `stop_reason: tool_use`. Verified 2026-05-30: stockmarket:1 PASSes via `--agent-provider claude-code`, music_brainz_20k:1 FAILs. Don't use `claude-code` as the Phase 4 provider — use `claude-mcp` instead.

**Caps:** `--max-turns` and `--max-tool-calls` are configurable, both default `None` (unbounded). Under `labrat-agent` they hard-cap `AgentLoop`. Under `claude-mcp`, `max-turns` maps to `claude --max-turns` (default 200 when `None`); `max-tool-calls` is **advisory only** (surfaced in the prompt — the claude CLI has no native tool-call cap). Under `raw-bash`, `max-turns` honours explicit override but defaults to 15 (Phase 1b reproducibility); `max-tool-calls` is ignored (no LabRat registry in the loop).

**Resume safety:** `config.json` records `driver`, `agent_model`, `agent_provider`, `agent_max_turns`, `agent_max_tool_calls`. Resuming via `--output-dir` restores all five; any CLI override that conflicts with the existing config is rejected to prevent mixed-driver runs from corrupting the aggregate score.

**Scoring:** stratified — mean of per-dataset pass rates. Each dataset contributes equally regardless of query count. Per-query rate = `passes / n_trials` (not binary pass@5), so a query with 1/5 passes scores 0.2, not 1.0.

**Phase 1a baseline (2026-05-29):** 43% overall on 5 DuckDB+SQLite datasets, n_trials=1.

**Phase 1b (2026-05-30):** 48.5% overall on 5 DuckDB+SQLite datasets (17 queries, pass@5, n_trials=5). Covers deps_dev_v1 (10%), github_repos (50%), music_brainz_20k (7%), stockindex (100%), stockmarket (76%). Raw Claude + prompt engineering floor — no LabRat tools. Reproducible via `--driver=raw-bash`.

**Phase 4 substrate (2026-05-30):** `labrat-agent` and `claude-mcp` drivers shipped on `master`. Smoke validated end-to-end on `stockmarket:1` (PASS, 34.8s, 7 tool calls, Max plan).

**Phase 4 measurement (2026-05-30): 54.0% overall on the 17-query DuckDB+SQLite subset, +5.5pp over the 48.5% Phase 1b baseline.** Per-dataset: deps_dev_v1 10%→40% (**+30pp**), stockmarket 76%→**88%** (**+12pp**); the +5.5pp delta is the measured value of LabRat's tool layer on that subset. Run dir: `runs/dab/dab-1780171421/`.

**Phase 5 — full 54-query DAB (DONE 2026-06-01): 58.0% overall, all 12 official datasets, claude-mcp driver, pass@5, claude-sonnet-4-6.** First directly leaderboard-comparable LabRat number: **above Spacedock (57.7%)**, behind Altimate Code (60.4%) and MinusX (63.1%). Substrate (Phases 2+3) validated at scale — agnews 95% (Mongo), bookreview 93% (Postgres), **crmarenapro 82% on 6 databases** (strongest single-dataset signal). 25 of 54 queries scored a perfect 5/5; patents 0% remains the Sonnet ceiling. Run required 4 `--output-dir` resume cycles to clear Max-plan session limits (auto-retry-on-resume handles this; harness lists `"infra:session_limit"` as the dominant non-progress reason). Run dir: `runs/dab/dab-1780210698/`. Full write-up: `docs/dab-progress-report.md`.

**Operational lesson from the Phase 5 run** — a 270-trial Max-plan run spans multiple session windows. Item 1 (`reason="infra:session_limit"` detection + aggregate skip + resume auto-retry, shipped in commit `404af15` / `9c46c1c`) is what makes this practical. The harness still fast-fails the rest of the queue once a session limit hits (each subsequent trial returns in ~1.5s with the error text as final_text). A future enhancement: detect session-limit in real time and sleep-until-reset so a single invocation can run to completion unattended.

**`claude-mcp` driver produces summary-only traces today** — `--output-format json` gives one bundled result; we get per-trial `passed` / `tool_calls` (count) / `latency` / `final_text` but not per-call SQL or LLM messages. For audit-grade traces (e.g., if DAB maintainers ask), switch `_run_trial_claude_mcp` to `--output-format stream-json` and persist the stream to `scratch/<task>__trial<n>/claude_stream.jsonl`, plus add server-side tool-call logging in `src/labrat/mcp/server.py` (one `{"tool", "input", "output", "ok", "latency_ms"}` line per dispatch, gated on a `LABRAT_MCP_LOG_DIR` env var). ~1 hour total; same compute as the original run.

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

**Plain `python3` doesn't see project deps** — use `uv run python3 -c '...'` even for one-off inline inspection. The system `python3` has no duckdb / polars / mcp / etc.

**`DuckDBConnection.execute()` is SELECT-only** — it goes through `pl.read_database`, which expects a result set. For DDL/DML on DuckDB (ATTACH, CREATE, INSERT, …) call `self._connection.execute(sql)` directly, as `DuckDBConnection.attach()` does in `src/labrat/db/duckdb_engine.py`.

**Long-running `uv run` piped to `tail`/`grep` block-buffers stdout** — output won't appear in the task-output file until the process exits. For live progress, drop the pipe or wrap with `stdbuf -oL`; or run via `run_in_background` and read the output file directly.

**One-off `claude --print` needs `env -u ANTHROPIC_API_KEY -u CLAUDECODE`** — if `ANTHROPIC_API_KEY` is in the shell, the CLI uses it (metered API) instead of Max-plan OAuth, and a credit-less account returns "Credit balance is too low". The `_invoke_agent` / `_run_trial_claude_mcp` subprocess paths strip this automatically; interactive spikes (MCP toy tests, prompt experiments) need to do it themselves.

**MCP server: use low-level `mcp.server.Server`, not FastMCP** — FastMCP's `@mcp.tool()` decorator infers schemas from Python function signatures, which doesn't fit a runtime `ToolRegistry` of arbitrary tools. Register handlers via `@server.list_tools()` + `@server.call_tool()` and feed schemas from `tool.anthropic_schema()` — see `src/labrat/mcp/server.py`.

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

**DAB cross-DB ATTACH idiom** — datasets with DuckDB+SQLite require `ATTACH` to join them. Under `--driver=raw-bash` the preamble in `_run_trial_raw_bash` auto-injects this when both DB types are present. Under `--driver=labrat-agent` and `--driver=claude-mcp` the model calls the `attach_database` tool against the primary DuckDB; SQLite paths come from `DabTaskEnv.attachable`. If adding new dataset support, check `db_config.yaml` `db_clients` keys and ensure all db types are handled in `env.py` (DuckDB → `ctx.connections`, SQLite → `attachable`; Postgres/Mongo still deferred).

**`ClaudeCodeProvider` text protocol is fragile, not blocked** — the 2026-05-29 Phase 0 spike concluded it didn't work; the 2026-05-30 smoke proved that conclusion was too strong. Simple single-step queries (e.g. stockmarket:1) pass because the model emits the right `{"call":...}` format; harder queries (e.g. music_brainz_20k:1) push the model to native `{"type":"tool_use",...}` and the CLI returns `error_max_turns`. Don't use `--agent-provider=claude-code` as the Phase 4 driver; use `--driver=claude-mcp` (proper MCP path, same Max-plan billing, no model-format dependence).

**DAB driver resume safety** — `eval_dab.py` records `driver`, `agent_model`, `agent_provider`, `agent_max_turns`, `agent_max_tool_calls` in `config.json`. On `--output-dir <existing>`, all five are restored; any explicit CLI override that disagrees with the recorded value is rejected so a resumed run can't silently swap drivers mid-stream and corrupt the aggregate.

**LabRat MCP server connections are JSON env vars** — `LABRAT_MCP_CONNECTIONS` and optional `LABRAT_MCP_PRIMARY` are read at startup. Only `db_type=duckdb` is supported in the connection spec today; SQLite/Postgres/MySQL are reached via the `attach_database` tool the agent calls inside the running session. If adding Postgres/Mongo MCP support, extend `_build_context_from_env` in `src/labrat/mcp/server.py`.

**DAB "Pass@1" is a stratified mean, not ML-style pass@1** — the DAB leaderboard column labeled `Pass@1` is `DabSuite.aggregate().overall`: the mean of per-dataset means of per-query (passes / n_trials). It is NOT "did any one of 5 attempts pass?". Don't reconcile our `report.md` against the leaderboard expecting different metrics — they're the same.

**Probe Max-plan availability before resuming a benchmark run** — after an `infra:session_limit` hit, fire `env -u ANTHROPIC_API_KEY -u CLAUDECODE claude --print --model claude-sonnet-4-6 --max-turns 1 -p "ping"` first. Real response → safe to fire `eval_dab.py --output-dir <id>`. Session-limit text → wait. Without the probe, a premature resume blasts ~170 fast-fail infra trials into `trials.jsonl` in ~4 minutes.

**DAB leaderboard submission files go in `leaderboard_submissions/`** — not `submissions/`. The latter holds older PromptQL/react-style runs; new leaderboard PRs go in the former. Naming convention: `<agent>_<model>_n5.json` (e.g., `altimate-code_claude-sonnet-46_n5.json`). PR commits only the JSON; maintainers update the README leaderboard table on merge.

**DAB dataset directory casing is mixed** — `DEPS_DEV_V1`, `GITHUB_REPOS`, `PANCANCER_ATLAS`, `PATENTS` are uppercase; the rest (`agnews`, `bookreview`, `crmarenapro`, `googlelocal`, `music_brainz_20k`, `stockindex`, `stockmarket`, `yelp`) are lowercase. Our `DabSuite` lowercases all task_ids; **submission JSON entries must be re-cased to match directory names** or DAB's official scorer won't find them. See the build script in `runs/dab/dab-1780210698/` for the lowercase→DAB-case mapping.

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
- `json.loads()` results are `Unknown` under pyright strict — when reading keys, use `# type: ignore[arg-type]` on the specific access (e.g. `str(data["result"])  # type: ignore[arg-type]`), matching the pattern in `src/labrat/eval/benchmarks/dab/suite.py::_invoke_agent`.
