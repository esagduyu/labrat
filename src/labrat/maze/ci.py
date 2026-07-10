"""Read-only dbt <-> Scent consistency check (dbt-CI pairing, Task 1).

``check_scent_freshness`` compares a committed dbt project's manifest.json
against the committed Scent (semantic-layer sections + their sidecar
fingerprints) and reports drift. It is strictly read-only: no ``write_doc``,
no sidecar write, no directory creation — see ``test_read_only_no_writes``.

Honest-unknown by design: no committed sidecar means nothing has ever been
ingested, which is *not* the same as staleness, so that case reports ok=True.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel

from labrat.catalog.dbt.loader import DbtLoader
from labrat.catalog.dbt.semantic import parse_semantic_manifest
from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.maze.document import Section
from labrat.maze.semantic_ingest import read_manifest_fingerprint, semantic_fingerprint
from labrat.maze.staleness import fingerprint_from_catalog
from labrat.maze.store import MazeStore

_FIX_TEMPLATE = "labrat scent ingest --dbt-project {path}"
_SEMANTIC_LAYER_SOURCE = "semantic_layer"


class StaleDomain(BaseModel):
    domain: str
    reason: Literal["semantic_drift", "schema_drift"]
    committed_hash: str | None
    current_hash: str | None


class CiCheckResult(BaseModel):
    ok: bool
    stale: list[StaleDomain]
    checked: int
    warnings: list[str]
    fix_command: str
    manifest_found: bool


def catalog_from_dbt(dbt_project_path: Path) -> Catalog | None:
    """Offline DbtLoader -> db.catalog.Catalog bridge (no existing bridge to reuse).

    ``DbtLoader`` reads ``manifest.json`` directly out of the path it's given
    (see ``catalog/dbt/loader.py``), while a real dbt project's compiled
    artifact lives at ``<project>/target/manifest.json``. Try the project
    root first (matches ``DbtLoader``'s own test fixtures), then fall back to
    ``target/`` (matches a real dbt project layout, and this module's own
    ``manifest_path`` convention below).
    """
    loader_path = dbt_project_path
    if not (loader_path / "manifest.json").exists():
        target_path = dbt_project_path / "target"
        if (target_path / "manifest.json").exists():
            loader_path = target_path
    try:
        entries = DbtLoader(loader_path).load()
    except Exception:  # unloadable dbt project -> honest-unknown None
        return None
    if not entries:
        return None
    by_schema: dict[str, list[Table]] = defaultdict(list)
    for e in entries.values():
        cols = [
            Column(
                name=c.name,
                data_type=c.data_type or "",
                nullable=True,
                comment=c.description or None,
            )
            for c in e.columns.values()
        ]
        by_schema[e.schema_name].append(Table(name=e.name, schema_name=e.schema_name, columns=cols))
    schemas = [
        Schema(name=s, tables=sorted(ts, key=lambda t: t.name))
        for s, ts in sorted(by_schema.items())
    ]
    return Catalog(database_name="dbt", schemas=schemas)


def _load_manifest(manifest_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read + parse manifest.json. Returns (manifest, error) — exactly one is None."""
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"failed to read manifest.json: {exc}"
    if not isinstance(raw, dict):
        return None, "manifest.json is not a JSON object"
    return cast(dict[str, Any], raw), None


def _semantic_domains(store: MazeStore) -> dict[str, Section]:
    """Committed domains carrying a semantic_layer section -> one representative section.

    ``build_semantic_sections`` stamps every section from one ingest with the
    same ``schema_hash``, so any one semantic_layer section per domain is
    enough to read the domain's committed schema_hash from.
    """
    out: dict[str, Section] = {}
    for doc in store.docs(kind="scent"):
        for section in doc.sections:
            if section.source == _SEMANTIC_LAYER_SOURCE:
                out.setdefault(doc.domain, section)
                break
    return out


def check_scent_freshness(
    dbt_project_path: Path, scent_dir: Path, *, store: MazeStore
) -> CiCheckResult:
    """Offline, read-only: does the committed Scent still match the committed dbt project?

    Never writes to disk — no ``write_doc``, no sidecar write, no directory
    creation. Two independent drift checks, both against sidecars already
    written by a prior ``labrat scent ingest`` run:

    - semantic_drift: ``semantic_fingerprint(current manifest)`` vs. the
      committed ``.manifest_fingerprint`` sidecar.
    - schema_drift: ``fingerprint_from_catalog(offline catalog)`` vs. the
      uniform ``schema_hash`` stamped on committed semantic sections.
    """
    fix_command = _FIX_TEMPLATE.format(path=dbt_project_path)
    manifest_path = dbt_project_path / "target" / "manifest.json"
    if not manifest_path.exists():
        return CiCheckResult(
            ok=False,
            stale=[],
            checked=0,
            warnings=["manifest.json not found — run `dbt parse` first"],
            fix_command=fix_command,
            manifest_found=False,
        )

    manifest, error = _load_manifest(manifest_path)
    if manifest is None:
        return CiCheckResult(
            ok=False,
            stale=[],
            checked=0,
            warnings=[error] if error else [],
            fix_command=fix_command,
            manifest_found=True,
        )

    committed_manifest_fp = read_manifest_fingerprint(scent_dir)
    if committed_manifest_fp is None:
        # Honest-unknown: nothing has ever been ingested here. Absence != stale.
        return CiCheckResult(
            ok=True,
            stale=[],
            checked=0,
            warnings=["no committed Scent to check"],
            fix_command=fix_command,
            manifest_found=True,
        )

    current_manifest_fp = semantic_fingerprint(manifest)
    semantic_domains = _semantic_domains(store)

    stale: list[StaleDomain] = []

    if current_manifest_fp != committed_manifest_fp:
        artifacts = parse_semantic_manifest(manifest)
        current_tables = {m.table for m in artifacts.models}
        matched_domains = sorted(current_tables & set(semantic_domains))
        if matched_domains:
            for domain in matched_domains:
                stale.append(
                    StaleDomain(
                        domain=domain,
                        reason="semantic_drift",
                        committed_hash=committed_manifest_fp,
                        current_hash=current_manifest_fp,
                    )
                )
        else:
            stale.append(
                StaleDomain(
                    domain="(semantic layer)",
                    reason="semantic_drift",
                    committed_hash=committed_manifest_fp,
                    current_hash=current_manifest_fp,
                )
            )

    catalog = catalog_from_dbt(dbt_project_path)
    warnings: list[str] = []
    if catalog is not None:
        current_schema_fp = fingerprint_from_catalog(catalog)
        for domain, section in sorted(semantic_domains.items()):
            if section.schema_hash is not None and section.schema_hash != current_schema_fp:
                stale.append(
                    StaleDomain(
                        domain=domain,
                        reason="schema_drift",
                        committed_hash=section.schema_hash,
                        current_hash=current_schema_fp,
                    )
                )
    elif semantic_domains:
        warnings.append(
            "could not build an offline catalog from the dbt project — schema drift not checked"
        )

    return CiCheckResult(
        ok=not stale,
        stale=stale,
        checked=len(semantic_domains),
        warnings=warnings,
        fix_command=fix_command,
        manifest_found=True,
    )
