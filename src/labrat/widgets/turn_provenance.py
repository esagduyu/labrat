"""Per-turn provenance aggregation for the chat footer (T3c).

Pure accumulator — no Textual imports, no LLM, no I/O. Fed from ChatPanel's
on_tool_call hook; tolerant of ledger-summarized tool outputs (any parse
failure degrades to call-counting, never raises).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from labrat.cheese.model import FindingProvenance

# NOTE: never try to segment per-DocResult with a non-greedy paren regex — the nested
# SectionMatch(...) reprs make it truncate before best_source/stale (which are declared
# AFTER sections). Instead: global findall per field and zip. This is collision-safe
# because SectionMatch has no domain=/best_source=/stale= fields (its names are
# source=/fresh=), so each pattern matches exactly once per DocResult.
_DOMAIN_RE = re.compile(r"domain='([^']*)'")
_BEST_RE = re.compile(r"best_source='([^']*)'")
_STALE_RE = re.compile(r"stale=(True|False|None)")


class TurnProvenance:
    """Aggregates one chat turn's grounding signals into a footer line."""

    def __init__(self, scent_stale: bool = False) -> None:
        self._scent_stale = scent_stale
        self._scent_hits = 0
        # (domain, best_source, stale) per matched doc, in arrival order; empty
        # when only count data was recoverable (fallback rendering).
        self._scent_docs: list[tuple[str, str, bool | None]] = []
        self._join_verified = False
        self._lineage_used = False
        self._sql_runs = 0
        self._verifier_rounds: int | None = None
        # True iff the last verifier check that actually ran judged the
        # answer sufficient. When the round (or turn) budget was exhausted
        # before a final re-check, this is False — the answer was never
        # actually verified sufficient, and the trust block must say so
        # rather than fabricate "verifier ✓" (whole-branch F2).
        self._verifier_sufficient: bool = True

    def _record_scent_doc(self, domain: str, best: str | None, stale: bool | None) -> None:
        self._scent_hits += 1
        if best is not None:
            self._scent_docs.append((domain, best, stale))

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
                    for doc in cast("list[Any]", results):
                        if not isinstance(doc, dict):
                            continue
                        d = cast(dict[str, Any], doc)
                        domain = d.get("domain")
                        if not isinstance(domain, str):
                            continue
                        best = d.get("best_source")
                        stale = d.get("stale")
                        self._record_scent_doc(
                            domain,
                            best if isinstance(best, str) else None,
                            stale if isinstance(stale, bool) else None,
                        )
            except (ValueError, TypeError):
                # Not JSON — production shape is a Pydantic repr of search_reference_docs'
                # _Output (e.g. "question='q' results=[]" or "...results=[DocResult(...), ...]").
                if "results=[]" in output:
                    pass  # zero hits: not grounding evidence
                elif "DocResult(" in output:
                    doc_spans = [m.start() for m in re.finditer(r"DocResult\(", output)]
                    n_docs = len(doc_spans)
                    bounds = [*doc_spans, len(output)]

                    def _positional(pattern: re.Pattern[str]) -> list[re.Match[str]] | None:
                        ms = list(pattern.finditer(output))
                        if len(ms) != n_docs:
                            return None
                        for i, m in enumerate(ms):
                            if not (bounds[i] <= m.start() < bounds[i + 1]):
                                return None
                        return ms

                    d_ms = _positional(_DOMAIN_RE)
                    b_ms = _positional(_BEST_RE)
                    s_ms = _positional(_STALE_RE)
                    if d_ms and b_ms and s_ms:
                        for i in range(n_docs):
                            stale_tok = s_ms[i].group(1)
                            self._record_scent_doc(
                                d_ms[i].group(1),
                                b_ms[i].group(1),
                                None if stale_tok == "None" else stale_tok == "True",
                            )
                    else:  # pre-enrichment repr, field-count mismatch, or positional
                        # misalignment (forged/adversarial substring) → count fallback
                        self._scent_hits += n_docs
                else:
                    self._scent_hits += 1  # truly opaque output
        elif name == "verify_join":
            self._join_verified = True
        elif name == "explain_lineage":
            self._lineage_used = True
        elif name == "run_sql":
            self._sql_runs += 1

    def set_verifier(self, rounds_used: int | None, *, sufficient: bool = True) -> None:
        """Record the verifier's involvement in this turn.

        ``sufficient`` must reflect whether the LAST verifier check that
        actually ran judged the answer sufficient — not merely whether the
        round budget wasn't yet exhausted. Pass ``False`` when the loop hit
        its round (or turn) budget without a final re-check, so the trust
        block can distinguish "verified sufficient" from "unresolved".
        """
        self._verifier_rounds = rounds_used
        self._verifier_sufficient = sufficient

    @staticmethod
    def _round_noun(n: int) -> str:
        return "round" if n == 1 else "rounds"

    def footer(self) -> str | None:
        parts: list[str] = []
        if self._scent_hits:
            if self._scent_docs:
                domain, best, stale = self._scent_docs[0]
                label = best
                if stale is True:
                    label += "·stale"
                elif stale is False:
                    label += "·fresh"
                seg = f"scent: {domain} ({label})"
                extra = self._scent_hits - 1
                if extra > 0:
                    seg += f" +{extra}"
                parts.append(seg)
            else:
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
            if not self._verifier_sufficient:
                parts.append("verifier ⚠ unresolved (round budget exhausted)")
            elif self._verifier_rounds > 0:
                n = self._verifier_rounds
                parts.append(f"verifier ✓ ({n} {self._round_noun(n)})")
            else:
                parts.append("verifier ✓")
        if not parts:
            return None
        return "⚑ grounded: " + " · ".join(parts)

    def snapshot(
        self,
        *,
        schema_fingerprint: str | None = None,
        git_sha: str | None = None,
        model_id: str | None = None,
    ) -> FindingProvenance | None:
        """Structured export of this turn's accumulated signals (pin-time capture).

        Returns None when the turn recorded nothing at all — callers store
        that as 'unattested', never an empty-but-real snapshot.
        """
        from datetime import UTC, datetime

        from labrat.cheese.model import FindingProvenance, ScentSourceRef

        if not (
            self._scent_hits
            or self._join_verified
            or self._lineage_used
            or self._sql_runs
            or self._verifier_rounds is not None
        ):
            return None
        sources = [
            ScentSourceRef(domain=d, tier=b, fresh=(None if s is None else not s))
            for d, b, s in self._scent_docs
        ]
        # count-fallback hits (no doc detail) still appear, honestly tier-less:
        for _ in range(self._scent_hits - len(self._scent_docs)):
            sources.append(ScentSourceRef(domain="(unattributed)", tier=None, fresh=None))
        verdict: str | None = None
        if self._verifier_rounds is not None:
            if not self._verifier_sufficient:
                verdict = "unresolved (round budget exhausted)"
            elif self._verifier_rounds > 0:
                n = self._verifier_rounds
                verdict = f"sufficient ({n} {self._round_noun(n)})"
            else:
                verdict = "sufficient"
        return FindingProvenance(
            scent_sources=sources,
            joins_verified=1 if self._join_verified else 0,
            lineage_used=self._lineage_used,
            verifier_verdict=verdict,
            run_sql_count=self._sql_runs,
            schema_fingerprint=schema_fingerprint,
            git_sha=git_sha,
            model_id=model_id,
            captured_at=datetime.now(tz=UTC),
        )
