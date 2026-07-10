"""Env-JSON connection resolution for the LabRat MCP server.

Extracted from ``labrat.mcp.server._build_context_from_env`` so the parsing
logic is independently testable and shareable with the (Task 2) profiles
path. This module owns only the ``LABRAT_MCP_CONNECTIONS`` /
``LABRAT_MCP_PRIMARY`` env-JSON path today; every message and exit path is a
byte-compatible port of the original ``server.py`` implementation.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from labrat.db.base import Connection
from labrat.db.duckdb_engine import DuckDBConnection


@dataclass(frozen=True)
class ResolvedConnections:
    """Everything a ToolContext needs, resolved from one config source."""

    connections: dict[str, Connection]
    catalogs: dict[str, object]
    primary: str
    read_only: bool
    profile_name: str


def resolve_from_env(env: Mapping[str, str]) -> ResolvedConnections:
    """Parse ``LABRAT_MCP_CONNECTIONS`` (+ ``LABRAT_MCP_PRIMARY``) into a ResolvedConnections.

    Byte-compatible port of the former ``server.py::_build_context_from_env``
    parsing body — same JSON parsing, same duckdb-only guard (same stderr
    message + ``sys.exit(2)``), same ``:memory:``-writable forcing, same
    ``LABRAT_MCP_PRIMARY`` validation.

    ``ResolvedConnections.read_only`` (the ToolContext-level gate on mutating
    tools) defaults to False when a spec omits the ``"read_only"`` key. This
    preserves TODAY's behavior byte-for-byte: the pre-extraction server never
    passed ``read_only`` to ``ToolContext`` at all (ToolContext's own default
    is False — see tools/base.py), and DAB's env builder
    (src/labrat/eval/benchmarks/dab/suite.py, LABRAT_MCP_CONNECTIONS
    construction ~line 1052) never sets a ``"read_only"`` key on its spec —
    so DAB trials must keep running with mutating tools (run_program,
    load_file, ...) open. The aggregate is only True when EVERY connection
    spec explicitly opts in with ``"read_only": true`` — the safety-first
    "True unless opted out" default belongs to the profiles path (Task 2)
    only, not this legacy env-JSON path. This is unrelated to the
    per-connection DuckDB open-mode flag below (``meta.get("read_only",
    True)``), which governs how the on-disk file itself is opened and is
    preserved verbatim from the original implementation.
    """
    raw = env.get("LABRAT_MCP_CONNECTIONS")
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

    connections: dict[str, Connection] = {}
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
        db_path = str(meta["db_path"])
        # An in-memory database cannot be opened read-only, and when it's the
        # primary it's the agent's writable workspace (attach_database / load_file
        # / load_mongo_collection materialize into it). Force read_only=False for
        # :memory: regardless of the spec default.
        conn_read_only = False if db_path == ":memory:" else bool(meta.get("read_only", True))
        conn = DuckDBConnection(path=db_path, read_only=conn_read_only)
        conn.connect()
        connections[name] = conn
        catalogs[name] = conn.introspect_catalog()

    primary = env.get("LABRAT_MCP_PRIMARY") or next(iter(connections))
    if primary not in connections:
        print(
            f"LABRAT_MCP_PRIMARY={primary!r} not in connections {list(connections)}.",
            file=sys.stderr,
        )
        sys.exit(2)

    ctx_read_only = all(spec_entry.get("read_only") is True for spec_entry in spec.values())

    return ResolvedConnections(
        connections=connections,
        catalogs=catalogs,
        primary=primary,
        read_only=ctx_read_only,
        profile_name="default",
    )
