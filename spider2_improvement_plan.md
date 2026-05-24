# Spider2-DBT Improvement Plan

> Written 2026-05-24. Based on competitive intelligence against Databao (#1, 60.29%) and
> SignalPilot (#2, 51.56%). Current baseline: 8.3% (1/12 dev tasks). Target: ≥35% within
> three phases, ≥50% longer term.
>
> Principle: each improvement is evaluated as a codebase addition, not a research idea.
> "Staff engineer" lens means: ship in phases, prefer boring solutions, note the real costs.

---

## Orientation: What We Already Have

Before listing what to build, it matters what already exists:

| Module | Status | Notes |
|--------|--------|-------|
| `agent/loop.py` | ✅ Built | Full async ReAct loop — terminates on no tool calls |
| `agent/tools/` | ✅ Built | `list_tables`, `describe_table`, `run_sql`, `sample_rows`, `search_columns`, etc. |
| `agent/providers/` | ✅ Built | Anthropic, OpenAI-compatible, Bedrock, Vertex |
| `catalog/dbt/loader.py` | ✅ Partial | Reads `manifest.json`, builds lineage. Missing: column types from `catalog.json`, M-Schema output, compiled SQL |
| `catalog/dbt/lineage.py` | ✅ Built | Lineage graph already scaffolded |
| `dspy_opt/executor.py` | ⚠️ Problem | `load_project_files()` = raw file dump. `identify_target_file()` has a broken fallback |
| `dspy_opt/module.py` | ⚠️ Single-shot | DSPy ChainOfThought — no feedback loop, no tools |
| `context_engine/` | ✅ Built | Relevance scoring, bundles, analyzer |

The main `AgentLoop` is production-ready — it doesn't need replacing. The problem is entirely in the Spider2-DBT-specific code in `dspy_opt/` and the quality of context fed into any agent.

---

## Phase 1: Replace the Single-Shot DSPy Call with an Agent Loop

**Effort**: 1–2 weeks  
**Expected impact**: 8% → ~30–40%  
**Scope**: Spider2-DBT benchmark only (does not touch the TUI product)

### What to build

A new `src/labrat/dspy_opt/spider2_agent.py` — a purpose-built agent for Spider2-DBT that
replaces the current `DBTModelCompletion` DSPy module. It reuses the existing `AgentLoop` and
`ModelProvider` infrastructure but registers a Spider2-specific tool set.

**New file: `src/labrat/dspy_opt/spider2_agent.py`**

The agent receives a task (project dir, instruction, target file), then drives a ReAct loop with
these tools:

```
read_file(path)                     → file contents (truncated at 20k chars)
write_file_new(path, content)       → write only if not in pre_existing_files
edit_file(path, old_str, new_str)   → regex-replace in existing files
run_dbt(select=None)                → run `dbt run`, return returncode + log tail
run_sql(sql, limit=10)              → query the task's DuckDB, return schema + rows
grep_project(pattern)               → whole-project grep, capped at 200 matches
submit(description)                 → mark task complete (terminal tool)
```

**New file: `src/labrat/dspy_opt/tools/`** — one file per tool, following the existing
`agent/tools/` pattern. Each tool is a `Tool[InputModel]` subclass.

**Key guard in `write_file_new`**: scan all files in the project dir before the agent starts
and store as `pre_existing_files: frozenset[str]`. Any write attempt targeting a file in that
set returns an error string — not an exception. This prevents the agent from overwriting existing
model SQL, which is the single most common corruption pattern.

**`dbt_dirty` flag**: track whether any file was written/edited since the last successful
`run_dbt`. Skip the subprocess if `dbt_dirty=False` — `dbt run` takes 30–180s and re-running
it unnecessarily burns time.

**System prompt**: hand-written, not DSPy. Modeled on Databao's numbered-rules format.
Hard invariants:
- Never run `ATTACH` in SQL (breaks DuckDB sandbox)
- Never overwrite pre-existing SQL models
- Never call `submit` without a prior `run_dbt` returning returncode 0
- Always read the target file before writing it (understand what's already there)
- Always read at least one sibling SQL file from the same directory before writing

**`identify_target_file` fix**: the existing fallback (first alphabetically sorted SQL file)
is wrong. Fix: match `condition_tabs[0]` against `manifest.json` model names first, then
against file stems. If no match in manifest, log a warning and return the first SQL stub
(a file with `select 1` or empty content), not the first alphabetically.

**Integration with `autoresearch_spider2.py`**: replace the `dspy.ChainOfThought` call
with the new agent. The metric (`duckdb_match`) stays the same. The evaluate harness stays
the same. Just swap the module.

### Pros

- The `AgentLoop` is already built and tested — minimal new infrastructure
- Self-correction falls out naturally: the agent reads `run_dbt` errors and fixes them
- Compatible with the existing provider abstraction — works with Sonnet, Haiku, Opus
- Doesn't require any new Python dependencies
- Most of the expected benchmark gain (8% → 30%+) comes from this change alone,
  because every competitor with a real loop clears 35%+ even with mediocre prompts

### Cons / Risks

- **Token cost increases significantly**: a 30-turn agent loop uses 10–20× more tokens than
  a single-shot call. At ~45s/task for the current call, expect 5–15 minutes per task.
  With 12 dev tasks and 2 threads, a full evaluation round takes ~1–3 hours vs ~10 minutes now.
- **Debugging is harder**: when the agent fails, you get a 30-turn transcript to read, not a
  single SQL string. Need good logging — transcript.json per task.
- **Non-deterministic**: the same task may pass one run and fail the next. Need to run each
  task N=3 times and take majority vote for reliable benchmark numbers.
- **The `AgentLoop` is async** — the current `autoresearch_spider2.py` runs DSPy's thread-
  based parallelizer. Need to bridge async/sync. Easiest: use `asyncio.run()` per task in a
  `ThreadPoolExecutor`. Not elegant but works.
- **`identify_target_file` fix may unblock some tasks and regress others**: the fallback was
  wrong but consistently wrong. Test carefully before declaring it fixed.

### What NOT to do here

Don't port this to LangGraph. The existing `AgentLoop` does the same thing with less overhead
and no new dependency. LangGraph is worth considering only if we need branching/parallel
subgraphs, which we don't yet.

---

## Phase 2: Manifest/Catalog Parser — M-Schema Output

**Effort**: 3–5 days  
**Expected impact**: +5–15% on top of Phase 1  
**Scope**: shared (improves both Spider2-DBT agent and the TUI product)

### What to build

Upgrade `src/labrat/catalog/dbt/loader.py` and add a new serialization function. Two parts:

**Part A: Extract column types from `catalog.json`** (currently only row counts are read)

`catalog.json` nodes contain a `columns` dict with `type` per column. The current
`_enrich_from_catalog` only reads `stats.row_count`. Extend it to also read actual warehouse
column types and update `ColumnEntry.data_type`. This matters because `schema.yml` often has
no `data_type` field — `catalog.json` is the authoritative source.

**Part B: Extract `compiled_code` from `manifest.json`**

`manifest.json` nodes contain `compiled_code` (the final Jinja-rendered SQL). This is more
useful to the agent than raw source SQL because refs/sources are already resolved to actual
table names. Add `compiled_sql: str` to `CatalogEntry` (or as a separate dict keyed by
model name in `DbtLoader`).

**New file: `src/labrat/catalog/dbt/mschema.py`**

A `serialize_m_schema(entries: dict[str, CatalogEntry], selected: list[str] | None = None) → str`
function that produces M-Schema format for a set of catalog entries:

```
[DB_ID] <project_name>

# Table stg_orders
(order_id, INTEGER, Unique identifier for each order, PK, [1001, 1002, 1003])
(customer_id, INTEGER, FK to dim_customers.customer_id, FK)
(status, VARCHAR, Order fulfillment status, [pending, shipped, delivered, cancelled])
(created_at, TIMESTAMP, When the order was placed)

[Foreign Keys]
stg_orders.customer_id → dim_customers.customer_id
```

`selected=None` means all models. `selected=['stg_orders', 'dim_customers']` means only those.

**Sample value extraction**: requires a DuckDB connection. Add an optional
`connection: duckdb.DuckDBPyConnection | None` parameter to `DbtLoader.__init__`. When
present, `load()` queries `SELECT DISTINCT <col> FROM <table> LIMIT 5` for VARCHAR/BOOLEAN
columns. When absent (e.g., during dbt parse before any data exists), sample values are omitted.

**Integration with Spider2-DBT agent (Phase 1)**: the agent's initial context message
contains a compact file tree + M-Schema for all models. The `search_context` tool (if we add
one) returns M-Schema snippets for specific models. For now, inject full M-Schema upfront and
let the agent read specific files on demand — this is Databao's approach and requires zero
retrieval infrastructure.

**Integration with TUI context engine (M29/M30)**: `ContextBundle` currently has
`dbt_models: None` placeholder. Wire in: when a dbt catalog is loaded, serialize as M-Schema
and include in the bundle. This replaces the raw schema.yml text if any was included before.

### Pros

- Reuses existing `DbtLoader` infrastructure — not a rewrite, just extension
- M-Schema is empirically the best-performing format (+2.03% EX average over raw DDL)
- `compiled_code` removes one class of wrong-model errors: the agent sees resolved SQL
  instead of Jinja templates with ambiguous `{{ ref('...') }}` calls
- Column types from `catalog.json` fix the verifier's type mismatch detection (Phase 3)
- Improves the TUI product, not just the benchmark

### Cons / Risks

- **`catalog.json` requires `dbt docs generate`**, which takes 30–120s. In practice, Spider2-DBT
  tasks include pre-compiled artifacts, but for the TUI product, this is a setup step users
  must run. Consider making it optional and degrading gracefully when absent.
- **Sample value extraction requires a live DB connection in `DbtLoader`**, which is a new
  dependency. The current `DbtLoader` is connection-free (reads JSON files only). Options:
  (a) pass the connection in optionally, (b) do it in a separate post-load enrichment step,
  (c) cache sample values in a sidecar file. Option (b) is cleanest.
- **M-Schema for large projects** (100+ models) will overflow the context window. The
  serialization should truncate at a configurable token budget. This is fine for Spider2-DBT
  (5–30 models per project) but needs thought for the TUI product against large warehouses.
- **`compiled_code` includes resolved table names** but also Jinja macros the compiler didn't
  expand (custom macros). Don't assume compiled_code is runnable SQL — it's context, not output.

---

## Phase 3: Reference Snapshot + Deterministic Verifier

**Effort**: 3–5 days  
**Expected impact**: +5–10% on top of Phases 1–2 (catches completions the agent "thinks" passed)  
**Scope**: Spider2-DBT benchmark only

### What to build

**New file: `src/labrat/dspy_opt/snapshot.py`**

`capture_reference_snapshot(project_dir: Path, db_path: Path) → ReferenceSnapshot`

Before the agent runs:
1. Open the task's DuckDB
2. For each model defined in `dbt_project.yml` / schema.yml that is a STUB (empty or `select 1`):
   - If the table already exists in DuckDB (pre-populated source data): capture its schema and
     3 sample rows
   - If not: record "not yet populated"
3. Serialize to `reference_snapshot.md` in the work directory

```markdown
## stg_orders (pre-existing: yes)
Row count: 1,482
Columns: order_id INTEGER, customer_id INTEGER, status VARCHAR, created_at TIMESTAMP
Sample rows:
| order_id | customer_id | status | created_at |
|----------|------------|--------|------------|
| 1001 | 42 | shipped | 2024-01-15 09:32:00 |
...
```

**Post-build deterministic verifier** in `src/labrat/dspy_opt/verifier.py`:

`verify_build(snapshot: ReferenceSnapshot, db_path: Path, eval_tables: list[str]) → VerifyResult`

Runs after `run_dbt` returns 0:
1. **Column schema check**: for each eval table, query `information_schema.columns` and compare
   name + type against snapshot. Mismatches return `ColumnMismatch` (not LLM — pure Python).
2. **Row count check**: `SELECT COUNT(*) FROM <table>`. If count differs from snapshot by >10%
   and snapshot count > 0, return `RowCountMismatch` with the delta.
3. **Value spot-check**: pick the first sample row's PK column and value from the snapshot,
   run `SELECT * FROM <table> WHERE <pk> = <val>`, compare the returned row against the
   snapshot row column-by-column with numeric tolerance 1e-2.

`VerifyResult` is a structured dataclass — not a pass/fail bool. It contains a list of failures
per check, so the agent can read the result and attempt targeted fixes.

**Integration with the Phase 1 agent**: after `run_dbt` returns 0, the agent automatically
receives the verifier result as a tool result. If there are failures, it's expected to fix
them before calling `submit`. This is cleaner than a separate verifier agent — the
same agent handles fixes, staying in its single context window.

### Pros

- **Deterministic verification is more reliable than an LLM verifier**: no hallucinated
  pass verdicts, no prompt sensitivity. Pure Python comparisons.
- Catches the most common failure modes: column name aliasing (agent uses `total_sales`
  but eval expects `sales_total`), type mismatches (INTEGER vs BIGINT), fan-out bugs (wrong
  JOIN produces 3× rows), off-by-one date ranges.
- `reference_snapshot.md` also helps the agent during generation: it can read the snapshot
  to understand what output shape is expected before writing SQL.
- The verifier double-checks the eval metric, reducing false positives (tasks that pass
  `duckdb_match` due to loose matching but would fail in prod).

### Cons / Risks

- **Snapshot may not exist for all tasks**: if a task's DuckDB starts empty (no pre-populated
  tables), there's nothing to snapshot against. The verifier degrades to column schema only.
  This is fine — partial verification is still better than none.
- **Column-by-column value comparison is fragile for float columns**: `1e-2` tolerance works
  for most financial data but will fail for small decimals (tax rates, ratios). Need to handle
  NULL comparison carefully too (`NULL ≠ NULL` in SQL; `IS NOT DISTINCT FROM` is the right check).
- **Row count check produces false positives for slowly-changing models**: some models are
  expected to have fewer rows than the source on first run. The `>10%` threshold is a heuristic;
  may need per-task tuning.
- **Adds 10–30s per task** for the snapshot capture and verification queries. Acceptable at the
  scale of Spider2-DBT (68 tasks), but worth noting.
- **The verifier is a new code path that can contain bugs** — a buggy verifier that reports
  false failures will cause the agent to thrash and waste turns. Write tests for it.

---

## Phase 4: Planning Span Before SQL Generation

**Effort**: 1–2 days  
**Expected impact**: +3–8% (addresses wrong-model and wrong-column hallucinations)  
**Scope**: Spider2-DBT benchmark; same pattern can be adopted in TUI agent prompts later

### What to build

A system prompt addition and a structured output parser. No new files — modifies the Spider2
agent's system prompt and adds a parsing step.

The system prompt instructs the agent to output a YAML planning block before writing any SQL:

```
Before writing SQL for any model, output exactly this YAML block:
---plan
target_model: models/fct_orders.sql
source_tables_and_why:
  - stg_orders: contains the order fact rows (grain: one per order)
  - dim_customers: need customer email and region
  - dim_dates: need date dimension for quarter lookup
key_joins:
  - stg_orders.customer_id = dim_customers.customer_id
  - stg_orders.created_at_date = dim_dates.date_key
grain: one row per order_id
estimated_row_count: ~1500 (matches stg_orders cardinality)
---
```

After emitting the plan, the agent writes SQL. A `parse_plan(text: str) → Plan | None` helper
in the agent validates that `target_model` matches the actual target file. If the plan targets
the wrong model, the tool result returns an explicit error: "Your plan targets `models/circuits.sql`
but the task requires `models/fct_driver_rankings.sql`. Revise your plan."

This catches the wrong-model class of failures early — before any SQL is written.

### Pros

- Tiny implementation: ~50 lines of prompt addition + ~30 lines of parser
- Directly addresses our largest failure category: wrong model completed
- The plan is also useful as a debugging artifact — you can read what the agent *intended*
  to do vs. what it wrote
- Thinkquel (the dedicated text-to-dbt model) uses exactly this pattern and achieves 92.2% EX

### Cons / Risks

- **The model can comply formally but still make the same mistakes**: writing a plan that says
  "grain: one row per order" and then generating SQL with a JOIN fanout. The plan doesn't
  guarantee correctness — it just makes errors more visible.
- **Adds output tokens per model**: the plan is ~100–200 tokens per model. On a 10-model
  project, that's 1000–2000 extra tokens — not significant but not free.
- **Parser brittleness**: YAML parsing is fragile if the model reformats the block. Use a
  lenient parser (regex + key extraction) rather than strict YAML parsing. The exact format
  matters less than extracting `target_model`.
- **May conflict with the agent's internal reasoning**: some models produce better results
  when reasoning freely. The planning constraint could occasionally make the model overthink
  a simple task. Low risk, but worth monitoring.

---

## Phase 5: Date Determinism for Spider2-DBT Eval

**Effort**: 3–5 days, higher complexity than it sounds  
**Expected impact**: +3–8% (affects tasks with `current_date` / rolling windows)  
**Scope**: Spider2-DBT benchmark eval only — not a product concern

### What to build

**Background**: dbt models that use `current_date` or `current_timestamp` produce different
output depending on when `dbt run` executes. Gold databases were built at a specific date.
Running them today produces wrong output even with correct SQL logic. SignalPilot reverse-
engineered the gold build date by scanning calendar spines and date column boundaries.

**Part A: Gold date derivation** — `src/labrat/dspy_opt/date_derive.py`

`derive_gold_date(gold_db_path: Path, task_manifest: dict) → date | None`

For each task that has a gold DuckDB, scan for:
1. Calendar spine tables (`RANGE(DATE '...', DATE '...', INTERVAL 1 DAY)` patterns) — the
   max date is the build date
2. Age/tenure columns: `SELECT MAX(tenure_days) FROM employees` → `today - max_tenure_days`
3. Rolling window columns: `WHERE created_at >= CURRENT_DATE - INTERVAL 90 DAY` in model SQL
   → run `SELECT MAX(created_at) FROM <table>` and derive the cutoff

Cache results in `autoresearch_output/gold_build_dates.json` — run once per task, not per eval.

**Part B: Date injection** — platform-specific, this is where it gets hard

*Option 1: `libfaketime` (Linux only)*
```bash
libfaketime -f "2024-03-15" dbt run ...
```
Simple, reliable. Does not work on macOS without Homebrew `faketime` which is finicky with
multi-threaded Python. The eval runs in CI (presumably Linux), but local dev on macOS breaks.

*Option 2: DuckDB date override (the better option for DuckDB-only tasks)*
```python
conn.execute("SET TimeZone = 'UTC'")
# DuckDB doesn't have SET current_date, but you can override via connection:
conn.execute("SELECT setseed(0)")  # not relevant
# Instead: patch the dbt Jinja context
```
DuckDB does not expose `SET current_date`. But dbt renders Jinja before SQL execution.
We can monkey-patch dbt's Jinja context to return a fixed `current_date` value by setting
an environment variable that a custom macro reads: `{% set today = env_var('DBT_TODAY', modules.datetime.datetime.now().strftime('%Y-%m-%d')) %}`.
This requires adding a custom macro to each task project — invasive and error-prone.

*Option 3: Pre-generated seed SQL (pragmatic fallback)*
Before `dbt run`, patch model SQL files that contain `current_date` or `current_timestamp`
by replacing them with the hardcoded gold date string. This is auditable and testable.
Fragile for complex date expressions but handles simple cases (>60% of date-related failures).

**Recommendation**: implement Option 3 for now (low effort, handles most cases), track how
many tasks it fixes, and revisit Option 1 if running evaluation in a Linux container anyway.

### Pros

- Addresses a correctness issue most competitors missed (only SignalPilot explicitly solved it)
- Option 3 (simple string replace) can be implemented in an afternoon and fixes the obvious cases

### Cons / Risks

- **The gold date derivation heuristic may be wrong**: a date column max might be stale data,
  not the build date. False derivations poison all subsequent runs. Build in confidence scoring
  (multiple signals must agree) and manual override.
- **Option 3 is fragile**: regex-replacing `current_date` in SQL is easy to get wrong (it may
  appear in comments, strings, or complex expressions). Use sqlglot to parse and replace properly.
- **macOS dev vs. Linux CI mismatch**: if we use `libfaketime`, it works in CI but not locally.
  This is a real workflow friction point.
- **Some tasks may intentionally use a rolling window relative to today** — replacing the date
  would produce wrong output. The derivation logic needs to distinguish "calendar endpoint"
  from "relative to today" patterns.

---

## Phase 6: FK Graph + BFS Schema Linking

**Effort**: 1–2 weeks  
**Expected impact**: +5–15% on projects with >20 models; ~0% on small projects (≤15 models)  
**Scope**: shared (Spider2-DBT + TUI product)

### What to build

**New file: `src/labrat/catalog/dbt/schema_graph.py`**

The `catalog/dbt/lineage.py` file already exists — check what's in it before building this.
Assuming it tracks upstream/downstream model edges, add:

`DbtSchemaGraph` — a wrapper around `networkx.DiGraph` built from `CatalogEntry.upstream` /
`CatalogEntry.downstream`:

```python
class DbtSchemaGraph:
    def __init__(self, entries: dict[str, CatalogEntry]) -> None: ...
    
    def seed_models(self, question: str, llm_fn: ...) -> list[str]:
        """3-layer entity resolution: exact match → ENTITY_MAP → column scan."""
        ...
    
    def expand_bfs(self, seeds: list[str], depth: int = 2) -> list[str]:
        """BFS from seed models through upstream/downstream edges."""
        ...
    
    def select_context(self, question: str, max_models: int = 8) -> list[str]:
        """Full pipeline: seed → BFS → return model names."""
        ...
```

**3-layer entity resolution**:
1. Exact match: does the question contain a model name exactly? (`stg_orders` in question → seed)
2. ENTITY_MAP: a configurable `dict[str, str]` mapping business terms to model names.
   `{"revenue": "fct_revenue", "orders": "stg_orders", "customers": "dim_customers"}`.
   Per-project, auto-generated from model descriptions at load time.
3. Column scan: does any column description contain a term from the question? BM25-style.

**Integration with Phase 1 Spider2 agent**: use `schema_graph.select_context(instruction)` to
select which models to include in M-Schema upfront context. This reduces the context footprint
from ~full project dump to 5–8 most relevant models.

**Integration with TUI `ContextEngine` (M29)**: `ContextAnalyzer` already scores table
relevance by frequency × recency. Add FK graph traversal as a third signal: a table that is
2 hops from a recently-used table gets a relevance bonus.

### Pros

- The data is already there: `CatalogEntry.upstream` and `.downstream` are already populated
  by `DbtLoader`. The graph just needs to be wrapped.
- 93% token reduction on large projects (35+ models) with maintained recall — this is the
  difference between being able to run large Spider2-Snow tasks at all vs. context overflow
- `networkx` is a small dependency that's already probably in the environment
- The ENTITY_MAP doubles as documentation of the project's business vocabulary — useful for
  the human team too

### Cons / Risks

- **For Spider2-DBT's 5–30 model projects, BFS provides little benefit**: if the full project
  fits in context (and at M-Schema density it usually does), you're adding complexity for
  marginal gain. Don't block Phase 1–3 on this.
- **ENTITY_MAP auto-generation is LLM-dependent** at index time: generating "revenue → fct_revenue"
  from model descriptions requires an LLM call. This adds latency to the first catalog load.
  Cache aggressively; make it a background task.
- **BFS at depth 2 can explode on a fan-out node** (a model referenced by 30 others):
  `dim_dates` in most dbt projects is connected to everything. Add a node degree cap
  (skip any node with upstream+downstream count > 15 from automatic BFS expansion; require
  explicit inclusion).
- **False negatives**: the entity resolver may miss a model the agent needs. The agent should
  still have `read_file` and `grep_project` as escape hatches when it can't find a table
  in the selected context. Don't make the graph selection a hard constraint.

---

## Phase 7: MIPROv2 Re-engagement (After Phases 1–3)

**Effort**: ongoing — runs in the background  
**Expected impact**: +3–10% on top of the agent loop baseline  
**Scope**: Spider2-DBT benchmark only

### What changes

Once Phases 1–3 are complete, the autoresearch loop becomes meaningful:
- The metric signal is richer: instead of binary pass/fail on a single SQL string, the agent
  produces a multi-turn transcript with verifier failures. These failures are structured
  (ColumnMismatch, RowCountMismatch, ValueMismatch) — MIPROv2 can correlate prompt variants
  with specific failure types.
- The system prompt is now a hand-crafted Jinja template (Phase 1). MIPROv2 should optimize
  the instruction component of the prompt, not the entire template.
- The val set problem goes away once playbook001 and other tasks are passable: with a better
  agent, more tasks pass, more folds have signal, MIPROv2 gets real gradient.

**Changes to `autoresearch_spider2.py`**:
- Replace the `DBTModelCompletion` DSPy module with the new Spider2 agent
- The DSPy `Evaluate` call still works — it just invokes the agent instead of a ChainOfThought
- Add structured logging of verifier failures per task per iteration — this becomes MIPROv2's
  richer signal
- Resume `--threads 2` with the same usage-limit discipline

### Pros

- No new infrastructure needed: the autoresearch loop is already built
- If the agent gets to 30%+ baseline, MIPROv2 has real signal to optimize
- The structured verifier failures give MIPROv2 something specific to optimize toward

### Cons / Risks

- **Cost is now much higher**: 30-turn agent × 12 tasks × 10 trials × 4 folds = ~1440
  agent turns per MIPROv2 iteration. At ~45s/turn, that's 18 hours per iteration.
  With `--threads 2`, still 9 hours. Budget accordingly; don't run this nightly.
- **DSPy's optimizer may not handle multi-turn agent prompts well**: MIPROv2 was designed
  for single-turn module optimization. The instruction it optimizes is the system prompt
  preamble. Test that the DSPy interface still works cleanly with the new agent structure.
- **If the agent baseline is already good (40%+), MIPROv2's marginal gain may be small**:
  the top competitors got their 60% and 51% without any optimizer. Don't over-invest here.

---

## Phase 8: Evidence Injection (SEED Pattern)

**Effort**: 1 week  
**Expected impact**: +4–10% (highest impact on tasks with ambiguous column semantics)  
**Scope**: shared (Spider2-DBT + TUI product)

### What to build

**New file: `src/labrat/catalog/dbt/evidence.py`**

`generate_evidence(entry: CatalogEntry, llm_fn, db_connection | None) → EvidenceBlock`

For each model in the catalog, auto-generate:
- **Synonym map**: column aliases and business terms (`total_amt` = revenue = GMV?)
- **Domain rules**: valid value ranges, enum values, units (`status ∈ {pending, shipped, cancelled}`)
- **Value illustrations**: concrete examples showing how a column is used in context

This is a one-time LLM call per model, cached to `catalog/.evidence_cache.json`, invalidated
when the model's `compiled_code` hash changes.

The evidence block is appended to the M-Schema for each model:

```
# Table fct_orders
(order_id, INTEGER, Unique order identifier, PK)
(status, VARCHAR, Order fulfillment status, [pending, shipped, delivered, cancelled])
...

[Evidence]
- "revenue" and "GMV" in questions refer to `subtotal_usd`
- Only count orders with status != 'cancelled' for active order metrics
- `customer_id` is the Shopify customer ID, not internal; join to dim_customers on this
```

**Integration**: `mschema.py` calls `EvidenceStore.get(model_name)` and appends when present.
The evidence store is a thin wrapper around the JSON cache.

### Pros

- SEED paper shows +17.73% EX on BIRD with human evidence; auto-generated matches it
- Cached: generated once, used on every run until the model changes
- Directly addresses "near-miss" column failures: the agent picks `total_sales` when the
  correct column is `net_revenue_usd`. A synonym map fixes this.
- The evidence cache is also useful as human-readable project documentation

### Cons / Risks

- **LLM-generated evidence can be wrong**: if the model hallucinates a synonym, the agent
  will use the wrong column confidently on every future run. Need a review/approval step
  before evidence is trusted in production.
- **Dependency on `llm_fn`**: requires a model call at catalog index time. For the TUI product,
  this means the first catalog load is slow if evidence hasn't been generated. Make evidence
  generation a background task triggered after dbt run, not blocking.
- **Evidence for Spider2-DBT is harder**: these are real dbt projects from enterprise clients.
  The evidence generator may not have enough domain context to correctly infer that `GMV = total_amt`.
  May need few-shot examples tuned to the dbt domain.
- **Cache invalidation complexity**: if a model's column is renamed, old evidence pointing to
  the old column name is silently wrong. The `compiled_code` hash catches this, but only if
  `dbt compile` has been re-run.

---

## Summary Table

| Phase | What | Effort | Impact | Scope | Dependency |
|-------|------|--------|--------|-------|------------|
| **1** | Replace DSPy with agent loop + guarded tools | 1–2 wks | +20–35pp | Spider2 | None |
| **2** | M-Schema from manifest.json + catalog.json | 3–5 days | +5–15pp | Shared | Phase 1 |
| **3** | Reference snapshot + deterministic verifier | 3–5 days | +5–10pp | Spider2 | Phase 1 |
| **4** | Planning span before SQL generation | 1–2 days | +3–8pp | Spider2 | Phase 1 |
| **5** | Date determinism for eval | 3–5 days | +3–8pp | Spider2 | None |
| **6** | FK graph + BFS schema linking | 1–2 wks | +5–15pp | Shared | Phase 2 |
| **7** | MIPROv2 re-engagement | Ongoing | +3–10pp | Spider2 | Phases 1–3 |
| **8** | Evidence injection (SEED pattern) | 1 wk | +4–10pp | Shared | Phase 2 |

**Cumulative ceiling estimate (phases 1–5, 3–4 weeks work)**: 8% → 40–55%
**Note**: ranges are wide because the benchmark has only 12–68 tasks — one task is 1.5–8.3pp.

---

## What to Skip (and Why)

**Vector database**: networkx BFS + keyword matching is sufficient for ≤50 model projects.
Adding a vector store (Chroma, FAISS, DuckDB VSS) before validating that Phase 6 produces
gains would be premature optimization. Add if BFS + keyword miss rate is measurably high.

**Second verifier agent (SignalPilot style)**: a second LLM call for verification is
expensive and nondeterministic. The deterministic Python verifier in Phase 3 does the same
job with less cost and more reliability. Reserve agent-based verification for cases where
the Python verifier cannot express the check (semantic correctness, not just structural).

**Fine-tuning**: Thinkquel (the fine-tuned text-to-dbt model) achieves 92% EX — better than
everything. But it requires a labeled training dataset and a fine-tuning pipeline. Our entire
dataset is 68 tasks — not enough. The top zero-shot agents hit 60% with architecture, not
fine-tuning. Don't go here until we have 500+ labeled examples.

**LangGraph**: our `AgentLoop` does the same thing without the dependency. LangGraph adds
value only when you need conditional branching between subgraphs (e.g., a specialist verifier
subgraph) — which we can handle with conditional logic in the main loop.

**DSPy autoresearch on the current architecture**: already demonstrated it doesn't work.
Don't resume until Phase 1 is complete.

---

## Recommended Execution Order

**Week 1**: Phase 1 (agent loop) + Phase 4 (planning span, 2 days)  
**Week 2**: Phase 2 (M-Schema) + Phase 3 (verifier)  
**Week 3**: Phase 5 (date determinism) + Phase 7 (resume MIPROv2)  
**Week 4+**: Phase 6 (FK graph) + Phase 8 (evidence)  

Run the benchmark after each phase with `--max-iters 1 --examine-failures` to validate the
delta before moving on. The impact estimates above assume each phase compounds — verify that
assumption empirically.
