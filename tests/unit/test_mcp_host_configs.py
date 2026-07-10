"""Host config generators: exact shapes per host; env only for provided values."""

import json
import subprocess
import sys

import pytest

from labrat.mcp.host_configs import build_mcp_server_config, render_host_config


def test_server_block_env_only_provided() -> None:
    server = build_mcp_server_config(profiles=["w1", "w2"], log_dir="/logs")
    assert server["command"] == "uv"
    assert server["args"] == ["run", "python", "-m", "labrat.mcp.server"]
    assert server["env"] == {
        "LABRAT_MCP_PROFILES": "w1,w2",
        "LABRAT_MCP_LOG_DIR": "/logs",
    }


def test_no_source_raises() -> None:
    with pytest.raises(ValueError):
        build_mcp_server_config()


def test_claude_code_shape_round_trips() -> None:
    server = build_mcp_server_config(profiles=["w"])
    text = render_host_config("claude-code", server)
    parsed = json.loads(text)
    assert parsed["mcpServers"]["labrat"]["env"]["LABRAT_MCP_PROFILES"] == "w"


def test_codex_toml_shape() -> None:
    server = build_mcp_server_config(profiles=["w"])
    text = render_host_config("codex", server)
    assert "[mcp_servers.labrat]" in text
    assert 'command = "uv"' in text
    assert "[mcp_servers.labrat.env]" in text
    assert 'LABRAT_MCP_PROFILES = "w"' in text


def test_unknown_host_raises() -> None:
    with pytest.raises(ValueError):
        render_host_config("cursor", build_mcp_server_config(profiles=["w"]))


def test_cli_smoke() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "labrat.mcp.print_config",
            "--host",
            "claude-code",
            "--profiles",
            "w1",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["mcpServers"]["labrat"]["env"]["LABRAT_MCP_PROFILES"] == "w1"


def test_cli_bad_host_exit_2() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "labrat.mcp.print_config", "--host", "vim"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
