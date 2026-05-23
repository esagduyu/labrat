"""SQL validation via SQLGlot.

validate(sql, dialect) → list[ValidationError]

Returns an empty list for valid SQL (including empty/whitespace input).
Returns one or more ValidationError items for parse errors.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot.errors import ErrorLevel, ParseError

# Map LabRat profile dialect names to SQLGlot dialect names.
_DIALECT_MAP: dict[str, str] = {
    "duckdb": "duckdb",
    "postgres": "postgres",
    "mysql": "mysql",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "redshift": "redshift",
    "trino": "trino",
}


@dataclass
class ValidationError:
    """A parse or dialect error found in a SQL statement."""

    message: str
    line: int
    col: int
    dialect: str


def validate(sql: str, *, dialect: str = "duckdb") -> list[ValidationError]:
    """Parse *sql* with SQLGlot and return any errors.

    Args:
        sql: The SQL text to validate (may contain multiple statements).
        dialect: The LabRat dialect name (e.g. "duckdb", "postgres").

    Returns:
        A list of ValidationError items, empty on success.
    """
    if not sql or not sql.strip():
        return []

    sg_dialect = _DIALECT_MAP.get(dialect, dialect)
    errors: list[ValidationError] = []

    try:
        sqlglot.parse(sql, dialect=sg_dialect, error_level=ErrorLevel.RAISE)
    except ParseError as exc:
        for err in exc.errors:
            errors.append(
                ValidationError(
                    message=err.get("description", "Parse error"),
                    line=int(err.get("line", 1)),
                    col=int(err.get("col", 1)),
                    dialect=dialect,
                )
            )

    return errors
