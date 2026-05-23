"""Tests for image chart rendering and terminal protocol detection (M22)."""

from __future__ import annotations

import polars as pl
import pytest

from labrat.chart.render_image import render_image
from labrat.chart.spec import ChartSpec, ChartType
from labrat.chart.terminal_proto import (
    ImageProtocol,
    detect_protocol,
)


@pytest.fixture()
def sample_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "month": ["Jan", "Feb", "Mar", "Apr"],
            "revenue": [100, 150, 130, 200],
        }
    )


# ── render_image tests ────────────────────────────────────────────────────────


def test_render_image_returns_bytes(sample_df: pl.DataFrame) -> None:
    """render_image returns PNG bytes."""
    spec = ChartSpec(chart_type=ChartType.bar, x="month", y="revenue")
    result = render_image(spec, sample_df)
    assert isinstance(result, bytes)
    assert result[:4] == b"\x89PNG"  # PNG magic bytes


def test_render_image_bar(sample_df: pl.DataFrame) -> None:
    """Bar chart produces valid PNG output."""
    spec = ChartSpec(chart_type=ChartType.bar, x="month", y="revenue", title="Revenue")
    result = render_image(spec, sample_df)
    assert len(result) > 100  # actual image, not empty


def test_render_image_line(sample_df: pl.DataFrame) -> None:
    """Line chart produces valid PNG output."""
    spec = ChartSpec(chart_type=ChartType.line, x="month", y="revenue")
    result = render_image(spec, sample_df)
    assert result[:4] == b"\x89PNG"


def test_render_image_missing_column_raises(sample_df: pl.DataFrame) -> None:
    """render_image raises ValueError for missing columns."""
    spec = ChartSpec(chart_type=ChartType.bar, x="nonexistent", y="revenue")
    with pytest.raises(ValueError, match="Column"):
        render_image(spec, sample_df)


# ── terminal protocol detection ───────────────────────────────────────────────


def test_detect_protocol_kitty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns ImageProtocol.kitty when TERM=xterm-kitty."""
    monkeypatch.setenv("TERM", "xterm-kitty")
    monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    assert detect_protocol() == ImageProtocol.kitty


def test_detect_protocol_iterm2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns ImageProtocol.iterm2 when ITERM_SESSION_ID is set."""
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv("ITERM_SESSION_ID", "abc123")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    assert detect_protocol() == ImageProtocol.iterm2


def test_detect_protocol_wezterm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns ImageProtocol.iterm2 when TERM_PROGRAM=WezTerm."""
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
    monkeypatch.setenv("TERM_PROGRAM", "WezTerm")
    assert detect_protocol() == ImageProtocol.iterm2


def test_detect_protocol_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns ImageProtocol.none when no supported terminal is detected."""
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.delenv("ITERM_SESSION_ID", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    assert detect_protocol() == ImageProtocol.none


def test_image_protocol_enum_values() -> None:
    """ImageProtocol has the expected values."""
    assert ImageProtocol.kitty in ImageProtocol
    assert ImageProtocol.iterm2 in ImageProtocol
    assert ImageProtocol.sixel in ImageProtocol
    assert ImageProtocol.none in ImageProtocol
