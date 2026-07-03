"""Structured semantic claims (join / column-role) emitted by the semantics author,
parsed from a line-based block and verified deterministically before any is persisted.
Line grammar (one per line, case-insensitive keyword):
  JOIN <lt>.<lc> = <rt>.<rc>
  ROLE <t>.<code_col> CODES <t>.<name_col>
Unparseable lines are ignored (tolerant)."""

from __future__ import annotations

import re
from typing import cast

from pydantic import BaseModel

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.verify_join import VerifyJoinTool
from labrat.db.base import Connection
from labrat.maze.document import Section

_JOIN_RE = re.compile(r"^\s*JOIN\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*$", re.IGNORECASE)
_ROLE_RE = re.compile(r"^\s*ROLE\s+(\w+)\.(\w+)\s+CODES\s+(\w+)\.(\w+)\s*$", re.IGNORECASE)
_CODE_SHAPE_RE = re.compile(r"^\[.*\]$|^(?=.*\d)[A-Za-z0-9/\-._]{1,12}$")
_SAFE_IDENT = re.compile(r"\w+")
_SAMPLE = 200
_SHAPE_THRESHOLD = 0.6
_NAME_CEILING = 0.4


def is_claim_line(line: str) -> bool:
    """True if a line matches the JOIN or ROLE claim grammar."""
    return bool(_JOIN_RE.match(line) or _ROLE_RE.match(line))


class JoinClaim(BaseModel):
    left_table: str
    left_col: str
    right_table: str
    right_col: str


class RoleClaim(BaseModel):
    table: str
    code_col: str
    name_col: str


def parse_semantic_claims(text: str) -> list[JoinClaim | RoleClaim]:
    claims: list[JoinClaim | RoleClaim] = []
    for line in text.splitlines():
        mj = _JOIN_RE.match(line)
        if mj:
            claims.append(
                JoinClaim(
                    left_table=mj.group(1),
                    left_col=mj.group(2),
                    right_table=mj.group(3),
                    right_col=mj.group(4),
                )
            )
            continue
        mr = _ROLE_RE.match(line)
        if mr:
            # ROLE t.code CODES t2.name — same table expected; take table from code side
            claims.append(RoleClaim(table=mr.group(1), code_col=mr.group(2), name_col=mr.group(4)))
    return claims


def _looks_like_code(values: list[str]) -> float:
    if not values:
        return 0.0
    hits = sum(1 for v in values if _CODE_SHAPE_RE.search(v.strip()))
    return hits / len(values)


def verify_role_claim(conn: Connection, claim: RoleClaim) -> bool:
    """True iff code_col holds code-shaped values and name_col does NOT (name-shaped).
    Conservative: any probe error or ambiguity → False (drop)."""
    if not all(_SAFE_IDENT.fullmatch(x) for x in (claim.table, claim.code_col, claim.name_col)):
        return False
    try:

        def _vals(col: str) -> list[str]:
            df = conn.execute(
                f"SELECT DISTINCT {col} FROM {claim.table} WHERE {col} IS NOT NULL LIMIT {_SAMPLE}"
            )
            return [str(r[0]) for r in df.iter_rows()]

        code_vals = _vals(claim.code_col)
        name_vals = _vals(claim.name_col)
    except Exception:
        return False
    if not code_vals or not name_vals:
        return False
    code_score = _looks_like_code(code_vals)
    name_score = _looks_like_code(name_vals)
    # code column must look code-shaped, clearly more so than the name column, AND the
    # name column must not itself be substantially code-shaped (ambiguous direction → drop)
    return (
        code_score >= _SHAPE_THRESHOLD and name_score <= _NAME_CEILING and code_score > name_score
    )


async def verify_semantic_claims(
    claims: list[JoinClaim | RoleClaim], ctx: ToolContext, *, database: str
) -> Section | None:
    """Probe each structured claim; persist ONLY survivors as a Verified Semantics section."""
    tool = VerifyJoinTool()
    conn = cast(Connection, ctx.connections[database])
    lines: list[str] = []
    for c in claims:
        if isinstance(c, JoinClaim):
            if not all(
                _SAFE_IDENT.fullmatch(x)
                for x in (c.left_table, c.left_col, c.right_table, c.right_col)
            ):
                continue
            try:
                v = await tool.execute(
                    ctx,
                    tool.input_model(
                        left_table=c.left_table,
                        left_column=c.left_col,
                        right_table=c.right_table,
                        right_column=c.right_col,
                        database=database,
                    ),
                )
            except Exception:
                continue
            if v.likely_valid:
                fan = (
                    "no fan-out"
                    if v.max_right_rows_per_key <= 1
                    else f"fans out up to {v.max_right_rows_per_key}/key"
                )
                lines.append(
                    f"- Join `{c.left_table}.{c.left_col} = {c.right_table}.{c.right_col}` "
                    f"(verified {round(v.match_rate * 100, 1)}% match, {fan})."
                )
        else:  # RoleClaim
            if verify_role_claim(conn, c):
                lines.append(
                    f"- For `{c.table}`, `{c.code_col}` holds coded values; "
                    f"`{c.name_col}` holds display names — group/filter by the code column "
                    f"when the question asks for codes."
                )
    if not lines:
        return None
    return Section(heading="Verified Semantics", body="\n".join(lines), source="verified")
