"""Host config generators for mounting the LabRat MCP server.

Pure string/dict building — no I/O. ``build_mcp_server_config`` produces the
generic ``{"command", "args", "env"}`` server block consumed by every host
(``LABRAT_MCP_PROFILES`` / ``LABRAT_MCP_CONNECTIONS`` / ``LABRAT_MCP_PRIMARY``
/ ``LABRAT_MCP_LOG_DIR`` — env holds only the values actually provided, same
env-var contract as ``labrat.mcp.config.resolve_from_env`` /
``labrat.mcp.server``). ``render_host_config`` wraps that block into the
host-specific config text: ``claude-code``/``generic`` emit the
``mcpServers`` JSON shape Claude Code (and most other JSON-config hosts)
expect; ``codex`` emits Codex's TOML ``[mcp_servers.labrat]`` table shape.
"""

from __future__ import annotations

import json
from typing import cast


def build_mcp_server_config(
    *,
    profiles: list[str] | None = None,
    connections_json: str | None = None,
    primary: str | None = None,
    log_dir: str | None = None,
) -> dict[str, object]:
    """Build the generic ``{command, args, env}`` server block.

    ``env`` holds only the vars actually provided — no empty/placeholder
    entries. At least one of ``profiles``/``connections_json`` must be given
    (mirrors ``resolve_from_env``'s "at least one source" requirement);
    otherwise the server would fail to start with neither, so we raise here
    instead of generating a config that's guaranteed to error at runtime.
    """
    if not profiles and not connections_json:
        raise ValueError(
            "build_mcp_server_config requires at least one of profiles/connections_json"
        )

    env: dict[str, str] = {}
    if profiles:
        env["LABRAT_MCP_PROFILES"] = ",".join(profiles)
    if connections_json:
        env["LABRAT_MCP_CONNECTIONS"] = connections_json
    if primary:
        env["LABRAT_MCP_PRIMARY"] = primary
    if log_dir:
        env["LABRAT_MCP_LOG_DIR"] = log_dir

    return {
        "command": "uv",
        "args": ["run", "python", "-m", "labrat.mcp.server"],
        "env": env,
    }


def render_host_config(host: str, server: dict[str, object]) -> str:
    """Render ``server`` into the config text a given host expects.

    ``claude-code``/``generic`` -> JSON ``{"mcpServers": {"labrat": server}}``.
    ``codex`` -> TOML text (``[mcp_servers.labrat]`` + an env sub-table),
    string-built (no TOML-writer dependency). Unknown host -> ``ValueError``.
    """
    if host in ("claude-code", "generic"):
        return json.dumps({"mcpServers": {"labrat": server}}, indent=2)
    if host == "codex":
        return _render_codex_toml(server)
    raise ValueError(f"Unknown host {host!r}; expected claude-code, generic, or codex")


def _toml_string(value: str) -> str:
    # TOML basic strings can't hold raw control characters (even escaped),
    # so reject those loudly rather than emit broken TOML — this is the only
    # class of input we can't represent. Everything else (including `"` and
    # `\`) is escaped per the TOML basic-string rules below.
    if any(ord(c) < 0x20 for c in value):
        raise ValueError(
            f"value contains a control character, not supported in TOML output: {value!r}"
        )
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_codex_toml(server: dict[str, object]) -> str:
    command = server["command"]
    args = server["args"]
    env = server["env"]
    assert isinstance(command, str)
    assert isinstance(args, list)
    assert isinstance(env, dict)
    args_typed = cast(list[object], args)
    env_typed = cast(dict[str, object], env)

    lines = ["[mcp_servers.labrat]"]
    lines.append(f"command = {_toml_string(command)}")
    args_str = ", ".join(_toml_string(str(a)) for a in args_typed)
    lines.append(f"args = [{args_str}]")
    if env_typed:
        lines.append("")
        lines.append("[mcp_servers.labrat.env]")
        for key, value in env_typed.items():
            lines.append(f"{key} = {_toml_string(str(value))}")
    return "\n".join(lines) + "\n"
