"""build_semantic_sections routing/determinism + manifest fingerprint sidecar."""

import json
from pathlib import Path

from labrat.catalog.dbt.semantic import parse_semantic_manifest
from labrat.maze.semantic_ingest import (
    build_semantic_sections,
    read_manifest_fingerprint,
    semantic_fingerprint,
    write_manifest_fingerprint,
)

_FIXTURE = Path("tests/fixtures/sample_dbt_project/manifest_semantic.json")


def _artifacts():
    return parse_semantic_manifest(json.loads(_FIXTURE.read_text(encoding="utf-8")))


def test_models_route_to_table_domains_with_stamp() -> None:
    drafts = build_semantic_sections(_artifacts(), schema_hash="fp123")
    assert {"orders", "customers"} <= set(drafts)
    orders_secs = drafts["orders"]
    model_sec = next(s for s in orders_secs if s.heading == "Semantic Model: orders")
    assert model_sec.source == "semantic_layer"
    assert model_sec.schema_hash == "fp123"
    assert model_sec.generated_at is None  # no clock, ever
    assert "order_total" in model_sec.body and "sum" in model_sec.body


def test_simple_metric_routes_to_owner_domain() -> None:
    drafts = build_semantic_sections(_artifacts(), schema_hash=None)
    headings = [s.heading for s in drafts["orders"]]
    assert "Metric: Revenue" in headings  # owner of order_total
    revenue = next(s for s in drafts["orders"] if s.heading == "Metric: Revenue")
    assert revenue.schema_hash is None  # honest unknown


def test_ratio_metric_routes_to_metrics_domain() -> None:
    drafts = build_semantic_sections(_artifacts(), schema_hash=None)
    assert "metrics" in drafts
    assert any(s.heading == "Metric: Revenue per Customer" for s in drafts["metrics"])


def test_deterministic_bytes() -> None:
    a = build_semantic_sections(_artifacts(), schema_hash="fp")
    b = build_semantic_sections(_artifacts(), schema_hash="fp")
    assert {k: [s.model_dump() for s in v] for k, v in a.items()} == {
        k: [s.model_dump() for s in v] for k, v in b.items()
    }


def test_manifest_fingerprint_tracks_semantic_subset_only() -> None:
    manifest = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    base = semantic_fingerprint(manifest)
    manifest["nodes"] = {"model.x": {"anything": 1}}  # model churn: no drift
    assert semantic_fingerprint(manifest) == base
    manifest["metrics"]["metric.jaffle.revenue"]["description"] = "changed"
    assert semantic_fingerprint(manifest) != base  # semantic change: drift


def test_sidecar_round_trip(tmp_path: Path) -> None:
    assert read_manifest_fingerprint(tmp_path) is None
    write_manifest_fingerprint(tmp_path, "abc")
    assert read_manifest_fingerprint(tmp_path) == "abc"
