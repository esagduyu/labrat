#!/usr/bin/env python3
"""Generate Scent reference docs for a database (FEATURE_ROADMAP #26b, GENERATE).

Builds a DuckDB connection set from a JSON spec (like scripts/run_task.py), runs the
deterministic cartographer (profile + verified joins + dimensions), optionally adds an
LLM-drafted semantics pass, and writes one ``<domain>.md`` per connection into the
Scent store (default ``labrat_maze/scent/``).

Usage::

    uv run python scripts/cartograph.py \\
      --connections '{"shop": {"db_type": "duckdb", "db_path": "/path/to.duckdb"}}' \\
      --out labrat_maze/scent
    # add --with-semantics --provider anthropic --model claude-sonnet-4-6 for the LLM pass
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

from labrat.agent.providers import PROVIDER_NAMES, build_provider
from labrat.agent.verifier import LLMFn, provider_llm_fn
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import generate_scent, write_docs


def _build_connections(spec: dict[str, dict[str, Any]]) -> dict[str, object]:
    conns: dict[str, object] = {}
    for name, meta in spec.items():
        if str(meta.get("db_type", "")).lower() != "duckdb":
            raise SystemExit(
                f"cartograph supports db_type=duckdb only (got {meta.get('db_type')!r})."
            )
        conn = DuckDBConnection(
            path=str(meta["db_path"]), read_only=bool(meta.get("read_only", True))
        )
        conn.connect()
        conns[name] = conn
    return conns


async def _run(args: argparse.Namespace) -> int:
    spec: dict[str, dict[str, Any]] = json.loads(args.connections)
    if not spec:
        raise SystemExit("--connections must contain at least one entry")
    connections = _build_connections(spec)
    primary = args.primary or next(iter(connections))
    try:
        catalogs: dict[str, object] = {
            name: conn.introspect_catalog()  # type: ignore[attr-defined]
            for name, conn in connections.items()
        }

        llm_fn: LLMFn | None = None
        if args.with_semantics:
            llm_fn = provider_llm_fn(build_provider(args.provider, args.model))

        docs = await generate_scent(
            connections=connections,
            catalogs=catalogs,
            primary=primary,
            with_semantics=args.with_semantics,
            llm_fn=llm_fn,
            table_budget=args.table_budget,
            distinct_cap=args.distinct_cap,
        )
        paths = write_docs(docs, Path(args.out))
    finally:
        for conn in connections.values():
            conn.disconnect()  # type: ignore[attr-defined]

    for p in paths:
        print(f"wrote {p}")
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--connections", required=True, help="JSON: {name: {db_type, db_path, ...}}")
    p.add_argument("--primary", default=None, help="Primary connection name (default: first key).")
    p.add_argument(
        "--out", default="labrat_maze/scent", help="Output dir (default: labrat_maze/scent)."
    )
    p.add_argument("--with-semantics", action="store_true", help="Add the opt-in LLM draft pass.")
    p.add_argument("--provider", default="anthropic", choices=list(PROVIDER_NAMES))
    p.add_argument("--model", default="claude-sonnet-4-6")
    p.add_argument("--table-budget", type=int, default=40)
    p.add_argument("--distinct-cap", type=int, default=25)
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(_parse_args())))
