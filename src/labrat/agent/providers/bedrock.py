"""AWS Bedrock provider (Anthropic via Bedrock)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider

_DEFAULT_MODEL = "anthropic.claude-sonnet-4-6-20251101-v1:0"
_DEFAULT_REGION = "us-east-1"


class BedrockProvider(ModelProvider):
    """Model provider backed by AWS Bedrock (Anthropic Claude via Bedrock)."""

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        region: str = _DEFAULT_REGION,
    ) -> None:
        self._model = model
        self._region = region

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        import asyncio

        import boto3  # type: ignore[import-untyped]

        def _invoke() -> list[dict[str, Any]]:
            client = boto3.client("bedrock-runtime", region_name=self._region)  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
            body: dict[str, Any] = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "messages": messages,
            }
            if system:
                body["system"] = system
            if tools:
                body["tools"] = tools
            import json

            response = client.invoke_model(  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
                modelId=self._model,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            return json.loads(response["body"].read())["content"]  # type: ignore[no-any-return]

        content_blocks = await asyncio.to_thread(_invoke)

        async def _emit() -> AsyncIterator[ContentBlock]:
            for block in content_blocks:
                if block.get("type") == "text":
                    yield TextBlock(text=block["text"])
                elif block.get("type") == "tool_use":
                    yield ToolUseBlock(
                        id=block["id"],
                        name=block["name"],
                        input=block["input"],
                    )

        return _emit()
