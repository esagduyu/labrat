"""attach_database populates ctx.catalogs/ctx.connections for the attached alias.

NOTE: the brief's reference test attaches a DuckDB secondary via
``db_type="duckdb"``, but the tool's ``_Input.db_type`` Literal is currently
``["sqlite", "postgres", "mysql"]`` (the "duckdb" literal is added in Task 4).
To keep this task scoped to Task 2's interface (ctx.catalogs/ctx.connections
population) without widening db_type early, the secondary here is a SQLite
file attached with ``db_type="sqlite"``. The assertions on
``ctx.catalogs``/``ctx.connections`` are unchanged from the brief. Task 4 adds
the "duckdb" literal plus a duckdb-type end-to-end test.
"""

import sqlite3
from pathlib import Path

from labrat.agent.tools.attach_database import AttachDatabaseTool
from labrat.agent.tools.base import ToolContext
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection


async def test_attach_registers_catalog_and_connection(tmp_path: Path) -> None:
    secondary = tmp_path / "clinical.db"
    sconn = sqlite3.connect(secondary)
    sconn.execute("CREATE TABLE clinical_info (icd_o_3_histology VARCHAR)")
    sconn.commit()
    sconn.close()

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
        tool.input_model(path=str(secondary), alias="clinical_database", db_type="sqlite"),
    )
    assert out.ok
    assert "clinical_database" in ctx.catalogs
    assert ctx.catalogs["clinical_database"].find_table("clinical_info") is not None
    assert ctx.connections["clinical_database"] is primary
    primary.disconnect()
