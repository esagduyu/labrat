"""Tests for the `labrat scent check` / `labrat scent ingest` CLI subcommands."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from labrat.cli import app
from labrat.maze.ci import CiCheckResult
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
    assert data["checked"] == 1
    parsed = CiCheckResult.model_validate(data)
    assert parsed.checked == 1
    assert parsed.model_dump(mode="json") == data


def test_scent_check_autodetects_dbt_project_from_cwd(
    runner: CliRunner, tmp_path: Path, maze_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bare `scent check` (no --dbt-project, no profile dbt_project_path)
    resolves the project by walking up from cwd to the nearest
    dbt_project.yml — F1 part 2. Guards against a real 'default' profile on
    the host machine by forcing ProfileManager.get to miss."""
    from labrat.profile.manager import ProfileError, ProfileManager

    def _raise_missing(self: ProfileManager, name: str) -> None:
        raise ProfileError(f"Profile {name!r} not found.")

    monkeypatch.setattr(ProfileManager, "get", _raise_missing)

    project = tmp_path / "proj"
    _write_manifest(project, _manifest())
    nested = project / "models" / "staging"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["scent", "check"])

    # Resolution must succeed (walked up from `nested` to `project`, found
    # dbt_project.yml there) rather than hitting the "no --dbt-project given"
    # resolution error.
    assert "no --dbt-project given" not in result.output
    assert result.exit_code == 0, result.output


def test_scent_check_scent_dir_from_different_cwd_still_detects_drift(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--scent-dir must root the MazeStore's project layer at the SAME place
    the sidecar comes from — F2. Without the fix, semantic sections come
    from MazeStore.from_env's cwd (empty here) while the sidecar comes from
    --scent-dir, producing a silent '0 domains checked' false pass."""
    from labrat.maze.ci import catalog_from_dbt
    from labrat.maze.semantic_ingest import ingest_dbt_semantics
    from labrat.maze.store import MazeStore

    monkeypatch.delenv("LABRAT_MAZE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

    project = tmp_path / "proj"
    scent_root = tmp_path / "scentroot"
    _write_manifest(project, _manifest())

    store = MazeStore(project_root=scent_root, home=tmp_path / "home", profile="default")
    ingest_dbt_semantics(
        manifest_path=project / "target" / "manifest.json",
        catalog=catalog_from_dbt(project),
        store=store,
        project_scent_dir=scent_root / "labrat_maze" / "scent",
        force=True,
    )

    # Introduce schema drift (add a column) without re-ingesting.
    _write_manifest(project, _manifest(extra_col=True))

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = runner.invoke(
        app,
        [
            "scent",
            "check",
            "--dbt-project",
            str(project),
            "--scent-dir",
            str(scent_root / "labrat_maze" / "scent"),
        ],
    )

    assert result.exit_code == 1, result.output
    assert "schema_drift" in result.output


def test_scent_check_from_subdir_uses_resolved_project(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bare `scent check` (no --scent-dir, no LABRAT_MAZE_DIR) run from a
    SUBDIRECTORY of the dbt project must root the store at the *resolved*
    project path (found by walking up to dbt_project.yml), not at the raw
    subdir cwd — otherwise it silently reports 0 domains / false OK instead
    of the real (stale) verdict."""
    from labrat.profile.manager import ProfileError, ProfileManager

    def _raise_missing(self: ProfileManager, name: str) -> None:
        raise ProfileError(f"Profile {name!r} not found.")

    monkeypatch.setattr(ProfileManager, "get", _raise_missing)
    monkeypatch.delenv("LABRAT_MAZE_DIR", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

    project = tmp_path / "proj"
    _write_manifest(project, _manifest())

    monkeypatch.chdir(project)
    ingest_result = runner.invoke(app, ["scent", "ingest"])
    assert ingest_result.exit_code == 0, ingest_result.output

    # change the measure expr WITHOUT re-ingesting -> sidecar now stale
    _write_manifest(project, _manifest(measure_expr="net_revenue"))

    nested = project / "models" / "staging"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["scent", "check"])

    assert result.exit_code == 1, result.output
    assert "semantic_drift" in result.output


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


def test_scent_ingest_unreadable_manifest_exits_nonzero(
    runner: CliRunner, tmp_path: Path, maze_env: Path
) -> None:
    project = tmp_path / "proj"
    target = project / "target"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text("not json{{{", encoding="utf-8")
    (project / "dbt_project.yml").write_text("name: demo\nprofile: demo\n")

    result = runner.invoke(app, ["scent", "ingest", "--dbt-project", str(project)])
    assert result.exit_code != 0
    assert "manifest unreadable" in result.output


def test_init_ci_writes_workflow(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / ".github" / "workflows" / "labrat-scent.yml"

    result = runner.invoke(app, ["scent", "init-ci", "--path", str(target)])

    assert result.exit_code == 0, result.output
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "dbt parse" in content
    assert "labrat scent check" in content
    assert "--dbt-project" in content


def test_init_ci_no_clobber(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / ".github" / "workflows" / "labrat-scent.yml"
    target.parent.mkdir(parents=True)
    original = "# hand-written workflow, do not touch\n"
    target.write_text(original, encoding="utf-8")

    result = runner.invoke(app, ["scent", "init-ci", "--path", str(target)])

    assert result.exit_code != 0
    assert target.read_text(encoding="utf-8") == original


def test_init_ci_invalid_platform_exits_nonzero(runner: CliRunner, tmp_path: Path) -> None:
    target = tmp_path / "workflow.yml"

    result = runner.invoke(app, ["scent", "init-ci", "--platform", "gitlab", "--path", str(target)])

    assert result.exit_code != 0
    assert not target.exists()
