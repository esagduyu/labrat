"""Spike: confirm ClaudeCodeProvider round-trips tool calls from a custom registry.

Exit 0 on success, 1 on failure. Prints diagnostic output.

Key API differences vs. the plan pseudocode (confirmed by reading source):
- AgentLoop.__init__ kwargs: provider, registry, ctx  (NOT tool_registry)
- AgentLoop.run(user_message) returns None; inspect loop.history after
- ToolRegistry lives in labrat.agent.tools.base (no separate registry.py)
- Tool.execute(ctx, args) — param named "args", not "input"
- DispatchResult is a plain dataclass: DispatchResult(ok=..., value=..., error=...)
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from pydantic import BaseModel

from labrat.agent.loop import AgentLoop
from labrat.agent.providers.claude_code import ClaudeCodeProvider
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry

# ── toy tools ─────────────────────────────────────────────────────────────────


class AddInput(BaseModel):
    a: int
    b: int


class AddTool(Tool[AddInput]):
    @property
    def name(self) -> str:
        return "add"

    @property
    def description(self) -> str:
        return "Add two integers and return the sum."

    @property
    def input_model(self) -> type[AddInput]:
        return AddInput

    async def execute(self, ctx: ToolContext, args: AddInput) -> object:
        return {"sum": args.a + args.b}


class EchoInput(BaseModel):
    text: str


class EchoTool(Tool[EchoInput]):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo a string back verbatim."

    @property
    def input_model(self) -> type[EchoInput]:
        return EchoInput

    async def execute(self, ctx: ToolContext, args: EchoInput) -> object:
        return {"echoed": args.text}


# ── main ──────────────────────────────────────────────────────────────────────


async def main() -> int:
    registry = ToolRegistry()
    registry.register(AddTool())
    registry.register(EchoTool())

    provider = ClaudeCodeProvider()

    # ToolContext requires connection and catalog; use stub objects for this spike.
    # The toy tools above don't touch ctx at all, so any sentinel is fine.
    class _Stub:
        pass

    ctx = ToolContext(connection=_Stub(), catalog=_Stub())

    loop = AgentLoop(provider=provider, registry=registry, ctx=ctx)

    prompt = (
        "Use the add tool to compute 17 + 25, then use echo to echo the answer as 'result: <n>'."
    )

    # Collect text emitted during the run.
    text_parts: list[str] = []

    def on_text(t: str) -> None:
        text_parts.append(t)

    await loop.run(prompt, on_text=on_text)

    final_text = "".join(text_parts)

    # Inspect history to reconstruct tool call log.
    tool_call_log: list[dict[str, Any]] = []
    for msg in loop.history:
        if msg["role"] == "assistant":
            content = msg["content"]
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_call_log.append(
                            {"tool": block.get("name"), "input": block.get("input")}
                        )

    print("=== Final text ===")
    print(final_text)
    print("=== Tool call log ===")
    for entry in tool_call_log:
        print(entry)
    print("=== Full history ===")
    for msg in loop.history:
        print(msg)

    add_called = any(c.get("tool") == "add" for c in tool_call_log)
    echo_called = any(c.get("tool") == "echo" for c in tool_call_log)
    has_42 = "42" in final_text

    if add_called and echo_called and has_42:
        print("\nSPIKE PASS: both tools were called and final answer contains '42'.")
        return 0
    else:
        print(f"\nSPIKE FAIL: add_called={add_called} echo_called={echo_called} has_42={has_42}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
