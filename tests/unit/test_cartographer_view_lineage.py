"""Unit D: lineage source token + build_view_lineage + generate_scent wiring + audit."""

from __future__ import annotations

from pathlib import Path

import duckdb

from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import build_view_lineage, generate_scent
from labrat.maze.document import ScentDoc, Section, parse_document, render_document


def test_lineage_source_token_round_trips() -> None:
    doc = ScentDoc(
        domain="shop",
        sections=[
            Section(
                heading="View Lineage",
                body="- view `customer_spend`.`total` ← `orders`.`amount`",
                source="lineage",
            )
        ],
    )
    rendered = render_document(doc)
    assert "**Source:** lineage" in rendered
    reparsed = parse_document(rendered, domain="shop")
    section = next(s for s in reparsed.sections if s.heading == "View Lineage")
    assert section.source == "lineage"


def test_unknown_source_token_still_falls_back_to_human() -> None:
    text = "---\ndomain: d\n---\n\n## X\n**Source:** wizardry\n\n- body\n"
    doc = parse_document(text, domain="d")
    assert doc.sections[0].source == "human"


def _view_catalog(tmp_path: Path) -> Catalog:
    p = str(tmp_path / "vl.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE orders(id INTEGER, customer_id INTEGER, amount DOUBLE)")
    raw.execute("CREATE TABLE customers(id INTEGER, name VARCHAR)")
    raw.execute(
        "CREATE VIEW customer_spend AS "
        "SELECT c.name AS customer_name, SUM(o.amount) AS total "
        "FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name"
    )
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        return conn.introspect_catalog()
    finally:
        conn.disconnect()


def test_build_view_lineage_emits_lineage_section(tmp_path: Path) -> None:
    section = build_view_lineage(_view_catalog(tmp_path), database="shop")
    assert section is not None
    assert section.heading == "View Lineage"
    assert section.source == "lineage"
    assert "- view `customer_spend`.`customer_name` ← `customers`.`name`" in section.body
    assert "- view `customer_spend`.`total` ← `orders`.`amount`" in section.body


def test_build_view_lineage_none_when_no_views(tmp_path: Path) -> None:
    p = str(tmp_path / "plain.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE city(id INTEGER, name VARCHAR)")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        cat = conn.introspect_catalog()
    finally:
        conn.disconnect()
    assert build_view_lineage(cat, database="c") is None


def test_build_view_lineage_needs_no_connection_and_skips_unparseable() -> None:
    # GT-firewall by construction: a hand-built Catalog (no live DB anywhere) is
    # sufficient input; an unparseable view definition is skipped fail-soft.
    cat = Catalog(
        database_name="x",
        schemas=[
            Schema(
                name="main",
                tables=[
                    Table(
                        name="base",
                        schema_name="main",
                        columns=[Column(name="a", data_type="INTEGER", nullable=True)],
                    ),
                    Table(
                        name="good_view",
                        schema_name="main",
                        columns=[Column(name="a2", data_type="INTEGER", nullable=True)],
                        view_definition="CREATE VIEW good_view AS SELECT a AS a2 FROM base",
                    ),
                    Table(
                        name="broken_view",
                        schema_name="main",
                        columns=[],
                        view_definition="CREATE VIEW broken_view AS SELEC nope FROM",
                    ),
                ],
            )
        ],
    )
    section = build_view_lineage(cat, database="x")
    assert section is not None
    assert "- view `good_view`.`a2` ← `base`.`a`" in section.body
    assert "broken_view" not in section.body


async def test_generate_scent_includes_lineage_section_for_view_db(tmp_path: Path) -> None:
    p = str(tmp_path / "scent_view.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE orders(id INTEGER, customer_id INTEGER, amount DOUBLE)")
    raw.execute("CREATE TABLE customers(id INTEGER, name VARCHAR)")
    raw.execute("INSERT INTO orders VALUES (1, 1, 10.0), (2, 1, 5.0)")
    raw.execute("INSERT INTO customers VALUES (1, 'Ada')")
    raw.execute(
        "CREATE VIEW customer_spend AS "
        "SELECT c.name AS customer_name, SUM(o.amount) AS total "
        "FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name"
    )
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        docs = await generate_scent(
            connections={"shop": conn},
            catalogs={"shop": conn.introspect_catalog()},
            primary="shop",
            with_semantics=False,
        )
    finally:
        conn.disconnect()
    sections = {s.heading: s for s in docs[0].sections}
    assert "View Lineage" in sections
    assert sections["View Lineage"].source == "lineage"
    assert "`customer_spend`.`total` ← `orders`.`amount`" in sections["View Lineage"].body


async def test_generate_scent_no_views_has_no_lineage_section(tmp_path: Path) -> None:
    # Byte-identity: the builder returns None → nothing appended → deterministic
    # output for a no-views DB is unchanged (mirrors the Code Columns precedent).
    p = str(tmp_path / "scent_plain.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE city(id INTEGER, name VARCHAR)")
    raw.execute("INSERT INTO city VALUES (1, 'London'), (2, 'Paris')")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        docs = await generate_scent(
            connections={"c": conn},
            catalogs={"c": conn.introspect_catalog()},
            primary="c",
            with_semantics=False,
        )
    finally:
        conn.disconnect()
    assert "View Lineage" not in {s.heading for s in docs[0].sections}
    assert all(s.source == "verified" for s in docs[0].sections)
