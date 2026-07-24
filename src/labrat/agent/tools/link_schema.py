"""link_schema tool: narrow a wide schema to the tables relevant to a question.

Grounding tool (FEATURE_ROADMAP #25, "schema-linking: NL→relevant-tables-only").
Rather than letting the agent discover a wide warehouse table by table, score every
table by lexical overlap between the question and the table/column names and return
a ranked shortlist with columns + matched terms. Cheap, deterministic, no LLM call.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, cast

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.catalog import Catalog
from labrat.maze._lexical import name_tokens, question_tokens, stem

# Column-disambiguation grounding (DAB autopsy "lever D" — agents repeatedly pick the
# wrong column when a table has a code column vs a friendlier name column). Deterministic,
# name-based only here (link_schema already scans every table in the catalog, so it must
# not add a per-table DB round-trip); describe_table.py adds a value-based pass on top of
# the same name heuristics below.
#
# P1 fix (2026-07): a Fable whole-branch review found this heuristic firing on ubiquitous
# NORMAL tables (customers(id,name), products(product_id,product_name), users(id,
# full_name), reviews(review_id,business_name) all wrongly emitted the code/label hint).
# The root cause was treating bare "id"/"no" as domain-code markers — virtually every
# table has an "*_id" surrogate key next to a descriptive "*_name" column, so that pattern
# is not a lever-D signal at all, it's just... a normal table. Only "code"/"cd" are kept as
# markers now, AND a marker alone isn't enough — the column must carry a real qualifier
# beyond the marker itself (e.g. "icd_o_code", "cpc_code"; bare "code" alone still doesn't
# count). This also means a name-only hierarchy/level hint can't be made safe (any table
# can have a "parent_id" or "level" column with nothing lever-D-shaped about it) — dropped
# entirely from this module; describe_table.py keeps ONLY the value-based nested-prefix
# check for hierarchy (real CPC/ICD-style codes whose *values* nest), which is a genuine
# signal that name-alone can never distinguish from a normal table.
CODE_NAME_TOKENS = {"code", "cd"}
NAME_LABEL_TOKENS = {"name", "title", "label", "desc", "description"}


class _ColumnLike(Protocol):
    name: str


def _is_code_like(col_name: str) -> bool:
    """A code marker ("code"/"cd") AND a real qualifier beyond the marker itself.

    Bare "id"/"no" columns (customer_id, invoice_no, ...) are surrogate keys, not domain
    codes — excluded entirely. A bare "code"/"cd" with no qualifier (nothing left after
    stripping the marker) is too generic to trust either.
    """
    toks = set(name_tokens(col_name))
    if not (toks & CODE_NAME_TOKENS):
        return False
    return bool(toks - CODE_NAME_TOKENS)


def _is_name_like(col_name: str) -> bool:
    return bool(set(name_tokens(col_name)) & NAME_LABEL_TOKENS)


def _code_name_hints(columns: Sequence[_ColumnLike]) -> list[str]:
    """Flag code/label column pairs so the agent groups/selects the CODE, not the name.

    A code-like column (name contains a qualified "code"/"cd" marker, e.g. ``icd_o_code``,
    ``region_code`` — bare ``id``/``no`` don't count, see ``_is_code_like``) is paired with
    a name-like sibling (name/title/label/desc) — preferring a shared non-marker name stem
    (``region_code`` + ``region_name``), falling back to an unambiguous single
    code-candidate + single name-candidate pairing (``icd_o_code`` + ``histology_name``,
    which share no stem at all).
    """
    code_cols = [c.name for c in columns if _is_code_like(c.name)]
    name_cols = [c.name for c in columns if _is_name_like(c.name)]
    if not code_cols or not name_cols:
        return []

    hints: list[str] = []
    used_names: set[str] = set()
    for code_col in code_cols:
        code_stems = set(name_tokens(code_col)) - CODE_NAME_TOKENS
        match: str | None = None
        for name_col in name_cols:
            if name_col == code_col or name_col in used_names:
                continue
            name_stems = set(name_tokens(name_col)) - NAME_LABEL_TOKENS
            if code_stems & name_stems:
                match = name_col
                break
        if match is None and len(code_cols) == 1 and len(name_cols) == 1:
            candidate = name_cols[0]
            if candidate != code_col:
                match = candidate
        if match is not None:
            used_names.add(match)
            hints.append(
                f"columns {code_col} (code-like) and {match} (name-like) look like a "
                "code/label pair — if the question asks for a code/id/classification, "
                f"group/select the CODE column ({code_col}), not the friendlier name."
            )
    return hints


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
    column_hints: list[str] = Field(default_factory=list)


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
                        column_hints=_code_name_hints(table.columns),
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
                            column_hints=_code_name_hints(table.columns),
                        )
                    )

        scored.sort(key=lambda m: (-m.score, m.table))
        return _Output(question=args.question, tables=scored[: args.top_k])
