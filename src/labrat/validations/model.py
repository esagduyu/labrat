"""ValidationRule model and built-in example rules (M32)."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ValidationSeverity(StrEnum):
    warn = "warn"
    block = "block"


class ValidationStatus(StrEnum):
    pass_ = "pass"
    warn = "warn"
    block = "block"


class ValidationRule(BaseModel):
    """A user-defined natural-language validation rule."""

    model_config = ConfigDict(frozen=False)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    profile: str
    natural_language_rule: str
    severity: ValidationSeverity = ValidationSeverity.warn
    enabled: bool = True
    table_scope: str | None = None


def _builtin(text: str, severity: ValidationSeverity = ValidationSeverity.warn) -> ValidationRule:
    return ValidationRule(
        profile="__builtin__",
        natural_language_rule=text,
        severity=severity,
        enabled=False,
    )


BUILTIN_EXAMPLE_RULES: list[ValidationRule] = [
    _builtin("WAU should never exceed 10 billion"),
    _builtin("Revenue queries must include a status filter"),
    _builtin("Always filter by is_test = false on events queries"),
]
