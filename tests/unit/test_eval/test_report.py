"""Unit tests for eval report generation (M26)."""

from __future__ import annotations

from labrat.eval.models import EvalResult, EvalStatus
from labrat.eval.report import EvalReport


def _make_result(status: EvalStatus, latency: float = 0.1, tool_calls: int = 1) -> EvalResult:
    return EvalResult(
        case_id="x",
        status=status,
        generated_sql=None,
        latency_seconds=latency,
        tool_calls=tool_calls,
    )


def test_report_total_count() -> None:
    report = EvalReport(suite_name="test", results=[_make_result(EvalStatus.correct)])
    assert report.total == 1


def test_report_accuracy_all_correct() -> None:
    results = [_make_result(EvalStatus.correct)] * 4
    report = EvalReport(suite_name="test", results=results)
    assert report.accuracy == 1.0


def test_report_accuracy_mixed() -> None:
    results = [
        _make_result(EvalStatus.correct),
        _make_result(EvalStatus.wrong),
        _make_result(EvalStatus.error),
        _make_result(EvalStatus.correct),
    ]
    report = EvalReport(suite_name="test", results=results)
    assert report.accuracy == 0.5


def test_report_accuracy_empty() -> None:
    report = EvalReport(suite_name="test", results=[])
    assert report.accuracy == 0.0


def test_report_latency_p50() -> None:
    results = [_make_result(EvalStatus.correct, latency=float(i)) for i in range(1, 5)]
    report = EvalReport(suite_name="test", results=results)
    assert report.latency_p50 > 0


def test_report_to_markdown_contains_suite_name() -> None:
    report = EvalReport(suite_name="spider2-dbt", results=[])
    md = report.to_markdown()
    assert "spider2-dbt" in md


def test_report_to_markdown_contains_accuracy() -> None:
    results = [_make_result(EvalStatus.correct)] * 3 + [_make_result(EvalStatus.wrong)]
    report = EvalReport(suite_name="test", results=results)
    md = report.to_markdown()
    assert "75" in md or "0.75" in md


def test_report_to_dict_is_serializable() -> None:
    import json

    results = [_make_result(EvalStatus.correct)]
    report = EvalReport(suite_name="test", results=results)
    d = report.to_dict()
    json.dumps(d)  # must not raise


def test_report_save_and_load(tmp_path: object) -> None:
    from pathlib import Path

    results = [_make_result(EvalStatus.correct, latency=0.42, tool_calls=3)]
    report = EvalReport(suite_name="test", results=results)
    out_dir = Path(str(tmp_path))  # type: ignore[arg-type]
    path = report.save(out_dir)
    assert path.exists()
    loaded = EvalReport.load(path)
    assert loaded.suite_name == "test"
    assert loaded.total == 1
