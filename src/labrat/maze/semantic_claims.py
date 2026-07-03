"""Structured semantic claims (join / column-role) emitted by the semantics author,
parsed from a line-based block and verified deterministically before any is persisted.
Line grammar (one per line, case-insensitive keyword):
  JOIN <lt>.<lc> = <rt>.<rc>
  ROLE <t>.<code_col> CODES <t>.<name_col>
Unparseable lines are ignored (tolerant)."""

from __future__ import annotations

import re

from pydantic import BaseModel

_JOIN_RE = re.compile(r"^\s*JOIN\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*$", re.IGNORECASE)
_ROLE_RE = re.compile(r"^\s*ROLE\s+(\w+)\.(\w+)\s+CODES\s+(\w+)\.(\w+)\s*$", re.IGNORECASE)


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
