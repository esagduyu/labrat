"""Tests for CatalogManager (M30)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from labrat.catalog.base import CatalogAdapter, CatalogEntry
from labrat.context_engine.bundle import ContextBundle, TableContext

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "sample_dbt_project"


def _entry(name: str, description: str = "", tags: list[str] | None = None) -> CatalogEntry:
    return CatalogEntry(
        name=name,
        schema_name="public",
        description=description,
        columns={},
        tags=tags or [],
        upstream=[],
        downstream=[],
        row_count=None,
    )


class _StubAdapter(CatalogAdapter):
    def __init__(self, entries: dict[str, CatalogEntry]) -> None:
        self._entries = entries

    @property
    def name(self) -> str:
        return "stub"

    def load(self) -> dict[str, CatalogEntry]:
        return self._entries


# ── CatalogManager ────────────────────────────────────────────────────────────


def test_manager_loads_from_single_adapter() -> None:
    from labrat.catalog.manager import CatalogManager

    adapter = _StubAdapter({"orders": _entry("orders", "Order data")})
    mgr = CatalogManager([adapter])
    entries = mgr.load_all()
    assert "orders" in entries
    assert entries["orders"].description == "Order data"


def test_manager_merges_multiple_adapters() -> None:
    from labrat.catalog.manager import CatalogManager

    a1 = _StubAdapter({"orders": _entry("orders", "From adapter 1")})
    a2 = _StubAdapter({"payments": _entry("payments", "From adapter 2")})
    mgr = CatalogManager([a1, a2])
    entries = mgr.load_all()
    assert "orders" in entries
    assert "payments" in entries


def test_manager_later_adapter_wins_on_conflict() -> None:
    """When two adapters describe the same table, the last one wins."""
    from labrat.catalog.manager import CatalogManager

    a1 = _StubAdapter({"orders": _entry("orders", "First description")})
    a2 = _StubAdapter({"orders": _entry("orders", "Second description (authoritative)")})
    mgr = CatalogManager([a1, a2])
    entries = mgr.load_all()
    assert "Second description" in entries["orders"].description


def test_manager_merge_into_bundle_adds_catalog_description() -> None:
    from labrat.catalog.manager import CatalogManager

    adapter = _StubAdapter({"orders": _entry("orders", "Human-authored catalog description")})
    mgr = CatalogManager([adapter])

    bundle = ContextBundle(
        profile="dev",
        generated_at=datetime.now(tz=UTC),
        tables=[
            TableContext(
                table_name="orders",
                schema_name="public",
                description="LLM-inferred description",
            )
        ],
    )
    updated = mgr.merge_into_bundle(bundle)
    orders_ctx = next(t for t in updated.tables if t.table_name == "orders")
    assert orders_ctx.catalog_description == "Human-authored catalog description"


def test_manager_merge_into_bundle_adds_new_table_from_catalog() -> None:
    from labrat.catalog.manager import CatalogManager

    adapter = _StubAdapter({"payments": _entry("payments", "Payments table")})
    mgr = CatalogManager([adapter])

    bundle = ContextBundle(
        profile="dev",
        generated_at=datetime.now(tz=UTC),
        tables=[],
    )
    updated = mgr.merge_into_bundle(bundle)
    table_names = [t.table_name for t in updated.tables]
    assert "payments" in table_names


def test_manager_dbt_adapter_integration() -> None:
    """Integration: DbtLoader plugged into CatalogManager returns real entries."""
    from labrat.catalog.dbt.loader import DbtLoader
    from labrat.catalog.manager import CatalogManager

    loader = DbtLoader(project_path=FIXTURE_DIR)
    mgr = CatalogManager([loader])
    entries = mgr.load_all()
    assert "orders" in entries
    assert "stg_customers" in entries
