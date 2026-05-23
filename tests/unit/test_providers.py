"""Tests for model provider implementations (M14.5).

Each provider is tested with a mock or against the real API (gated).
Provider conformance is verified against a shared test battery.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.providers.base import ModelProvider
from labrat.agent.providers.openai_compatible import OpenAICompatibleProvider

# ── conformance battery ───────────────────────────────────────────────────────


async def _run_conformance(provider: ModelProvider) -> list[ContentBlock]:
    """Collect all blocks from a single-turn stream call."""
    blocks: list[ContentBlock] = []
    stream = await provider.stream(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        system="You are a test assistant.",
    )
    async for block in stream:
        blocks.append(block)
    return blocks


# ── AnthropicProvider ─────────────────────────────────────────────────────────


async def test_anthropic_provider_is_model_provider() -> None:
    """AnthropicProvider is a ModelProvider subclass."""
    assert issubclass(AnthropicProvider, ModelProvider)


async def test_anthropic_provider_mock_text_response() -> None:
    """AnthropicProvider emits TextBlocks from a mocked API response."""
    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = "hello from mock"

    fake_response = MagicMock()
    fake_response.content = [fake_block]

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=fake_response)

        provider = AnthropicProvider()
        blocks = await _run_conformance(provider)

    assert len(blocks) == 1
    assert isinstance(blocks[0], TextBlock)
    assert blocks[0].text == "hello from mock"


async def test_anthropic_provider_mock_tool_use_response() -> None:
    """AnthropicProvider emits ToolUseBlocks for tool_use content."""
    fake_tool = MagicMock()
    fake_tool.type = "tool_use"
    fake_tool.id = "tu_123"
    fake_tool.name = "list_tables"
    fake_tool.input = {"schema_filter": None}

    fake_response = MagicMock()
    fake_response.content = [fake_tool]

    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create = AsyncMock(return_value=fake_response)

        provider = AnthropicProvider()
        blocks = await _run_conformance(provider)

    assert len(blocks) == 1
    assert isinstance(blocks[0], ToolUseBlock)
    assert blocks[0].name == "list_tables"
    assert blocks[0].id == "tu_123"


# ── OpenAICompatibleProvider ──────────────────────────────────────────────────


async def test_openai_provider_is_model_provider() -> None:
    """OpenAICompatibleProvider is a ModelProvider subclass."""
    assert issubclass(OpenAICompatibleProvider, ModelProvider)


async def test_openai_provider_mock_text_response() -> None:
    """OpenAICompatibleProvider emits TextBlocks from a mocked OpenAI response."""
    fake_message = MagicMock()
    fake_message.content = "hello from openai"
    fake_message.tool_calls = None

    fake_choice = MagicMock()
    fake_choice.message = fake_message

    fake_response = MagicMock()
    fake_response.choices = [fake_choice]

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

        provider = OpenAICompatibleProvider(model="gpt-4o-mini")
        blocks = await _run_conformance(provider)

    assert len(blocks) == 1
    assert isinstance(blocks[0], TextBlock)
    assert "openai" in blocks[0].text


async def test_openai_provider_mock_tool_use_response() -> None:
    """OpenAICompatibleProvider emits ToolUseBlocks for function calls."""
    import json

    fake_tool_call = MagicMock()
    fake_tool_call.id = "call_abc"
    fake_tool_call.function.name = "list_tables"
    fake_tool_call.function.arguments = json.dumps({"schema_filter": None})

    fake_message = MagicMock()
    fake_message.content = None
    fake_message.tool_calls = [fake_tool_call]

    fake_choice = MagicMock()
    fake_choice.message = fake_message

    fake_response = MagicMock()
    fake_response.choices = [fake_choice]

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=fake_response)

        provider = OpenAICompatibleProvider(model="gpt-4o-mini")
        blocks = await _run_conformance(provider)

    assert len(blocks) == 1
    assert isinstance(blocks[0], ToolUseBlock)
    assert blocks[0].name == "list_tables"


async def test_openai_provider_custom_base_url() -> None:
    """OpenAICompatibleProvider passes base_url to the AsyncOpenAI client."""
    fake_msg = MagicMock()
    fake_msg.content = "ok"
    fake_msg.tool_calls = None
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=fake_msg)]

    with patch("openai.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=fake_resp)

        provider = OpenAICompatibleProvider(base_url="http://localhost:11434/v1", model="llama3")
        await _run_conformance(provider)  # triggers stream() which instantiates AsyncOpenAI

        mock_cls.assert_called_once()
        _, kwargs = mock_cls.call_args
        assert kwargs.get("base_url") == "http://localhost:11434/v1"


# ── real API (gated) ──────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not __import__("os").environ.get("LABRAT_RUN_LLM_TESTS"),
    reason="Set LABRAT_RUN_LLM_TESTS=1 to run real API tests",
)
async def test_anthropic_provider_real_api() -> None:
    """End-to-end: AnthropicProvider returns at least one text block for a simple prompt."""
    provider = AnthropicProvider()
    blocks = await _run_conformance(provider)
    assert any(isinstance(b, TextBlock) for b in blocks)
