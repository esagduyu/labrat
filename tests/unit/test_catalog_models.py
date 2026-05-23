"""Unit tests for catalog Pydantic models."""

from labrat.db.catalog import Catalog, Column, ColumnStats, ForeignKey, Schema, Table


def test_table_qualified_name() -> None:
    col = Column(name="id", data_type="INTEGER", nullable=False)
    table = Table(name="orders", schema_name="public", columns=[col])
    assert table.qualified_name == "public.orders"


def test_catalog_find_table_found() -> None:
    col = Column(name="id", data_type="INTEGER", nullable=False)
    table = Table(name="orders", schema_name="main", columns=[col])
    schema = Schema(name="main", tables=[table])
    catalog = Catalog(database_name="test", schemas=[schema])

    found = catalog.find_table("orders")
    assert found is not None
    assert found.name == "orders"


def test_catalog_find_table_with_schema() -> None:
    col = Column(name="id", data_type="INTEGER", nullable=False)
    table = Table(name="users", schema_name="public", columns=[col])
    schema = Schema(name="public", tables=[table])
    catalog = Catalog(database_name="test", schemas=[schema])

    assert catalog.find_table("users", schema_name="public") is not None
    assert catalog.find_table("users", schema_name="private") is None


def test_catalog_find_table_missing() -> None:
    catalog = Catalog(database_name="test", schemas=[])
    assert catalog.find_table("nonexistent") is None


def test_foreign_key_frozen() -> None:
    fk = ForeignKey(column="region_id", referenced_table="regions", referenced_column="id")
    assert fk.column == "region_id"


def test_column_stats_model() -> None:
    stats = ColumnStats(
        column_name="total_amount",
        table_name="orders",
        data_type="DECIMAL",
        null_count=0,
        distinct_count=5,
        min_value="88.50",
        max_value="3150.00",
    )
    assert stats.null_count == 0
    assert stats.distinct_count == 5
