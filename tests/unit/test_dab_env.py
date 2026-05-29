from pathlib import Path

import yaml

from labrat.eval.benchmarks.dab.env import build_dab_tool_context


def _write_config(path: Path, clients: dict) -> None:  # type: ignore[type-arg]
    path.write_text(yaml.safe_dump({"db_clients": clients}))


def test_build_tool_context_with_only_duckdb(tmp_path: Path) -> None:
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
    ctx = build_dab_tool_context(config)
    assert ctx.primary == "main_database"
    assert set(ctx.connections.keys()) == {"main_database"}


def test_build_tool_context_picks_duckdb_as_primary_over_sqlite(tmp_path: Path) -> None:
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
    ctx = build_dab_tool_context(config)
    assert ctx.primary == "main_database"
    assert "aux_database" in ctx.connections
    assert "main_database" in ctx.connections


def test_build_tool_context_skips_postgres_entries(tmp_path: Path) -> None:
    (tmp_path / "main.duckdb").touch()
    config = tmp_path / "db_config.yaml"
    _write_config(
        config,
        {
            "pg_db": {"db_type": "postgres", "db_name": "some_pg"},
            "duckdb": {"db_type": "duckdb", "db_path": "main.duckdb"},
        },
    )
    ctx = build_dab_tool_context(config)
    assert "pg_db" not in ctx.connections
    assert ctx.primary == "duckdb"


def test_build_tool_context_catalogs_match_connections(tmp_path: Path) -> None:
    (tmp_path / "main.duckdb").touch()
    config = tmp_path / "db_config.yaml"
    _write_config(config, {"db": {"db_type": "duckdb", "db_path": "main.duckdb"}})
    ctx = build_dab_tool_context(config)
    assert set(ctx.catalogs.keys()) == set(ctx.connections.keys())
