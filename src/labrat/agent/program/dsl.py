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
from typing import Any

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
