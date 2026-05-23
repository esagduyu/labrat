"""Tests for the labrat conn CLI subcommands."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from labrat.cli import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_conn_add_and_list(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Add a profile via CLI and verify it appears in list."""
    profiles_file = tmp_path / "profiles.json"
    monkeypatch.setattr("labrat.profile.storage._PROFILES_FILE", profiles_file)

    result = runner.invoke(
        app,
        ["conn", "add", "--name", "cli-test", "--dialect", "duckdb", "--path", ":memory:"],
    )
    assert result.exit_code == 0, result.output
    assert "cli-test" in result.output

    result = runner.invoke(app, ["conn", "list"])
    assert result.exit_code == 0
    assert "cli-test" in result.output


def test_conn_help_renders(runner: CliRunner) -> None:
    result = runner.invoke(app, ["conn", "--help"])
    assert result.exit_code == 0
    assert "conn" in result.output.lower()
