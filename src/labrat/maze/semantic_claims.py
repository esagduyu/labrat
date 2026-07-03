"""Structured semantic claims (join / column-role) emitted by the semantics author,
parsed from a line-based block and verified deterministically before any is persisted.
Line grammar (one per line, case-insensitive keyword):
  JOIN <lt>.<lc> = <rt>.<rc>
  ROLE <t>.<code_col> CODES <t>.<name_col>
Unparseable lines are ignored (tolerant)."""

from __future__ import annotations

import re

from pydantic import BaseModel

from labrat.db.base import Connection

_JOIN_RE = re.compile(r"^\s*JOIN\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*$", re.IGNORECASE)
_ROLE_RE = re.compile(r"^\s*ROLE\s+(\w+)\.(\w+)\s+CODES\s+(\w+)\.(\w+)\s*$", re.IGNORECASE)
_CODE_SHAPE_RE = re.compile(r"(\d.*[/\-._]|^\[.*\]$|^[A-Za-z0-9]{1,10}$)")
_SAMPLE = 200
_SHAPE_THRESHOLD = 0.6


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
    # code column must look code-shaped AND clearly more so than the name column
    return code_score >= _SHAPE_THRESHOLD and code_score > name_score
