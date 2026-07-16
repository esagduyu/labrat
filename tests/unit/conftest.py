"""Shared unit-test doubles and fixtures."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from labrat.agent.providers.base import RateLimitError
from labrat.agent.tools.base import Tool, ToolContext


class _LimitedInput(BaseModel):
    message: str = ""


class LimitedTool(Tool[_LimitedInput]):
    """Test double: a tool whose execute() always raises the provider 429 signal."""

    @property
    def name(self) -> str:
        return "limited"

    @property
    def description(self) -> str:
        return "Always hits the provider rate limit."

    @property
    def input_model(self) -> type[_LimitedInput]:
        return _LimitedInput

    async def execute(self, ctx: ToolContext, args: _LimitedInput) -> object:
        raise RateLimitError()


@pytest.fixture()
def limited_tool() -> LimitedTool:
    return LimitedTool()
