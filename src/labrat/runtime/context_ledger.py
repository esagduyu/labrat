"""ContextLedger: bounds what tool output enters model history.

Mechanical only — summaries are row counts / column names / byte counts /
truncation notes. NO LLM call anywhere in this module. Attached to AgentLoop
as an opt-in; when absent the loop is byte-identical to today.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

import polars as pl

from labrat.agent.tools.base import DispatchResult
from labrat.agent.tools.serialization import (
    LedgerPayloadProvider,
    ModelVisibleToolResult,
)
from labrat.results.store import ResultStore, cap_bytes, render_table_head


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

    def record(
        self,
        tool_name: str,
        dispatch: DispatchResult,
        *,
        full_str: str | None = None,
    ) -> ModelVisibleToolResult:
        value = dispatch.value
        if full_str is None:
            full_str = str(value)
        payload: object = None
        if isinstance(value, LedgerPayloadProvider):
            try:
                payload = value.ledger_payload()
            except Exception:
                # A tool's ledger_payload() hook can raise anything (or be
                # buggy in ways we can't anticipate) — never let that crash
                # the loop; degrade to the string fallback below.
                payload = None
        if isinstance(payload, tuple) and len(payload) == 2:
            kind, obj = payload
            try:
                if kind == "table" and isinstance(obj, pl.DataFrame):
                    return self._record_table(tool_name, full_str, obj)
                if kind == "json":
                    return self._record_json(tool_name, full_str, obj)
                if kind == "trace" and isinstance(obj, list):
                    return self._record_trace(tool_name, full_str, cast("list[object]", obj))
                # Malformed hook (e.g. kind "table" but payload isn't a
                # DataFrame): never crash the loop — degrade to the string
                # fallback below.
            except Exception:
                # The store-write path (e.g. json.dumps on an unserialisable
                # object) can raise on a well-formed-looking payload we can't
                # anticipate — never let that crash the loop; degrade to the
                # string fallback below.
                pass
        # payload is None, or wrong shape (not a 2-tuple), or the typed store
        # write raised: degrade to the string fallback.
        return self._record_fallback(tool_name, full_str)

    # ── paths ─────────────────────────────────────────────────────────────────

    def _record_table(
        self, tool_name: str, full_str: str, df: pl.DataFrame
    ) -> ModelVisibleToolResult:
        if df.height <= self._budget.max_rows and not self._over_bytes(full_str):
            return _passthrough(full_str, row_count=df.height)
        ref = self._store.put_table(df, meta={"tool": tool_name})
        shown = min(self._budget.max_rows, df.height)
        cols = ", ".join(df.columns[:20]) + (", ..." if df.width > 20 else "")
        summary = (
            f"{tool_name}: {df.height} rows x {df.width} columns ({cols}); "
            f"showing first {shown} rows; full result stored."
        )
        return ModelVisibleToolResult(
            summary=summary,
            preview=cap_bytes(render_table_head(df, self._budget.max_rows), self._budget.max_bytes),
            artifact_ref=ref,
            full_row_count=df.height,
            truncated=True,
        )

    def _record_json(self, tool_name: str, full_str: str, obj: object) -> ModelVisibleToolResult:
        if not self._over_bytes(full_str):
            return _passthrough(full_str)
        ref = self._store.put_json(obj, kind="json")
        rendered = json.dumps(obj, default=str)
        summary = (
            f"{tool_name}: {len(rendered.encode('utf-8'))}-byte JSON payload; "
            f"preview capped at {self._budget.max_bytes} bytes; full result stored."
        )
        return ModelVisibleToolResult(
            summary=summary,
            preview=cap_bytes(rendered, self._budget.max_bytes),
            artifact_ref=ref,
            full_row_count=None,
            truncated=True,
        )

    def _record_trace(
        self, tool_name: str, full_str: str, items: list[object]
    ) -> ModelVisibleToolResult:
        if len(items) <= self._budget.max_rows and not self._over_bytes(full_str):
            return _passthrough(full_str, row_count=len(items))
        ref = self._store.put_json(items, kind="trace")
        shown = min(self._budget.max_rows, len(items))
        summary = (
            f"{tool_name}: {len(items)} trace items; showing first {shown}; full trace stored."
        )
        lines = [json.dumps(item, default=str) for item in items[: self._budget.max_rows]]
        return ModelVisibleToolResult(
            summary=summary,
            preview=cap_bytes("\n".join(lines), self._budget.max_bytes),
            artifact_ref=ref,
            full_row_count=len(items),
            truncated=True,
        )

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
