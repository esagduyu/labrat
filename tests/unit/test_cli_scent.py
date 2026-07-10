"""Tests for the `labrat scent check` / `labrat scent ingest` CLI subcommands."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from labrat.cli import app
from tests.unit.test_maze_ci import _manifest, _write_manifest


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def maze_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate MazeStore.from_env / project_scent_dir() to a tmp project + home root."""
    scent_root = tmp_path / "scentroot"
    scent_root.mkdir()
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(scent_root))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    return scent_root


def test_scent_check_fresh_exit_0(runner: CliRunner, tmp_path: Path, maze_env: Path) -> None:
    project = tmp_path / "proj"
    _write_manifest(project, _manifest())
    ingest_result = runner.invoke(app, ["scent", "ingest", "--dbt-project", str(project)])
    assert ingest_result.exit_code == 0, ingest_result.output

    result = runner.invoke(app, ["scent", "check", "--dbt-project", str(project)])
    assert result.exit_code == 0, result.output


def test_scent_check_stale_exit_1(runner: CliRunner, tmp_path: Path, maze_env: Path) -> None:
    project = tmp_path / "proj"
    _write_manifest(project, _manifest())
    ingest_result = runner.invoke(app, ["scent", "ingest", "--dbt-project", str(project)])
    assert ingest_result.exit_code == 0, ingest_result.output

    # change the measure expr WITHOUT re-ingesting -> sidecar now stale
    _write_manifest(project, _manifest(measure_expr="net_revenue"))

    result = runner.invoke(app, ["scent", "check", "--dbt-project", str(project)])
    assert result.exit_code == 1
    assert "semantic_drift" in result.output


def test_scent_check_warn_only_exit_0(runner: CliRunner, tmp_path: Path, maze_env: Path) -> None:
    project = tmp_path / "proj"
    _write_manifest(project, _manifest())
    ingest_result = runner.invoke(app, ["scent", "ingest", "--dbt-project", str(project)])
    assert ingest_result.exit_code == 0, ingest_result.output

    _write_manifest(project, _manifest(measure_expr="net_revenue"))

    result = runner.invoke(app, ["scent", "check", "--dbt-project", str(project), "--warn-only"])
    assert result.exit_code == 0, result.output
    assert "semantic_drift" in result.output  # warning still printed


def test_scent_check_json(runner: CliRunner, tmp_path: Path, maze_env: Path) -> None:
    project = tmp_path / "proj"
    _write_manifest(project, _manifest())
    ingest_result = runner.invoke(app, ["scent", "ingest", "--dbt-project", str(project)])
    assert ingest_result.exit_code == 0, ingest_result.output

    result = runner.invoke(
        app, ["scent", "check", "--dbt-project", str(project), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["manifest_found"] is True
    assert data["stale"] == []


def test_scent_check_missing_manifest_exit_1(
    runner: CliRunner, tmp_path: Path, maze_env: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    result = runner.invoke(app, ["scent", "check", "--dbt-project", str(project)])
    assert result.exit_code == 1


def test_scent_check_skip_if_no_manifest(runner: CliRunner, tmp_path: Path, maze_env: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    result = runner.invoke(
        app,
        ["scent", "check", "--dbt-project", str(project), "--skip-if-no-manifest"],
    )
    assert result.exit_code == 0, result.output


def test_scent_ingest_writes(runner: CliRunner, tmp_path: Path, maze_env: Path) -> None:
    project = tmp_path / "proj"
    _write_manifest(project, _manifest())

    result = runner.invoke(app, ["scent", "ingest", "--dbt-project", str(project)])
    assert result.exit_code == 0, result.output

    scent_dir = maze_env / "labrat_maze" / "scent"
    written = list(scent_dir.glob("*.md"))
    assert written, "expected scent ingest to write at least one domain doc"
