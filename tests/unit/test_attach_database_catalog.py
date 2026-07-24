"""attach_database populates ctx.catalogs/ctx.connections for the attached alias.

NOTE: ``test_attach_registers_catalog_and_connection`` predates the "duckdb"
``db_type`` literal (added in Task 4) and still attaches a SQLite secondary to
cover that path; it is left as-is. ``test_attach_duckdb_type_end_to_end``
below covers the ``db_type="duckdb"`` path added in Task 4.
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


async def test_attach_duckdb_type_end_to_end(tmp_path: Path) -> None:
    secondary = tmp_path / "activities.duckdb"
    c = DuckDBConnection(path=str(secondary), read_only=False)
    c.connect()
    c._connection.execute(  # pyright: ignore[reportPrivateUsage]
        "CREATE TABLE VoiceCallTranscript__c (LeadId__c VARCHAR, Body__c VARCHAR)"
    )
    c.disconnect()

    # Real DAB primaries are read-only, file-backed DuckDB connections
    # (build_dab_task_env's `DuckDBConnection(path=db_path)` defaults read_only=True) —
    # exercise that configuration rather than an in-memory writable primary. DuckDB
    # allows ATTACH from a read-only session (verified empirically).
    primary_path = tmp_path / "sales_pipeline.duckdb"
    setup = DuckDBConnection(path=str(primary_path), read_only=False)
    setup.connect()
    setup.disconnect()

    primary = DuckDBConnection(path=str(primary_path), read_only=True)
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
