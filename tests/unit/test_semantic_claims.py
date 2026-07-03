# tests/unit/test_semantic_claims.py
from __future__ import annotations

import duckdb

from labrat.agent.tools.base import ToolContext
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.semantic_claims import (
    JoinClaim,
    RoleClaim,
    parse_semantic_claims,
    verify_role_claim,
    verify_semantic_claims,
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


async def test_verify_keeps_survivors_drops_bogus(tmp_path) -> None:
    p = str(tmp_path / "j.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE customers(id INT, name VARCHAR)")
    raw.execute("INSERT INTO customers VALUES (1,'a'),(2,'b')")
    raw.execute("CREATE TABLE orders(customer_id INT, amt INT)")
    raw.execute("INSERT INTO orders VALUES (1,10),(2,20)")
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False)
    conn.connect()
    ctx = ToolContext(connection=conn, catalog=conn.introspect_catalog(), primary="main")
    claims = [
        JoinClaim(
            left_table="orders", left_col="customer_id", right_table="customers", right_col="id"
        ),  # real
        JoinClaim(
            left_table="orders", left_col="amt", right_table="customers", right_col="id"
        ),  # bogus
    ]
    section = await verify_semantic_claims(claims, ctx, database="main")
    assert section is not None and section.source == "verified"
    assert "orders.customer_id" in section.body and "customers.id" in section.body
    assert "orders.amt" not in section.body  # bogus join dropped
    conn.disconnect()


async def test_verify_role_survivor_renders_bullet(tmp_path) -> None:
    conn = _clinical_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=conn.introspect_catalog(), primary="main")
    section = await verify_semantic_claims(
        [RoleClaim(table="clinical", code_col="icd_o_3_histology", name_col="histological_type")],
        ctx,
        database="main",
    )
    assert section is not None and section.source == "verified"
    # the pancancer fix: the bullet must name both columns and advise using the code column
    assert "icd_o_3_histology" in section.body and "histological_type" in section.body
    conn.disconnect()


async def test_verify_no_survivors_returns_none(tmp_path) -> None:
    p = str(tmp_path / "n.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(a INT, b INT)")
    raw.execute("INSERT INTO t VALUES (1,999)")
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False)
    conn.connect()
    ctx = ToolContext(connection=conn, catalog=conn.introspect_catalog(), primary="main")
    bogus = [JoinClaim(left_table="t", left_col="a", right_table="t", right_col="b")]
    assert await verify_semantic_claims(bogus, ctx, database="main") is None
    conn.disconnect()


# --- FIX 1 (IMPORTANT-1 + IMPORTANT-2) regression tests -----------------------------


def _sized_products_conn(tmp_path):
    p = str(tmp_path / "products.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE products(size_code VARCHAR, size_label VARCHAR)")
    raw.execute(
        "INSERT INTO products VALUES "
        "('S','Size 5.5 (US women)'),('M','Size 6.0 (US women)'),('L','Size 7.5 (US women)')"
    )
    raw.close()
    c = DuckDBConnection(path=p, read_only=False)
    c.connect()
    return c


def test_role_claim_reversed_free_text_no_longer_survives(tmp_path) -> None:
    # Fable's reversed-claim fixture (load-bearing): free-text size_label used to score
    # code-shaped under the old unanchored regex, letting the REVERSED claim survive.
    conn = _sized_products_conn(tmp_path)
    assert (
        verify_role_claim(
            conn, RoleClaim(table="products", code_col="size_label", name_col="size_code")
        )
        is False
    )  # reversed claim: must now drop (the bug)
    assert (
        verify_role_claim(
            conn, RoleClaim(table="products", code_col="size_code", name_col="size_label")
        )
        is False
    )  # correct direction also drops: pure-alpha S/M/L scores 0.0 (accepted false-drop)
    conn.disconnect()


def test_role_claim_genuine_digit_bearing_code_still_survives(tmp_path) -> None:
    p = str(tmp_path / "dx.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE dx(code VARCHAR, label VARCHAR)")
    raw.execute(
        "INSERT INTO dx VALUES "
        "('9400/3','Glioblastoma'),('8140/3','Adenocarcinoma'),('8500/2','Ductal carcinoma')"
    )
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False)
    conn.connect()
    assert verify_role_claim(conn, RoleClaim(table="dx", code_col="code", name_col="label")) is True
    assert (
        verify_role_claim(conn, RoleClaim(table="dx", code_col="label", name_col="code")) is False
    )
    conn.disconnect()


def test_role_claim_name_ceiling_drops_when_both_sides_code_shaped(tmp_path) -> None:
    # IMPORTANT-2: name side itself scores >= _NAME_CEILING code-shaped → direction ambiguous.
    p = str(tmp_path / "codes.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(num_code VARCHAR, alt_code VARCHAR)")
    raw.execute("INSERT INTO t VALUES ('840','124'),('826','276'),('392','392')")
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False)
    conn.connect()
    assert (
        verify_role_claim(conn, RoleClaim(table="t", code_col="num_code", name_col="alt_code"))
        is False
    )
    conn.disconnect()
