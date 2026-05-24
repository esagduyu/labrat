"""Parse dbt artifacts into CatalogEntry objects (M30).

Priority order:
  1. manifest.json (from `dbt parse` / `dbt compile`)
  2. catalog.json (from `dbt docs generate`) — enriches row counts
  3. schema.yml fallback — when no compiled artifacts exist

PII detection: column meta.pii == true marks the column as PII.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from labrat.catalog.base import CatalogAdapter, CatalogEntry, ColumnEntry

_MANIFEST_NAME = "manifest.json"
_CATALOG_NAME = "catalog.json"
_MODEL_RESOURCE_TYPE = "model"


def _extract_columns(raw_cols: dict[str, Any]) -> dict[str, ColumnEntry]:
    cols: dict[str, ColumnEntry] = {}
    for col_name, col_data in raw_cols.items():
        meta: dict[str, Any] = col_data.get("meta", {}) or {}
        cols[col_name] = ColumnEntry(
            name=col_name,
            description=col_data.get("description", ""),
            data_type=col_data.get("data_type", ""),
            is_pii=bool(meta.get("pii", False)),
        )
    return cols


def _model_name_from_unique_id(unique_id: str) -> str:
    """'model.project.name' → 'name'"""
    return unique_id.split(".")[-1]


def _upstream_models(depends_on_nodes: list[str]) -> list[str]:
    """Filter to only model nodes (not sources/tests)."""
    names: list[str] = []
    for uid in depends_on_nodes:
        if uid.startswith("model."):
            names.append(_model_name_from_unique_id(uid))
    return names


class DbtLoader(CatalogAdapter):
    """Load dbt catalog from manifest.json (+ optional catalog.json) or schema.yml files."""

    def __init__(
        self,
        project_path: Path,
        catalog_json: Path | None = None,
    ) -> None:
        self._project_path = project_path
        self._catalog_json = catalog_json

    @property
    def name(self) -> str:
        return "dbt"

    def load(self) -> dict[str, CatalogEntry]:
        manifest_path = self._project_path / _MANIFEST_NAME
        if manifest_path.exists():
            entries = self._load_manifest(manifest_path)
        else:
            entries = self._load_schema_yml()

        if self._catalog_json and self._catalog_json.exists():
            self._enrich_from_catalog(entries, self._catalog_json)

        return entries

    # ── private ───────────────────────────────────────────────────────────────

    def _load_manifest(self, path: Path) -> dict[str, CatalogEntry]:
        raw: dict[str, Any] = json.loads(path.read_text())
        nodes: dict[str, Any] = raw.get("nodes", {})
        entries: dict[str, CatalogEntry] = {}

        for uid, node in nodes.items():
            if node.get("resource_type") != _MODEL_RESOURCE_TYPE:
                continue
            name = node.get("name", _model_name_from_unique_id(uid))
            depends_on: dict[str, Any] = node.get("depends_on", {})
            upstream = _upstream_models(depends_on.get("nodes", []))
            entries[name] = CatalogEntry(
                name=name,
                schema_name=node.get("schema", "public"),
                description=node.get("description", ""),
                columns=_extract_columns(node.get("columns", {})),
                tags=node.get("tags", []),
                upstream=upstream,
                downstream=[],
                compiled_sql=node.get("compiled_code", ""),
            )

        # Build downstream edges from upstream data
        for name, entry in entries.items():
            for up in entry.upstream:
                if up in entries:
                    entries[up].downstream.append(name)

        return entries

    def _load_schema_yml(self) -> dict[str, CatalogEntry]:
        entries: dict[str, CatalogEntry] = {}
        for schema_file in self._project_path.rglob("schema.yml"):
            raw: dict[str, Any] = yaml.safe_load(schema_file.read_text()) or {}
            for model in raw.get("models", []):
                name: str = model.get("name", "")
                if not name:
                    continue
                cols: dict[str, ColumnEntry] = {}
                for col in model.get("columns", []):
                    col_name: str = col.get("name", "")
                    if not col_name:
                        continue
                    meta: dict[str, Any] = col.get("meta", {}) or {}
                    cols[col_name] = ColumnEntry(
                        name=col_name,
                        description=col.get("description", ""),
                        data_type=str(meta.get("data_type", "")),
                        is_pii=bool(meta.get("pii", False)),
                    )
                entries[name] = CatalogEntry(
                    name=name,
                    schema_name="public",
                    description=model.get("description", ""),
                    columns=cols,
                    tags=model.get("tags", []),
                )
        return entries

    def _enrich_from_catalog(self, entries: dict[str, CatalogEntry], catalog_path: Path) -> None:
        raw: dict[str, Any] = json.loads(catalog_path.read_text())
        for uid, node_data in raw.get("nodes", {}).items():
            name = _model_name_from_unique_id(uid)
            if name not in entries:
                continue
            stats: dict[str, Any] = node_data.get("stats", {})
            row_count_stat = stats.get("row_count", {})
            if row_count_stat.get("include", False):
                val = row_count_stat.get("value")
                if isinstance(val, (int, float)):
                    entries[name].row_count = int(val)

            # Enrich column types from catalog.json (warehouse-reported types)
            cat_cols: dict[str, Any] = node_data.get("columns", {})
            for col_key, col_data in cat_cols.items():
                col_name = col_key.lower()
                cat_type: str = col_data.get("type", "")
                if not cat_type:
                    continue
                entry = entries[name]
                if col_name in entry.columns:
                    existing = entry.columns[col_name]
                    if not existing.data_type:
                        entry.columns[col_name] = existing.model_copy(
                            update={"data_type": cat_type}
                        )
                else:
                    entry.columns[col_name] = ColumnEntry(name=col_name, data_type=cat_type)
