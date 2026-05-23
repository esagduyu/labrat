"""Tests for dialect-aware system prompt loading (M15)."""

from __future__ import annotations

import pytest

from labrat.agent.prompts import SUPPORTED_DIALECTS, build_system_prompt


def test_build_system_prompt_duckdb_returns_string() -> None:
    """build_system_prompt returns a non-empty string for duckdb."""
    prompt = build_system_prompt("duckdb")
    assert isinstance(prompt, str)
    assert len(prompt) > 100


def test_build_system_prompt_contains_base_content() -> None:
    """The returned prompt includes content from the base system prompt."""
    prompt = build_system_prompt("duckdb")
    # The base prompt should mention the agent's purpose
    assert "sql" in prompt.lower() or "data" in prompt.lower()


def test_build_system_prompt_contains_dialect_content() -> None:
    """The returned prompt includes dialect-specific content."""
    duckdb_prompt = build_system_prompt("duckdb")
    postgres_prompt = build_system_prompt("postgres")
    # Dialect prompts differ
    assert duckdb_prompt != postgres_prompt


@pytest.mark.parametrize(
    "dialect", ["duckdb", "postgres", "redshift", "bigquery", "snowflake", "trino", "mysql"]
)
def test_all_dialects_produce_non_empty_prompts(dialect: str) -> None:
    """Every supported dialect produces a non-empty system prompt."""
    prompt = build_system_prompt(dialect)
    assert isinstance(prompt, str)
    assert len(prompt) > 50


def test_supported_dialects_list_is_complete() -> None:
    """SUPPORTED_DIALECTS covers all expected warehouse dialects."""
    expected = {"duckdb", "postgres", "redshift", "bigquery", "snowflake", "trino", "mysql"}
    assert expected == set(SUPPORTED_DIALECTS)


def test_unknown_dialect_raises_value_error() -> None:
    """build_system_prompt raises ValueError for an unrecognised dialect."""
    with pytest.raises(ValueError, match="Unsupported dialect"):
        build_system_prompt("oracle")
