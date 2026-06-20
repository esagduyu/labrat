# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Test
uv run pytest                                    # full suite (~567 tests)
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
uv run python scripts/eval_dab.py --driver labrat-agent --agent-verify   # opt-in LLM-as-judge verifier (default off; extra LLM call/answer)
uv run python scripts/eval_dab.py --driver labrat-agent --agent-provider claude-code --agent-timeout 300   # raise claude-code per-call timeout

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

**Verifier loop (opt-in):** `AgentLoop` accepts an optional `verifier: Verifier | None` + `max_verify_rounds` (default 2), and `run()` takes an `on_status` callback. At the would-be-final turn (the model emits no tool calls), the verifier judges whether the answer addresses the question; if it returns insufficient, the feedback is injected as a new user turn and the loop continues — bounded by the round cap AND the remaining turn budget. It is **fail-open**: an unparseable verdict counts as sufficient, so the verifier can never trap the loop. `loop.verify_rounds_used` reports rounds spent. Verifier status goes to `on_status`, deliberately separate from `on_text` so it never corrupts `final_text`. The verifier types live in `verifier.py` (`Verdict`, the `Verifier` protocol, `LLMVerifier`, `parse_verdict`, `provider_llm_fn`), mirroring the `validations.ValidationChecker` LLM-judge pattern — one constrained call parsed to `"sufficient"` / `"insufficient: <feedback>"`. `provider_llm_fn` adapts the loop's own `ModelProvider` into the judge (same model + billing). `run_agent_task` exposes this via `verify=False` (default off — it costs an extra LLM call per would-be-final answer) + `max_verify_rounds`; when `verify=True` it builds an `LLMVerifier` backed by the same provider.

`run_agent_task` in `runner.py` is the in-process wrapper that turns a one-shot prompt into an `AgentTaskResult(final_text, tool_calls, latency_seconds)`. Used by the DAB `labrat-agent` driver, `scripts/run_task.py`, and (eventually) the TUI chat path. The standard data tools come from `data_tools.py::build_data_tools_registry()` — `profile_dataset`, `list_tables`, `describe_table`, `search_columns`, `link_schema`, `sample_rows`, `column_stats`, `run_sql`, `explain_sql`, `verify_join`, `attach_database`, `load_file`, `load_mongo_collection`. TUI-callback tools (`draft_sql`, `create_chart`) and profile-keyed tools (`run_validations`, `recall_memories`, `search_query_history`) are registered separately by the TUI.

Every tool subclasses `Tool[InputT]` (`tools/base.py`). It declares `name`, `description`, and `input_model` (a Pydantic model). The registry validates inputs, calls `execute(ctx, input)`, and wraps results in `DispatchResult`. `ToolContext` supports **multi-DB construction** — `connections: dict[str, Connection]` + `catalogs: dict[str, object]` + `primary: str`. Single-DB construction (`connection=`, `catalog=`) is preserved as a back-compat shim that wraps the value under the `primary` name. Tools that accept an optional `database: str | None` field (most data-access tools) route via `ctx.connections[args.database or ctx.primary]`.

Current tools (18): `build_data_tools_registry()` registers the 13 above; the TUI adds 5 — `draft_sql`/`create_chart` (callbacks) and `run_validations`/`recall_memories`/`search_query_history` (profile-keyed). `link_schema` (NL→relevant-tables-only, lexical scoring over the catalog) and `verify_join` (probe a join's match-rate + fan-out before trusting it) are the grounding tools from FEATURE_ROADMAP #25 — they attack the multi-table / wide-schema failure modes; both are pure/deterministic (no LLM call).

`profile_dataset` (`tools/profile_dataset.py`) is a one-call dataset profiler: for every table it returns columns+types, row count, declared foreign keys, and a few sample rows. Size-budgeted via `max_tables` with explicit truncation flagging; it reads structure from the introspected catalog (`ctx.catalogs[db]`) and samples live rows from the connection. It has a `COUNT(*)` fallback because DuckDB introspection leaves `Table.row_count` `None`. Requires the catalog to be populated.

`load_file` (`tools/load_file.py`) loads a CSV/TSV/JSON/Parquet file into the DuckDB session as a TEMP table — works even against a read-only primary (like `load_mongo_collection`). DuckDB-only: it guards `isinstance(conn, DuckDBConnection)` like `attach_database`. Backed by `DuckDBConnection.load_file()` (`db/duckdb_engine.py`), which runs the `CREATE OR REPLACE TEMP TABLE ... AS SELECT * FROM read_csv_auto/read_json_auto/read_parquet(...)` DDL directly (because `DuckDBConnection.execute()` is SELECT-only) and returns the loaded row count.

The system prompt (`agent/prompts/system_base.md`) is **prescriptive**: profile first (`profile_dataset`) → state a numbered plan → execute step by step, reading each result → verify the answer addresses the question before finishing.

### Database layer (`src/labrat/db/`)

`Connection` ABC defines `connect`, `disconnect`, `introspect_catalog`, `execute`, and `explain`. Seven concrete adapters: `DuckDBConnection`, `PostgresConnection`, `SnowflakeConnection`, `BigQueryConnection`, `RedshiftConnection`, `TrinoConnection`, `MySQLConnection`. All return Polars DataFrames. `catalog.py` defines `Catalog / Schema / Table / Column / ForeignKey` — the in-memory schema representation passed in `ToolContext`.

### LLM providers (`src/labrat/agent/providers/`)

`ModelProvider` ABC. `AnthropicProvider` uses the Anthropic SDK. `ClaudeCodeProvider` shells out to the `claude` CLI (Mac OAuth, Max plan; **fragile under tool round-trips** — see DAB integration below). `OpenAICompatibleProvider` covers Azure, LiteLLM, Ollama, etc. `providers/__init__.py::build_provider(name, model)` is the shared string-to-provider factory used by the DAB harness and `scripts/run_task.py`. `PROVIDER_NAMES = ("anthropic", "claude-code", "openai")`.

### MCP server (`src/labrat/mcp/`)

`labrat.mcp.server` mounts the data-tools registry over MCP stdio (`mcp.server.Server` low-level API). Reads `LABRAT_MCP_CONNECTIONS` (JSON: `{name: {db_type: "duckdb", db_path: "..."}}`) + optional `LABRAT_MCP_PRIMARY` + optional `LABRAT_MCP_LOG_DIR` (when set, `_log_tool_call` appends one `{"tool","input","output","ok","latency_ms"}` line per dispatch to `<dir>/mcp_tool_calls.jsonl` for audit-grade traces). Each LabRat tool is exposed via its `anthropic_schema()`; results are serialised via Pydantic `model_dump_json()` or `json.dumps` fallback. The DAB `claude-mcp` driver writes a per-trial mcp-config and shells `claude --print --mcp-config <file>`. The TUI product can mount the same server in Claude Code / Codex / Cursor for harness-agnostic data access. `labrat.mcp.toy` is the 2-tool spike server kept around for MCP compatibility checks.

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
- `env.py` — `build_dab_task_env(db_config_path) → DabTaskEnv(ctx, attachable)`. DuckDB clients become real `DuckDBConnection`s in `ctx.connections`; SQLite clients become `AttachSpec(alias, path, db_type)` entries the agent uses via the `attach_database` tool. Connections are not pre-`connect()`ed — the driver does that at trial start. Because connections aren't connected at build time, the catalogs it builds are empty (`Catalog(schemas=[])`); `introspect_env_catalogs(ctx)` repopulates them post-connect and is called by `_run_trial_labrat_agent` after it connects, so the catalog-backed tools (`list_tables` / `describe_table` / `column_stats` / `search_columns` / `profile_dataset`) actually work under the `labrat-agent` driver. The `claude-mcp` driver is unaffected (it introspects via the MCP server).
- `scorer.py` — imports each query's `validate.py`, adds DAB repo root to `sys.path` for `common_scaffold`
- `reporter.py` — writes `submission.json` in DAB leaderboard format

**Three drivers (selected via `--driver` on `scripts/eval_dab.py`):**

| Driver | Loop owner | Billing | Reliability | Use for |
|---|---|---|---|---|
| `raw-bash` (default) | claude CLI native Bash | Max plan | high | Reproducing Phase 1b 48.5% baseline |
| `labrat-agent` | `AgentLoop` + LabRat tools | depends on `--agent-provider` | high w/ anthropic; **fragile** w/ claude-code | Phase 4 measurement on metered API; cross-provider matrix |
| `claude-mcp` | claude CLI + LabRat MCP server | Max plan | high | **Recommended Phase 4 path** — LabRat tools, free per run |

The `labrat-agent` driver builds the `DabTaskEnv`, registers `data_tools` (`profile_dataset`, `list_tables`, `describe_table`, `search_columns`, `link_schema`, `sample_rows`, `column_stats`, `run_sql`, `explain_sql`, `verify_join`, `attach_database`, `load_file`, `load_mongo_collection`), and routes through `run_agent_task` in-process. Its system prompt (`_build_labrat_agent_system_prompt`) surfaces `profile_dataset`/`link_schema`/`verify_join`/`load_file` and a profile→link→plan→verify-joins→verify-answer discipline. Opt-in `--agent-verify` enables the LLM-as-judge verifier loop on this driver only (default off); `--agent-timeout` overrides the per-trial subprocess timeout (claude-mcp wall-clock and the claude-code per-call timeout; claude-mcp default 1200s). The `claude-mcp` driver writes a per-trial `mcp-config.json` to the scratch dir and shells `claude --print --strict-mcp-config --mcp-config <file> --allowedTools mcp__labrat --disallowedTools Bash,WebFetch,WebSearch,Task,Read,Write,Edit,NotebookEdit,Glob,Grep --model <agent_model> --permission-mode bypassPermissions`. **Sandbox gate (commit `6b4d3bf`, 2026-06-03):** the `--allowedTools`/`--disallowedTools` pair confines the agent to the LabRat MCP server (no native Bash/web/subagents), the subprocess runs with `cwd=<absolute trial scratch dir>` so the DataAgentBench checkout is not on the agent's path, and `mcp-config.json` / `LABRAT_MCP_LOG_DIR` paths are made absolute (`scratch_dir.resolve()`) so the cwd change can't re-resolve them. `ANTHROPIC_API_KEY` / `CLAUDECODE` / `CLAUDE_CODE_*` are stripped from the subprocess env so the CLI falls through to Max-plan OAuth. See the sandbox-gate paragraph below for the contamination backstop.

**Cross-DB ATTACH:** under `raw-bash` the ATTACH idiom is injected into the prompt (text). Under `labrat-agent` and `claude-mcp` the model uses the `attach_database` tool against the primary DuckDB; SQLite paths are surfaced from `DabTaskEnv.attachable` so the model knows what's available.

**`ClaudeCodeProvider` fragility:** the text protocol convention (`{"call":"<tool>","input":{...}}`) works when the model emits it cleanly. On harder queries the model falls back to native `{"type":"tool_use",...}` blocks and the CLI returns `error_max_turns` with `stop_reason: tool_use`. Verified 2026-05-30: stockmarket:1 PASSes via `--agent-provider claude-code`, music_brainz_20k:1 FAILs. Don't use `claude-code` as the Phase 4 provider — use `claude-mcp` instead.

**Caps:** `--max-turns` and `--max-tool-calls` are configurable, both default `None` (unbounded). Under `labrat-agent` they hard-cap `AgentLoop`. Under `claude-mcp`, `max-turns` maps to `claude --max-turns` (default 200 when `None`); `max-tool-calls` is **advisory only** (surfaced in the prompt — the claude CLI has no native tool-call cap). Under `raw-bash`, `max-turns` honours explicit override but defaults to 15 (Phase 1b reproducibility); `max-tool-calls` is ignored (no LabRat registry in the loop).

**Resume safety:** `config.json` records `driver`, `agent_model`, `agent_provider`, `agent_max_turns`, `agent_max_tool_calls`, `agent_verify`, `agent_timeout`. Resuming via `--output-dir` restores all seven; any CLI override that conflicts with the existing config is rejected to prevent mixed-driver runs from corrupting the aggregate score.

**Per-trial isolation:** `DabSuite.run_trial` wraps the driver dispatch in try/except — a provider/agent exception (e.g. claude-code's per-call `TimeoutError`) is recorded as `reason="infra:timeout"` / `"infra:agent_error"` and skipped by `aggregate()` (and auto-retried on resume), so a single failure can't crash a long run.

**Scoring:** stratified — mean of per-dataset pass rates. Each dataset contributes equally regardless of query count. Per-query rate = `passes / n_trials` (not binary pass@5), so a query with 1/5 passes scores 0.2, not 1.0.

**Status — on the leaderboard at 51.38% (cite this number).** LabRat is on the [DAB leaderboard](https://ucbepic.github.io/DataAgentBench/) at a stratified Pass@1 of **51.38%** (rank #10 of 18, full 54-query benchmark, claude-mcp, pass@5, claude-sonnet-4-6), accepted 2026-06-18 after the maintainers independently re-validated all 270 answers. The full four-act history (48.5% raw-Claude floor → 54.0% tool-layer delta → 58.0% submission that was contaminated and corrected → 51.38% accepted) lives in **`docs/dab-progress-report.md`** — read it for phase-by-phase numbers, the contamination audit, and the roadmap. **Three numbers, three meanings: never cite 58.0% (contaminated); 50.5% was our interim withdraw-only recompute; 51.38% is the official maintainer-re-validated figure.** Strongest signal: crmarenapro 82% (6 DBs); patents 0% is the Sonnet ceiling. Memory: `project_dab_phase5_submission`, `project_dab_contamination`, `reference_dab_pr53_altimate_precedent`.

**Sandbox gate — architectural invariant of the `claude-mcp` driver (must not regress).** The Phase 5 contamination was an unsandboxed agent reading answer-key files / loading HF labels via native Bash. The fix is now load-bearing and permanent: (1) **tool allowlist** — `--allowedTools mcp__labrat` + `--disallowedTools` for every native tool, so the LabRat MCP server is the sole interface (`--permission-mode bypassPermissions` alone is NOT a sandbox — it keeps the full Claude Code toolset live); (2) **filesystem isolation** — `cwd=<absolute scratch dir>`, DAB checkout off-path; (3) **contamination backstop** — `_detect_contamination()` scans each trial's output for answer-key/external-dataset markers and withdraws any hit as `reason="contaminated:<tag>"`, excluded by `aggregate()` alongside `infra:` (a regression can never silently inflate the score again); (4) **audit traces** — `LABRAT_MCP_LOG_DIR` logs every dispatch. Network egress isolation (container / `unshare -n`) stays an environment step. **Caveat that survives the sandbox:** agnews leaks via model *parametric memory* (Sonnet recalls the public AG News id→label mapping and applies it via SQL) — `_detect_contamination` only catches trials that *name* the dataset, so agnews is intrinsically unreliable for a pretraining-exposed model; same leak hit DAB PR #53 (Altimate, GPT-5.5), so it's model-agnostic.

**Operational notes:** (a) a 270-trial Max-plan run spans multiple session windows — `reason="infra:session_limit"` detection + aggregate-skip + resume auto-retry (commits `404af15`/`9c46c1c`) make it practical; the harness still fast-fails the queue once a limit hits (sub-2s trials with the error as final_text), so resume after reset (open enhancement: sleep-until-reset for unattended completion). (b) `claude-mcp` per-trial traces: `--output-format json` gives one bundled result (passed/tool_calls/latency/final_text), but the MCP server logs every dispatch to `<LABRAT_MCP_LOG_DIR>/mcp_tool_calls.jsonl` for audit-grade per-call traces (open enhancement: `--output-format stream-json` for the LLM message stream).

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

**DAB `eval_dab.py` default n_trials is 5** — pass `--n-trials 1` for a quick single-trial run. Resuming a crashed run: `--output-dir runs/dab/dab-<id>` reads the existing `trials.jsonl` and skips already-completed `(task_id, trial_num)` pairs.

**DAB answer-from-context failure mode (music_brainz_20k)** — consistently returns in 7-10s with wrong answers; sub-10s times mean the model is answering from prompt context without querying. A force-query prompt rule is the candidate fix (untried). Distinct from a slow-but-wrong failure like deps_dev_v1:1 (60-170s real queries, still wrong) — don't conflate response-time signatures. (Per-dataset history: `docs/dab-progress-report.md`.)

**DAB cross-DB ATTACH idiom** — datasets spanning DuckDB/SQLite/Postgres require `ATTACH` to join them. Under `raw-bash` the preamble auto-injects the idiom; under `labrat-agent`/`claude-mcp` the model calls the `attach_database` tool against the primary DuckDB (Postgres via DuckDB's `postgres` extension; Mongo via `load_mongo_collection` → TEMP table). SQLite/Postgres paths come from `DabTaskEnv.attachable`, Mongo from `MongoSpec`. If adding a dataset, check `db_config.yaml` `db_clients` keys and ensure all db types are handled in `env.py` (DuckDB → `ctx.connections`; SQLite/Postgres → `attachable`; Mongo → `MongoSpec`).

**`ClaudeCodeProvider` text protocol is fragile, not blocked** — simple single-step queries pass; harder queries push the model to native `{"type":"tool_use",...}` and the CLI returns `error_max_turns`. Don't use `--agent-provider=claude-code`; use `--driver=claude-mcp` (proper MCP path, same Max-plan billing, no model-format dependence).

**DAB driver resume safety** — `eval_dab.py` records `driver`, `agent_model`, `agent_provider`, `agent_max_turns`, `agent_max_tool_calls`, `agent_verify`, `agent_timeout` in `config.json`. On `--output-dir <existing>`, all seven are restored; any explicit CLI override that disagrees with the recorded value is rejected so a resumed run can't silently swap drivers/caps mid-stream and corrupt the aggregate.

**LabRat MCP server connections are JSON env vars** — `LABRAT_MCP_CONNECTIONS` and optional `LABRAT_MCP_PRIMARY` are read at startup; optional `LABRAT_MCP_LOG_DIR` turns on per-dispatch tool-call logging. Only `db_type=duckdb` is supported in the connection spec today; SQLite/Postgres/MySQL are reached via the `attach_database` tool the agent calls inside the running session. If adding Postgres/Mongo MCP support, extend `_build_context_from_env` in `src/labrat/mcp/server.py`.

**DAB `claude-mcp` scratch paths must be absolute** — the sandboxed driver runs the `claude` subprocess with `cwd=scratch_dir` for filesystem isolation, so any relative `--mcp-config` / `LABRAT_MCP_LOG_DIR` path gets re-resolved by the CLI against the new cwd and doubles (`Invalid MCP configuration: ... not found`). `_run_trial_claude_mcp` calls `scratch_dir = scratch_dir.resolve()` up front to prevent this; the live agnews smoke caught it because the harness passes a repo-relative scratch dir while unit tests used an absolute `tmp_path`.

**MCP server `:memory:` primary must be writable** — `_build_context_from_env` forces `read_only=False` when `db_path == ":memory:"` (DuckDB cannot open `:memory:` read-only, and the federation primary is the agent's writable workspace — `attach_database` / `load_file` / `load_mongo_collection` materialize into it). This bit federation datasets (agnews, yelp — no file-backed DuckDB primary, so `env.py` synthesizes a `:memory:` `__federation`): the MCP server crashed on startup, but it was masked in Phase 5 because the agent used Bash. The sandbox (Bash blocked) exposed it; now fixed + regression-tested. File-backed primaries still default `read_only=True` (DAB DBs are read-only sources).

**DAB `labrat-agent` driver needs `introspect_env_catalogs` post-connect** — `build_dab_task_env` builds `Catalog(schemas=[])` (connections aren't `connect()`-ed at build time), so without re-introspection the catalog-backed tools (`list_tables` / `describe_table` / `column_stats` / `search_columns` / `profile_dataset`) see nothing and are effectively dead. `_run_trial_labrat_agent` connects, then calls `introspect_env_catalogs(env.ctx)` to populate the catalogs before running the agent. The `claude-mcp` driver doesn't need this (it introspects via the MCP server).

**DAB "Pass@1" is a stratified mean, not ML-style pass@1** — the DAB leaderboard column labeled `Pass@1` is `DabSuite.aggregate().overall`: the mean of per-dataset means of per-query (passes / n_trials). It is NOT "did any one of 5 attempts pass?". Don't reconcile our `report.md` against the leaderboard expecting different metrics — they're the same.

**Probe Max-plan availability before resuming a benchmark run** — after an `infra:session_limit` hit, fire `env -u ANTHROPIC_API_KEY -u CLAUDECODE claude --print --model claude-sonnet-4-6 --max-turns 1 -p "ping"` first. Real response → safe to fire `eval_dab.py --output-dir <id>`. Session-limit text → wait. Without the probe, a premature resume blasts ~170 fast-fail infra trials into `trials.jsonl` in ~4 minutes.

**DAB leaderboard submission files go in `leaderboard_submissions/`** — not `submissions/`. The latter holds older PromptQL/react-style runs; new leaderboard PRs go in the former. Naming convention: `<agent>_<model>_n5.json` (e.g., `altimate-code_claude-sonnet-46_n5.json`). PR commits only the JSON. **Maintainer process (observed on PR #54):** they independently re-validate all answers, then **close the PR and merge the leaderboard entry on their end** (they keep third-party commits out of the project repo) — so a closed PR is not a rejection; check the live leaderboard.

**DAB suite enumerates 104 queries, not the official 54 — always `--datasets` filter for a leaderboard run.** The local DAB checkout has 17 dataset dirs; `DabSuite.tasks()` enumerates all of them = **104 queries / 520 trials**, including 5 unofficial extras (`civic_unstructured`, `cve`, `imdb`, `krama`, `usaspending`). The official benchmark is the **12 datasets / 54 queries / 270 trials**. A run without `--datasets agnews,bookreview,crmarenapro,deps_dev_v1,github_repos,googlelocal,music_brainz_20k,pancancer_atlas,patents,stockindex,stockmarket,yelp` wastes budget on unofficial datasets and pollutes the aggregate. `task_filter` is NOT resume-guarded, so the filter can be added on a `--output-dir` resume without conflict (unofficial trials already on disk are then just ignored by the official aggregate).

**Self-healing DAB re-run loop (`scripts/dab_rerun_tick.sh` + `dab_rerun_loop.sh`)** — the tick probes Max-plan (skips cleanly if the limit is active, so it never blasts fast-fail trials), then starts/resumes `eval_dab.py --output-dir runs/dab/dab-rerun-clean` (official-scoped, n=5, 1200s); idempotent with a concurrency guard. The loop runs it every **30 min** (not 6h — the cheap probe means you resume ~30 min after a limit reset instead of waiting out a fixed window). Run it detached: `nohup bash scripts/dab_rerun_loop.sh >> runs/dab/rerun_loop.log 2>&1 &`. **Must run locally** (Max-plan OAuth + mongod + local DAB checkout); a Claude Code routine on the **bridge env** also works but dies on reboot (`environment_not_found`), so the local loop is the durable path. mongod must be up (agnews/yelp need it; the tick warns if down).

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
