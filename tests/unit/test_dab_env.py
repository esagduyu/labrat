from pathlib import Path

import yaml

from labrat.eval.benchmarks.dab.env import build_dab_task_env


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


def test_build_task_env_skips_postgres_entries(tmp_path: Path) -> None:
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
    assert env.attachable == []


def test_build_task_env_catalogs_match_connections(tmp_path: Path) -> None:
    (tmp_path / "main.duckdb").touch()
    config = tmp_path / "db_config.yaml"
    _write_config(config, {"db": {"db_type": "duckdb", "db_path": "main.duckdb"}})
    env = build_dab_task_env(config)
    assert set(env.ctx.catalogs.keys()) == set(env.ctx.connections.keys())


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
