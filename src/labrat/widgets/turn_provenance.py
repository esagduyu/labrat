"""Per-turn provenance aggregation for the chat footer (T3c).

Pure accumulator — no Textual imports, no LLM, no I/O. Fed from ChatPanel's
on_tool_call hook; tolerant of ledger-summarized tool outputs (any parse
failure degrades to call-counting, never raises).
"""

from __future__ import annotations

import json
from typing import Any, cast


class TurnProvenance:
    """Aggregates one chat turn's grounding signals into a footer line."""

    def __init__(self, scent_stale: bool = False) -> None:
        self._scent_stale = scent_stale
        self._scent_hits = 0
        self._join_verified = False
        self._lineage_used = False
        self._sql_runs = 0
        self._verifier_rounds: int | None = None

    def record_tool(self, name: str, ok: bool, output: str) -> None:
        if not ok:
            return
        if name == "search_reference_docs":
            try:
                parsed: Any = json.loads(output)
                results: Any = (
                    cast(dict[str, Any], parsed).get("results", [])
                    if isinstance(parsed, dict)
                    else []
                )
                if isinstance(results, list):
                    self._scent_hits += len(cast("list[Any]", results))
            except (ValueError, TypeError):
                # Not JSON — production shape is a Pydantic repr of search_reference_docs'
                # _Output (e.g. "question='q' results=[]" or "...results=[DocResult(...), ...]").
                if "results=[]" in output:
                    pass  # zero hits: not grounding evidence, don't increment
                elif "DocResult(" in output:
                    self._scent_hits += output.count("DocResult(")
                else:
                    self._scent_hits += 1  # truly opaque output: fall back to a call count
        elif name == "verify_join":
            self._join_verified = True
        elif name == "explain_lineage":
            self._lineage_used = True
        elif name == "run_sql":
            self._sql_runs += 1

    def set_verifier(self, rounds_used: int | None) -> None:
        self._verifier_rounds = rounds_used

    def footer(self) -> str | None:
        parts: list[str] = []
        if self._scent_hits:
            freshness = "stale" if self._scent_stale else "fresh"
            parts.append(f"scent ×{self._scent_hits} ({freshness})")  # noqa: RUF001
        if self._join_verified:
            parts.append("join verified")
        if self._lineage_used:
            parts.append("lineage")
        if self._sql_runs:
            noun = "query" if self._sql_runs == 1 else "queries"
            parts.append(f"{self._sql_runs} {noun}")
        if self._verifier_rounds is not None:
            if self._verifier_rounds > 0:
                noun = "round" if self._verifier_rounds == 1 else "rounds"
                parts.append(f"verifier ✓ ({self._verifier_rounds} {noun})")
            else:
                parts.append("verifier ✓")
        if not parts:
            return None
        return "⚑ grounded: " + " · ".join(parts)
