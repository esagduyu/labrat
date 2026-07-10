"""E2E: parent dispatches; sub-loop is scoped; result returns via the parent ledger.

Drives a REAL parent AgentLoop (via build_agent_session) through a REAL dispatch
into a REAL sub-loop, using a scripted provider whose stream() calls are shared
between the parent loop and the sub-loop (they're the same provider instance —
build_agent_session's injected subagent_runner closure reuses `provider`). The
scripted-provider shape mirrors tests/unit/test_agent_runner_ledger.py's
_CapturingProvider: snapshot messages per call, replay a pre-scripted block list.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.session import build_agent_session
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry
from labrat.agent.tools.dispatch_subagent import DispatchSubagentTool


class _EchoInput(BaseModel):
    text: str = ""


class _EchoTool(Tool[_EchoInput]):
    """Minimal fake tool for the sub-loop to call, so its trace is observable."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the input text back."

    @property
    def input_model(self) -> type[_EchoInput]:
        return _EchoInput

    async def execute(self, ctx: ToolContext, args: _EchoInput) -> str:
        return args.text


class _ScriptedProvider(ModelProvider):
    """Scripted provider that snapshots the messages of every stream() call.

    A single instance is shared by the parent loop and every sub-loop it spawns
    (build_agent_session's subagent_runner closure reuses the same `provider`),
    so `script` is one flat, globally-ordered list of per-call block lists and
    `calls` records the messages seen on each call in that same global order.
    """

    def __init__(self, script: list[list[ContentBlock]]) -> None:
        self._script = script
        self._call = 0
        self.calls: list[list[dict[str, Any]]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        self.calls.append(list(messages))
        blocks = self._script[self._call]
        self._call += 1

        async def _emit() -> AsyncIterator[ContentBlock]:
            for b in blocks:
                yield b

        return _emit()


async def test_dispatch_e2e_scoped_and_ledgered(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [
            # call 0: parent turn 1 — delegates to a sub-agent.
            [
                ToolUseBlock(
                    id="t1",
                    name="dispatch_subagent",
                    input={"sub_task": "count the widgets", "max_turns": 2},
                )
            ],
            # call 1: sub-loop turn 1 — answers directly, no further tool use.
            [TextBlock(text="SUB ANSWER: 42")],
            # call 2: parent turn 2 — closes out using the sub-loop's answer.
            [TextBlock(text="parent done")],
        ]
    )
    registry = ToolRegistry()
    registry.register(DispatchSubagentTool())
    ctx = ToolContext()
    loop = build_agent_session(
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="parent system",
        ledger_dir=tmp_path / "ledger",
    )
    texts: list[str] = []
    await loop.run("PARENT TASK: delegate the widget count", on_text=texts.append)

    # 1. Parent completed and the dispatch counted against the parent budget
    #    (the sub-loop's own tool calls, if any, are NOT the parent's budget).
    assert loop.tool_calls_used == 1
    assert "parent done" in "".join(texts)

    # 2. The sub-loop's provider call saw ONLY the seed — never parent history.
    sub_messages = provider.calls[1]  # second stream() call = sub-loop turn 1
    flat = str(sub_messages)
    assert "count the widgets" in flat
    assert "PARENT TASK" not in flat  # scoped: parent user msg absent

    # 3. The sub answer reached the parent as a tool result (via history).
    parent_followup = provider.calls[2]
    assert "SUB ANSWER: 42" in str(parent_followup)


async def test_oversized_sub_result_returns_by_ref(tmp_path: Path) -> None:
    big_answer = "X" * 20_000
    provider = _ScriptedProvider(
        [
            [
                ToolUseBlock(
                    id="t1",
                    name="dispatch_subagent",
                    input={"sub_task": "produce a huge answer", "max_turns": 2},
                )
            ],
            [TextBlock(text=big_answer)],
            [TextBlock(text="parent done")],
        ]
    )
    registry = ToolRegistry()
    registry.register(DispatchSubagentTool())
    ctx = ToolContext()
    loop = build_agent_session(
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="parent system",
        ledger_dir=tmp_path / "ledger",
    )
    await loop.run("PARENT TASK: delegate", on_text=lambda _t: None)

    followup = str(provider.calls[2])
    assert "artifact_ref: result://" in followup
    assert big_answer not in followup
    assert any((tmp_path / "ledger").rglob("*")), "artifact file written"


async def test_dispatch_unknown_inside_subloop(tmp_path: Path) -> None:
    """Depth-1 guard #1, end to end: the sub-loop's registry lacks dispatch_subagent.

    build_agent_session._sub_registry derives the sub-loop's registry from the
    HOSTING registry minus dispatch_subagent — so when the sub-loop's own
    scripted turn attempts to call dispatch_subagent, ToolRegistry.dispatch
    can't find it and returns the registry's unknown-tool error. The sub-loop
    must recover (it's just a failed tool call, not a crash) and finish
    normally, and the parent must complete on top of that.
    """
    provider = _ScriptedProvider(
        [
            # call 0: parent turn 1 — delegates.
            [
                ToolUseBlock(
                    id="t1",
                    name="dispatch_subagent",
                    input={"sub_task": "nested dispatch attempt", "max_turns": 3},
                )
            ],
            # call 1: sub-loop turn 1 — illegally tries to dispatch a sub-sub-agent.
            [
                ToolUseBlock(
                    id="t2",
                    name="dispatch_subagent",
                    input={"sub_task": "should never run"},
                )
            ],
            # call 2: sub-loop turn 2 — recovers from the error, answers with text.
            [TextBlock(text="sub done")],
            # call 3: parent turn 2 — closes out.
            [TextBlock(text="parent done")],
        ]
    )
    registry = ToolRegistry()
    registry.register(DispatchSubagentTool())
    ctx = ToolContext()
    loop = build_agent_session(
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="parent system",
        ledger_dir=tmp_path / "ledger",
    )
    texts: list[str] = []
    await loop.run("PARENT TASK: try nested dispatch", on_text=texts.append)

    # The parent only ever dispatched ONE tool call (the outer dispatch_subagent);
    # the sub-loop's failed nested attempt is not on the parent's registry/budget.
    assert loop.tool_calls_used == 1
    assert "parent done" in "".join(texts)

    # The sub-loop's SECOND stream() call must have seen the unknown-tool error
    # as a tool_result, quoted exactly as ToolRegistry.dispatch produces it.
    sub_followup = provider.calls[2]  # sub-loop turn 2 = global call index 2
    assert "Unknown tool: 'dispatch_subagent'" in str(sub_followup)


async def test_sub_loop_traces_forwarded_with_prefix(tmp_path: Path) -> None:
    """R1: the parent's on_tool_call hook observes sub-loop dispatches, tagged."""
    provider = _ScriptedProvider(
        [
            # call 0: parent turn 1 — delegates.
            [
                ToolUseBlock(
                    id="t1",
                    name="dispatch_subagent",
                    input={"sub_task": "echo something", "max_turns": 2},
                )
            ],
            # call 1: sub-loop turn 1 — calls the echo tool.
            [ToolUseBlock(id="t2", name="echo", input={"text": "hi"})],
            # call 2: sub-loop turn 2 — answers with text.
            [TextBlock(text="sub done")],
            # call 3: parent turn 2 — closes out.
            [TextBlock(text="parent done")],
        ]
    )
    registry = ToolRegistry()
    registry.register(DispatchSubagentTool())
    registry.register(_EchoTool())
    ctx = ToolContext()
    loop = build_agent_session(
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="parent system",
        ledger_dir=tmp_path / "ledger",
    )
    events: list[str] = []

    def on_tool_call(
        name: str, args: dict[str, Any], ok: bool, output: str, latency_ms: float
    ) -> None:
        events.append(name)

    await loop.run("PARENT: delegate", on_tool_call=on_tool_call)

    assert "subagent:echo" in events  # sub-loop activity visible, tagged
    assert "dispatch_subagent" in events  # parent's own event untagged
    assert not any(e.startswith("subagent:dispatch") for e in events)
    assert loop.active_on_tool_call is None  # cleared after run (no leak)


async def test_no_forwarding_without_parent_hook(tmp_path: Path) -> None:
    """R1: no parent on_tool_call → the sub-loop still runs fine, nothing forwarded."""
    provider = _ScriptedProvider(
        [
            [
                ToolUseBlock(
                    id="t1",
                    name="dispatch_subagent",
                    input={"sub_task": "echo something", "max_turns": 2},
                )
            ],
            [ToolUseBlock(id="t2", name="echo", input={"text": "hi"})],
            [TextBlock(text="sub done")],
            [TextBlock(text="parent done")],
        ]
    )
    registry = ToolRegistry()
    registry.register(DispatchSubagentTool())
    registry.register(_EchoTool())
    ctx = ToolContext()
    loop = build_agent_session(
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="parent system",
        ledger_dir=tmp_path / "ledger",
    )
    texts: list[str] = []
    await loop.run("PARENT: delegate", on_text=texts.append)

    assert "parent done" in "".join(texts)
    assert loop.active_on_tool_call is None
