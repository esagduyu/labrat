"""Claude Code CLI provider — uses 'claude -p' with Max subscription auth.

Requires the claude CLI to be installed and authenticated via one of:
  - CLAUDE_CODE_OAUTH_TOKEN env var  (from: claude setup-token)
  - Stored credentials                (from: claude auth login)

No ANTHROPIC_API_KEY required.

How it works:
  1. Serialises the full conversation history + tool schemas into a prompt.
  2. Runs `claude --print --output-format json` with the prompt on stdin.
  3. Parses Claude's text output: JSON tool-call → ToolUseBlock, else TextBlock.
  4. AgentLoop drives the round-trip exactly as with any other provider.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider

_DEFAULT_TIMEOUT = 120
_DEFAULT_MODEL = "claude-sonnet-4-6"


class ClaudeCodeProvider(ModelProvider):
    """ModelProvider that shells out to the claude CLI."""

    def __init__(self, model: str = _DEFAULT_MODEL, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._model = model
        self._timeout = timeout

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        if not shutil.which("claude"):
            raise RuntimeError(
                "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
            )

        prompt = _build_prompt(messages, tools, system)
        raw = await _run_claude_p(prompt, self._model, self._timeout)

        async def _emit() -> AsyncIterator[ContentBlock]:
            for block in _parse_response(raw):
                yield block

        return _emit()


# ── prompt construction ───────────────────────────────────────────────────────


def _build_prompt(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    system: str,
) -> str:
    parts: list[str] = []

    if system:
        parts.append(f"SYSTEM:\n{system}\n")

    if tools:
        parts.append("AVAILABLE TOOLS:")
        parts.append(
            "If you need to call a tool, respond with ONLY a single-line JSON object:\n"
            '  {"type":"tool_use","name":"<tool_name>","input":{...}}\n'
            "If your answer is complete (no more tool calls needed), respond with plain text."
        )
        for t in tools:
            schema_str = json.dumps(t.get("input_schema", {}))
            parts.append(
                f"\n  {t['name']}: {t.get('description', '')}\n  input_schema: {schema_str}"
            )
        parts.append("")

    parts.append("CONVERSATION:")
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if role == "user":
            if isinstance(content, str):
                parts.append(f"USER: {content}")
            elif isinstance(content, list):
                for raw_block in cast(list[dict[str, Any]], content):
                    btype: str = str(raw_block.get("type", ""))
                    if btype == "tool_result":
                        tid: str = str(raw_block.get("tool_use_id", ""))
                        res: str = str(raw_block.get("content", ""))
                        parts.append(f"TOOL_RESULT [{tid}]: {res}")
                    else:
                        parts.append(f"USER: {json.dumps(raw_block)}")

        elif role == "assistant":
            if isinstance(content, str):
                parts.append(f"ASSISTANT: {content}")
            elif isinstance(content, list):
                for raw_block in cast(list[dict[str, Any]], content):
                    btype = str(raw_block.get("type", ""))
                    if btype == "text":
                        parts.append(f"ASSISTANT: {raw_block.get('text', '')!s}")
                    elif btype == "tool_use":
                        call = json.dumps(
                            {
                                "type": "tool_use",
                                "name": raw_block.get("name"),
                                "input": raw_block.get("input", {}),
                            }
                        )
                        parts.append(f"ASSISTANT_TOOL_CALL: {call}")

    parts.append("\nASSISTANT:")
    return "\n".join(parts)


# ── subprocess ────────────────────────────────────────────────────────────────


async def _run_claude_p(prompt: str, model: str, timeout: int) -> str:
    """Pipe prompt to `claude --print --output-format json` and return its output."""
    import os

    # Strip ANTHROPIC_API_KEY so the CLI uses stored OAuth credentials (Max
    # subscription) rather than falling back to API-key billing.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    proc = await asyncio.create_subprocess_exec(
        "claude",
        "--print",
        "--output-format",
        "json",
        "--max-turns",
        "1",
        "--model",
        model,
        "--tools",
        "",  # "" = disable all built-in tools; we manage tool dispatch in AgentLoop
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        raise TimeoutError(f"claude --print timed out after {timeout}s") from None

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        out = stdout.decode(errors="replace").strip()
        # Extract human-readable message from JSON if present
        try:
            data = json.loads(out)
            if isinstance(data, dict) and "result" in data:
                out = str(data["result"])
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"claude CLI error: {err or out[:300]}")

    raw = stdout.decode(errors="replace").strip()

    # claude --output-format json wraps the response: {"type":"result","result":"..."}
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "result" in data:
            return str(cast(object, data["result"]))
    except json.JSONDecodeError:
        pass

    return raw


# ── response parsing ──────────────────────────────────────────────────────────


def _parse_response(text: str) -> list[ContentBlock]:
    """Detect a tool_use JSON call on any line; fall back to TextBlock."""
    stripped = text.strip()

    for line in stripped.splitlines():
        line = line.strip()
        if not (line.startswith("{") and '"tool_use"' in line):
            continue
        try:
            obj = json.loads(line)
            if obj.get("type") == "tool_use" and isinstance(obj.get("name"), str):
                return [
                    ToolUseBlock(
                        id=f"tu_{uuid.uuid4().hex[:8]}",
                        name=obj["name"],
                        input=obj.get("input") or {},
                    )
                ]
        except json.JSONDecodeError:
            continue

    return [TextBlock(text=stripped)]
