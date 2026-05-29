"""Minimal Connection fake for ToolContext tests."""

from __future__ import annotations

from typing import Any


class FakeConnection:
    def __init__(self, dialect: str) -> None:
        self.dialect = dialect

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError
