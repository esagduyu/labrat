"""DAB submission.json writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from labrat.eval.types import BenchmarkReport, TrialResult


def build_submission(trials: list[TrialResult]) -> list[dict[str, Any]]:
    """Materialise DAB's required submission format from a list of trials."""
    entries: list[dict[str, Any]] = []
    for t in trials:
        dataset, _, query = t.task_id.partition(":")
        answer = t.artifact.get("payload") if t.artifact.get("type") == "text" else ""
        entries.append(
            {
                "dataset": dataset,
                "query": query,
                "run": t.trial_num,
                "answer": answer or "",
            }
        )
    return entries


def write_submission_json(report: BenchmarkReport, output_dir: Path) -> None:
    """Write submission.json into `output_dir`."""
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = build_submission(report.trials)
    (output_dir / "submission.json").write_text(json.dumps(entries, indent=2))
