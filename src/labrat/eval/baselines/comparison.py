"""Comparison report: LabRat vs baselines (M27)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from labrat.eval.report import EvalReport


class ComparisonReport:
    """Side-by-side accuracy and latency comparison: LabRat vs each baseline."""

    def __init__(
        self,
        labrat: EvalReport,
        baselines: list[tuple[str, EvalReport]],
        run_at: datetime | None = None,
    ) -> None:
        self.labrat = labrat
        self.baselines = baselines
        self.run_at = run_at or datetime.now(tz=UTC)

    def to_markdown(self) -> str:
        lines = [
            f"# LabRat Comparison Report — {self.labrat.suite_name}",
            "",
            f"**Run at:** {self.run_at.isoformat()}",
            "",
            "## Accuracy",
            "",
            "| System | Accuracy | Correct | Total |",
            "|--------|----------|---------|-------|",
        ]
        from labrat.eval.models import EvalStatus

        labrat_correct = sum(1 for r in self.labrat.results if r.status == EvalStatus.correct)
        lines.append(
            f"| **LabRat** | {self.labrat.accuracy * 100:.1f}%"
            f" | {labrat_correct} | {self.labrat.total} |"
        )
        for name, report in self.baselines:
            correct = sum(1 for r in report.results if r.status == EvalStatus.correct)
            lines.append(f"| {name} | {report.accuracy * 100:.1f}% | {correct} | {report.total} |")

        lines += [
            "",
            "## Latency (p50)",
            "",
            "| System | p50 (s) | p95 (s) |",
            "|--------|---------|---------|",
            f"| **LabRat** | {self.labrat.latency_p50:.3f} | {self.labrat.latency_p95:.3f} |",
        ]
        for name, report in self.baselines:
            lines.append(f"| {name} | {report.latency_p50:.3f} | {report.latency_p95:.3f} |")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_at": self.run_at.isoformat(),
            "labrat": self.labrat.to_dict(),
            "baselines": {name: report.to_dict() for name, report in self.baselines},
        }

    def save(self, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = self.run_at.strftime("%Y%m%dT%H%M%S")
        path = output_dir / f"comparison_{self.labrat.suite_name}_{ts}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def load(cls, path: Path) -> ComparisonReport:
        data: dict[str, Any] = json.loads(path.read_text())
        labrat = EvalReport.load_dict(data["labrat"])
        baselines = [
            (name, EvalReport.load_dict(report_data))
            for name, report_data in data.get("baselines", {}).items()
        ]
        return cls(
            labrat=labrat,
            baselines=baselines,
            run_at=datetime.fromisoformat(data["run_at"]),
        )
