"""Detect when a Scent doc's derived skeleton drifted from the live schema (T2b v1)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from labrat.db.catalog import Catalog


def schema_fingerprint(tables: dict[str, list[str]]) -> str:
    canonical = {t: sorted(cols) for t, cols in tables.items()}
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def is_stale(section_schema_hash: str | None, current_fingerprint: str) -> bool:
    if section_schema_hash is None:
        return False
    return section_schema_hash != current_fingerprint


_FINGERPRINT_FILE = ".schema_fingerprint"


def fingerprint_from_catalog(catalog: Catalog) -> str:
    """Fingerprint an introspected Catalog (all schemas' tables + column names)."""
    tables: dict[str, list[str]] = {}
    for schema in catalog.schemas:
        for table in schema.tables:
            tables[table.name] = [c.name for c in table.columns]
    return schema_fingerprint(tables)


def read_scent_fingerprint(scent_dir: Path) -> str | None:
    """Read the sidecar fingerprint written at pre-pass time (None if absent)."""
    path = scent_dir / _FINGERPRINT_FILE
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def write_scent_fingerprint(scent_dir: Path, fingerprint: str) -> None:
    """Persist the catalog fingerprint next to the generated scent docs."""
    scent_dir.mkdir(parents=True, exist_ok=True)
    (scent_dir / _FINGERPRINT_FILE).write_text(fingerprint + "\n", encoding="utf-8")
