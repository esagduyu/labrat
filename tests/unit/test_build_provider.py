"""Tests for the build_provider factory, esp. the claude-code timeout override."""

from __future__ import annotations

import pytest

from labrat.agent.providers import build_provider
from labrat.agent.providers.claude_code import ClaudeCodeProvider


def test_claude_code_default_timeout() -> None:
    p = build_provider("claude-code", "claude-sonnet-4-6")
    assert isinstance(p, ClaudeCodeProvider)
    assert p._timeout == 120


def test_claude_code_timeout_override() -> None:
    p = build_provider("claude-code", "claude-sonnet-4-6", timeout=300)
    assert isinstance(p, ClaudeCodeProvider)
    assert p._timeout == 300


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown provider"):
        build_provider("nope", "m")
