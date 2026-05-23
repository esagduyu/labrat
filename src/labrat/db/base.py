"""Abstract base class for database connections."""

from abc import ABC, abstractmethod

import polars as pl

from labrat.db.catalog import Catalog, ColumnStats


class Connection(ABC):
    """Abstract database connection.

    All warehouse adapters implement this interface. Methods return Polars
    DataFrames (via Arrow zero-copy where the driver supports it).
    """

    @abstractmethod
    def connect(self) -> None:
        """Open the connection."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the connection."""

    @abstractmethod
    def introspect_catalog(self) -> Catalog:
        """Introspect the full schema and return a Catalog."""

    @abstractmethod
    def execute(self, sql: str) -> pl.DataFrame:
        """Execute a SQL query and return a Polars DataFrame."""

    @abstractmethod
    def explain(self, sql: str) -> str:
        """Return the query plan for a SQL statement."""

    @abstractmethod
    def sample_table(self, table: str, n: int = 10) -> pl.DataFrame:
        """Return up to n rows from a table."""

    @abstractmethod
    def column_stats(self, table: str, column: str) -> ColumnStats:
        """Return basic statistics for a column."""

    def __enter__(self) -> "Connection":
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()
