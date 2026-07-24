"""describe_table tool: return schema detail for a single table."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol, cast

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext, stringify_rows
from labrat.db.base import Connection
from labrat.db.catalog import Catalog, Table
from labrat.maze._lexical import name_tokens

# Column-disambiguation grounding (DAB autopsy "lever D" — agents repeatedly pick the
# wrong column when a table has a code column vs a friendlier name column, or a
# hierarchy/level column). Name-based heuristics mirror link_schema.py (kept as a
# separate copy — the two tools are independently owned/deployed). describe_table
# additionally scopes to a single table, so it may afford ONE small bounded sample
# (reusing Connection.sample_table, same call already used by profile_dataset) to
# catch hierarchy/code shapes that no naming convention reveals (e.g. CPC codes).
#
# P1 fix (2026-07): a Fable whole-branch review found the name-based heuristics firing on
# ubiquitous NORMAL tables. As in link_schema.py: only "code"/"cd" are domain-code markers
# now (bare "id"/"no" are surrogate-key markers, not code markers — every table has one),
# and a marker alone isn't enough — the column needs a real qualifier beyond the marker
# (e.g. "icd_o_code", "cpc_code"). Hierarchy/level detection by NAME ALONE turned out to be
# unsafeable the same way (any table can have a harmless "parent_id"/"level" column), so
# it's dropped entirely — hierarchy now fires ONLY from the value-based nested-prefix check
# below (``_values_look_hierarchical``), which is a genuine signal name-alone can't fake.
CODE_NAME_TOKENS = {"code", "cd"}
NAME_LABEL_TOKENS = {"name", "title", "label", "desc", "description"}

_CODE_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/.]{0,9}$")

_SAMPLE_ROWS_FOR_HINTS = 8


class _ColumnLike(Protocol):
    name: str


def _is_code_like(col_name: str) -> bool:
    """A code marker ("code"/"cd") AND a real qualifier beyond the marker itself.

    Mirrors link_schema._is_code_like — see that module's comment for the full
    rationale (bare "id"/"no" columns are surrogate keys, not domain codes).
    """
    toks = set(name_tokens(col_name))
    if not (toks & CODE_NAME_TOKENS):
        return False
    return bool(toks - CODE_NAME_TOKENS)


def _is_name_like(col_name: str) -> bool:
    return bool(set(name_tokens(col_name)) & NAME_LABEL_TOKENS)


def _code_name_pairs(columns: Sequence[_ColumnLike]) -> list[tuple[str, str]]:
    """Pair code-like columns with name-like siblings by name alone (see link_schema)."""
    code_cols = [c.name for c in columns if _is_code_like(c.name)]
    name_cols = [c.name for c in columns if _is_name_like(c.name)]
    if not code_cols or not name_cols:
        return []

    pairs: list[tuple[str, str]] = []
    used: set[str] = set()
    for code_col in code_cols:
        code_stems = set(name_tokens(code_col)) - CODE_NAME_TOKENS
        match: str | None = None
        for name_col in name_cols:
            if name_col == code_col or name_col in used:
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
            used.add(match)
            pairs.append((code_col, match))
    return pairs


def _format_pair_hint(code_col: str, name_col: str, *, via_values: bool = False) -> str:
    code_desc = "code-like values" if via_values else "code-like"
    name_desc = "prose-like values" if via_values else "name-like"
    return (
        f"columns {code_col} ({code_desc}) and {name_col} ({name_desc}) look like a "
        "code/label pair — if the question asks for a code/id/classification, "
        f"group/select the CODE column ({code_col}), not the friendlier name."
    )


def _format_level_hint(col_name: str) -> str:
    return (
        f"column {col_name} holds hierarchical values at multiple levels — confirm "
        "which level the question asks for (e.g. subclass vs subgroup) before grouping."
    )


def _values_look_hierarchical(values: Sequence[str]) -> bool:
    """True when one sampled value is a proper prefix of another (nested-code shape).

    e.g. CPC codes "A01" -> "A01B" -> "A01B1", or ICD codes at varying granularity.
    Purely-numeric values (plain ids/counts) are excluded — string-prefix relations
    among integers ("1" prefixes "10") are not a hierarchy signal.
    """
    vals = sorted({v.strip() for v in values if v and v.strip()}, key=len)
    if any(v.isdigit() for v in vals):
        return False
    for i, a in enumerate(vals):
        for b in vals[i + 1 :]:
            if b != a and b.startswith(a):
                return True
    return False


def _values_code_like(values: Sequence[str]) -> bool:
    """True when sampled values look like short domain codes (e.g. "9382/3", "A1").

    Purely-numeric values (plain sequential ids/counts, e.g. "1"/"2"/"3") are excluded
    — same rationale as ``_values_look_hierarchical``: a bare integer PK column
    incidentally satisfies the code-shape regex on every table that has one, which
    would otherwise pair any integer id with any prose-like sibling column table-wide
    (P1 fix — found via reviews(review_id, business_name), a table with an ordinary
    integer PK next to a free-text name, wrongly flagged pre-fix).
    """
    vals = [v for v in values if v]
    if len(vals) < 2:
        return False
    if all(v.isdigit() for v in vals):
        return False
    return all(_CODE_VALUE_RE.match(v) and any(ch.isdigit() for ch in v) for v in vals)


def _values_prose_like(values: Sequence[str]) -> bool:
    vals = [v for v in values if v]
    if len(vals) < 2:
        return False
    return any(" " in v for v in vals)


def _value_code_name_pairs(
    col_values: dict[str, list[str]], exclude: set[str]
) -> list[tuple[str, str]]:
    """Value-based fallback for code/name pairing when naming gives no signal.

    One column's sampled values are short alphanumeric codes (contain a digit, no
    spaces) while a sibling's are prose (contains a space) — e.g. a "language_code"
    vs "display_text" pair that neither name mentions "code" nor "name" for.
    """
    code_candidates = [
        c for c, vs in col_values.items() if c not in exclude and _values_code_like(vs)
    ]
    prose_candidates = [
        c for c, vs in col_values.items() if c not in exclude and _values_prose_like(vs)
    ]
    pairs: list[tuple[str, str]] = []
    used: set[str] = set()
    for code_col in code_candidates:
        for name_col in prose_candidates:
            if name_col == code_col or name_col in used:
                continue
            used.add(name_col)
            pairs.append((code_col, name_col))
            break
    return pairs


class _Input(BaseModel):
    table: str
    database: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )


class _ColumnDetail(BaseModel):
    name: str
    data_type: str
    nullable: bool
    default: str | None = None


class _FKDetail(BaseModel):
    column: str
    references: str  # "table.column"


class _Output(BaseModel):
    table_name: str
    schema_name: str
    columns: list[_ColumnDetail]
    foreign_keys: list[_FKDetail]
    row_count: int | None = None
    column_hints: list[str] = Field(default_factory=list)


class DescribeTableTool(Tool[_Input]):
    """Describe a table's columns, types, nullability, and foreign keys."""

    @property
    def name(self) -> str:
        return "describe_table"

    @property
    def description(self) -> str:
        return (
            "Return the full schema of a table: column names, data types, "
            "nullability, defaults, and foreign key relationships. "
            "Use this before writing a query to understand the table structure."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        database = args.database
        table_name = args.table
        # Agents often pass the DuckDB-qualified "alias.table" form. If a dotted
        # prefix names a known catalog, route there and match the bare table name.
        if "." in table_name:
            prefix, _, rest = table_name.partition(".")
            if prefix in ctx.catalogs:
                database = database or prefix
                table_name = rest
        catalog = cast(Catalog, ctx.catalogs[database or ctx.primary])
        table = catalog.find_table(table_name)
        if table is None:
            raise ValueError(f"Table {args.table!r} not found in catalog")

        columns = [
            _ColumnDetail(
                name=col.name,
                data_type=col.data_type,
                nullable=col.nullable,
                default=col.default,
            )
            for col in table.columns
        ]
        fks = [
            _FKDetail(
                column=fk.column,
                references=f"{fk.referenced_table}.{fk.referenced_column}",
            )
            for fk in table.foreign_keys
        ]
        return _Output(
            table_name=table.name,
            schema_name=table.schema_name,
            columns=columns,
            foreign_keys=fks,
            row_count=table.row_count,
            column_hints=self._column_hints(ctx, database, table),
        )

    def _column_hints(self, ctx: ToolContext, database: str | None, table: Table) -> list[str]:
        """Deterministic code/name-pair + hierarchy hints (see module docstring).

        Code/name-pair detection has a name-based pass (zero cost) PLUS a value-based
        fallback. Hierarchy detection is value-based ONLY (see module comment — a
        name-only hierarchy signal can't be told apart from a normal table). Value-based
        detection needs one small bounded sample of the table's live rows (reusing
        ``Connection.sample_table``, same call profile_dataset already makes), so it's
        skipped silently when no connection is registered for this db or sampling fails
        for any reason — code/name pairing still degrades to name-only in that case, but
        hierarchy hints simply don't fire without a sample.
        """
        columns = table.columns

        name_pairs = _code_name_pairs(columns)
        covered_pair_cols = {c for pair in name_pairs for c in pair}

        hints = [_format_pair_hint(code_col, name_col) for code_col, name_col in name_pairs]

        col_values = self._sample_values(ctx, database, table)
        if col_values:
            value_pairs = _value_code_name_pairs(col_values, exclude=covered_pair_cols)
            hints += [
                _format_pair_hint(code_col, name_col, via_values=True)
                for code_col, name_col in value_pairs
            ]
            for col_name, values in col_values.items():
                if _values_look_hierarchical(values):
                    hints.append(_format_level_hint(col_name))

        return hints

    def _sample_values(
        self, ctx: ToolContext, database: str | None, table: Table
    ) -> dict[str, list[str]]:
        conn = ctx.connections.get(database or ctx.primary)
        if conn is None:
            return {}
        try:
            df = cast(Connection, conn).sample_table(
                table.qualified_name,
                n=_SAMPLE_ROWS_FOR_HINTS,
            )
            rows = stringify_rows(df)
            columns = df.columns
        except Exception:
            return {}

        col_values: dict[str, list[str]] = {name: [] for name in columns}
        for row in rows:
            for idx, value in enumerate(row):
                col_values[columns[idx]].append(value)
        return col_values
