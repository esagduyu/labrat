"""explain_lineage: deterministic column-level lineage via sqlglot (no execution, no LLM).

Traces each output column of a SQL query back to the base-table columns it derives
from, resolving against the live introspected Catalog — never a dbt manifest
(manifests go stale; a live parse is always current). Mirrors check_sql's
parse-only, fail-soft design: a ParseError / unresolved column returns a
structured parse_error, never raises.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import Node
from sqlglot.optimizer.qualify import qualify

from labrat.db.catalog import Catalog


class _SourceRef(BaseModel):
    table: str
    column: str


# NOTE: _catalog_schema_dict / _flatten / _output_columns are pure helpers with
# no in-tree consumer yet — the ExplainLineageTool that calls them lands in the
# next task (Unit D). Until then they're only exercised by
# tests/unit/test_explain_lineage.py, which pyright's `include = ["src"]` scope
# doesn't see, so each needs a targeted reportUnusedFunction suppression. Task 7
# should remove these three ignore comments once the Tool wires the calls in.
def _catalog_schema_dict(  # pyright: ignore[reportUnusedFunction]
    cat: Catalog,
) -> dict[str, dict[str, str]]:
    """sqlglot schema mapping {table: {column: data_type}} across all schemas.

    Adapts check_sql._catalog_index, but keeps original casing and data types —
    sqlglot.lineage() wants the {table: {col: dtype}} schema form.
    """
    schema: dict[str, dict[str, str]] = {}
    for sch in cat.schemas:
        for t in sch.tables:
            schema.setdefault(t.name, {}).update({c.name: c.data_type for c in t.columns})
    return schema


def _leaves(node: Node) -> list[Node]:
    if not node.downstream:
        return [node]
    out: list[Node] = []
    for child in node.downstream:
        out.extend(_leaves(child))
    return out


def _flatten(node: Node) -> list[_SourceRef]:  # pyright: ignore[reportUnusedFunction]
    """Flatten a lineage Node tree to deduplicated base-table {table, column} pairs.

    A leaf is a node with no downstream. It maps to a base column iff its .source
    is an exp.Table (a literal-only projection has a Select source and yields
    nothing). node.name is '<alias-or-table>.<column>' (possibly quoted) — the real
    table name comes from leaf.source.name and the column name from
    exp.to_column(leaf.name).name (which strips identifier quoting).
    """
    refs: list[_SourceRef] = []
    seen: set[tuple[str, str]] = set()
    for leaf in _leaves(node):
        src = leaf.source
        if not isinstance(src, exp.Table):
            continue
        pair = (src.name, exp.to_column(leaf.name).name)
        if pair in seen:
            continue
        seen.add(pair)
        refs.append(_SourceRef(table=pair[0], column=pair[1]))
    return refs


def _output_columns(  # pyright: ignore[reportUnusedFunction]
    query: exp.Query, schema: dict[str, dict[str, str]]
) -> list[str]:
    """Names of the query's final projections; '*' is expanded via qualify(schema).

    A star that cannot be resolved (table missing from the schema) survives as '*'
    and is skipped — fail-soft, consistent with check_sql's fail-open stance.
    """
    try:
        # qualify's `schema` param is `dict[str, object] | Schema | None`; dict's
        # invariant value type means our dict[str, dict[str, str]] isn't directly
        # assignable even though it satisfies the shape at runtime.
        qualified = qualify(query.copy(), schema=cast(dict[str, object], schema))
    except SqlglotError:
        qualified = query
    return [s.alias_or_name for s in qualified.selects if s.alias_or_name != "*"]
