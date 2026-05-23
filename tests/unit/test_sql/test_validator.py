"""Table-driven tests for SQL validation across dialects."""

import pytest

from labrat.sql.validator import ValidationError, validate

# ── helpers ───────────────────────────────────────────────────────────────────

ALL_DIALECTS = ["duckdb", "postgres", "mysql", "snowflake", "bigquery", "redshift", "trino"]


# ── valid SQL produces no errors ───────────────────────────────────────────────


@pytest.mark.parametrize("dialect", ALL_DIALECTS)
def test_valid_select_no_errors(dialect: str) -> None:
    """A syntactically valid SELECT produces zero validation errors."""
    errors = validate("SELECT id, name FROM users WHERE active = 1", dialect=dialect)
    assert errors == []


def test_empty_sql_no_errors() -> None:
    """Empty SQL is treated as no-op and returns no errors."""
    assert validate("", dialect="duckdb") == []


def test_whitespace_only_no_errors() -> None:
    """Whitespace-only SQL returns no errors."""
    assert validate("   \n  ", dialect="duckdb") == []


# ── syntax errors ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("dialect", ALL_DIALECTS)
def test_invalid_syntax_produces_error(dialect: str) -> None:
    """SQL with clear syntax errors returns at least one ValidationError."""
    errors = validate("SELECT FROM", dialect=dialect)
    assert len(errors) >= 1


def test_error_has_line_and_col() -> None:
    """Validation errors carry line and column information."""
    errors = validate("SELECT *** FROM t", dialect="duckdb")
    assert any(isinstance(e, ValidationError) for e in errors)
    for err in errors:
        assert err.line >= 1
        assert err.col >= 1


def test_error_has_message() -> None:
    """Validation errors carry a non-empty message."""
    errors = validate("SELECT FROM", dialect="duckdb")
    assert all(err.message for err in errors)


# ── multi-statement ───────────────────────────────────────────────────────────


def test_multi_statement_valid() -> None:
    """Multiple valid statements produce no errors."""
    sql = "SELECT 1; SELECT 2;"
    assert validate(sql, dialect="duckdb") == []


def test_multi_statement_with_error() -> None:
    """One bad statement in a multi-statement block produces an error."""
    sql = "SELECT 1; SELECT FROM;"
    errors = validate(sql, dialect="duckdb")
    assert errors  # at least one error from the bad statement


# ── dialect-specific ──────────────────────────────────────────────────────────


def test_duckdb_list_function_valid_on_duckdb() -> None:
    """DuckDB-specific list functions are accepted on DuckDB dialect."""
    errors = validate("SELECT list_aggregate([1,2,3], 'sum')", dialect="duckdb")
    assert errors == []


def test_postgres_ilike_valid_on_postgres() -> None:
    """ILIKE is accepted on Postgres dialect."""
    errors = validate("SELECT * FROM t WHERE name ILIKE '%foo%'", dialect="postgres")
    assert errors == []


# ── ValidationError dataclass ─────────────────────────────────────────────────


def test_validation_error_fields() -> None:
    """ValidationError has message, line, col, and dialect fields."""
    err = ValidationError(message="test", line=1, col=5, dialect="duckdb")
    assert err.message == "test"
    assert err.line == 1
    assert err.col == 5
    assert err.dialect == "duckdb"
