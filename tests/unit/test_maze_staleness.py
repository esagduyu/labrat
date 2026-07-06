# tests/unit/test_maze_staleness.py
from __future__ import annotations

from labrat.maze.staleness import is_stale, schema_fingerprint


def test_fingerprint_is_order_independent() -> None:
    a = schema_fingerprint({"orders": ["id", "total"], "users": ["id"]})
    b = schema_fingerprint({"users": ["id"], "orders": ["total", "id"]})
    assert a == b


def test_staleness_detection() -> None:
    fp = schema_fingerprint({"orders": ["id", "total"]})
    assert is_stale(fp, fp) is False
    assert is_stale("oldhash", fp) is True
    assert is_stale(None, fp) is False  # no baseline → not flagged
