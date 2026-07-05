"""ContextLedger: mechanical budgets; over-budget payloads go to the ResultStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.agent.tools.base import DispatchResult
from labrat.agent.tools.serialization import render
from labrat.results.store import ResultStore
from labrat.runtime.context_ledger import ContextLedger, LedgerBudget


@pytest.fixture()
def store(tmp_path: Path) -> ResultStore:
    return ResultStore(tmp_path)


def test_budget_defaults_are_conservative() -> None:
    budget = LedgerBudget()
    assert budget.max_rows == 50
    assert budget.max_bytes == 8000


def test_under_budget_string_passes_through_byte_identical(store: ResultStore) -> None:
    ledger = ContextLedger(store)
    dispatch = DispatchResult(ok=True, value="echoed: hi")
    mvtr = ledger.record("echo", dispatch)
    assert mvtr.truncated is False
    assert mvtr.artifact_ref is None
    assert mvtr.preview == "echoed: hi"
    assert render(mvtr) == str(dispatch.value)  # exactly today's string


def test_over_budget_string_is_stored_and_bounded(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=50, max_bytes=64))
    big = "x" * 500
    mvtr = ledger.record("big_tool", DispatchResult(ok=True, value=big))
    assert mvtr.truncated is True
    assert len(mvtr.preview.encode("utf-8")) <= 64
    assert "big_tool" in mvtr.summary and "500-byte" in mvtr.summary
    assert mvtr.artifact_ref is not None
    assert store.get(mvtr.artifact_ref) == {"tool": "big_tool", "text": big}


def test_non_string_value_uses_str_like_today(store: ResultStore) -> None:
    ledger = ContextLedger(store)
    value = {"ok": True, "rows": [["1", "a"]]}
    mvtr = ledger.record("some_tool", DispatchResult(ok=True, value=value))
    assert mvtr.truncated is False
    assert render(mvtr) == str(value)
