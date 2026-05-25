"""Unit tests for M-Schema serializer (Phase 2)."""

from __future__ import annotations

from labrat.catalog.base import CatalogEntry, ColumnEntry
from labrat.catalog.dbt.mschema import (
    _infer_fk_cols,
    _infer_pk_cols,
    _render_val,
    serialize_m_schema,
)


def _make_entry(name: str, cols: dict[str, str]) -> CatalogEntry:
    return CatalogEntry(
        name=name,
        columns={c: ColumnEntry(name=c, data_type=dt) for c, dt in cols.items()},
    )


def test_serialize_db_id_header() -> None:
    entries = {"orders": _make_entry("orders", {"order_id": "INTEGER"})}
    result = serialize_m_schema(entries, db_id="my_project")
    assert result.startswith("[DB_ID] my_project")


def test_serialize_table_header() -> None:
    entries = {"orders": _make_entry("orders", {"order_id": "INTEGER"})}
    result = serialize_m_schema(entries)
    assert "# Table orders" in result


def test_serialize_column_line() -> None:
    entries = {
        "orders": CatalogEntry(
            name="orders",
            columns={
                "status": ColumnEntry(
                    name="status", data_type="VARCHAR", description="Order status"
                )
            },
        )
    }
    result = serialize_m_schema(entries)
    assert "(status, VARCHAR, Order status)" in result


def test_serialize_pk_inferred() -> None:
    entries = {"orders": _make_entry("orders", {"orders_id": "INTEGER", "amount": "NUMERIC"})}
    result = serialize_m_schema(entries)
    assert "PK" in result


def test_serialize_fk_inferred() -> None:
    entries = {
        "orders": _make_entry("orders", {"orders_id": "INTEGER", "customer_id": "INTEGER"}),
        "customer": _make_entry("customer", {"customer_id": "INTEGER"}),
    }
    result = serialize_m_schema(entries)
    assert "FK" in result
    assert "[Foreign Keys]" in result
    assert "orders.customer_id → customer.customer_id" in result


def test_serialize_sample_values_included() -> None:
    entries = {"orders": _make_entry("orders", {"status": "VARCHAR"})}
    samples = {"orders": {"status": ["pending", "shipped", "delivered"]}}
    result = serialize_m_schema(entries, sample_values=samples)
    assert "'pending'" in result
    assert "'shipped'" in result


def test_serialize_sample_values_capped_at_5() -> None:
    entries = {"orders": _make_entry("orders", {"x": "INTEGER"})}
    samples = {"orders": {"x": [1, 2, 3, 4, 5, 6, 7]}}
    result = serialize_m_schema(entries, sample_values=samples)
    # Only first 5 values should appear
    assert "6" not in result
    assert "7" not in result


def test_serialize_selected_filters_models() -> None:
    entries = {
        "orders": _make_entry("orders", {"order_id": "INTEGER"}),
        "customers": _make_entry("customers", {"customer_id": "INTEGER"}),
    }
    result = serialize_m_schema(entries, selected=["orders"])
    assert "# Table orders" in result
    assert "# Table customers" not in result


def test_serialize_max_models_truncates() -> None:
    entries = {f"model_{i}": _make_entry(f"model_{i}", {"id": "INTEGER"}) for i in range(10)}
    result = serialize_m_schema(entries, max_models=3)
    assert result.count("# Table") == 3


def test_render_val_null() -> None:
    assert _render_val(None) == "NULL"


def test_render_val_string() -> None:
    assert _render_val("shipped") == "'shipped'"


def test_render_val_number() -> None:
    assert _render_val(42) == "42"


def test_infer_pk_cols_stem_match() -> None:
    entry = _make_entry("stg_orders", {"orders_id": "INTEGER", "amount": "NUMERIC"})
    pks = _infer_pk_cols("stg_orders", entry)
    assert "orders_id" in pks


def test_infer_fk_cols_matches_existing_model() -> None:
    entry = _make_entry("orders", {"orders_id": "INTEGER", "customer_id": "INTEGER"})
    all_entries = {
        "orders": entry,
        "customer": _make_entry("customer", {"customer_id": "INTEGER"}),
    }
    fks = _infer_fk_cols("orders", entry, all_entries)
    assert "customer_id" in fks
    assert fks["customer_id"] == "customer.customer_id"
