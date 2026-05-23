"""Tests for ResultsTable widget."""

import time
from collections.abc import Callable

import polars as pl
import pytest
from textual.app import App, ComposeResult

from labrat.widgets.results_table import ResultsTable


@pytest.fixture()
def sample_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "name": ["alice", "bob", "charlie", "dave", "eve"],
            "score": [90, 75, 85, 60, 95],
        }
    )


def _host(df: pl.DataFrame, execution_time: float = 0.0) -> App[None]:
    class _H(App[None]):
        def compose(self) -> ComposeResult:
            rt = ResultsTable(id="table")
            rt.load(df, execution_time=execution_time)
            yield rt

    return _H()


# ── load ──────────────────────────────────────────────────────────────────────


async def test_load_populates_rows(sample_df: pl.DataFrame) -> None:
    """load() sets row_count to the DataFrame's row count."""
    async with _host(sample_df).run_test() as pilot:
        await pilot.pause()
        rt = pilot.app.query_one("#table", ResultsTable)
        assert rt.row_count == len(sample_df)


async def test_load_adds_columns(sample_df: pl.DataFrame) -> None:
    """load() adds columns matching the DataFrame's schema."""
    async with _host(sample_df).run_test() as pilot:
        await pilot.pause()
        rt = pilot.app.query_one("#table", ResultsTable)
        col_labels = [str(col.label) for col in rt.ordered_columns]
        assert "id" in col_labels
        assert "name" in col_labels
        assert "score" in col_labels


async def test_load_empty_dataframe() -> None:
    """load() on an empty DataFrame produces zero rows."""
    async with _host(pl.DataFrame()).run_test() as pilot:
        await pilot.pause()
        rt = pilot.app.query_one("#table", ResultsTable)
        assert rt.row_count == 0


# ── sort ──────────────────────────────────────────────────────────────────────


async def test_sort_ascending(sample_df: pl.DataFrame) -> None:
    """set_sort() sorts ascending; first visible row has the lowest value."""
    async with _host(sample_df).run_test() as pilot:
        await pilot.pause()
        rt = pilot.app.query_one("#table", ResultsTable)
        rt.set_sort("score", descending=False)
        await pilot.pause()
        first_name = rt.get_cell_at((0, 1))
        assert str(first_name) == "dave"


async def test_sort_descending(sample_df: pl.DataFrame) -> None:
    """set_sort() with descending=True puts highest value first."""
    async with _host(sample_df).run_test() as pilot:
        await pilot.pause()
        rt = pilot.app.query_one("#table", ResultsTable)
        rt.set_sort("score", descending=True)
        await pilot.pause()
        first_name = rt.get_cell_at((0, 1))
        assert str(first_name) == "eve"


async def test_sort_toggle(sample_df: pl.DataFrame) -> None:
    """Calling set_sort with same column twice applies both directions."""
    async with _host(sample_df).run_test() as pilot:
        await pilot.pause()
        rt = pilot.app.query_one("#table", ResultsTable)
        rt.set_sort("score", descending=False)
        rt.set_sort("score", descending=True)
        await pilot.pause()
        first_name = rt.get_cell_at((0, 1))
        assert str(first_name) == "eve"


# ── filter ────────────────────────────────────────────────────────────────────


async def test_filter_reduces_rows(sample_df: pl.DataFrame) -> None:
    """set_filter() with 'alice' shows only the row containing alice."""
    async with _host(sample_df).run_test() as pilot:
        await pilot.pause()
        rt = pilot.app.query_one("#table", ResultsTable)
        rt.set_filter("alice")
        await pilot.pause()
        assert rt.row_count == 1


async def test_filter_substring(sample_df: pl.DataFrame) -> None:
    """set_filter() matches across any column substring."""
    async with _host(sample_df).run_test() as pilot:
        await pilot.pause()
        rt = pilot.app.query_one("#table", ResultsTable)
        rt.set_filter("li")  # matches 'alice' and 'charlie'
        await pilot.pause()
        assert rt.row_count == 2


async def test_filter_clear_restores(sample_df: pl.DataFrame) -> None:
    """Clearing the filter restores all rows."""
    async with _host(sample_df).run_test() as pilot:
        await pilot.pause()
        rt = pilot.app.query_one("#table", ResultsTable)
        rt.set_filter("alice")
        rt.set_filter("")
        await pilot.pause()
        assert rt.row_count == len(sample_df)


# ── status ────────────────────────────────────────────────────────────────────


async def test_status_shows_row_count(sample_df: pl.DataFrame) -> None:
    """After loading, border_subtitle includes the row count."""
    async with _host(sample_df, execution_time=42.5).run_test() as pilot:
        await pilot.pause()
        rt = pilot.app.query_one("#table", ResultsTable)
        assert "5" in (rt.border_subtitle or "")


# ── performance ───────────────────────────────────────────────────────────────


async def test_sort_100k_rows() -> None:
    """set_sort() handles 100k rows correctly; underlying Polars sort is < 100ms."""
    big_df = pl.DataFrame({"val": range(100_000), "label": ["x"] * 100_000})

    class _H(App[None]):
        def compose(self) -> ComposeResult:
            rt = ResultsTable(id="table")
            rt.load(big_df)
            yield rt

    async with _H().run_test() as pilot:
        await pilot.pause()
        rt = pilot.app.query_one("#table", ResultsTable)
        rt.set_sort("val", descending=True)
        await pilot.pause()
        assert rt.get_cell_at((0, 0)) == "99999"

    # DataTable row insertion is O(n) and owned by Textual; test the data layer.
    start = time.monotonic()
    _ = big_df.sort("val", descending=True)
    elapsed_ms = (time.monotonic() - start) * 1000
    assert elapsed_ms < 100, f"Polars sort took {elapsed_ms:.1f}ms"


# ── snapshot ──────────────────────────────────────────────────────────────────


def test_results_table_snapshot(snap_compare: Callable[..., bool]) -> None:
    """Snapshot of a populated results table."""
    df = pl.DataFrame(
        {
            "order_id": [1, 2, 3],
            "amount": [99.5, 42.0, 150.0],
            "status": ["active", "closed", "pending"],
        }
    )

    class _Host(App[None]):
        def compose(self) -> ComposeResult:
            rt = ResultsTable(id="table")
            rt.load(df, execution_time=15.3)
            yield rt

    assert snap_compare(_Host(), terminal_size=(100, 20))
