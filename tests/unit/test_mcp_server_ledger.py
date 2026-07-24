"""Tests for the opt-in server-side Context Ledger on the MCP server (GAP 3).

Every test asserts real behavior end-to-end (helper output, or the
``_dispatch_and_render``/``_list_tool_schemas`` seams that back ``_call_tool``/
``_list_tools``) — none are comment-only placeholders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from labrat.agent.tools.base import Tool as AgentTool
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.mcp import server as mcp_server


class _EmptyInput(BaseModel):
    pass


class _BigResult:
    """Mimics a Pydantic tool result whose model_dump_json() is oversized."""

    def model_dump_json(self) -> str:
        return "x" * 50_000


class _StubBigTool(AgentTool[_EmptyInput]):
    """Deterministic stub tool returning an oversized payload."""

    @property
    def name(self) -> str:
        return "stub_big"

    @property
    def description(self) -> str:
        return "stub tool returning an oversized payload"

    @property
    def input_model(self) -> type[_EmptyInput]:
        return _EmptyInput

    async def execute(self, ctx: ToolContext, args: _EmptyInput) -> object:
        return _BigResult()


def _registry_with_stub() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_StubBigTool())
    return registry


# ── _render_payload_via_ledger (helper-level unit) ──────────────────────────


def test_render_payload_via_ledger_truncates_oversized_payload(tmp_path: Path) -> None:
    text = mcp_server._render_payload_via_ledger(
        store_dir=tmp_path,
        tool_name="run_sql",
        payload="y" * 50_000,
    )
    assert "[context ledger]" in text
    assert "artifact_ref:" in text
    assert len(text) < 20_000


def test_render_payload_via_ledger_passthrough_under_budget(tmp_path: Path) -> None:
    """Under-budget payloads pass through byte-identical — no ledger marker."""
    text = mcp_server._render_payload_via_ledger(
        store_dir=tmp_path,
        tool_name="run_sql",
        payload="small payload",
    )
    assert text == "small payload"


# ── _store_dir_from_env (fixes the `Path("") or None` truthiness trap) ─────


def test_store_dir_from_env_unset_is_none(monkeypatch: Any) -> None:
    monkeypatch.delenv("LABRAT_MCP_RESULT_STORE_DIR", raising=False)
    assert mcp_server._store_dir_from_env() is None


def test_store_dir_from_env_set_returns_path(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("LABRAT_MCP_RESULT_STORE_DIR", str(tmp_path))
    assert mcp_server._store_dir_from_env() == tmp_path


# ── _list_tool_schemas (backs _list_tools) ──────────────────────────────────


def test_list_tool_schemas_ledger_off_omits_get_artifact() -> None:
    """OFF path: exactly today's tool list — no get_artifact."""
    registry = _registry_with_stub()
    tools = mcp_server._list_tool_schemas(registry, ledger_on=False)
    assert [t.name for t in tools] == ["stub_big"]


def test_list_tool_schemas_ledger_on_appends_get_artifact() -> None:
    registry = _registry_with_stub()
    tools = mcp_server._list_tool_schemas(registry, ledger_on=True)
    assert [t.name for t in tools] == ["stub_big", "get_artifact"]


# ── _dispatch_and_render (backs _call_tool) — the cardinal-constraint tests ─


async def test_dispatch_and_render_ledger_off_is_byte_identical(tmp_path: Path) -> None:
    """THE cardinal constraint: with ledger_on=False, _call_tool's core returns
    exactly today's raw payload — even when a store_dir happens to be
    configured, proving the *flag* gates behavior, not just store_dir."""
    registry = _registry_with_stub()
    ctx = ToolContext()
    result = await mcp_server._dispatch_and_render(
        "stub_big",
        {},
        ctx,
        registry,
        ledger_on=False,
        store_dir=tmp_path,
        log_dir=None,
    )
    assert len(result) == 1
    assert result[0].text == "x" * 50_000


async def test_dispatch_and_render_get_artifact_not_intercepted_when_ledger_off() -> None:
    """get_artifact is not a real tool; with the ledger off it falls through
    to the registry and gets today's 'Unknown tool' error — unchanged."""
    registry = _registry_with_stub()
    ctx = ToolContext()
    result = await mcp_server._dispatch_and_render(
        "get_artifact",
        {"ref": "result://nonexistent/0000"},
        ctx,
        registry,
        ledger_on=False,
        store_dir=None,
        log_dir=None,
    )
    assert result[0].text == "Error: Unknown tool: 'get_artifact'"


async def test_dispatch_and_render_ledger_on_bounds_oversized_payload(tmp_path: Path) -> None:
    registry = _registry_with_stub()
    ctx = ToolContext()
    result = await mcp_server._dispatch_and_render(
        "stub_big",
        {},
        ctx,
        registry,
        ledger_on=True,
        store_dir=tmp_path,
        log_dir=None,
    )
    text = result[0].text
    assert "[context ledger]" in text
    assert "artifact_ref:" in text
    assert len(text) < 20_000


async def test_dispatch_and_render_get_artifact_roundtrip(tmp_path: Path) -> None:
    registry = _registry_with_stub()
    ctx = ToolContext()
    stored = await mcp_server._dispatch_and_render(
        "stub_big",
        {},
        ctx,
        registry,
        ledger_on=True,
        store_dir=tmp_path,
        log_dir=None,
    )
    text = stored[0].text
    ref_line = next(line for line in text.splitlines() if line.startswith("artifact_ref: "))
    ref = ref_line.removeprefix("artifact_ref: ")

    fetched = await mcp_server._dispatch_and_render(
        "get_artifact",
        {"ref": ref},
        ctx,
        registry,
        ledger_on=True,
        store_dir=tmp_path,
        log_dir=None,
    )
    assert fetched[0].text.count("x") > 100


async def test_dispatch_and_render_get_artifact_no_store_configured() -> None:
    registry = _registry_with_stub()
    ctx = ToolContext()
    result = await mcp_server._dispatch_and_render(
        "get_artifact",
        {"ref": "result://x/0000"},
        ctx,
        registry,
        ledger_on=True,
        store_dir=None,
        log_dir=None,
    )
    assert result[0].text == "Error: no result store configured"
