"""Minimal Catalog fake for ToolContext tests."""

from __future__ import annotations

from typing import ClassVar


class FakeCatalog:
    schemas: ClassVar[list[str]] = []
