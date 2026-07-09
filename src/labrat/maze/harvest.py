"""Promote clustered correction memories into drafted, audited Scent sections (T2b v1).

Draft-only: the returned Sections are shown to a human for approval before any
MazeStore write. Every drafted body is contamination-audited (fail-loud).
"""

from __future__ import annotations

from labrat.maze.document import ScentDoc, Section
from labrat.maze.scent_audit import (
    ScentContaminationError,
    audit_scent_doc,
    detect_contamination,
)
from labrat.maze.store import MazeStore
from labrat.memory.model import Memory, MemoryKind

_CORRECTION_KINDS = {MemoryKind.edit_derived, MemoryKind.chat_correction}
_GLOBAL_KEY = "__global__"


def cluster_corrections(memories: list[Memory]) -> dict[str, list[Memory]]:
    clusters: dict[str, list[Memory]] = {}
    for m in memories:
        if m.kind not in _CORRECTION_KINDS:
            continue
        key = m.table_scope or _GLOBAL_KEY
        clusters.setdefault(key, []).append(m)
    return clusters


def draft_harvested_sections(
    clusters: dict[str, list[Memory]],
    *,
    generated_at: str,
    model_id: str | None = None,
) -> dict[str, list[Section]]:
    """Draft harvested Gotchas sections per cluster.

    Returns a dict keyed by cluster key (a ``table_scope`` value, or
    ``"__global__"`` for the ungrouped cluster) mapping to the drafted
    sections for that cluster (one per cluster today; list-valued for
    future headroom). Callers map ``"__global__"`` to a Scent domain via
    ``harvest_controller.domain_for_cluster``.
    """
    out: dict[str, list[Section]] = {}
    for key in sorted(clusters):
        seen: set[str] = set()
        bullets: list[str] = []
        for m in clusters[key]:
            t = m.text.strip()
            if t and t not in seen:
                seen.add(t)
                bullets.append(f"- {t}")
        if not bullets:
            continue
        body = "\n".join(bullets)
        hit = detect_contamination(body)
        if hit:
            raise ScentContaminationError(
                f"harvested draft for {key!r} tripped contamination guard: {hit}"
            )
        section = Section(
            heading="Gotchas",
            body=body,
            source="harvested",
            generated_at=generated_at,
            model_id=model_id,
        )
        out.setdefault(key, []).append(section)
    return out


def apply_approved_sections(store: MazeStore, domain: str, approved: list[Section]) -> None:
    """Merge human-approved harvested sections into the domain's Scent doc and persist.

    Dedups against existing section bodies so re-approving the same bullet is idempotent.

    Note: ``load_domain`` returns the merged (user+project) view, while ``write_doc``
    defaults to the project layer — so approving a section for a domain that currently
    exists only in the user layer will copy that doc into the project layer.
    """
    if not approved:
        return
    doc = store.load_domain(domain) or ScentDoc(domain=domain)
    existing_bodies = {s.body.strip() for s in doc.sections}
    for s in approved:
        if s.body.strip() not in existing_bodies:
            doc.sections.append(s)
            existing_bodies.add(s.body.strip())
    tag = audit_scent_doc(doc)
    if tag:
        raise ScentContaminationError(
            f"approved sections for {domain!r} tripped contamination guard: {tag}"
        )
    store.write_doc(doc)
