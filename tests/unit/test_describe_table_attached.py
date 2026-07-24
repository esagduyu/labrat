"""describe_table resolves the dotted "alias.table" form for an attached catalog.

NOTE: the brief's reference test attaches a DuckDB secondary via
``db_type="duckdb"``, but the tool's ``_Input.db_type`` Literal is currently
``["sqlite", "postgres", "mysql"]`` (the "duckdb" literal is added in Task 4).
To keep this task scoped to Task 3's interface (dotted-name resolution against
``ctx.catalogs``) without widening db_type early, the secondary here is a
SQLite file attached with ``db_type="sqlite"`` — same substitution Task 2 made
in test_attach_database_catalog.py. The describe_table assertions (dotted
``clinical_database.clinical_info`` resolution, same column names) are
unchanged from the brief.
"""

import sqlite3
from pathlib import Path

from labrat.agent.tools.attach_database import AttachDatabaseTool
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.describe_table import DescribeTableTool
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection


async def test_describe_table_resolves_attached_dotted_name(tmp_path: Path) -> None:
    secondary = tmp_path / "clinical.db"
    sconn = sqlite3.connect(secondary)
    sconn.execute(
        "CREATE TABLE clinical_info (icd_o_3_histology VARCHAR, histological_type VARCHAR)"
    )
    sconn.commit()
    sconn.close()

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
        attach.input_model(path=str(secondary), alias="clinical_database", db_type="sqlite"),
    )

    describe = DescribeTableTool()
    # dotted form, no explicit database=
    out = await describe.execute(ctx, describe.input_model(table="clinical_database.clinical_info"))
    assert out.table_name == "clinical_info"
    assert {c.name for c in out.columns} == {"icd_o_3_histology", "histological_type"}
    primary.disconnect()
