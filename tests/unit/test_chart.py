"""Tests for ChartSpec model and unicode rendering (M21)."""

from __future__ import annotations

import polars as pl
import pytest

from labrat.chart.render_unicode import render_unicode
from labrat.chart.spec import ChartSpec, ChartType


@pytest.fixture()
def sample_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "month": ["Jan", "Feb", "Mar", "Apr", "May"],
            "revenue": [100, 150, 130, 200, 175],
            "cost": [80, 110, 95, 160, 130],
        }
    )


# ── ChartSpec model ───────────────────────────────────────────────────────────


def test_chart_spec_bar() -> None:
    """ChartSpec can be created for a bar chart."""
    spec = ChartSpec(chart_type=ChartType.bar, x="month", y="revenue", title="Monthly Revenue")
    assert spec.chart_type == ChartType.bar
    assert spec.x == "month"
    assert spec.y == "revenue"


def test_chart_spec_all_types() -> None:
    """All five chart types are valid ChartType values."""
    for ct in ChartType:
        spec = ChartSpec(chart_type=ct, x="x_col", y="y_col")
        assert spec.chart_type == ct


def test_chart_spec_optional_fields() -> None:
    """color, facet, aggregation, title are all optional."""
    spec = ChartSpec(chart_type=ChartType.line, x="month", y="revenue")
    assert spec.color is None
    assert spec.facet is None
    assert spec.aggregation is None
    assert spec.title is None


def test_chart_spec_roundtrip() -> None:
    """ChartSpec serializes and deserializes cleanly."""
    spec = ChartSpec(
        chart_type=ChartType.scatter,
        x="cost",
        y="revenue",
        title="Cost vs Revenue",
        color="month",
    )
    data = spec.model_dump()
    spec2 = ChartSpec.model_validate(data)
    assert spec2.chart_type == spec.chart_type
    assert spec2.x == spec.x
    assert spec2.color == spec.color


# ── unicode renderer ──────────────────────────────────────────────────────────


def test_render_unicode_returns_string(sample_df: pl.DataFrame) -> None:
    """render_unicode returns a non-empty string."""
    spec = ChartSpec(chart_type=ChartType.bar, x="month", y="revenue")
    result = render_unicode(spec, sample_df)
    assert isinstance(result, str)
    assert len(result) > 0


def test_render_unicode_bar(sample_df: pl.DataFrame) -> None:
    """Bar chart renders without error."""
    spec = ChartSpec(chart_type=ChartType.bar, x="month", y="revenue", title="Revenue")
    result = render_unicode(spec, sample_df)
    assert len(result) > 10


def test_render_unicode_line(sample_df: pl.DataFrame) -> None:
    """Line chart renders without error."""
    spec = ChartSpec(chart_type=ChartType.line, x="month", y="revenue")
    result = render_unicode(spec, sample_df)
    assert len(result) > 10


def test_render_unicode_scatter(sample_df: pl.DataFrame) -> None:
    """Scatter chart renders without error."""
    spec = ChartSpec(chart_type=ChartType.scatter, x="cost", y="revenue")
    result = render_unicode(spec, sample_df)
    assert len(result) > 10


def test_render_unicode_histogram(sample_df: pl.DataFrame) -> None:
    """Histogram renders without error."""
    spec = ChartSpec(chart_type=ChartType.histogram, x="revenue", y="revenue")
    result = render_unicode(spec, sample_df)
    assert len(result) > 10


def test_render_unicode_area(sample_df: pl.DataFrame) -> None:
    """Area chart renders without error."""
    spec = ChartSpec(chart_type=ChartType.area, x="month", y="revenue")
    result = render_unicode(spec, sample_df)
    assert len(result) > 10


def test_render_unicode_missing_column_raises(sample_df: pl.DataFrame) -> None:
    """render_unicode raises ValueError when a column is not in the DataFrame."""
    spec = ChartSpec(chart_type=ChartType.bar, x="nonexistent", y="revenue")
    with pytest.raises(ValueError, match="Column"):
        render_unicode(spec, sample_df)
