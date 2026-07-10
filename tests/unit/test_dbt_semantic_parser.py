"""parse_semantic_manifest: tolerant extraction of semantic_models + metrics."""

import json
from pathlib import Path

from labrat.catalog.dbt.semantic import parse_semantic_manifest

_FIXTURE = Path("tests/fixtures/sample_dbt_project/manifest_semantic.json")


def _manifest() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_parses_models_and_metrics() -> None:
    art = parse_semantic_manifest(_manifest())
    names = {m.name for m in art.models}
    assert {"orders", "customers"} <= names
    orders = next(m for m in art.models if m.name == "orders")
    assert orders.table == "orders"
    assert [e.name for e in orders.entities] == ["order_id", "customer_id"]
    assert [d.name for d in orders.dimensions] == ["status", "created_at"]
    assert [me.name for me in orders.measures] == ["order_total", "order_count"]
    assert orders.measures[0].agg == "sum" and orders.measures[0].expr == "total_amount"
    metrics = {m.name: m for m in art.metrics}
    assert metrics["revenue"].type == "simple"
    assert metrics["revenue"].measure_refs == ["order_total"]
    assert metrics["revenue_per_customer"].type == "ratio"
    assert set(metrics["revenue_per_customer"].measure_refs) == {"revenue", "customer_count"}


def test_malformed_entries_become_warnings_not_errors() -> None:
    art = parse_semantic_manifest(_manifest())
    # "broken" model lacks node_relation → skipped-with-warning OR parsed with
    # name-fallback table; either way NO exception and a warning mentioning it.
    assert any("broken" in w for w in art.warnings)


def test_missing_keys_yield_empty_artifacts() -> None:
    art = parse_semantic_manifest({"metadata": {}, "nodes": {}})
    assert art.models == [] and art.metrics == [] and art.warnings == []


def test_never_raises_on_garbage_shapes() -> None:
    art = parse_semantic_manifest(
        {
            "semantic_models": {"x": None, "y": 3, "z": {"entities": "nope"}},
            "metrics": {"a": [], "b": {"type_params": 7}},
        }
    )
    assert isinstance(art.warnings, list) and len(art.warnings) >= 3
