from __future__ import annotations

from labrat.agent.tools.run_sql import RunSqlTool
from labrat.db.duckdb_engine import DuckDBConnection


def test_normalize_text_registered(tmp_path) -> None:
    p = str(tmp_path / "t.duckdb")
    conn = DuckDBConnection(
        path=p, read_only=False
    )  # default read_only=True; need to create the file
    conn.connect()
    df = conn.execute("SELECT normalize_text('Café  Del-Mar!') AS n")
    assert df.row(0)[0] == "cafedelmar"
    conn.disconnect()


def test_normalize_text_works_read_only(tmp_path) -> None:
    p = str(tmp_path / "t.duckdb")
    seed = DuckDBConnection(path=p, read_only=False)
    seed.connect()
    seed._connection.execute("CREATE TABLE x(a INT); INSERT INTO x VALUES (1)")
    seed.disconnect()
    ro = DuckDBConnection(path=p, read_only=True)
    ro.connect()  # macro must register on read-only
    assert ro.execute("SELECT normalize_text('  A B c ')").row(0)[0] == "abc"
    ro.disconnect()


def test_run_sql_description_surfaces_normalize_text() -> None:
    # I3: normalize_text exists as a DuckDB macro but was otherwise undiscoverable to
    # the agent — the run_sql tool description is the spec-mandated surfacing point.
    assert "normalize_text" in RunSqlTool().description
