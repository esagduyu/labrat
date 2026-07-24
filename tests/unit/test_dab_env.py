from pathlib import Path
from typing import cast

import yaml

from labrat.db.catalog import Catalog
from labrat.eval.benchmarks.dab.env import build_dab_task_env, introspect_env_catalogs


def _write_config(path: Path, clients: dict) -> None:  # type: ignore[type-arg]
    path.write_text(yaml.safe_dump({"db_clients": clients}))


def test_build_task_env_with_only_duckdb(tmp_path: Path) -> None:
    duckdb_path = tmp_path / "main.duckdb"
    duckdb_path.touch()
    config = tmp_path / "db_config.yaml"
    _write_config(
        config,
        {
            "main_database": {
                "db_type": "duckdb",
                "db_path": "main.duckdb",
            }
        },
    )
    env = build_dab_task_env(config)
    assert env.ctx.primary == "main_database"
    assert set(env.ctx.connections.keys()) == {"main_database"}
    assert env.attachable == []


def test_build_task_env_sqlite_is_attachable_not_connection(tmp_path: Path) -> None:
    (tmp_path / "aux.db").touch()
    (tmp_path / "main.duckdb").touch()
    config = tmp_path / "db_config.yaml"
    _write_config(
        config,
        {
            "aux_database": {"db_type": "sqlite", "db_path": "aux.db"},
            "main_database": {"db_type": "duckdb", "db_path": "main.duckdb"},
        },
    )
    env = build_dab_task_env(config)
    assert env.ctx.primary == "main_database"
    assert set(env.ctx.connections.keys()) == {"main_database"}
    assert "aux_database" not in env.ctx.connections
    assert len(env.attachable) == 1
    spec = env.attachable[0]
    assert spec.alias == "aux_database"
    assert spec.db_type == "sqlite"
    assert spec.path.endswith("aux.db")


def test_build_task_env_postgres_is_attachable_not_connection(tmp_path: Path) -> None:
    (tmp_path / "main.duckdb").touch()
    config = tmp_path / "db_config.yaml"
    _write_config(
        config,
        {
            "pg_db": {"db_type": "postgres", "db_name": "some_pg"},
            "duckdb": {"db_type": "duckdb", "db_path": "main.duckdb"},
        },
    )
    env = build_dab_task_env(config)
    assert "pg_db" not in env.ctx.connections
    assert env.ctx.primary == "duckdb"
    assert len(env.attachable) == 1
    spec = env.attachable[0]
    assert spec.alias == "pg_db"
    assert spec.db_type == "postgres"
    assert spec.path == "host=localhost dbname=some_pg"


def test_build_task_env_catalogs_match_connections(tmp_path: Path) -> None:
    (tmp_path / "main.duckdb").touch()
    config = tmp_path / "db_config.yaml"
    _write_config(config, {"db": {"db_type": "duckdb", "db_path": "main.duckdb"}})
    env = build_dab_task_env(config)
    assert set(env.ctx.catalogs.keys()) == set(env.ctx.connections.keys())


def test_build_task_env_mongo_becomes_mongo_spec_not_attachable(tmp_path: Path) -> None:
    (tmp_path / "main.duckdb").touch()
    config = tmp_path / "db_config.yaml"
    _write_config(
        config,
        {
            "articles": {"db_type": "mongo", "db_name": "articles_db"},
            "main": {"db_type": "duckdb", "db_path": "main.duckdb"},
        },
    )
    env = build_dab_task_env(config)
    assert env.attachable == []
    assert len(env.mongo) == 1
    assert env.mongo[0].alias == "articles"
    assert env.mongo[0].database == "articles_db"
    assert env.ctx.primary == "main"


def test_introspect_env_catalogs_populates_from_connected_duckdb(tmp_path: Path) -> None:
    """The labrat-agent driver bug: build_dab_task_env yields empty catalogs because
    connections aren't connect()-ed yet. After connecting, introspect_env_catalogs
    must fill ctx.catalogs so list_tables/describe_table/column_stats work."""
    import duckdb

    duckdb_path = tmp_path / "main.duckdb"
    con = duckdb.connect(str(duckdb_path))
    con.execute("CREATE TABLE widgets (id INTEGER, name TEXT)")
    con.close()

    config = tmp_path / "db_config.yaml"
    _write_config(config, {"db": {"db_type": "duckdb", "db_path": "main.duckdb"}})
    env = build_dab_task_env(config)

    # Empty before introspection — the bug this fixes.
    assert cast(Catalog, env.ctx.catalogs["db"]).schemas == []

    for conn in env.ctx.connections.values():
        conn.connect()  # type: ignore[attr-defined]
    try:
        introspect_env_catalogs(env.ctx)
    finally:
        for conn in env.ctx.connections.values():
            conn.disconnect()  # type: ignore[attr-defined]

    after = cast(Catalog, env.ctx.catalogs["db"])
    table_names = {t.name for s in after.schemas for t in s.tables}
    assert "widgets" in table_names


def test_introspect_env_catalogs_ignores_connections_without_introspect(tmp_path: Path) -> None:
    """A connection lacking introspect_catalog keeps its (empty) catalog, no crash."""
    from labrat.agent.tools.base import ToolContext

    ctx = ToolContext(
        connections={"x": object()},
        catalogs={"x": Catalog(database_name="x", schemas=[])},
        primary="x",
    )
    introspect_env_catalogs(ctx)
    assert cast(Catalog, ctx.catalogs["x"]).schemas == []


def test_secondary_duckdb_becomes_attachable(tmp_path: Path) -> None:
    # NOTE: written with literal text (not the _write_config helper) because
    # yaml.safe_dump's default sort_keys=True would alphabetize "activities"
    # before "sales_pipeline" and silently flip which one becomes primary.
    (tmp_path / "primary.duckdb").touch()
    (tmp_path / "activities.duckdb").touch()
    config = tmp_path / "db_config.yaml"
    config.write_text(
        "db_clients:\n"
        "  sales_pipeline: {db_type: duckdb, db_path: primary.duckdb}\n"
        "  activities: {db_type: duckdb, db_path: activities.duckdb}\n"
    )
    env = build_dab_task_env(config)
    assert env.ctx.primary == "sales_pipeline"
    aliases = {s.alias: s.db_type for s in env.attachable}
    assert aliases == {"activities": "duckdb"}
    assert env.attachable[0].path.endswith("activities.duckdb")


def test_build_task_env_federation_host_when_no_duckdb(tmp_path: Path) -> None:
    (tmp_path / "aux.db").touch()
    config = tmp_path / "db_config.yaml"
    _write_config(
        config,
        {"aux_database": {"db_type": "sqlite", "db_path": "aux.db"}},
    )
    env = build_dab_task_env(config)
    assert env.ctx.primary == "__federation"
    assert "__federation" in env.ctx.connections
    assert len(env.attachable) == 1
