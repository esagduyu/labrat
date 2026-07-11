"""Abstract model provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from labrat.agent.loop import ContentBlock


RATE_LIMIT_MESSAGE = "Model provider rate limit reached; retry later."
_RATE_LIMIT_FIELDS = {"resets_at", "resets_in_seconds"}


class RateLimitError(RuntimeError):
    """Stable provider-level signal for retryable quota exhaustion."""

    def __init__(
        self,
        _message: str | None = None,
        *,
        response: Any | None = None,
        rate_limit: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(RATE_LIMIT_MESSAGE)
        self.response = response
        self.rate_limit = {
            key: value
            for key, value in (rate_limit or {}).items()
            if key in _RATE_LIMIT_FIELDS and isinstance(value, int) and not isinstance(value, bool)
        }


class ModelProvider(ABC):
    """Abstract interface for a model API backend.

    Each provider translates the generic messages/tools format into its
    native API call and yields content blocks as they stream in.
    """

    def bind_conversation(self) -> ModelProvider:
        """Return a provider bound to one independent conversation.

        Stateless providers can return ``self``. Stateful transports should return
        a lightweight child that isolates replay/continuation state while sharing
        credentials and aggregate usage with the parent provider.
        """
        return self

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        """Stream a model response as content blocks.

        Args:
            messages: Conversation history in Anthropic message format.
            tools: Tool schemas (Anthropic format).
            system: System prompt text.

        Yields:
            TextBlock or ToolUseBlock instances as they become available.
        """
