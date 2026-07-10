"""resolve_from_env: byte-compatible env-JSON path + previously-unpinned rejections."""

from pathlib import Path

import pytest

from labrat.mcp.config import ResolvedConnections, resolve_from_env
from labrat.profile.manager import ProfileManager, make_profile


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


def test_blank_profiles_env_rejected() -> None:
    # LABRAT_MCP_PROFILES=" , " passes the "present" top guard (non-empty
    # string) but parses to zero profile names, and there's no env-JSON
    # either — connections ends up empty. Must exit(2) with the same
    # message as the missing-env case, not raise a bare StopIteration from
    # `next(iter(connections))`.
    with pytest.raises(SystemExit) as exc:
        resolve_from_env({"LABRAT_MCP_PROFILES": " , "})
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


def _mgr_with(tmp_path: Path, *profiles: object) -> ProfileManager:
    mgr = ProfileManager(profiles_path=tmp_path / "profiles.json")
    for p in profiles:
        mgr.add(p)  # type: ignore[arg-type]
    return mgr


def test_profile_backed_duckdb_resolves(tmp_path: Path) -> None:
    db = tmp_path / "p.duckdb"
    import duckdb

    duckdb.connect(str(db)).close()
    mgr = _mgr_with(tmp_path, make_profile(name="warehouse", dialect="duckdb", path=str(db)))
    rc = resolve_from_env({"LABRAT_MCP_PROFILES": "warehouse"}, manager_factory=lambda: mgr)
    assert set(rc.connections) == {"warehouse"}
    assert rc.primary == "warehouse"
    assert rc.profile_name == "warehouse"
    assert rc.read_only is True  # default is_read_only=True
    assert "warehouse" in rc.catalogs


def test_postgres_profile_uses_factory(tmp_path: Path) -> None:
    # Launch-pair coverage without a live PG: assert make_connection would be
    # called with the postgres profile, via the connection_factory seam.
    calls: list[str] = []

    class _FakeConn:
        def connect(self) -> None: ...
        def introspect_catalog(self) -> object:
            return object()

        def disconnect(self) -> None: ...

    def fake_factory(profile: object) -> object:
        calls.append(f"{profile.name}:{profile.dialect}")  # type: ignore[attr-defined]
        return _FakeConn()

    mgr = _mgr_with(
        tmp_path,
        make_profile(
            name="pg", dialect="postgres", host="h", port=5432, database="d", username="u"
        ),
    )
    rc = resolve_from_env(
        {"LABRAT_MCP_PROFILES": "pg"},
        manager_factory=lambda: mgr,
        connection_factory=fake_factory,  # type: ignore[arg-type]
    )
    assert calls == ["pg:postgres"]
    assert set(rc.connections) == {"pg"}


def test_unknown_profile_exits_2(tmp_path: Path) -> None:
    mgr = _mgr_with(tmp_path)
    with pytest.raises(SystemExit) as exc:
        resolve_from_env({"LABRAT_MCP_PROFILES": "ghost"}, manager_factory=lambda: mgr)
    assert exc.value.code == 2


def test_name_collision_exits_2(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    import duckdb

    duckdb.connect(str(db)).close()
    mgr = _mgr_with(tmp_path, make_profile(name="main", dialect="duckdb", path=str(db)))
    env = _duckdb_env(tmp_path)  # env-JSON already defines "main"
    env["LABRAT_MCP_PROFILES"] = "main"
    with pytest.raises(SystemExit) as exc:
        resolve_from_env(env, manager_factory=lambda: mgr)
    assert exc.value.code == 2


def test_mixed_sources_and_writable_profile(tmp_path: Path) -> None:
    db = tmp_path / "w.duckdb"
    import duckdb

    duckdb.connect(str(db)).close()
    # make_profile takes is_read_only directly (checked manager.py:174-201) — no
    # model_copy needed.
    writable = make_profile(name="rw", dialect="duckdb", path=str(db), is_read_only=False)
    mgr = _mgr_with(tmp_path, writable)
    env = _duckdb_env(tmp_path)
    env["LABRAT_MCP_PROFILES"] = "rw"
    env["LABRAT_MCP_PRIMARY"] = "rw"
    rc = resolve_from_env(env, manager_factory=lambda: mgr)
    assert set(rc.connections) == {"main", "rw"}
    assert rc.primary == "rw" and rc.profile_name == "rw"
    # combined read_only per the shipped rule, derived:
    # - env-JSON "main" omits "read_only" -> Task-1 default -> env-JSON
    #   contribution is False (open/DAB-compat; not vacuously True — the
    #   env-JSON contribution is gated on spec being non-empty).
    # - profile "rw" is explicitly is_read_only=False -> no profile in the
    #   profiles set has is_read_only=True -> profiles contribution is False.
    # combined = env-JSON contribution (False) OR profiles contribution (False)
    assert rc.read_only is False
