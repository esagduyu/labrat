"""explain_lineage: deterministic column-level lineage via sqlglot (no execution, no LLM).

Traces each output column of a SQL query back to the base-table columns it derives
from, resolving against the live introspected Catalog — never a dbt manifest
(manifests go stale; a live parse is always current). Mirrors check_sql's
parse-only, fail-soft design: a ParseError / unresolved column returns a
structured parse_error, never raises.
"""

from __future__ import annotations

from typing import cast

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import Node, lineage
from sqlglot.optimizer.qualify import qualify

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.catalog import Catalog


class _SourceRef(BaseModel):
    table: str
    column: str


def _catalog_schema_dict(cat: Catalog) -> dict[str, dict[str, str]]:
    """sqlglot schema mapping {table: {column: data_type}} across all schemas.

    Adapts check_sql._catalog_index, but keeps original casing and data types —
    sqlglot.lineage() wants the {table: {col: dtype}} schema form.

    Zero-column tables (e.g. a view whose columns failed to introspect) are
    dropped: sqlglot's MappingSchema raises SchemaError GLOBALLY the instant ANY
    entry has zero columns, which would poison every lineage() call against this
    schema — even queries that never touch that table. A table with no known
    columns can never be a valid lineage source, so dropping it only prevents
    poisoning; it can never hide a legitimate resolution.
    """
    schema: dict[str, dict[str, str]] = {}
    for sch in cat.schemas:
        for t in sch.tables:
            schema.setdefault(t.name, {}).update({c.name: c.data_type for c in t.columns})
    return {name: cols for name, cols in schema.items() if cols}


def _leaves(node: Node) -> list[Node]:
    if not node.downstream:
        return [node]
    out: list[Node] = []
    for child in node.downstream:
        out.extend(_leaves(child))
    return out


def _flatten(node: Node) -> list[_SourceRef]:
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


def _output_columns(query: exp.Query, schema: dict[str, dict[str, str]]) -> list[str]:
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


class _Input(BaseModel):
    sql: str = Field(description="The SQL query to trace lineage for (not executed).")
    database: str | None = Field(default=None, description="Connection name; defaults to primary.")
    column: str | None = Field(
        default=None,
        description="Trace only this output column; omit to trace every output column.",
    )


class _ColumnLineage(BaseModel):
    output_column: str
    sources: list[_SourceRef]
    derivation: str


class _Output(BaseModel):
    columns: list[_ColumnLineage]
    parse_error: str | None = None


class ExplainLineageTool(Tool[_Input]):
    """Column-level lineage for a query, resolved live against the Catalog."""

    @property
    def name(self) -> str:
        return "explain_lineage"

    @property
    def description(self) -> str:
        return (
            "Trace column-level lineage for a SQL query WITHOUT executing it: for each "
            "output column, report the base-table columns it derives from plus the "
            "deriving expression. Use it to answer 'where does this column come from?' "
            "and to sanity-check a query's sources before trusting its results. "
            "Pass column= to trace a single output column."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        cat = cast(Catalog, ctx.catalogs[args.database or ctx.primary])
        schema = _catalog_schema_dict(cat)

        try:
            tree = sqlglot.parse_one(args.sql)
        except SqlglotError as exc:
            return _Output(columns=[], parse_error=str(exc))
        if not isinstance(tree, exp.Query):
            return _Output(columns=[], parse_error="lineage requires a SELECT query")

        targets = [args.column] if args.column is not None else _output_columns(tree, schema)
        deduped: list[str] = []
        for t in targets:  # SELECT a, a FROM t — trace each name once
            if t not in deduped:
                deduped.append(t)

        results: list[_ColumnLineage] = []
        for col in deduped:
            try:
                node = lineage(col, args.sql, schema=schema)
            except SqlglotError as exc:
                return _Output(columns=results, parse_error=str(exc))
            results.append(
                _ColumnLineage(
                    output_column=col,
                    sources=_flatten(node),
                    derivation=node.expression.sql(),
                )
            )
        return _Output(columns=results)
