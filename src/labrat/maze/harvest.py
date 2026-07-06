"""Promote clustered correction memories into drafted, audited Scent sections (T2b v1).

Draft-only: the returned Sections are shown to a human for approval before any
MazeStore write. Every drafted body is contamination-audited (fail-loud).
"""

from __future__ import annotations

from labrat.maze.document import ScentDoc, Section
from labrat.maze.scent_audit import ScentContaminationError, detect_contamination
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
) -> list[Section]:
    sections: list[Section] = []
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
        sections.append(
            Section(
                heading="Gotchas",
                body=body,
                source="harvested",
                generated_at=generated_at,
                model_id=model_id,
            )
        )
    return sections


def apply_approved_sections(store: MazeStore, domain: str, approved: list[Section]) -> None:
    """Merge human-approved harvested sections into the domain's Scent doc and persist.

    Dedups against existing section bodies so re-approving the same bullet is idempotent.
    """
    if not approved:
        return
    doc = store.load_domain(domain) or ScentDoc(domain=domain)
    existing_bodies = {s.body.strip() for s in doc.sections}
    for s in approved:
        if s.body.strip() not in existing_bodies:
            doc.sections.append(s)
            existing_bodies.add(s.body.strip())
    store.write_doc(doc)
