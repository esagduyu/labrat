"""LabRat MCP server — exposes the data-tools registry over MCP stdio.

The server reads a connection spec from the ``LABRAT_MCP_CONNECTIONS`` env var
(JSON, same shape as ``scripts/run_task.py`` ``--connections``), wires up a
``ToolContext`` plus the standard data tools, and serves them as MCP tools.

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
import sys
from typing import Any

from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.db.duckdb_engine import DuckDBConnection


def _build_context_from_env() -> tuple[ToolContext, list[DuckDBConnection]]:
    """Parse env vars into a ToolContext + the list of live connections to clean up."""
    raw = os.environ.get("LABRAT_MCP_CONNECTIONS")
    if not raw:
        print(
            "LABRAT_MCP_CONNECTIONS env var is required (JSON connection spec).",
            file=sys.stderr,
        )
        sys.exit(2)

    spec: dict[str, dict[str, Any]] = json.loads(raw)
    if not spec:
        print("LABRAT_MCP_CONNECTIONS must contain at least one entry.", file=sys.stderr)
        sys.exit(2)

    live: list[DuckDBConnection] = []
    connections: dict[str, object] = {}
    catalogs: dict[str, object] = {}

    for name, meta in spec.items():
        db_type = str(meta.get("db_type", "")).lower()
        if db_type != "duckdb":
            print(
                f"labrat-mcp only supports db_type=duckdb in --connections today "
                f"(got {db_type!r} for {name!r}). Use the attach_database tool from "
                f"the agent for SQLite/Postgres/MySQL.",
                file=sys.stderr,
            )
            sys.exit(2)
        conn = DuckDBConnection(
            path=str(meta["db_path"]), read_only=bool(meta.get("read_only", True))
        )
        conn.connect()
        live.append(conn)
        connections[name] = conn
        catalogs[name] = conn.introspect_catalog()

    primary = os.environ.get("LABRAT_MCP_PRIMARY") or next(iter(connections))
    if primary not in connections:
        print(
            f"LABRAT_MCP_PRIMARY={primary!r} not in connections {list(connections)}.",
            file=sys.stderr,
        )
        sys.exit(2)

    return (
        ToolContext(connections=connections, catalogs=catalogs, primary=primary),
        live,
    )


def _build_server(ctx: ToolContext, registry: ToolRegistry) -> Server[Any, Any]:
    server: Server[Any, Any] = Server("labrat")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:  # pyright: ignore[reportUnusedFunction]
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
        return out

    @server.call_tool()
    async def _call_tool(  # pyright: ignore[reportUnusedFunction]
        name: str, arguments: dict[str, Any]
    ) -> list[TextContent]:
        dispatch = await registry.dispatch(name, arguments, ctx)
        if not dispatch.ok:
            return [TextContent(type="text", text=f"Error: {dispatch.error}")]
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
        return [TextContent(type="text", text=payload)]

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
