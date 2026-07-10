"""Team-scent status surface: inventory, tiers, freshness, sidecars. READ-ONLY."""

import os
import subprocess
import sys
from pathlib import Path

from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.maze.document import ScentDoc, Section, render_document
from labrat.maze.staleness import fingerprint_from_catalog, write_scent_fingerprint
from labrat.maze.status import build_status, render_status
from labrat.maze.store import MazeStore


def _catalog() -> Catalog:
    return Catalog(
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


def _seed(tmp_path: Path, fp: str) -> MazeStore:
    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")
    user = tmp_path / "home" / ".labrat" / "maze" / "p1" / "scent"
    proj = tmp_path / "proj" / "labrat_maze" / "scent"
    user.mkdir(parents=True)
    proj.mkdir(parents=True)
    (user / "orders.md").write_text(
        render_document(
            ScentDoc(
                domain="orders",
                sections=[
                    Section(
                        heading="Key Tables", body="- orders", source="verified", schema_hash=fp
                    ),
                ],
            )
        ),
        encoding="utf-8",
    )
    (proj / "orders.md").write_text(
        render_document(
            ScentDoc(
                domain="orders",
                sections=[
                    Section(heading="Gotchas", body="- exclude test", source="harvested"),
                ],
            )
        ),
        encoding="utf-8",
    )
    (proj / "metrics.md").write_text(
        render_document(
            ScentDoc(
                domain="metrics",
                sections=[
                    Section(
                        heading="Metric: Revenue",
                        body="- type: simple",
                        source="semantic_layer",
                        schema_hash="stalehash",
                    ),
                ],
            )
        ),
        encoding="utf-8",
    )
    return store


def test_build_status_rows(tmp_path: Path) -> None:
    cat = _catalog()
    fp = fingerprint_from_catalog(cat)
    store = _seed(tmp_path, fp)
    status = build_status(
        store,
        catalog=cat,
        user_scent_dir=tmp_path / "home" / ".labrat" / "maze" / "p1" / "scent",
        project_scent_dir=tmp_path / "proj" / "labrat_maze" / "scent",
    )
    rows = {r.domain: r for r in status.rows}
    assert list(rows) == sorted(rows)  # domain-sorted
    orders = rows["orders"]
    assert orders.scope == "merged" and orders.sections == 2
    assert orders.best == "verified"  # orders has verified+harvested -> best == "verified"
    assert orders.fresh == 1 and orders.unknown == 1  # stamped-fresh + unstamped
    metrics = rows["metrics"]
    assert metrics.best == "semantic_layer" and metrics.stale == 1
    assert status.manifest_sidecar_present is False


def test_no_catalog_all_unknown(tmp_path: Path) -> None:
    store = _seed(tmp_path, "whatever")
    status = build_status(store)
    for r in status.rows:
        assert r.fresh == 0 and r.stale == 0 and r.unknown == r.sections
    assert status.current_fingerprint is None
    assert status.scent_sidecar_stale is None


def test_scent_sidecar_states(tmp_path: Path) -> None:
    cat = _catalog()
    fp = fingerprint_from_catalog(cat)
    store = _seed(tmp_path, fp)
    user = tmp_path / "home" / ".labrat" / "maze" / "p1" / "scent"
    write_scent_fingerprint(user, fp)
    fresh = build_status(store, catalog=cat, user_scent_dir=user)
    assert fresh.scent_sidecar_stale is False
    write_scent_fingerprint(user, "drifted")
    stale = build_status(store, catalog=cat, user_scent_dir=user)
    assert stale.scent_sidecar_stale is True


def test_render_is_plain_table(tmp_path: Path) -> None:
    store = _seed(tmp_path, "x")
    text = render_status(build_status(store))
    assert "orders" in text and "metrics" in text
    assert "harvested" in text and "semantic_layer" in text
    assert "\x1b" not in text  # no ANSI


def test_read_only(tmp_path: Path) -> None:
    store = _seed(tmp_path, "x")
    proj = tmp_path / "proj" / "labrat_maze" / "scent"
    before = {p.name: p.read_bytes() for p in proj.glob("*")}
    build_status(store, project_scent_dir=proj)
    after = {p.name: p.read_bytes() for p in proj.glob("*")}
    assert before == after


def test_cli_smoke_exit_0(tmp_path: Path) -> None:
    _seed(tmp_path, "x")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "labrat.maze.print_status",
            "--project-root",
            str(tmp_path / "proj"),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(tmp_path / "home")},
    )
    assert result.returncode == 0
    assert "orders" in result.stdout
    assert "metrics" in result.stdout


def test_cli_bad_db_exit_2(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "labrat.maze.print_status",
            "--project-root",
            str(tmp_path / "proj"),
            "--db",
            str(tmp_path / "does-not-exist.duckdb"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stderr.strip() != ""
