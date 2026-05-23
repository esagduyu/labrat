"""Conformance tests for warehouse adapters (M25).

Unit tests verify structure and instantiation without live connections.
Integration tests per adapter are gated by individual env vars:
  LABRAT_RUN_SNOWFLAKE_TESTS=1  (needs SNOWFLAKE_DSN)
  LABRAT_RUN_BQ_TESTS=1         (needs BQ_PROJECT)
  LABRAT_RUN_REDSHIFT_TESTS=1   (needs REDSHIFT_DSN)
  LABRAT_RUN_TRINO_TESTS=1      (needs TRINO_HOST)
  LABRAT_RUN_MYSQL_TESTS=1      (needs MYSQL_DSN)
"""

from __future__ import annotations

import os

from labrat.db.base import Connection

# ── conformance helpers ───────────────────────────────────────────────────────


def _check_is_connection(cls: type) -> None:
    assert issubclass(cls, Connection)


# ── Snowflake ─────────────────────────────────────────────────────────────────

_RUN_SNOW = os.environ.get("LABRAT_RUN_SNOWFLAKE_TESTS")


def test_snowflake_connection_is_connection() -> None:
    from labrat.db.snowflake import SnowflakeConnection

    _check_is_connection(SnowflakeConnection)


def test_snowflake_connection_init() -> None:
    from labrat.db.snowflake import SnowflakeConnection

    conn = SnowflakeConnection(
        account="myaccount",
        user="myuser",
        password="secret",
        database="mydb",
        schema="public",
        warehouse="compute_wh",
    )
    assert conn is not None


def test_snowflake_connection_repr() -> None:
    from labrat.db.snowflake import SnowflakeConnection

    conn = SnowflakeConnection(account="acct", user="u", password="p")
    assert "Snowflake" in repr(conn) or "snowflake" in str(conn).lower()


# ── BigQuery ──────────────────────────────────────────────────────────────────

_RUN_BQ = os.environ.get("LABRAT_RUN_BQ_TESTS")


def test_bigquery_connection_is_connection() -> None:
    from labrat.db.bigquery import BigQueryConnection

    _check_is_connection(BigQueryConnection)


def test_bigquery_connection_init() -> None:
    from labrat.db.bigquery import BigQueryConnection

    conn = BigQueryConnection(project="my-project", dataset="my_dataset")
    assert conn is not None


def test_bigquery_connection_repr() -> None:
    from labrat.db.bigquery import BigQueryConnection

    conn = BigQueryConnection(project="my-project")
    assert "BigQuery" in repr(conn) or "bigquery" in str(conn).lower()


# ── Redshift ──────────────────────────────────────────────────────────────────

_RUN_REDSHIFT = os.environ.get("LABRAT_RUN_REDSHIFT_TESTS")
_REDSHIFT_DSN = os.environ.get(
    "REDSHIFT_DSN", "redshift+redshift_connector://user:pass@host:5439/db"
)


def test_redshift_connection_is_connection() -> None:
    from labrat.db.redshift import RedshiftConnection

    _check_is_connection(RedshiftConnection)


def test_redshift_connection_init() -> None:
    from labrat.db.redshift import RedshiftConnection

    conn = RedshiftConnection(
        host="cluster.region.redshift.amazonaws.com",
        port=5439,
        database="mydb",
        user="myuser",
        password="secret",
    )
    assert conn is not None


def test_redshift_connection_repr() -> None:
    from labrat.db.redshift import RedshiftConnection

    conn = RedshiftConnection(host="cluster", database="db", user="u", password="p")
    assert "Redshift" in repr(conn) or "redshift" in str(conn).lower()


# ── Trino ─────────────────────────────────────────────────────────────────────

_RUN_TRINO = os.environ.get("LABRAT_RUN_TRINO_TESTS")


def test_trino_connection_is_connection() -> None:
    from labrat.db.trino import TrinoConnection

    _check_is_connection(TrinoConnection)


def test_trino_connection_init() -> None:
    from labrat.db.trino import TrinoConnection

    conn = TrinoConnection(
        host="localhost",
        port=8080,
        user="trino",
        catalog="hive",
        schema="default",
    )
    assert conn is not None


def test_trino_connection_repr() -> None:
    from labrat.db.trino import TrinoConnection

    conn = TrinoConnection(host="localhost", user="u")
    assert "Trino" in repr(conn) or "trino" in str(conn).lower()


# ── MySQL ─────────────────────────────────────────────────────────────────────

_RUN_MYSQL = os.environ.get("LABRAT_RUN_MYSQL_TESTS")


def test_mysql_connection_is_connection() -> None:
    from labrat.db.mysql import MySQLConnection

    _check_is_connection(MySQLConnection)


def test_mysql_connection_init() -> None:
    from labrat.db.mysql import MySQLConnection

    conn = MySQLConnection(
        host="localhost",
        port=3306,
        database="mydb",
        user="myuser",
        password="secret",
    )
    assert conn is not None


def test_mysql_connection_repr() -> None:
    from labrat.db.mysql import MySQLConnection

    conn = MySQLConnection(host="localhost", database="db", user="u", password="p")
    assert "MySQL" in repr(conn) or "mysql" in str(conn).lower()


# ── make_connection dialect routing ──────────────────────────────────────────


def test_make_connection_snowflake() -> None:
    from labrat.db.snowflake import SnowflakeConnection
    from labrat.profile.manager import make_connection, make_profile

    profile = make_profile(
        "sf-dev",
        "snowflake",
        host="myaccount.snowflakecomputing.com",
        database="mydb",
        username="myuser",
        has_secret=True,
    )
    conn = make_connection(profile)
    assert isinstance(conn, SnowflakeConnection)


def test_make_connection_mysql() -> None:
    from labrat.db.mysql import MySQLConnection
    from labrat.profile.manager import make_connection, make_profile

    profile = make_profile(
        "mysql-dev",
        "mysql",
        host="localhost",
        port=3306,
        database="mydb",
        username="myuser",
    )
    conn = make_connection(profile)
    assert isinstance(conn, MySQLConnection)


def test_make_connection_redshift() -> None:
    from labrat.db.redshift import RedshiftConnection
    from labrat.profile.manager import make_connection, make_profile

    profile = make_profile(
        "rs-dev",
        "redshift",
        host="cluster.us-east-1.redshift.amazonaws.com",
        port=5439,
        database="mydb",
        username="myuser",
    )
    conn = make_connection(profile)
    assert isinstance(conn, RedshiftConnection)


def test_make_connection_trino() -> None:
    from labrat.db.trino import TrinoConnection
    from labrat.profile.manager import make_connection, make_profile

    profile = make_profile(
        "trino-dev",
        "trino",
        host="localhost",
        port=8080,
        database="hive",
        username="trino",
    )
    conn = make_connection(profile)
    assert isinstance(conn, TrinoConnection)


def test_make_connection_bigquery() -> None:
    from labrat.db.bigquery import BigQueryConnection
    from labrat.profile.manager import make_connection, make_profile

    profile = make_profile(
        "bq-dev",
        "bigquery",
        database="my-project",
        default_schema="my_dataset",
    )
    conn = make_connection(profile)
    assert isinstance(conn, BigQueryConnection)
