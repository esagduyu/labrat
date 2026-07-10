"""Read-only dbt↔Scent consistency check (maze/ci.py)."""

import json
from pathlib import Path

from labrat.maze.ci import CiCheckResult, check_scent_freshness
from labrat.maze.store import MazeStore


def _manifest(measure_expr: str = "revenue", extra_col: bool = False) -> dict:
    cols = {"amount": {"name": "amount", "meta": {}}}
    if extra_col:
        cols["region"] = {"name": "region", "meta": {}}
    return {
        "nodes": {
            "model.demo.orders": {
                "resource_type": "model",
                "name": "orders",
                "schema": "analytics",
                "columns": cols,
                "depends_on": {"nodes": []},
                "compiled_code": "select 1",
            }
        },
        "semantic_models": {
            "semantic_model.demo.orders_sm": {
                "name": "orders_sm",
                "node_relation": {"alias": "orders"},
                "description": "orders",
                "entities": [{"name": "id", "type": "primary"}],
                "dimensions": [{"name": "day", "type": "time"}],
                "measures": [{"name": "rev", "agg": "sum", "expr": measure_expr}],
            }
        },
        "metrics": {},
    }


def _write_manifest(project: Path, manifest: dict) -> None:
    target = project / "target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(json.dumps(manifest))
    (project / "dbt_project.yml").write_text("name: demo\nprofile: demo\n")


def _ingest(project: Path, scent_root: Path) -> MazeStore:
    """Run the real ingest so the committed Scent + sidecar are 'fresh'."""
    from labrat.maze.ci import _catalog_from_dbt  # the shared bridge helper
    from labrat.maze.semantic_ingest import ingest_dbt_semantics

    store = MazeStore(project_root=scent_root, home=scent_root / "home", profile="default")
    ingest_dbt_semantics(
        manifest_path=project / "target" / "manifest.json",
        catalog=_catalog_from_dbt(project),
        store=store,
        project_scent_dir=scent_root / "labrat_maze" / "scent",
        force=True,
    )
    return store


def test_fresh_state_ok(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = _ingest(project, scent)
    res = check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    assert isinstance(res, CiCheckResult)
    assert res.ok is True and res.stale == [] and res.manifest_found is True


def test_semantic_drift_flagged(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = _ingest(project, scent)
    # change the measure expr WITHOUT re-ingesting → sidecar now stale
    _write_manifest(project, _manifest(measure_expr="net_revenue"))
    res = check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    assert res.ok is False
    assert any(s.reason == "semantic_drift" for s in res.stale)
    assert "labrat scent ingest" in res.fix_command


def test_schema_drift_flagged(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = _ingest(project, scent)
    # add a column (schema change), keep semantics identical → schema_hash diverges
    _write_manifest(project, _manifest(extra_col=True))
    res = check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    assert res.ok is False
    assert any(s.reason == "schema_drift" for s in res.stale)


def test_missing_manifest(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    (project).mkdir()
    store = MazeStore(project_root=scent, home=scent / "home", profile="default")
    res = check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    assert res.manifest_found is False and res.ok is False


def test_no_committed_scent_passes(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = MazeStore(project_root=scent, home=scent / "home", profile="default")
    res = check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    # manifest present, but no ingested Scent/sidecar → nothing to be stale
    assert res.ok is True and res.stale == []


def test_read_only_no_writes(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = _ingest(project, scent)
    before = {p: p.read_bytes() for p in (scent / "labrat_maze").rglob("*") if p.is_file()}
    check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    after = {p: p.read_bytes() for p in (scent / "labrat_maze").rglob("*") if p.is_file()}
    assert before == after  # the check writes nothing
