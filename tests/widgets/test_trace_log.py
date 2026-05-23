"""Tests for TraceLog widget (M16)."""

from __future__ import annotations

from textual.app import App, ComposeResult

from labrat.widgets.trace_log import TraceLog


class _TraceApp(App[None]):
    def compose(self) -> ComposeResult:
        yield TraceLog(id="trace")


async def test_trace_log_renders() -> None:
    """TraceLog mounts without error."""
    async with _TraceApp().run_test() as pilot:
        await pilot.pause()
        tl = pilot.app.query_one("#trace", TraceLog)
        assert tl is not None


async def test_trace_log_log_tool_call() -> None:
    """log_tool_call() records a tool invocation in the trace."""
    async with _TraceApp().run_test() as pilot:
        await pilot.pause()
        tl = pilot.app.query_one("#trace", TraceLog)
        tl.log_tool_call("list_tables", {"schema_filter": None}, "3 tables")
        await pilot.pause()
        assert "list_tables" in tl.content


async def test_trace_log_log_text() -> None:
    """log_text() records agent text in the trace."""
    async with _TraceApp().run_test() as pilot:
        await pilot.pause()
        tl = pilot.app.query_one("#trace", TraceLog)
        tl.log_text("The answer is 42.")
        await pilot.pause()
        assert "42" in tl.content


async def test_trace_log_multiple_entries() -> None:
    """Multiple calls accumulate in order."""
    async with _TraceApp().run_test() as pilot:
        await pilot.pause()
        tl = pilot.app.query_one("#trace", TraceLog)
        tl.log_tool_call("list_tables", {}, "result1")
        tl.log_tool_call("describe_table", {"table": "orders"}, "result2")
        tl.log_text("summary text")
        await pilot.pause()
        content = tl.content
        assert content.index("list_tables") < content.index("describe_table")
        assert "summary text" in content
