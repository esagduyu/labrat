"""Dialect-aware system prompt loader (M15)."""

from __future__ import annotations

from importlib.resources import files

SUPPORTED_DIALECTS: list[str] = [
    "duckdb",
    "postgres",
    "redshift",
    "bigquery",
    "snowflake",
    "trino",
    "mysql",
]

_PKG = files(__name__)


def _read(filename: str) -> str:
    return (_PKG / filename).read_text(encoding="utf-8")  # type: ignore[return-value]


def build_system_prompt(dialect: str) -> str:
    """Return the full system prompt for the given SQL dialect.

    Combines the common base instructions with dialect-specific cheatsheet.
    Raises ValueError for unrecognised dialects.
    """
    if dialect not in SUPPORTED_DIALECTS:
        raise ValueError(f"Unsupported dialect: {dialect!r}. Supported: {SUPPORTED_DIALECTS}")
    base = _read("system_base.md")
    specific = _read(f"dialect_{dialect}.md")
    return f"{base}\n\n{specific}"
