"""Google Vertex AI provider (Anthropic Claude via Vertex)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider

_DEFAULT_MODEL = "claude-sonnet-4-6@20251101"
_DEFAULT_REGION = "us-east5"


class VertexProvider(ModelProvider):
    """Model provider backed by Google Vertex AI (Anthropic Claude via Vertex)."""

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        project: str | None = None,
        region: str = _DEFAULT_REGION,
    ) -> None:
        self._model = model
        self._project = project
        self._region = region

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        import anthropic

        client = anthropic.AsyncAnthropicVertex(  # pyright: ignore[reportPrivateImportUsage]
            project_id=self._project if self._project is not None else anthropic.NOT_GIVEN,
            region=self._region,
        )
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools

        response = await client.messages.create(**body)  # type: ignore[arg-type]

        async def _emit() -> AsyncIterator[ContentBlock]:
            for block in response.content:  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
                if block.type == "text":  # pyright: ignore[reportUnknownMemberType]
                    yield TextBlock(text=block.text)  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                elif block.type == "tool_use":  # pyright: ignore[reportUnknownMemberType]
                    yield ToolUseBlock(
                        id=block.id,  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                        name=block.name,  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                        input=dict(block.input),  # type: ignore[arg-type]
                    )

        return _emit()
