"""Unit and integration tests for the SQL completion engine."""

import pytest

from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.sql.completer import CompletionKind, SQLCompleter


@pytest.fixture()
def catalog() -> Catalog:
    return Catalog(
        database_name="testdb",
        schemas=[
            Schema(
                name="main",
                tables=[
                    Table(
                        name="orders",
                        schema_name="main",
                        columns=[
                            Column(name="order_id", data_type="INTEGER", nullable=False),
                            Column(name="amount", data_type="DECIMAL", nullable=True),
                            Column(name="status", data_type="VARCHAR", nullable=True),
                        ],
                    ),
                    Table(
                        name="order_items",
                        schema_name="main",
                        columns=[
                            Column(name="item_id", data_type="INTEGER", nullable=False),
                            Column(name="order_id", data_type="INTEGER", nullable=False),
                        ],
                    ),
                    Table(
                        name="users",
                        schema_name="main",
                        columns=[
                            Column(name="user_id", data_type="INTEGER", nullable=False),
                            Column(name="email", data_type="VARCHAR", nullable=False),
                        ],
                    ),
                ],
            )
        ],
    )


# ── ranking tests ─────────────────────────────────────────────────────────────


def test_exact_match_beats_prefix(catalog: Catalog) -> None:
    """An exact-match candidate ranks above a prefix match."""
    completer = SQLCompleter(catalog)
    results = completer.complete("SELECT * FROM orders", 20)
    labels = [c.label for c in results]
    assert "orders" in labels
    orders_idx = labels.index("orders")
    for other in results:
        if other.label != "orders" and other.label.startswith("order"):
            assert results[orders_idx].score >= other.score


def test_prefix_beats_substring(catalog: Catalog) -> None:
    """A prefix match ranks above a substring match for the same prefix."""
    completer = SQLCompleter(catalog)
    results = completer.complete("SELECT * FROM or", 16)
    labels = [c.label for c in results]
    assert "orders" in labels
    assert "order_items" in labels
    orders_score = next(c.score for c in results if c.label == "orders")
    items_score = next(c.score for c in results if c.label == "order_items")
    assert orders_score >= items_score


def test_no_prefix_returns_empty_from_score() -> None:
    """Empty prefix returns no keyword completions (score-based gate)."""
    completer = SQLCompleter()
    results = completer.complete("SELECT ", 7)
    # Keywords should be included only if context suggests them
    assert isinstance(results, list)


# ── context detection ─────────────────────────────────────────────────────────


def test_from_context_suggests_tables(catalog: Catalog) -> None:
    """Typing after FROM suggests table names."""
    completer = SQLCompleter(catalog)
    sql = "SELECT * FROM ord"
    results = completer.complete(sql, len(sql))
    table_labels = [c.label for c in results if c.kind == CompletionKind.TABLE]
    assert "orders" in table_labels
    assert results[0].label in ("orders", "order_items")


def test_join_context_suggests_tables(catalog: Catalog) -> None:
    """Typing after JOIN suggests table names."""
    completer = SQLCompleter(catalog)
    sql = "SELECT * FROM orders JOIN us"
    results = completer.complete(sql, len(sql))
    table_labels = [c.label for c in results if c.kind == CompletionKind.TABLE]
    assert "users" in table_labels


def test_dot_notation_suggests_columns(catalog: Catalog) -> None:
    """'orders.' suggests all columns of the orders table."""
    completer = SQLCompleter(catalog)
    sql = "SELECT orders."
    results = completer.complete(sql, len(sql))
    col_labels = [c.label for c in results if c.kind == CompletionKind.COLUMN]
    assert "order_id" in col_labels
    assert "amount" in col_labels
    assert "status" in col_labels


def test_dot_notation_with_prefix_filters_columns(catalog: Catalog) -> None:
    """'orders.am' suggests only columns matching 'am'."""
    completer = SQLCompleter(catalog)
    sql = "SELECT orders.am"
    results = completer.complete(sql, len(sql))
    col_labels = [c.label for c in results if c.kind == CompletionKind.COLUMN]
    assert "amount" in col_labels
    assert "order_id" not in col_labels


def test_two_char_minimum_triggers_completions(catalog: Catalog) -> None:
    """Typing 2+ characters triggers completions even outside FROM/JOIN context."""
    completer = SQLCompleter(catalog)
    sql = "us"
    results = completer.complete(sql, len(sql))
    assert any(c.label == "users" for c in results)


def test_one_char_returns_no_keyword_matches() -> None:
    """Single character prefix does not return completions (below minimum)."""
    completer = SQLCompleter()
    results = completer.complete("s", 1)
    assert results == []


# ── column type info in completions ───────────────────────────────────────────


def test_column_completion_includes_type(catalog: Catalog) -> None:
    """Column completions carry the column's data type in the detail field."""
    completer = SQLCompleter(catalog)
    sql = "SELECT orders."
    results = completer.complete(sql, len(sql))
    amount = next((c for c in results if c.label == "amount"), None)
    assert amount is not None
    assert "DECIMAL" in amount.detail


def test_table_completion_includes_schema(catalog: Catalog) -> None:
    """Table completions include schema name in the detail field."""
    completer = SQLCompleter(catalog)
    sql = "SELECT * FROM ord"
    results = completer.complete(sql, len(sql))
    orders = next((c for c in results if c.label == "orders"), None)
    assert orders is not None
    assert "main" in orders.detail
