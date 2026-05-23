"""Eval report: scorecard generation and persistence (M26)."""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from labrat.eval.models import EvalResult, EvalStatus


class EvalReport:
    """Aggregate scorecard for an eval suite run."""

    def __init__(
        self,
        suite_name: str,
        results: list[EvalResult],
        run_at: datetime | None = None,
    ) -> None:
        self.suite_name = suite_name
        self.results = results
        self.run_at = run_at or datetime.now(tz=UTC)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def accuracy(self) -> float:
        if not self.results:
            return 0.0
        correct = sum(1 for r in self.results if r.status == EvalStatus.correct)
        return correct / len(self.results)

    @property
    def latency_p50(self) -> float:
        latencies = [r.latency_seconds for r in self.results]
        if not latencies:
            return 0.0
        return statistics.median(latencies)

    @property
    def latency_p95(self) -> float:
        latencies = sorted(r.latency_seconds for r in self.results)
        if not latencies:
            return 0.0
        idx = int(len(latencies) * 0.95)
        return latencies[min(idx, len(latencies) - 1)]

    @property
    def avg_tool_calls(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.tool_calls for r in self.results) / len(self.results)

    def to_markdown(self) -> str:
        status_counts: dict[str, int] = {}
        for r in self.results:
            status_counts[r.status] = status_counts.get(r.status, 0) + 1

        pct = f"{self.accuracy * 100:.1f}"
        lines = [
            f"# LabRat Eval Report — {self.suite_name}",
            "",
            f"**Run at:** {self.run_at.isoformat()}",
            f"**Total cases:** {self.total}",
            f"**Accuracy:** {pct}%",
            "",
            "## Status breakdown",
            "",
            "| Status | Count |",
            "|--------|-------|",
        ]
        for status, count in sorted(status_counts.items()):
            lines.append(f"| {status} | {count} |")

        lines += [
            "",
            "## Latency",
            "",
            f"- p50: {self.latency_p50:.3f}s",
            f"- p95: {self.latency_p95:.3f}s",
            "",
            "## Tool call efficiency",
            "",
            f"- Average tool calls per case: {self.avg_tool_calls:.1f}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "run_at": self.run_at.isoformat(),
            "total": self.total,
            "accuracy": self.accuracy,
            "latency_p50": self.latency_p50,
            "latency_p95": self.latency_p95,
            "avg_tool_calls": self.avg_tool_calls,
            "results": [r.model_dump() for r in self.results],
        }

    def save(self, output_dir: Path) -> Path:
        """Save report as JSON; return the written path."""
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = self.run_at.strftime("%Y%m%dT%H%M%S")
        path = output_dir / f"{self.suite_name}_{ts}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def load(cls, path: Path) -> EvalReport:
        data: dict[str, Any] = json.loads(path.read_text())
        results = [EvalResult.model_validate(r) for r in data["results"]]
        return cls(
            suite_name=data["suite_name"],
            results=results,
            run_at=datetime.fromisoformat(data["run_at"]),
        )
