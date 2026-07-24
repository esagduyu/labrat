"""LabRat MCP server — exposes the data-tools registry over MCP stdio.

The server resolves its connections from two additive env vars (parsed by
``labrat.mcp.config.resolve_from_env``), wires up a ``ToolContext`` plus the
standard data tools, and serves them as MCP tools:

- ``LABRAT_MCP_CONNECTIONS`` — JSON connection spec, duckdb-only (same shape
  as ``scripts/run_task.py`` ``--connections``); the legacy path used by
  DAB's ``claude-mcp`` driver.
- ``LABRAT_MCP_PROFILES`` — comma-separated profile names, resolved through
  ``labrat.profile.manager`` so any of the seven adapters can be mounted
  with keyring-backed secrets. Connection keys are profile names.
- ``LABRAT_MCP_LEDGER`` (opt-in, ``"1"`` to enable) + ``LABRAT_MCP_RESULT_STORE_DIR``
  — the server-side Context Ledger: oversized tool payloads are bounded to a
  ``[context ledger] ...`` preview block and the full text is stashed as a
  ``ResultStore`` artifact, retrievable via the synthetic ``get_artifact``
  tool (only listed when the ledger is on). Both unset (the default) keeps
  ``_call_tool``/``_list_tools`` byte-identical to the no-ledger path.

At least one of the two must be set. ``ToolContext.read_only`` is derived,
not user-set directly: it's False unless every ``LABRAT_MCP_CONNECTIONS``
entry explicitly sets ``"read_only": true``, OR'd with True if any resolved
``LABRAT_MCP_PROFILES`` profile has ``is_read_only=True`` (the default) —
see ``resolve_from_env``'s docstring for the full derivation.

Any MCP-capable host harness (``claude --print --mcp-config …``, Codex, Cursor,
OpenCode, etc.) can mount this server and drive the tools natively — no custom
text protocol, so no model-format fragility.

Run directly::

    LABRAT_MCP_CONNECTIONS='{"main":{"db_type":"duckdb","db_path":"/path.duckdb"}}' \\
      uv run python -m labrat.mcp.server

mcp-config snippet::

    {
      "mcpServers": {
        "labrat": {
          "command": "uv",
          "args": ["--directory", "/Users/ege/repos/labrat",
                   "run", "python", "-m", "labrat.mcp.server"],
          "env": {
            "LABRAT_MCP_CONNECTIONS": "{...}",
            "LABRAT_MCP_PRIMARY": "main"
          }
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tool_trace import append_tool_trace
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.tools.serialization import ModelVisibleToolResult, render
from labrat.db.base import Connection
from labrat.mcp.config import resolve_from_env
from labrat.results.store import ResultStore, cap_bytes
from labrat.runtime.context_ledger import LedgerBudget

_TOOL_LOG_FILENAME = "mcp_tool_calls.jsonl"

# get_artifact's fetch budget is deliberately far above LedgerBudget.max_bytes
# (the inline ledger-block preview's cap, 8000 by default): if it matched, the
# tool would add no information over the preview already in context, and the
# ledger summary's "pull full via get_artifact" promise would be unfulfillable
# for anything beyond the first 8000 bytes.
_ARTIFACT_FETCH_MAX_BYTES = 64_000

# One ResultStore per store_dir for the life of the process. ResultStore mints
# a random session id per instance and keeps its artifact index in memory only
# (see labrat/results/store.py) — a fresh instance per call would get a new
# session each time and could never resolve a ref written by an earlier
# instance. Caching by directory lets get_artifact resolve refs written
# earlier in the same server process.
_result_stores: dict[str, ResultStore] = {}


def _get_result_store(store_dir: Path) -> ResultStore:
    key = str(store_dir)
    store = _result_stores.get(key)
    if store is None:
        store = ResultStore(store_dir)
        _result_stores[key] = store
    return store


def _get_artifact_text(
    store: ResultStore, ref: str, *, offset: int = 0, max_bytes: int = _ARTIFACT_FETCH_MAX_BYTES
) -> str:
    """Fetch a stored artifact's text for get_artifact, well beyond the inline
    ledger preview's budget (``_ARTIFACT_FETCH_MAX_BYTES`` vs
    ``LedgerBudget.max_bytes``), optionally starting at a character ``offset``
    for simple pagination.

    Uses ``ResultStore.get()`` (which JSON-*decodes* the stored payload) rather
    than ``ResultStore.preview()`` (which re-reads the raw on-disk JSON text).
    ``_render_payload_via_ledger`` stores the raw MCP payload string via
    ``put_json``, so ``.preview()`` would return it quote-wrapped and
    backslash-escaped (double JSON encoding); ``.get()`` round-trips back to
    the original string verbatim.
    """
    obj = store.get(ref)
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    if offset > 0:
        text = text[offset:]
    return cap_bytes(text, max_bytes)


def _store_dir_from_env() -> Path | None:
    """Parse ``LABRAT_MCP_RESULT_STORE_DIR``; unset/empty -> None.

    NOTE: ``Path(os.environ.get(...)) or None`` is a no-op guard — pathlib.Path
    defines neither ``__bool__`` nor ``__len__``, so instances are always
    truthy and the ``or`` branch never fires; ``Path("")`` (normalises to
    ``Path(".")``) would silently become the ledger's store directory even
    with the env var unset. Guard on the raw string instead.
    """
    raw = os.environ.get("LABRAT_MCP_RESULT_STORE_DIR")
    return Path(raw) if raw else None


def _ledger_max_bytes() -> int:
    """Byte budget above which a tool payload is stored and previewed.

    Defaults to ``LedgerBudget.max_bytes`` (8000). ``LABRAT_MCP_LEDGER_MAX_BYTES``
    overrides it — the DAB claude-mcp path raises it to 64000 so grounding
    payloads (search_reference_docs/describe_table run 8-22 KB) pass through
    whole while genuinely oversized run_sql dumps still get bounded. A missing
    or non-integer value falls back to the default rather than crashing.
    """
    raw = os.environ.get("LABRAT_MCP_LEDGER_MAX_BYTES")
    if raw is None:
        return LedgerBudget().max_bytes
    try:
        return int(raw)
    except ValueError:
        return LedgerBudget().max_bytes


def _render_payload_via_ledger(*, store_dir: Path, tool_name: str, payload: str) -> str:
    """Bound an oversized MCP tool payload: store the full text, return a preview block.

    MCP already serialized the tool value to a string by the time this is
    called, so the ledger's typed table/json/trace hooks (ContextLedger.record)
    aren't reachable here — we bound by bytes directly and stash the full
    string as a json artifact the model can pull back via get_artifact.
    """
    budget = LedgerBudget(max_bytes=_ledger_max_bytes())
    if len(payload.encode("utf-8")) <= budget.max_bytes:
        return payload
    store = _get_result_store(store_dir)
    ref = store.put_json(payload, kind="json")
    mv = ModelVisibleToolResult(
        summary=f"{tool_name}: {len(payload.encode('utf-8'))}-byte payload stored; "
        f"get_artifact(ref={ref!r}) returns up to {_ARTIFACT_FETCH_MAX_BYTES} bytes "
        "(pass offset to page further).",
        preview=cap_bytes(payload, budget.max_bytes),
        artifact_ref=ref,
        truncated=True,
    )
    return render(mv)


def _list_tool_schemas(registry: ToolRegistry, *, ledger_on: bool) -> list[Tool]:
    """Registry schemas as MCP ``Tool`` objects, plus the synthetic get_artifact
    tool when the ledger is enabled. ``ledger_on=False`` reproduces exactly
    today's list — the OFF-path listing guarantee."""
    out: list[Tool] = []
    for tool in registry.tools:
        schema = tool.anthropic_schema()
        out.append(
            Tool(
                name=schema["name"],
                description=schema.get("description", ""),
                inputSchema=schema["input_schema"],
            )
        )
    if ledger_on:
        out.append(
            Tool(
                name="get_artifact",
                description=(
                    "Retrieve a stored tool-result artifact by ref (e.g. "
                    "'result://<session>/0000'), returning up to "
                    f"{_ARTIFACT_FETCH_MAX_BYTES} bytes of the full payload the "
                    "ledger stored — well beyond the inline ledger preview. Pass "
                    "an integer `offset` to page through payloads longer than "
                    "that."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string"},
                        "offset": {
                            "type": "integer",
                            "description": (
                                "Character offset to start the returned text "
                                "from, for paging past the first "
                                f"{_ARTIFACT_FETCH_MAX_BYTES} bytes (default 0)."
                            ),
                        },
                    },
                    "required": ["ref"],
                },
            )
        )
    return out


def _log_tool_call(
    log_dir: str | None,
    *,
    name: str,
    arguments: dict[str, Any],
    ok: bool,
    output: str,
    latency_ms: float,
) -> None:
    """Append one audit line per tool dispatch to ``<log_dir>/mcp_tool_calls.jsonl``.
    No-op when ``log_dir`` is falsy (gated on ``LABRAT_MCP_LOG_DIR``).
    Enables audit-grade per-call traces (the gap that made the DAB contamination only
    reconstructable after the fact)."""
    append_tool_trace(
        log_dir,
        _TOOL_LOG_FILENAME,
        tool=name,
        input=arguments,
        ok=ok,
        output=output,
        latency_ms=latency_ms,
    )


async def _dispatch_and_render(
    name: str,
    arguments: dict[str, Any],
    ctx: ToolContext,
    registry: ToolRegistry,
    *,
    ledger_on: bool,
    store_dir: Path | None,
    log_dir: str | None,
) -> list[TextContent]:
    """Core of MCP ``_call_tool``, factored out of the ``@server.call_tool()``
    closure so tests can drive it directly without going through the mcp SDK's
    request-handler plumbing.

    ``ledger_on=False`` reproduces exactly today's behavior byte-for-byte: the
    get_artifact intercept and the ledger-bounding call are both gated on the
    flag, not merely on ``store_dir`` being set.
    """
    if name == "get_artifact" and ledger_on:
        t0 = time.monotonic()
        ref = str(arguments.get("ref", ""))
        if store_dir is None:
            text = "Error: no result store configured"
        else:
            try:
                offset = int(arguments.get("offset", 0) or 0)
            except (TypeError, ValueError):
                offset = 0
            try:
                text = _get_artifact_text(_get_result_store(store_dir), ref, offset=max(offset, 0))
            except Exception as exc:
                text = f"Error: {exc}"
        _log_tool_call(
            log_dir,
            name="get_artifact",
            arguments=arguments,
            ok=not text.startswith("Error:"),
            output=text,
            latency_ms=(time.monotonic() - t0) * 1000,
        )
        return [TextContent(type="text", text=text)]

    t0 = time.monotonic()
    dispatch = await registry.dispatch(name, arguments, ctx)
    if not dispatch.ok:
        error_text = f"Error: {dispatch.error}"
        _log_tool_call(
            log_dir,
            name=name,
            arguments=arguments,
            ok=False,
            output=error_text,
            latency_ms=(time.monotonic() - t0) * 1000,
        )
        return [TextContent(type="text", text=error_text)]
    value = dispatch.value
    payload: str
    dumper = getattr(value, "model_dump_json", None)
    if callable(dumper):
        payload = str(dumper())
    else:
        try:
            payload = json.dumps(value, default=str)
        except (TypeError, ValueError):
            payload = str(value)
    if ledger_on and store_dir is not None:
        try:
            payload = _render_payload_via_ledger(
                store_dir=store_dir, tool_name=name, payload=payload
            )
        except Exception:
            # A ResultStore write failure (e.g. a full/unwritable disk) must
            # never crash the dispatch — degrade to the raw (unbounded)
            # payload rather than losing the tool result entirely.
            pass
    _log_tool_call(
        log_dir,
        name=name,
        arguments=arguments,
        ok=True,
        output=payload,
        latency_ms=(time.monotonic() - t0) * 1000,
    )
    return [TextContent(type="text", text=payload)]


def _build_context_from_env() -> tuple[ToolContext, list[Connection]]:
    """Parse env vars into a ToolContext + the list of live connections to clean up."""
    rc = resolve_from_env(os.environ)
    ctx = ToolContext(
        connections=dict(rc.connections),
        catalogs=dict(rc.catalogs),
        primary=rc.primary,
        read_only=rc.read_only,
        profile_name=rc.profile_name,
        # The local-embed classify backend runs without an llm_fn, so it is the
        # only classification path usable over MCP (where llm_fn is None and the
        # LLM backend self-errors). Opt in via env; default "llm" is unchanged.
        llm_classify_backend=os.environ.get("LABRAT_MCP_LLM_CLASSIFY_BACKEND", "llm"),
    )
    return ctx, list(rc.connections.values())


def _build_server(ctx: ToolContext, registry: ToolRegistry) -> Server[Any, Any]:
    server: Server[Any, Any] = Server("labrat")

    # Read once at server-build time — matches the existing log_dir pattern
    # below. Opt-in: LABRAT_MCP_LEDGER unset (the default) keeps ledger_on
    # False, so _list_tool_schemas/_dispatch_and_render both take their
    # byte-identical-to-today branch.
    ledger_on = os.environ.get("LABRAT_MCP_LEDGER") == "1"
    store_dir = _store_dir_from_env()

    @server.list_tools()
    async def _list_tools() -> list[Tool]:  # pyright: ignore[reportUnusedFunction]
        return _list_tool_schemas(registry, ledger_on=ledger_on)

    log_dir = os.environ.get("LABRAT_MCP_LOG_DIR")

    @server.call_tool()
    async def _call_tool(  # pyright: ignore[reportUnusedFunction]
        name: str, arguments: dict[str, Any]
    ) -> list[TextContent]:
        return await _dispatch_and_render(
            name,
            arguments,
            ctx,
            registry,
            ledger_on=ledger_on,
            store_dir=store_dir,
            log_dir=log_dir,
        )

    return server


async def _serve() -> None:
    ctx, live = _build_context_from_env()
    registry = build_data_tools_registry()
    server = _build_server(ctx, registry)
    init_options = InitializationOptions(
        server_name="labrat",
        server_version="0.1.0",
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={},
        ),
    )
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_options)
    finally:
        for conn in live:
            try:
                conn.disconnect()
            except Exception:
                pass


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
