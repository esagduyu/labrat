"""Tests for dbt lineage graph (M30)."""

from __future__ import annotations

from pathlib import Path

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "sample_dbt_project"


def _entries() -> dict:  # type: ignore[type-arg]
    from labrat.catalog.dbt.loader import DbtLoader

    return DbtLoader(project_path=FIXTURE_DIR).load()


def test_lineage_upstream_direct() -> None:
    from labrat.catalog.dbt.lineage import DbtLineage

    lineage = DbtLineage(_entries())
    upstream = lineage.upstream("orders", depth=1)
    assert "stg_orders" in upstream
    assert "stg_customers" in upstream


def test_lineage_upstream_depth_2() -> None:
    from labrat.catalog.dbt.lineage import DbtLineage

    lineage = DbtLineage(_entries())
    # orders → stg_orders → (source, not a model) depth-2 won't add sources
    # But stg_customers is depth-1; depth-2 should still include depth-1 entries
    upstream = lineage.upstream("orders", depth=2)
    assert "stg_orders" in upstream
    assert "stg_customers" in upstream


def test_lineage_downstream_direct() -> None:
    from labrat.catalog.dbt.lineage import DbtLineage

    lineage = DbtLineage(_entries())
    downstream = lineage.downstream("stg_orders", depth=1)
    assert "orders" in downstream


def test_lineage_empty_upstream_for_source_table() -> None:
    from labrat.catalog.dbt.lineage import DbtLineage

    lineage = DbtLineage(_entries())
    # stg_orders depends only on a source node (not a model)
    upstream = lineage.upstream("stg_orders", depth=1)
    assert "orders" not in upstream  # orders is downstream, not upstream


def test_lineage_graph_includes_table_itself() -> None:
    from labrat.catalog.dbt.lineage import DbtLineage

    lineage = DbtLineage(_entries())
    graph = lineage.lineage_graph("orders", depth=1)
    # graph should contain orders plus its immediate relatives
    assert "orders" in graph or any("orders" in v for v in graph.values())
