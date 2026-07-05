"""llm_extract / llm_classify are registered in the shared data-tools registry."""

from __future__ import annotations

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import ToolContext


def test_llm_tools_in_default_registry() -> None:
    names = {t.name for t in build_data_tools_registry().tools}
    assert "llm_extract" in names
    assert "llm_classify" in names


async def test_llm_extract_dispatch_without_llm_fn_is_structured_error() -> None:
    """On a deterministic context the tool self-errors — dispatch succeeds, no raise."""
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=object(), catalog=object())  # llm_fn defaults None
    result = await registry.dispatch(
        "llm_extract",
        {"table": "t", "text_column": "c", "json_schema": {"properties": {"x": {}}}},
        ctx,
    )
    assert result.ok  # the dispatch itself succeeded
    ok_flag = getattr(result.value, "ok", None)
    assert ok_flag is False  # ... and the tool returned a structured error


async def test_llm_classify_dispatch_without_llm_fn_is_structured_error() -> None:
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=object(), catalog=object())
    result = await registry.dispatch(
        "llm_classify",
        {"table": "t", "text_column": "c", "labels": ["a", "b"]},
        ctx,
    )
    assert result.ok
    ok_flag = getattr(result.value, "ok", None)
    assert ok_flag is False
