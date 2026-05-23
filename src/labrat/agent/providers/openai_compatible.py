"""OpenAI-compatible provider: covers OpenAI, Azure, LiteLLM, Ollama, etc."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider


class OpenAICompatibleProvider(ModelProvider):
    """Model provider for any OpenAI-compatible API endpoint.

    Works with OpenAI, Azure OpenAI, LiteLLM proxies, vLLM, Together,
    Fireworks, Ollama (``base_url="http://localhost:11434/v1"``),
    and corporate OpenAI-compatible gateways.
    """

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._api_key = api_key

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        import openai

        kwargs: dict[str, Any] = {}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        if self._api_key:
            kwargs["api_key"] = self._api_key

        client = openai.AsyncOpenAI(**kwargs)

        # Prepend system as a system message if provided
        all_messages: list[dict[str, Any]] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        # Convert Anthropic tool schema → OpenAI function schema
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for t in tools
        ]

        response = await client.chat.completions.create(
            model=self._model,
            messages=all_messages,  # type: ignore[arg-type]
            tools=openai_tools or openai.NOT_GIVEN,  # type: ignore[arg-type]
            max_tokens=4096,
        )

        async def _emit() -> AsyncIterator[ContentBlock]:
            choice = response.choices[0]
            msg = choice.message
            if msg.content:
                yield TextBlock(text=msg.content)
            if msg.tool_calls:
                for call in msg.tool_calls:
                    yield ToolUseBlock(
                        id=call.id,
                        name=call.function.name,  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue,reportUnknownArgumentType]
                        input=json.loads(call.function.arguments),  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue,reportUnknownArgumentType]
                    )

        return _emit()
