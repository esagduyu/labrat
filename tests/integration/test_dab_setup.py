"""Integration tests for scripts/dab_setup.py.

These tests require a local PostgreSQL instance. They use a sentinel DB name
that won't collide with real DAB datasets.
"""

from __future__ import annotations

import subprocess

import pytest

from scripts.dab_setup import pg_database_exists, pg_load_dataset

_SENTINEL_DB = "dabsetup_test_sentinel"


@pytest.fixture(autouse=True)
def _cleanup_sentinel():
    yield
    subprocess.run(
        [
            "psql",
            "-h",
            "localhost",
            "-d",
            "postgres",
            "-c",
            f"DROP DATABASE IF EXISTS {_SENTINEL_DB};",
        ],
        check=False,
    )


def test_pg_database_exists_false_before_create():
    assert pg_database_exists(_SENTINEL_DB) is False


def test_pg_database_exists_true_after_create(tmp_path):
    sql_file = tmp_path / "init.sql"
    sql_file.write_text("CREATE TABLE t (x int); INSERT INTO t VALUES (1), (2);")
    pg_load_dataset(_SENTINEL_DB, sql_file)
    assert pg_database_exists(_SENTINEL_DB) is True


def test_pg_load_dataset_is_idempotent(tmp_path):
    sql_file = tmp_path / "init.sql"
    sql_file.write_text("CREATE TABLE t (x int); INSERT INTO t VALUES (1);")
    pg_load_dataset(_SENTINEL_DB, sql_file)
    pg_load_dataset(_SENTINEL_DB, sql_file)  # second call must not raise
    assert pg_database_exists(_SENTINEL_DB) is True
