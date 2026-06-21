"""link_schema tool: narrow a wide schema to the tables relevant to a question.

Grounding tool (FEATURE_ROADMAP #25, "schema-linking: NL→relevant-tables-only").
Rather than letting the agent discover a wide warehouse table by table, score every
table by lexical overlap between the question and the table/column names and return
a ranked shortlist with columns + matched terms. Cheap, deterministic, no LLM call.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.catalog import Catalog
from labrat.maze._lexical import name_tokens, question_tokens, stem


class _Input(BaseModel):
    question: str = Field(description="The natural-language question to ground against the schema.")
    top_k: int = Field(default=10, description="Max number of tables to return.")
    database: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )


class _Match(BaseModel):
    table: str
    schema_name: str
    score: float
    matched_terms: list[str]
    columns: list[str]


class _Output(BaseModel):
    question: str
    tables: list[_Match]


class LinkSchemaTool(Tool[_Input]):
    """Rank tables by relevance to a question so the agent starts from the right ones."""

    @property
    def name(self) -> str:
        return "link_schema"

    @property
    def description(self) -> str:
        return (
            "Given the question, return the tables most likely relevant (ranked, with "
            "their columns and the terms that matched). Call this FIRST on a wide or "
            "unfamiliar schema to narrow the search space before describing tables or "
            "writing SQL, instead of scanning every table."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        catalog = cast(Catalog, ctx.catalogs[args.database or ctx.primary])
        q_stems = {stem(t) for t in question_tokens(args.question)}
        # map stem -> original question token (for human-readable matched_terms)
        stem_to_term = {stem(t): t for t in question_tokens(args.question)}

        scored: list[_Match] = []
        for schema in catalog.schemas:
            for table in schema.tables:
                name_stems = {stem(t) for t in name_tokens(table.name)}
                col_stems: set[str] = set()
                for col in table.columns:
                    col_stems.update(stem(t) for t in name_tokens(col.name))

                name_hits = q_stems & name_stems
                col_hits = (q_stems & col_stems) - name_hits
                if not name_hits and not col_hits:
                    continue
                matched = sorted(stem_to_term[s] for s in (name_hits | col_hits))
                scored.append(
                    _Match(
                        table=table.name,
                        schema_name=schema.name,
                        score=float(2 * len(name_hits) + len(col_hits)),
                        matched_terms=matched,
                        columns=[c.name for c in table.columns],
                    )
                )

        # If nothing matched lexically, fall back to every table (score 0) so the
        # agent still gets the schema rather than an empty result.
        if not scored:
            for schema in catalog.schemas:
                for table in schema.tables:
                    scored.append(
                        _Match(
                            table=table.name,
                            schema_name=schema.name,
                            score=0.0,
                            matched_terms=[],
                            columns=[c.name for c in table.columns],
                        )
                    )

        scored.sort(key=lambda m: (-m.score, m.table))
        return _Output(question=args.question, tables=scored[: args.top_k])
