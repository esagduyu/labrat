"""Read-only dbt↔Scent consistency check (maze/ci.py)."""

import json
from pathlib import Path

from labrat.maze.ci import CiCheckResult, catalog_from_dbt, check_scent_freshness
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
    from labrat.maze.semantic_ingest import ingest_dbt_semantics

    store = MazeStore(project_root=scent_root, home=scent_root / "home", profile="default")
    ingest_dbt_semantics(
        manifest_path=project / "target" / "manifest.json",
        catalog=catalog_from_dbt(project),
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


def test_catalog_from_dbt_prefers_target_over_stale_root_manifest(tmp_path):
    """F4: a stale root-level manifest.json beside a fresh target/manifest.json
    must not shadow the fresh one — catalog_from_dbt (and therefore schema-drift
    detection) has to agree with check_scent_freshness's own target/ convention."""
    project = tmp_path / "proj"
    project.mkdir(parents=True)
    (project / "manifest.json").write_text(json.dumps(_manifest()))  # stale root copy
    _write_manifest(project, _manifest(extra_col=True))  # fresh target/ copy

    catalog = catalog_from_dbt(project)
    assert catalog is not None
    cols = {c.name for schema in catalog.schemas for t in schema.tables for c in t.columns}
    assert "region" in cols  # only present in the fresh target/ manifest


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


def test_removing_semantics_clears_scent_and_unblocks_check(tmp_path):
    """F3: deleting all semantic_models from the dbt project must clear the
    stale semantic_layer sections (and refresh the sidecar) on the next
    `scent ingest`, not silently no-op and leave `scent check` stale forever."""
    from labrat.maze.ci import check_scent_freshness
    from labrat.maze.semantic_ingest import ingest_dbt_semantics

    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = _ingest(project, scent)  # fresh, has a ## Semantic Model section
    scdir = scent / "labrat_maze" / "scent"
    # remove ALL semantic_models from the manifest, then re-ingest (the fix path)
    _write_manifest(project, {"nodes": _manifest()["nodes"], "semantic_models": {}, "metrics": {}})
    outcome = ingest_dbt_semantics(
        manifest_path=project / "target" / "manifest.json",
        catalog=catalog_from_dbt(project),
        store=store,
        project_scent_dir=scdir,
        force=True,
    )
    assert outcome.cleared >= 1
    # check now passes (no stale semantic Scent left)
    res = check_scent_freshness(project, scdir, store=store)
    assert res.ok is True


def test_first_ingest_on_semantics_free_project_clears_nothing(tmp_path):
    """A genuinely-empty project (no prior ingest at all) must not run the
    clear-pass — nothing to clear, and it shouldn't touch unrelated docs."""
    from labrat.maze.semantic_ingest import ingest_dbt_semantics

    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, {"nodes": {}, "semantic_models": {}, "metrics": {}})
    store = MazeStore(project_root=scent, home=scent / "home", profile="default")
    scdir = scent / "labrat_maze" / "scent"
    outcome = ingest_dbt_semantics(
        manifest_path=project / "target" / "manifest.json",
        catalog=catalog_from_dbt(project),
        store=store,
        project_scent_dir=scdir,
    )
    assert outcome.skipped is True
    assert outcome.cleared == 0
    assert outcome.domains == ()
    # the fingerprint sidecar IS written, so a later `scent check` sees fresh
    # rather than "no committed Scent to check".
    from labrat.maze.semantic_ingest import read_manifest_fingerprint

    assert read_manifest_fingerprint(scdir) is not None


def test_read_only_no_writes(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = _ingest(project, scent)
    # rglob from `scent` (not just `scent / "labrat_maze"`) so a hypothetical
    # user-layer write (scent / "home" / ...) would also be caught.
    before = {p: p.read_bytes() for p in scent.rglob("*") if p.is_file()}
    check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    after = {p: p.read_bytes() for p in scent.rglob("*") if p.is_file()}
    assert before == after  # the check writes nothing
