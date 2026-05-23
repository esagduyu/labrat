"""Anthropic direct provider using the official Anthropic Python SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider

_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096


class AnthropicProvider(ModelProvider):
    """Model provider backed by the Anthropic API (direct)."""

    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        self._model = model

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        import anthropic

        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=self._model,
            messages=messages,  # type: ignore[arg-type]
            tools=tools or [],  # type: ignore[arg-type]
            system=system if system else anthropic.NOT_GIVEN,  # type: ignore[arg-type]
            max_tokens=_MAX_TOKENS,
        )

        async def _emit() -> AsyncIterator[ContentBlock]:
            for block in response.content:
                if block.type == "text":
                    yield TextBlock(text=block.text)
                elif block.type == "tool_use":
                    yield ToolUseBlock(
                        id=block.id,
                        name=block.name,
                        input=dict(block.input),  # type: ignore[arg-type]
                    )

        return _emit()
