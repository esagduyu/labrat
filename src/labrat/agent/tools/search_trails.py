"""search_trails: retrieve saved Trails (reusable analysis SOPs) relevant to an intent.

Trail layer (FEATURE_ROADMAP Trail v1) — near-clone of search_reference_docs.py,
keyed on kind="trail" docs instead of kind="scent". Section-level lexical retrieval
over the dual reference-doc store; deterministic, no LLM, same mechanics as
search_reference_docs / link_schema. Empty/absent store → no results (the
benchmark-safety guarantee).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.agent.tools.search_reference_docs import SectionMatch
from labrat.db.catalog import Catalog
from labrat.maze._lexical import question_tokens, stem
from labrat.maze.document import ScentDoc, Section
from labrat.maze.provenance import best_source
from labrat.maze.staleness import fingerprint_from_catalog
from labrat.maze.store import MazeStore


def _stems(text: str) -> set[str]:
    return {stem(t) for t in question_tokens(text)}


def _when_to_use_section(doc: ScentDoc) -> Section | None:
    for s in doc.sections:
        if s.heading.strip().lower() == "when to use":
            return s
    return None


class _Input(BaseModel):
    intent: str = Field(
        description="The natural-language analysis intent to match against saved Trails."
    )
    top_k: int = Field(default=5, description="Max number of matched sections to return.")


class TrailResult(BaseModel):
    intent_slug: str
    when_to_use: str | None
    sections: list[SectionMatch]
    best_source: str = "human"
    stale: bool | None = None  # any section fresh=False → True; all None → None


class _Output(BaseModel):
    intent: str
    results: list[TrailResult]


@dataclass
class _Hit:
    domain: str
    order: int
    section: Section
    score: float
    matched: list[str]


class SearchTrailsTool(Tool[_Input]):
    """Lexically retrieve the Trail sections most relevant to an intent."""

    @property
    def name(self) -> str:
        return "search_trails"

    @property
    def description(self) -> str:
        return (
            "Search saved Trails (reusable analysis SOPs) for one matching your intent — "
            "the canonical steps, reference SQL, applicable validations, and gotchas for a "
            "known analysis type. Call this before planning a recognizable analysis "
            "(retention, attribution, funnels, cohorts). Returns nothing if no Trails are saved."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        store = MazeStore.from_env(profile=ctx.profile_name)
        docs = store.docs(kind="trail")

        active = ctx.active_maps or []
        if active:
            from labrat.maze.map import resolve_members

            map_docs = [
                d for s in active if (d := store.load_domain(s, kind="map", scope=None)) is not None
            ]
            resolved = resolve_members(map_docs, store)
            allowed = set(resolved.trails)
            docs = [d for d in docs if d.domain in allowed]

        q_stems = _stems(args.intent)
        stem_to_term = {stem(t): t for t in question_tokens(args.intent)}

        catalog = ctx.catalogs.get(ctx.primary) if ctx.catalogs else None
        current_fp = fingerprint_from_catalog(catalog) if isinstance(catalog, Catalog) else None

        hits: list[_Hit] = []
        for doc in docs:
            for idx, section in enumerate(doc.sections):
                # Skip the When to use section from matching — it's only for context
                if section.heading.strip().lower() == "when to use":
                    continue
                heading_stems = _stems(f"{doc.domain} {section.heading}")
                body_stems = _stems(section.body)
                name_hits = q_stems & heading_stems
                body_hits = (q_stems & body_stems) - name_hits
                if not name_hits and not body_hits:
                    continue
                matched = sorted(stem_to_term[s] for s in (name_hits | body_hits))
                hits.append(
                    _Hit(
                        domain=doc.domain,
                        order=idx,
                        section=section,
                        score=float(2 * len(name_hits) + len(body_hits)),
                        matched=matched,
                    )
                )

        hits.sort(key=lambda h: (-h.score, h.domain, h.order))
        top = hits[: args.top_k]

        results: list[TrailResult] = []
        seen: dict[str, TrailResult] = {}
        for h in top:
            tr = seen.get(h.domain)
            if tr is None:
                tr = TrailResult(intent_slug=h.domain, when_to_use=None, sections=[])
                seen[h.domain] = tr
                results.append(tr)
            tr.sections.append(
                SectionMatch(
                    heading=h.section.heading,
                    body=h.section.body,
                    score=h.score,
                    matched_terms=h.matched,
                    source=h.section.source,
                    fresh=(
                        None
                        if current_fp is None or h.section.schema_hash is None
                        else h.section.schema_hash == current_fp
                    ),
                )
            )

        # Prepend each hit doc's When to use once, unless it's itself a matched section.
        wtu_by_domain = {d.domain: _when_to_use_section(d) for d in docs}
        for tr in results:
            wtu = wtu_by_domain.get(tr.intent_slug)
            if wtu is not None and all(s.heading != wtu.heading for s in tr.sections):
                tr.when_to_use = wtu.body

        for tr in results:
            tr.best_source = best_source([s.source for s in tr.sections])
            freshes = [s.fresh for s in tr.sections]
            if any(f is False for f in freshes):
                tr.stale = True
            elif any(f is True for f in freshes):
                tr.stale = False
            # else: all None → stale stays None

        return _Output(intent=args.intent, results=results)
