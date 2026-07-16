#!/usr/bin/env python3
"""Run one LabRat agent task against an arbitrary prompt + connection spec.

The thin CLI around ``labrat.agent.runner.run_agent_task``. Builds a multi-DB
ToolContext from a JSON connection spec, registers the standard data tools,
picks a provider, runs the agent loop, and emits JSON to stdout.

Usage::

    uv run python scripts/run_task.py \\
      --prompt "How many rows are in the orders table?" \\
      --connections '{"main": {"db_type": "duckdb", "db_path": "/path/to.duckdb"}}' \\
      --provider anthropic \\
      --model claude-sonnet-4-6

Output (stdout): one JSON object with ``final_text``, ``tool_calls``,
``latency_seconds``.

Providers:
    anthropic       — Anthropic SDK (needs ANTHROPIC_API_KEY)
    claude-code     — claude CLI subprocess (Max plan; hits the text-protocol
                      conflict for tool round-trips, included for parity)
    openai          — OpenAI-compatible API (needs OPENAI_API_KEY)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.providers import PROVIDER_NAMES, build_provider
from labrat.agent.runner import run_agent_task
from labrat.agent.tools.base import ToolContext
from labrat.db.duckdb_engine import DuckDBConnection

_DEFAULT_SYSTEM_PROMPT = (
    "You are a data analyst answering questions by querying databases. "
    "Use list_tables / describe_table / sample_rows to understand the schema, "
    "then run_sql to compute the answer. "
    "If multiple databases are configured, pass `database=<name>` to scope a "
    "tool call. "
    "Use attach_database to bring SQLite/Postgres/MySQL files into the primary "
    "DuckDB session for cross-database JOINs. "
    "When confident, respond with a plain final answer."
)


def _build_connections(spec: dict[str, dict[str, Any]]) -> dict[str, object]:
    conns: dict[str, object] = {}
    for name, meta in spec.items():
        db_type = str(meta.get("db_type", "")).lower()
        if db_type != "duckdb":
            raise SystemExit(
                f"run_task only supports db_type=duckdb in --connections today "
                f"(got {db_type!r} for {name!r}). Use attach_database from the "
                "agent for SQLite/Postgres/MySQL."
            )
        path = str(meta["db_path"])
        read_only = bool(meta.get("read_only", True))
        conn = DuckDBConnection(path=path, read_only=read_only)
        conn.connect()
        conns[name] = conn
    return conns


async def _main(args: argparse.Namespace) -> int:
    spec: dict[str, dict[str, Any]] = json.loads(args.connections)
    if not spec:
        raise SystemExit("--connections must contain at least one entry")

    connections = _build_connections(spec)
    primary = args.primary or next(iter(connections))
    if primary not in connections:
        raise SystemExit(f"--primary {primary!r} not in --connections keys")

    catalogs: dict[str, object] = {
        name: conn.introspect_catalog()  # type: ignore[attr-defined]
        for name, conn in connections.items()
    }
    ctx = ToolContext(connections=connections, catalogs=catalogs, primary=primary)
    # Standalone scripting path: crash loudly on a provider 429 instead of degrading.
    ctx.raise_rate_limits = True

    registry = build_data_tools_registry()
    provider = build_provider(args.provider, args.model)
    system_prompt = (
        Path(args.system_prompt_file).read_text(encoding="utf-8")
        if args.system_prompt_file
        else _DEFAULT_SYSTEM_PROMPT
    )

    result = await run_agent_task(
        prompt=args.prompt,
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt=system_prompt,
        max_turns=args.max_turns,
        max_tool_calls=args.max_tool_calls,
    )
    json.dump(result.model_dump(), sys.stdout)
    sys.stdout.write("\n")
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prompt", required=True, help="The user query.")
    p.add_argument(
        "--connections",
        required=True,
        help=(
            "JSON object mapping name → {db_type, db_path, ...}. "
            'Example: \'{"main": {"db_type": "duckdb", "db_path": "/tmp/a.duckdb"}}\''
        ),
    )
    p.add_argument(
        "--primary",
        default=None,
        help="Connection name to mark as primary; defaults to first key in --connections.",
    )
    p.add_argument(
        "--provider",
        default="anthropic",
        choices=list(PROVIDER_NAMES),
        help="Model provider. Default: anthropic (needs ANTHROPIC_API_KEY).",
    )
    p.add_argument("--model", default="claude-sonnet-4-6", help="Model id to pass to the provider.")
    p.add_argument(
        "--system-prompt-file",
        default=None,
        help="Optional path to a file whose contents replace the default system prompt.",
    )
    p.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Cap on assistant turns; default unbounded.",
    )
    p.add_argument(
        "--max-tool-calls",
        type=int,
        default=None,
        help=(
            "Cap on cumulative tool dispatches. AgentLoop drops overflow within the "
            "round and exits when reached. Default: unbounded."
        ),
    )
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parse_args())))
