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

import json
import os
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from labrat.eval.types import (
    AggregateScore,
    BenchmarkReport,
    BenchmarkTask,
    TrialResult,
)

_DAB_SYSTEM_PROMPT = (
    "You are LabRat, a data agent answering a question about a database. "
    "Use the available tools to explore tables and run SQL. "
    "Return your final answer as plain text once you are confident."
)


async def _invoke_agent(
    prompt: str,
    ctx: Any,
    max_turns: int = 100,
) -> dict[str, Any]:
    """Run AgentLoop and return final_text + tool_calls count.

    Extracted as a module-level helper so unit tests can patch it.
    Will be replaced by LabRatAgentDriver in Phase 4.
    """
    from labrat.agent.loop import AgentLoop
    from labrat.agent.providers.claude_code import ClaudeCodeProvider
    from labrat.agent.tools.base import ToolRegistry
    from labrat.agent.tools.column_stats import ColumnStatsTool
    from labrat.agent.tools.describe_table import DescribeTableTool
    from labrat.agent.tools.explain_sql import ExplainSqlTool
    from labrat.agent.tools.list_tables import ListTablesTool
    from labrat.agent.tools.run_sql import RunSqlTool
    from labrat.agent.tools.sample_rows import SampleRowsTool
    from labrat.agent.tools.search_columns import SearchColumnsTool

    registry = ToolRegistry()
    for tool in [
        ListTablesTool(),
        DescribeTableTool(),
        SampleRowsTool(),
        SearchColumnsTool(),
        ColumnStatsTool(),
        ExplainSqlTool(),
        RunSqlTool(),
    ]:
        registry.register(tool)

    text_parts: list[str] = []
    loop = AgentLoop(
        provider=ClaudeCodeProvider(),
        registry=registry,
        ctx=ctx,
        system=_DAB_SYSTEM_PROMPT,
    )
    for _ in range(max_turns):
        await loop.run(prompt, on_text=text_parts.append)
        break  # single-turn; loop.run handles tool round-trips internally

    tool_calls = sum(
        1
        for msg in loop.history
        if msg["role"] == "assistant"
        for block in cast(list[dict[str, Any]], msg.get("content") or [])
        if block.get("type") == "tool_use"
    )
    return {"final_text": "".join(text_parts), "tool_calls": tool_calls}


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
        from labrat.eval.benchmarks.dab.env import build_dab_tool_context
        from labrat.eval.benchmarks.dab.scorer import score_with_validator

        scratch_dir.mkdir(parents=True, exist_ok=True)
        db_config_path = Path(task.config["db_config_path"])
        validator_path = Path(task.config["validator_path"])

        ctx = build_dab_tool_context(db_config_path)

        t0 = time.monotonic()
        agent_out = await _invoke_agent(prompt=task.prompt, ctx=ctx)
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
        raise NotImplementedError("Implemented in Task 21")

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None:
        raise NotImplementedError("Implemented in Task 22")
