"""dbt semantic-layer → Scent ingestion (T1b).

Pure builder + fingerprint here; the write controller (Task 3) lives below
them in this same module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from labrat.catalog.dbt.semantic import MetricDef, SemanticArtifacts, SemanticModelDef
from labrat.maze.document import Section

_MANIFEST_FINGERPRINT_FILE = ".manifest_fingerprint"
_METRICS_DOMAIN = "metrics"


def _model_body(m: SemanticModelDef) -> str:
    lines: list[str] = []
    if m.description:
        lines.append(m.description)
    for e in m.entities:
        lines.append(f"- entity `{e.name}` ({e.type})" if e.type else f"- entity `{e.name}`")
    for d in m.dimensions:
        desc = f" — {d.description}" if d.description else ""
        lines.append(f"- dimension `{d.name}` ({d.type}){desc}")
    for me in m.measures:
        expr = f" = `{me.expr}`" if me.expr else ""
        desc = f" — {me.description}" if me.description else ""
        lines.append(f"- measure `{me.name}` ({me.agg}){expr}{desc}")
    return "\n".join(lines)


def _metric_body(mt: MetricDef, owners: dict[str, str]) -> str:
    lines: list[str] = []
    if mt.description:
        lines.append(mt.description)
    lines.append(f"- type: {mt.type}")
    for ref in mt.measure_refs:
        owner = owners.get(ref)
        lines.append(f"- uses `{ref}`" + (f" (from `{owner}`)" if owner else ""))
    return "\n".join(lines)


def build_semantic_sections(
    artifacts: SemanticArtifacts, schema_hash: str | None
) -> dict[str, list[Section]]:
    """Route semantic models to their table domains; metrics to owner-or-'metrics'."""
    owners: dict[str, str] = {}  # measure name -> semantic model TABLE (domain)
    for m in artifacts.models:
        for me in m.measures:
            owners.setdefault(me.name, m.table)

    out: dict[str, list[Section]] = {}

    def _add(domain: str, heading: str, body: str) -> None:
        out.setdefault(domain, []).append(
            Section(heading=heading, body=body, source="semantic_layer", schema_hash=schema_hash)
        )

    for m in artifacts.models:  # already name-sorted by the parser
        _add(m.table, f"Semantic Model: {m.name}", _model_body(m))
    for mt in artifacts.metrics:
        title = mt.label or mt.name
        if mt.type == "simple" and mt.measure_refs and mt.measure_refs[0] in owners:
            domain = owners[mt.measure_refs[0]]
        else:
            domain = _METRICS_DOMAIN
        _add(domain, f"Metric: {title}", _metric_body(mt, owners))
    return out


def semantic_fingerprint(manifest: dict[str, Any]) -> str:
    """sha256 over ONLY the semantic subset — model-body churn must not signal drift."""
    subset: dict[str, Any] = {
        "semantic_models": manifest.get("semantic_models") or {},
        "metrics": manifest.get("metrics") or {},
    }
    canonical = json.dumps(subset, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_manifest_fingerprint(scent_dir: Path) -> str | None:
    path = scent_dir / _MANIFEST_FINGERPRINT_FILE
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def write_manifest_fingerprint(scent_dir: Path, fingerprint: str) -> None:
    scent_dir.mkdir(parents=True, exist_ok=True)
    (scent_dir / _MANIFEST_FINGERPRINT_FILE).write_text(fingerprint + "\n", encoding="utf-8")
