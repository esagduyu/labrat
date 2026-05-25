"""Tests for the ClaudeCodeProvider (claude -p subprocess backend)."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from labrat.agent.loop import TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.providers.claude_code import (
    ClaudeCodeProvider,
    _build_prompt,
    _parse_response,
)

# ── _build_prompt ─────────────────────────────────────────────────────────────


def test_build_prompt_includes_system() -> None:
    prompt = _build_prompt(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        system="You are a data agent.",
    )
    assert "You are a data agent." in prompt


def test_build_prompt_includes_user_message() -> None:
    prompt = _build_prompt(
        messages=[{"role": "user", "content": "show revenue"}],
        tools=[],
        system="",
    )
    assert "show revenue" in prompt


def test_build_prompt_includes_tool_schema() -> None:
    tools = [
        {
            "name": "list_tables",
            "description": "List all tables",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    prompt = _build_prompt(
        messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        system="",
    )
    assert "list_tables" in prompt
    assert "List all tables" in prompt


def test_build_prompt_includes_tool_call_in_history() -> None:
    messages = [
        {"role": "user", "content": "show revenue"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu_001",
                    "name": "list_tables",
                    "input": {},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_001",
                    "content": "orders, customers",
                }
            ],
        },
    ]
    prompt = _build_prompt(messages=messages, tools=[], system="")
    assert "list_tables" in prompt
    assert "orders, customers" in prompt


def test_build_prompt_ends_with_assistant_cue() -> None:
    prompt = _build_prompt(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        system="",
    )
    assert prompt.strip().endswith("ASSISTANT:")


# ── _parse_response ───────────────────────────────────────────────────────────


def test_parse_response_plain_text() -> None:
    blocks = _parse_response("Here is the answer.")
    assert len(blocks) == 1
    assert isinstance(blocks[0], TextBlock)
    assert blocks[0].text == "Here is the answer."


def test_parse_response_tool_use_json() -> None:
    line = json.dumps({"type": "tool_use", "name": "list_tables", "input": {}})
    blocks = _parse_response(line)
    assert len(blocks) == 1
    assert isinstance(blocks[0], ToolUseBlock)
    assert blocks[0].name == "list_tables"
    assert blocks[0].input == {}


def test_parse_response_tool_use_with_input() -> None:
    payload = {"type": "tool_use", "name": "run_sql", "input": {"sql": "SELECT 1"}}
    blocks = _parse_response(json.dumps(payload))
    assert isinstance(blocks[0], ToolUseBlock)
    assert blocks[0].input == {"sql": "SELECT 1"}


def test_parse_response_tool_use_id_generated() -> None:
    line = json.dumps({"type": "tool_use", "name": "list_tables", "input": {}})
    blocks = _parse_response(line)
    assert isinstance(blocks[0], ToolUseBlock)
    assert blocks[0].id.startswith("tu_")


def test_parse_response_tool_use_embedded_in_text() -> None:
    """Tool call JSON on its own line amid surrounding text."""
    tool_line = json.dumps({"type": "tool_use", "name": "list_tables", "input": {}})
    text = f"I'll look up the tables.\n{tool_line}\nProceeding..."
    blocks = _parse_response(text)
    assert any(isinstance(b, ToolUseBlock) for b in blocks)


def test_parse_response_invalid_json_falls_back_to_text() -> None:
    blocks = _parse_response('{"type": "tool_use", bad json}')
    assert isinstance(blocks[0], TextBlock)


def test_parse_response_empty_string() -> None:
    blocks = _parse_response("")
    assert len(blocks) == 1
    assert isinstance(blocks[0], TextBlock)


# ── ClaudeCodeProvider class ──────────────────────────────────────────────────


def test_claude_code_provider_is_model_provider() -> None:
    assert issubclass(ClaudeCodeProvider, ModelProvider)


async def test_claude_code_provider_raises_if_cli_missing() -> None:
    with patch("shutil.which", return_value=None):
        provider = ClaudeCodeProvider()
        with pytest.raises(RuntimeError, match="claude CLI not found"):
            await provider.stream(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                system="",
            )


async def test_claude_code_provider_text_response() -> None:
    """Provider yields TextBlock when claude outputs plain text."""
    fake_json = json.dumps({"type": "result", "subtype": "success", "result": "Hello!"})

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = fake_json.encode()
    mock_result.stderr = b""

    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        with patch("subprocess.run", return_value=mock_result):
            provider = ClaudeCodeProvider()
            stream = await provider.stream(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                system="",
            )
            blocks = [b async for b in stream]

    assert len(blocks) == 1
    assert isinstance(blocks[0], TextBlock)
    assert "Hello!" in blocks[0].text


async def test_claude_code_provider_tool_use_response() -> None:
    """Provider yields ToolUseBlock when claude outputs tool-call JSON."""
    tool_json = json.dumps({"type": "tool_use", "name": "list_tables", "input": {}})
    fake_json = json.dumps({"type": "result", "subtype": "success", "result": tool_json})

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = fake_json.encode()
    mock_result.stderr = b""

    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        with patch("subprocess.run", return_value=mock_result):
            provider = ClaudeCodeProvider()
            stream = await provider.stream(
                messages=[{"role": "user", "content": "what tables?"}],
                tools=[{"name": "list_tables", "description": "...", "input_schema": {}}],
                system="",
            )
            blocks = [b async for b in stream]

    assert len(blocks) == 1
    assert isinstance(blocks[0], ToolUseBlock)
    assert blocks[0].name == "list_tables"


async def test_claude_code_provider_nonzero_exit_raises() -> None:
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = b""
    mock_result.stderr = b"auth error"

    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        with patch("subprocess.run", return_value=mock_result):
            provider = ClaudeCodeProvider()
            with pytest.raises(RuntimeError, match=r"claude CLI error"):
                await provider.stream(
                    messages=[{"role": "user", "content": "hi"}],
                    tools=[],
                    system="",
                )


# ── real CLI test (gated) ─────────────────────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("LABRAT_RUN_LLM_TESTS"),
    reason="Set LABRAT_RUN_LLM_TESTS=1 to run real claude CLI tests",
)
async def test_claude_code_provider_real_cli() -> None:
    """End-to-end: ClaudeCodeProvider returns at least one block via real claude CLI."""
    import shutil

    if not shutil.which("claude"):
        pytest.skip("claude CLI not installed")

    provider = ClaudeCodeProvider()
    stream = await provider.stream(
        messages=[{"role": "user", "content": "Reply with exactly: hello"}],
        tools=[],
        system="You are a test assistant. Follow instructions exactly.",
    )
    blocks = [b async for b in stream]
    assert len(blocks) >= 1
    assert any(isinstance(b, TextBlock) for b in blocks)
