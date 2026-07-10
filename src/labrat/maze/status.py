"""Team-scent status surface (moat extra 2.3, D1): a pure read-only report over a
``MazeStore`` plus an optional live ``Catalog``.

For each domain in the merged store view: which scope(s) contributed sections,
a per-source tier count, the highest-trust source present, and a freshness
breakdown of each section's ``schema_hash`` against the live catalog's
fingerprint. Also reports the two sidecar drift signals (`.schema_fingerprint`
for the Cartographer pre-pass, `.manifest_fingerprint` for dbt semantic
ingestion) when their directories are supplied.

Never writes anything — ``build_status`` only reads ``store.docs()`` and, at
most, two sidecar files on disk.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from labrat.db.catalog import Catalog
from labrat.maze.provenance import best_source
from labrat.maze.semantic_ingest import read_manifest_fingerprint
from labrat.maze.staleness import fingerprint_from_catalog, is_stale, read_scent_fingerprint
from labrat.maze.store import MazeStore


class DomainStatus(BaseModel):
    domain: str
    scope: str
    sections: int
    tier_counts: dict[str, int]
    best: str
    fresh: int
    stale: int
    unknown: int


class MazeStatus(BaseModel):
    rows: list[DomainStatus]
    current_fingerprint: str | None
    scent_sidecar_stale: bool | None
    manifest_sidecar_present: bool


def build_status(
    store: MazeStore,
    *,
    catalog: Catalog | None = None,
    user_scent_dir: Path | None = None,
    project_scent_dir: Path | None = None,
) -> MazeStatus:
    """Build a read-only status report over ``store``'s merged domain docs."""
    current_fingerprint = fingerprint_from_catalog(catalog) if catalog is not None else None

    rows: list[DomainStatus] = []
    for doc in sorted(store.docs(), key=lambda d: d.domain):
        sources = [s.source for s in doc.sections]
        fresh = stale = unknown = 0
        for section in doc.sections:
            if section.schema_hash is None or current_fingerprint is None:
                unknown += 1
            elif section.schema_hash == current_fingerprint:
                fresh += 1
            else:
                stale += 1
        rows.append(
            DomainStatus(
                domain=doc.domain,
                scope=doc.scope,
                sections=len(doc.sections),
                tier_counts=dict(Counter(sources)),
                best=best_source(sources),
                fresh=fresh,
                stale=stale,
                unknown=unknown,
            )
        )

    scent_sidecar_stale: bool | None = None
    if current_fingerprint is not None and user_scent_dir is not None:
        stamped = read_scent_fingerprint(user_scent_dir)
        if stamped is not None:
            scent_sidecar_stale = is_stale(stamped, current_fingerprint)

    manifest_sidecar_present = (
        project_scent_dir is not None and read_manifest_fingerprint(project_scent_dir) is not None
    )

    return MazeStatus(
        rows=rows,
        current_fingerprint=current_fingerprint,
        scent_sidecar_stale=scent_sidecar_stale,
        manifest_sidecar_present=manifest_sidecar_present,
    )


def render_status(status: MazeStatus) -> str:
    """Render a ``MazeStatus`` as a plain aligned text table (no ANSI)."""
    fp_display = f"{status.current_fingerprint[:8]}…" if status.current_fingerprint else "n/a"
    if status.scent_sidecar_stale is None:
        sidecar_display = "n/a"
    elif status.scent_sidecar_stale:
        sidecar_display = "stale"
    else:
        sidecar_display = "fresh"
    manifest_display = "yes" if status.manifest_sidecar_present else "no"

    lines = [
        f"fingerprint: {fp_display} | scent sidecar: {sidecar_display} "
        f"| manifest sidecar: {manifest_display}",
        "",
    ]

    header = ("domain", "scope", "sections", "best", "fresh/stale/unknown", "tiers")
    table_rows: list[tuple[str, str, str, str, str, str]] = [header]
    for row in status.rows:
        tiers = ", ".join(f"{k}={v}" for k, v in sorted(row.tier_counts.items()))
        table_rows.append(
            (
                row.domain,
                row.scope,
                str(row.sections),
                row.best,
                f"{row.fresh}/{row.stale}/{row.unknown}",
                tiers,
            )
        )

    widths = [max(len(r[i]) for r in table_rows) for i in range(len(header))]
    for row in table_rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())

    return "\n".join(lines) + "\n"
