"""Env-JSON + profile connection resolution for the LabRat MCP server.

Extracted from ``labrat.mcp.server._build_context_from_env`` so the parsing
logic is independently testable. Two additive sources, coexisting:

- ``LABRAT_MCP_CONNECTIONS`` (JSON, duckdb-only) — the legacy path, byte-
  compatible with the pre-extraction server and with DAB's ``claude-mcp``
  driver (Task 1).
- ``LABRAT_MCP_PROFILES`` (comma-separated profile names, Task 2) — resolves
  each name through ``labrat.profile.manager`` (``ProfileManager.get`` +
  ``make_connection``), so any of the seven adapters can be mounted with
  keyring-backed secrets. Connection keys are profile names; a name colliding
  with an env-JSON connection is a hard error (exit 2) checked before any
  connection is opened for that name.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from labrat.db.base import Connection
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.profile.manager import ProfileError, ProfileManager, make_connection
from labrat.profile.model import Profile


@dataclass(frozen=True)
class ResolvedConnections:
    """Everything a ToolContext needs, resolved from one or both config sources."""

    connections: dict[str, Connection]
    catalogs: dict[str, object]
    primary: str
    read_only: bool
    profile_name: str


def resolve_from_env(
    env: Mapping[str, str],
    *,
    manager_factory: Callable[[], ProfileManager] | None = None,
    connection_factory: Callable[[Profile], Connection] | None = None,
) -> ResolvedConnections:
    """Parse ``LABRAT_MCP_CONNECTIONS`` and/or ``LABRAT_MCP_PROFILES`` into a ResolvedConnections.

    The env-JSON half is a byte-compatible port of the former
    ``server.py::_build_context_from_env`` parsing body — same JSON parsing,
    same duckdb-only guard (same stderr message + ``sys.exit(2)``), same
    ``:memory:``-writable forcing, same ``LABRAT_MCP_PRIMARY`` validation.
    At least one of the two env vars must be present, or resolution fails
    exactly as the legacy "LABRAT_MCP_CONNECTIONS env var is required" path
    did before profiles existed.

    ``manager_factory``/``connection_factory`` are test seams (default
    ``None``, resolved at call time to the real ``ProfileManager``/
    ``make_connection`` module globals) — not meant to be overridden by
    production callers. Defaulting to ``None`` instead of binding the
    globals at def-time means a test can monkeypatch this module's
    ``ProfileManager``/``make_connection`` attributes directly (e.g.
    ``monkeypatch.setattr(mcp_config, "ProfileManager", ...)``) and have it
    take effect even when the caller doesn't pass the kwarg explicitly.

    ``ResolvedConnections.read_only`` (the ToolContext-level gate on mutating
    tools) is the OR of two independently-derived contributions (amended
    2026-07-09, T1 review — DAB byte-compat outranks safety-first on the
    legacy path):

    - env-JSON contribution: False unless every spec in
      ``LABRAT_MCP_CONNECTIONS`` explicitly sets ``"read_only": true``
      (omitted -> False, preserving today's open ctx for DAB — see Task 1's
      docstring/test for the full rationale). When there is no env-JSON spec
      at all, this contribution is False (not vacuously True) — there is
      nothing to have opted in.
    - profiles contribution: safety-first — True if ANY resolved profile has
      ``is_read_only=True`` (the ``make_profile``/``Profile`` default).

    This is unrelated to the per-connection DuckDB open-mode flag below
    (``meta.get("read_only", True)``), which governs how the on-disk file
    itself is opened and is preserved verbatim from the original
    implementation.

    ``profile_name``: the primary's profile name when the primary connection
    is profile-backed, else ``"default"`` (matches today's behavior for the
    env-JSON-only path).
    """
    raw = env.get("LABRAT_MCP_CONNECTIONS")
    profiles_raw = env.get("LABRAT_MCP_PROFILES")

    if not raw and not profiles_raw:
        print(
            "LABRAT_MCP_CONNECTIONS env var is required (JSON connection spec).",
            file=sys.stderr,
        )
        sys.exit(2)

    spec: dict[str, dict[str, Any]] = {}
    connections: dict[str, Connection] = {}
    catalogs: dict[str, object] = {}

    if raw:
        spec = json.loads(raw)
        if not spec:
            print("LABRAT_MCP_CONNECTIONS must contain at least one entry.", file=sys.stderr)
            sys.exit(2)

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

    profile_backed_names: set[str] = set()
    profiles_used: list[Profile] = []

    profile_names = [n.strip() for n in (profiles_raw or "").split(",") if n.strip()]
    if profile_names:
        manager = (manager_factory or ProfileManager)()
        factory = connection_factory or make_connection
        for pname in profile_names:
            if pname in connections:
                print(
                    f"Connection name {pname!r} is defined by both LABRAT_MCP_CONNECTIONS "
                    "and LABRAT_MCP_PROFILES.",
                    file=sys.stderr,
                )
                sys.exit(2)
            try:
                profile = manager.get(pname)
            except ProfileError as exc:
                print(f"Unknown profile {pname!r}: {exc}", file=sys.stderr)
                sys.exit(2)
            conn = factory(profile)
            conn.connect()
            connections[pname] = conn
            catalogs[pname] = conn.introspect_catalog()
            profile_backed_names.add(pname)
            profiles_used.append(profile)

    if not connections:
        # LABRAT_MCP_PROFILES parsed to zero names (e.g. " , ") with no
        # env-JSON either: the top guard only checks that the raw strings are
        # present/non-empty, not that they resolved to anything. Without this
        # check, `next(iter(connections))` below raises a bare StopIteration
        # instead of the same clear error as the missing-env case.
        print(
            "LABRAT_MCP_CONNECTIONS env var is required (JSON connection spec).",
            file=sys.stderr,
        )
        sys.exit(2)

    primary = env.get("LABRAT_MCP_PRIMARY") or next(iter(connections))
    if primary not in connections:
        print(
            f"LABRAT_MCP_PRIMARY={primary!r} not in connections {list(connections)}.",
            file=sys.stderr,
        )
        sys.exit(2)

    env_json_read_only = bool(spec) and all(
        spec_entry.get("read_only") is True for spec_entry in spec.values()
    )
    profiles_read_only = any(profile.is_read_only for profile in profiles_used)
    ctx_read_only = env_json_read_only or profiles_read_only

    profile_name = primary if primary in profile_backed_names else "default"

    return ResolvedConnections(
        connections=connections,
        catalogs=catalogs,
        primary=primary,
        read_only=ctx_read_only,
        profile_name=profile_name,
    )
