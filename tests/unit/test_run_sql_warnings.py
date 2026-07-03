from __future__ import annotations

import duckdb

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.db.duckdb_engine import DuckDBConnection


def _conn(tmp_path):
    p = str(tmp_path / "w.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(a INT, b INT)")
    raw.execute("INSERT INTO t VALUES (1, NULL), (2, NULL)")
    raw.close()
    c = DuckDBConnection(path=p, read_only=False)
    c.connect()
    return c


async def test_empty_result_when_filtered_warns(tmp_path) -> None:
    conn = _conn(tmp_path)
    out = await RunSqlTool().execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        RunSqlTool().input_model(query="SELECT * FROM t WHERE a = 999"),
    )
    assert out.ok
    assert any("0 rows" in w.lower() or "empty" in w.lower() for w in out.warnings)
    conn.disconnect()


async def test_all_null_column_warns(tmp_path) -> None:
    conn = _conn(tmp_path)
    out = await RunSqlTool().execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        RunSqlTool().input_model(query="SELECT a, b FROM t"),
    )
    assert out.ok
    assert any("b" in w and "null" in w.lower() for w in out.warnings)
    conn.disconnect()


async def test_clean_query_no_warnings(tmp_path) -> None:
    conn = _conn(tmp_path)
    out = await RunSqlTool().execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        RunSqlTool().input_model(query="SELECT a FROM t"),
    )
    assert out.ok and out.warnings == []
    conn.disconnect()
