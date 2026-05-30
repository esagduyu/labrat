"""Markdown rendering for BenchmarkReport."""

from __future__ import annotations

import json

from labrat.eval.types import BenchmarkReport


def report_to_markdown(report: BenchmarkReport) -> str:
    """Render a BenchmarkReport as Markdown."""
    lines: list[str] = [
        f"# {report.benchmark}",
        "",
        f"**Run ID:** `{report.run_id}`",
        "",
        "## Score",
        "",
        f"- Overall: {report.score.overall:.2f}",
        f"- Tasks: {report.score.n_tasks}",
        f"- Trials: {report.score.n_trials}",
        f"- Passes: {report.score.n_passes}",
        "",
    ]

    if report.score.by_dimension:
        lines.append("## Score by Dimension")
        lines.append("")
        for dim_name, dim_values in report.score.by_dimension.items():
            lines.append(f"### {dim_name}")
            lines.append("")
            for k, v in sorted(dim_values.items()):
                lines.append(f"- {k}: {v:.2f}")
            lines.append("")

    failures = [t for t in report.trials if not t.passed]
    if failures:
        lines.append("## Failures")
        lines.append("")
        for t in failures:
            lines.append(f"- `{t.task_id}` (trial {t.trial_num}): {t.reason or 'no reason'}")
        lines.append("")

    lines.append("## Config")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(report.config, indent=2, sort_keys=True))
    lines.append("```")

    return "\n".join(lines)
