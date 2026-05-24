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

### Spider2-DBT autoresearch (2026-05-23)

- **DSPy 3.x API change**: `dspy.LM("anthropic/...")` requires real Anthropic API credits. With a Max
  subscription (no API credits), you must use a custom `dspy.BaseLM` subclass that shells out to the
  `claude --print` CLI. See `src/labrat/dspy_opt/claude_code_lm.py`.

- **`EvaluationResult` not a float**: DSPy 3.x `dspy.Evaluate()(module)` returns an `EvaluationResult`
  object, not a float. `.score` is 0–100. Use `.score / 100.0`.

- **`optuna` not bundled**: `dspy.MIPROv2` requires `optuna` separately — `pip install dspy[optuna]`
  or `uv pip install optuna`. Not pulled in by `dspy-ai` alone.

- **`dbt` binary in venv**: `shutil.which("dbt")` fails when dbt is in `.venv/bin/` but not on PATH.
  Fix: check `Path(sys.executable).parent / "dbt"` first.

- **Train/val split bias**: the Spider2-DBT JSONL is ordered; `playbook001` (the only passing task at
  baseline) was always first, so the static 2/3–1/3 split always put it in training, never in val.
  MIPROv2 saw zero positive signal on every trial. Fixed with shuffle (seed=42) + k-fold rotation.

- **Usage limit kills MIPROv2 trials silently**: when Claude CLI hits usage cap mid-eval, each
  `claude --print` call returns the string "You're out of extra usage · resets 7:50pm …" instead of
  SQL. DSPy's parallelizer catches the resulting parse error and marks the example as failed — every
  trial scores 0.0. Fix: bump usage limit before running; monitor task timing (normal ~45s/task vs
  <2s when exhausted).

- **`--max-iters 0` means run forever**: zero is the "unlimited" sentinel, not "zero iterations".

- **Findings file ordering**: `init_findings()` always appends a new run header on restart. Iteration
  numbers reflect the in-process counter, not a global counter — after a crash and restart, numbering
  resets to 1.

### Failure analysis: what Sonnet gets wrong on Spider2-DBT (2026-05-23)

Diagnosed by reading generated SQL from `autoresearch_output/examine/failures_iter0.md` (v1 baseline)
and `autoresearch_output/may23v2/failures_iter0.md` + `failures_iter1.md` (v2 prompt, after MIPROv2 iter 1):

**v2 prompt fixed (DuckDB dialect now correct):**
- workday002: uses `STRING_AGG` correctly, proper join chain — held across iter 1 ✅
- provider001: `strptime()` + `cast(... as date)` — clean ✅
- netflix001: `TRY_CAST(strptime(...))`, `regexp_replace` without flags — stable ✅
- analytics_engineering001: `current_timestamp`, complex multi-join — looks correct ✅

**Persistent failures (prompt alone insufficient):**

1. **Wrong model completed — date-spine pattern** (xero_new001, flicks001): Model generates a
   `dbt_utils.date_spine` helper instead of the target mart. Prompt says "Complete ONLY target_file"
   but the model ignores it when a date-spine helper is the most salient pattern in project_files.
   This is a few-shot / context contamination problem — MIPROv2's bootstrapped traces from date-spine
   tasks poison unrelated tasks.

2. **Wrong model completed — other** (f1001 → `circuits`, shopify001 → customer rollup,
   chinook001 → `dim_date` calendar, asset001 → executions/positions): Each project's `project_files`
   context contains a model that pattern-matches to the instruction more strongly than the actual
   target. The "only complete target_file" instruction is not sufficient to override this.

3. **Unavailable package macros** (shopify001, asana001): `shopify.shopify_partition_by_cols`,
   `dbt_utils.date_spine`, `dbt.datediff` — these packages not installed in the eval project.
   Rule added to prompt but model still uses them when it sees them in project_files as templates.

4. **Near-miss** (chinook001, provider001, flicks001): Structure right but column/join details diverge.

### MIPROv2 signal problem (2026-05-23)

MIPROv2 scored 0.0% on all 10 trials in iteration 1 despite k-fold + shuffle fix. Root cause:
fold 1 = (xero_new001, flicks001, asana001) — none of these tasks pass, so every trial is blind.
The only reliably-passing task (playbook001) lands in val on fold 4 (iteration 4 onward).
With 12 tasks and 4 folds of 3, only 1 fold has any positive signal — making 3 out of 4 iterations
completely uninformative for MIPROv2.

**What's needed to make MIPROv2 work:**
- More passing tasks in the dev set (expand from 12 to 30–40 tasks, with easier ones included)
- OR hand-labeled few-shot examples to give MIPROv2 positive signal regardless of fold
- OR reduce to 2 folds so playbook001 appears every other iteration (more signal, less diversity)
- The wrong-model failures need a deeper fix: possibly examine what `identify_target_file()` resolves
  to for each failing task — `target_file` may itself be pointing at a helper model in some projects.

## Open questions

- Can Haiku with an optimized prompt approach Sonnet's baseline? Haiku scored 0.0% at baseline —
  MIPROv2 run not started (waiting for v2 prompt to be validated first).
- For tasks where the model generates a date-spine helper: is `target_file` pointing at the right
  model, or is `identify_target_file()` itself resolving to a helper/staging model?
- Would hand-labeled few-shot examples for 3–4 tasks unlock MIPROv2 signal across all folds?

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
