"""Program-mode DSL: pipeline models + handle-reference resolution.

Pure and deterministic — no DB access and no LLM call anywhere in this module.
Handle refs in step args: ``$handle`` resolves to that step's materialized
temp-table name (``program_<handle>``); ``$handle.field`` resolves to a scalar
field of that step's output dump. The token must start with a letter or
underscore, so SQL dollar-literals like ``$100`` are never touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator, model_validator

# Same identifier guard shape as llm_primitives.py / sample_rows.py.
_SAFE_IDENT = re.compile(r"\w+")

# $handle or $handle.field — verified: `$100` never matches (digit after $);
# `FROM $facts f JOIN $docs d` yields two bare tokens.
_REF_TOKEN = re.compile(r"\$([A-Za-z_]\w*)(?:\.(\w+))?")


class ProgramError(Exception):
    """A structured program-level failure (bad ref, bad bind). Never a crash —

    the interpreter converts it into a failed-step summary (stop-on-error)."""


class ProgramStep(BaseModel):
    """One pipeline step: dispatch ``tool`` with ``args``, bind the result to ``bind``."""

    tool: str = Field(description="Name of a registered tool to dispatch.")
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Tool arguments; may reference earlier steps via $handle / $handle.field",
    )
    bind: str = Field(description="Handle name for this step's result (alphanumeric/underscore).")

    @field_validator("bind")
    @classmethod
    def _bind_is_safe_ident(cls, v: str) -> str:
        if not _SAFE_IDENT.fullmatch(v):
            raise ValueError(f"bind must be alphanumeric/underscore: {v!r}")
        return v


class Program(BaseModel):
    """An ordered pipeline of tool steps with unique handle binds."""

    steps: list[ProgramStep] = Field(min_length=1, description="Steps, run sequentially.")

    @model_validator(mode="after")
    def _binds_unique(self) -> Program:
        seen: set[str] = set()
        for step in self.steps:
            if step.bind in seen:
                raise ValueError(f"duplicate bind: {step.bind!r}")
            seen.add(step.bind)
        return self


@dataclass
class ResolvedHandle:
    """What a completed step exposes to later steps' $refs."""

    table: str | None  # program_<bind> temp-table name, when the step produced a table
    output: dict[str, Any]  # the step output's model_dump(), for $handle.field lookups


def _resolve_token(
    handle: str, field_name: str | None, handles: dict[str, ResolvedHandle]
) -> object:
    if handle not in handles:
        raise ProgramError(f"unknown handle: ${handle} (bound so far: {sorted(handles)})")
    resolved = handles[handle]
    if field_name is None:
        if resolved.table is None:
            raise ProgramError(
                f"${handle} refers to a step that produced no table; "
                f"use ${handle}.<field> to pass a scalar output forward"
            )
        return resolved.table
    if field_name not in resolved.output:
        raise ProgramError(
            f"${handle}.{field_name}: no field {field_name!r} in that step's output "
            f"(available: {sorted(resolved.output)})"
        )
    return resolved.output[field_name]


def _resolve_str(value: str, handles: dict[str, ResolvedHandle]) -> object:
    whole = _REF_TOKEN.fullmatch(value)
    if whole is not None:
        # A whole-string ref may resolve to a non-str value ($handle.field).
        return _resolve_token(whole.group(1), whole.group(2), handles)

    def repl(m: re.Match[str]) -> str:
        return str(_resolve_token(m.group(1), m.group(2), handles))

    return _REF_TOKEN.sub(repl, value)


def _resolve_value(value: object, handles: dict[str, ResolvedHandle]) -> object:
    if isinstance(value, str):
        return _resolve_str(value, handles)
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        return {k: _resolve_value(v, handles) for k, v in d.items()}
    if isinstance(value, list):
        items = cast(list[Any], value)
        return [_resolve_value(v, handles) for v in items]
    return value


def resolve_refs(args: dict[str, Any], handles: dict[str, ResolvedHandle]) -> dict[str, Any]:
    """Recursively substitute $handle / $handle.field refs in a step's args tree.

    Raises :class:`ProgramError` on an unknown handle, a missing field, or a
    bare ``$handle`` whose step produced no table.
    """
    return {k: _resolve_value(v, handles) for k, v in args.items()}
