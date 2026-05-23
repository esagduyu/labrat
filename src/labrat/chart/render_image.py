"""Raster chart rendering via matplotlib (M22).

Produces PNG bytes from a ChartSpec + Polars DataFrame. The caller is
responsible for delivering the bytes to the terminal via the appropriate
image protocol (kitty, iterm2, sixel) — see terminal_proto.py.
"""

from __future__ import annotations

import io
from typing import Any

import polars as pl

from labrat.chart.spec import ChartSpec, ChartType


def _validate_columns(spec: ChartSpec, df: pl.DataFrame) -> None:
    missing = [col for col in [spec.x, spec.y] if col not in df.columns]
    if missing:
        raise ValueError(f"Column(s) not found in DataFrame: {missing!r}")


def render_image(
    spec: ChartSpec,
    df: pl.DataFrame,
    width_inches: float = 8.0,
    height_inches: float = 4.5,
    dpi: int = 150,
) -> bytes:
    """Render a ChartSpec as a PNG image and return the raw bytes.

    Uses a non-interactive matplotlib backend so it works without a display.
    """
    import matplotlib

    matplotlib.use("Agg")  # non-interactive, must come before pyplot import
    import matplotlib.pyplot as plt  # type: ignore[import-untyped]

    _validate_columns(spec, df)

    fig, ax = plt.subplots(figsize=(width_inches, height_inches), dpi=dpi)  # pyright: ignore[reportUnknownMemberType]

    x_vals: list[Any] = df[spec.x].to_list()
    y_vals: list[Any] = df[spec.y].cast(pl.Float64).to_list()  # pyright: ignore[reportUnknownMemberType]

    match spec.chart_type:
        case ChartType.bar:
            ax.bar(range(len(x_vals)), y_vals, tick_label=x_vals)  # pyright: ignore[reportUnknownMemberType]
        case ChartType.line:
            ax.plot(range(len(x_vals)), y_vals)  # pyright: ignore[reportUnknownMemberType]
            ax.set_xticks(range(len(x_vals)))  # pyright: ignore[reportUnknownMemberType]
            ax.set_xticklabels(x_vals)  # pyright: ignore[reportUnknownMemberType]
        case ChartType.scatter:
            ax.scatter(range(len(x_vals)), y_vals)  # pyright: ignore[reportUnknownMemberType]
            ax.set_xticks(range(len(x_vals)))  # pyright: ignore[reportUnknownMemberType]
            ax.set_xticklabels(x_vals)  # pyright: ignore[reportUnknownMemberType]
        case ChartType.area:
            ax.fill_between(range(len(x_vals)), y_vals, alpha=0.5)  # pyright: ignore[reportUnknownMemberType]
            ax.plot(range(len(x_vals)), y_vals)  # pyright: ignore[reportUnknownMemberType]
            ax.set_xticks(range(len(x_vals)))  # pyright: ignore[reportUnknownMemberType]
            ax.set_xticklabels(x_vals)  # pyright: ignore[reportUnknownMemberType]
        case ChartType.histogram:
            ax.hist(y_vals, bins="auto")  # pyright: ignore[reportUnknownMemberType]

    if spec.title:
        ax.set_title(spec.title)  # pyright: ignore[reportUnknownMemberType]
    ax.set_xlabel(spec.x)  # pyright: ignore[reportUnknownMemberType]
    ax.set_ylabel(spec.y)  # pyright: ignore[reportUnknownMemberType]
    fig.tight_layout()  # pyright: ignore[reportUnknownMemberType]

    buf = io.BytesIO()
    fig.savefig(buf, format="png")  # pyright: ignore[reportUnknownMemberType]
    plt.close(fig)
    return buf.getvalue()
