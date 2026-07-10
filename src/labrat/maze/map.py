"""Map v1: a per-domain bundle of POINTERS to Scent + Trail docs.

A Map is a ``kind="map"`` ScentDoc whose sections are member lists — ``## Scent``
and ``## Trails`` sections hold bullet-list references to other domains' Scent /
Trail docs (by domain slug), ``## Suggested Prompts`` holds example questions, and
``## Overview`` is free-text. Reuses ``maze.document.ScentDoc``/``Section`` and
``maze.store.MazeStore`` as-is — no new store or parser.

References are resolved lazily and softly: ``resolve_members`` never raises on a
missing member (a Map is a set of pointers, and staleness — a renamed/deleted
target domain — must never break the bundle; it just drops from the resolved view
and shows up in ``ResolvedMembers.misses``).
"""

from __future__ import annotations

from pydantic import BaseModel

from labrat.maze.document import ScentDoc, Section
from labrat.maze.store import MazeStore

_SCENT_HEADING = "Scent"
_TRAILS_HEADING = "Trails"
_PROMPTS_HEADING = "Suggested Prompts"
_OVERVIEW_HEADING = "Overview"


def _section(doc: ScentDoc, heading: str) -> Section | None:
    for s in doc.sections:
        if s.heading.strip().lower() == heading.lower():
            return s
    return None


def _bullets(body: str) -> list[str]:
    """Parse a bullet-list body (``- <item>`` per line) into a list of items.

    Mirrors the bullet convention used by ``maze.harvest``'s drafted sections:
    lines are stripped, a leading ``"- "`` is dropped, blank lines are skipped.
    """
    items: list[str] = []
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        elif stripped.startswith("-"):
            stripped = stripped[1:].strip()
        if stripped:
            items.append(stripped)
    return items


def _members(doc: ScentDoc, heading: str) -> list[str]:
    section = _section(doc, heading)
    return _bullets(section.body) if section is not None else []


def scent_members(doc: ScentDoc) -> list[str]:
    return _members(doc, _SCENT_HEADING)


def trail_members(doc: ScentDoc) -> list[str]:
    return _members(doc, _TRAILS_HEADING)


def map_prompts(doc: ScentDoc) -> list[str]:
    return _members(doc, _PROMPTS_HEADING)


def build_map_doc(
    slug: str,
    *,
    scent: list[str],
    trails: list[str],
    prompts: list[str],
    overview: str = "",
    source: str = "human",
) -> ScentDoc:
    """Construct a ``kind="map"`` doc bundling pointers to Scent/Trail domains."""
    return ScentDoc(
        domain=slug,
        kind="map",
        sections=[
            Section(heading=_OVERVIEW_HEADING, body=overview, source=source),
            Section(heading=_SCENT_HEADING, body="\n".join(f"- {m}" for m in scent), source=source),
            Section(
                heading=_TRAILS_HEADING, body="\n".join(f"- {m}" for m in trails), source=source
            ),
            Section(
                heading=_PROMPTS_HEADING, body="\n".join(f"- {p}" for p in prompts), source=source
            ),
        ],
    )


class ResolvedMembers(BaseModel):
    scent: list[str] = []
    trails: list[str] = []
    misses: list[str] = []


def resolve_members(map_docs: list[ScentDoc], store: MazeStore) -> ResolvedMembers:
    """Union the scent/trail members referenced across ``map_docs``.

    A member whose target doc doesn't exist in the store is dropped from the
    resolved list and recorded in ``misses`` instead — never raises, so a
    stale/renamed reference degrades gracefully rather than breaking the bundle.
    """
    scent_seen: dict[str, None] = {}
    trail_seen: dict[str, None] = {}
    misses_seen: dict[str, None] = {}
    for doc in map_docs:
        for m in scent_members(doc):
            if m in scent_seen or m in misses_seen:
                continue
            if store.load_domain(m, kind="scent", scope=None) is not None:
                scent_seen[m] = None
            else:
                misses_seen[m] = None
        for m in trail_members(doc):
            if m in trail_seen or m in misses_seen:
                continue
            if store.load_domain(m, kind="trail", scope=None) is not None:
                trail_seen[m] = None
            else:
                misses_seen[m] = None
    return ResolvedMembers(
        scent=list(scent_seen), trails=list(trail_seen), misses=list(misses_seen)
    )
