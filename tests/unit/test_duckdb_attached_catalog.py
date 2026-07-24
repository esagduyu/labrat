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
