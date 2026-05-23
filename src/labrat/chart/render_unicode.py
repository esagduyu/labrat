"""Unicode chart rendering via plotext (M21)."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Any

import plotext as plt  # type: ignore[import-untyped]
import polars as pl

from labrat.chart.spec import ChartSpec, ChartType

_NUMERIC_DTYPES = {
    pl.Int8,
    pl.Int16,
    pl.Int32,
    pl.Int64,
    pl.UInt8,
    pl.UInt16,
    pl.UInt32,
    pl.UInt64,
    pl.Float32,
    pl.Float64,
}


def _validate_columns(spec: ChartSpec, df: pl.DataFrame) -> None:
    missing: list[str] = []
    for col in [spec.x, spec.y]:
        if col not in df.columns:
            missing.append(col)
    if missing:
        raise ValueError(f"Column(s) not found in DataFrame: {missing!r}")


def _is_numeric_col(df: pl.DataFrame, col: str) -> bool:
    return type(df[col].dtype) in _NUMERIC_DTYPES


def render_unicode(spec: ChartSpec, df: pl.DataFrame, width: int = 80, height: int = 24) -> str:
    """Render a ChartSpec against a Polars DataFrame using plotext.

    Returns the chart as a string (ANSI / plain text).
    Raises ValueError if required columns are missing from the DataFrame.
    """
    _validate_columns(spec, df)

    plt.clf()  # pyright: ignore[reportUnknownMemberType]
    plt.plotsize(width, height)  # pyright: ignore[reportUnknownMemberType]
    if spec.title:
        plt.title(spec.title)  # pyright: ignore[reportUnknownMemberType]

    x_data: list[Any] = df[spec.x].to_list()
    y_data: list[Any] = df[spec.y].cast(pl.Float64).to_list()  # pyright: ignore[reportUnknownMemberType]

    # plotext tries to parse string X values as dates for line/scatter/area.
    # Fall back to integer indices when X is non-numeric.
    x_for_plot = x_data if _is_numeric_col(df, spec.x) else list(range(len(x_data)))

    match spec.chart_type:
        case ChartType.bar:
            plt.bar(x_data, y_data)  # pyright: ignore[reportUnknownMemberType]
        case ChartType.line:
            plt.plot(x_for_plot, y_data)  # pyright: ignore[reportUnknownMemberType]
        case ChartType.scatter:
            plt.scatter(x_for_plot, y_data)  # pyright: ignore[reportUnknownMemberType]
        case ChartType.area:
            plt.plot(x_for_plot, y_data, fillx=True)  # pyright: ignore[reportUnknownMemberType]
        case ChartType.histogram:
            plt.hist(y_data)  # pyright: ignore[reportUnknownMemberType]

    buf = io.StringIO()
    with redirect_stdout(buf):
        plt.show()  # pyright: ignore[reportUnknownMemberType]
    return buf.getvalue()
