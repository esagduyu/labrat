"""verify_join tool: mechanically probe a join before trusting it.

Grounding tool (FEATURE_ROADMAP #25). Instead of assuming two columns join
cleanly, run cheap COUNT probes to measure (a) what fraction of left-side keys
actually find a partner on the right, and (b) whether the right side is unique on
the key (else the join fans out and aggregates double-count). Surfaces the two
failure modes that silently produce wrong answers on multi-table questions.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.base import Connection


class _Input(BaseModel):
    left_table: str = Field(description="Left table (qualified if attached, e.g. 'db.table').")
    left_column: str = Field(description="Join key column on the left table.")
    right_table: str = Field(description="Right table (qualified if attached).")
    right_column: str = Field(description="Join key column on the right table.")
    database: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )


class _Output(BaseModel):
    left: str
    right: str
    left_rows: int
    left_non_null_keys: int
    matched_rows: int
    match_rate: float
    right_distinct_keys: int
    max_right_rows_per_key: int
    likely_valid: bool
    verdict: str


class VerifyJoinTool(Tool[_Input]):
    """Probe a candidate join (match rate + fan-out) before relying on it."""

    @property
    def name(self) -> str:
        return "verify_join"

    @property
    def description(self) -> str:
        return (
            "Mechanically verify a join BEFORE you trust it: reports what fraction of "
            "left-side keys actually match a right-side row, and whether the right side "
            "is unique on the key (non-unique → the join fans out and aggregates "
            "double-count). Use this whenever a question spans two tables and a wrong "
            "or fan-out join would change the answer."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        conn = cast(Connection, ctx.connections[args.database or ctx.primary])
        lt, lc, rt, rc = args.left_table, args.left_column, args.right_table, args.right_column

        def _scalar(sql: str) -> int:
            df = conn.execute(sql)
            value = df.row(0)[0]
            return int(value) if value is not None else 0

        left_rows = _scalar(f"SELECT COUNT(*) FROM {lt}")
        left_non_null = _scalar(f"SELECT COUNT({lc}) FROM {lt}")
        matched = _scalar(
            f"SELECT COUNT(*) FROM {lt} "
            f"WHERE {lc} IN (SELECT {rc} FROM {rt} WHERE {rc} IS NOT NULL)"
        )
        right_distinct = _scalar(f"SELECT COUNT(DISTINCT {rc}) FROM {rt}")
        max_per_key = _scalar(
            f"SELECT COALESCE(MAX(c), 0) FROM "
            f"(SELECT {rc} AS k, COUNT(*) AS c FROM {rt} WHERE {rc} IS NOT NULL GROUP BY {rc})"
        )

        match_rate = matched / left_non_null if left_non_null else 0.0
        likely_valid = left_non_null > 0 and match_rate >= 0.95

        parts: list[str] = []
        if left_non_null == 0:
            parts.append(f"Left key {lt}.{lc} is entirely NULL — nothing to join.")
        else:
            pct = round(match_rate * 100, 1)
            if match_rate >= 0.99:
                parts.append(f"{pct}% of left rows match — strong key.")
            elif match_rate >= 0.5:
                parts.append(f"{pct}% of left rows match — partial; check value types/filters.")
            else:
                parts.append(f"Only {pct}% of left rows match — likely the wrong join keys.")
        if max_per_key > 1:
            parts.append(
                f"Right side is NOT unique on the key (up to {max_per_key} rows/key): "
                f"this join fans out — aggregate with care (use DISTINCT or pre-aggregate)."
            )
        else:
            parts.append("Right side unique on the key (one-to-one / many-to-one) — no fan-out.")

        return _Output(
            left=f"{lt}.{lc}",
            right=f"{rt}.{rc}",
            left_rows=left_rows,
            left_non_null_keys=left_non_null,
            matched_rows=matched,
            match_rate=round(match_rate, 4),
            right_distinct_keys=right_distinct,
            max_right_rows_per_key=max_per_key,
            likely_valid=likely_valid,
            verdict=" ".join(parts),
        )
