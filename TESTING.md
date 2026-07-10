# LabRat — Manual Testing Guide

Work through the milestones in order. Each section lists the exact commands to
run, the steps to take inside the TUI, and the conditions to verify.

---

## Setup

**Working directory** — run every command from the repo root:

```
cd /Users/ege/repos/labrat
```

**Test database** (ecommerce fixture, read-only DuckDB):

```
tests/fixtures/sample_dbs/ecommerce.duckdb
```

Tables: `customers` (6 rows), `events` (4 rows), `orders` (8 rows), `products` (3 rows)

**Launch the app** (all milestones M4 onward use this):

```bash
uv run labrat
```

---

## M1 · Branding & Project Scaffolding

**Goal:** ASCII mascot and banner render correctly.

```bash
uv run python -c "from labrat.branding import get_banner_renderable; from rich import print; print(get_banner_renderable('splash'))"
uv run python -c "from labrat.branding import get_banner_renderable; from rich import print; print(get_banner_renderable('header'))"
```

**Check:**
- [ ] `splash` variant prints a large rat ASCII art banner
- [ ] `header` variant prints a compact single-line banner
- [ ] Neither prints any Python errors or tracebacks

---

## M2 · Database Abstraction Layer

**Goal:** DuckDB connection opens, introspects catalog, runs queries.

```bash
uv run python - <<'EOF'
from labrat.db.duckdb_engine import DuckDBConnection
from pathlib import Path
conn = DuckDBConnection(Path("tests/fixtures/sample_dbs/ecommerce.duckdb"), read_only=True)
conn.connect()
cat = conn.introspect_catalog()
for schema in cat.schemas:
    for tbl in schema.tables:
        print(f"  {schema.name}.{tbl.name}  ({len(tbl.columns)} cols)")
df = conn.execute("SELECT order_id, total_amount, status FROM orders LIMIT 3")
print(df)
stats = conn.column_stats("orders", "total_amount")
print(stats)
conn.disconnect()
EOF
```

**Check:**
- [ ] Four tables listed: customers, events, orders, products
- [ ] DataFrame with 3 rows and columns order_id / total_amount / status
- [ ] Stats dict contains min, max, avg keys
- [ ] No errors on disconnect

---

## M3 · Connection Profile Management

**Goal:** CLI `conn add` / `conn list` / `conn remove` work end-to-end.

```bash
# Add the ecommerce fixture as a profile
uv run labrat conn add \
  --name ecommerce \
  --dialect duckdb \
  --path "$(pwd)/tests/fixtures/sample_dbs/ecommerce.duckdb"

# Verify it was saved
uv run labrat conn list

# Test the connection is reachable
uv run labrat conn test ecommerce
```

**Check:**
- [ ] `conn list` shows `[RO] ecommerce  (duckdb)`
- [ ] `conn test` prints `Connection 'ecommerce' OK.`
- [ ] Running `conn add` with the same name again prints a friendly error (no crash)

```bash
# Cleanup if you want a fresh onboarding run later
uv run labrat conn remove ecommerce
```

---

## M4 · Onboarding TUI

**Goal:** First-run wizard walks through 5 steps and saves a profile.

```bash
# Make sure no profiles exist first
uv run labrat conn list   # should say "No profiles configured"
uv run labrat             # launches onboarding
```

**Steps inside the TUI:**

1. **Welcome screen** — press `Enter` to continue
2. **Dialect screen** — `DuckDB` is selected; press `Enter`
3. **Credentials screen** — enter path:
   ```
   /Users/ege/repos/labrat/tests/fixtures/sample_dbs/ecommerce.duckdb
   ```
   Give the profile a name (e.g. `ecommerce`), then press `Enter`
4. **Catalog screen** — skip (press `Enter` or the Skip button)
5. **Finish screen** — press `Enter`

**Check:**
- [ ] Step indicator shows progress (● filled, ○ empty) through all 5 steps
- [ ] After finish, the main three-pane layout opens automatically
- [ ] Bottom status bar shows `profile: ecommerce  dialect: duckdb  ● connected`
- [ ] `uv run labrat conn list` in another terminal shows the new profile

---

## M5 · Main Three-Pane Layout

**Goal:** Connected layout renders all panes with correct proportions.

After completing onboarding (or running `uv run labrat` with a profile saved):

**Check:**
- [ ] Left pane (~25% width) — Chat header visible
- [ ] Center pane (~55%) — Editor pane (top ~55%) and Results pane (bottom)
- [ ] Right pane (~20%) — Schema browser tree
- [ ] Top and bottom status bars both show profile / dialect / connection status
- [ ] Press `Ctrl+H` — schema pane disappears; press again to restore
- [ ] Press `Ctrl+L` — chat pane disappears; press again to restore
- [ ] Resize terminal below 80 cols — side panes hide, center goes full-width

**Keyboard bindings to verify:**

| Key | Expected focus |
|-----|---------------|
| `Ctrl+1` | Chat input |
| `Ctrl+2` | SQL editor |
| `Ctrl+3` | Results table |
| `Ctrl+4` | Schema tree |

---

## M6 · Schema Browser

**Goal:** Tree expands databases → schemas → tables → columns.

In the running TUI, press `Ctrl+4` to focus the schema pane.

**Steps:**
1. Press `Enter` or `→` on the root node — schema(s) expand
2. Navigate to `orders` with arrow keys; press `Enter` — columns list appears
3. Confirm column names: order_id, customer_id, product_id, total_amount, status, region, created_at

**Check:**
- [ ] Tree shows all 4 tables (customers, events, orders, products)
- [ ] Expanding a table shows its columns with data types
- [ ] Scrolling works if the list is long

---

## M7 · SQL Editor

**Goal:** Editor accepts multi-line SQL, syntax-highlights it, and is focusable.

Press `Ctrl+2` to focus the editor. Type or paste:

```sql
SELECT
    c.name,
    COUNT(o.order_id)           AS order_count,
    SUM(o.total_amount)         AS lifetime_value
FROM   customers c
JOIN   orders o ON o.customer_id = c.customer_id
WHERE  o.status = 'completed'
GROUP  BY c.name
ORDER  BY lifetime_value DESC;
```

**Check:**
- [ ] SQL keywords (`SELECT`, `FROM`, `JOIN`, etc.) appear in a distinct colour
- [ ] Multi-line text wraps within the pane
- [ ] Cursor moves with arrow keys; `Home`/`End` work within a line
- [ ] `Ctrl+A` selects all text

---

## M8 · Schema-Aware Completion

**Goal:** Completer returns ranked table and column candidates.

```bash
uv run python - <<'EOF'
from labrat.sql.completer import SQLCompleter
from labrat.db.duckdb_engine import DuckDBConnection
from pathlib import Path
conn = DuckDBConnection(Path("tests/fixtures/sample_dbs/ecommerce.duckdb"), read_only=True)
conn.connect()
catalog = conn.introspect_catalog()
conn.disconnect()

c = SQLCompleter(catalog=catalog)

# Table completion after FROM
results = c.complete("SELECT * FROM ord", cursor_offset=17)
print("After 'FROM ord':")
for r in results[:5]:
    print(f"  {r.label:20s}  {r.kind:10s}  score={r.score}")

# Column completion after dot
results2 = c.complete("SELECT orders.", cursor_offset=14)
print("\nAfter 'orders.':")
for r in results2[:6]:
    print(f"  {r.label:20s}  {r.kind:10s}")
EOF
```

**Check:**
- [ ] `orders` appears in the first result set for `FROM ord`
- [ ] Column results after `orders.` include order_id, customer_id, total_amount, status, etc.
- [ ] `score=300` for exact matches, `score=200` for prefix matches

---

## M9 · SQL Validation + Inline Errors

**Goal:** `validate()` catches syntax errors and mutation statements without running the query.

```bash
uv run python - <<'EOF'
from labrat.sql.validator import validate

cases = [
    ("SELECT order_id, status FROM orders WHERE status = 'pending'", "duckdb"),
    ("SELEKT id FROM orders",                                        "duckdb"),
    ("DROP TABLE orders",                                            "duckdb"),
]
for sql, dialect in cases:
    errs = validate(sql, dialect=dialect)
    print(f"{'OK' if not errs else 'ERR':3s}  {sql[:55]}")
    for e in errs:
        print(f"       line {e.line} col {e.col}: {e.message}")
EOF
```

**Check:**
- [ ] Valid SELECT → no errors
- [ ] `SELEKT` → at least 1 error mentioning line/col
- [ ] `DROP TABLE` → error flagging it as a mutation/DDL statement

---

## M10 · Results Table

**Goal:** `ResultsTable` widget renders a DataFrame with column headers and row count.

In the running TUI, press `Ctrl+2` to focus the editor. Paste this query and press `F5`
(or `Enter` if that's the run binding — check the footer for the key):

```sql
SELECT c.name, COUNT(o.order_id) AS orders, SUM(o.total_amount) AS revenue
FROM customers c
JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.name
ORDER BY revenue DESC;
```

**Check:**
- [ ] Results pane fills with a table showing name / orders / revenue columns
- [ ] Row count shown in the pane header or footer
- [ ] Arrow keys scroll the table when it has many rows
- [ ] Column widths auto-size to fit content

---

## M11 · Tool Registry + Pydantic Tool Models

**Goal:** All tools register and expose valid Anthropic-compatible JSON schemas.

```bash
uv run python - <<'EOF'
from labrat.agent.tools.base import ToolRegistry
from labrat.agent.tools.list_tables import ListTablesTool
from labrat.agent.tools.describe_table import DescribeTableTool
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.agent.tools.draft_sql import DraftSqlTool
from labrat.agent.tools.create_chart import CreateChartTool
from labrat.agent.tools.sample_rows import SampleRowsTool
from labrat.agent.tools.column_stats import ColumnStatsTool
from labrat.agent.tools.explain_sql import ExplainSqlTool
from labrat.agent.tools.search_columns import SearchColumnsTool

registry = ToolRegistry()
for t in [ListTablesTool(), DescribeTableTool(), RunSqlTool(), DraftSqlTool(),
          CreateChartTool(), SampleRowsTool(), ColumnStatsTool(), ExplainSqlTool(),
          SearchColumnsTool()]:
    registry.register(t)

schemas = registry.to_anthropic_schemas()
for s in schemas:
    req = s["input_schema"].get("required", [])
    print(f"  {s['name']:28s}  required={req}")
EOF
```

**Check:**
- [ ] 9 tools printed, each with a non-empty `name` and `description`
- [ ] `run_sql` has `required=["query"]`
- [ ] `create_chart` has `required=["query", "chart_type", "x", "y"]`
- [ ] No duplicate tool names

---

## M12 · Schema Exploration Tools

**Goal:** `list_tables`, `describe_table`, `column_stats` return live data from the fixture DB.

```bash
uv run python - <<'EOF'
import asyncio
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.tools.list_tables import ListTablesTool
from labrat.agent.tools.describe_table import DescribeTableTool
from labrat.agent.tools.column_stats import ColumnStatsTool
from labrat.db.duckdb_engine import DuckDBConnection
from pathlib import Path

async def main():
    conn = DuckDBConnection(Path("tests/fixtures/sample_dbs/ecommerce.duckdb"), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    ctx = ToolContext(connection=conn, catalog=catalog, profile_name="ecommerce")
    r = ToolRegistry()
    r.register(ListTablesTool()); r.register(DescribeTableTool()); r.register(ColumnStatsTool())

    tables = await r.dispatch("list_tables", {}, ctx)
    print("Tables:", [t.name for t in tables.value.tables])

    desc = await r.dispatch("describe_table", {"table": "orders"}, ctx)
    print("orders cols:", [c.name for c in desc.value.columns])

    stats = await r.dispatch("column_stats", {"table": "orders", "column": "total_amount"}, ctx)
    print("total_amount stats:", stats.value)
    conn.disconnect()

asyncio.run(main())
EOF
```

**Check:**
- [ ] `list_tables` returns all 4 tables
- [ ] `describe_table` returns all 7 columns of `orders`
- [ ] `column_stats` returns a dict with `min`, `max`, `avg` (or similar numeric keys)

---

## M13 · SQL Execution Tools with Safety Gates

**Goal:** `run_sql` refuses mutations; applies auto-LIMIT; logs to query history.

```bash
uv run python - <<'EOF'
import asyncio
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.db.duckdb_engine import DuckDBConnection
from pathlib import Path

async def main():
    conn = DuckDBConnection(Path("tests/fixtures/sample_dbs/ecommerce.duckdb"), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    ctx = ToolContext(connection=conn, catalog=catalog, profile_name="ecommerce")
    r = ToolRegistry()
    r.register(RunSqlTool())

    # Should be refused
    mut = await r.dispatch("run_sql", {"query": "DROP TABLE orders", "force": False}, ctx)
    print(f"refused={mut.value.refused}  ok={mut.value.ok}")

    # Should succeed with auto-limit
    ok = await r.dispatch("run_sql", {"query": "SELECT * FROM orders"}, ctx)
    print(f"ok={ok.value.ok}  row_count={ok.value.row_count}  (auto-limited to ≤1000)")
    conn.disconnect()

asyncio.run(main())
EOF
```

**Check:**
- [ ] `refused=True  ok=False` for the DROP statement
- [ ] `ok=True  row_count=8` for the SELECT (all 8 rows; under 1000 limit)
- [ ] Check `~/.local/share/labrat/history/ecommerce.jsonl` was written

---

## M14 · Agent Loop Foundation

**Goal:** `AgentLoop.run()` executes a ReAct round-trip and calls tool callbacks.

```bash
uv run python - <<'EOF'
import asyncio
from collections.abc import AsyncIterator
from labrat.agent.loop import AgentLoop, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.tools.list_tables import ListTablesTool
from labrat.db.duckdb_engine import DuckDBConnection
from pathlib import Path

class _Scripted(ModelProvider):
    _turns = [
        [ToolUseBlock(id="t1", name="list_tables", input={})],
        [TextBlock(text="Found 4 tables: customers, events, orders, products.")],
    ]
    _idx = 0
    async def stream(self, messages, tools, system) -> AsyncIterator:
        blocks = self._turns[min(self._idx, len(self._turns)-1)]
        self._idx += 1
        async def _emit():
            for b in blocks: yield b
        return _emit()

async def main():
    conn = DuckDBConnection(Path("tests/fixtures/sample_dbs/ecommerce.duckdb"), read_only=True)
    conn.connect()
    ctx = ToolContext(connection=conn, catalog=conn.introspect_catalog(), profile_name="ecommerce")
    reg = ToolRegistry()
    reg.register(ListTablesTool())
    loop = AgentLoop(provider=_Scripted(), registry=reg, ctx=ctx, dialect="duckdb")
    chunks = []
    await loop.run("What tables are there?", on_text=lambda t: chunks.append(t))
    print("Agent reply:", "".join(chunks))
    print("History turns:", len(loop.history))
    conn.disconnect()

asyncio.run(main())
EOF
```

**Check:**
- [ ] Agent reply contains the expected text about 4 tables
- [ ] `History turns` is > 1 (tool call + result + final answer)

---

## M14.5 · Model Provider Abstraction

**Goal:** `AnthropicProvider` is importable and has a `stream()` method signature.

```bash
uv run python - <<'EOF'
from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.providers.base import ModelProvider
import inspect
p = AnthropicProvider()
print(type(p).__mro__)
sig = inspect.signature(p.stream)
print("stream() params:", list(sig.parameters))
EOF
```

**Check:**
- [ ] `AnthropicProvider` is in the MRO chain for `ModelProvider`
- [ ] `stream()` has `messages`, `tools`, `system` parameters

---

## M15 · Dialect-Aware System Prompts

**Goal:** `build_system_prompt()` produces non-empty dialect-specific text.

```bash
uv run python - <<'EOF'
from labrat.agent.prompts import build_system_prompt
for dialect in ["duckdb", "postgres", "snowflake"]:
    p = build_system_prompt(dialect)
    print(f"{dialect:12s}  {len(p):5d} chars  starts: {p[:60]!r}")
EOF
```

**Check:**
- [ ] Each dialect returns a non-empty string (> 200 chars)
- [ ] DuckDB prompt contains DuckDB-specific content (e.g. "duckdb" or "QUALIFY")
- [ ] Prompts differ between dialects

---

## M16 · Chat Panel + Draft SQL Wiring

**Goal:** Typing a question in the chat panel triggers the agent, which drafts SQL into the editor.

> **Requires:** `ANTHROPIC_API_KEY` set in your shell.

In the running TUI (`uv run labrat` with a profile connected):

1. Press `Ctrl+1` to focus the chat input
2. Type: `Show me total revenue by customer`
3. Press `Enter`

**Check:**
- [ ] Chat panel shows a "thinking…" indicator or streaming text
- [ ] Agent's text reply appears in the chat bubble
- [ ] SQL is automatically drafted into the editor pane (top-center)
- [ ] The drafted SQL contains a `JOIN` between `customers` and `orders`
- [ ] No Python errors appear; the UI remains responsive

---

## M17 · Thread + Version Model

**Goal:** `ThreadManager` creates threads, appends versions, and retrieves them.

```bash
uv run python - <<'EOF'
import tempfile, asyncio
from pathlib import Path
from labrat.thread.manager import ThreadManager

with tempfile.TemporaryDirectory() as tmp:
    mgr = ThreadManager(store_dir=Path(tmp))
    t = mgr.create_thread(name="revenue-analysis", profile_name="ecommerce")
    v1 = mgr.append_version(t.id, sql="SELECT COUNT(*) FROM orders", chat_history=[])
    v2 = mgr.append_version(t.id, sql="SELECT customer_id, SUM(total_amount) FROM orders GROUP BY 1", chat_history=[])
    threads = mgr.list_threads()
    versions = mgr.get_versions(t.id)
    loaded = mgr.get_thread(t.id)

    print(f"Threads:  {[th.name for th in threads]}")
    print(f"Versions: {len(versions)}")
    print(f"v1 SQL:   {versions[0].sql}")
    print(f"Loaded:   {loaded.name}")
EOF
```

**Check:**
- [ ] 1 thread returned, named `revenue-analysis`
- [ ] 2 versions in order (COUNT first, then GROUP BY)
- [ ] `loaded.name` matches the created thread

---

## M18 · Findings (Pin & Curate)

**Goal:** `Finding` objects can be created and serialised.

```bash
uv run python - <<'EOF'
from datetime import datetime, UTC
from labrat.thread.model import Finding
f = Finding(
    id="f1", version_id="v1",
    question="Who are the top customers?",
    sql="SELECT name, SUM(total_amount) FROM customers JOIN orders USING (customer_id) GROUP BY 1",
    results_ref=None, chart_spec=None,
    note="Alice leads with $1,234.",
    pinned_at=datetime.now(tz=UTC),
)
print(f.model_dump_json(indent=2))
EOF
```

**Check:**
- [ ] JSON output contains all fields (id, question, sql, note, pinned_at)
- [ ] No validation errors

---

## M19 · Audit Log / Event Sourcing

**Goal:** `AuditLog` writes events to JSONL and reads them back.

```bash
uv run python - <<'EOF'
import tempfile
from pathlib import Path
from labrat.audit.log import AuditLog
from labrat.audit.events import SqlExecuted, AgentMessage, ToolCall

with tempfile.TemporaryDirectory() as tmp:
    alog = AuditLog(sessions_dir=Path(tmp), session_id="test-session")
    alog.log(SqlExecuted(session_id="test-session", sql="SELECT COUNT(*) FROM orders",
                         success=True, row_count=8, results_ref=None))
    alog.log(AgentMessage(session_id="test-session", text="There are 8 orders."))
    alog.log(ToolCall(session_id="test-session", tool_name="list_tables", args={}))
    events = alog.read_session("test-session")
    for ev in events:
        print(ev.model_dump_json())
EOF
```

**Check:**
- [ ] 3 events printed in order: sql_executed, agent_message, tool_call
- [ ] Each line is valid JSON with a `session_id` and `timestamp` field
- [ ] `event_type` differs per event

---

## M20 · HTML Export — retired, superseded by Cheese v1

`labrat.audit.export.export_findings()` has been removed. Its one-shot, unversioned HTML
export is superseded by the Cheese v1 share surface (`labrat.cheese.export.export_cheese`) —
versioned, provenance-stamped artifacts. See "Cheese share (v1)" below.

---

## M21 · Chart Spec + Unicode Rendering

**Goal:** Agent can request a bar chart; it renders in the results pane instead of the table.

> **Requires:** `ANTHROPIC_API_KEY` set in your shell.

In the running TUI:

1. Press `Ctrl+1` to focus chat
2. Type: `Show me a bar chart of revenue by customer`
3. Press `Enter`

**Check:**
- [ ] The results pane switches from a DataTable to a unicode bar chart
- [ ] Chart has labeled X axis (customer names) and Y axis (revenue amounts)
- [ ] Title appears at the top of the chart area
- [ ] Running another SQL query switches the pane back to the table

You can also test rendering directly:

```bash
uv run python - <<'EOF'
from labrat.chart.spec import ChartSpec, ChartType
from labrat.chart.render_unicode import render_unicode
from labrat.db.duckdb_engine import DuckDBConnection
from pathlib import Path

conn = DuckDBConnection(Path("tests/fixtures/sample_dbs/ecommerce.duckdb"), read_only=True)
conn.connect()
df = conn.execute("SELECT c.name, SUM(o.total_amount) AS revenue FROM customers c JOIN orders o ON o.customer_id = c.customer_id GROUP BY c.name ORDER BY revenue DESC")
conn.disconnect()
spec = ChartSpec(chart_type=ChartType.bar, x="name", y="revenue", title="Revenue by Customer")
print(render_unicode(spec, df, width=80, height=20))
EOF
```

**Check:**
- [ ] Unicode bar chart printed to terminal
- [ ] Bars proportional to revenue values
- [ ] Customer names appear on X axis

---

## M22 · Image Protocol Detection

**Goal:** Protocol detection doesn't crash; unicode fallback always works.

```bash
uv run python - <<'EOF'
from labrat.chart.spec import ChartSpec, ChartType
from labrat.chart.render_unicode import render_unicode
import polars as pl

df = pl.DataFrame({"x": ["A", "B", "C"], "y": [10, 25, 15]})
spec = ChartSpec(chart_type=ChartType.line, x="x", y="y", title="Test Line")
output = render_unicode(spec, df, width=60, height=12)
print(output)
print("\nLength:", len(output), "chars")
EOF
```

**Check:**
- [ ] Chart output is non-empty (> 50 chars)
- [ ] Contains ANSI escape codes or unicode block chars
- [ ] No ImportError or protocol-detection crash

---

## M23 · PostgreSQL Adapter

**Goal:** `PostgresConnection` is importable and conforms to the `Connection` ABC.

```bash
uv run python - <<'EOF'
from labrat.db.postgres import PostgresConnection
from labrat.db.base import Connection
import inspect
print("Is subclass of Connection:", issubclass(PostgresConnection, Connection))
methods = [m for m in dir(PostgresConnection) if not m.startswith("_")]
print("Public methods:", methods)
EOF
```

**Check:**
- [ ] Imports without error
- [ ] `issubclass` returns `True`
- [ ] Methods include `connect`, `disconnect`, `execute`, `introspect_catalog`

> Live connection test requires a running Postgres instance — skip if unavailable.

---

## M24 · Multi-Connection / Profile Manager

**Goal:** `ProfileManager` supports add / list / get / remove.

```bash
uv run python - <<'EOF'
from labrat.profile.manager import ProfileManager
from labrat.profile.model import Profile

mgr = ProfileManager()
existing = mgr.list_all()
print(f"Existing profiles: {[p.name for p in existing]}")

# If 'ecommerce' profile exists from M3 steps:
if existing:
    p = mgr.get(existing[0].name)
    print(f"Got: {p.name} ({p.dialect}), read_only={p.is_read_only}")
EOF
```

**Check:**
- [ ] Lists all previously added profiles without error
- [ ] `get()` returns a `Profile` with correct fields
- [ ] Adding a duplicate name raises a descriptive error (not a crash)

---

## M25 · Remaining Warehouse Adapters

**Goal:** All 5 adapter classes are importable and subclass `Connection`.

```bash
uv run python - <<'EOF'
from labrat.db.base import Connection
adapters = [
    ("snowflake", "SnowflakeConnection"),
    ("bigquery",  "BigQueryConnection"),
    ("redshift",  "RedshiftConnection"),
    ("trino",     "TrinoConnection"),
    ("mysql",     "MySQLConnection"),
]
for module, cls_name in adapters:
    try:
        mod = __import__(f"labrat.db.{module}", fromlist=[cls_name])
        cls = getattr(mod, cls_name)
        ok = issubclass(cls, Connection)
        print(f"  {'✓' if ok else '✗'}  {cls_name}")
    except Exception as e:
        print(f"  ✗  {cls_name}: {e}")
EOF
```

**Check:**
- [ ] All 5 adapters print `✓`
- [ ] Each is confirmed as a subclass of `Connection`

---

## M26 · Benchmark + Eval Framework

**Goal:** `EvalRunner` runs a suite and produces an `EvalReport` with accuracy.

```bash
uv run python - <<'EOF'
import asyncio
from labrat.eval.runner import EvalRunner
from labrat.eval.models import EvalCase, EvalStatus

class Suite:
    suite_name = "manual-test"
    cases = [
        EvalCase(id="q1", question="Count orders", expected_sql="SELECT COUNT(*) FROM orders"),
        EvalCase(id="q2", question="List customers", expected_sql="SELECT * FROM customers"),
    ]

async def agent(question: str) -> str:
    if "Count" in question:
        return "SELECT COUNT(*) FROM orders"
    return "SELECT name FROM customers"  # slightly different

async def main():
    runner = EvalRunner(suite=Suite(), agent_fn=agent)
    report = await runner.run()
    print(f"Suite:    {report.suite_name}")
    print(f"Total:    {report.total}")
    print(f"Accuracy: {report.accuracy:.1%}")
    for r in report.results:
        print(f"  {r.case_id}  {r.status}  {r.latency_seconds:.3f}s")

asyncio.run(main())
EOF
```

**Check:**
- [ ] Suite name and total print correctly
- [ ] At least q1 is `correct` (exact SQL match)
- [ ] Accuracy is a float between 0.0 and 1.0
- [ ] Latency values are non-negative

---

## M27 · Comparison Harness (Baselines)

**Goal:** `ComparisonReport` produces a markdown comparison table.

```bash
uv run python - <<'EOF'
from labrat.eval.baselines.comparison import ComparisonReport
from labrat.eval.models import EvalResult, EvalStatus
from labrat.eval.report import EvalReport

labrat_r = EvalReport("fixture", [
    EvalResult(case_id="q1", status=EvalStatus.correct),
    EvalResult(case_id="q2", status=EvalStatus.correct),
    EvalResult(case_id="q3", status=EvalStatus.wrong),
])
baseline_r = EvalReport("fixture", [
    EvalResult(case_id="q1", status=EvalStatus.correct),
    EvalResult(case_id="q2", status=EvalStatus.wrong),
    EvalResult(case_id="q3", status=EvalStatus.wrong),
])
comp = ComparisonReport(labrat=labrat_r, baselines=[("zero-shot", baseline_r)])
print(comp.to_markdown())
EOF
```

**Check:**
- [ ] Markdown table with columns for each baseline and LabRat
- [ ] LabRat accuracy (67%) higher than baseline (33%)
- [ ] Delta column shows improvement

---

## M28 · Query History (Always-On)

**Goal:** Every SQL execution writes to a per-profile JSONL history file.

Run a query through the TUI (or M13 script), then:

```bash
# View the history file (replace 'ecommerce' with your profile name)
cat ~/.local/share/labrat/history/ecommerce.jsonl | head -5
```

Or verify programmatically:

```bash
uv run python - <<'EOF'
from labrat.history.log import QueryHistoryLog
events = QueryHistoryLog().read_profile("ecommerce")
print(f"{len(events)} events logged")
for e in events[-3:]:
    print(f"  [{e.timestamp.strftime('%H:%M:%S')}] {e.sql_final[:60]}")
EOF
```

**Check:**
- [ ] JSONL file exists at `~/.local/share/labrat/history/ecommerce.jsonl`
- [ ] Events have `sql_final`, `executed`, `success`, `row_count` fields
- [ ] Queries from M13 and M16 tests are present

---

## M29 · Context Engine — Personal Domain Builder

**Goal:** `score_table_relevance` and `ContextAnalyzer` work on real query history.

```bash
uv run python - <<'EOF'
import asyncio
from datetime import datetime, UTC
from labrat.context_engine.relevance import score_table_relevance
from labrat.context_engine.analyzer import ContextAnalyzer
from labrat.history.events import QueryEvent

events = [
    QueryEvent(timestamp=datetime.now(tz=UTC), profile="ecommerce", thread_id="t1",
               version_id="v1", sql_final="SELECT customer_id, SUM(total_amount) FROM orders GROUP BY 1",
               executed=True, success=True, execution_time_ms=12, row_count=6),
    QueryEvent(timestamp=datetime.now(tz=UTC), profile="ecommerce", thread_id="t1",
               version_id="v2", sql_final="SELECT * FROM customers WHERE region = 'west'",
               executed=True, success=True, execution_time_ms=5, row_count=2),
]

scores = score_table_relevance(events)
print("Table relevance scores:")
for tbl, score in sorted(scores.items(), key=lambda x: -x[1]):
    print(f"  {tbl:15s}  {score:.4f}")

async def mock_llm(prompt: str) -> str:
    return "Used for revenue and customer analysis."

async def main():
    analyzer = ContextAnalyzer(llm_fn=mock_llm)
    descs = await analyzer.generate_descriptions(tables=["orders", "customers"], events=events)
    for tbl, desc in descs.items():
        print(f"\n{tbl}: {desc}")

asyncio.run(main())
EOF
```

**Check:**
- [ ] `orders` has a higher relevance score than `customers` (appears more in queries)
- [ ] Descriptions are non-empty strings for each table
- [ ] No async errors

---

## M30 · External Catalog Integration (dbt)

**Goal:** `DbtLoader` reads the fixture dbt project's `manifest.json` and returns models.

```bash
uv run python - <<'EOF'
from labrat.catalog.dbt.loader import DbtLoader
from pathlib import Path

loader = DbtLoader(project_path=Path("tests/fixtures/sample_dbt_project"))
entries = list(loader.load().values())
print(f"Models loaded: {len(entries)}")
for e in entries:
    cols = list(e.columns.keys())
    print(f"  {e.name:25s}  schema={e.schema_name}  cols={cols[:4]}")
EOF
```

**Check:**
- [ ] At least 1 model loaded from the fixture project
- [ ] Each model has a `name`, `schema_name`, and `columns` dict
- [ ] No FileNotFoundError (fixture project has `manifest.json`)

---

## M31 · Self-Healing Memory

**Goal:** `MemoryStore` persists corrections and retrieves them by profile.

```bash
uv run python - <<'EOF'
import tempfile
from pathlib import Path
from labrat.memory.store import MemoryStore
from labrat.memory.model import Memory, MemoryScope, MemoryKind

with tempfile.TemporaryDirectory() as tmp:
    store = MemoryStore(memory_dir=Path(tmp))
    store.append(Memory(
        profile="ecommerce",
        scope=MemoryScope.table_,
        kind=MemoryKind.chat_correction,
        text="Order status values: 'pending', 'completed', 'shipped'. Never use 'done' or 'open'.",
        table_scope="orders",
        application_count=0,
    ))
    store.append(Memory(
        profile="ecommerce",
        scope=MemoryScope.global_,
        kind=MemoryKind.explicit_user_rule,
        text="Always show revenue in dollars, not cents.",
        application_count=2,
    ))
    memories = store.read_profile("ecommerce")
    print(f"Stored: {len(memories)} memories")
    for m in memories:
        print(f"  [{m.scope.value}] {m.text[:60]}")
EOF
```

**Check:**
- [ ] 2 memories returned in order
- [ ] Scope values are `table_` and `global_`
- [ ] Text content is preserved exactly

---

## M32 · Custom Validations

**Goal:** `ValidationChecker` evaluates SQL against natural-language rules via an LLM.

```bash
uv run python - <<'EOF'
import asyncio
from labrat.validations.checker import ValidationChecker
from labrat.validations.model import ValidationRule, ValidationSeverity

rules = [
    ValidationRule(
        profile="ecommerce",
        natural_language_rule="Warn when SELECT * is used — prefer explicit column names.",
        severity=ValidationSeverity.warn,
    ),
]

async def mock_llm(prompt: str) -> str:
    if "SELECT *" in prompt:
        return "warn: SELECT * returns all columns; prefer explicit column names."
    return "pass"

async def main():
    checker = ValidationChecker(llm_fn=mock_llm)

    results_star = await checker.check("SELECT * FROM orders", "8 rows", rules)
    results_ok   = await checker.check("SELECT order_id, status FROM orders LIMIT 20", "20 rows", rules)

    print("SELECT *  →", [(r.status.value, r.explanation) for r in results_star])
    print("Explicit  →", [(r.status.value,) for r in results_ok])

asyncio.run(main())
EOF
```

**Check:**
- [ ] `SELECT *` produces a result with `status="warn"` and non-empty explanation
- [ ] Explicit column query produces `status="pass"`
- [ ] No unhandled exceptions

---

## Keyboard Reference (TUI)

| Key | Action |
|-----|--------|
| `Ctrl+1` | Focus chat input |
| `Ctrl+2` | Focus SQL editor |
| `Ctrl+3` | Focus results table |
| `Ctrl+4` | Focus schema tree |
| `Ctrl+H` | Toggle schema pane |
| `Ctrl+L` | Toggle chat pane |
| `q` | Quit |

---

## Quick Smoke Test (all milestones in ~2 minutes)

```bash
# 1. Verify imports
uv run python -c "import labrat; print('import OK')"

# 2. Verify DB connection
uv run python -c "
from labrat.db.duckdb_engine import DuckDBConnection
from pathlib import Path
c = DuckDBConnection(Path('tests/fixtures/sample_dbs/ecommerce.duckdb'), read_only=True)
c.connect(); print(c.execute('SELECT COUNT(*) FROM orders').row(0)); c.disconnect()
"

# 3. Verify CLI
uv run labrat --help
uv run labrat conn list

# 4. Launch TUI (exit with q)
uv run labrat
```

---

## M1 — chat through the real agent stack (manual gate)

Setup: `uv run labrat` against a profile pointing at `tests/fixtures/sample_dbs/ecommerce.duckdb`
(or any DuckDB file; create the profile via onboarding). With no ANTHROPIC_API_KEY exported,
expect a one-time "degraded" warning toast (claude CLI fallback).

1. Ask in chat: "profile this dataset" → expect a `profile_dataset` trace line (`▸ profile_dataset(...) ✓`)
   and a structured summary. This tool did not exist in the TUI before M1.
2. Ask: "which tables are relevant to revenue by product category?" → expect `link_schema` and/or
   `search_columns` traces.
3. Ask: "run a query counting orders per status and chart it" → expect `run_sql` trace, results table
   populating, then `create_chart` rendering in the results pane.
4. Ask: "draft (don't run) a query for top customers by spend" → expect `draft_sql` trace and SQL
   appearing in the editor, NOT executed.
5. With a read-only profile, ask the agent to `CREATE TABLE tmp1 AS SELECT 1` → expect the tool
   result to show "blocked: read-only Analyst mode" and the agent to relay the refusal.
6. `ctrl+,` → toggle "Verify answers" on, Save → restart → ask a question → expect a dim
   `verifier: …` status line only if the first answer was judged insufficient (usually none).
7. `ctrl+\` toggles tool-trace lines off/on including the new status lines.

Note: to exercise `run_program`/`llm_extract` the profile must be read-write; on a default
read-only profile these self-report "blocked: read-only Analyst mode" (expected).

---

## M2 — first-connect Cartographer (manual gate)

1. Delete `~/.labrat/maze/<profile>/scent` if present. Launch `uv run labrat` → expect
   "🗺 mapping schema…" then "scent ready · N docs" toasts; verify `~/.labrat/maze/<profile>/scent/*.md`
   plus `.schema_fingerprint` exist.
2. Relaunch → no "scent ready" toast (idempotent reuse, no stale warning).
3. Ask in chat: "any reference notes on the orders table?" → expect a `search_reference_docs`
   trace and content drawn from the generated docs.
4. Add a column to the DB (or edit `.schema_fingerprint` to garbage), relaunch → expect the
   "schema changed … Ctrl+Shift+M" warning. Press Ctrl+Shift+M → confirm → docs regenerate.
   If Ctrl+Shift+M does not register in your terminal, note it — rebind to F6 per the plan.
5. Create `./labrat_maze/scent/manual-note.md` (any heading/body), run refresh → the project-scope
   file must be untouched.

---

## M3 — harvest surface (manual gate)

Setup: profile with `harvest_opt_in` ON (Ctrl+, → toggle → Save). Chat needs a working provider.

1. Ask the agent a question that yields SQL; then reply "no — exclude test orders". Ask another;
   edit the drafted SQL in the editor before running it. → two capture events (no LLM calls yet;
   verify no latency).
2. Ctrl+Shift+H → "Harvesting…" toast → review modal lists drafted Gotchas rows with domains
   (table name or `general`). Toggle one row to skip (space). Apply → success toast; verify
   `./labrat_maze/scent/<domain>.md` gained a `**Source:** harvested` section; skipped row absent.
3. Re-run Ctrl+Shift+H → re-drafts appear; Apply again → doc unchanged (body dedup, idempotent).
4. With harvest_opt_in OFF: Ctrl+Shift+H → "Harvesting is off" warning, no modal, no LLM call.
5. Switch threads (Ctrl+T) with captured corrections pending → confirm prompt appears; Cancel
   proceeds with the switch, nothing harvested.
6. Ask in chat about the harvested topic → `search_reference_docs` should retrieve the new section.

---

## M4 — verification toggle + provenance footer (manual gate)

1. Ctrl+, → "Verify answers" ON → Save → restart. Ask a question the agent will answer thinly
   ("how many rows?" with no table named) → occasionally a dim `verifier: insufficient — …`
   status line appears and the agent continues; final answer then carries `verifier ✓ (1 round)`
   in its footer. With a good first answer the footer shows `verifier ✓`.
2. Ask "any reference notes on orders? then count the orders" → footer like
   `⚑ grounded: scent: orders (verified) · 1 query`.
3. Corrupt `.schema_fingerprint` (see M2 gate) and relaunch → the boot toast warns about the
   schema change (M2), but the footer still shows `scent: orders (verified)` with no freshness
   word — per-section freshness needs `schema_hash` meta, which no writer stamps yet (the
   global stale flag is fallback-only and unreachable on the normal enriched path).
4. A pure-prose turn (e.g. "thanks") → no footer line at all.
5. Verify OFF (default): no verifier segment ever appears in footers.
6. Harvest a correction into `orders` (M3 gate steps 1–2), then re-ask about orders →
   the same domain's footer tier stays the doc's best tier (verified, from the
   Cartographer sections) and the merged answer includes the harvested gotcha even
   immediately after a Ctrl+Shift+M scent refresh (the I2 shadow is gone).

## T1b — dbt semantic ingestion (manual gate)

Setup: profile with `dbt_project_path` set (Ctrl+, → "dbt project"), pointing at a dbt project
whose `target/manifest.json` contains `semantic_models` (run `dbt parse` first).

1. Connect → "semantic layer ingested · N sections across M domains" toast; verify
   `./labrat_maze/scent/<table>.md` files carry `**Source:** semantic_layer` sections with
   `**Meta:** schema_hash=…`.
2. Reconnect → silent (fingerprint unchanged).
3. Edit a metric description in the dbt project, `dbt parse`, reconnect → drift warning toast;
   press F9 → confirm → sections replaced (old description gone), harvested/human sections in
   the same docs untouched.
4. Ask about a semantic-model table in chat → footer shows `scent: <table> (semantic_layer·fresh)`.
5. No dbt path configured → no toasts, no `metrics.md`, nothing ingested.

## dispatch_subagent (manual spot-check)

1. In the TUI (any connected profile), ask: "delegate a sub-task to a sub-agent: count the
   orders and report just the number". → expect a `▸ dispatch_subagent({...}) ✓` trace, the
   final answer citing the sub-agent's result, and NO sub-agent tool chatter in the parent
   transcript (the sub-loop's own run_sql traces do not appear — only the one dispatch line).
2. Budget echo: the answer/trace completes within the default budgets (no hang).

## Cheese share (v1)

**Goal:** any pinned answer exports as a free, versioned, self-contained HTML artifact — no
LabRat, no network, needed to view it.

1. Run a query in chat (ask a question that produces rows and, ideally, a chart).
2. Press **f8** ("Share") → toast `🧀 Cheese exported: <path>`. Open that path in a browser:
   - chart image (if one was drawn this turn) and a bounded results table render
   - a trust block: either attested (Scent sources / join-verified / lineage / verifier
     verdict / schema+git+model stamp) or the honest line
     `unattested (pinned before provenance capture)` — never fabricated
   - footer `Made with LabRat — terminal-native data agent` linking the GitHub repo, and no
     other external URLs anywhere in the page source
   - the page renders fully offline (airplane mode / disconnect works)
3. Pin a couple of answers from the results table (pin icon / existing M18 flow), then
   **Ctrl+K** to open the Findings viewer:
   - **e** → exports all pinned findings as one report (`LabRat Report`) → toast + artifact
   - select a row, **x** → exports just that finding (single) → a second, separate artifact
   - **Shift+E** / **Shift+X** → same two exports with rows omitted (open the HTML — the table
     rows are gone, replaced by `Result rows omitted at export.`)
4. **v** → opens the version browser: lists every Cheese with its versions
   (`v{n} · <date> · rows:<mode>`, current version marked `← current`).
   - Press **Enter** on an older version → toast with that version's file path; open it — it's
     the exact old artifact, unchanged.
   - Press **r** on an older version → rollback toast; the list's `← current` marker moves.
5. Re-run the same export (**e** again on the same pinned set) → a new `v(N+1).html` appears
   next to the untouched older versions (immutable — `v1.html`'s bytes never change), and the
   version browser's `current` marker moves to the newest version.

**Check:**
- [ ] f8 export opens standalone in a browser with zero network requests
- [ ] Trust block never shows fabricated grounding data for a pre-Cheese finding
- [ ] Report vs. single-finding exports land in separate artifacts; rows-omitted variants never
      leak row values
- [ ] Rollback moves the `current` pointer without deleting or rewriting any version file
- [ ] Re-export continues the version sequence (never overwrites an existing `v<N>.html`)

## Trail (v1) — save-as-Trail (manual gate)

**Goal:** promote a completed, pinned analysis into a named, intent-retrieved Trail —
read-as-guidance, never auto-executed — that a later `search_trails` call can surface.

Setup: `Ctrl+,` → toggle "Trails (save-as-Trail)" ON → Save.

1. Run a query in chat that produces a real answer (ideally a note added via the results
   pane), then pin it (existing M18 pin flow) and open the Findings viewer (**Ctrl+K**).
2. Highlight the pinned finding, press **t** (or click "Save as Trail") → the review screen
   opens showing the 5 drafted sections: When to use / Steps / Reference SQL / Validations /
   Gotchas. Edit the **Steps** body to spell out the ordered steps a rat should follow.
3. Press **a** (Approve & save) → toast `🥾 Trail saved: <slug>`; the review screen closes back
   to the Findings viewer. Verify `./labrat_maze/trail/<slug>.md` exists on disk with
   `kind: trail` frontmatter and your edited Steps body.
4. Start a fresh session (or restart `uv run labrat`) and ask a matching-intent question in
   chat → expect a `search_trails` trace in the tool log and the retrieved Trail's guidance
   reflected in the answer (not silently ignored).
5. Gate check: `Ctrl+,` → toggle Trails OFF → Save. Back in the Findings viewer, press **t** on
   a pinned finding → expect the toast "Enable Trails in Settings (ctrl+,) to save." and
   confirm no new file appears under `labrat_maze/trail/`.

**Check:**
- [ ] Trails OFF (default) → `t` writes nothing, only notifies
- [ ] Trails ON → `t` → review screen → edited Steps land verbatim in the saved `.md`
- [ ] Saved Trail file has `kind: trail` frontmatter and all 5 sections
- [ ] A fresh session's matching-intent question triggers `search_trails` and surfaces the
      saved Trail's content
