"""Plumbing tests for the hybrid-retrieval flag (T2b v2, T3)."""

from __future__ import annotations

from labrat.agent.session import _sub_ctx
from labrat.agent.tools.base import ToolContext
from labrat.profile.model import Profile


def test_tool_context_hybrid_retrieval_defaults_off() -> None:
    assert ToolContext().hybrid_retrieval is False


def test_sub_ctx_propagates_hybrid_retrieval() -> None:
    parent = ToolContext(hybrid_retrieval=True)
    assert _sub_ctx(parent).hybrid_retrieval is True
    assert _sub_ctx(ToolContext()).hybrid_retrieval is False


def test_profile_hybrid_retrieval_defaults_off_and_legacy_validates() -> None:
    legacy = Profile.model_validate({"name": "legacy", "dialect": "duckdb"})
    assert legacy.hybrid_retrieval is False
    on = Profile(name="p", dialect="duckdb", hybrid_retrieval=True)
    assert on.hybrid_retrieval is True
