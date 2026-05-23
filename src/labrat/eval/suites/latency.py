"""Latency benchmark suite (M26).

Measures time-to-first-token, time-to-SQL, and time-to-results for a small
set of canonical questions. These are fast cases with simple SQL so the wall
time reflects agent overhead, not query complexity.
"""

from __future__ import annotations

from labrat.eval.models import EvalCase

_CASES: list[EvalCase] = [
    EvalCase(
        id="lat-001",
        question="Show me the first 5 rows of the orders table.",
        expected_sql="SELECT * FROM orders LIMIT 5",
        domain="latency",
    ),
    EvalCase(
        id="lat-002",
        question="How many rows are in the users table?",
        expected_sql="SELECT COUNT(*) FROM users",
        domain="latency",
    ),
    EvalCase(
        id="lat-003",
        question="What are the column names in the products table?",
        expected_sql=None,
        domain="latency",
        rubric="Agent should use schema introspection, not a raw SQL query",
    ),
    EvalCase(
        id="lat-004",
        question="Select id and name from users, limit 10.",
        expected_sql="SELECT id, name FROM users LIMIT 10",
        domain="latency",
    ),
    EvalCase(
        id="lat-005",
        question="Return the current timestamp.",
        expected_sql="SELECT NOW()",
        domain="latency",
    ),
]


class LatencySuite:
    """Micro-benchmark suite for measuring agent latency on simple queries."""

    suite_name = "latency"
    cases: list[EvalCase] = _CASES
