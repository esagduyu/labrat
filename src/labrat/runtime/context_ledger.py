"""ContextLedger: bounds what tool output enters model history.

Mechanical only — summaries are row counts / column names / byte counts /
truncation notes. NO LLM call anywhere in this module. Attached to AgentLoop
as an opt-in; when absent the loop is byte-identical to today.
"""

from __future__ import annotations

from dataclasses import dataclass

from labrat.agent.tools.base import DispatchResult
from labrat.agent.tools.serialization import ModelVisibleToolResult
from labrat.results.store import ResultStore, cap_bytes


@dataclass(frozen=True)
class LedgerBudget:
    """Per-tool-result model-visibility budget.

    Defaults are conservative: 50 rows is enough to read value patterns and
    formats; 8000 bytes ≈ roughly 2000 tokens per tool result. History is
    resent on every provider call, so each oversized result is paid for on
    every subsequent turn — one 1000-row run_sql output (~50-100 KB) can
    dominate the context. ``preview`` is capped by BOTH limits.
    """

    max_rows: int = 50
    max_bytes: int = 8000


class ContextLedger:
    """Records tool DispatchResults; over-budget payloads go to the ResultStore.

    Only called for ``dispatch.ok`` results — AgentLoop keeps the error path
    (``f"Error: {dispatch.error}"``) unchanged.
    """

    def __init__(self, store: ResultStore, *, budget: LedgerBudget | None = None) -> None:
        self._store = store
        self._budget = budget if budget is not None else LedgerBudget()

    @property
    def store(self) -> ResultStore:
        return self._store

    def record(self, tool_name: str, dispatch: DispatchResult) -> ModelVisibleToolResult:
        full_str = str(dispatch.value)
        return self._record_fallback(tool_name, full_str)

    # ── paths ─────────────────────────────────────────────────────────────────

    def _record_fallback(self, tool_name: str, full_str: str) -> ModelVisibleToolResult:
        """String fallback for outputs with no (usable) ledger_payload hook."""
        if not self._over_bytes(full_str):
            return _passthrough(full_str)
        ref = self._store.put_json({"tool": tool_name, "text": full_str}, kind="json")
        n_bytes = len(full_str.encode("utf-8"))
        summary = (
            f"{tool_name}: {n_bytes}-byte text output; preview capped at "
            f"{self._budget.max_bytes} bytes; full output stored."
        )
        return ModelVisibleToolResult(
            summary=summary,
            preview=cap_bytes(full_str, self._budget.max_bytes),
            artifact_ref=ref,
            full_row_count=None,
            truncated=True,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _over_bytes(self, text: str) -> bool:
        return len(text.encode("utf-8")) > self._budget.max_bytes


def _passthrough(full_str: str, *, row_count: int | None = None) -> ModelVisibleToolResult:
    return ModelVisibleToolResult(
        summary="",
        preview=full_str,
        artifact_ref=None,
        full_row_count=row_count,
        truncated=False,
    )
