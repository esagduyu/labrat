"""Profile model for LabRat database connections."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

Dialect = Literal["duckdb", "postgres", "mysql", "snowflake", "bigquery", "redshift", "trino"]


class Profile(BaseModel):
    """A named database connection profile.

    Secrets (passwords, tokens) are stored via keyring, not serialized here.
    The `has_secret` flag indicates whether a secret is stored in keyring.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    dialect: Dialect
    # DuckDB-specific
    path: str | None = None
    # Network connections
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    # Auth
    has_secret: bool = False
    # Role and schema
    is_read_only: bool = True
    default_schema: str | None = None
    # Display
    description: str = ""

    @field_validator("name")
    @classmethod
    def name_must_be_slug(cls, v: str) -> str:
        if not v or not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Profile name must be alphanumeric (hyphens and underscores allowed)")
        return v
