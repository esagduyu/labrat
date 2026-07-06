"""Detect when a Scent doc's derived skeleton drifted from the live schema (T2b v1)."""

from __future__ import annotations

import hashlib
import json


def schema_fingerprint(tables: dict[str, list[str]]) -> str:
    canonical = {t: sorted(cols) for t, cols in tables.items()}
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def is_stale(section_schema_hash: str | None, current_fingerprint: str) -> bool:
    if section_schema_hash is None:
        return False
    return section_schema_hash != current_fingerprint
