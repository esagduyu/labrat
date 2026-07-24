"""Tests for link_schema's column-disambiguation hints (DAB autopsy lever D).

Deterministic, name-based-only heuristics (link_schema never adds a DB round-trip):

1. code/name pairs — a code-like column (name carries a QUALIFIED "code"/"cd" marker,
   e.g. ``icd_o_code``, ``region_code`` — bare ``id``/``no`` do NOT count, see
   ``_is_code_like``) paired with a name-like sibling (name/title/label/desc) gets a
   hint pointing at the CODE column.
2. hierarchy/level columns — DROPPED entirely from this module (P1 fix, see below).
   Name-alone can't safely tell "genuine nested classification code" apart from an
   ordinary "parent_id"/"level"/"tier" column on a normal table; describe_table.py
   keeps a value-based nested-prefix check instead (real CPC/ICD-style codes whose
   *values* nest — a signal name-alone can't fake).

Plain tables (no code/name pair) must get an empty ``column_hints`` list — i.e. the
match is byte-identical to pre-fix output plus the new (empty) field.

P1 FIX (2026-07): a Fable whole-branch review found the ORIGINAL heuristic firing on
ubiquitous NORMAL tables — customers(id,name), products(product_id,product_name),
users(id,full_name), reviews(review_id,business_name) all wrongly emitted the
code/label hint (bare "id" was treated as a domain-code marker, and a "no-stem
single-pair fallback" fired on any lone id+name pair). Similarly age_group/
user_group/parent_id/account_tier all wrongly emitted a hierarchy hint. This file's
false-positive tests below assert the fixed byte-identical-on-normal-tables behavior;
the true-positive tests confirm the genuine lever-D shapes (pancancer-style
icd_o_code+histology_name) still fire.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.link_schema import LinkSchemaTool, _code_name_hints, _Match
from labrat.db.duckdb_engine import DuckDBConnection


class _Col:
    def __init__(self, name: str) -> None:
        self.name = name


def _ctx(tmp_path: Path, ddl: str) -> ToolContext:
    p = tmp_path / "link_schema_hints.duckdb"
    con = duckdb.connect(str(p))
    con.execute(ddl)
    con.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    return ToolContext(connection=conn, catalog=catalog)  # type: ignore[arg-type]


async def test_code_name_pair_hint_names_the_code_column(tmp_path: Path) -> None:
    """A code/label pair (icd_o_code vs histology_name) is flagged, naming the CODE column.

    Mirrors the pancancer_atlas:1 failure — the agent grouped by the histology
    *name* ("Astrocytoma") when the validator wanted the ICD-O *code* ("9382/3").
    icd_o_code carries a qualified "code" marker (qualifier {"icd","o"}) so it still
    fires post-P1-fix even though it shares no stem at all with histology_name.
    """
    ctx = _ctx(
        tmp_path,
        "CREATE TABLE histology (icd_o_code VARCHAR, histology_name VARCHAR, case_count INTEGER)",
    )
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="unrelated database schema info"))
    match = next(m for m in out.tables if m.table == "histology")
    assert any(
        "icd_o_code" in h and "histology_name" in h and "code-like" in h and "name-like" in h
        for h in match.column_hints
    ), match.column_hints


async def test_plain_table_has_no_hints_and_is_byte_identical(tmp_path: Path) -> None:
    """A table with no code/name pair gets column_hints == []."""
    ctx = _ctx(
        tmp_path,
        "CREATE TABLE orders (id INTEGER, total_amount DOUBLE, status VARCHAR)",
    )
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="total amount by status"))
    match = next(m for m in out.tables if m.table == "orders")

    expected = _Match(
        table="orders",
        schema_name=match.schema_name,
        score=match.score,
        matched_terms=match.matched_terms,
        columns=match.columns,
        column_hints=[],
    )
    assert match == expected


def test_no_false_positive_on_bare_id_without_name_sibling(tmp_path: Path) -> None:
    """A code-like column with no name-like sibling in the table must not fire."""
    assert _code_name_hints([_Col("id"), _Col("total_amount")]) == []


# --- P1 false-positive regression tests -------------------------------------------
# Every case below MUST produce column_hints == [] — these are all ordinary tables
# that the pre-fix heuristic wrongly flagged.


async def test_no_hint_on_bare_id_and_name_table(tmp_path: Path) -> None:
    """customers(id, name) — the classic bare surrogate PK + generic name. Must NOT fire."""
    ctx = _ctx(tmp_path, "CREATE TABLE customers (id INTEGER, name VARCHAR)")
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="list customers"))
    match = next(m for m in out.tables if m.table == "customers")
    assert match.column_hints == []


async def test_no_hint_on_generic_surrogate_id_and_name_pair(tmp_path: Path) -> None:
    """products(product_id, product_name) — surrogate key + descriptive name. Must NOT fire.

    Notably product_id/product_name SHARE a lexical stem ("product") just like a
    genuine code/name pair would — proving the fix can't merely rely on stem-sharing,
    it must also exclude "id" as a domain-code marker.
    """
    ctx = _ctx(tmp_path, "CREATE TABLE products (product_id INTEGER, product_name VARCHAR)")
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="list products"))
    match = next(m for m in out.tables if m.table == "products")
    assert match.column_hints == []


async def test_no_hint_on_users_id_full_name(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "CREATE TABLE users (id INTEGER, full_name VARCHAR)")
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="list users"))
    match = next(m for m in out.tables if m.table == "users")
    assert match.column_hints == []


async def test_no_hint_on_reviews_review_id_business_name(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "CREATE TABLE reviews (review_id INTEGER, business_name VARCHAR)")
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="list reviews"))
    match = next(m for m in out.tables if m.table == "reviews")
    assert match.column_hints == []


async def test_no_hint_on_age_group_column(tmp_path: Path) -> None:
    """A demographic 'age_group' column on a normal table must not trigger a hierarchy hint."""
    ctx = _ctx(tmp_path, "CREATE TABLE respondents (id INTEGER, age_group VARCHAR, name VARCHAR)")
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="respondents by age group"))
    match = next(m for m in out.tables if m.table == "respondents")
    assert match.column_hints == []


async def test_no_hint_on_user_group_column(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "CREATE TABLE accounts (id INTEGER, user_group VARCHAR, name VARCHAR)")
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="accounts by user group"))
    match = next(m for m in out.tables if m.table == "accounts")
    assert match.column_hints == []


async def test_no_hint_on_parent_id_column(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "CREATE TABLE categories (id INTEGER, parent_id INTEGER, name VARCHAR)")
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="category parent hierarchy"))
    match = next(m for m in out.tables if m.table == "categories")
    assert match.column_hints == []


async def test_no_hint_on_account_tier_column(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "CREATE TABLE accounts2 (id INTEGER, account_tier VARCHAR, name VARCHAR)")
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="accounts by tier"))
    match = next(m for m in out.tables if m.table == "accounts2")
    assert match.column_hints == []


async def test_no_hint_on_subgroup_column_name_alone(tmp_path: Path) -> None:
    """Was a TRUE positive pre-fix (name-only 'subgroup') — now dropped entirely: a
    hierarchy/level column name with no value evidence (link_schema has no values at
    all) is exactly as unsafe a name-only signal as parent_id/age_group.
    """
    ctx = _ctx(tmp_path, "CREATE TABLE taxonomy (id INTEGER, category VARCHAR, subgroup VARCHAR)")
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="show taxonomy subgroup breakdown"))
    match = next(m for m in out.tables if m.table == "taxonomy")
    assert match.column_hints == []


async def test_no_hint_on_group_name_column(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "CREATE TABLE cohorts (id INTEGER, group_name VARCHAR)")
    tool = LinkSchemaTool()
    out = await tool.execute(ctx, tool.input_model(question="cohorts by group name"))
    match = next(m for m in out.tables if m.table == "cohorts")
    assert match.column_hints == []


@pytest.mark.parametrize(
    ("cols", "expect"),
    [
        (["cpc_code", "title"], True),  # qualified code marker, exactly-one-each fallback
        (["icd_o_code", "histology_name"], True),  # qualified code marker, no shared stem
        (["region_code", "region_name"], True),  # qualified code marker + shared stem
        (["id", "name"], False),  # bare surrogate + generic name — NOT a domain code
        (["product_id", "product_name"], False),  # shares a stem, but "id" isn't a code marker
        (["region_id", "region_name"], False),  # same shape as product_id/product_name
        (["review_id", "business_name"], False),  # qualifier present but marker is "id"
    ],
)
def test_code_name_hints_direct(cols: list[str], expect: bool) -> None:
    hints = _code_name_hints([_Col(c) for c in cols])
    assert bool(hints) == expect
