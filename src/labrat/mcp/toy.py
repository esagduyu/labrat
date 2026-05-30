"""2-tool MCP toy server for the Layer 2a compatibility spike.

Goal: confirm that a host harness (specifically `claude --print --mcp-config`)
round-trips a tool call cleanly in non-interactive mode. If yes, we proceed to
wrapping LabRat's full ToolRegistry as an MCP server. If no, we drop the MCP
driver from the plan and stay on AnthropicProvider for Phase 4.

Run directly::

    uv run python -m labrat.mcp.toy

Use from claude CLI::

    claude --print --strict-mcp-config --mcp-config '{
      "mcpServers": {
        "labrat-toy": {
          "command": "uv",
          "args": ["run", "python", "-m", "labrat.mcp.toy"]
        }
      }
    }' -p "Use the echo tool from labrat-toy to say hi."
"""

from __future__ import annotations

from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

mcp: FastMCP = FastMCP("labrat-toy")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the provided text back. Use this to confirm an MCP round-trip works."""
    return text


@mcp.tool()
def now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    mcp.run()
