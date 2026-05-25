"""Phase 2 loader tests: compiled_sql extraction and catalog column type enrichment."""

from __future__ import annotations

from pathlib import Path

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "sample_dbt_project"


def test_loader_extracts_compiled_sql_from_manifest() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    loader = DbtLoader(project_path=FIXTURE_DIR)
    entries = loader.load()
    assert "orders" in entries
    assert "SELECT" in entries["orders"].compiled_sql
    assert "stg_orders" in entries["orders"].compiled_sql


def test_loader_compiled_sql_empty_when_not_in_manifest() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    loader = DbtLoader(project_path=FIXTURE_DIR)
    entries = loader.load()
    # stg_orders node has no compiled_code in fixture
    assert entries["stg_orders"].compiled_sql == ""


def test_loader_enriches_column_type_from_catalog_json() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    catalog_json = FIXTURE_DIR / "catalog.json"
    loader = DbtLoader(project_path=FIXTURE_DIR, catalog_json=catalog_json)
    entries = loader.load()
    # shipped_at has empty data_type in manifest but TIMESTAMP in catalog.json
    shipped_at = entries["orders"].columns.get("shipped_at")
    assert shipped_at is not None
    assert shipped_at.data_type == "TIMESTAMP"


def test_loader_does_not_overwrite_existing_type_with_catalog() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    catalog_json = FIXTURE_DIR / "catalog.json"
    loader = DbtLoader(project_path=FIXTURE_DIR, catalog_json=catalog_json)
    entries = loader.load()
    # order_id already has data_type=integer in manifest — catalog should not overwrite
    order_id = entries["orders"].columns.get("order_id")
    assert order_id is not None
    assert order_id.data_type == "integer"  # manifest value preserved
