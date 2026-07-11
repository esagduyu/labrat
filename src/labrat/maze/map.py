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

import json
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from labrat.maze.document import ScentDoc, Section
from labrat.maze.scent_audit import ScentContaminationError, audit_scent_doc
from labrat.maze.store import MazeStore

_SCENT_HEADING = "Scent"
_TRAILS_HEADING = "Trails"
_PROMPTS_HEADING = "Suggested Prompts"
_OVERVIEW_HEADING = "Overview"

# fqn[-2] folder-name conventions that mark staging/intermediate layers, not
# domains — a Map auto-seeded from these would bundle plumbing, not a
# business area. Case-insensitive.
_NON_DOMAIN_FOLDERS = {"staging", "stg", "intermediate", "int", "base"}


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
            # Deliberately more lenient than the harvest "- " convention: tolerate
            # a bare "-" prefix (no following space) so hand-edited Map docs parse.
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


def _table_for_node(node: dict[str, Any]) -> str:
    """Table name for a dbt manifest model node: alias-first, mirroring
    ``catalog.dbt.semantic._table_for``'s resolution."""
    alias = node.get("alias")
    if isinstance(alias, str) and alias:
        return alias
    name = node.get("name")
    return name if isinstance(name, str) else ""


def draft_maps_from_dbt(
    manifest_path: Path,
    *,
    existing_scent_domains: set[str],
    generated_at: str,
    model_id: str | None = None,
) -> dict[str, ScentDoc]:
    """Cartographer dbt-structure auto-seed: sketch ``kind="map"`` skeletons
    from a dbt project's folder structure.

    Groups model nodes by their immediate parent folder (``fqn[-2]``),
    skipping staging/intermediate-convention folders (not domains) and nodes
    with fewer than 2 fqn segments. Each group's Scent members are its
    models' table names, filtered to ``existing_scent_domains`` — a Map only
    points at Scent that already exists. Deterministic, no LLM. Fail-open on
    an unreadable/invalid manifest (mirrors ``semantic_ingest.py``); fail-loud
    (``ScentContaminationError``) if a drafted doc trips the contamination
    audit.
    """
    try:
        manifest: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(manifest, dict):
        return {}
    manifest = cast(dict[str, Any], manifest)

    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        return {}
    nodes = cast(dict[str, Any], nodes)

    groups: dict[str, list[str]] = {}
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        node = cast(dict[str, Any], node)
        if node.get("resource_type") != "model":
            continue
        fqn_raw = node.get("fqn")
        if not isinstance(fqn_raw, list):
            continue
        fqn = cast(list[Any], fqn_raw)
        if len(fqn) < 2:
            continue
        folder = fqn[-2]
        if not isinstance(folder, str) or folder.lower() in _NON_DOMAIN_FOLDERS:
            continue
        table = _table_for_node(node)
        if not table:
            continue
        groups.setdefault(folder, []).append(table)

    out: dict[str, ScentDoc] = {}
    for folder in sorted(groups):
        members = sorted({t for t in groups[folder] if t in existing_scent_domains})
        if not members:
            continue
        doc = build_map_doc(slug=folder, scent=members, trails=[], prompts=[], source="draft")
        doc.sections = [
            s.model_copy(update={"generated_at": generated_at, "model_id": model_id})
            for s in doc.sections
        ]
        tag = audit_scent_doc(doc)
        if tag:
            raise ScentContaminationError(
                f"dbt auto-seed for {folder!r} tripped contamination guard: {tag}"
            )
        out[folder] = doc
    return out


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
    scent_misses: dict[str, None] = {}
    trail_misses: dict[str, None] = {}
    for doc in map_docs:
        for m in scent_members(doc):
            if m in scent_seen or m in scent_misses:
                continue
            if store.load_domain(m, kind="scent", scope=None) is not None:
                scent_seen[m] = None
            else:
                scent_misses[m] = None
        for m in trail_members(doc):
            if m in trail_seen or m in trail_misses:
                continue
            if store.load_domain(m, kind="trail", scope=None) is not None:
                trail_seen[m] = None
            else:
                trail_misses[m] = None
    return ResolvedMembers(
        scent=list(scent_seen),
        trails=list(trail_seen),
        misses=list(dict.fromkeys([*scent_misses, *trail_misses])),
    )
