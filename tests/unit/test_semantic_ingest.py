"""build_semantic_sections routing/determinism + manifest fingerprint sidecar."""

import json
from pathlib import Path

import pytest

from labrat.catalog.dbt.semantic import parse_semantic_manifest
from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.maze.document import Section
from labrat.maze.scent_audit import ScentContaminationError
from labrat.maze.semantic_ingest import (
    build_semantic_sections,
    ingest_dbt_semantics,
    read_manifest_fingerprint,
    semantic_fingerprint,
    write_manifest_fingerprint,
)
from labrat.maze.store import MazeStore

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


def _store(tmp_path: Path) -> tuple[MazeStore, Path]:
    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")
    return store, tmp_path / "proj" / "labrat_maze" / "scent"


def test_first_contact_ingests_and_stamps_sidecar(tmp_path: Path) -> None:
    store, scent_dir = _store(tmp_path)
    out = ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=None, store=store, project_scent_dir=scent_dir
    )
    assert out.skipped is False and out.sections_written >= 4
    assert {"orders", "customers", "metrics"} <= set(out.domains)
    assert read_manifest_fingerprint(scent_dir) is not None
    doc = store.load_domain("orders", scope="project")
    assert doc is not None
    assert any(s.source == "semantic_layer" for s in doc.sections)


def test_unchanged_manifest_skips(tmp_path: Path) -> None:
    store, scent_dir = _store(tmp_path)
    ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=None, store=store, project_scent_dir=scent_dir
    )
    out = ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=None, store=store, project_scent_dir=scent_dir
    )
    assert out.skipped is True and out.drifted is False


def test_drift_detected_and_force_replaces_only_semantic_sections(tmp_path: Path) -> None:
    store, scent_dir = _store(tmp_path)
    ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=None, store=store, project_scent_dir=scent_dir
    )
    # A harvested section lands in the same doc between ingests.
    doc = store.load_domain("orders", scope="project")
    assert doc is not None
    doc.sections.append(Section(heading="Gotchas", body="- keep me", source="harvested"))
    store.write_doc(doc)
    write_manifest_fingerprint(scent_dir, "stale")  # simulate drift
    out = ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=None, store=store, project_scent_dir=scent_dir
    )
    assert out.skipped is True and out.drifted is True  # offer, don't force
    out2 = ingest_dbt_semantics(
        manifest_path=_FIXTURE,
        catalog=None,
        store=store,
        project_scent_dir=scent_dir,
        force=True,
    )
    assert out2.skipped is False
    doc2 = store.load_domain("orders", scope="project")
    assert doc2 is not None
    harvested = [s for s in doc2.sections if s.source == "harvested"]
    assert [s.body for s in harvested] == ["- keep me"]  # preserved byte-for-byte
    semantic_after = [s for s in doc2.sections if s.source == "semantic_layer"]
    semantic_first = [s for s in doc.sections if s.source == "semantic_layer"]
    assert len(semantic_after) == len(semantic_first)  # replaced, not doubled


def test_missing_manifest_skips_with_warning(tmp_path: Path) -> None:
    store, scent_dir = _store(tmp_path)
    out = ingest_dbt_semantics(
        manifest_path=tmp_path / "nope" / "manifest.json",
        catalog=None,
        store=store,
        project_scent_dir=scent_dir,
    )
    assert out.skipped is True and out.warnings


def test_contaminated_description_fails_loud_writes_nothing(tmp_path: Path) -> None:
    store, scent_dir = _store(tmp_path)
    bad = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    bad["semantic_models"]["semantic_model.jaffle.orders"]["description"] = (
        "see ground_truth.csv for the answers"
    )
    bad_path = tmp_path / "manifest.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ScentContaminationError):
        ingest_dbt_semantics(
            manifest_path=bad_path, catalog=None, store=store, project_scent_dir=scent_dir
        )
    assert store.load_domain("orders", scope="project") is None  # nothing written


def test_catalog_stamps_real_fingerprint(tmp_path: Path) -> None:
    from labrat.maze.staleness import fingerprint_from_catalog

    cat = Catalog(
        database_name="db",
        schemas=[
            Schema(
                name="main",
                tables=[
                    Table(
                        name="orders",
                        schema_name="main",
                        columns=[Column(name="id", data_type="INTEGER", nullable=False)],
                    )
                ],
            )
        ],
    )
    store, scent_dir = _store(tmp_path)
    ingest_dbt_semantics(
        manifest_path=_FIXTURE, catalog=cat, store=store, project_scent_dir=scent_dir
    )
    doc = store.load_domain("orders", scope="project")
    assert doc is not None
    sem = next(s for s in doc.sections if s.source == "semantic_layer")
    assert sem.schema_hash == fingerprint_from_catalog(cat)
