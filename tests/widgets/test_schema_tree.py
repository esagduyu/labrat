"""Tests for SchemaTree and SchemaBrowser widgets."""

from collections.abc import Callable
from pathlib import Path

import pytest

from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.db.catalog_cache import load_cached_catalog, save_catalog
from labrat.widgets.schema_tree import SchemaBrowser, SchemaTree, _ColNodeData

# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def sample_catalog() -> Catalog:
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


# ── unit: catalog → tree nodes ────────────────────────────────────────────────


def test_schema_tree_has_schema_nodes(sample_catalog: Catalog) -> None:
    """Root has one schema child matching the catalog's single schema."""
    tree = SchemaTree(sample_catalog)
    schema_nodes = list(tree.root.children)
    assert len(schema_nodes) == 1
    assert str(schema_nodes[0].label) == "main"


def test_schema_tree_has_table_nodes(sample_catalog: Catalog) -> None:
    """Each schema node has table children."""
    tree = SchemaTree(sample_catalog)
    schema_node = next(iter(tree.root.children))
    table_names = {str(n.label) for n in schema_node.children}
    assert "orders" in table_names
    assert "users" in table_names


def test_column_node_data(sample_catalog: Catalog) -> None:
    """Column leaf nodes carry _ColNodeData with correct qualified_name."""
    tree = SchemaTree(sample_catalog)
    schema_node = next(iter(tree.root.children))
    orders_node = next(n for n in schema_node.children if "orders" in str(n.label))
    col_nodes = list(orders_node.children)
    assert len(col_nodes) == 2
    data = col_nodes[0].data
    assert isinstance(data, _ColNodeData)
    assert data.qualified_name.startswith("main.orders.")


def test_filter_hides_non_matching_tables(sample_catalog: Catalog) -> None:
    """Filtering to 'order' shows only the orders table."""
    tree = SchemaTree(sample_catalog)
    tree.filter("order")
    schema_node = next(iter(tree.root.children))
    table_labels = {str(n.label) for n in schema_node.children}
    assert "orders" in table_labels
    assert "users" not in table_labels


def test_filter_empty_restores_all(sample_catalog: Catalog) -> None:
    """Clearing the filter restores all tables."""
    tree = SchemaTree(sample_catalog)
    tree.filter("order")
    tree.filter("")
    schema_node = next(iter(tree.root.children))
    assert len(list(schema_node.children)) == 2


def test_populate_replaces_content(sample_catalog: Catalog) -> None:
    """populate() replaces the tree with a new catalog."""
    tree = SchemaTree(sample_catalog)
    new_catalog = Catalog(
        database_name="newdb",
        schemas=[
            Schema(
                name="pub",
                tables=[Table(name="items", schema_name="pub", columns=[])],
            )
        ],
    )
    tree.populate(new_catalog)
    assert str(tree.root.label) == "newdb"
    schema_node = next(iter(tree.root.children))
    assert str(schema_node.label) == "pub"


# ── catalog cache ─────────────────────────────────────────────────────────────


def test_save_and_load_catalog(
    sample_catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save_catalog / load_cached_catalog round-trip."""
    monkeypatch.setattr("labrat.db.catalog_cache._CACHE_DIR", tmp_path)
    save_catalog("test_profile", sample_catalog)
    assert (tmp_path / "test_profile.json").exists()
    loaded = load_cached_catalog("test_profile")
    assert loaded is not None
    assert loaded.database_name == "testdb"
    assert len(loaded.schemas[0].tables) == 2


def test_load_cached_catalog_missing_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_cached_catalog returns None when no cache file exists."""
    monkeypatch.setattr("labrat.db.catalog_cache._CACHE_DIR", tmp_path)
    assert load_cached_catalog("no_such_profile") is None


# ── integration: refresh updates cache ────────────────────────────────────────


def test_refresh_updates_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh via DuckDB connection writes a catalog to the cache."""

    from labrat.db.catalog_cache import save_catalog
    from labrat.db.duckdb_engine import DuckDBConnection

    monkeypatch.setattr("labrat.db.catalog_cache._CACHE_DIR", tmp_path)

    conn = DuckDBConnection(path=":memory:", read_only=False)
    conn.connect()
    raw = conn._connection  # pyright: ignore[reportPrivateUsage]
    assert raw is not None
    raw.execute("CREATE TABLE things (id INTEGER, name VARCHAR)")
    catalog = conn.introspect_catalog()
    save_catalog("refresh_test", catalog)
    conn.disconnect()

    loaded = load_cached_catalog("refresh_test")
    assert loaded is not None
    table_names = {t.name for s in loaded.schemas for t in s.tables}
    assert "things" in table_names


# ── snapshot ──────────────────────────────────────────────────────────────────


def test_schema_tree_snapshot(snap_compare: Callable[..., bool], sample_catalog: Catalog) -> None:
    """Snapshot of the schema tree populated with sample catalog."""
    from textual.app import App, ComposeResult

    class _Host(App[None]):
        def compose(self) -> ComposeResult:
            yield SchemaBrowser(sample_catalog)

    assert snap_compare(_Host(), terminal_size=(80, 30))
