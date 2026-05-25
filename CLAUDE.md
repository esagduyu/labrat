# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Test
uv run pytest                                    # full suite (~500 tests)
uv run pytest tests/unit/test_agent_loop.py      # single file
uv run pytest -k "test_smoke"                    # by name
uv run pytest --co -q                            # list tests without running

# Lint / format / types
uv run ruff check .
uv run ruff format .
uv run pyright

# Run the app
uv run labrat

# Evals
uv run python scripts/eval_duckdb.py             # no API key needed
cd ~/repos/ade-bench && uv run ade run helixops_saas001 --db duckdb --project-type dbt --agent labrat_local --no-diffs
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
| `eval/` | `EvalSuite` → `EvalRunner` → `EvalReport` pipeline. ADE-bench suite + runner live here |
| `audit/` | JSONL event sourcing for every interaction |
| `dspy_opt/` | DSPy-based prompt optimisation utilities. Pyright strict excluded here (no dspy stubs) |

### ADE-bench integration (`~/repos/ade-bench`)

`LabratLocalAgent` (in the ade-bench repo at `ade_bench/agents/installed_agents/labrat_local/`) extends `BaseAgent` directly. It runs `claude` locally via `subprocess` using Mac OAuth, and bridges into the Docker container via `docker exec` / `docker cp`. See `decisions.md` for the auth rationale.

Run command:
```bash
cd ~/repos/ade-bench && uv run ade run <task_ids> --db duckdb --project-type dbt --agent labrat_local --no-diffs --n-concurrent-trials 3
```

Baseline (2026-05-25, claude-sonnet-4-6): **67% overall** (40/60 tasks) — 93% easy, 73% medium, 53% hard.

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

**ADE-bench known failures** — `helixops_saas009` persistently fails (dbt run scope too narrow, 3 test models never built — genuine bug). `helixops_saas010` is flaky (passes ~50% of runs).

**decisions.md** is the living design log — add a dated entry for every significant architectural decision made in this repo.

## Key conventions

- Pyright strict applies to all of `src/labrat/` except `dspy_opt/` and `screens/`.
- `Connection` adapter names: use `duckdb_engine.py` (not `duckdb.py`) to avoid shadowing the library.
- Profile credentials live in the OS keyring via `keyring` — never logged or printed.
- `QueryEvent` never stores result rows (security decision).
- `asyncio_mode = "auto"` — no decorator needed on async tests.
- Tool `name`, `description`, and `input_model` must be `@property` methods, not class attributes.
