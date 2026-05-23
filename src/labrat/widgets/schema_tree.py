"""Schema browser widget: search box + catalog tree."""

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Tree
from textual.widgets.tree import TreeNode

from labrat.db.catalog import Catalog, Column


@dataclass
class _ColNodeData:
    catalog_name: str
    schema_name: str
    table_name: str
    column: Column

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}.{self.column.name}"


class SchemaTree(Tree[_ColNodeData]):
    """Tree widget backed by a Catalog.

    Node structure: root → schemas → tables → column leaves.
    Column leaves carry _ColNodeData; branch nodes carry None.
    """

    class ColumnSelected(Message):
        """Posted when a column leaf is activated."""

        def __init__(self, qualified: str) -> None:
            self.qualified = qualified
            super().__init__()

    class ColumnHighlighted(Message):
        """Posted when a column leaf is highlighted (for status bar display)."""

        def __init__(self, column: Column, table_name: str) -> None:
            self.column = column
            self.table_name = table_name
            super().__init__()

    def __init__(self, catalog: Catalog | None = None) -> None:
        label = catalog.database_name if catalog else "schema"
        super().__init__(label, id="schema-tree")
        self._catalog = catalog
        self._filter = ""
        if catalog:
            self._fill(catalog)

    # ── public API ────────────────────────────────────────────────────────────

    def populate(self, catalog: Catalog) -> None:
        """Replace current tree contents with a new catalog."""
        self._catalog = catalog
        self.root.set_label(catalog.database_name)
        self.root.remove_children()
        self._fill(catalog)

    def filter(self, text: str) -> None:
        """Rebuild the tree showing only nodes matching *text*."""
        self._filter = text.lower()
        if self._catalog:
            self.root.remove_children()
            self._fill(self._catalog)

    # ── tree construction ─────────────────────────────────────────────────────

    def _fill(self, catalog: Catalog) -> None:
        flt = self._filter
        for schema in catalog.schemas:
            schema_node: TreeNode[_ColNodeData] = self.root.add(
                schema.name, data=None, allow_expand=True
            )
            for table in schema.tables:
                col_names = [c.name for c in table.columns]
                if flt and not self._table_matches(table.name, col_names, flt):
                    continue
                table_node: TreeNode[_ColNodeData] = schema_node.add(
                    table.name, data=None, allow_expand=True
                )
                for col in table.columns:
                    if flt and flt not in col.name.lower() and flt not in table.name.lower():
                        continue
                    nullable = "NULL" if col.nullable else "NOT NULL"
                    data = _ColNodeData(catalog.database_name, schema.name, table.name, col)
                    table_node.add_leaf(f"{col.name}  {col.data_type} {nullable}", data=data)
        self.root.expand()

    @staticmethod
    def _table_matches(table_name: str, col_names: list[str], flt: str) -> bool:
        return flt in table_name.lower() or any(flt in c.lower() for c in col_names)

    # ── event handlers ────────────────────────────────────────────────────────

    def on_tree_node_selected(self, event: Tree.NodeSelected[_ColNodeData]) -> None:
        if event.node.data is not None:
            self.post_message(SchemaTree.ColumnSelected(event.node.data.qualified_name))

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[_ColNodeData]) -> None:
        if event.node.data is not None:
            d = event.node.data
            self.post_message(SchemaTree.ColumnHighlighted(d.column, d.table_name))


class SchemaBrowser(Widget):
    """Composite widget: search box on top, SchemaTree below.

    Posts SchemaTree.ColumnSelected and SchemaTree.ColumnHighlighted messages
    that parent screens can handle.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+r", "refresh", "Refresh Schema"),
    ]

    DEFAULT_CSS = """
    SchemaBrowser {
        height: 1fr;
    }
    SchemaBrowser > Vertical {
        height: 1fr;
    }
    #schema-search {
        height: 3;
        border: none;
    }
    """

    def __init__(
        self,
        catalog: Catalog | None = None,
        *,
        profile_name: str = "default",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._catalog = catalog
        self._profile_name = profile_name
        self._connection: object = None

    def set_connection(self, connection: object) -> None:
        """Provide a live connection for refresh operations."""
        self._connection = connection

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Input(placeholder="Filter tables/columns…", id="schema-search")
            yield SchemaTree(self._catalog)

    @on(Input.Changed, "#schema-search")
    def _on_search_changed(self, event: Input.Changed) -> None:
        self.query_one(SchemaTree).filter(event.value)

    async def action_refresh(self) -> None:
        """Re-introspect the live connection and update the cache + tree."""
        from labrat.db.base import Connection

        if not isinstance(self._connection, Connection):
            return
        catalog = self._connection.introspect_catalog()
        from labrat.db.catalog_cache import save_catalog

        save_catalog(self._profile_name, catalog)
        self.query_one(SchemaTree).populate(catalog)
