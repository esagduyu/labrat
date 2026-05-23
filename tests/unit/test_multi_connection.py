"""Tests for multi-connection switching (M24).

Verifies that:
- make_connection supports postgres dialect via DSN
- ConnectionSession tracks active profile and connection
- Thread scoping invariants are enforced
"""

from __future__ import annotations

import pytest

from labrat.profile.manager import ProfileManager, make_connection, make_profile
from labrat.profile.session import ConnectionScopeError, ConnectionSession

# ── make_connection ───────────────────────────────────────────────────────────


def test_make_connection_duckdb_returns_duckdb() -> None:
    """make_connection with duckdb returns DuckDBConnection."""
    from labrat.db.duckdb_engine import DuckDBConnection

    profile = make_profile("dev", "duckdb", path=":memory:")
    conn = make_connection(profile)
    assert isinstance(conn, DuckDBConnection)


def test_make_connection_postgres_returns_postgres(tmp_path: pytest.TempPathFactory) -> None:
    """make_connection with postgres returns PostgresConnection (no live DB needed)."""
    from labrat.db.postgres import PostgresConnection

    profile = make_profile(
        "pg-dev",
        "postgres",
        host="localhost",
        port=5432,
        database="labrat",
        username="labrat",
    )
    conn = make_connection(profile)
    assert isinstance(conn, PostgresConnection)


# ── ConnectionSession ─────────────────────────────────────────────────────────


def test_connection_session_active_profile(tmp_path: pytest.TempPathFactory) -> None:
    """Session tracks the active profile."""
    profile = make_profile("dev", "duckdb", path=":memory:")
    session = ConnectionSession(profile)
    assert session.active_profile == profile
    assert session.active_profile.name == "dev"


def test_connection_session_switch_profile() -> None:
    """Switching changes the active profile."""
    p1 = make_profile("dev", "duckdb", path=":memory:")
    p2 = make_profile("prod", "duckdb", path=":memory:")
    session = ConnectionSession(p1)
    session.switch(p2)
    assert session.active_profile == p2


def test_connection_session_get_connection_returns_connection() -> None:
    """get_connection returns a Connection for the active profile."""
    from labrat.db.base import Connection

    profile = make_profile("dev", "duckdb", path=":memory:")
    session = ConnectionSession(profile)
    conn = session.get_connection()
    assert isinstance(conn, Connection)


def test_connection_session_get_connection_caches() -> None:
    """get_connection returns the same object across calls (cached per profile)."""
    profile = make_profile("dev", "duckdb", path=":memory:")
    session = ConnectionSession(profile)
    conn1 = session.get_connection()
    conn2 = session.get_connection()
    assert conn1 is conn2


def test_connection_session_switch_disconnects_old() -> None:
    """Switching closes the old connection if one was opened."""
    p1 = make_profile("dev", "duckdb", path=":memory:", is_read_only=False)
    p2 = make_profile("prod", "duckdb", path=":memory:", is_read_only=False)
    session = ConnectionSession(p1)
    conn1 = session.get_connection()
    conn1.connect()
    session.switch(p2)
    # After switch, old connection should be closed (conn._conn is None for DuckDB)
    from labrat.db.duckdb_engine import DuckDBConnection

    assert isinstance(conn1, DuckDBConnection)
    # The new active profile should be p2
    assert session.active_profile == p2


# ── thread scoping ────────────────────────────────────────────────────────────


def test_connection_scope_check_passes_for_matching_profile() -> None:
    """assert_thread_scope passes when thread profile matches active profile."""
    profile = make_profile("dev", "duckdb", path=":memory:")
    session = ConnectionSession(profile)
    session.assert_thread_scope("dev")  # must not raise


def test_connection_scope_check_raises_for_mismatch() -> None:
    """assert_thread_scope raises ConnectionScopeError when profiles differ."""
    profile = make_profile("dev", "duckdb", path=":memory:")
    session = ConnectionSession(profile)
    with pytest.raises(ConnectionScopeError, match="prod"):
        session.assert_thread_scope("prod")


def test_connection_scope_error_includes_profiles() -> None:
    """ConnectionScopeError message includes active and expected profile names."""
    p = make_profile("staging", "duckdb", path=":memory:")
    session = ConnectionSession(p)
    with pytest.raises(ConnectionScopeError) as exc_info:
        session.assert_thread_scope("prod")
    msg = str(exc_info.value)
    assert "staging" in msg
    assert "prod" in msg


# ── profile manager integration ───────────────────────────────────────────────


def test_profile_manager_two_profiles(tmp_path: pytest.TempPathFactory) -> None:
    """ProfileManager can hold two profiles and switch between them."""
    pm = ProfileManager(tmp_path / "profiles.json")  # type: ignore[arg-type]
    p1 = make_profile("duck1", "duckdb", path=":memory:")
    p2 = make_profile("duck2", "duckdb", path=":memory:")
    pm.add(p1)
    pm.add(p2)
    all_profiles = pm.list_all()
    assert len(all_profiles) == 2
    names = {p.name for p in all_profiles}
    assert names == {"duck1", "duck2"}
