"""CatalogAdapter ABC and shared data models (M30)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict


class ColumnEntry(BaseModel):
    """Catalog metadata for a single column."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = ""
    data_type: str = ""
    is_pii: bool = False


class CatalogEntry(BaseModel):
    """Catalog metadata for a single table / model."""

    model_config = ConfigDict(frozen=False)

    name: str
    schema_name: str = "public"
    description: str = ""
    columns: dict[str, ColumnEntry] = {}
    tags: list[str] = []
    upstream: list[str] = []
    downstream: list[str] = []
    row_count: int | None = None
    compiled_sql: str = ""  # Jinja-rendered SQL from manifest.json compiled_code


class CatalogAdapter(ABC):
    """Interface all catalog backends must implement."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def load(self) -> dict[str, CatalogEntry]:
        """Return a mapping of table_name → CatalogEntry."""
        ...
