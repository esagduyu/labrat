"""run_sql ledger_payload hook: successful results expose their DataFrame."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.agent.tools.serialization import LedgerPayloadProvider
from labrat.db.duckdb_engine import DuckDBConnection


def _conn(tmp_path: Path) -> DuckDBConnection:
    p = str(tmp_path / "lp.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(a INT, b VARCHAR)")
    raw.execute("INSERT INTO t VALUES (1, 'x'), (2, 'y')")
    raw.close()
    c = DuckDBConnection(path=p, read_only=False)
    c.connect()
    return c


async def test_success_exposes_table_payload(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    tool = RunSqlTool()
    out = await tool.execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        tool.input_model(query="SELECT * FROM t ORDER BY a"),
    )
    assert out.ok
    assert isinstance(out, LedgerPayloadProvider)
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "table"
    assert isinstance(obj, pl.DataFrame)
    assert obj.height == 2
    assert obj.columns == ["a", "b"]
    conn.disconnect()


async def test_refused_mutation_has_no_payload(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    tool = RunSqlTool()
    out = await tool.execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        tool.input_model(query="DROP TABLE t"),
    )
    assert not out.ok
    assert out.ledger_payload() is None
    conn.disconnect()


async def test_sql_error_has_no_payload(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    tool = RunSqlTool()
    out = await tool.execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        tool.input_model(query="SELECT nope FROM t"),
    )
    assert not out.ok
    assert out.ledger_payload() is None
    conn.disconnect()
