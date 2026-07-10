"""resolve_from_env: byte-compatible env-JSON path + previously-unpinned rejections."""

from pathlib import Path

import pytest

from labrat.mcp.config import ResolvedConnections, resolve_from_env


def _duckdb_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    db = tmp_path / "t.duckdb"
    import duckdb

    duckdb.connect(str(db)).close()
    env = {"LABRAT_MCP_CONNECTIONS": f'{{"main": {{"db_type": "duckdb", "db_path": "{db}"}}}}'}
    env.update(extra)
    return env


def test_duckdb_env_json_resolves(tmp_path: Path) -> None:
    rc = resolve_from_env(_duckdb_env(tmp_path))
    assert isinstance(rc, ResolvedConnections)
    assert set(rc.connections) == {"main"} and rc.primary == "main"
    # Spec omitted "read_only" -> ctx-level read_only defaults to False, matching
    # TODAY's pre-extraction behavior (ToolContext never received read_only at all)
    # AND DAB's LABRAT_MCP_CONNECTIONS builder (suite.py), which never sets the key.
    # Byte-compat with DAB outranks a safety-first True default on this legacy path.
    assert rc.read_only is False
    assert rc.profile_name == "default"
    assert "main" in rc.catalogs


def test_duckdb_env_json_explicit_read_only_true_honored(tmp_path: Path) -> None:
    env = _duckdb_env(tmp_path)
    db = tmp_path / "t.duckdb"
    env["LABRAT_MCP_CONNECTIONS"] = (
        f'{{"main": {{"db_type": "duckdb", "db_path": "{db}", "read_only": true}}}}'
    )
    rc = resolve_from_env(env)
    assert rc.read_only is True


def test_non_duckdb_db_type_rejected(tmp_path: Path) -> None:
    env = {"LABRAT_MCP_CONNECTIONS": '{"pg": {"db_type": "postgres", "db_path": "x"}}'}
    with pytest.raises(SystemExit) as exc:
        resolve_from_env(env)
    assert exc.value.code == 2


def test_unknown_primary_rejected(tmp_path: Path) -> None:
    env = _duckdb_env(tmp_path, LABRAT_MCP_PRIMARY="nope")
    with pytest.raises(SystemExit) as exc:
        resolve_from_env(env)
    assert exc.value.code == 2


def test_missing_connections_env_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        resolve_from_env({})
    assert exc.value.code == 2


def test_memory_primary_writable(tmp_path: Path) -> None:
    # Ported invariant from test_mcp_server.py::test_build_context_from_env_allows_in_memory_primary
    # — a :memory: primary (the DAB federation workspace) must come up writable:
    # DuckDB cannot even open :memory: read-only, and the agent ATTACHes / loads
    # Mongo into it.
    env = {"LABRAT_MCP_CONNECTIONS": '{"ws": {"db_type": "duckdb", "db_path": ":memory:"}}'}
    rc = resolve_from_env(env)
    conn = rc.connections["ws"]
    # Writable check: CREATE would raise on a read-only connection.
    conn._conn.execute("CREATE TABLE t(x INTEGER)")  # type: ignore[attr-defined]
