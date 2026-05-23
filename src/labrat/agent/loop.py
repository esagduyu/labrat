"""Agent loop: drives tool-use round-trips between the model and the registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from labrat.agent.tools.base import ToolContext, ToolRegistry

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
    ) -> None:
        from labrat.agent.prompts import build_system_prompt
        from labrat.agent.providers.base import ModelProvider  # deferred import

        if not isinstance(provider, ModelProvider):
            raise TypeError(f"Expected ModelProvider, got {type(provider)}")

        self._provider = provider
        self._registry = registry
        self._ctx = ctx
        self._system = system or build_system_prompt(dialect)
        self.history: list[dict[str, Any]] = []

    async def run(
        self,
        user_message: str,
        *,
        on_text: Callable[[str], None] | None = None,
    ) -> None:
        """Process a single user turn, handling any tool call round-trips."""
        self.history.append({"role": "user", "content": user_message})

        while True:
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

            # Record the assistant turn in history
            content: list[dict[str, Any]] = []
            if text_parts:
                content.append({"type": "text", "text": "".join(text_parts)})
            for tu in tool_uses:
                content.append(
                    {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input}
                )
            self.history.append({"role": "assistant", "content": content})

            if not tool_uses:
                break  # no more tool calls — done

            # Dispatch all tool calls and send results back
            tool_result_content: list[dict[str, Any]] = []
            for tu in tool_uses:
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
            self.history.append({"role": "user", "content": tool_result_content})
