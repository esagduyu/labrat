"""Shells to the external `ade` CLI for one task.

Ported from src/labrat/eval/runners/ade_bench_runner.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def run_one(
    task_id: str,
    *,
    ade_bench_dir: Path,
    agent: str = "labrat_local",
    no_diffs: bool = True,
) -> dict[str, Any]:
    """Run one task via `ade run` and return the parsed trial dict.

    Returns a dict with keys: task_id, is_resolved, failure_mode, runtime_ms,
    experiment_dir.
    """
    cmd = [
        "uv",
        "run",
        "ade",
        "run",
        task_id,
        "--db",
        "duckdb",
        "--project-type",
        "dbt",
        "--agent",
        agent,
        "--n-attempts",
        "1",
    ]
    if no_diffs:
        cmd.append("--no-diffs")
    subprocess.run(
        cmd,
        cwd=ade_bench_dir,
        check=True,
        capture_output=True,
        text=True,
    )

    experiments = ade_bench_dir / "experiments"
    if not experiments.exists():
        return {
            "task_id": task_id,
            "is_resolved": False,
            "failure_mode": "no_experiments_dir",
            "runtime_ms": 0,
            "experiment_dir": None,
        }
    most_recent = max(experiments.iterdir(), key=lambda p: p.stat().st_mtime)
    results_path = most_recent / "results.json"
    if not results_path.exists():
        return {
            "task_id": task_id,
            "is_resolved": False,
            "failure_mode": "no_metadata",
            "runtime_ms": 0,
            "experiment_dir": str(most_recent),
        }

    data: dict[str, Any] = json.loads(results_path.read_text())
    for row in data.get("results", []):
        if row.get("task_id") == task_id:
            return {
                "task_id": task_id,
                "is_resolved": bool(row.get("is_resolved", False)),
                "failure_mode": row.get("failure_mode", "none"),
                "runtime_ms": int(row.get("runtime_ms") or 0),
                "experiment_dir": str(most_recent),
            }

    return {
        "task_id": task_id,
        "is_resolved": False,
        "failure_mode": "task_not_in_metadata",
        "runtime_ms": 0,
        "experiment_dir": str(most_recent),
    }
