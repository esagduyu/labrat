# DAB claude-mcp Feature-Gap Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three gaps that stop the DAB `claude-mcp` path (how Sonnet runs on DAB) from using tools we already built: catalog tools break on attached secondary DBs (GAP 1), the second DuckDB in a dataset is silently dropped (GAP 2), and the Context Ledger never attaches (GAP 3).

**Architecture:** GAP 1 makes `attach_database` introspect the attached catalog into `ctx.catalogs[alias]` (+ register the alias→primary connection) so the four catalog tools and the column-disambiguation hint resolve attached tables. GAP 2 routes secondary DuckDB files through the existing attach path (env spec + `attach_database` accept `db_type="duckdb"`; DuckDB's `ATTACH ... (TYPE DUCKDB)` already works). GAP 3 ports the existing, LLM-free `ContextLedger`/`ResultStore` into the MCP server's result serializer behind an opt-in env flag, with an MCP-local `get_artifact` retrieval handler.

**Tech Stack:** Python 3.12, DuckDB, Pydantic, pytest (`asyncio_mode="auto"`), the low-level `mcp.server.Server`.

## Global Constraints

- Every OFF path must stay **byte-identical** to today: GAP 3 only changes behavior when `LABRAT_MCP_LEDGER=1`; GAP 1/GAP 2 change only the attach path and are additive to previously-empty state.
- Pyright strict applies to all of `src/labrat/` except `dspy_opt/` and `screens/`. `json.loads()` results are `Unknown` — narrow or `cast`.
- `DuckDBConnection.execute()` is SELECT-only (goes through `pl.read_database`). For anything else call `self._connection.execute(...)` directly.
- Before every commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- Tool `name`/`description`/`input_model` are `@property` methods, not class attributes.
- Do NOT modify any DAB eval environment / dataset DBs (per user standing rule). All tests use `tmp_path` DuckDB/SQLite fixtures created in-test.

---

## File Structure

**GAP 1 — attached-catalog introspection**
- Modify `src/labrat/db/duckdb_engine.py` — add `introspect_attached_catalog(alias)`.
- Modify `src/labrat/agent/tools/attach_database.py` — populate `ctx.catalogs[alias]` + `ctx.connections[alias]` after a successful attach.
- Modify `src/labrat/agent/tools/describe_table.py` — strip a leading `"<database>."` prefix from the table arg so `find_table` resolves the dotted form agents pass.
- Test: `tests/unit/test_duckdb_attached_catalog.py`, extend `tests/unit/test_describe_table_column_hints.py`.

**GAP 2 — secondary DuckDB mounting**
- Modify `src/labrat/eval/benchmarks/dab/env.py` — emit secondary DuckDBs as `AttachSpec(db_type="duckdb")` instead of dropping them; widen `AttachSpec.db_type`.
- Modify `src/labrat/agent/tools/attach_database.py` — widen `_Input.db_type` to include `"duckdb"`.
- Test: extend `tests/unit/test_dab_env.py`, extend the attach test above.

**GAP 3 — server-side Context Ledger (opt-in)**
- Modify `src/labrat/mcp/server.py` — build an optional `ContextLedger`, route the success payload through it, add an MCP-local `get_artifact` tool + handler.
- Test: `tests/unit/test_mcp_server_ledger.py`.

---

## Task 1: `DuckDBConnection.introspect_attached_catalog(alias)`

**Files:**
- Modify: `src/labrat/db/duckdb_engine.py` (add method near `introspect_catalog`, ~line 120)
- Test: `tests/unit/test_duckdb_attached_catalog.py`

**Interfaces:**
- Produces: `DuckDBConnection.introspect_attached_catalog(self, alias: str) -> Catalog`. Returned `Catalog` has `database_name == alias`, one `Schema(name="main")`, and each `Table` has `schema_name == alias` (so `Table.qualified_name == "alias.table"`, which resolves through the primary connection) and populated `columns`; `foreign_keys` is `[]` (cross-/intra-attached FKs are out of scope).
- Consumes: `duckdb_tables()` / `duckdb_columns()` metadata functions (both expose a `database_name` column — verified against DuckDB in this repo).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_duckdb_attached_catalog.py
from pathlib import Path

from labrat.db.duckdb_engine import DuckDBConnection


def _make_duckdb(path: Path, ddl: list[str]) -> None:
    conn = DuckDBConnection(path=str(path), read_only=False)
    conn.connect()
    for stmt in ddl:
        conn._connection.execute(stmt)  # pyright: ignore[reportPrivateUsage]
    conn.disconnect()


def test_introspect_attached_catalog_lists_tables_and_columns(tmp_path: Path) -> None:
    secondary = tmp_path / "clinical.duckdb"
    _make_duckdb(
        secondary,
        [
            "CREATE TABLE clinical_info (bcr_patient_barcode VARCHAR, icd_o_3_histology VARCHAR)",
            "INSERT INTO clinical_info VALUES ('TCGA-01', '9382/3')",
        ],
    )
    primary = DuckDBConnection(path=":memory:", read_only=False)
    primary.connect()
    primary.attach(str(secondary), "clinical_database", "duckdb")

    catalog = primary.introspect_attached_catalog("clinical_database")

    assert catalog.database_name == "clinical_database"
    table = catalog.find_table("clinical_info")
    assert table is not None
    assert table.schema_name == "clinical_database"
    assert table.qualified_name == "clinical_database.clinical_info"
    assert {c.name for c in table.columns} == {"bcr_patient_barcode", "icd_o_3_histology"}
    primary.disconnect()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_duckdb_attached_catalog.py -v`
Expected: FAIL — `AttributeError: 'DuckDBConnection' object has no attribute 'introspect_attached_catalog'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/labrat/db/duckdb_engine.py` (import `Catalog, Schema, Table, Column` are already imported at top — verify; `ForeignKey` too):

```python
    def introspect_attached_catalog(self, alias: str) -> Catalog:
        """Introspect one ATTACHed database's tables into a Catalog scoped to ``alias``.

        ``introspect_catalog`` only sees the current (primary) catalog — DuckDB's
        information_schema is catalog-local — so an ATTACHed secondary is invisible to
        it. This reads ``duckdb_tables()``/``duckdb_columns()`` filtered by
        ``database_name = alias`` and builds a Catalog whose tables carry
        ``schema_name = alias`` so ``Table.qualified_name`` is ``alias.table`` and
        resolves through this (primary) connection. Foreign keys are omitted.
        """
        # NOTE (proven in the 2026-07-24 smoke): an attached POSTGRES db exposes
        # information_schema/pg_catalog tables under the alias too (212 tables on
        # pancancer_atlas), so filter system schemas or the agent drowns in noise.
        table_rows = self._connection.execute(
            "SELECT table_name, schema_name FROM duckdb_tables() "
            "WHERE database_name = ? "
            "AND schema_name NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY table_name",
            [alias],
        ).fetchall()
        tables: list[Table] = []
        for (table_name, _schema_name) in table_rows:
            col_rows = self._connection.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM duckdb_columns() "
                "WHERE database_name = ? AND table_name = ? "
                "ORDER BY column_index",
                [alias, str(table_name)],
            ).fetchall()
            columns = [
                Column(
                    name=str(cn),
                    data_type=str(dt),
                    nullable=bool(nn),
                    default=None,
                )
                for (cn, dt, nn) in col_rows
            ]
            tables.append(
                Table(
                    name=str(table_name),
                    schema_name=alias,
                    columns=columns,
                    foreign_keys=[],
                )
            )
        return Catalog(
            database_name=alias,
            schemas=[Schema(name="main", tables=tables)],
        )
```

If `Column`/`Schema` aren't imported in this file, add them to the existing `from labrat.db.catalog import ...` line.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_duckdb_attached_catalog.py -v`
Expected: PASS

- [ ] **Step 5: Verify sqlite + postgres alias introspection works too (add test)**

```python
def test_introspect_attached_catalog_for_sqlite(tmp_path: Path) -> None:
    import sqlite3

    sqlite_path = tmp_path / "review.db"
    sconn = sqlite3.connect(sqlite_path)
    sconn.execute("CREATE TABLE review (gmap_id TEXT, rating REAL)")
    sconn.commit()
    sconn.close()

    primary = DuckDBConnection(path=":memory:", read_only=False)
    primary.connect()
    primary.attach(str(sqlite_path), "review_database", "sqlite")
    catalog = primary.introspect_attached_catalog("review_database")
    table = catalog.find_table("review")
    assert table is not None
    assert {c.name for c in table.columns} == {"gmap_id", "rating"}
    primary.disconnect()
```

Run: `uv run pytest tests/unit/test_duckdb_attached_catalog.py -v`
Expected: PASS (both tests). If sqlite introspection needs the extension, `primary.attach` already loads it; `duckdb_columns()` reflects attached-sqlite tables.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/db/duckdb_engine.py tests/unit/test_duckdb_attached_catalog.py
git commit -m "feat(db): introspect_attached_catalog for ATTACHed secondary DBs"
```

---

## Task 2: `attach_database` populates `ctx.catalogs` + `ctx.connections`

**Files:**
- Modify: `src/labrat/agent/tools/attach_database.py:73-84`
- Test: `tests/unit/test_attach_database_catalog.py`

**Interfaces:**
- Consumes: `DuckDBConnection.introspect_attached_catalog(alias)` (Task 1).
- Produces: after `attach_database(alias=X)` succeeds, `ctx.catalogs[X]` is a `Catalog` and `ctx.connections[X]` is the same `DuckDBConnection` object as `ctx.connections[primary]`. On introspection failure the attach still returns `ok=True` (message notes the catalog was not indexed).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_attach_database_catalog.py
from pathlib import Path

from labrat.agent.tools.attach_database import AttachDatabaseTool
from labrat.agent.tools.base import ToolContext
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection


async def test_attach_registers_catalog_and_connection(tmp_path: Path) -> None:
    secondary = tmp_path / "clinical.duckdb"
    c = DuckDBConnection(path=str(secondary), read_only=False)
    c.connect()
    c._connection.execute(  # pyright: ignore[reportPrivateUsage]
        "CREATE TABLE clinical_info (icd_o_3_histology VARCHAR)"
    )
    c.disconnect()

    primary = DuckDBConnection(path=":memory:", read_only=False)
    primary.connect()
    ctx = ToolContext(
        connections={"main": primary},
        catalogs={"main": Catalog(database_name="main", schemas=[])},
        primary="main",
    )
    tool = AttachDatabaseTool()
    out = await tool.execute(
        ctx,
        tool.input_model(path=str(secondary), alias="clinical_database", db_type="duckdb"),
    )
    assert out.ok
    assert "clinical_database" in ctx.catalogs
    assert ctx.catalogs["clinical_database"].find_table("clinical_info") is not None
    assert ctx.connections["clinical_database"] is primary
    primary.disconnect()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_attach_database_catalog.py -v`
Expected: FAIL — `assert "clinical_database" in ctx.catalogs` (KeyError-equivalent, alias absent).

- [ ] **Step 3: Write minimal implementation**

Replace the success block in `attach_database.py` `execute` (lines 73-84):

```python
        try:
            conn.attach(args.path, args.alias, args.db_type)
        except Exception as exc:
            return _Output(ok=False, alias=args.alias, message=f"ATTACH failed: {exc}")

        catalog_note = ""
        try:
            ctx.catalogs[args.alias] = conn.introspect_attached_catalog(args.alias)
            # Register the alias against the SAME primary connection so catalog tools
            # that need a live handle (column_stats, describe_table value-hints) resolve
            # it; attached tables are addressed as alias.table through this connection.
            ctx.connections[args.alias] = conn
        except Exception as exc:  # introspection is best-effort; never fail the attach
            catalog_note = f" (schema not indexed: {exc})"
        return _Output(
            ok=True,
            alias=args.alias,
            message=(
                f"Attached {args.path!r} as {args.alias}. "
                f"Reference its tables as {args.alias}.<table_name>.{catalog_note}"
            ),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_attach_database_catalog.py -v`
Expected: PASS

- [ ] **Step 5: Regression — existing attach + suite tests still green**

Run: `uv run pytest tests/unit/test_dab_env.py tests/unit/test_claude_mcp_prompt.py -q`
Expected: PASS (no behavioral change to prompt/env yet).

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/tools/attach_database.py tests/unit/test_attach_database_catalog.py
git commit -m "feat(tools): attach_database indexes the attached catalog into ctx"
```

---

## Task 3: `describe_table`/`list_tables` resolve the dotted `alias.table` form

**Files:**
- Modify: `src/labrat/agent/tools/describe_table.py:224-227`
- Test: extend `tests/unit/test_describe_table_column_hints.py` (or new `tests/unit/test_describe_table_attached.py`)

**Interfaces:**
- Consumes: `ctx.catalogs[alias]` populated by Task 2.
- Produces: `describe_table(table="clinical_database.clinical_info")` resolves whether or not `database` is passed, by trying the catalog named by the dotted prefix and matching the bare table name.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_describe_table_attached.py
from pathlib import Path

from labrat.agent.tools.attach_database import AttachDatabaseTool
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.describe_table import DescribeTableTool
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection


async def test_describe_table_resolves_attached_dotted_name(tmp_path: Path) -> None:
    secondary = tmp_path / "clinical.duckdb"
    c = DuckDBConnection(path=str(secondary), read_only=False)
    c.connect()
    c._connection.execute(  # pyright: ignore[reportPrivateUsage]
        "CREATE TABLE clinical_info (icd_o_3_histology VARCHAR, histological_type VARCHAR)"
    )
    c.disconnect()

    primary = DuckDBConnection(path=":memory:", read_only=False)
    primary.connect()
    ctx = ToolContext(
        connections={"main": primary},
        catalogs={"main": Catalog(database_name="main", schemas=[])},
        primary="main",
    )
    attach = AttachDatabaseTool()
    await attach.execute(
        ctx,
        attach.input_model(path=str(secondary), alias="clinical_database", db_type="duckdb"),
    )

    describe = DescribeTableTool()
    # dotted form, no explicit database=
    out = await describe.execute(ctx, describe.input_model(table="clinical_database.clinical_info"))
    assert out.table_name == "clinical_info"
    assert {c.name for c in out.columns} == {"icd_o_3_histology", "histological_type"}
    primary.disconnect()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_describe_table_attached.py -v`
Expected: FAIL — `ValueError: Table 'clinical_database.clinical_info' not found in catalog` (looked up in `main`).

- [ ] **Step 3: Write minimal implementation**

Replace the head of `describe_table.py` `execute` (currently lines 224-227):

```python
    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        database = args.database
        table_name = args.table
        # Agents often pass the DuckDB-qualified "alias.table" form. If a dotted
        # prefix names a known catalog, route there and match the bare table name.
        if "." in table_name:
            prefix, _, rest = table_name.partition(".")
            if prefix in ctx.catalogs:
                database = database or prefix
                table_name = rest
        catalog = cast(Catalog, ctx.catalogs[database or ctx.primary])
        table = catalog.find_table(table_name)
        if table is None:
            raise ValueError(f"Table {args.table!r} not found in catalog")
```

Then update the two downstream references that used `args.table`/`args.database`:
- the `find_table` call now uses `table_name` (done above);
- the final `column_hints=self._column_hints(ctx, args.database, table)` → `self._column_hints(ctx, database, table)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_describe_table_attached.py -v`
Expected: PASS

- [ ] **Step 5: Regression — column-hint tests unchanged**

Run: `uv run pytest tests/unit/test_describe_table_column_hints.py -q`
Expected: PASS (dotted-prefix branch is inert when no dot / unknown prefix).

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/tools/describe_table.py tests/unit/test_describe_table_attached.py
git commit -m "feat(tools): describe_table resolves attached alias.table dotted names"
```

---

## Task 4: env.py emits secondary DuckDBs as attachable (GAP 2)

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/env.py:36-48` (`AttachSpec.db_type`), `env.py:74-133` (`build_dab_task_env`)
- Test: extend `tests/unit/test_dab_env.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `build_dab_task_env` keeps `duckdb[0]` as primary and emits every *other* duckdb client as `AttachSpec(alias=name, path=<abs duckdb path>, db_type="duckdb")`. `AttachSpec.db_type` Literal is `"sqlite" | "postgres" | "mysql" | "duckdb"`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_dab_env.py
def test_secondary_duckdb_becomes_attachable(tmp_path: Path) -> None:
    (tmp_path / "primary.duckdb").touch()
    (tmp_path / "activities.duckdb").touch()
    config = tmp_path / "db_config.yaml"
    config.write_text(
        "db_clients:\n"
        "  sales_pipeline: {db_type: duckdb, db_path: primary.duckdb}\n"
        "  activities: {db_type: duckdb, db_path: activities.duckdb}\n"
    )
    env = build_dab_task_env(config)
    assert env.ctx.primary == "sales_pipeline"
    aliases = {s.alias: s.db_type for s in env.attachable}
    assert aliases == {"activities": "duckdb"}
    assert env.attachable[0].path.endswith("activities.duckdb")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_env.py::test_secondary_duckdb_becomes_attachable -v`
Expected: FAIL — `activities` is dropped, `env.attachable == []`.

- [ ] **Step 3: Widen `AttachSpec.db_type`**

In `env.py`, change:

```python
    db_type: Literal["sqlite", "postgres", "mysql", "duckdb"]
```

- [ ] **Step 4: Emit secondary DuckDBs in `build_dab_task_env`**

In the `for name, spec in clients.items()` loop, change the `duckdb` branch to only make the FIRST duckdb a primary connection and route the rest to attachable:

```python
        if db_type == "duckdb":
            db_path = dataset_dir / str(spec["db_path"])
            if not file_backed_duckdb:
                connections[name] = DuckDBConnection(path=db_path)
                file_backed_duckdb.append(name)
            else:
                # Secondary DuckDB — DuckDB can ATTACH another .duckdb file
                # (TYPE DUCKDB). Route it through the same attach path as SQLite
                # instead of dropping it.
                attachable.append(
                    AttachSpec(alias=name, path=str(db_path), db_type="duckdb")
                )
                file_backed_duckdb.append(name)
```

Note: `file_backed_duckdb.append(name)` in the else branch keeps the "if no DuckDB primary" logic correct but must NOT make a second primary — only `connections[...]` for the first. Verify `primary = file_backed_duckdb[0]` still picks the first.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_dab_env.py -q`
Expected: PASS (new test + all existing env tests).

- [ ] **Step 6: Widen the `attach_database` tool input Literal**

In `src/labrat/agent/tools/attach_database.py`:

```python
    db_type: Literal["sqlite", "postgres", "mysql", "duckdb"] = Field(
        description="Database type of the file being attached.",
    )
```

(`DuckDBConnection.attach` already emits `ATTACH '...' AS alias (TYPE DUCKDB)` via `db_type.upper()` — verified working against DuckDB in this repo.)

- [ ] **Step 7: End-to-end attach test for duckdb type**

Add to `tests/unit/test_attach_database_catalog.py`:

```python
async def test_attach_duckdb_type_end_to_end(tmp_path: Path) -> None:
    secondary = tmp_path / "activities.duckdb"
    c = DuckDBConnection(path=str(secondary), read_only=False)
    c.connect()
    c._connection.execute(  # pyright: ignore[reportPrivateUsage]
        "CREATE TABLE VoiceCallTranscript__c (LeadId__c VARCHAR, Body__c VARCHAR)"
    )
    c.disconnect()

    primary = DuckDBConnection(path=":memory:", read_only=False)
    primary.connect()
    ctx = ToolContext(
        connections={"sales_pipeline": primary},
        catalogs={"sales_pipeline": Catalog(database_name="sales_pipeline", schemas=[])},
        primary="sales_pipeline",
    )
    tool = AttachDatabaseTool()
    out = await tool.execute(
        ctx, tool.input_model(path=str(secondary), alias="activities", db_type="duckdb")
    )
    assert out.ok
    assert ctx.catalogs["activities"].find_table("VoiceCallTranscript__c") is not None
    primary.disconnect()
```

Run: `uv run pytest tests/unit/test_attach_database_catalog.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/env.py src/labrat/agent/tools/attach_database.py tests/unit/test_dab_env.py tests/unit/test_attach_database_catalog.py
git commit -m "feat(dab): mount secondary DuckDB databases via attach (TYPE DUCKDB)"
```

---

## Task 5: Server-side Context Ledger for the MCP path (GAP 3, opt-in)

**Files:**
- Modify: `src/labrat/mcp/server.py` (`_build_server`, `_call_tool`, `_list_tools`)
- Test: `tests/unit/test_mcp_server_ledger.py`

**Interfaces:**
- Consumes: `ContextLedger`, `LedgerBudget` (`labrat.runtime.context_ledger`); `ResultStore` (`labrat.results.store`); `render` (`labrat.agent.tools.serialization`).
- Produces: when `LABRAT_MCP_LEDGER=1`, oversized tool payloads are replaced by `render(ledger.record(...))` (a `[context ledger] summary + artifact_ref + preview` block), and a synthetic `get_artifact` tool returns `store.preview(ref)`. When the env flag is unset, `_call_tool` returns exactly today's payload and `get_artifact` is not listed.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mcp_server_ledger.py
from pathlib import Path
from typing import Any

from labrat.agent.tools.base import DispatchResult, ToolContext, ToolRegistry
from labrat.mcp import server as mcp_server


class _BigResult:
    def model_dump_json(self) -> str:
        return "x" * 50_000


def test_ledger_truncates_oversized_payload(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("LABRAT_MCP_LEDGER", "1")
    monkeypatch.setenv("LABRAT_MCP_RESULT_STORE_DIR", str(tmp_path))

    text = mcp_server._render_payload_via_ledger(  # helper under test
        store_dir=tmp_path,
        tool_name="run_sql",
        payload="y" * 50_000,
    )
    assert "[context ledger]" in text
    assert "artifact_ref:" in text
    assert len(text) < 20_000
```

(If you prefer an integration test over the whole `_call_tool`, build a `ToolRegistry` with a stub tool returning `_BigResult`; the helper-level test above is the minimal unit.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_mcp_server_ledger.py -v`
Expected: FAIL — `AttributeError: module 'labrat.mcp.server' has no attribute '_render_payload_via_ledger'`

- [ ] **Step 3: Add the ledger helper + wire it into `_call_tool`**

In `src/labrat/mcp/server.py`, add imports:

```python
from labrat.agent.tools.serialization import ModelVisibleToolResult, render
from labrat.results.store import ResultStore
from labrat.runtime.context_ledger import ContextLedger, LedgerBudget
```

Add a module-level helper (JSON/string payloads only — the MCP boundary already serialized the tool value to a string, so use the fallback byte-bounding path via a synthetic DispatchResult is overkill; store the raw string):

```python
def _render_payload_via_ledger(*, store_dir: Path, tool_name: str, payload: str) -> str:
    """Bound an oversized MCP tool payload: store the full text, return a preview block.

    MCP serialized the tool value to a string already, so the ledger's typed
    table/json hooks aren't reachable here — we bound by bytes and stash the full
    string as a json artifact the model can pull with get_artifact.
    """
    budget = LedgerBudget()
    if len(payload.encode("utf-8")) <= budget.max_bytes:
        return payload
    store = ResultStore(store_dir)
    ref = store.put_json(payload, kind="json")
    mv = ModelVisibleToolResult(
        summary=f"{tool_name}: {len(payload.encode('utf-8'))}-byte payload stored; "
        f"pull full via get_artifact({ref}).",
        preview=payload[: budget.max_bytes],
        artifact_ref=ref,
        truncated=True,
    )
    return render(mv)
```

In `_build_server`, read the flag once:

```python
    ledger_on = os.environ.get("LABRAT_MCP_LEDGER") == "1"
    store_dir = Path(os.environ.get("LABRAT_MCP_RESULT_STORE_DIR", "")) or None
```

In `_call_tool`, after computing `payload` on the success path, before `_log_tool_call`:

```python
        if ledger_on and store_dir is not None:
            payload = _render_payload_via_ledger(
                store_dir=store_dir, tool_name=name, payload=payload
            )
```

(Keep `_log_tool_call` writing the — now possibly bounded — `payload`, so traces mirror what the model saw. If you want the full payload in traces, log before the ledger call; note the choice in the commit.)

- [ ] **Step 4: Add the `get_artifact` MCP-local tool + handler**

In `_list_tools`, when `ledger_on`, append one synthetic tool:

```python
        if ledger_on:
            out.append(
                Tool(
                    name="get_artifact",
                    description="Retrieve a stored tool-result artifact by ref (e.g. 'r3'). "
                    "Returns a bounded preview of the full payload the ledger stored.",
                    inputSchema={
                        "type": "object",
                        "properties": {"ref": {"type": "string"}},
                        "required": ["ref"],
                    },
                )
            )
```

At the TOP of `_call_tool`, intercept before `registry.dispatch`:

```python
        if name == "get_artifact":
            ref = str(arguments.get("ref", ""))
            if store_dir is None:
                return [TextContent(type="text", text="Error: no result store configured")]
            try:
                text = ResultStore(store_dir).preview(ref)
            except Exception as exc:
                text = f"Error: {exc}"
            return [TextContent(type="text", text=text)]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_mcp_server_ledger.py tests/unit/test_mcp_server.py -q`
Expected: PASS. Confirm the OFF path is untouched:

```python
def test_ledger_off_is_byte_identical(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.delenv("LABRAT_MCP_LEDGER", raising=False)
    text = mcp_server._render_payload_via_ledger(
        store_dir=tmp_path, tool_name="run_sql", payload="z" * 50_000
    )
    # helper still bounds when called directly, but _call_tool only calls it when
    # ledger_on — assert the flag gate in _build_server, not the helper.
```

(Prefer asserting the gate via a small `_call_tool` integration with `ledger_on=False` returning the raw 50k payload.)

- [ ] **Step 6: Commit**

```bash
git add src/labrat/mcp/server.py tests/unit/test_mcp_server_ledger.py
git commit -m "feat(mcp): opt-in server-side Context Ledger + get_artifact retrieval"
```

---

## Task 6: Wire the DAB claude-mcp driver to the new capabilities

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` (`_run_trial_claude_mcp` mcp_config env + `--allowedTools`)
- Test: extend `tests/unit/test_claude_mcp_prompt.py` / `test_dab_suite_run_trial.py` as applicable

**Interfaces:**
- Consumes: `LABRAT_MCP_LEDGER`, `LABRAT_MCP_RESULT_STORE_DIR` (Task 5); the widened attachable list (Task 4) flows through the existing prompt builder unchanged.
- Produces: an optional `--agent-ledger` suite flag (default OFF, mirroring `--agent-cartograph`) that sets the two env vars in the `mcpServers.labrat.env` block and adds `get_artifact` to the allowed tools. GAP 1/GAP 2 need NO suite change — they ride the existing attach path.

- [ ] **Step 1: Confirm GAP 1/GAP 2 need no suite wiring (test)**

The claude-mcp prompt already enumerates `env_spec.attachable`; Task 4 makes the secondary DuckDB appear there. Add an assertion:

```python
# tests/unit/test_claude_mcp_prompt.py
def test_prompt_lists_secondary_duckdb(tmp_path: Path) -> None:
    ...  # build a DabTaskEnv with a duckdb AttachSpec, assert its alias/path/db_type
    # appears under "Secondary databases you can bring in via attach_database".
```

Run: `uv run pytest tests/unit/test_claude_mcp_prompt.py -q`
Expected: PASS after Task 4.

- [ ] **Step 2: Add the `--agent-ledger` flag (only if pursuing GAP 3 on-benchmark)**

Thread a `self._agent_ledger: bool` through the suite exactly like `self._cartograph` (constructor + `eval_dab.py` argparse `--agent-ledger` store_true, default False). In the `mcp_config` `env` dict, when set:

```python
                        **(
                            {
                                "LABRAT_MCP_LEDGER": "1",
                                "LABRAT_MCP_RESULT_STORE_DIR": str(scratch_dir / "results"),
                            }
                            if self._agent_ledger
                            else {}
                        ),
```

and when set, extend `--allowedTools` to include `mcp__labrat__get_artifact` (the get_artifact tool is exposed under the `labrat` server, so `mcp__labrat` already covers it — verify the CLI's prefix match; if `--allowedTools mcp__labrat` matches all server tools, no change needed).

- [ ] **Step 3: Full suite green + format/lint/types**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
```

Expected: all clean.

- [ ] **Step 4: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py scripts/eval_dab.py tests/unit/test_claude_mcp_prompt.py
git commit -m "feat(dab): optional --agent-ledger for claude-mcp; secondary DuckDB in prompt"
```

---

## Self-Review

**Spec coverage:**
- GAP 1 → Tasks 1–3 (introspection method, ctx population, dotted-name resolution). ✓
- GAP 2 → Task 4 (env emits secondary duckdb; tool accepts duckdb type). ✓
- GAP 3 → Task 5 (server-side ledger + get_artifact), Task 6 Step 2 (on-benchmark opt-in). ✓
- Driver wiring / no-regression → Task 6. ✓

**Type consistency:** `introspect_attached_catalog(alias) -> Catalog` used identically in Tasks 2/3. `AttachSpec.db_type` and `_Input.db_type` widened to the same 4-member Literal (Task 4). `_render_payload_via_ledger(store_dir, tool_name, payload) -> str` signature matches its test and call site.

**Placeholder scan:** every code step shows real code; no TBD/TODO. FK introspection is explicitly scoped out (empty list) rather than left vague.

**Known risks to watch during execution:**
1. `duckdb_columns()` column names (`is_nullable`, `column_index`) — confirm against the installed DuckDB in Step 4 of Task 1; adjust the SELECT if a name differs.
2. `column_stats`/`sample_rows` on an attached alias run `FROM <table>`; with `Table.schema_name = alias` the qualified path is `alias.table` (resolves), but a *bare* `database=alias` call that doesn't qualify may still miss — the four pure-catalog tools (the measured failures) are fully covered; note any residual in the PR.
3. GAP 3's ledger stores the already-stringified payload as JSON (not the typed table), so previews are text-bounded, not row-rendered — acceptable for the MCP boundary; flagged in the helper docstring.
