# LabRat — Decisions Log

> Working scratch doc. Updated by the coding agent as milestones progress.
> Treated as untracked / private. Other docs (PROJECT_PLAN, CONTEXT/*) are read-only.

## Conventions established

- Using `typing.Self` and `pathlib.Path` throughout.
- Pydantic `model_config = ConfigDict(frozen=True)` for value objects.
- `assets_dir` optional parameter on `render_banner` for testability (avoids test files touching real assets).
- Entry point: `labrat.cli:app` (typer Typer instance); `labrat.cli:app` invokes TUI by default.
- `pyright` strict mode scoped to `src/labrat`; third-party stubs missing are annotated with `# pyright: ignore[reportMissingModuleSource]`.
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` decorators needed.

## Trade-offs made

- **2026-05-23 — M1 — rich-pyfiglet API**: rich-pyfiglet (0.1.x) may or may not expose a clean `RichFiglet` class. Falling back to `pyfiglet.figlet_format` + `rich.Text` wrapping if needed. Documenting the actual API after first install.
- **2026-05-23 — M1 — Banner in Textual**: `render_banner(console)` renders to a rich Console. Textual app uses `get_banner_renderable()` (returns a Rich renderable) for Static widget. This avoids ANSI-escape re-parsing.

## Gotchas encountered

(none yet)

## Open questions

(none yet)

## Proposed plan/context updates

(none yet)

## Per-milestone notes

### M1: Project Scaffolding
- Installing dependencies per PROJECT_PLAN.md Section 5 bootstrap commands (all runtime + dev).
- Branding module exposes both `render_banner(console, variant)` and `get_banner_renderable(variant)` so Textual widgets can use the renderable directly.
- Snapshot tests generate SVG on first run; stored in `tests/fixtures/snapshots/`.

### M2: Database Abstraction Layer
- `Connection` ABC defined in `db/base.py`; `DuckDBConnection` in `db/duckdb_engine.py` (not `duckdb.py` to avoid shadowing the library).
- Polars chosen for query results throughout — 10–50× faster than pandas, cleaner API.

### M3: Connection Profile Management
- Credentials stored in OS keyring via `keyring` lib (macOS Keychain, GNOME Keyring, etc.). Never logged, never printed.
- Profile config uses TOML via `tomllib` (stdlib in 3.11+).

### M7: SQL Editor
- Extends Textual's built-in `TextArea`; tree-sitter-sql for syntax highlighting.
- `QueryEditor(text="")` constructor — no `dialect` arg at the widget level; dialect is handled at the SQL validation layer (M9).

### M9: SQL Validation
- `sqlglot` chosen over pg_query: supports all 7 dialects, Python-native, no native deps.

### M11: Tool Registry
- `Tool[InputT]` is generic over Pydantic model; `input_model` is an `@abstractproperty`.
- `anthropic_schema()` exports JSON schema for Anthropic API tool-use format.
- `name`, `description`, `input_model` must all be `@property` methods, not class attributes.

### M13: SQL Execution Safety
- Two-layer safety: read-only role at connection + EXPLAIN cost gate.
- Safety gate uses DuckDB `EXPLAIN` to estimate rows scanned before running.

### M14: Agent Loop
- Loop terminates when model returns no tool calls.
- `AnthropicProvider` wraps the Anthropic SDK; `OpenAICompatibleProvider` enables Ollama.

### M19: Audit Log
- JSONL format chosen over SQLite — human-readable, grep-able, no schema migrations.

### M22: Charts
- Two rendering strategies: plotext (unicode, always works) + matplotlib+kitty/sixel (rich, terminal-dependent).
- Image protocol detected at startup.

### M23: Postgres Adapter
- psycopg v3 (not v2) — async-first, better types, actively maintained.

### M24: Multi-Connection
- `ConnectionScopeError` raised when thread profile doesn't match active connection.
- In-memory DuckDB default is `read_only=False` (can't open in-memory DB as read-only).

### M25: Warehouse Adapters
- All 5 drivers (Snowflake, BigQuery, Redshift, Trino, MySQL) have no type stubs.
- Strategy: `# type: ignore[import-untyped]` on imports, `# pyright: ignore` on call sites.

### M26–M27: Eval Framework
- `EvalRunner` accepts `agent_fn: Callable[[str], Awaitable[str]]` — decouples runner from agent.
- SQL comparison normalizes whitespace and uppercases before comparing.
- `EvalReport.load_dict()` added in M27 for `ComparisonReport` deserialization.

### M28: Query History
- `QueryEvent` never stores result rows — explicit security decision.
- PII redaction order: SSN → email → phone (SSN must be first to avoid false positives).
- `QueryHistoryLog` is a module-level singleton in `run_sql.py` — monkeypatched in tests.

### M29: Context Engine
- `ContextBundle` has `dbt_models` and `mcp_descriptions` as `None` placeholders for M30.
- `ContextAnalyzer` uses `llm_fn` injection for testability — same pattern as M31 extractors.
- Table relevance: score = frequency × recency weight (30-day half-life exponential decay).

### M30: External Catalog
- `CatalogEntry` uses Pydantic (not dataclasses) — avoids `Unknown` type errors in strict mode.
- Field named `schema_name` not `schema` — `schema` conflicts with BaseModel class method.
- Last-writer-wins when multiple adapters describe the same table.
- DbtLoader: manifest.json → schema.yml fallback → raw SQL (no introspection).
- 9/10 Spider2-DBT tasks have parseable schema.yml files.

### M31: Self-Healing Memory
- Three scopes: global, table, thread — not per-column (too granular, rarely fires).
- `llm_fn` injection for both extractors — consistent with M29 pattern.
- JSONL store per profile; `increment_applied` rewrites file (small files, acceptable).

### M32: Custom Validations
- Per-rule LLM calls (not batch) — easier parsing, per-rule attribution, future parallelism.
- Response format: `"pass"` | `"warn: <explanation>"` | `"block: <explanation>"`.
- 3 built-in example rules ship disabled.

## Architecture overview (as built)

```
src/labrat/
├── agent/           # Agent loop, tool registry, LLM providers (M11, M14, M14.5)
│   ├── tools/       # Schema exploration, SQL execution, history search, memory recall
│   └── providers/   # Anthropic, OpenAI-compatible, Bedrock, Vertex
├── db/              # Connection ABC + 7 warehouse adapters (M2, M23–M25)
│   └── catalog.py   # Catalog, Schema, Table, Column, ForeignKey models
├── profile/         # Profile management, ConnectionSession (M3, M24)
├── catalog/         # External catalog adapters: dbt + MCP (M30)
├── context_engine/  # Personal domain builder: relevance, bundle, analyzer (M29)
├── history/         # Always-on query history log with PII redaction (M28)
├── memory/          # Self-healing memories: model, store, extractor, retrieval (M31)
├── validations/     # Custom validation rules + LLM checker (M32)
├── eval/            # Benchmark harness + baselines (M26, M27)
├── widgets/         # Textual UI widgets: editor, results, schema, chat, trace (M5–M10)
├── screens/         # TUI screens: main app, onboarding (M4, M5)
├── chart/           # Chart spec + unicode/image rendering (M21, M22)
├── thread/          # Thread + version model (M17)
├── audit/           # Audit log event sourcing (M19)
└── branding.py      # Banner + mascot (M1)
```
