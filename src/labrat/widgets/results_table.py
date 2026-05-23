"""Results table widget: sortable, filterable DataTable backed by Polars."""

from typing import ClassVar

import polars as pl
from textual.binding import Binding, BindingType
from textual.coordinate import Coordinate
from textual.widgets import DataTable


class ResultsTable(DataTable[str]):
    """DataTable subclass that loads from a Polars DataFrame.

    Supports:
    - load(df) to replace contents
    - set_sort(column, descending) to sort in-place
    - set_filter(text) to filter rows by substring
    - s key to sort by cursor column, / to open filter
    - Ctrl+C to copy the focused cell to the clipboard
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("s", "sort_cursor_column", "Sort"),
        Binding("ctrl+c", "copy_cell", "Copy Cell"),
    ]

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes, zebra_stripes=True, show_header=True)
        self._source_df: pl.DataFrame = pl.DataFrame()
        self._displayed_df: pl.DataFrame = pl.DataFrame()
        self._sort_column: str | None = None
        self._sort_desc: bool = False
        self._filter_text: str = ""
        self._execution_time: float = 0.0

    # ── public API ────────────────────────────────────────────────────────────

    def load(self, df: pl.DataFrame, *, execution_time: float = 0.0) -> None:
        """Replace contents with a new Polars DataFrame."""
        self._source_df = df
        self._execution_time = execution_time
        self._sort_column = None
        self._sort_desc = False
        self._filter_text = ""
        self._reload()

    def set_sort(self, column: str, *, descending: bool = False) -> None:
        """Sort the table by *column*."""
        self._sort_column = column
        self._sort_desc = descending
        self._reload()

    def set_filter(self, text: str) -> None:
        """Filter visible rows to those containing *text* in any column."""
        self._filter_text = text
        self._reload()

    # ── internals ────────────────────────────────────────────────────────────

    def _reload(self) -> None:
        df = self._source_df
        if self._filter_text and not df.is_empty():
            df = _filter_df(df, self._filter_text)
        if self._sort_column and self._sort_column in df.columns and not df.is_empty():
            df = df.sort(self._sort_column, descending=self._sort_desc)  # pyright: ignore[reportUnknownMemberType]
        self._displayed_df = df

        self.clear(columns=True)
        if df.is_empty() or len(df.columns) == 0:
            self._update_status()
            return
        self.add_columns(*df.columns)
        self.add_rows([str(v) if v is not None else "" for v in row] for row in df.iter_rows())
        self._update_status()

    def _update_status(self) -> None:
        total = len(self._source_df)
        shown = len(self._displayed_df)
        time_ms = f"{self._execution_time:.0f}ms" if self._execution_time else ""
        if shown < total:
            subtitle = f"{shown:,} of {total:,} rows  {time_ms}"
        else:
            subtitle = f"{total:,} rows  {time_ms}"
        self.border_subtitle = subtitle.strip()

    # ── actions ───────────────────────────────────────────────────────────────

    def action_sort_cursor_column(self) -> None:
        """Sort by the column under the cursor; toggle direction on repeat."""
        col_idx = self.cursor_column
        if col_idx < 0 or col_idx >= len(self._displayed_df.columns):
            return
        col_name = self._displayed_df.columns[col_idx]
        if self._sort_column == col_name:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_column = col_name
            self._sort_desc = False
        self._reload()

    async def action_copy_cell(self) -> None:
        """Copy the value of the focused cell to the OS clipboard."""
        coord: Coordinate = self.cursor_coordinate
        try:
            value = self.get_cell_at(coord)
            self.app.copy_to_clipboard(str(value))  # pyright: ignore[reportUnknownMemberType]
            self.notify(f"Copied: {value}", timeout=2)
        except Exception:
            pass

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Sort by clicking a column header."""
        col_name = str(event.label)
        if col_name in self._source_df.columns:
            if self._sort_column == col_name:
                self._sort_desc = not self._sort_desc
            else:
                self._sort_column = col_name
                self._sort_desc = False
            self._reload()


def _filter_df(df: pl.DataFrame, text: str) -> pl.DataFrame:
    """Return rows where any column contains *text* as a substring."""
    masks = [df[col].cast(pl.String).str.contains(text, literal=True) for col in df.columns]
    combined = masks[0]
    for m in masks[1:]:
        combined = combined | m
    return df.filter(combined)  # pyright: ignore[reportUnknownMemberType]
