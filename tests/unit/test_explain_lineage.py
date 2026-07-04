"""Unit C: explain_lineage tool — sqlglot lineage against the Catalog, parse-only."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage

from labrat.agent.tools.explain_lineage import (
    _catalog_schema_dict,
    _flatten,
    _output_columns,
)
from labrat.db.catalog import Catalog, Column, Schema, Table

_CAT = Catalog(
    database_name="shop",
    schemas=[
        Schema(
            name="main",
            tables=[
                Table(
                    name="orders",
                    schema_name="main",
                    columns=[
                        Column(name="id", data_type="INTEGER", nullable=False),
                        Column(name="customer_id", data_type="INTEGER", nullable=True),
                        Column(name="amount", data_type="DOUBLE", nullable=True),
                    ],
                ),
                Table(
                    name="customers",
                    schema_name="main",
                    columns=[
                        Column(name="id", data_type="INTEGER", nullable=False),
                        Column(name="name", data_type="VARCHAR", nullable=True),
                    ],
                ),
            ],
        )
    ],
)

_JOIN_SQL = (
    "SELECT c.name AS customer_name, SUM(o.amount) AS total_spend "
    "FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name"
)


def test_catalog_schema_dict_shape() -> None:
    schema = _catalog_schema_dict(_CAT)
    assert schema == {
        "orders": {"id": "INTEGER", "customer_id": "INTEGER", "amount": "DOUBLE"},
        "customers": {"id": "INTEGER", "name": "VARCHAR"},
    }


def test_flatten_resolves_alias_to_real_table() -> None:
    schema = _catalog_schema_dict(_CAT)
    node = lineage("total_spend", _JOIN_SQL, schema=schema)
    refs = _flatten(node)
    assert [(r.table, r.column) for r in refs] == [("orders", "amount")]


def test_flatten_strips_quoted_identifiers() -> None:
    schema = _catalog_schema_dict(_CAT)
    node = lineage("customer_name", _JOIN_SQL, schema=schema)
    refs = _flatten(node)
    assert [(r.table, r.column) for r in refs] == [("customers", "name")]


def test_flatten_literal_projection_yields_no_sources() -> None:
    schema = _catalog_schema_dict(_CAT)
    node = lineage("k", "SELECT 1 AS k FROM customers", schema=schema)
    assert _flatten(node) == []


def test_flatten_traces_through_cte() -> None:
    schema = _catalog_schema_dict(_CAT)
    sql = (
        "WITH big AS (SELECT customer_id, SUM(amount) AS spend FROM orders GROUP BY customer_id) "
        "SELECT c.name, b.spend AS total FROM big b JOIN customers c ON b.customer_id = c.id"
    )
    node = lineage("total", sql, schema=schema)
    assert [(r.table, r.column) for r in _flatten(node)] == [("orders", "amount")]


def test_output_columns_named_projections() -> None:
    schema = _catalog_schema_dict(_CAT)
    tree = sqlglot.parse_one(_JOIN_SQL)
    assert isinstance(tree, exp.Query)
    assert _output_columns(tree, schema) == ["customer_name", "total_spend"]


def test_output_columns_expands_star_via_schema() -> None:
    schema = _catalog_schema_dict(_CAT)
    tree = sqlglot.parse_one("SELECT * FROM orders")
    assert isinstance(tree, exp.Query)
    assert _output_columns(tree, schema) == ["id", "customer_id", "amount"]


def test_output_columns_unresolvable_star_skipped() -> None:
    schema = _catalog_schema_dict(_CAT)
    tree = sqlglot.parse_one("SELECT * FROM mystery")
    assert isinstance(tree, exp.Query)
    assert _output_columns(tree, schema) == []
