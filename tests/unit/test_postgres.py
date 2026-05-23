"""Tests for PostgresConnection (M23).

Unit tests verify class structure and mock-based behaviour.
Integration tests (gated by LABRAT_RUN_PG_TESTS=1) require a live Postgres
instance at PG_DSN (default: postgresql://labrat:labrat@localhost:5432/labrat).
"""

from __future__ import annotations

import os

import pytest

from labrat.db.postgres import PostgresConnection

_RUN_PG = os.environ.get("LABRAT_RUN_PG_TESTS")
_PG_DSN = os.environ.get("PG_DSN", "postgresql://labrat:labrat@localhost:5432/labrat")


# ── unit tests (no DB) ────────────────────────────────────────────────────────


def test_postgres_connection_is_connection() -> None:
    """PostgresConnection satisfies the Connection ABC."""
    from labrat.db.base import Connection

    assert issubclass(PostgresConnection, Connection)


def test_postgres_connection_init() -> None:
    """PostgresConnection stores DSN without connecting."""
    conn = PostgresConnection(_PG_DSN)
    assert conn is not None


def test_postgres_connection_repr() -> None:
    """Repr includes the connection class name."""
    conn = PostgresConnection(_PG_DSN)
    assert "PostgresConnection" in repr(conn) or "postgres" in str(conn).lower()


# ── integration tests (gated) ─────────────────────────────────────────────────


@pytest.mark.skipif(not _RUN_PG, reason="Set LABRAT_RUN_PG_TESTS=1 to run Postgres tests")
def test_pg_connect_and_ping() -> None:
    """Can connect and execute a trivial query."""
    import polars as pl

    conn = PostgresConnection(_PG_DSN)
    conn.connect()
    try:
        df = conn.execute("SELECT 1 AS n")
        assert isinstance(df, pl.DataFrame)
        assert df["n"][0] == 1
    finally:
        conn.disconnect()


@pytest.mark.skipif(not _RUN_PG, reason="Set LABRAT_RUN_PG_TESTS=1 to run Postgres tests")
def test_pg_introspect_catalog() -> None:
    """introspect_catalog returns a Catalog with at least pg_catalog tables."""
    conn = PostgresConnection(_PG_DSN)
    conn.connect()
    try:
        catalog = conn.introspect_catalog()
        assert catalog is not None
        assert len(catalog.tables) > 0
    finally:
        conn.disconnect()


@pytest.mark.skipif(not _RUN_PG, reason="Set LABRAT_RUN_PG_TESTS=1 to run Postgres tests")
def test_pg_explain() -> None:
    """explain() returns a non-empty string."""
    conn = PostgresConnection(_PG_DSN)
    conn.connect()
    try:
        plan = conn.explain("SELECT 1")
        assert isinstance(plan, str)
        assert len(plan) > 0
    finally:
        conn.disconnect()
