"""Join-transform detection (M0 Deterministic Data-Intelligence Pack, task 3)."""

from __future__ import annotations

import duckdb

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import build_join_keys


def _conn(tmp_path):
    p = str(tmp_path / "j.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE customers(id VARCHAR, name VARCHAR)")
    raw.execute("INSERT INTO customers VALUES ('12345','a'),('67890','b')")
    raw.execute("CREATE TABLE orders(customer_id VARCHAR, amt INT)")
    # No leading zeros in the digit run: extract-digits on 'CUST-12345' -> '12345',
    # matching customers.id exactly. (A zero-padded id like 'CUST-0012345' would extract
    # to '0012345' and NOT match '12345' as a string — that's a different transform.)
    raw.execute("INSERT INTO orders VALUES ('CUST-12345',10),('CUST-67890',20)")
    raw.close()
    c = DuckDBConnection(path=p)
    c.connect()
    return c


async def test_detects_extract_digits_transform(tmp_path) -> None:
    conn = _conn(tmp_path)
    # profile the two tables via the profiler so field shapes match generate_scent
    catalog = conn.introspect_catalog()
    prof = await ProfileDatasetTool().execute(
        ToolContext(connection=conn, catalog=catalog, primary="main"),
        ProfileDatasetTool().input_model(sample_rows=0, max_tables=100),
    )
    # orders.customer_id -> customers.id needs digit-extraction; raw match is 0
    section = build_join_keys(prof, conn, verified=[])
    assert section is not None
    assert "orders" in section.body and "customers" in section.body
    assert "[^0-9]" in section.body  # emits the extract-digits normalization SQL
    conn.disconnect()
