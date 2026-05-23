"""Tests for QueryEditor widget."""

import time
from collections.abc import Callable

from textual.app import App, ComposeResult

from labrat.widgets.query_editor import QueryEditor


class _EditorHost(App[None]):
    def __init__(self, text: str = "") -> None:
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        yield QueryEditor(self._text, id="editor")


async def test_editor_mounts() -> None:
    """QueryEditor mounts and accepts text."""
    async with _EditorHost("SELECT 1").run_test() as pilot:
        await pilot.pause()
        editor = pilot.app.query_one("#editor", QueryEditor)
        assert "SELECT" in editor.text


async def test_toggle_comment_adds_prefix() -> None:
    """Ctrl+/ adds SQL comment prefix on the current line."""
    async with _EditorHost("SELECT 1").run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+slash")
        await pilot.pause()
        editor = pilot.app.query_one("#editor", QueryEditor)
        assert editor.text.startswith("-- ")


async def test_toggle_comment_removes_prefix() -> None:
    """Ctrl+/ removes existing comment prefix."""
    async with _EditorHost("-- SELECT 1").run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+slash")
        await pilot.pause()
        editor = pilot.app.query_one("#editor", QueryEditor)
        assert not editor.text.startswith("-- ")
        assert "SELECT" in editor.text


async def test_cursor_moved_message() -> None:
    """Moving the cursor emits QueryEditor.CursorMoved."""
    received: list[QueryEditor.CursorMoved] = []

    class _Host(App[None]):
        def compose(self) -> ComposeResult:
            yield QueryEditor("line1\nline2", id="editor")

        def on_query_editor_cursor_moved(self, event: QueryEditor.CursorMoved) -> None:
            received.append(event)

    async with _Host().run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert any(m.row == 2 for m in received)


async def test_editor_performance_10k_lines() -> None:
    """10k lines loads within a reasonable wall-clock budget."""
    sql = "SELECT * FROM orders WHERE id = 1;\n" * 10_000
    start = time.monotonic()
    async with _EditorHost(sql).run_test() as pilot:
        await pilot.pause()
    elapsed = time.monotonic() - start
    assert elapsed < 30.0, f"10k-line load took {elapsed:.1f}s"


def test_query_editor_snapshot(snap_compare: Callable[..., bool]) -> None:
    """Snapshot of SQL editor with sample query."""
    sql = (
        "SELECT\n"
        "    order_id,\n"
        "    amount\n"
        "FROM orders\n"
        "WHERE status = 'active'\n"
        "ORDER BY amount DESC\n"
        "LIMIT 100;\n"
    )
    assert snap_compare(_EditorHost(sql), terminal_size=(100, 20))
