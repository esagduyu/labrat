"""Smoke tests: CLI help and basic import."""

from typer.testing import CliRunner

from labrat.cli import app


def test_cli_help_renders() -> None:
    """typer help output mentions labrat."""
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "labrat" in result.output.lower()
