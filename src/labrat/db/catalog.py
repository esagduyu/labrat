"""Catalog models for database schema introspection."""

from pydantic import BaseModel, ConfigDict


class ForeignKey(BaseModel):
    """A foreign key relationship from one column to another."""

    model_config = ConfigDict(frozen=True)

    column: str
    referenced_table: str
    referenced_column: str


class Column(BaseModel):
    """A column within a table."""

    model_config = ConfigDict(frozen=True)

    name: str
    data_type: str
    nullable: bool
    default: str | None = None
    comment: str | None = None


class Table(BaseModel):
    """A table within a schema."""

    model_config = ConfigDict(frozen=True)

    name: str
    schema_name: str
    columns: list[Column]
    foreign_keys: list[ForeignKey] = []
    row_count: int | None = None
    comment: str | None = None
    view_definition: str | None = None  # full CREATE VIEW ... AS SELECT ...; None = base table

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.name}"


class Schema(BaseModel):
    """A schema containing tables."""

    model_config = ConfigDict(frozen=True)

    name: str
    tables: list[Table]


class Catalog(BaseModel):
    """The full introspected schema catalog for a connection."""

    model_config = ConfigDict(frozen=True)

    database_name: str
    schemas: list[Schema]

    def find_table(self, table_name: str, schema_name: str | None = None) -> Table | None:
        """Find a table by name, optionally scoped to a schema."""
        for schema in self.schemas:
            if schema_name and schema.name != schema_name:
                continue
            for table in schema.tables:
                if table.name == table_name:
                    return table
        return None


class ColumnStats(BaseModel):
    """Basic statistics for a single column."""

    model_config = ConfigDict(frozen=True)

    column_name: str
    table_name: str
    data_type: str
    null_count: int
    distinct_count: int
    min_value: str | None = None
    max_value: str | None = None
