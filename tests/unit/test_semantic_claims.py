# tests/unit/test_semantic_claims.py
from __future__ import annotations

import duckdb

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.semantic_claims import (
    JoinClaim,
    RoleClaim,
    parse_semantic_claims,
    verify_role_claim,
)


def test_parses_join_and_role_lines() -> None:
    text = (
        "JOIN orders.customer_id = customers.id\n"
        "ROLE clinical.icd_o_3_histology CODES clinical.histological_type\n"
        "this is prose, ignore it\n"
        "JOIN malformed line\n"
    )
    claims = parse_semantic_claims(text)
    assert (
        JoinClaim(
            left_table="orders", left_col="customer_id", right_table="customers", right_col="id"
        )
        in claims
    )
    assert (
        RoleClaim(table="clinical", code_col="icd_o_3_histology", name_col="histological_type")
        in claims
    )
    assert len(claims) == 2  # garbage + malformed ignored


def test_empty_text_no_claims() -> None:
    assert parse_semantic_claims("") == []


def _clinical_conn(tmp_path):
    p = str(tmp_path / "c.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE clinical(icd_o_3_histology VARCHAR, histological_type VARCHAR)")
    raw.execute(
        "INSERT INTO clinical VALUES "
        "('9400/3','Astrocytoma'),('9401/3','Astrocytoma'),('9450/3','Oligodendroglioma'),"
        "('[Not Applicable]','Oligodendroglioma'),('9382/3','Oligoastrocytoma')"
    )
    raw.close()
    c = DuckDBConnection(path=p, read_only=False)
    c.connect()
    return c


def test_role_claim_correct_direction_survives(tmp_path) -> None:
    conn = _clinical_conn(tmp_path)
    assert (
        verify_role_claim(
            conn,
            RoleClaim(table="clinical", code_col="icd_o_3_histology", name_col="histological_type"),
        )
        is True
    )
    conn.disconnect()


def test_role_claim_reversed_direction_dropped(tmp_path) -> None:
    conn = _clinical_conn(tmp_path)
    # reversed: claims the NAME column is the code column → must be dropped
    assert (
        verify_role_claim(
            conn,
            RoleClaim(table="clinical", code_col="histological_type", name_col="icd_o_3_histology"),
        )
        is False
    )
    conn.disconnect()


def test_role_claim_short_wordy_names_not_false_kept(tmp_path) -> None:
    # reversed/spurious: a short single-word CATEGORY column must NOT be accepted as the code column
    import duckdb

    from labrat.db.duckdb_engine import DuckDBConnection

    p = str(tmp_path / "cat.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(category VARCHAR, description VARCHAR)")
    raw.execute(
        "INSERT INTO t VALUES ('Alpha','the alpha group'),('Beta','the beta group'),"
        "('Gamma','the gamma group')"
    )
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False)
    conn.connect()
    # claim: category is codes (WRONG — they're short words with no digits) → must DROP
    assert (
        verify_role_claim(conn, RoleClaim(table="t", code_col="category", name_col="description"))
        is False
    )
    conn.disconnect()


def test_role_claim_rejects_non_identifier(tmp_path) -> None:
    import duckdb

    from labrat.db.duckdb_engine import DuckDBConnection

    p = str(tmp_path / "id.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(a INT)")
    raw.execute("INSERT INTO t VALUES (1)")
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False)
    conn.connect()
    assert (
        verify_role_claim(conn, RoleClaim(table="t; DROP TABLE t", code_col="a", name_col="a"))
        is False
    )
    conn.disconnect()
