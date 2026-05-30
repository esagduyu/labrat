"""Build a multi-DB ToolContext for one DAB trial from db_config.yaml.

Real DAB db_config.yaml format:

  db_clients:
    <name>:
      db_type: duckdb | sqlite | postgres | mongodb
      db_path: <path relative to dataset dir>   # duckdb / sqlite
      db_name: <pg dbname>                       # postgres
      sql_file: <path>                           # postgres
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from labrat.agent.tools.base import ToolContext
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection


def _build_connection(spec: dict[str, Any], dataset_dir: Path) -> DuckDBConnection | None:
    """Return a DuckDBConnection for the spec, or None if unsupported in Phase 1a."""
    db_type = str(spec.get("db_type", "")).lower()
    if db_type == "duckdb":
        db_path = dataset_dir / str(spec["db_path"])
        return DuckDBConnection(path=db_path)
    if db_type == "sqlite":
        # SQLite reached via DuckDB ATTACH — use in-memory DuckDB as the host.
        return DuckDBConnection(path=":memory:")
    # postgres / mongodb — Phase 1b
    return None


def build_dab_tool_context(db_config_path: Path) -> ToolContext:
    """Parse db_config.yaml and return a multi-DB ToolContext.

    Primary = first DuckDB entry (agent's run_sql default routes here).
    SQLite entries get an in-memory DuckDB proxy (ATTACH wired in Phase 1b).
    Postgres / MongoDB entries are skipped until Phase 1b.
    """
    dataset_dir = db_config_path.parent
    config = yaml.safe_load(db_config_path.read_text())
    clients: dict[str, Any] = config.get("db_clients") or {}

    connections: dict[str, object] = {}
    file_backed_duckdb: list[str] = []
    for name, spec in clients.items():
        conn = _build_connection(spec, dataset_dir)
        if conn is not None:
            connections[name] = conn
            if str(spec.get("db_type", "")).lower() == "duckdb":
                file_backed_duckdb.append(name)

    # Primary = first file-backed DuckDB, then first connection overall.
    duckdb_primary: str | None = file_backed_duckdb[0] if file_backed_duckdb else None
    if duckdb_primary is None and connections:
        duckdb_primary = next(iter(connections))

    # If nothing at all was built, add a federation host.
    if not connections or duckdb_primary is None:
        connections["__federation"] = DuckDBConnection(path=":memory:")
        duckdb_primary = "__federation"

    catalogs: dict[str, object] = {
        name: Catalog(database_name=name, schemas=[]) for name in connections
    }

    return ToolContext(
        connections=connections,
        catalogs=catalogs,
        primary=duckdb_primary,
    )
