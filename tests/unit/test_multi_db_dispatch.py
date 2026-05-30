"""Multi-DB routing: tools with a `database` arg pick the named connection."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb
import pytest

from labrat.agent.tools.attach_database import AttachDatabaseTool
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.list_tables import ListTablesTool
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.db.duckdb_engine import DuckDBConnection


@pytest.fixture()
def two_dbs(tmp_path: Path) -> dict[str, Path]:
    main = tmp_path / "main.duckdb"
    aux = tmp_path / "aux.duckdb"
    con = duckdb.connect(str(main))
    con.execute("CREATE TABLE widgets (id INTEGER, name VARCHAR);")
    con.execute("INSERT INTO widgets VALUES (1, 'sprocket'), (2, 'flange');")
    con.close()
    con = duckdb.connect(str(aux))
    con.execute("CREATE TABLE gizmos (id INTEGER, label VARCHAR);")
    con.execute("INSERT INTO gizmos VALUES (10, 'alpha'), (20, 'beta'), (30, 'gamma');")
    con.close()
    return {"main": main, "aux": aux}


@pytest.fixture()
def multi_ctx(two_dbs: dict[str, Path]) -> ToolContext:  # type: ignore[misc]
    main_conn = DuckDBConnection(two_dbs["main"], read_only=True)
    aux_conn = DuckDBConnection(two_dbs["aux"], read_only=True)
    main_conn.connect()
    aux_conn.connect()
    catalogs = {
        "main": main_conn.introspect_catalog(),
        "aux": aux_conn.introspect_catalog(),
    }
    ctx = ToolContext(
        connections={"main": main_conn, "aux": aux_conn},
        catalogs=catalogs,
        primary="main",
    )
    yield ctx  # type: ignore[misc]
    main_conn.disconnect()
    aux_conn.disconnect()


async def test_run_sql_defaults_to_primary(multi_ctx: ToolContext) -> None:
    tool = RunSqlTool()
    result = await tool.execute(multi_ctx, tool.input_model(query="SELECT COUNT(*) FROM widgets"))
    assert result.ok is True
    assert result.rows is not None
    assert result.rows[0][0] == "2"


async def test_run_sql_routes_to_named_aux(multi_ctx: ToolContext) -> None:
    tool = RunSqlTool()
    result = await tool.execute(
        multi_ctx,
        tool.input_model(query="SELECT COUNT(*) FROM gizmos", database="aux"),
    )
    assert result.ok is True
    assert result.rows is not None
    assert result.rows[0][0] == "3"


async def test_run_sql_primary_cannot_see_aux_tables(multi_ctx: ToolContext) -> None:
    tool = RunSqlTool()
    result = await tool.execute(multi_ctx, tool.input_model(query="SELECT COUNT(*) FROM gizmos"))
    assert result.ok is False
    assert result.error is not None


async def test_list_tables_routes_to_named_catalog(multi_ctx: ToolContext) -> None:
    tool = ListTablesTool()
    primary = await tool.execute(multi_ctx, tool.input_model())
    aux = await tool.execute(multi_ctx, tool.input_model(database="aux"))
    primary_names = {t.name for t in primary.tables}
    aux_names = {t.name for t in aux.tables}
    assert "widgets" in primary_names
    assert "gizmos" in aux_names
    assert "gizmos" not in primary_names
    assert "widgets" not in aux_names


# ── attach_database ──────────────────────────────────────────────────────────


@pytest.fixture()
def attach_ctx(tmp_path: Path) -> ToolContext:  # type: ignore[misc]
    sqlite_path = tmp_path / "ext.sqlite"
    sql_con = sqlite3.connect(str(sqlite_path))
    sql_con.execute("CREATE TABLE planets (id INTEGER, name TEXT);")
    sql_con.execute("INSERT INTO planets VALUES (1, 'mercury'), (2, 'venus'), (3, 'earth');")
    sql_con.commit()
    sql_con.close()

    duck_path = tmp_path / "primary.duckdb"
    duck = duckdb.connect(str(duck_path))
    duck.execute("CREATE TABLE moons (planet_id INTEGER, name VARCHAR);")
    duck.execute("INSERT INTO moons VALUES (3, 'luna');")
    duck.close()

    primary = DuckDBConnection(duck_path, read_only=True)
    primary.connect()
    ctx = ToolContext(
        connections={"primary": primary},
        catalogs={"primary": primary.introspect_catalog()},
        primary="primary",
    )
    ctx.attach_path = sqlite_path  # type: ignore[attr-defined]
    yield ctx  # type: ignore[misc]
    primary.disconnect()


async def test_attach_database_makes_sqlite_tables_queryable(attach_ctx: ToolContext) -> None:
    attach = AttachDatabaseTool()
    sqlite_path = str(attach_ctx.attach_path)  # type: ignore[attr-defined]
    res = await attach.execute(
        attach_ctx,
        attach.input_model(path=sqlite_path, alias="ext", db_type="sqlite"),
    )
    assert res.ok is True, res.message

    run = RunSqlTool()
    out = await run.execute(
        attach_ctx,
        run.input_model(query="SELECT COUNT(*) FROM ext.planets"),
    )
    assert out.ok is True, out.error
    assert out.rows is not None
    assert out.rows[0][0] == "3"


async def test_attach_database_enables_cross_db_join(attach_ctx: ToolContext) -> None:
    attach = AttachDatabaseTool()
    sqlite_path = str(attach_ctx.attach_path)  # type: ignore[attr-defined]
    await attach.execute(
        attach_ctx,
        attach.input_model(path=sqlite_path, alias="ext", db_type="sqlite"),
    )

    run = RunSqlTool()
    out = await run.execute(
        attach_ctx,
        run.input_model(
            query=(
                "SELECT p.name AS planet, m.name AS moon "
                "FROM ext.planets p JOIN moons m ON m.planet_id = p.id"
            )
        ),
    )
    assert out.ok is True, out.error
    assert out.rows is not None
    assert len(out.rows) == 1
    assert out.rows[0] == ["earth", "luna"]


async def test_attach_database_rejects_non_duckdb_primary() -> None:
    """attach_database should refuse when the primary connection isn't DuckDB."""

    class _FakeConn:
        pass

    ctx = ToolContext(
        connections={"primary": _FakeConn()},
        catalogs={"primary": None},
        primary="primary",
    )
    attach = AttachDatabaseTool()
    res = await attach.execute(
        ctx, attach.input_model(path="/tmp/x.db", alias="ext", db_type="sqlite")
    )
    assert res.ok is False
    assert "DuckDB" in res.message
