"""check_sql: deterministic pre-execution validation of table/column references.

Parses SQL with sqlglot and resolves every referenced table + column against the
catalog, returning unresolved refs with the closest real names (difflib). No
execution, no LLM. Catches wrong-table/typo'd-column before a warehouse round-trip.
"""

from __future__ import annotations

import difflib
from typing import cast

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp
from sqlglot.errors import ParseError

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.catalog import Catalog

_SUGGEST_N = 3
_SUGGEST_CUTOFF = 0.6


class _UnknownRef(BaseModel):
    ref: str
    suggestions: list[str]


class _UnknownCol(BaseModel):
    table: str | None
    ref: str
    suggestions: list[str]


class _Input(BaseModel):
    sql: str = Field(description="The SQL to validate (not executed).")
    database: str | None = Field(default=None, description="Connection name; defaults to primary.")


class _Output(BaseModel):
    valid: bool
    unknown_tables: list[_UnknownRef]
    unknown_columns: list[_UnknownCol]
    parse_error: str | None


def _catalog_index(cat: Catalog) -> dict[str, set[str]]:
    """{table_name_lower: {column_name_lower, ...}} across all schemas."""
    idx: dict[str, set[str]] = {}
    for schema in cat.schemas:
        for t in schema.tables:
            idx.setdefault(t.name.lower(), set()).update(c.name.lower() for c in t.columns)
    return idx


def _suggest(ref: str, candidates: list[str]) -> list[str]:
    return difflib.get_close_matches(ref.lower(), candidates, n=_SUGGEST_N, cutoff=_SUGGEST_CUTOFF)


class CheckSqlTool(Tool[_Input]):
    @property
    def name(self) -> str:
        return "check_sql"

    @property
    def description(self) -> str:
        return (
            "Validate a SQL query's table and column references against the schema BEFORE "
            "running it. Returns any unknown tables/columns with the closest real names. "
            "Use this to catch typos and wrong table/column names without a failed query."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        cat = cast(Catalog, ctx.catalogs[args.database or ctx.primary])
        idx = _catalog_index(cat)
        all_tables = list(idx.keys())

        try:
            tree = sqlglot.parse_one(args.sql)
        except ParseError as e:
            return _Output(valid=False, unknown_tables=[], unknown_columns=[], parse_error=str(e))

        # CTE names are not base tables — never flag them, and don't resolve their
        # projected columns (fail-open: we don't track what a CTE actually selects).
        cte_names = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}

        # Output/derived names — SELECT-list aliases (e.g. `COUNT(*) AS n`, then
        # referenced unqualified in ORDER BY/QUALIFY as `n`) and derived-table/subquery
        # aliases (e.g. `s` in `FROM (SELECT id AS x FROM orders) s`, or the projected
        # `x` referenced from the outer query). These are projected/derived names, not
        # base columns — fail-open, same treatment as CTE names, since we don't track
        # what a subquery/window/aggregate actually projects.
        derived_names = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
        derived_names |= {sq.alias.lower() for sq in tree.find_all(exp.Subquery) if sq.alias}

        # alias -> real table name, for tables in scope
        alias_map: dict[str, str] = {}
        in_scope: list[str] = []
        unknown_tables: list[_UnknownRef] = []
        for tbl in tree.find_all(exp.Table):
            tname = tbl.name
            if not tname:
                continue
            if tname.lower() in cte_names:
                continue
            alias = tbl.alias or tname
            alias_map[alias.lower()] = tname.lower()
            if tname.lower() in idx:
                in_scope.append(tname.lower())
            else:
                unknown_tables.append(
                    _UnknownRef(ref=tname, suggestions=_suggest(tname, all_tables))
                )

        unknown_cols: list[_UnknownCol] = []
        for col in tree.find_all(exp.Column):
            cname = col.name
            if not cname:
                continue
            qualifier = col.table  # alias or table, '' if unqualified
            if qualifier:
                if qualifier.lower() in cte_names or qualifier.lower() in derived_names:
                    continue  # can't resolve a CTE/derived-table's projected columns — fail-open
                real = alias_map.get(qualifier.lower())
                if real is None or real not in idx:
                    continue  # unknown/foreign qualifier — table already flagged or out of scope
                if cname.lower() not in idx[real]:
                    unknown_cols.append(
                        _UnknownCol(
                            table=real, ref=cname, suggestions=_suggest(cname, sorted(idx[real]))
                        )
                    )
            else:
                if cname.lower() in derived_names:
                    continue  # SELECT-list alias / derived-table projected name — fail-open
                owners = [t for t in in_scope if cname.lower() in idx.get(t, set())]
                if len(owners) == 0 and in_scope and not cte_names:
                    pool = sorted({c for t in in_scope for c in idx.get(t, set())})
                    unknown_cols.append(
                        _UnknownCol(table=None, ref=cname, suggestions=_suggest(cname, pool))
                    )
                # len>=1 (resolved), ambiguous(len>1), or query references a CTE
                # (unqualified col may come from CTE projection) -> don't flag

        valid = not unknown_tables and not unknown_cols
        return _Output(
            valid=valid,
            unknown_tables=unknown_tables,
            unknown_columns=unknown_cols,
            parse_error=None,
        )
