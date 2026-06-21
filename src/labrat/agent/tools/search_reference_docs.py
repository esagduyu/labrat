"""search_reference_docs: retrieve curated reference docs relevant to a question.

Scent layer (FEATURE_ROADMAP #26a) — the consume half of the Rat Maze. Section-level
lexical retrieval over the dual reference-doc store; deterministic, no LLM, same mechanics
as link_schema. Empty/absent store → no results (the benchmark-safety guarantee).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.maze._lexical import question_tokens, stem
from labrat.maze.document import Section
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


class DocResult(BaseModel):
    domain: str
    quick_reference: str | None
    sections: list[SectionMatch]


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
        docs = MazeStore.from_env(profile=ctx.profile_name).docs(kind="scent")
        q_stems = _stems(args.question)
        stem_to_term = {stem(t): t for t in question_tokens(args.question)}

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
                )
            )

        # Prepend each hit doc's Quick Reference once, unless the QR is itself a matched section.
        qr_by_domain = {d.domain: d.quick_reference() for d in docs}
        for dr in results:
            qr = qr_by_domain.get(dr.domain)
            if qr is not None and all(s.heading != qr.heading for s in dr.sections):
                dr.quick_reference = qr.body

        return _Output(question=args.question, results=results)
