"""Tests for the branding module."""

from io import StringIO
from pathlib import Path

from rich.console import Console

from labrat.branding import render_banner


def _render_to_string(variant: str, assets_dir: Path) -> str:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, no_color=True, width=80)
    render_banner(console, variant, assets_dir=assets_dir)
    return buf.getvalue()


def test_branding_default(tmp_path: Path) -> None:
    """render_banner uses rich-pyfiglet when no override file exists."""
    output = _render_to_string("splash", tmp_path)
    assert len(output.strip()) > 0


def test_branding_override(tmp_path: Path) -> None:
    """render_banner uses override file content when banner_splash.txt exists."""
    override_content = "CUSTOM LABRAT BANNER ART"
    (tmp_path / "banner_splash.txt").write_text(override_content)

    output = _render_to_string("splash", tmp_path)
    assert override_content in output
