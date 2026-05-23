"""Spider-Agent baseline wrapper (M27).

Wraps the xlang-ai Spider-Agent reference implementation.
Requires a local clone of xlang-ai/Spider2 and the spider-agent dependencies.
If the agent is not configured (SPIDER_AGENT_DIR env var unset), all cases
are marked as skipped.
"""

from __future__ import annotations

import os
import time

from labrat.eval.models import EvalCase, EvalResult, EvalStatus
from labrat.eval.report import EvalReport


def _spider_agent_available() -> bool:
    return bool(os.environ.get("SPIDER_AGENT_DIR"))


class SpiderAgentBaseline:
    """Spider-Agent reference implementation baseline.

    Set SPIDER_AGENT_DIR to the local clone of xlang-ai/Spider2 to enable.
    Without it, all cases return EvalStatus.skipped.
    """

    name = "spider-agent"

    async def run(self, cases: list[EvalCase]) -> EvalReport:
        available = _spider_agent_available()
        results: list[EvalResult] = []
        for case in cases:
            if not available:
                results.append(
                    EvalResult(
                        case_id=case.id,
                        status=EvalStatus.skipped,
                        error_message="SPIDER_AGENT_DIR not set",
                    )
                )
                continue
            result = await self._run_case(case)
            results.append(result)
        return EvalReport(suite_name="spider-agent", results=results)

    async def _run_case(self, case: EvalCase) -> EvalResult:
        agent_dir = os.environ.get("SPIDER_AGENT_DIR", "")
        start = time.monotonic()
        try:
            import subprocess

            proc = subprocess.run(  # pyright: ignore[reportUnknownVariableType]
                ["python", "run_agent.py", "--question", case.question],
                cwd=agent_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            latency = time.monotonic() - start
            sql = proc.stdout.strip()  # pyright: ignore[reportUnknownMemberType]
        except Exception as exc:
            return EvalResult(
                case_id=case.id,
                status=EvalStatus.error,
                error_message=str(exc),
                latency_seconds=time.monotonic() - start,
            )

        if case.expected_sql is None:
            status = EvalStatus.skipped
        elif sql.upper().strip() == (case.expected_sql or "").upper().strip():
            status = EvalStatus.correct
        else:
            status = EvalStatus.wrong

        return EvalResult(
            case_id=case.id,
            status=status,
            generated_sql=sql,
            latency_seconds=latency,
        )
