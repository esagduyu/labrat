"""Build a multi-DB ToolContext and attach-list for one DAB trial from db_config.yaml.

Real DAB db_config.yaml format:

  db_clients:
    <name>:
      db_type: duckdb | sqlite | postgres | mongodb
      db_path: <path relative to dataset dir>   # duckdb / sqlite
      db_name: <pg dbname>                       # postgres
      sql_file: <path>                           # postgres

Design (Phase 4):
  - DuckDB clients become real `DuckDBConnection`s in `ctx.connections`.
  - SQLite clients are NOT separate connections — they are exposed as `AttachSpec`s
    so the agent calls `attach_database` to bring them into the primary DuckDB
    session and JOIN via `alias.table_name`.
  - Postgres / MongoDB clients remain skipped until later phases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from labrat.agent.tools.base import ToolContext
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection


class AttachSpec(BaseModel):
    """A non-DuckDB database the agent can pull into the primary session via attach_database.

    ``path`` is the argument the ``attach_database`` tool will pass to DuckDB's
    ATTACH — a filesystem path for sqlite, a libpq connection string for postgres
    (e.g. ``host=localhost dbname=foo``), etc.
    """

    model_config = ConfigDict(frozen=True)

    alias: str
    path: str
    db_type: Literal["sqlite", "postgres", "mysql"]


class MongoSpec(BaseModel):
    """A MongoDB database the agent reaches via ``load_mongo_collection``.

    Mongo can't be ATTACH'd into DuckDB natively; the agent materializes individual
    collections into temp DuckDB tables and then queries them with ``run_sql``.
    """

    model_config = ConfigDict(frozen=True)

    alias: str
    database: str  # mongo database name (e.g. 'articles_db')


class DabTaskEnv(BaseModel):
    """Per-trial environment: primary DuckDB context + attachable secondaries + mongo dbs."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    ctx: ToolContext
    attachable: list[AttachSpec]
    mongo: list[MongoSpec] = []


def build_dab_task_env(db_config_path: Path) -> DabTaskEnv:
    """Parse db_config.yaml and return a DabTaskEnv.

    Primary connection = first DuckDB entry. SQLite entries become AttachSpecs.
    Postgres / MongoDB entries are dropped silently until adapters land.
    """
    dataset_dir = db_config_path.parent
    config = yaml.safe_load(db_config_path.read_text())
    clients: dict[str, Any] = config.get("db_clients") or {}

    connections: dict[str, object] = {}
    file_backed_duckdb: list[str] = []
    attachable: list[AttachSpec] = []
    mongo: list[MongoSpec] = []

    for name, spec in clients.items():
        db_type = str(spec.get("db_type", "")).lower()
        if db_type == "duckdb":
            db_path = dataset_dir / str(spec["db_path"])
            connections[name] = DuckDBConnection(path=db_path)
            file_backed_duckdb.append(name)
        elif db_type == "sqlite":
            attachable.append(
                AttachSpec(
                    alias=name,
                    path=str(dataset_dir / str(spec["db_path"])),
                    db_type="sqlite",
                )
            )
        elif db_type == "postgres":
            # DAB local setup (docs/dab_local_setup.md) runs Postgres on the
            # default socket; auth falls through to the current OS user.
            attachable.append(
                AttachSpec(
                    alias=name,
                    path=f"host=localhost dbname={spec['db_name']}",
                    db_type="postgres",
                )
            )
        elif db_type == "mongo":
            # Mongo isn't ATTACH-able by DuckDB; the agent calls
            # load_mongo_collection to materialize collections into temp tables.
            mongo.append(MongoSpec(alias=name, database=str(spec["db_name"])))

    # If no DuckDB primary, synthesize an in-memory one so the agent can still ATTACH.
    primary = file_backed_duckdb[0] if file_backed_duckdb else "__federation"
    if primary == "__federation":
        connections[primary] = DuckDBConnection(path=":memory:", read_only=False)

    catalogs: dict[str, object] = {
        name: Catalog(database_name=name, schemas=[]) for name in connections
    }

    ctx = ToolContext(
        connections=connections,
        catalogs=catalogs,
        primary=primary,
    )
    return DabTaskEnv(ctx=ctx, attachable=attachable, mongo=mongo)


def introspect_env_catalogs(ctx: ToolContext) -> None:
    """Populate ``ctx.catalogs`` from each *connected* connection, in place.

    ``build_dab_task_env`` constructs empty ``Catalog``s because connections are not
    yet ``connect()``-ed at build time. The driver connects at trial start and then
    calls this so the catalog-backed tools (``list_tables`` / ``describe_table`` /
    ``column_stats`` / ``search_columns``) see real schemas instead of nothing.

    Connections without an ``introspect_catalog`` method keep their empty catalog
    rather than aborting the trial. Must be called *after* the connections are
    connected (introspection runs over the live handle).
    """
    for name, conn in ctx.connections.items():
        introspect = getattr(conn, "introspect_catalog", None)
        if callable(introspect):
            ctx.catalogs[name] = introspect()
