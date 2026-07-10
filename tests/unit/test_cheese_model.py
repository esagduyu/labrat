"""Cheese v1 models + provenance snapshot."""

from datetime import UTC, datetime

from labrat.cheese.model import (
    CheeseManifest,
    CheeseVersion,
    FindingProvenance,
    ScentSourceRef,
)
from labrat.thread.model import Finding


def _prov() -> FindingProvenance:
    return FindingProvenance(
        scent_sources=[ScentSourceRef(domain="orders", tier="semantic_layer", fresh=True)],
        joins_verified=1,
        lineage_used=False,
        verifier_verdict=None,
        run_sql_count=2,
        schema_fingerprint="abc123",
        git_sha="a1b2c3d",
        model_id="claude-sonnet-4-6",
        captured_at=datetime.now(tz=UTC),
    )


def test_finding_provenance_roundtrip():
    p = _prov()
    assert FindingProvenance.model_validate_json(p.model_dump_json()) == p


def test_finding_gains_optional_provenance_and_old_json_loads():
    old = {
        "id": "f1",
        "version_id": "v1",
        "question": "q",
        "sql": "select 1",
        "results_ref": None,
        "chart_spec": None,
        "note": "",
        "pinned_at": "2026-07-01T00:00:00Z",
    }
    f = Finding.model_validate(old)
    assert f.provenance is None


def test_manifest_roundtrip():
    m = CheeseManifest(
        cheese_id="c1",
        kind="single",
        finding_ids=["f1"],
        title="t",
        versions=[
            CheeseVersion(
                n=1, exported_at=datetime.now(tz=UTC), path="v1.html", rows_mode="preview"
            )
        ],
        current=1,
    )
    assert CheeseManifest.model_validate_json(m.model_dump_json()) == m
