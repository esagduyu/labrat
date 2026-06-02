"""Agent loop: drives tool-use round-trips between the model and the registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.verifier import Verifier

# ── content block types ───────────────────────────────────────────────────────


@dataclass
class TextBlock:
    """A text segment from the model."""

    text: str


@dataclass
class ToolUseBlock:
    """A tool invocation requested by the model."""

    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResultBlock:
    """A tool result to send back to the model."""

    tool_use_id: str
    content: str


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


# ── agent loop ────────────────────────────────────────────────────────────────


class AgentLoop:
    """Drives model ↔ tool round-trips.

    Call ``run(user_message)`` to start a turn. The loop will:
    1. Send the message to the model via the provider.
    2. For each tool_use block, dispatch via the registry and append the result.
    3. Repeat until the model returns a response with no tool_use blocks.

    Text blocks are forwarded to the ``on_text`` callback as they arrive.
    """

    def __init__(
        self,
        *,
        provider: Any,  # ModelProvider — typed as Any to avoid import cycle at runtime
        registry: ToolRegistry,
        ctx: ToolContext,
        system: str = "",
        dialect: str = "duckdb",
        max_turns: int | None = None,
        max_tool_calls: int | None = None,
        verifier: Verifier | None = None,
        max_verify_rounds: int = 2,
    ) -> None:
        from labrat.agent.prompts import build_system_prompt
        from labrat.agent.providers.base import ModelProvider  # deferred import

        if not isinstance(provider, ModelProvider):
            raise TypeError(f"Expected ModelProvider, got {type(provider)}")

        self._provider = provider
        self._registry = registry
        self._ctx = ctx
        self._system = system or build_system_prompt(dialect)
        self._max_turns = max_turns
        self._max_tool_calls = max_tool_calls
        self._verifier = verifier
        self._max_verify_rounds = max_verify_rounds
        self.history: list[dict[str, Any]] = []
        # Counters reset by run(); exposed so callers can inspect what was used.
        self.turns_used = 0
        self.tool_calls_used = 0
        self.verify_rounds_used = 0

    async def run(
        self,
        user_message: str,
        *,
        on_text: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        """Process a single user turn, handling any tool call round-trips.

        Caps:
          - ``max_turns`` (set on the loop) limits the number of assistant rounds.
            When reached, the loop exits before issuing the next provider call.
          - ``max_tool_calls`` (set on the loop) limits cumulative tool dispatches.
            Within a round, tools are dispatched up to the remaining budget; any
            extras emitted by the model are dropped and the loop exits.
        """
        self.history.append({"role": "user", "content": user_message})
        self.turns_used = 0
        self.tool_calls_used = 0
        self.verify_rounds_used = 0

        while True:
            if self._max_turns is not None and self.turns_used >= self._max_turns:
                break

            text_parts: list[str] = []
            tool_uses: list[ToolUseBlock] = []

            stream = await self._provider.stream(
                messages=self.history,
                tools=self._registry.to_anthropic_schemas(),
                system=self._system,
            )
            async for block in stream:
                if isinstance(block, TextBlock):
                    if on_text is not None:
                        on_text(block.text)
                    text_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_uses.append(block)

            self.turns_used += 1

            content: list[dict[str, Any]] = []
            if text_parts:
                content.append({"type": "text", "text": "".join(text_parts)})
            for tu in tool_uses:
                content.append(
                    {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input}
                )
            self.history.append({"role": "assistant", "content": content})

            if not tool_uses:
                if await self._verify_and_maybe_continue(
                    user_message, "".join(text_parts), on_status
                ):
                    continue  # verifier asked for another pass
                break  # no more tool calls — done

            # Dispatch up to the remaining tool-call budget. If the model emitted
            # more than the budget allows, drop the overflow and exit the loop.
            tool_result_content: list[dict[str, Any]] = []
            dispatched_all = True
            for tu in tool_uses:
                if (
                    self._max_tool_calls is not None
                    and self.tool_calls_used >= self._max_tool_calls
                ):
                    dispatched_all = False
                    break
                dispatch = await self._registry.dispatch(tu.name, tu.input, self._ctx)
                tool_result_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": (
                            str(dispatch.value) if dispatch.ok else f"Error: {dispatch.error}"
                        ),
                    }
                )
                self.tool_calls_used += 1

            if tool_result_content:
                self.history.append({"role": "user", "content": tool_result_content})

            if not dispatched_all:
                break  # budget exhausted; stop instead of continuing partial state

    async def _verify_and_maybe_continue(
        self,
        question: str,
        answer: str,
        on_status: Callable[[str], None] | None,
    ) -> bool:
        """Run the verifier on a would-be-final answer.

        Returns True if the answer was judged insufficient and feedback was appended
        as a new user turn (the loop should continue); False if there's no verifier,
        the round budget is spent, the turn budget is spent, or the answer passed.
        """
        if self._verifier is None or self.verify_rounds_used >= self._max_verify_rounds:
            return False
        # Don't re-prompt if we couldn't afford to answer the feedback anyway.
        if self._max_turns is not None and self.turns_used >= self._max_turns:
            return False

        verdict = await self._verifier.verify(
            question=question, answer=answer, transcript=self.history
        )
        if verdict.sufficient:
            return False

        self.verify_rounds_used += 1
        if on_status is not None:
            on_status(f"verifier: insufficient — {verdict.feedback}")
        self.history.append(
            {
                "role": "user",
                "content": (
                    "A reviewer checked your answer and judged it INSUFFICIENT:\n"
                    f"{verdict.feedback}\n\n"
                    "Address this — use tools to verify if needed — and produce a "
                    "corrected, complete final answer."
                ),
            }
        )
        return True
