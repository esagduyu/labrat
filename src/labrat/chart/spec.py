"""ChartSpec: declarative chart specification (M21)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class ChartType(StrEnum):
    bar = "bar"
    line = "line"
    scatter = "scatter"
    area = "area"
    histogram = "histogram"


class ChartSpec(BaseModel):
    """Declarative specification for a chart.

    Passed to a renderer (unicode via plotext, or image via matplotlib) to
    produce the actual chart output.
    """

    chart_type: ChartType
    x: str
    y: str
    color: str | None = None
    facet: str | None = None
    aggregation: str | None = None
    title: str | None = None
