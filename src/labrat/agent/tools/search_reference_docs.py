"""search_reference_docs: retrieve curated reference docs relevant to a question.

Scent layer (FEATURE_ROADMAP #26a) — the consume half of the Rat Maze. Section-level
lexical retrieval over the dual reference-doc store; deterministic, no LLM, same mechanics
as link_schema. Empty/absent store → no results (the benchmark-safety guarantee).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.catalog import Catalog
from labrat.maze._lexical import question_tokens, stem
from labrat.maze.document import Section
from labrat.maze.provenance import best_source
from labrat.maze.staleness import fingerprint_from_catalog
from labrat.maze.store import MazeStore


def _stems(text: str) -> set[str]:
    return {stem(t) for t in question_tokens(text)}


class _Input(BaseModel):
    question: str = Field(
        description="The natural-language question to ground against the reference docs."
    )
    top_k: int = Field(default=5, description="Max number of matched sections to return.")


class SectionMatch(BaseModel):
    heading: str
    body: str
    score: float
    matched_terms: list[str]
    source: str = "human"
    fresh: bool | None = None  # None = no schema_hash meta / no catalog → unknown


class DocResult(BaseModel):
    domain: str
    quick_reference: str | None
    sections: list[SectionMatch]
    best_source: str = "human"
    stale: bool | None = None  # any section fresh=False → True; all None → None


class _Output(BaseModel):
    question: str
    results: list[DocResult]


@dataclass
class _Hit:
    domain: str
    order: int
    section: Section
    score: float
    matched: list[str]


class SearchReferenceDocsTool(Tool[_Input]):
    """Lexically retrieve the reference-doc sections most relevant to a question."""

    @property
    def name(self) -> str:
        return "search_reference_docs"

    @property
    def description(self) -> str:
        return (
            "Search the curated reference docs for grounding relevant to the question — "
            "metric definitions, join keys, table grain, and known data-quality gotchas for "
            "this warehouse. Call this FIRST, before profiling or writing SQL. Returns nothing "
            "if no reference docs are configured."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        store = MazeStore.from_env(profile=ctx.profile_name)
        docs = store.docs(kind="scent")

        active = ctx.active_maps or []
        if active:
            from labrat.maze.map import resolve_members

            map_docs = [
                d for s in active if (d := store.load_domain(s, kind="map", scope=None)) is not None
            ]
            resolved = resolve_members(map_docs, store)
            allowed = set(resolved.scent)
            docs = [d for d in docs if d.domain in allowed]

        q_stems = _stems(args.question)
        stem_to_term = {stem(t): t for t in question_tokens(args.question)}

        catalog = ctx.catalogs.get(ctx.primary) if ctx.catalogs else None
        current_fp = fingerprint_from_catalog(catalog) if isinstance(catalog, Catalog) else None

        hits: list[_Hit] = []
        for doc in docs:
            for idx, section in enumerate(doc.sections):
                # Skip the Quick Reference section from matching — it's only for context
                if section.heading.strip().lower() == "quick reference":
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

        if ctx.hybrid_retrieval:
            from labrat.maze.hybrid import hybrid_section_keys

            fused = hybrid_section_keys(
                args.question,
                docs,
                skip_heading="quick reference",
                lexical_order=[(h.domain, h.order) for h in hits],
                profile=ctx.profile_name,
                kind="scent",
            )
            if fused is not None:
                by_key = {(h.domain, h.order): h for h in hits}
                sections = {
                    (doc.domain, idx): s for doc in docs for idx, s in enumerate(doc.sections)
                }
                hits = [
                    by_key.get(key)
                    or _Hit(
                        domain=key[0], order=key[1], section=sections[key], score=0.0, matched=[]
                    )
                    for key in fused
                ]

        top = hits[: args.top_k]

        results: list[DocResult] = []
        seen: dict[str, DocResult] = {}
        for h in top:
            dr = seen.get(h.domain)
            if dr is None:
                dr = DocResult(domain=h.domain, quick_reference=None, sections=[])
                seen[h.domain] = dr
                results.append(dr)
            dr.sections.append(
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

        # Prepend each hit doc's Quick Reference once, unless the QR is itself a matched section.
        qr_by_domain = {d.domain: d.quick_reference() for d in docs}
        for dr in results:
            qr = qr_by_domain.get(dr.domain)
            if qr is not None and all(s.heading != qr.heading for s in dr.sections):
                dr.quick_reference = qr.body

        for dr in results:
            dr.best_source = best_source([s.source for s in dr.sections])
            freshes = [s.fresh for s in dr.sections]
            if any(f is False for f in freshes):
                dr.stale = True
            elif any(f is True for f in freshes):
                dr.stale = False
            # else: all None → stale stays None

        return _Output(question=args.question, results=results)
