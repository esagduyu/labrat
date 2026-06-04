"""Tests for the grounding tools (FEATURE_ROADMAP #25): verify_join + link_schema."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.link_schema import LinkSchemaTool
from labrat.agent.tools.verify_join import VerifyJoinTool
from labrat.db.duckdb_engine import DuckDBConnection


@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    p = tmp_path_factory.mktemp("grounding") / "g.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""
        CREATE TABLE users (id INTEGER, name VARCHAR);
        INSERT INTO users VALUES (1,'Alice'),(2,'Bob'),(3,'Carol'),(4,'Dan'),(5,'Eve');
        CREATE TABLE regions (id INTEGER, name VARCHAR);
        INSERT INTO regions VALUES (1,'North'),(2,'South');
        CREATE TABLE orders (id INTEGER, user_id INTEGER, region_id INTEGER, total DOUBLE);
        INSERT INTO orders VALUES
            (1,1,1,100.0),(2,2,1,200.0),(3,3,2,150.0),(4,4,2,300.0),(5,5,1,50.0);
    """)
    con.close()
    return p


@pytest.fixture()
def ctx(fixture_db: Path) -> Iterator[ToolContext]:
    conn = DuckDBConnection(fixture_db, read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    yield ToolContext(connection=conn, catalog=catalog)
    conn.disconnect()


# ── verify_join ──────────────────────────────────────────────────────────────


async def test_verify_join_valid_key_full_match_no_fanout(ctx: ToolContext) -> None:
    """orders.user_id → users.id: every order matches a user, and users.id is unique."""
    tool = VerifyJoinTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            left_table="orders", left_column="user_id", right_table="users", right_column="id"
        ),
    )
    assert out.left_rows == 5
    assert out.matched_rows == 5
    assert out.match_rate == 1.0
    assert out.max_right_rows_per_key == 1  # users.id unique → no fan-out
    assert out.likely_valid is True


async def test_verify_join_wrong_key_low_match(ctx: ToolContext) -> None:
    """orders.user_id → regions.id: only user_ids 1,2 exist in regions → 2/5 match."""
    tool = VerifyJoinTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            left_table="orders", left_column="user_id", right_table="regions", right_column="id"
        ),
    )
    assert out.matched_rows == 2
    assert out.match_rate == pytest.approx(0.4)
    assert out.likely_valid is False
    assert "match" in out.verdict.lower()


async def test_verify_join_detects_fanout(ctx: ToolContext) -> None:
    """regions.id → orders.region_id: region 1 has 3 orders, region 2 has 2 → fan-out > 1."""
    tool = VerifyJoinTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            left_table="regions", left_column="id", right_table="orders", right_column="region_id"
        ),
    )
    assert out.max_right_rows_per_key == 3  # region 1 → 3 orders
    assert "fan" in out.verdict.lower() or "fan-out" in out.verdict.lower()


# ── link_schema ──────────────────────────────────────────────────────────────


async def test_link_schema_ranks_relevant_tables_first(ctx: ToolContext) -> None:
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="which users placed the most orders?"))
    ranked = [m.table for m in out.tables]
    assert ranked[:2].count("orders") + ranked[:2].count("users") == 2  # both in top 2
    assert "regions" not in ranked  # no lexical overlap → score 0, dropped


async def test_link_schema_returns_columns_and_matched_terms(ctx: ToolContext) -> None:
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="list all regions"))
    regions = next(m for m in out.tables if m.table == "regions")
    assert "name" in regions.columns and "id" in regions.columns
    assert any("region" in t for t in regions.matched_terms)
