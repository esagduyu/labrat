"""Promote clustered correction memories into drafted, audited Scent sections (T2b v1).

Draft-only: the returned Sections are shown to a human for approval before any
MazeStore write. Every drafted body is contamination-audited (fail-loud).
"""

from __future__ import annotations

from pathlib import Path

from labrat.maze.document import ScentDoc, Section
from labrat.maze.gitmeta import current_git_sha
from labrat.maze.scent_audit import (
    ScentContaminationError,
    audit_scent_doc,
    detect_contamination,
)
from labrat.maze.store import MazeStore
from labrat.memory.model import Memory, MemoryKind

_CORRECTION_KINDS = {MemoryKind.edit_derived, MemoryKind.chat_correction}
_GLOBAL_KEY = "__global__"


def _cluster_by_scope(memories: list[Memory]) -> dict[str, list[Memory]]:
    """Group memories by ``table_scope`` (``"__global__"`` for ungrouped). No kind filter."""
    clusters: dict[str, list[Memory]] = {}
    for m in memories:
        key = m.table_scope or _GLOBAL_KEY
        clusters.setdefault(key, []).append(m)
    return clusters


def cluster_corrections(memories: list[Memory]) -> dict[str, list[Memory]]:
    return _cluster_by_scope([m for m in memories if m.kind in _CORRECTION_KINDS])


def cluster_decisions(memories: list[Memory]) -> dict[str, list[Memory]]:
    """Cluster analyst-stated durable rules (``MemoryKind.explicit_user_rule``) by scope."""
    return _cluster_by_scope([m for m in memories if m.kind == MemoryKind.explicit_user_rule])


def _draft_sections(
    clusters: dict[str, list[Memory]],
    *,
    heading: str,
    generated_at: str,
    model_id: str | None = None,
) -> dict[str, list[Section]]:
    """Shared drafting body for ``draft_harvested_sections``/``draft_decision_sections``.

    Deduped, verbatim bullets; contamination-audited fail-loud; provenance
    ``source="harvested"``. Returns a dict keyed by cluster key (a
    ``table_scope`` value, or ``"__global__"`` for the ungrouped cluster).
    Callers map ``"__global__"`` to a Scent domain via
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
            heading=heading,
            body=body,
            source="harvested",
            generated_at=generated_at,
            model_id=model_id,
        )
        out.setdefault(key, []).append(section)
    return out


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
    return _draft_sections(
        clusters, heading="Gotchas", generated_at=generated_at, model_id=model_id
    )


def draft_decision_sections(
    clusters: dict[str, list[Memory]],
    *,
    generated_at: str,
    model_id: str | None = None,
) -> dict[str, list[Section]]:
    """Draft harvested Decisions sections per cluster.

    Structurally identical to ``draft_harvested_sections`` except heading
    ``"Decisions"`` — analyst-stated durable rules, drafted verbatim.
    """
    return _draft_sections(
        clusters, heading="Decisions", generated_at=generated_at, model_id=model_id
    )


def apply_approved_sections(
    store: MazeStore,
    domain: str,
    approved: list[Section],
    *,
    git_root: Path | None = None,
) -> None:
    """Merge human-approved harvested sections into the domain's PROJECT-layer doc.

    Loads only the project layer (never the merged view), so user-layer
    Cartographer content is never copied — M2's user-scope refresh can never be
    shadowed by a frozen project copy (spec 2026-07-09 non-negotiable #2).
    Dedups against existing project-layer section bodies so re-approving the
    same bullet is idempotent. Audits the doc fail-loud BEFORE writing.

    When ``git_root`` is given and resolves to a git sha, newly-appended
    sections are stamped ``git_sha=<sha>`` (BEFORE the audit, so audited bytes
    == written bytes). Dedup-skipped sections are left untouched — re-applying
    an already-present body never overwrites its original stamp. Default
    ``git_root=None`` is byte-identical to before this stamping existed (the
    Meta renderer omits ``None`` fields).
    """
    if not approved:
        return
    sha = current_git_sha(git_root) if git_root is not None else None
    doc = store.load_domain(domain, scope="project") or ScentDoc(domain=domain)
    existing_bodies = {s.body.strip() for s in doc.sections}
    for s in approved:
        if s.body.strip() not in existing_bodies:
            doc.sections.append(s.model_copy(update={"git_sha": sha}) if sha else s)
            existing_bodies.add(s.body.strip())
    tag = audit_scent_doc(doc)
    if tag:
        raise ScentContaminationError(
            f"approved sections for {domain!r} tripped contamination guard: {tag}"
        )
    store.write_doc(doc)
