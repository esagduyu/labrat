"""TUI first-connect Cartographer controller (T2c).

Pure async glue — no Textual imports — so the whole connect-time policy is
unit-testable: run the (idempotent) deterministic pre-pass, stamp a sidecar
schema fingerprint on generation, and report staleness on reuse. The TUI
worker owns notifications; this module owns the decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from labrat.maze.cartographer import cartograph_prepass
from labrat.maze.staleness import (
    fingerprint_from_catalog,
    is_stale,
    read_scent_fingerprint,
    write_scent_fingerprint,
)

if TYPE_CHECKING:
    from labrat.db.catalog import Catalog


@dataclass(frozen=True)
class PrepassOutcome:
    doc_paths: tuple[Path, ...]
    generated: bool  # True when this call authored the docs (first contact)
    stale: bool  # True when existing docs' fingerprint mismatches the live catalog


async def tui_first_connect_prepass(
    *,
    connections: dict[str, object],
    catalogs: dict[str, object],
    primary: str,
    catalog: Catalog,
    scent_dir: Path,
) -> PrepassOutcome:
    """Deterministic-only pre-pass + staleness check. Never regenerates on its own.

    Semantics stays off by construction (T1c ablated net-negative); refresh is a
    separate, user-confirmed action that deletes ``scent_dir`` before calling
    this again.
    """
    current = fingerprint_from_catalog(catalog)
    had_docs = scent_dir.exists() and any(scent_dir.glob("*.md"))

    doc_paths = await cartograph_prepass(
        connections, catalogs, primary, scent_dir, with_semantics=False
    )

    if had_docs:
        stored = read_scent_fingerprint(scent_dir)
        return PrepassOutcome(
            doc_paths=tuple(doc_paths), generated=False, stale=is_stale(stored, current)
        )

    write_scent_fingerprint(scent_dir, current)
    return PrepassOutcome(doc_paths=tuple(doc_paths), generated=True, stale=False)
