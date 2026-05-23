"""Vanna baseline wrapper (M27).

Wraps the `vanna` library if installed. If vanna is not installed, all cases
are marked as skipped rather than failing so the comparison report still runs.
Install: uv add "vanna"
"""

from __future__ import annotations

import time

from labrat.eval.models import EvalCase, EvalResult, EvalStatus
from labrat.eval.report import EvalReport


def _vanna_available() -> bool:
    try:
        import vanna  # type: ignore[import-untyped]  # noqa: F401

        return True
    except ImportError:
        return False


class VannaBaseline:
    """Vanna NL→SQL baseline. Skips all cases if vanna is not installed."""

    name = "vanna"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self._model = model
        self._api_key = api_key
        self._vanna: object | None = None

    def _setup(self) -> bool:
        if not _vanna_available():
            return False
        try:
            import vanna  # type: ignore[import-untyped]

            self._vanna = vanna.base.VannaBase()  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue]
            return True
        except Exception:
            return False

    async def run(self, cases: list[EvalCase]) -> EvalReport:
        available = _vanna_available()
        results: list[EvalResult] = []
        for case in cases:
            if not available:
                results.append(
                    EvalResult(
                        case_id=case.id,
                        status=EvalStatus.skipped,
                        error_message="vanna not installed",
                    )
                )
                continue
            result = await self._run_case(case)
            results.append(result)
        return EvalReport(suite_name="vanna", results=results)

    async def _run_case(self, case: EvalCase) -> EvalResult:
        start = time.monotonic()
        try:
            import vanna  # type: ignore[import-untyped]

            vn = vanna.base.VannaBase()  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue,reportUnknownVariableType]
            sql_raw = vn.generate_sql(case.question)  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            sql: str = str(sql_raw)  # pyright: ignore[reportUnknownArgumentType]
            latency = time.monotonic() - start
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
