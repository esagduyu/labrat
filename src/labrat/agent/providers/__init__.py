"""Model provider implementations and a small string→provider factory."""

from __future__ import annotations

from typing import Literal

from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.providers.base import ModelProvider
from labrat.agent.providers.claude_code import ClaudeCodeProvider
from labrat.agent.providers.openai_compatible import OpenAICompatibleProvider

ProviderName = Literal["anthropic", "claude-code", "openai"]
PROVIDER_NAMES: tuple[ProviderName, ...] = ("anthropic", "claude-code", "openai")


def build_provider(name: str, model: str, timeout: int | None = None) -> ModelProvider:
    """Map a CLI/config string to a concrete ModelProvider instance.

    Billing notes:
      anthropic   → metered API (needs ANTHROPIC_API_KEY with credits)
      claude-code → Max plan via the claude CLI subprocess (text-protocol;
                    hits the documented conflict for tool round-trips)
      openai      → metered OpenAI-compatible (needs OPENAI_API_KEY or
                    an OPENAI_BASE_URL/api_key pair)

    ``timeout`` (seconds) overrides the per-call subprocess timeout for the
    ``claude-code`` provider; the others manage their own HTTP timeouts and ignore
    it. ``None`` keeps the provider default.
    """
    if name == "anthropic":
        return AnthropicProvider(model=model)
    if name == "claude-code":
        if timeout is not None:
            return ClaudeCodeProvider(model=model, timeout=timeout)
        return ClaudeCodeProvider(model=model)
    if name == "openai":
        return OpenAICompatibleProvider(model=model)
    raise ValueError(f"Unknown provider {name!r}. Use one of {PROVIDER_NAMES}.")


__all__ = [
    "PROVIDER_NAMES",
    "AnthropicProvider",
    "ClaudeCodeProvider",
    "ModelProvider",
    "OpenAICompatibleProvider",
    "ProviderName",
    "build_provider",
]
