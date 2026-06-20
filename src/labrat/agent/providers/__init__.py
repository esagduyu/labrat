"""Model provider implementations and a small string→provider factory."""

from __future__ import annotations

from typing import Literal

from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.providers.base import ModelProvider
from labrat.agent.providers.claude_code import ClaudeCodeProvider
from labrat.agent.providers.codex_subscription import CodexSubscriptionProvider
from labrat.agent.providers.openai_compatible import OpenAICompatibleProvider

ProviderName = Literal["anthropic", "claude-code", "openai", "codex"]
PROVIDER_NAMES: tuple[ProviderName, ...] = ("anthropic", "claude-code", "openai", "codex")


def build_provider(
    name: str,
    model: str,
    timeout: int | None = None,
    reasoning: str | None = None,
) -> ModelProvider:
    """Map a CLI/config string to a concrete ModelProvider instance.

    Billing notes:
      anthropic   → metered API (needs ANTHROPIC_API_KEY with credits)
      claude-code → Max plan via the claude CLI subprocess (text-protocol;
                    hits the documented conflict for tool round-trips)
      openai      → metered OpenAI-compatible (needs OPENAI_API_KEY or
                    an OPENAI_BASE_URL/api_key pair)
      codex       → GPT-5.5 via the user's ChatGPT subscription (Codex Responses
                    API + ~/.codex/auth.json; no metered key). Personal/dev path.

    ``timeout`` (seconds) overrides the per-call subprocess timeout for the
    ``claude-code`` provider; the others manage their own HTTP timeouts and ignore
    it. ``reasoning`` sets the reasoning effort for the ``codex`` provider
    (default ``medium``); ignored by the others. ``None`` keeps provider defaults.
    """
    if name == "anthropic":
        return AnthropicProvider(model=model)
    if name == "claude-code":
        if timeout is not None:
            return ClaudeCodeProvider(model=model, timeout=timeout)
        return ClaudeCodeProvider(model=model)
    if name == "openai":
        return OpenAICompatibleProvider(model=model)
    if name == "codex":
        return CodexSubscriptionProvider(model=model, reasoning_effort=reasoning or "medium")
    raise ValueError(f"Unknown provider {name!r}. Use one of {PROVIDER_NAMES}.")


__all__ = [
    "PROVIDER_NAMES",
    "AnthropicProvider",
    "ClaudeCodeProvider",
    "CodexSubscriptionProvider",
    "ModelProvider",
    "OpenAICompatibleProvider",
    "ProviderName",
    "build_provider",
]
