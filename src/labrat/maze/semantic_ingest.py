"""dbt semantic-layer → Scent ingestion (T1b).

Pure builder + fingerprint here; the write controller (Task 3) lives below
them in this same module.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from labrat.catalog.dbt.semantic import (
    MetricDef,
    SemanticArtifacts,
    SemanticModelDef,
    parse_semantic_manifest,
)
from labrat.db.catalog import Catalog
from labrat.maze.document import ScentDoc, Section
from labrat.maze.scent_audit import ScentContaminationError, audit_scent_doc
from labrat.maze.staleness import fingerprint_from_catalog
from labrat.maze.store import MazeStore

_MANIFEST_FINGERPRINT_FILE = ".manifest_fingerprint"
_METRICS_DOMAIN = "metrics"

# Any body line that could be mistaken for a section heading or a provenance
# marker once the section is rendered-then-reparsed (see document.py's
# _H2_RE / _SOURCE_LINE_RE / _META_LINE_RE). Left unescaped, a dbt
# description containing e.g. "\n## Sneaky\n**Source:** verified" round-trips
# into a NEW section with a forged "verified" provenance token.
_INJECTION_LINE_RE = re.compile(r"^(\s*)(##\s|\*\*Source:\*\*|\*\*Meta:\*\*)")


def _sanitize_body_text(text: str) -> str:
    """Markdown-escape lines that would be parsed as a heading/provenance marker.

    Applied to every parser-provided (dbt) description string before it lands
    in a Section body — the only external-text entry point for semantic
    ingestion. Escaping (leading ``\\``) rather than stripping keeps the text
    visible, readable prose; it just stops it being *structurally* a heading
    or marker line on the next render/parse round-trip.
    """
    return "\n".join(_INJECTION_LINE_RE.sub(r"\1\\\2", line) for line in text.split("\n"))


def _model_body(m: SemanticModelDef) -> str:
    lines: list[str] = []
    if m.description:
        lines.append(_sanitize_body_text(m.description))
    for e in m.entities:
        lines.append(f"- entity `{e.name}` ({e.type})" if e.type else f"- entity `{e.name}`")
    for d in m.dimensions:
        desc = f" — {_sanitize_body_text(d.description)}" if d.description else ""
        lines.append(f"- dimension `{d.name}` ({d.type}){desc}")
    for me in m.measures:
        expr = f" = `{me.expr}`" if me.expr else ""
        desc = f" — {_sanitize_body_text(me.description)}" if me.description else ""
        lines.append(f"- measure `{me.name}` ({me.agg}){expr}{desc}")
    return "\n".join(lines)


def _metric_body(mt: MetricDef, owners: dict[str, str]) -> str:
    lines: list[str] = []
    if mt.description:
        lines.append(_sanitize_body_text(mt.description))
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


class IngestOutcome(BaseModel):
    domains: tuple[str, ...] = ()
    sections_written: int = 0
    warnings: tuple[str, ...] = ()
    skipped: bool = False
    drifted: bool = False


def ingest_dbt_semantics(
    *,
    manifest_path: Path,
    catalog: Catalog | None,
    store: MazeStore,
    project_scent_dir: Path,
    force: bool = False,
) -> IngestOutcome:
    """Ingest semantic models/metrics into project-layer Scent (replace + audit).

    Fail-open at the controller level for missing/invalid manifests (skipped +
    warning); fail-LOUD (ScentContaminationError) once content reaches the
    write path — never catch the audit.
    """
    try:
        manifest: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return IngestOutcome(skipped=True, warnings=(f"manifest unreadable: {exc}",))
    if not isinstance(manifest, dict):
        return IngestOutcome(skipped=True, warnings=("manifest is not a JSON object",))
    manifest = cast(dict[str, Any], manifest)

    fingerprint = semantic_fingerprint(manifest)
    stored = read_manifest_fingerprint(project_scent_dir)
    if stored is not None and not force:
        if stored == fingerprint:
            return IngestOutcome(skipped=True)
        return IngestOutcome(skipped=True, drifted=True)

    artifacts = parse_semantic_manifest(manifest)
    if not artifacts.models and not artifacts.metrics:
        return IngestOutcome(skipped=True, warnings=tuple(artifacts.warnings))

    schema_hash = fingerprint_from_catalog(catalog) if catalog is not None else None
    drafts = build_semantic_sections(artifacts, schema_hash)

    written = 0
    for domain in sorted(drafts):
        doc = store.load_domain(domain, scope="project") or ScentDoc(domain=domain)
        doc.sections = [s for s in doc.sections if s.source != "semantic_layer"]
        doc.sections.extend(drafts[domain])
        tag = audit_scent_doc(doc)
        if tag:
            raise ScentContaminationError(
                f"semantic ingestion for {domain!r} tripped contamination guard: {tag}"
            )
        store.write_doc(doc)
        written += len(drafts[domain])

    write_manifest_fingerprint(project_scent_dir, fingerprint)
    return IngestOutcome(
        domains=tuple(sorted(drafts)),
        sections_written=written,
        warnings=tuple(artifacts.warnings),
    )
