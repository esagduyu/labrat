"""Abstract model provider interface."""

from __future__ import annotations

import re
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


def is_rate_limit_error(value: BaseException) -> bool:
    """Return True for the provider signal or an HTTP/textual 429 equivalent."""
    if isinstance(value, RateLimitError):
        return True
    response = getattr(value, "response", None)
    status = getattr(response, "status_code", None)
    if status == 429 or str(status) == "429":
        return True
    direct_status = getattr(value, "status_code", None)
    if direct_status == 429 or str(direct_status) == "429":
        return True
    text = str(value)
    return bool(
        re.search(
            r"(?i)(?:\b429\b.*(?:too many requests|rate.?limit)|"
            r"(?:too many requests|rate.?limit).*\b429\b)",
            text,
        )
    )


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
