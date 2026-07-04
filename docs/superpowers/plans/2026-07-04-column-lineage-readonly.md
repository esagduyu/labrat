# Column-Level Lineage + Read-Only Analyst Mode — Implementation Plan

> **For agentic workers: REQUIRED SUB-SKILL: superpowers:subagent-driven-development**

**Goal:** Ship engine-enforced read-only "Analyst" mode plus deterministic column-level lineage (an `explain_lineage` tool and a Cartographer view-lineage Scent section), all parse-only via sqlglot against the live `Catalog`.

**Architecture:** Unit A adds a `read_only` flag to `ToolContext` and a single `is_mutating` gate inside `ToolRegistry.dispatch` (the sole enforcement point; `run_sql` classifies its SQL via an override). Units B–D form a pipeline: DuckDB introspection captures view definitions into `Table.view_definition` (B), `sqlglot.lineage` traces query/view columns to base-table sources (C), and the Cartographer emits a `lineage`-tagged Scent section per view (D), audited fail-loud.

**Tech Stack:** Python 3.12 / pydantic v2 / sqlglot 30.8.0 (`sqlglot.lineage`, `sqlglot.optimizer.qualify`) / DuckDB (`duckdb_views()`, `information_schema`) / pytest (`asyncio_mode="auto"`) / ruff + pyright strict.

**Spec (source of truth):** `docs/superpowers/specs/2026-07-04-column-lineage-readonly-design.md`
**Branch:** `feat/column-lineage` (already checked out)

## Global Constraints

Copied verbatim from the spec's Non-negotiables — every task must hold all four:

- Lineage is **deterministic / no-LLM / no execution** (parse-only, like `check_sql`).
- Read-only enforcement lives **at the registry/dispatch layer**, never in the prompt.
- GT-firewall preserved: the Cartographer view-lineage builder reads view **SQL metadata** only (never data, never answer-key files); every frozen doc still passes `audit_scent_doc` (fail-loud).
- Additive + backward-compatible: `Table.view_definition` defaults `None`; adapters without view introspection surface no views; a DB with no views yields byte-identical deterministic Scent.

Additional repo invariants: pyright strict on all of `src/labrat/` touched here; tool `name`/`description`/`input_model` are `@property` methods; gates before every commit in this order — `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. `tests/tui/test_app_renders.py` is an env-sensitive pre-existing snapshot test — unrelated, do not touch; if it is the only failure, it does not block a commit.

**Convention for every "append to test file" step:** any `import` / `from` lines shown at the start of an appended block belong in the file's single top-of-file import block (a literal mid-file paste trips ruff E402); the test functions themselves are appended at the end. Tests use `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` decorator, ever.

## Verified mechanisms (live experiments run 2026-07-04, sqlglot 30.8.0, DuckDB in-repo)

These were confirmed by throwaway `uv run python` experiments against the real libraries. The code in the tasks below bakes these in — do not re-derive them.

**sqlglot.lineage Node flattening (Unit C/D):**
- `lineage(column, sql, schema={table: {col: dtype}})` returns a `Node`. A **leaf** is a node with `node.downstream == []`. A leaf maps to a base-table column **iff** `isinstance(leaf.source, exp.Table)`; then the real table name is `leaf.source.name` (correct even when the query used an alias) and the column is `exp.to_column(leaf.name).name` (`leaf.name` is `'<alias-or-table>.<column>'`, possibly quoted, e.g. `'c."name"'` — `exp.to_column(...).name` strips the quotes). A literal-only projection (`SELECT 1 AS k`) yields a leaf whose `.source` is a `Select`, not a `Table` → it contributes no sources.
- Lineage resolves **through CTEs** to base tables (verified: CTE-aggregated column traced to `orders.amount`).
- The **derivation** string is the root node's deriving expression: `node.expression.sql()` (e.g. `'SUM(o.amount) AS total_spend'`).
- **Output-column enumeration** when `column` is omitted: `sqlglot.parse_one(sql)` → the node is an `exp.Query` (`Select` and `Union` both are; `Insert` is not) → `tree.selects` with `.alias_or_name` per projection. `SELECT *` yields a single `'*'` projection; `qualify(tree.copy(), schema=schema)` expands `*`/`o.*` into real column names using the schema (verified). A star over a table missing from the schema stays `'*'` → skipped.
- **Errors:** bad SQL raises `ParseError`; an unknown column raises `SqlglotError("Cannot find column ...")`. Both subclass `sqlglot.errors.SqlglotError` — one catch handles both (fail-soft → structured `parse_error`).

**DuckDB view introspection (Unit B):**
- `SELECT view_name, sql FROM duckdb_views() WHERE NOT internal AND schema_name = ?` returns exactly the user-created views with their full definition (verified; parameterized `?` works). Do **not** use `information_schema.views` unfiltered — it includes ~50 internal temp views (`sqlite_master`, `pg_catalog.*`, …), some under `table_schema='main'`.
- The stored definition is the **full statement**: `CREATE VIEW v AS SELECT ...;` (identifiers may come back quoted, e.g. `c."name"`). Store it as-is in `view_definition` (raw metadata); consumers extract the SELECT.
- `sqlglot.parse_one('CREATE VIEW ... AS SELECT ...')` → `exp.Create`; the SELECT is `tree.expression` (verified, lineage over it traces `customer_spend.customer_name ← customers.name`).
- View **columns** (names + data types) are readable via the exact `information_schema.columns` query `_introspect_columns` already uses for base tables (verified: `('customer_name','VARCHAR'), ('total','DOUBLE')`).
- Base tables come from `information_schema.tables ... table_type = 'BASE TABLE'` — that branch is untouched.

**Read-only SQL classification (Unit A):**
- Statement-type parse results (duckdb dialect, verified): `Insert / Update / Delete / Drop / Create / Alter / TruncateTable / Merge / Grant / Attach / Detach / Copy / Set` all exist as `exp` classes and parse as such. `WITH ... SELECT` parses as `Select`; `EXPLAIN SELECT 1` falls back to `exp.Command` with `root.this == 'EXPLAIN'`; `DESCRIBE`/`SHOW`/`PRAGMA` parse as `exp.Describe`/`exp.Show`/`exp.Pragma`. `EXPORT DATABASE 'd'` raises `ParseError` (correctly fail-closed below).
- Therefore the read-only classifier is a fail-closed **safelist** (`Select/Union/Intersect/Except/Describe/Show/Pragma` + `Command('EXPLAIN')`); everything else — including every write type above, parse failures, and unknown `Command`s — is treated as mutating. The tests enumerate the concrete write types so the blocklist behavior is pinned.

**Known behavior change (intended, spec-compliant):** after Unit B, a DB **with** views gets those views in its `Catalog`, so `profile_dataset` / Scent Quick Reference will list them (they are queryable tables). DAB datasets and the repo fixtures have **no views** → byte-identical there, which is what the constraint promises.

---

## Task 1 — `ToolContext.read_only` + `Tool.is_mutating` + dispatch gate (Unit A core)

**Files:**
- Modify: `src/labrat/agent/tools/base.py`
- Create: `tests/unit/test_read_only_mode.py`

**Interfaces:**
- Consumes: existing `ToolContext.__init__` (line 24), `Tool` ABC (line 65), `ToolRegistry.dispatch` (line 154).
- Produces: `ToolContext(..., read_only: bool = False)` (keyword-only); `Tool.mutating: bool = False` class attr; `Tool.is_mutating(self, args: InputT) -> bool` (default returns `self.mutating`); dispatch gate returning `DispatchResult(ok=False, value=None, error="blocked: read-only Analyst mode")`.

**Step 1.1 — write the failing tests** (create `tests/unit/test_read_only_mode.py`):

```python
"""Read-only Analyst mode (Unit A): ToolContext.read_only + Tool.is_mutating + dispatch gate."""

from __future__ import annotations

from pydantic import BaseModel

from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry


class _NoopInput(BaseModel):
    value: str = "x"


class _ReaderTool(Tool[_NoopInput]):
    @property
    def name(self) -> str:
        return "reader"

    @property
    def description(self) -> str:
        return "A read-only tool."

    @property
    def input_model(self) -> type[_NoopInput]:
        return _NoopInput

    async def execute(self, ctx: ToolContext, args: _NoopInput) -> object:
        return "read-ok"


class _WriterTool(Tool[_NoopInput]):
    mutating = True

    @property
    def name(self) -> str:
        return "writer"

    @property
    def description(self) -> str:
        return "A structurally mutating tool."

    @property
    def input_model(self) -> type[_NoopInput]:
        return _NoopInput

    async def execute(self, ctx: ToolContext, args: _NoopInput) -> object:
        return "wrote"


def test_tool_context_read_only_defaults_false() -> None:
    assert ToolContext().read_only is False


def test_tool_context_read_only_flag_set() -> None:
    assert ToolContext(read_only=True).read_only is True


def test_default_is_mutating_false() -> None:
    assert _ReaderTool().is_mutating(_NoopInput()) is False


def test_class_attr_mutating_true() -> None:
    assert _WriterTool().is_mutating(_NoopInput()) is True


async def test_dispatch_blocks_mutating_tool_when_read_only() -> None:
    reg = ToolRegistry()
    reg.register(_WriterTool())
    res = await reg.dispatch("writer", {}, ToolContext(read_only=True))
    assert res.ok is False
    assert res.value is None
    assert res.error == "blocked: read-only Analyst mode"


async def test_dispatch_allows_reader_tool_when_read_only() -> None:
    reg = ToolRegistry()
    reg.register(_ReaderTool())
    res = await reg.dispatch("reader", {}, ToolContext(read_only=True))
    assert res.ok is True
    assert res.value == "read-ok"


async def test_dispatch_allows_mutating_tool_when_not_read_only() -> None:
    # Regression: read_only defaults False → zero behavior change for all callers.
    reg = ToolRegistry()
    reg.register(_WriterTool())
    res = await reg.dispatch("writer", {}, ToolContext())
    assert res.ok is True
    assert res.value == "wrote"
```

**Step 1.2 — run, expect FAIL** (`read_only` unknown kwarg / attribute):

```bash
uv run pytest tests/unit/test_read_only_mode.py -v
```

**Step 1.3 — minimal implementation.** In `src/labrat/agent/tools/base.py`:

(a) Add the keyword-only parameter to `ToolContext.__init__` (after `profile_name: str = "default",`) and store it (after `self.profile_name = profile_name`):

```python
        profile_name: str = "default",
        read_only: bool = False,
    ) -> None:
```

```python
        self.primary = primary
        self.profile_name = profile_name
        self.read_only = read_only
```

(b) In the `Tool` class body, directly under the docstring (before the `name` property):

```python
    # Class-level default consulted by the read-only Analyst-mode dispatch gate.
    # Structurally-mutating tools set this True; tools whose mutation depends on
    # the arguments (run_sql) override is_mutating() instead.
    mutating: bool = False

    def is_mutating(self, args: InputT) -> bool:
        """True if THIS call would mutate state. Default: the class-level flag."""
        _ = args
        return self.mutating
```

(c) In `ToolRegistry.dispatch`, insert the gate between arg validation and execution (after the `except ValidationError` block, before `try: result = await tool.execute(...)`):

```python
        if ctx.read_only and tool.is_mutating(parsed):  # pyright: ignore[reportArgumentType]
            return DispatchResult(ok=False, value=None, error="blocked: read-only Analyst mode")
```

**Step 1.4 — run, expect PASS:**

```bash
uv run pytest tests/unit/test_read_only_mode.py tests/unit/test_tool_registry.py -v
```

**Step 1.5 — gates + commit:**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/base.py tests/unit/test_read_only_mode.py
git commit -m "feat(tools): read-only Analyst mode — ToolContext.read_only + is_mutating dispatch gate (Unit A core)"
```

---

## Task 2 — flag the three structurally-mutating tools

**Files:**
- Modify: `src/labrat/agent/tools/attach_database.py`, `src/labrat/agent/tools/load_file.py`, `src/labrat/agent/tools/load_mongo_collection.py`
- Modify: `tests/unit/test_read_only_mode.py`

**Interfaces:**
- Consumes: `Tool.mutating` class attr from Task 1; `build_data_tools_registry()` (`src/labrat/agent/data_tools.py:29`).
- Produces: `AttachDatabaseTool.mutating = True`, `LoadFileTool.mutating = True`, `LoadMongoCollectionTool.mutating = True`.

**Step 2.1 — write the failing tests** (append to `tests/unit/test_read_only_mode.py`; the gate fires before `execute`, so no live connection is needed — args only have to pass pydantic validation):

```python
from labrat.agent.data_tools import build_data_tools_registry


async def test_attach_database_blocked_when_read_only() -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "attach_database",
        {"path": "/tmp/x.sqlite", "alias": "ext", "db_type": "sqlite"},
        ToolContext(read_only=True),
    )
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


async def test_load_file_blocked_when_read_only() -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "load_file",
        {"path": "/tmp/x.csv", "table_name": "t"},
        ToolContext(read_only=True),
    )
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


async def test_load_mongo_collection_blocked_when_read_only() -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "load_mongo_collection",
        {"database": "articles_db", "collection": "articles", "target_table": "articles"},
        ToolContext(read_only=True),
    )
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"
```

**Step 2.2 — run, expect FAIL** (tools run/raise instead of being gate-blocked):

```bash
uv run pytest tests/unit/test_read_only_mode.py -v
```

**Step 2.3 — minimal implementation.** In each of the three tool classes, add one line at the top of the class body (immediately under the class docstring). Example for `AttachDatabaseTool` in `attach_database.py`:

```python
class AttachDatabaseTool(Tool[_Input]):
    """ATTACH another database into the primary DuckDB session for cross-DB JOINs."""

    mutating = True
```

Apply identically to `LoadFileTool` (`load_file.py`) and `LoadMongoCollectionTool` (`load_mongo_collection.py`).

**Step 2.4 — run, expect PASS:**

```bash
uv run pytest tests/unit/test_read_only_mode.py tests/unit/test_load_file_tool.py -v
```

**Step 2.5 — gates + commit:**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/attach_database.py src/labrat/agent/tools/load_file.py src/labrat/agent/tools/load_mongo_collection.py tests/unit/test_read_only_mode.py
git commit -m "feat(tools): mark attach_database/load_file/load_mongo_collection mutating (Unit A)"
```

---

## Task 3 — `RunSqlTool.is_mutating`: SQL classification, fail-closed

**Files:**
- Modify: `src/labrat/agent/tools/run_sql.py`
- Modify: `tests/unit/test_read_only_mode.py`

**Interfaces:**
- Consumes: `sqlglot.parse`, `_unwrap_with` (run_sql.py line 26), `exp` types verified above; `Tool.is_mutating` hook from Task 1.
- Produces: `_is_write_for_readonly(sql: str) -> bool` (module-level, fail-closed) and `RunSqlTool.is_mutating(self, args: _Input) -> bool`.

Design notes (from the spec + experiments): `run_sql` cannot be statically `mutating=True` — that would block legitimate SELECTs. The override classifies `args.query`. It is deliberately **stricter** than the existing `_is_mutation` (which is fail-open, returning False on `ParseError`, so the DB rejects malformed SQL itself): under `read_only` we **block** the unparseable rather than run it. The safelist necessarily blocks every enumerated write type — `Insert, Update, Delete, Drop, Create, Alter, TruncateTable, Merge` — plus `Grant, Attach, Detach, Copy, Set` and any unknown statement; the tests pin each one concretely. Note `force=True` does NOT bypass the gate (the gate runs in `dispatch`, before `execute` ever sees `force`). A gate-blocked call is not written to the query-history log (it never reaches the tool) — acceptable: the gate is central and tool-agnostic.

**Step 3.1 — write the failing tests** (append to `tests/unit/test_read_only_mode.py`):

```python
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

import labrat.agent.tools.run_sql as run_sql_mod
from labrat.agent.tools.run_sql import RunSqlTool, _is_write_for_readonly
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.history.log import QueryHistoryLog


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "CREATE TABLE t(a INT)",
        "CREATE OR REPLACE VIEW v AS SELECT 1",
        "ALTER TABLE t ADD COLUMN b INT",
        "TRUNCATE t",
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = 1",
        "GRANT SELECT ON t TO bob",
        "ATTACH 'x.db' AS aux",
        "DETACH aux",
        "COPY t TO 'out.csv'",
        "SET threads = 4",
        "SELECT 1; DROP TABLE t",  # stacked write in position 2
    ],
)
def test_write_statements_classified_mutating(sql: str) -> None:
    assert _is_write_for_readonly(sql) is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT a FROM t WHERE b > 2",
        "WITH q AS (SELECT 1 AS x) SELECT * FROM q",
        "SELECT 1 UNION SELECT 2",
        "EXPLAIN SELECT 1",
        "DESCRIBE t",
        "SHOW TABLES",
        "PRAGMA database_list",
    ],
)
def test_read_statements_classified_safe(sql: str) -> None:
    assert _is_write_for_readonly(sql) is False


@pytest.mark.parametrize("sql", ["SELEC nope FROM", "EXPORT DATABASE 'd'", ""])
def test_unparseable_sql_fail_closed(sql: str) -> None:
    assert _is_write_for_readonly(sql) is True


def test_run_sql_is_mutating_uses_query_classification() -> None:
    tool = RunSqlTool()
    assert tool.is_mutating(tool.input_model(query="SELECT 1")) is False
    assert tool.is_mutating(tool.input_model(query="DROP TABLE t")) is True


@pytest.fixture()
def ro_sql_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ToolContext]:
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    p = str(tmp_path / "ro.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE items(id INTEGER, label VARCHAR)")
    raw.execute("INSERT INTO items VALUES (1, 'a'), (2, 'b')")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    yield ToolContext(connection=conn, read_only=True)
    conn.disconnect()


async def test_run_sql_select_passes_under_read_only(ro_sql_ctx: ToolContext) -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch("run_sql", {"query": "SELECT * FROM items"}, ro_sql_ctx)
    assert res.ok is True
    assert res.value.row_count == 2  # type: ignore[union-attr]


async def test_run_sql_insert_blocked_under_read_only(ro_sql_ctx: ToolContext) -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "run_sql", {"query": "INSERT INTO items VALUES (3, 'c')"}, ro_sql_ctx
    )
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


async def test_run_sql_force_does_not_bypass_read_only(ro_sql_ctx: ToolContext) -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "run_sql", {"query": "DROP TABLE items", "force": True}, ro_sql_ctx
    )
    assert res.ok is False
    assert res.error == "blocked: read-only Analyst mode"


async def test_run_sql_mutation_refusal_unchanged_when_not_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: without read_only, an INSERT is still handled by run_sql's own
    # (pre-existing) mutation refusal — dispatch ok=True, tool output refused=True.
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    p = str(tmp_path / "rw.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE items(id INTEGER)")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        reg = build_data_tools_registry()
        res = await reg.dispatch(
            "run_sql", {"query": "INSERT INTO items VALUES (1)"}, ToolContext(connection=conn)
        )
    finally:
        conn.disconnect()
    assert res.ok is True
    assert res.value.refused is True  # type: ignore[union-attr]
```

Note: merge the import lines above into the file's single top-of-file import block (ruff-format/isort will order them).

**Step 3.2 — run, expect FAIL** (`_is_write_for_readonly` does not exist):

```bash
uv run pytest tests/unit/test_read_only_mode.py -v
```

**Step 3.3 — minimal implementation.** In `src/labrat/agent/tools/run_sql.py`, add below `_SAFE_STATEMENT_TYPES` / `_unwrap_with` / `_statement_count` (module level):

```python
_READONLY_SAFE_TYPES = (
    exp.Select,
    exp.Union,
    exp.Intersect,
    exp.Except,
    exp.Describe,
    exp.Show,
    exp.Pragma,
)


def _is_write_for_readonly(sql: str) -> bool:
    """Classify *sql* for read-only Analyst mode. FAIL-CLOSED, unlike _is_mutation.

    Safelist: SELECT/UNION/INTERSECT/EXCEPT (incl. WITH-wrapped), DESCRIBE, SHOW,
    PRAGMA, and EXPLAIN (which sqlglot parses as a generic Command whose keyword is
    'EXPLAIN'). Everything else is treated as a write — this blocks
    INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE/MERGE/GRANT/ATTACH/DETACH/COPY/SET,
    unknown statements, and unparseable SQL (block rather than run under read_only).
    """
    try:
        statements = sqlglot.parse(sql.strip())
    except ParseError:
        return True
    if not statements:
        return True
    for stmt in statements:
        if stmt is None:
            return True
        root = _unwrap_with(stmt)
        if isinstance(root, _READONLY_SAFE_TYPES):
            continue
        if isinstance(root, exp.Command) and str(root.this).upper() == "EXPLAIN":
            continue
        return True
    return False
```

And in the `RunSqlTool` class (after the `input_model` property, before `execute`):

```python
    def is_mutating(self, args: _Input) -> bool:
        """Read-only-mode classification: a SELECT still runs; a write is blocked.

        Reuses the same sqlglot parse family as the statement-stacking guard, but
        fail-closed (see _is_write_for_readonly). force=True cannot bypass this —
        the dispatch gate runs before execute() ever sees the args.
        """
        return _is_write_for_readonly(args.query)
```

**Step 3.4 — run, expect PASS** (including the pre-existing run_sql suites):

```bash
uv run pytest tests/unit/test_read_only_mode.py tests/unit/test_sql_execution_tools.py tests/unit/test_run_sql_repair.py tests/unit/test_run_sql_warnings.py -v
```

**Step 3.5 — gates + commit:**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/run_sql.py tests/unit/test_read_only_mode.py
git commit -m "feat(tools): run_sql read-only SQL classification, fail-closed (Unit A complete)"
```

---

## Task 4 — `Table.view_definition` catalog field (Unit B, model)

**Files:**
- Modify: `src/labrat/db/catalog.py`
- Create: `tests/unit/test_view_introspection.py`

**Interfaces:**
- Consumes: `Table` (frozen pydantic model, `src/labrat/db/catalog.py:28`).
- Produces: `Table.view_definition: str | None = None` (None = base table; a view is a `Table` with columns + a definition).

**Step 4.1 — write the failing tests** (create `tests/unit/test_view_introspection.py`):

```python
"""Unit B: Table.view_definition + DuckDB view introspection."""

from __future__ import annotations

from pathlib import Path

import duckdb

from labrat.db.catalog import Table
from labrat.db.duckdb_engine import DuckDBConnection


def test_table_view_definition_defaults_none() -> None:
    t = Table(name="orders", schema_name="main", columns=[])
    assert t.view_definition is None


def test_table_view_definition_settable() -> None:
    t = Table(
        name="v",
        schema_name="main",
        columns=[],
        view_definition="CREATE VIEW v AS SELECT 1;",
    )
    assert t.view_definition == "CREATE VIEW v AS SELECT 1;"
```

**Step 4.2 — run, expect FAIL:**

```bash
uv run pytest tests/unit/test_view_introspection.py -v
```

**Step 4.3 — minimal implementation.** In `src/labrat/db/catalog.py`, add one field to `Table` (after `comment: str | None = None`):

```python
    comment: str | None = None
    view_definition: str | None = None  # full CREATE VIEW ... AS SELECT ...; None = base table
```

**Step 4.4 — run, expect PASS:**

```bash
uv run pytest tests/unit/test_view_introspection.py tests/unit/test_catalog_models.py -v
```

**Step 4.5 — gates + commit:**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/db/catalog.py tests/unit/test_view_introspection.py
git commit -m "feat(db): Table.view_definition field, default None (Unit B model)"
```

---

## Task 5 — DuckDB view introspection (Unit B, adapter)

**Files:**
- Modify: `src/labrat/db/duckdb_engine.py` (`_introspect_schema`, ~line 181)
- Modify: `tests/unit/test_view_introspection.py`

**Interfaces:**
- Consumes: `duckdb_views()` mechanism verified above; existing `_introspect_columns(schema_name, table_name)`.
- Produces: `introspect_catalog()` additionally returns one `Table` per user view (columns populated via `information_schema.columns`, `view_definition` = full `CREATE VIEW` SQL, `foreign_keys=[]`, `row_count=None`), appended after the base tables of each schema. Base-table branch byte-identical. DuckDB adapter only — other adapters untouched (surface no views).

**Step 5.1 — write the failing tests** (append to `tests/unit/test_view_introspection.py`):

```python
def _view_db(tmp_path: Path) -> DuckDBConnection:
    p = str(tmp_path / "v.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE orders(id INTEGER, customer_id INTEGER, amount DOUBLE)")
    raw.execute("CREATE TABLE customers(id INTEGER, name VARCHAR)")
    raw.execute(
        "CREATE VIEW customer_spend AS "
        "SELECT c.name AS customer_name, SUM(o.amount) AS total "
        "FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name"
    )
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    return conn


def test_view_enters_catalog_with_definition_and_columns(tmp_path: Path) -> None:
    conn = _view_db(tmp_path)
    try:
        cat = conn.introspect_catalog()
    finally:
        conn.disconnect()
    v = cat.find_table("customer_spend")
    assert v is not None
    assert v.view_definition is not None
    assert v.view_definition.upper().startswith("CREATE VIEW")
    assert [c.name for c in v.columns] == ["customer_name", "total"]
    assert [c.data_type for c in v.columns] == ["VARCHAR", "DOUBLE"]


def test_base_tables_keep_view_definition_none(tmp_path: Path) -> None:
    conn = _view_db(tmp_path)
    try:
        cat = conn.introspect_catalog()
    finally:
        conn.disconnect()
    for name in ("orders", "customers"):
        t = cat.find_table(name)
        assert t is not None
        assert t.view_definition is None


def test_no_views_db_surfaces_only_base_tables(tmp_path: Path) -> None:
    p = str(tmp_path / "plain.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE city(id INTEGER, name VARCHAR)")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        cat = conn.introspect_catalog()
    finally:
        conn.disconnect()
    main = next(s for s in cat.schemas if s.name == "main")
    assert [t.name for t in main.tables] == ["city"]
    assert all(t.view_definition is None for t in main.tables)
```

**Step 5.2 — run, expect FAIL** (`find_table("customer_spend")` returns None — views filtered out today):

```bash
uv run pytest tests/unit/test_view_introspection.py -v
```

**Step 5.3 — minimal implementation.** In `src/labrat/db/duckdb_engine.py::_introspect_schema`, keep the existing base-table loop **untouched** and append a view loop before `return tables`:

```python
    def _introspect_schema(self, schema_name: str) -> list[Table]:
        table_rows = self._connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = ? AND table_type = 'BASE TABLE' "
            "ORDER BY table_name",
            [schema_name],
        ).fetchall()

        tables: list[Table] = []
        for (table_name,) in table_rows:
            columns = self._introspect_columns(schema_name, str(table_name))
            fks = self._introspect_fks(schema_name, str(table_name))
            tables.append(
                Table(
                    name=str(table_name),
                    schema_name=schema_name,
                    columns=columns,
                    foreign_keys=fks,
                )
            )

        # Views: enumerate via duckdb_views() (NOT information_schema.views, which
        # includes ~50 internal temp views such as sqlite_master / pg_catalog.*).
        # A view is a Table with columns (same information_schema.columns path as
        # base tables) plus the full CREATE VIEW SQL in view_definition.
        view_rows = self._connection.execute(
            "SELECT view_name, sql FROM duckdb_views() "
            "WHERE NOT internal AND schema_name = ? "
            "ORDER BY view_name",
            [schema_name],
        ).fetchall()
        for view_name, view_sql in view_rows:
            columns = self._introspect_columns(schema_name, str(view_name))
            tables.append(
                Table(
                    name=str(view_name),
                    schema_name=schema_name,
                    columns=columns,
                    view_definition=str(view_sql),
                )
            )
        return tables
```

**Step 5.4 — run, expect PASS; then run the FULL suite** (views entering the catalog could surface in any test that introspects a fixture DB — repo fixtures have no views, so expect green; if a test asserts an exact table list over a DB that creates views, fix that test's expectation, never the adapter):

```bash
uv run pytest tests/unit/test_view_introspection.py -v
uv run pytest -q
```

**Step 5.5 — gates + commit:**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/db/duckdb_engine.py tests/unit/test_view_introspection.py
git commit -m "feat(db): DuckDB introspection captures views + definitions (Unit B)"
```

---

## Task 6 — lineage helpers: schema dict + Node flattening (Unit C core)

**Files:**
- Create: `src/labrat/agent/tools/explain_lineage.py`
- Create: `tests/unit/test_explain_lineage.py`

**Interfaces:**
- Consumes: `Catalog` (`labrat.db.catalog`); `sqlglot.lineage.lineage/Node`; `sqlglot.optimizer.qualify.qualify`; the flattening mechanism verified above.
- Produces (module-level in `explain_lineage.py`; Unit D imports the first two):
  - `_catalog_schema_dict(cat: Catalog) -> dict[str, dict[str, str]]` — sqlglot schema mapping `{table: {column: data_type}}` (adapts `check_sql._catalog_index`, keeping original case + data types).
  - `_flatten(node: Node) -> list[_SourceRef]` — deduplicated base-table `{table, column}` leaf pairs.
  - `_output_columns(query: exp.Query, schema: dict[str, dict[str, str]]) -> list[str]` — final projection names, star-expanded via `qualify`.
  - `_SourceRef(table: str, column: str)` pydantic model.

**Step 6.1 — write the failing tests** (create `tests/unit/test_explain_lineage.py`):

```python
"""Unit C: explain_lineage tool — sqlglot lineage against the Catalog, parse-only."""

from __future__ import annotations

from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.explain_lineage import (
    _catalog_schema_dict,
    _flatten,
    _output_columns,
)
from labrat.db.catalog import Catalog, Column, Schema, Table

_CAT = Catalog(
    database_name="shop",
    schemas=[
        Schema(
            name="main",
            tables=[
                Table(
                    name="orders",
                    schema_name="main",
                    columns=[
                        Column(name="id", data_type="INTEGER", nullable=False),
                        Column(name="customer_id", data_type="INTEGER", nullable=True),
                        Column(name="amount", data_type="DOUBLE", nullable=True),
                    ],
                ),
                Table(
                    name="customers",
                    schema_name="main",
                    columns=[
                        Column(name="id", data_type="INTEGER", nullable=False),
                        Column(name="name", data_type="VARCHAR", nullable=True),
                    ],
                ),
            ],
        )
    ],
)

_JOIN_SQL = (
    "SELECT c.name AS customer_name, SUM(o.amount) AS total_spend "
    "FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name"
)


def test_catalog_schema_dict_shape() -> None:
    schema = _catalog_schema_dict(_CAT)
    assert schema == {
        "orders": {"id": "INTEGER", "customer_id": "INTEGER", "amount": "DOUBLE"},
        "customers": {"id": "INTEGER", "name": "VARCHAR"},
    }


def test_flatten_resolves_alias_to_real_table() -> None:
    schema = _catalog_schema_dict(_CAT)
    node = lineage("total_spend", _JOIN_SQL, schema=schema)
    refs = _flatten(node)
    assert [(r.table, r.column) for r in refs] == [("orders", "amount")]


def test_flatten_strips_quoted_identifiers() -> None:
    schema = _catalog_schema_dict(_CAT)
    node = lineage("customer_name", _JOIN_SQL, schema=schema)
    refs = _flatten(node)
    assert [(r.table, r.column) for r in refs] == [("customers", "name")]


def test_flatten_literal_projection_yields_no_sources() -> None:
    schema = _catalog_schema_dict(_CAT)
    node = lineage("k", "SELECT 1 AS k FROM customers", schema=schema)
    assert _flatten(node) == []


def test_flatten_traces_through_cte() -> None:
    schema = _catalog_schema_dict(_CAT)
    sql = (
        "WITH big AS (SELECT customer_id, SUM(amount) AS spend FROM orders GROUP BY customer_id) "
        "SELECT c.name, b.spend AS total FROM big b JOIN customers c ON b.customer_id = c.id"
    )
    node = lineage("total", sql, schema=schema)
    assert [(r.table, r.column) for r in _flatten(node)] == [("orders", "amount")]


def test_output_columns_named_projections() -> None:
    schema = _catalog_schema_dict(_CAT)
    tree = sqlglot.parse_one(_JOIN_SQL)
    assert isinstance(tree, exp.Query)
    assert _output_columns(tree, schema) == ["customer_name", "total_spend"]


def test_output_columns_expands_star_via_schema() -> None:
    schema = _catalog_schema_dict(_CAT)
    tree = sqlglot.parse_one("SELECT * FROM orders")
    assert isinstance(tree, exp.Query)
    assert _output_columns(tree, schema) == ["id", "customer_id", "amount"]


def test_output_columns_unresolvable_star_skipped() -> None:
    schema = _catalog_schema_dict(_CAT)
    tree = sqlglot.parse_one("SELECT * FROM mystery")
    assert isinstance(tree, exp.Query)
    assert _output_columns(tree, schema) == []
```

Note on `test_flatten_traces_through_cte`: pinned to `[("orders", "amount")]` — verified live on sqlglot 30.8.0 (lineage resolves through the CTE's `spend` alias down to the base column).

**Step 6.2 — run, expect FAIL** (module does not exist):

```bash
uv run pytest tests/unit/test_explain_lineage.py -v
```

**Step 6.3 — minimal implementation.** Create `src/labrat/agent/tools/explain_lineage.py`:

```python
"""explain_lineage: deterministic column-level lineage via sqlglot (no execution, no LLM).

Traces each output column of a SQL query back to the base-table columns it derives
from, resolving against the live introspected Catalog — never a dbt manifest
(manifests go stale; a live parse is always current). Mirrors check_sql's
parse-only, fail-soft design: a ParseError / unresolved column returns a
structured parse_error, never raises.
"""

from __future__ import annotations

from typing import cast

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import Node, lineage
from sqlglot.optimizer.qualify import qualify

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.catalog import Catalog


class _SourceRef(BaseModel):
    table: str
    column: str


class _ColumnLineage(BaseModel):
    output_column: str
    sources: list[_SourceRef]
    derivation: str


class _Input(BaseModel):
    sql: str = Field(description="The SQL query to trace (parsed only — never executed).")
    column: str | None = Field(
        default=None,
        description="Trace only this output column; when omitted, every output column is traced.",
    )
    database: str | None = Field(default=None, description="Connection name; defaults to primary.")


class _Output(BaseModel):
    columns: list[_ColumnLineage]
    parse_error: str | None = None


def _catalog_schema_dict(cat: Catalog) -> dict[str, dict[str, str]]:
    """sqlglot schema mapping {table: {column: data_type}} across all schemas.

    Adapts check_sql._catalog_index, but keeps original casing and data types —
    sqlglot.lineage() wants the {table: {col: dtype}} schema form.
    """
    schema: dict[str, dict[str, str]] = {}
    for sch in cat.schemas:
        for t in sch.tables:
            schema.setdefault(t.name, {}).update({c.name: c.data_type for c in t.columns})
    return schema


def _leaves(node: Node) -> list[Node]:
    if not node.downstream:
        return [node]
    out: list[Node] = []
    for child in node.downstream:
        out.extend(_leaves(child))
    return out


def _flatten(node: Node) -> list[_SourceRef]:
    """Flatten a lineage Node tree to deduplicated base-table {table, column} pairs.

    A leaf is a node with no downstream. It maps to a base column iff its .source
    is an exp.Table (a literal-only projection has a Select source and yields
    nothing). node.name is '<alias-or-table>.<column>' (possibly quoted) — the real
    table name comes from leaf.source.name and the column name from
    exp.to_column(leaf.name).name (which strips identifier quoting).
    """
    refs: list[_SourceRef] = []
    seen: set[tuple[str, str]] = set()
    for leaf in _leaves(node):
        src = leaf.source
        if not isinstance(src, exp.Table):
            continue
        pair = (src.name, exp.to_column(leaf.name).name)
        if pair in seen:
            continue
        seen.add(pair)
        refs.append(_SourceRef(table=pair[0], column=pair[1]))
    return refs


def _output_columns(query: exp.Query, schema: dict[str, dict[str, str]]) -> list[str]:
    """Names of the query's final projections; '*' is expanded via qualify(schema).

    A star that cannot be resolved (table missing from the schema) survives as '*'
    and is skipped — fail-soft, consistent with check_sql's fail-open stance.
    """
    try:
        qualified = qualify(query.copy(), schema=schema)
    except SqlglotError:
        qualified = query
    if not isinstance(qualified, exp.Query):  # qualify returns Expression; keep pyright narrow
        qualified = query
    return [s.alias_or_name for s in qualified.selects if s.alias_or_name != "*"]
```

**Step 6.4 — run, expect PASS:**

```bash
uv run pytest tests/unit/test_explain_lineage.py -v
```

**Step 6.5 — gates + commit:**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/explain_lineage.py tests/unit/test_explain_lineage.py
git commit -m "feat(tools): lineage helpers — catalog schema dict + Node flattening (Unit C core)"
```

---

## Task 7 — `ExplainLineageTool` + registration (Unit C complete)

**Files:**
- Modify: `src/labrat/agent/tools/explain_lineage.py`
- Modify: `src/labrat/agent/data_tools.py`
- Modify: `tests/unit/test_explain_lineage.py`

**Interfaces:**
- Consumes: Task 6 helpers; `ToolContext.catalogs` routing idiom (`ctx.catalogs[args.database or ctx.primary]`, as in `check_sql.py:79`); Task 1 read-only gate.
- Produces: `ExplainLineageTool(Tool[_Input])` with `name="explain_lineage"`, `mutating` default False (read-only-safe); registered in `build_data_tools_registry()`. Output model: `_Output(columns: list[_ColumnLineage], parse_error: str | None)`.

**Step 7.1 — write the failing tests** (append to `tests/unit/test_explain_lineage.py`):

```python
from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.explain_lineage import ExplainLineageTool


def _ctx() -> ToolContext:
    return ToolContext(catalog=_CAT)


async def test_traces_all_output_columns_of_a_join() -> None:
    tool = ExplainLineageTool()
    out = await tool.execute(_ctx(), tool.input_model(sql=_JOIN_SQL))
    assert out.parse_error is None
    assert [c.output_column for c in out.columns] == ["customer_name", "total_spend"]
    by_name = {c.output_column: c for c in out.columns}
    assert [(r.table, r.column) for r in by_name["customer_name"].sources] == [
        ("customers", "name")
    ]
    assert [(r.table, r.column) for r in by_name["total_spend"].sources] == [("orders", "amount")]
    assert "SUM" in by_name["total_spend"].derivation.upper()


async def test_column_arg_narrows_to_one_output() -> None:
    tool = ExplainLineageTool()
    out = await tool.execute(_ctx(), tool.input_model(sql=_JOIN_SQL, column="total_spend"))
    assert out.parse_error is None
    assert [c.output_column for c in out.columns] == ["total_spend"]


async def test_bad_sql_returns_parse_error_not_raise() -> None:
    tool = ExplainLineageTool()
    out = await tool.execute(_ctx(), tool.input_model(sql="SELEC nope FROM"))
    assert out.columns == []
    assert out.parse_error is not None


async def test_unknown_column_returns_parse_error_not_raise() -> None:
    tool = ExplainLineageTool()
    out = await tool.execute(
        _ctx(), tool.input_model(sql="SELECT name FROM customers", column="nonexistent")
    )
    assert out.parse_error is not None
    assert "nonexistent" in out.parse_error


async def test_non_select_returns_parse_error() -> None:
    tool = ExplainLineageTool()
    out = await tool.execute(_ctx(), tool.input_model(sql="INSERT INTO orders VALUES (1, 2, 3)"))
    assert out.columns == []
    assert out.parse_error == "lineage requires a SELECT query"


async def test_star_query_expanded_via_catalog() -> None:
    tool = ExplainLineageTool()
    out = await tool.execute(_ctx(), tool.input_model(sql="SELECT * FROM orders"))
    assert out.parse_error is None
    assert [c.output_column for c in out.columns] == ["id", "customer_id", "amount"]


async def test_registered_and_read_only_safe() -> None:
    reg = build_data_tools_registry()
    res = await reg.dispatch(
        "explain_lineage",
        {"sql": "SELECT name FROM customers"},
        ToolContext(catalog=_CAT, read_only=True),
    )
    assert res.ok is True
    assert res.value.parse_error is None  # type: ignore[union-attr]
```

**Step 7.2 — run, expect FAIL** (`ExplainLineageTool` does not exist):

```bash
uv run pytest tests/unit/test_explain_lineage.py -v
```

**Step 7.3 — minimal implementation.** Append to `src/labrat/agent/tools/explain_lineage.py`:

```python
class ExplainLineageTool(Tool[_Input]):
    """Column-level lineage for a query, resolved live against the Catalog."""

    @property
    def name(self) -> str:
        return "explain_lineage"

    @property
    def description(self) -> str:
        return (
            "Trace column-level lineage for a SQL query WITHOUT executing it: for each "
            "output column, report the base-table columns it derives from plus the "
            "deriving expression. Use it to answer 'where does this column come from?' "
            "and to sanity-check a query's sources before trusting its results. "
            "Pass column= to trace a single output column."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        cat = cast(Catalog, ctx.catalogs[args.database or ctx.primary])
        schema = _catalog_schema_dict(cat)

        try:
            tree = sqlglot.parse_one(args.sql)
        except SqlglotError as exc:
            return _Output(columns=[], parse_error=str(exc))
        if not isinstance(tree, exp.Query):
            return _Output(columns=[], parse_error="lineage requires a SELECT query")

        targets = [args.column] if args.column is not None else _output_columns(tree, schema)
        deduped: list[str] = []
        for t in targets:  # SELECT a, a FROM t — trace each name once
            if t not in deduped:
                deduped.append(t)

        results: list[_ColumnLineage] = []
        for col in deduped:
            try:
                node = lineage(col, args.sql, schema=schema)
            except SqlglotError as exc:
                return _Output(columns=results, parse_error=str(exc))
            results.append(
                _ColumnLineage(
                    output_column=col,
                    sources=_flatten(node),
                    derivation=node.expression.sql(),
                )
            )
        return _Output(columns=results)
```

Register in `src/labrat/agent/data_tools.py` — add the import (alphabetical, after `explain_sql`):

```python
from labrat.agent.tools.explain_lineage import ExplainLineageTool
```

add to the docstring tool list: `run_sql, explain_sql, explain_lineage, verify_join, ...`, and register next to its sibling:

```python
    registry.register(ExplainSqlTool())
    registry.register(ExplainLineageTool())
```

**Step 7.4 — run, expect PASS** (plus MCP server + agent suites, which enumerate the registry):

```bash
uv run pytest tests/unit/test_explain_lineage.py tests/unit/test_mcp_server.py tests/unit/test_agent_runner.py -v
```

**Step 7.5 — gates + commit:**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/explain_lineage.py src/labrat/agent/data_tools.py tests/unit/test_explain_lineage.py
git commit -m "feat(tools): explain_lineage tool — query-scoped column lineage, fail-soft (Unit C)"
```

---

## Task 8 — `lineage` Scent source token (Unit D, document model)

**Files:**
- Modify: `src/labrat/maze/document.py` (`_RECOGNIZED_SOURCES`, line 17; `Section.source` comment, line 24)
- Create: `tests/unit/test_cartographer_view_lineage.py`

**Interfaces:**
- Consumes: `_extract_source` / `render_document` / `parse_document` round-trip machinery (unchanged).
- Produces: `_RECOGNIZED_SOURCES = {"verified", "draft", "human", "lineage"}` so a `**Source:** lineage` marker survives the parse↔render round-trip instead of falling back to `human`.

**Step 8.1 — write the failing tests** (create `tests/unit/test_cartographer_view_lineage.py`):

```python
"""Unit D: lineage source token + build_view_lineage + generate_scent wiring + audit."""

from __future__ import annotations

from labrat.maze.document import ScentDoc, Section, parse_document, render_document


def test_lineage_source_token_round_trips() -> None:
    doc = ScentDoc(
        domain="shop",
        sections=[
            Section(
                heading="View Lineage",
                body="- view `customer_spend`.`total` ← `orders`.`amount`",
                source="lineage",
            )
        ],
    )
    rendered = render_document(doc)
    assert "**Source:** lineage" in rendered
    reparsed = parse_document(rendered, domain="shop")
    section = next(s for s in reparsed.sections if s.heading == "View Lineage")
    assert section.source == "lineage"


def test_unknown_source_token_still_falls_back_to_human() -> None:
    text = "---\ndomain: d\n---\n\n## X\n**Source:** wizardry\n\n- body\n"
    doc = parse_document(text, domain="d")
    assert doc.sections[0].source == "human"
```

**Step 8.2 — run, expect FAIL** (round-trip collapses `lineage` → `human`):

```bash
uv run pytest tests/unit/test_cartographer_view_lineage.py -v
```

**Step 8.3 — minimal implementation.** In `src/labrat/maze/document.py`:

```python
_RECOGNIZED_SOURCES = {"verified", "draft", "human", "lineage"}
```

and update the `Section.source` comment:

```python
    source: str = "human"  # "verified" | "draft" | "human" | "lineage"; provenance for #26b cartographer
```

**Step 8.4 — run, expect PASS:**

```bash
uv run pytest tests/unit/test_cartographer_view_lineage.py tests/unit/test_maze_document.py tests/unit/test_maze_document_render.py -v
```

**Step 8.5 — gates + commit:**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/document.py tests/unit/test_cartographer_view_lineage.py
git commit -m "feat(maze): recognize 'lineage' Scent section source token (Unit D)"
```

---

## Task 9 — `build_view_lineage` builder (Unit D core)

**Files:**
- Modify: `src/labrat/maze/cartographer.py`
- Modify: `tests/unit/test_cartographer_view_lineage.py`

**Interfaces:**
- Consumes: `Table.view_definition` (Task 5); `_catalog_schema_dict` / `_flatten` from `explain_lineage` (Task 6; private-import with `# pyright: ignore[reportPrivateUsage]`, same idiom as the existing `_TableProfile` import at the top of cartographer.py); `sqlglot.parse_one` → `exp.Create.expression` (verified mechanism).
- Produces: `build_view_lineage(catalog: Catalog, *, database: str) -> Section | None` — one bullet per resolved view column, `Section(heading="View Lineage", source="lineage")`; `None` when the catalog has no views. **GT-firewall by construction:** the signature takes only the `Catalog` — no `Connection`, so it *cannot* read data.

**Step 9.1 — write the failing tests** (append to `tests/unit/test_cartographer_view_lineage.py`):

```python
from pathlib import Path

import duckdb

from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import build_view_lineage


def _view_catalog(tmp_path: Path) -> Catalog:
    p = str(tmp_path / "vl.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE orders(id INTEGER, customer_id INTEGER, amount DOUBLE)")
    raw.execute("CREATE TABLE customers(id INTEGER, name VARCHAR)")
    raw.execute(
        "CREATE VIEW customer_spend AS "
        "SELECT c.name AS customer_name, SUM(o.amount) AS total "
        "FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name"
    )
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        return conn.introspect_catalog()
    finally:
        conn.disconnect()


def test_build_view_lineage_emits_lineage_section(tmp_path: Path) -> None:
    section = build_view_lineage(_view_catalog(tmp_path), database="shop")
    assert section is not None
    assert section.heading == "View Lineage"
    assert section.source == "lineage"
    assert "- view `customer_spend`.`customer_name` ← `customers`.`name`" in section.body
    assert "- view `customer_spend`.`total` ← `orders`.`amount`" in section.body


def test_build_view_lineage_none_when_no_views(tmp_path: Path) -> None:
    p = str(tmp_path / "plain.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE city(id INTEGER, name VARCHAR)")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        cat = conn.introspect_catalog()
    finally:
        conn.disconnect()
    assert build_view_lineage(cat, database="c") is None


def test_build_view_lineage_needs_no_connection_and_skips_unparseable() -> None:
    # GT-firewall by construction: a hand-built Catalog (no live DB anywhere) is
    # sufficient input; an unparseable view definition is skipped fail-soft.
    cat = Catalog(
        database_name="x",
        schemas=[
            Schema(
                name="main",
                tables=[
                    Table(
                        name="base",
                        schema_name="main",
                        columns=[Column(name="a", data_type="INTEGER", nullable=True)],
                    ),
                    Table(
                        name="good_view",
                        schema_name="main",
                        columns=[Column(name="a2", data_type="INTEGER", nullable=True)],
                        view_definition="CREATE VIEW good_view AS SELECT a AS a2 FROM base",
                    ),
                    Table(
                        name="broken_view",
                        schema_name="main",
                        columns=[],
                        view_definition="CREATE VIEW broken_view AS SELEC nope FROM",
                    ),
                ],
            )
        ],
    )
    section = build_view_lineage(cat, database="x")
    assert section is not None
    assert "- view `good_view`.`a2` ← `base`.`a`" in section.body
    assert "broken_view" not in section.body
```

**Step 9.2 — run, expect FAIL** (`build_view_lineage` does not exist):

```bash
uv run pytest tests/unit/test_cartographer_view_lineage.py -v
```

**Step 9.3 — minimal implementation.** In `src/labrat/maze/cartographer.py`, add to the top-level imports (ruff-format/isort will order them):

```python
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage

from labrat.agent.tools.explain_lineage import (
    _catalog_schema_dict,  # pyright: ignore[reportPrivateUsage]
    _flatten,  # pyright: ignore[reportPrivateUsage]
)
from labrat.db.catalog import Catalog
```

Then add the builder after `build_code_name_notes` (before `discover_joins`):

```python
def build_view_lineage(catalog: Catalog, *, database: str) -> Section | None:
    """Deterministic column-level lineage for every view in *catalog* (Unit D).

    Reads ONLY ``Table.view_definition`` SQL metadata — no connection, no data, no
    LLM (GT-firewall by construction: the signature cannot reach a database).
    Fail-soft per view/column: unparseable definitions or unresolvable columns are
    skipped, never fatal. Returns None when the catalog has no views, so a no-views
    DB yields byte-identical deterministic Scent.
    """
    _ = database  # doc identity is per-connection already; kept for call-site clarity
    schema = _catalog_schema_dict(catalog)
    lines: list[str] = []
    for sch in catalog.schemas:
        for t in sch.tables:
            if t.view_definition is None:
                continue
            try:
                tree = sqlglot.parse_one(t.view_definition)
            except SqlglotError:
                continue  # unparseable view — skip it, never block Scent generation
            select = tree.expression if isinstance(tree, exp.Create) else tree
            if not isinstance(select, exp.Query):
                continue
            select_sql = select.sql()
            for proj in select.selects:
                out_name = proj.alias_or_name
                if out_name == "*":
                    continue
                try:
                    node = lineage(out_name, select_sql, schema=schema)
                except SqlglotError:
                    continue  # unresolvable column — skip the bullet, keep the rest
                refs = _flatten(node)
                if not refs:
                    continue  # literal-only column: no base sources to report
                srcs = ", ".join(f"`{r.table}`.`{r.column}`" for r in refs)
                lines.append(f"- view `{t.name}`.`{out_name}` ← {srcs}")
    if not lines:
        return None
    return Section(heading="View Lineage", body="\n".join(lines), source="lineage")
```

**Step 9.4 — run, expect PASS:**

```bash
uv run pytest tests/unit/test_cartographer_view_lineage.py -v
```

**Step 9.5 — gates + commit:**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_view_lineage.py
git commit -m "feat(maze): build_view_lineage — metadata-only view lineage builder (Unit D core)"
```

---

## Task 10 — wire into `generate_scent` + no-views byte-identity (Unit D complete)

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (`generate_scent`, ~line 622)
- Modify: `tests/unit/test_cartographer_view_lineage.py`

**Interfaces:**
- Consumes: `build_view_lineage` (Task 9); `generate_scent`'s per-connection section list and its existing `catalogs[name]` dict; the `build_code_name_notes` None-guard pattern at cartographer.py:622-624.
- Produces: `generate_scent` docs carry a `View Lineage` / `source="lineage"` section iff the connection's catalog has views. Runs on the deterministic path (no `with_semantics` needed). No-views DB: builder returns `None` → nothing appended → byte-identical output (the existing `test_generate_writes_retrievable_verified_doc` assertion `all(s.source == "verified" ...)` on the no-views ecommerce fixture is the standing regression witness).

**Step 10.1 — write the failing tests** (append to `tests/unit/test_cartographer_view_lineage.py`):

```python
from labrat.maze.cartographer import generate_scent


async def test_generate_scent_includes_lineage_section_for_view_db(tmp_path: Path) -> None:
    p = str(tmp_path / "scent_view.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE orders(id INTEGER, customer_id INTEGER, amount DOUBLE)")
    raw.execute("CREATE TABLE customers(id INTEGER, name VARCHAR)")
    raw.execute("INSERT INTO orders VALUES (1, 1, 10.0), (2, 1, 5.0)")
    raw.execute("INSERT INTO customers VALUES (1, 'Ada')")
    raw.execute(
        "CREATE VIEW customer_spend AS "
        "SELECT c.name AS customer_name, SUM(o.amount) AS total "
        "FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name"
    )
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        docs = await generate_scent(
            connections={"shop": conn},
            catalogs={"shop": conn.introspect_catalog()},
            primary="shop",
            with_semantics=False,
        )
    finally:
        conn.disconnect()
    sections = {s.heading: s for s in docs[0].sections}
    assert "View Lineage" in sections
    assert sections["View Lineage"].source == "lineage"
    assert "`customer_spend`.`total` ← `orders`.`amount`" in sections["View Lineage"].body


async def test_generate_scent_no_views_has_no_lineage_section(tmp_path: Path) -> None:
    # Byte-identity: the builder returns None → nothing appended → deterministic
    # output for a no-views DB is unchanged (mirrors the Code Columns precedent).
    p = str(tmp_path / "scent_plain.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE city(id INTEGER, name VARCHAR)")
    raw.execute("INSERT INTO city VALUES (1, 'London'), (2, 'Paris')")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        docs = await generate_scent(
            connections={"c": conn},
            catalogs={"c": conn.introspect_catalog()},
            primary="c",
            with_semantics=False,
        )
    finally:
        conn.disconnect()
    assert "View Lineage" not in {s.heading for s in docs[0].sections}
    assert all(s.source == "verified" for s in docs[0].sections)
```

**Step 10.2 — run, expect FAIL** (no lineage section produced by `generate_scent`):

```bash
uv run pytest tests/unit/test_cartographer_view_lineage.py -v
```

**Step 10.3 — minimal implementation.** In `generate_scent`, directly after the `build_code_name_notes` block (`cn = ...` / `if cn is not None: sections.append(cn)`), add:

```python
        vl = build_view_lineage(cast(Catalog, catalogs[name]), database=name)
        if vl is not None:
            sections.append(vl)
```

**Step 10.4 — run, expect PASS** (plus every existing cartographer suite — the byte-identity regression witnesses):

```bash
uv run pytest tests/unit/test_cartographer_view_lineage.py tests/unit/test_cartographer_generate.py tests/unit/test_cartograph_prepass.py tests/unit/test_cartographer_audit.py -v
```

**Step 10.5 — gates + commit:**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_view_lineage.py
git commit -m "feat(maze): wire view lineage into generate_scent, None-guarded (Unit D complete)"
```

---

## Task 11 — Phase 5: audit fail-loud over lineage sections, full regression, docs

**Files:**
- Modify: `tests/unit/test_cartographer_view_lineage.py`
- Modify: `CLAUDE.md` (tool counts), `decisions.md` (dated entry)

**Interfaces:**
- Consumes: `audit_scent_doc(doc: ScentDoc) -> str | None` / `CONTAMINATION_PATTERNS` (`src/labrat/maze/scent_audit.py:44`) — **no production code changes expected**: `audit_scent_doc` renders the whole doc, so a `lineage`-tagged section is already in scope; these tests pin that.
- Produces: proof that a leaky lineage section still trips the audit; green gates over the whole feature; doc touch-ups.

**Step 11.1 — write the tests** (append to `tests/unit/test_cartographer_view_lineage.py`; these should PASS immediately — they pin existing fail-loud behavior over the new section type. If either fails, STOP: that is a real audit hole, not a test problem):

```python
from labrat.maze.scent_audit import audit_scent_doc


def test_leaky_lineage_section_trips_audit() -> None:
    doc = ScentDoc(
        domain="d",
        sections=[
            Section(
                heading="View Lineage",
                body="- view `v`.`label` ← `ground_truth`.`label`",
                source="lineage",
            )
        ],
    )
    assert audit_scent_doc(doc) == "answer_key"


def test_clean_lineage_section_passes_audit() -> None:
    doc = ScentDoc(
        domain="d",
        sections=[
            Section(
                heading="View Lineage",
                body="- view `customer_spend`.`total` ← `orders`.`amount`",
                source="lineage",
            )
        ],
    )
    assert audit_scent_doc(doc) is None
```

**Step 11.2 — run, expect PASS:**

```bash
uv run pytest tests/unit/test_cartographer_view_lineage.py -v
```

**Step 11.3 — full regression over everything this branch touched:**

```bash
uv run pytest tests/unit/test_read_only_mode.py tests/unit/test_view_introspection.py tests/unit/test_explain_lineage.py tests/unit/test_cartographer_view_lineage.py -v
uv run pytest -q
```

Expected: full suite green (~670 + ~45 new). Known allowed exception: `tests/tui/test_app_renders.py` (env-sensitive, pre-existing, unrelated).

**Step 11.4 — docs.** In `CLAUDE.md`: bump "Current tools (20)" → "Current tools (21)", "registers the 15 above" → "registers the 16 above", and add `explain_lineage` to the `build_data_tools_registry()` tool list sentence (after `explain_sql`). In `decisions.md`, append a dated entry:

```markdown
## 2026-07-04 — Column-level lineage + read-only Analyst mode (M3 / T1b)

- Read-only "Analyst" mode is enforced at ToolRegistry.dispatch (ToolContext.read_only
  + Tool.is_mutating), never in the prompt. run_sql classifies its SQL via a
  fail-closed sqlglot safelist (unparseable SQL is blocked under read_only, and
  force=True cannot bypass the gate).
- Column lineage is live-parsed via sqlglot.lineage against the introspected Catalog —
  deliberately NOT dbt-manifest-based (manifests go stale). explain_lineage is
  parse-only/fail-soft, mirroring check_sql.
- DuckDB introspection now captures views (Table.view_definition; duckdb_views(),
  NOT information_schema.views which leaks ~50 internal temp views). The Cartographer
  emits a `lineage`-tagged View Lineage section from view metadata only (GT-firewalled
  by construction: build_view_lineage takes a Catalog, no Connection); no-views DBs
  yield byte-identical Scent.
```

**Step 11.5 — gates + final commit:**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add tests/unit/test_cartographer_view_lineage.py CLAUDE.md decisions.md
git commit -m "test(maze): pin audit fail-loud over lineage sections; docs for M3 (Phase 5)"
```

Then follow **superpowers:finishing-a-development-branch** (do not merge without the user's go-ahead; this branch stays isolated until exit gates pass, per the branch-isolation convention).

---

## Spec-coverage checklist (self-review, completed at authoring time)

- Unit A: `ToolContext.read_only` (T1) ✓, `Tool.is_mutating` default→class attr (T1) ✓, central dispatch gate with exact error string (T1) ✓, three structural tools flagged (T2) ✓, `run_sql` SQL classification with SELECT-still-runs + fail-closed parse + force-no-bypass + `read_only=False` regression (T3) ✓.
- Unit B: `Table.view_definition` default None (T4) ✓, DuckDB view enumeration + definition + columns via existing path, base branch untouched, no-views catalog unchanged (T5) ✓, DuckDB-only scope honored (no other adapter touched) ✓.
- Unit C: schema dict adapted from `check_sql` (T6) ✓, experimentally-confirmed flattening (leaf = no downstream + `exp.Table` source; `leaf.source.name` + `exp.to_column(leaf.name).name`) (T6) ✓, output-column enumeration incl. star expansion via `qualify` (T6) ✓, tool with `sql/column/database` inputs, `{columns:[{output_column,sources,derivation}], parse_error}` output, fail-soft, `mutating=False`, registered (T7) ✓.
- Unit D: `lineage` source token (T8) ✓, `build_view_lineage → Section | None`, None-on-no-views, GT-firewall-by-signature, fail-soft per view (T9) ✓, `generate_scent` wiring at the `build_code_name_notes` spot (T10) ✓.
- Phase 5: leaky-lineage-section audit fail-loud + clean-pass (T11) ✓, byte-identity regression (T10 + existing cartographer suites) ✓, full gates every task ✓.
- Placeholder scan: every code block is complete and runnable; no `TODO`/`...`/pseudo-code. Type consistency: `_SourceRef`/`_ColumnLineage`/`_Input`/`_Output` names match between Tasks 6/7/9; `build_view_lineage(catalog: Catalog, *, database: str)` matches its T10 call site; `read_only` kwarg matches all test call sites.
