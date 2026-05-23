"""Tests for dbt catalog loader (M30)."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "sample_dbt_project"


# ── DbtLoader ─────────────────────────────────────────────────────────────────


def test_loader_parses_manifest_nodes() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    loader = DbtLoader(project_path=FIXTURE_DIR)
    entries = loader.load()
    assert "orders" in entries
    assert "stg_orders" in entries
    assert "stg_customers" in entries


def test_loader_skips_non_model_nodes() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    loader = DbtLoader(project_path=FIXTURE_DIR)
    entries = loader.load()
    # The fixture has a test node; it must not appear in entries
    for name in entries:
        assert not name.startswith("not_null"), f"test node leaked: {name}"


def test_loader_extracts_model_description() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    loader = DbtLoader(project_path=FIXTURE_DIR)
    entries = loader.load()
    assert "transactional order data" in entries["orders"].description


def test_loader_extracts_column_metadata() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    loader = DbtLoader(project_path=FIXTURE_DIR)
    entries = loader.load()
    col = entries["orders"].columns.get("order_id")
    assert col is not None
    assert "unique order identifier" in col.description


def test_loader_detects_pii_columns() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    loader = DbtLoader(project_path=FIXTURE_DIR)
    entries = loader.load()
    email_col = entries["stg_customers"].columns.get("email")
    assert email_col is not None
    assert email_col.is_pii is True
    # non-PII column
    order_id_col = entries["orders"].columns.get("order_id")
    assert order_id_col is not None
    assert order_id_col.is_pii is False


def test_loader_builds_upstream_lineage() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    loader = DbtLoader(project_path=FIXTURE_DIR)
    entries = loader.load()
    upstream = entries["orders"].upstream
    assert "stg_orders" in upstream
    assert "stg_customers" in upstream


def test_loader_enriches_row_count_from_catalog_json() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    catalog_json = FIXTURE_DIR / "catalog.json"
    loader = DbtLoader(project_path=FIXTURE_DIR, catalog_json=catalog_json)
    entries = loader.load()
    assert entries["orders"].row_count == 99


def test_loader_no_row_count_without_catalog_json() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    loader = DbtLoader(project_path=FIXTURE_DIR)
    entries = loader.load()
    assert entries["orders"].row_count is None


def test_loader_falls_back_to_schema_yml(tmp_path: Path) -> None:
    """When no manifest.json exists, loader parses schema.yml files."""
    from labrat.catalog.dbt.loader import DbtLoader

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    schema = models_dir / "schema.yml"
    schema.write_text(
        "version: 2\nmodels:\n"
        "  - name: payments\n"
        "    description: Payment records.\n"
        "    columns:\n"
        "      - name: payment_id\n"
        "        description: PK\n"
    )
    loader = DbtLoader(project_path=tmp_path)
    entries = loader.load()
    assert "payments" in entries
    assert "Payment records" in entries["payments"].description


def test_loader_raises_on_invalid_manifest(tmp_path: Path) -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    manifest = tmp_path / "manifest.json"
    manifest.write_text("not json at all")
    loader = DbtLoader(project_path=tmp_path)
    with pytest.raises(Exception):  # noqa: B017
        loader.load()
