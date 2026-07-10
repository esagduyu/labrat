"""CLI: print a ready-to-paste MCP host config for the LabRat MCP server.

    uv run python -m labrat.mcp.print_config --host claude-code --profiles main
    uv run python -m labrat.mcp.print_config --host codex --connections-json '{...}'

Pure wrapper over ``labrat.mcp.host_configs`` — no config is written to disk;
the config text goes to stdout so the caller can pipe/redirect it into their
host's config file themselves. Usage errors (bad ``--host``, neither source
given, malformed input) print to stderr and exit 2.
"""

from __future__ import annotations

import argparse
import sys

from labrat.mcp.host_configs import build_mcp_server_config, render_host_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m labrat.mcp.print_config",
        description="Print a ready-to-paste MCP host config for the LabRat MCP server.",
    )
    parser.add_argument("--host", required=True, help="claude-code, generic, or codex")
    parser.add_argument("--profiles", default=None, help="comma-separated profile names")
    parser.add_argument("--connections-json", default=None, help="LABRAT_MCP_CONNECTIONS JSON")
    parser.add_argument("--primary", default=None, help="LABRAT_MCP_PRIMARY connection name")
    parser.add_argument("--log-dir", default=None, help="LABRAT_MCP_LOG_DIR path")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()] if args.profiles else None

    try:
        server = build_mcp_server_config(
            profiles=profiles,
            connections_json=args.connections_json,
            primary=args.primary,
            log_dir=args.log_dir,
        )
        text = render_host_config(args.host, server)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not text.endswith("\n"):
        text += "\n"
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
