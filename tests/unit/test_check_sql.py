# tests/unit/test_check_sql.py
from __future__ import annotations

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.check_sql import CheckSqlTool
from labrat.db.catalog import Catalog, Column, Schema, Table


def _catalog() -> Catalog:
    orders = Table(
        name="orders",
        schema_name="main",
        columns=[
            Column(name="id", data_type="INTEGER", nullable=False),
            Column(name="total", data_type="DOUBLE", nullable=True),
            Column(name="customer_id", data_type="INTEGER", nullable=True),
        ],
    )
    customers = Table(
        name="customers",
        schema_name="main",
        columns=[
            Column(name="id", data_type="INTEGER", nullable=False),
            Column(name="name", data_type="VARCHAR", nullable=True),
        ],
    )
    return Catalog(database_name="main", schemas=[Schema(name="main", tables=[orders, customers])])


def _ctx() -> ToolContext:
    return ToolContext(
        connections={"main": object()}, catalogs={"main": _catalog()}, primary="main"
    )


async def test_clean_sql_is_valid() -> None:
    out = await CheckSqlTool().execute(
        _ctx(),
        CheckSqlTool().input_model(
            sql="SELECT o.total FROM orders o JOIN customers c ON o.customer_id = c.id"
        ),
    )
    assert out.valid and not out.unknown_tables and not out.unknown_columns


async def test_typo_column_flagged_with_suggestion() -> None:
    out = await CheckSqlTool().execute(
        _ctx(), CheckSqlTool().input_model(sql="SELECT totl FROM orders")
    )
    assert not out.valid
    cols = {u.ref: u.suggestions for u in out.unknown_columns}
    assert "totl" in cols and "total" in cols["totl"]


async def test_unknown_table_flagged() -> None:
    out = await CheckSqlTool().execute(
        _ctx(), CheckSqlTool().input_model(sql="SELECT * FROM ordrs")
    )
    assert not out.valid
    assert any(u.ref == "ordrs" and "orders" in u.suggestions for u in out.unknown_tables)


async def test_ambiguous_unqualified_column_not_flagged() -> None:
    # 'id' exists in both orders and customers; unqualified -> ambiguous -> don't flag
    out = await CheckSqlTool().execute(
        _ctx(),
        CheckSqlTool().input_model(
            sql="SELECT id FROM orders JOIN customers ON orders.customer_id = customers.id"
        ),
    )
    assert not any(u.ref == "id" for u in out.unknown_columns)


async def test_malformed_sql_returns_parse_error_not_raise() -> None:
    out = await CheckSqlTool().execute(_ctx(), CheckSqlTool().input_model(sql="SELECT FROM WHERE"))
    assert out.valid is False and out.parse_error is not None


async def test_cte_reference_not_flagged() -> None:
    out = await CheckSqlTool().execute(
        _ctx(),
        CheckSqlTool().input_model(
            sql="WITH recent AS (SELECT id, total FROM orders) SELECT id, total FROM recent"
        ),
    )
    assert out.valid, (out.unknown_tables, out.unknown_columns)


async def test_select_list_alias_referenced_in_order_by_not_flagged() -> None:
    out = await CheckSqlTool().execute(
        _ctx(),
        CheckSqlTool().input_model(
            sql="SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id ORDER BY n"
        ),
    )
    assert out.valid, (out.unknown_tables, out.unknown_columns)


async def test_window_alias_referenced_in_qualify_not_flagged() -> None:
    out = await CheckSqlTool().execute(
        _ctx(),
        CheckSqlTool().input_model(
            sql=("SELECT total, RANK() OVER (ORDER BY total) AS rnk FROM orders QUALIFY rnk <= 3")
        ),
    )
    assert out.valid, (out.unknown_tables, out.unknown_columns)


async def test_subquery_projected_alias_not_flagged() -> None:
    out = await CheckSqlTool().execute(
        _ctx(),
        CheckSqlTool().input_model(sql="SELECT x FROM (SELECT id AS x FROM orders) s"),
    )
    assert out.valid, (out.unknown_tables, out.unknown_columns)


async def test_base_column_typo_still_flagged_alongside_aliases() -> None:
    # regression guard: alias fail-open must not swallow a real base-column typo
    out = await CheckSqlTool().execute(
        _ctx(), CheckSqlTool().input_model(sql="SELECT totl FROM orders")
    )
    assert not out.valid
    cols = {u.ref: u.suggestions for u in out.unknown_columns}
    assert "totl" in cols and "total" in cols["totl"]
