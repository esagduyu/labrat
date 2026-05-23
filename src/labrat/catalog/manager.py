"""CatalogManager: coordinate multiple CatalogAdapters and merge into ContextBundle (M30).

When multiple adapters describe the same table the last adapter wins, allowing
a dbt adapter to provide a base description that a more-authoritative MCP
catalog can override.
"""

from __future__ import annotations

from labrat.catalog.base import CatalogAdapter, CatalogEntry
from labrat.context_engine.bundle import ContextBundle, TableContext


class CatalogManager:
    """Aggregate catalog data from multiple adapters."""

    def __init__(self, adapters: list[CatalogAdapter]) -> None:
        self._adapters = adapters

    def load_all(self) -> dict[str, CatalogEntry]:
        """Load from all adapters; later adapters override earlier ones on conflict."""
        merged: dict[str, CatalogEntry] = {}
        for adapter in self._adapters:
            merged.update(adapter.load())
        return merged

    def merge_into_bundle(self, bundle: ContextBundle) -> ContextBundle:
        """Return the bundle enriched with catalog descriptions.

        For tables already in the bundle: sets catalog_description.
        For tables only in the catalog: appends a new TableContext entry.
        """
        catalog = self.load_all()
        existing_names = {t.table_name for t in bundle.tables}

        updated_tables: list[TableContext] = []
        for t in bundle.tables:
            entry = catalog.get(t.table_name)
            if entry is not None and entry.description:
                t = TableContext(
                    table_name=t.table_name,
                    schema_name=t.schema_name,
                    description=t.description,
                    frequent_columns=list(t.frequent_columns),
                    common_filters=list(t.common_filters),
                    common_joins=list(t.common_joins),
                    row_count_estimate=t.row_count_estimate,
                    catalog_description=entry.description,
                )
            updated_tables.append(t)

        for name, entry in catalog.items():
            if name not in existing_names:
                updated_tables.append(
                    TableContext(
                        table_name=name,
                        schema_name=entry.schema_name,
                        description="",
                        catalog_description=entry.description or None,
                    )
                )

        bundle.tables = updated_tables
        return bundle
