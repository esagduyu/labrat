"""ContextLedger: mechanical budgets; over-budget payloads go to the ResultStore."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from labrat.agent.tools.base import DispatchResult
from labrat.agent.tools.serialization import LedgerPayloadKind, render
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


class _HookedTable:
    """Minimal tool-output stand-in exposing a DataFrame via the contract."""

    def __init__(self, df: pl.DataFrame) -> None:
        self._df = df

    def __str__(self) -> str:
        return f"rows={self._df.rows()}"

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("table", self._df)


class _HookedJson:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    def __str__(self) -> str:
        return f"payload={self._obj}"

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("json", self._obj)


class _HookedTrace:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def __str__(self) -> str:
        return f"trace={self._items}"

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("trace", self._items)


class _MalformedHook:
    def __str__(self) -> str:
        return "malformed-but-small"

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("table", "not a dataframe")


def test_under_budget_table_passes_through_with_row_count(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=5, max_bytes=8000))
    value = _HookedTable(pl.DataFrame({"a": [1, 2]}))
    mvtr = ledger.record("run_sql", DispatchResult(ok=True, value=value))
    assert mvtr.truncated is False
    assert mvtr.full_row_count == 2
    assert render(mvtr) == str(value)


def test_over_row_budget_table_stored_and_previewed(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=5, max_bytes=8000))
    df = pl.DataFrame({"a": list(range(10)), "b": ["v"] * 10})
    mvtr = ledger.record("run_sql", DispatchResult(ok=True, value=_HookedTable(df)))
    assert mvtr.truncated is True
    assert mvtr.full_row_count == 10
    assert len(mvtr.preview.splitlines()) == 6  # header + max_rows
    assert "run_sql: 10 rows" in mvtr.summary and "a, b" in mvtr.summary
    assert mvtr.artifact_ref is not None
    stored = store.get(mvtr.artifact_ref)
    assert isinstance(stored, pl.DataFrame) and stored.equals(df)
    meta = store.meta(mvtr.artifact_ref)
    assert meta is not None and meta["tool"] == "run_sql"


def test_over_byte_budget_table_triggers_even_under_row_cap(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=50, max_bytes=64))
    df = pl.DataFrame({"a": ["y" * 200]})  # 1 row, but str(value) > 64 bytes
    mvtr = ledger.record("run_sql", DispatchResult(ok=True, value=_HookedTable(df)))
    assert mvtr.truncated is True
    assert len(mvtr.preview.encode("utf-8")) <= 64


def test_over_budget_json_payload_stored(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=50, max_bytes=64))
    obj = {"tables": [{"name": f"t{i}", "rows": 100} for i in range(20)]}
    mvtr = ledger.record("profile_dataset", DispatchResult(ok=True, value=_HookedJson(obj)))
    assert mvtr.truncated is True
    assert "profile_dataset" in mvtr.summary and "JSON payload" in mvtr.summary
    assert mvtr.artifact_ref is not None
    assert store.get(mvtr.artifact_ref) == obj
    assert len(mvtr.preview.encode("utf-8")) <= 64


def test_over_budget_trace_stored_as_jsonl(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=3, max_bytes=8000))
    items: list[object] = [{"step": i} for i in range(10)]
    mvtr = ledger.record("workflow", DispatchResult(ok=True, value=_HookedTrace(items)))
    assert mvtr.truncated is True
    assert mvtr.full_row_count == 10
    assert len(mvtr.preview.splitlines()) == 3
    assert mvtr.artifact_ref is not None
    assert store.get(mvtr.artifact_ref) == items


def test_malformed_hook_degrades_to_string_fallback(store: ResultStore) -> None:
    ledger = ContextLedger(store)
    mvtr = ledger.record("buggy", DispatchResult(ok=True, value=_MalformedHook()))
    assert mvtr.truncated is False  # small string → passthrough, no crash
    assert render(mvtr) == "malformed-but-small"
