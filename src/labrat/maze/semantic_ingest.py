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
from labrat.maze.gitmeta import current_git_sha
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
    cleared: int = 0


def _clear_stale_semantic_sections(
    store: MazeStore, project_scent_dir: Path
) -> tuple[int, tuple[str, ...]]:
    """Strip every ``semantic_layer`` section from committed PROJECT-layer docs.

    Runs when a re-ingest finds no semantic models/metrics left in the dbt
    manifest (F3). Without this, the empty-artifacts branch would no-op and
    leave stale ``semantic_layer`` sections (and a stale
    ``.manifest_fingerprint`` sidecar) behind forever — `scent check` would
    keep reporting drift with no way to clear it. Enumerates
    ``project_scent_dir/*.md`` directly (the same on-disk convention
    ``MazeStore.write_doc`` uses for the project layer: domain == file stem)
    rather than adding a new store API. A doc with no ``semantic_layer``
    sections is left untouched (no read/audit/write) — nothing to clear.
    """
    if not project_scent_dir.is_dir():
        return 0, ()
    cleared = 0
    cleared_domains: list[str] = []
    for path in sorted(project_scent_dir.glob("*.md")):
        domain = path.stem
        doc = store.load_domain(domain, scope="project")
        if doc is None:
            continue
        stale = [s for s in doc.sections if s.source == "semantic_layer"]
        if not stale:
            continue
        doc.sections = [s for s in doc.sections if s.source != "semantic_layer"]
        tag = audit_scent_doc(doc)
        if tag:
            raise ScentContaminationError(
                f"semantic clear-pass for {domain!r} tripped contamination guard: {tag}"
            )
        store.write_doc(doc)
        cleared += len(stale)
        cleared_domains.append(domain)
    return cleared, tuple(cleared_domains)


def ingest_dbt_semantics(
    *,
    manifest_path: Path,
    catalog: Catalog | None,
    store: MazeStore,
    project_scent_dir: Path,
    force: bool = False,
    git_root: Path | None = None,
) -> IngestOutcome:
    """Ingest semantic models/metrics into project-layer Scent (replace + audit).

    Fail-open at the controller level for missing/invalid manifests (skipped +
    warning); fail-LOUD (ScentContaminationError) once content reaches the
    write path — never catch the audit.

    When ``git_root`` is given and resolves to a git sha, every built section
    is stamped ``git_sha=<sha>`` before the per-domain audit (stamp-then-audit
    order: audited bytes == written bytes). Default ``git_root=None`` is
    byte-identical to before this stamping existed.
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
        # Past the fingerprint gate above (stored is None -> genuinely first
        # contact, or force=True -> caller asked to reconcile) with nothing
        # to ingest. A prior ingest may have left semantic_layer sections
        # behind (the project's semantic models were deleted) — clear them
        # so `scent check` doesn't stay stale forever (F3). On a project
        # that has never been ingested (or was always semantics-free), the
        # glob finds nothing with a semantic_layer section, so this is a
        # no-op past the directory-existence check: cleared stays 0.
        cleared, cleared_domains = _clear_stale_semantic_sections(store, project_scent_dir)
        write_manifest_fingerprint(project_scent_dir, fingerprint)
        return IngestOutcome(
            domains=cleared_domains,
            cleared=cleared,
            skipped=True,
            warnings=tuple(artifacts.warnings),
        )

    schema_hash = fingerprint_from_catalog(catalog) if catalog is not None else None
    drafts = build_semantic_sections(artifacts, schema_hash)

    sha = current_git_sha(git_root) if git_root is not None else None
    if sha:
        drafts = {
            domain: [s.model_copy(update={"git_sha": sha}) for s in sections]
            for domain, sections in drafts.items()
        }

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
