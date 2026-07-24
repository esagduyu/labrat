"""Tests for describe_table's column-disambiguation hints (DAB autopsy lever D).

describe_table already scopes to a single table, so unlike link_schema it may take
ONE small bounded sample (reusing ``Connection.sample_table``, same pattern as
``profile_dataset``) to additionally catch hierarchy-shaped *values* (e.g. CPC codes
like "A01" -> "A01B" -> "A01B1") that no naming convention would reveal. Sampling
failures (no connection registered) degrade silently to name-only code/name pairing
(hierarchy has no name-only fallback at all — see P1 fix below).

Heuristics:

1. code/name pairs — a code-like column (name carries a QUALIFIED "code"/"cd" marker,
   e.g. "icd_o_code" — bare "id"/"no" do NOT count) paired with a name-like sibling,
   PLUS a value-based fallback: one column's sampled values are short alphanumeric
   codes while a sibling's are prose.
2. hierarchy/level columns — VALUE-BASED ONLY: sampled values where one value is a
   proper prefix of another (nested-code shape). No name-only signal (P1 fix).

Plain tables get ``column_hints == []`` and are otherwise byte-identical to the
pre-fix output.

P1 FIX (2026-07): a Fable whole-branch review found the ORIGINAL heuristics firing on
ubiquitous NORMAL tables (bare "id"/"no" treated as domain-code markers; a name-only
hierarchy signal on any group/tier/parent column). The false-positive tests below
assert the fixed byte-identical-on-normal-tables behavior; the true-positive tests
confirm the genuine lever-D shapes (pancancer-style icd_o_code+histology_name,
patents-style CPC nested-prefix values) still fire.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.describe_table import DescribeTableTool, _Output
from labrat.db.duckdb_engine import DuckDBConnection


def _ctx(tmp_path: Path, name: str, ddl: str, inserts: str) -> ToolContext:
    p = tmp_path / f"describe_hints_{name}.duckdb"
    con = duckdb.connect(str(p))
    con.execute(ddl)
    con.execute(inserts)
    con.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    return ToolContext(connection=conn, catalog=catalog)  # type: ignore[arg-type]


async def test_code_name_pair_hint_names_the_code_column(tmp_path: Path) -> None:
    """Mirrors pancancer_atlas:1 — icd_o_code (qualified code marker) vs histology_name (label)."""
    ctx = _ctx(
        tmp_path,
        "histology",
        "CREATE TABLE histology (icd_o_code VARCHAR, histology_name VARCHAR)",
        "INSERT INTO histology VALUES "
        "('9382/3','Astrocytoma'),('8000/0','Neoplasm NOS'),('8140/3','Adenocarcinoma')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="histology"))
    assert any(
        "icd_o_code" in h and "histology_name" in h and "code-like" in h for h in out.column_hints
    ), out.column_hints


async def test_hierarchy_hint_from_value_pattern(tmp_path: Path) -> None:
    """Mirrors patents:3 — a plain 'cpc_code' column whose *values* nest (A01 -> A01B -> A01B1).

    No level-suggestive naming here (deliberately, to isolate the value-based path);
    the hint must fire from the sampled values alone.
    """
    ctx = _ctx(
        tmp_path,
        "cpc",
        "CREATE TABLE cpc (cpc_code VARCHAR, patent_count INTEGER)",
        "INSERT INTO cpc VALUES ('A01',10),('A01B',4),('A01B1',1)",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="cpc"))
    assert any("cpc_code" in h and "hierarchical" in h for h in out.column_hints), out.column_hints


async def test_plain_table_has_no_hints_and_is_byte_identical(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        "orders",
        "CREATE TABLE orders (id INTEGER, total_amount DOUBLE, status VARCHAR)",
        "INSERT INTO orders VALUES (1,100.0,'complete'),(2,200.0,'pending'),(3,150.0,'cancelled')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="orders"))

    expected = _Output(
        table_name=out.table_name,
        schema_name=out.schema_name,
        columns=out.columns,
        foreign_keys=out.foreign_keys,
        row_count=out.row_count,
        column_hints=[],
    )
    assert out == expected


async def test_value_based_code_name_fallback_when_naming_gives_no_signal(tmp_path: Path) -> None:
    """Neither column name matches a code/name token — only the sampled VALUES do.

    short_form's values are short + contain a digit (code-like); full_text's values
    contain spaces (prose-like). Naming alone (no code/id/cd/no or name/title/label/
    desc tokens) would miss this pair entirely.
    """
    ctx = _ctx(
        tmp_path,
        "value_fallback",
        "CREATE TABLE value_fallback (short_form VARCHAR, full_text VARCHAR)",
        "INSERT INTO value_fallback VALUES "
        "('A1','Category One Description'),('B2','Category Two Description'),"
        "('C3','Category Three Description')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="value_fallback"))
    assert any(
        "short_form" in h and "full_text" in h and "values" in h for h in out.column_hints
    ), out.column_hints


async def test_no_connection_falls_back_to_name_only_heuristics(tmp_path: Path) -> None:
    """When ctx has no live connection for the db, value sampling is skipped silently."""
    p = tmp_path / "catalog_only.duckdb"
    con = duckdb.connect(str(p))
    con.execute("CREATE TABLE histology (icd_o_code VARCHAR, histology_name VARCHAR)")
    con.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    conn.disconnect()

    # No `connection=` — connections dict is empty, so sample_table can't be reached.
    ctx = ToolContext(catalog=catalog)  # type: ignore[arg-type]
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="histology"))
    # Name-based pairing still fires even without a live connection.
    assert any("icd_o_code" in h and "code-like" in h for h in out.column_hints)


def test_values_look_hierarchical_direct() -> None:
    from labrat.agent.tools.describe_table import _values_look_hierarchical

    assert _values_look_hierarchical(["A01", "A01B", "A01B1"]) is True
    assert _values_look_hierarchical(["complete", "pending", "cancelled"]) is False
    assert _values_look_hierarchical([]) is False
    assert _values_look_hierarchical(["only-one"]) is False


def test_values_code_like_direct() -> None:
    """A bare integer PK column (1/2/3) must NOT count as code-like values (P1 fix) —
    it would otherwise pair with ANY prose sibling column on any table with an int PK.
    Real domain codes ("9382/3", "A1") still count.
    """
    from labrat.agent.tools.describe_table import _values_code_like

    assert _values_code_like(["1", "2", "3"]) is False
    assert _values_code_like(["9382/3", "8000/0", "8140/3"]) is True
    assert _values_code_like(["A1", "B2", "C3"]) is True
    assert _values_code_like(["only-one"]) is False


@pytest.mark.parametrize("run", [1])
def test_registry_still_lists_describe_table(run: int) -> None:
    from labrat.agent.data_tools import build_data_tools_registry

    registry = build_data_tools_registry()
    assert "describe_table" in {t.name for t in registry.tools}


# --- P1 false-positive regression tests -------------------------------------------
# Every case below MUST produce column_hints == [] — these are all ordinary tables
# that the pre-fix heuristics wrongly flagged (either via name-only code/name pairing
# treating bare "id" as a domain code, or via name-only hierarchy detection).


async def test_no_hint_on_bare_id_and_name_table(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        "customers",
        "CREATE TABLE customers (id INTEGER, name VARCHAR)",
        "INSERT INTO customers VALUES (1,'Acme Corp'),(2,'Globex Inc'),(3,'Initech LLC')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="customers"))
    assert out.column_hints == []


async def test_no_hint_on_generic_surrogate_id_and_name_pair(tmp_path: Path) -> None:
    """products(product_id, product_name) shares a lexical stem ("product") just like a
    genuine code/name pair would — proving the fix can't rely on stem-sharing alone,
    it must also exclude "id" as a domain-code marker.
    """
    ctx = _ctx(
        tmp_path,
        "products",
        "CREATE TABLE products (product_id INTEGER, product_name VARCHAR)",
        "INSERT INTO products VALUES (1,'Widget'),(2,'Gadget'),(3,'Gizmo')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="products"))
    assert out.column_hints == []


async def test_no_hint_on_users_id_full_name(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        "users",
        "CREATE TABLE users (id INTEGER, full_name VARCHAR)",
        "INSERT INTO users VALUES (1,'Ada Lovelace'),(2,'Alan Turing'),(3,'Grace Hopper')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="users"))
    assert out.column_hints == []


async def test_no_hint_on_reviews_review_id_business_name(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        "reviews",
        "CREATE TABLE reviews (review_id INTEGER, business_name VARCHAR)",
        "INSERT INTO reviews VALUES (1,'Joes Diner'),(2,'Marios Pizzeria'),(3,'Lees Bakery')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="reviews"))
    assert out.column_hints == []


async def test_no_hint_on_age_group_column(tmp_path: Path) -> None:
    """A demographic 'age_group' column with plain non-nesting values must not fire."""
    ctx = _ctx(
        tmp_path,
        "respondents",
        "CREATE TABLE respondents (id INTEGER, age_group VARCHAR)",
        "INSERT INTO respondents VALUES (1,'18-25'),(2,'26-40'),(3,'41-60')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="respondents"))
    assert out.column_hints == []


async def test_no_hint_on_user_group_column(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        "accounts",
        "CREATE TABLE accounts (id INTEGER, user_group VARCHAR)",
        "INSERT INTO accounts VALUES (1,'trial'),(2,'standard'),(3,'enterprise')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="accounts"))
    assert out.column_hints == []


async def test_no_hint_on_parent_id_column(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        "categories",
        "CREATE TABLE categories (id INTEGER, parent_id INTEGER)",
        "INSERT INTO categories VALUES (1,NULL),(2,1),(3,1)",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="categories"))
    assert out.column_hints == []


async def test_no_hint_on_account_tier_column(tmp_path: Path) -> None:
    ctx = _ctx(
        tmp_path,
        "accounts2",
        "CREATE TABLE accounts2 (id INTEGER, account_tier VARCHAR)",
        "INSERT INTO accounts2 VALUES (1,'gold'),(2,'silver'),(3,'bronze')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="accounts2"))
    assert out.column_hints == []


async def test_no_hierarchy_hint_from_subgroup_name_alone_without_nesting_values(
    tmp_path: Path,
) -> None:
    """Was a TRUE positive pre-fix via name alone (non-nesting values 'Mammal'/'Bird') —
    now dropped: hierarchy fires ONLY when the sampled VALUES actually nest.
    """
    ctx = _ctx(
        tmp_path,
        "taxonomy",
        "CREATE TABLE taxonomy (id INTEGER, subgroup VARCHAR)",
        "INSERT INTO taxonomy VALUES (1,'Mammal'),(2,'Bird')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="taxonomy"))
    assert out.column_hints == []


async def test_no_hierarchy_hint_from_numeric_prefix_values(tmp_path: Path) -> None:
    """A purely numeric column whose values happen to string-prefix each other (1/10)
    is NOT a hierarchy signal — plain ids/counts, not a nested classification code.
    """
    ctx = _ctx(
        tmp_path,
        "ranked",
        "CREATE TABLE ranked (id INTEGER, rank_value VARCHAR)",
        "INSERT INTO ranked VALUES (1,'1'),(2,'10'),(3,'100')",
    )
    tool = DescribeTableTool()
    out = await tool.execute(ctx, tool.input_model(table="ranked"))
    assert out.column_hints == []
