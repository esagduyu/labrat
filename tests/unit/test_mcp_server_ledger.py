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


# Placed past LedgerBudget.max_bytes (8000, the inline preview's cap) but
# well within _ARTIFACT_FETCH_MAX_BYTES (64_000) -- proves get_artifact
# reaches content the inline ledger preview cannot.
_MARKER = "MARKER_BEYOND_8000_BYTES"


class _MarkerResult:
    def model_dump_json(self) -> str:
        return ("a" * 8500) + _MARKER + ("b" * 100)


class _StubMarkerTool(AgentTool[_EmptyInput]):
    """Stub tool whose payload has a marker beyond the inline preview budget."""

    @property
    def name(self) -> str:
        return "stub_marker"

    @property
    def description(self) -> str:
        return "stub tool with a marker beyond the inline preview budget"

    @property
    def input_model(self) -> type[_EmptyInput]:
        return _EmptyInput

    async def execute(self, ctx: ToolContext, args: _EmptyInput) -> object:
        return _MarkerResult()


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


# ── _get_artifact_text (backs get_artifact retrieval) ───────────────────────


def test_get_artifact_text_is_verbatim_not_double_json_encoded(tmp_path: Path) -> None:
    payload = ("a" * 8500) + _MARKER + ("b" * 100)
    store = mcp_server._get_result_store(tmp_path)
    ref = store.put_json(payload, kind="json")
    text = mcp_server._get_artifact_text(store, ref)
    assert text == payload
    assert not text.startswith('"')


def test_get_artifact_text_offset_slices_from_position(tmp_path: Path) -> None:
    payload = "a" * 100 + "b" * 100
    store = mcp_server._get_result_store(tmp_path)
    ref = store.put_json(payload, kind="json")
    text = mcp_server._get_artifact_text(store, ref, offset=100)
    assert text == "b" * 100


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
    # Verbatim, not double-JSON-encoded (no wrapping quotes/backslash escapes).
    assert fetched[0].text == "x" * 50_000


async def test_dispatch_and_render_get_artifact_returns_content_beyond_inline_preview(
    tmp_path: Path,
) -> None:
    """Load-bearing regression for the reviewer's Important finding: the
    inline ledger preview is capped at LedgerBudget.max_bytes (8000), so the
    marker (placed at byte ~8500) must NOT appear in the stored block, but
    MUST be reachable via get_artifact — otherwise get_artifact adds no
    information beyond what was already in context."""
    registry = ToolRegistry()
    registry.register(_StubMarkerTool())
    ctx = ToolContext()
    stored = await mcp_server._dispatch_and_render(
        "stub_marker",
        {},
        ctx,
        registry,
        ledger_on=True,
        store_dir=tmp_path,
        log_dir=None,
    )
    text = stored[0].text
    assert _MARKER not in text
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
    fetched_text = fetched[0].text
    assert _MARKER in fetched_text
    # Verbatim retrieval: no double-JSON-encoding artifacts.
    assert not fetched_text.startswith('"')
    assert fetched_text == ("a" * 8500) + _MARKER + ("b" * 100)


async def test_dispatch_and_render_get_artifact_offset_pages_further(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(_StubMarkerTool())
    ctx = ToolContext()
    stored = await mcp_server._dispatch_and_render(
        "stub_marker",
        {},
        ctx,
        registry,
        ledger_on=True,
        store_dir=tmp_path,
        log_dir=None,
    )
    ref_line = next(
        line for line in stored[0].text.splitlines() if line.startswith("artifact_ref: ")
    )
    ref = ref_line.removeprefix("artifact_ref: ")

    fetched = await mcp_server._dispatch_and_render(
        "get_artifact",
        {"ref": ref, "offset": 8500 + len(_MARKER)},
        ctx,
        registry,
        ledger_on=True,
        store_dir=tmp_path,
        log_dir=None,
    )
    assert fetched[0].text == "b" * 100
    assert _MARKER not in fetched[0].text


async def test_dispatch_and_render_get_artifact_unknown_ref_returns_error(tmp_path: Path) -> None:
    """Store IS configured, but the ref doesn't resolve -> error text branch."""
    registry = _registry_with_stub()
    ctx = ToolContext()
    result = await mcp_server._dispatch_and_render(
        "get_artifact",
        {"ref": "result://nonexistent-session/0000"},
        ctx,
        registry,
        ledger_on=True,
        store_dir=tmp_path,
        log_dir=None,
    )
    assert result[0].text.startswith("Error:")


async def test_dispatch_and_render_ledger_write_failure_falls_back_to_raw_payload(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A ResultStore write failure (e.g. a full disk) must never crash the
    dispatch — it degrades to the raw, unbounded payload."""

    def _boom(self: Any, obj: object, kind: str = "json") -> str:
        raise OSError("disk full")

    monkeypatch.setattr(mcp_server.ResultStore, "put_json", _boom)
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
    assert result[0].text == "x" * 50_000


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
