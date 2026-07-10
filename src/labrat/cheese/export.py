"""export_cheese: Finding list → rendered, versioned, self-contained artifact."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from labrat.cheese.render import FindingRender, render_cheese
from labrat.cheese.store import CheeseStore, FindingDataStore
from labrat.thread.model import Finding


def _to_render(f: Finding, data_store: FindingDataStore) -> FindingRender:
    columns: list[str] | None = None
    rows: list[list[str]] | None = None
    total: int | None = None
    chart_b64: str | None = None
    if f.results_ref:
        loaded = data_store.load(f.results_ref)
        if loaded is not None:
            df, total = loaded
            columns = df.columns
            rows = [["" if v is None else str(v) for v in row] for row in df.iter_rows()]
        png = data_store.load_chart_png(f.results_ref)
        if png is not None:
            chart_b64 = base64.b64encode(png).decode("ascii")
    return FindingRender(
        question=f.question,
        note=f.note,
        sql=f.sql,
        provenance=f.provenance,
        chart_png_b64=chart_b64,
        table_columns=columns,
        table_rows=rows,
        total_rows=total,
        pinned_at=f.pinned_at,
    )


def export_cheese(
    findings: list[Finding],
    *,
    kind: Literal["single", "report"],
    title: str,
    rows_mode: Literal["preview", "none"] = "preview",
    cheese_store: CheeseStore,
    data_store: FindingDataStore,
) -> Path:
    manifest = cheese_store.create_or_get(kind, [f.id for f in findings], title)
    html = render_cheese(
        [_to_render(f, data_store) for f in findings],
        title=title,
        kind=kind,
        version_n=len(manifest.versions) + 1,
        exported_at=datetime.now(tz=UTC),
        rows_mode=rows_mode,
    )
    return cheese_store.add_version(manifest.cheese_id, html, rows_mode)
