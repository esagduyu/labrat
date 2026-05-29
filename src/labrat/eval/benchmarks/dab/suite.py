"""DAB BenchmarkSuite — task enumeration (Phase 1a).

Real DAB repo layout (~/repos/DataAgentBench/):
  query_<DATASET>/
    db_config.yaml             ← dataset-level; key is db_clients:
    db_description.txt
    db_description_withhint.txt
    query_dataset/             ← raw data files
    query1/
      query.json               ← question as a JSON string
      validate.py
      ground_truth.csv
    query2/ ...
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from labrat.eval.types import (
    AggregateScore,
    BenchmarkReport,
    BenchmarkTask,
    TrialResult,
)

_DAB_SYSTEM_PROMPT = (
    "You are a data analyst. Query the databases using Python+DuckDB/SQLite via Bash. "
    "Return your final answer as plain text once you are confident."
)

_DAB_TIMEOUT = 300  # per-turn timeout for the claude subprocess


async def _invoke_agent(
    prompt: str,
    ctx: Any,
    max_turns: int = 15,
) -> dict[str, Any]:
    """Invoke claude --print with Bash tool to query DuckDB/SQLite databases.

    Uses --disable-slash-commands (skip superpowers skill overhead) and
    --dangerously-skip-permissions (auto-approve Bash) so the model can run
    Python+DuckDB queries directly without a permission dialog.

    Extracted as a module-level helper so unit tests can patch it.
    Will be replaced by LabRatAgentDriver in Phase 4.
    """
    import shutil
    import subprocess

    if not shutil.which("claude"):
        raise RuntimeError(
            "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
        )

    env = {
        k: v
        for k, v in os.environ.items()
        if k != "ANTHROPIC_API_KEY" and k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE")
    }

    cmd = [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--model",
        "claude-sonnet-4-6",
        "--disable-slash-commands",
        "--dangerously-skip-permissions",
    ]

    full_prompt = f"SYSTEM:\n{_DAB_SYSTEM_PROMPT}\n\n{prompt}"

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            input=full_prompt.encode(),
            capture_output=True,
            timeout=_DAB_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"claude --print timed out after {_DAB_TIMEOUT}s") from None

    if result.returncode != 0:
        err = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"claude CLI error: {err[:300]}")

    raw = result.stdout.decode(errors="replace").strip()
    final_text = raw
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "result" in data:
            final_text = str(data["result"])  # type: ignore[arg-type]
    except json.JSONDecodeError:
        pass

    return {"final_text": final_text, "tool_calls": 0}


_DATASET_DIR_RE = re.compile(r"^query_(.+)$", re.IGNORECASE)
_QUERY_DIR_RE = re.compile(r"^query(\d+)$")


class DabSuite:
    """Reads DAB queries from a DataAgentBench checkout."""

    name = "dab"

    def __init__(
        self,
        dab_dir: Path | None = None,
        hints: bool = False,
    ) -> None:
        self._dir = (
            dab_dir or Path(os.environ.get("DAB_DIR", "~/repos/DataAgentBench")).expanduser()
        )
        self._hints = hints
        self._tasks_cache: list[BenchmarkTask] | None = None

    def tasks(self) -> Iterable[BenchmarkTask]:
        if self._tasks_cache is None:
            self._tasks_cache = self._load_tasks()
        return self._tasks_cache

    def _load_tasks(self) -> list[BenchmarkTask]:
        result: list[BenchmarkTask] = []
        if not self._dir.exists():
            return result

        for dataset_dir in sorted(self._dir.iterdir()):
            m = _DATASET_DIR_RE.match(dataset_dir.name)
            if not m or not dataset_dir.is_dir():
                continue
            dataset_name = m.group(1).lower()

            db_config = dataset_dir / "db_config.yaml"
            if not db_config.exists():
                continue

            desc_file = dataset_dir / (
                "db_description_withhint.txt" if self._hints else "db_description.txt"
            )
            description = desc_file.read_text().strip() if desc_file.exists() else ""

            for query_dir in sorted(dataset_dir.iterdir()):
                qm = _QUERY_DIR_RE.match(query_dir.name)
                if not qm or not query_dir.is_dir():
                    continue
                query_num = qm.group(1)

                query_file = query_dir / "query.json"
                validator = query_dir / "validate.py"
                if not query_file.exists() or not validator.exists():
                    continue

                question = json.loads(query_file.read_text().strip())
                if not isinstance(question, str) or not question:
                    continue

                prompt_parts = [p for p in [description, question] if p]
                prompt = "\n\n".join(prompt_parts)

                result.append(
                    BenchmarkTask(
                        id=f"{dataset_name}:{query_num}",
                        benchmark="dab",
                        prompt=prompt,
                        config={
                            "dataset": dataset_name,
                            "query_num": query_num,
                            "db_config_path": str(db_config),
                            "validator_path": str(validator),
                            "hints": self._hints,
                        },
                    )
                )

        return result

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult:
        import yaml

        from labrat.eval.benchmarks.dab.scorer import score_with_validator

        scratch_dir.mkdir(parents=True, exist_ok=True)
        db_config_path = Path(task.config["db_config_path"])
        validator_path = Path(task.config["validator_path"])
        dataset_dir = db_config_path.parent

        # Build db-access preamble so the model can query via Python+DuckDB/SQLite.
        config = yaml.safe_load(db_config_path.read_text())
        clients: dict[str, Any] = config.get("db_clients") or {}
        db_lines: list[str] = []
        for name, spec in clients.items():
            db_type = str(spec.get("db_type", "")).lower()
            db_path = dataset_dir / str(spec.get("db_path", ""))
            if db_type == "duckdb":
                db_lines.append(
                    f'  {name} (DuckDB): python3 -c "import duckdb; '
                    f"conn = duckdb.connect('{db_path}'); "
                    "print(conn.execute('SELECT ...').fetchall())\""
                )
            elif db_type == "sqlite":
                db_lines.append(
                    f'  {name} (SQLite): python3 -c "import sqlite3; '
                    f"conn = sqlite3.connect('{db_path}'); "
                    "print(conn.execute('SELECT ...').fetchall())\""
                )

        db_preamble = "You can query these databases via Bash (Python is available):\n" + "\n".join(
            db_lines
        )
        enriched_prompt = f"{db_preamble}\n\n{task.prompt}"

        t0 = time.monotonic()
        agent_out = await _invoke_agent(prompt=enriched_prompt, ctx=None)
        latency = time.monotonic() - t0

        passed, reason = score_with_validator(validator_path, agent_out["final_text"])

        return TrialResult(
            task_id=task.id,
            trial_num=trial_num,
            passed=passed,
            reason=reason,
            latency_seconds=latency,
            tool_calls=agent_out["tool_calls"],
            artifact={"type": "text", "payload": agent_out["final_text"]},
        )

    def aggregate(self, results: list[TrialResult]) -> AggregateScore:
        if not results:
            return AggregateScore(overall=0.0, per_task={}, n_tasks=0, n_trials=0, n_passes=0)

        per_task: dict[str, list[bool]] = {}
        for r in results:
            per_task.setdefault(r.task_id, []).append(r.passed)
        per_task_pass_rate = {tid: sum(passes) / len(passes) for tid, passes in per_task.items()}

        by_dataset: dict[str, list[float]] = {}
        for tid, pr in per_task_pass_rate.items():
            dataset = tid.split(":", 1)[0]
            by_dataset.setdefault(dataset, []).append(pr)
        dataset_means = {ds: sum(prs) / len(prs) for ds, prs in by_dataset.items()}

        return AggregateScore(
            overall=sum(dataset_means.values()) / len(dataset_means),
            per_task=per_task_pass_rate,
            by_dimension={"dataset": dataset_means},
            n_tasks=len(per_task),
            n_trials=len(results),
            n_passes=sum(1 for r in results if r.passed),
        )

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None:
        from labrat.eval.benchmarks.dab.reporter import write_submission_json

        write_submission_json(report, output_dir)
