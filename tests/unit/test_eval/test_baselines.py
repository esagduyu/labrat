"""Tests for comparison harness baselines (M27)."""

from __future__ import annotations

import pytest

from labrat.eval.baselines.comparison import ComparisonReport
from labrat.eval.baselines.naive import NaiveShotBaseline
from labrat.eval.baselines.spider_agent import SpiderAgentBaseline
from labrat.eval.baselines.vanna import VannaBaseline
from labrat.eval.models import EvalCase, EvalResult, EvalStatus
from labrat.eval.report import EvalReport


def _make_report(suite_name: str, n_correct: int, n_wrong: int) -> EvalReport:
    results = [
        EvalResult(case_id=f"c{i}", status=EvalStatus.correct, generated_sql="SELECT 1")
        for i in range(n_correct)
    ] + [
        EvalResult(case_id=f"w{i}", status=EvalStatus.wrong, generated_sql="SELECT 2")
        for i in range(n_wrong)
    ]
    return EvalReport(suite_name=suite_name, results=results)


# ── NaiveShotBaseline ─────────────────────────────────────────────────────────


def test_naive_baseline_has_name() -> None:
    baseline = NaiveShotBaseline(model="claude-haiku-4-5-20251001")
    assert baseline.name == "naive-one-shot"


@pytest.mark.asyncio
async def test_naive_baseline_run_returns_report() -> None:
    """NaiveShotBaseline run() returns an EvalReport (mocked LLM)."""

    async def _mock_llm(prompt: str) -> str:
        return "SELECT 1"

    baseline = NaiveShotBaseline(model="stub", llm_fn=_mock_llm)
    case = EvalCase(id="x", question="How many rows?", expected_sql="SELECT COUNT(*) FROM t")
    report = await baseline.run([case])
    assert isinstance(report, EvalReport)
    assert report.total == 1
    assert report.results[0].status in {EvalStatus.correct, EvalStatus.wrong, EvalStatus.error}


# ── VannaBaseline ─────────────────────────────────────────────────────────────


def test_vanna_baseline_has_name() -> None:
    baseline = VannaBaseline()
    assert baseline.name == "vanna"


@pytest.mark.asyncio
async def test_vanna_baseline_returns_report_without_vanna_installed() -> None:
    """VannaBaseline marks all cases as skipped when vanna is unavailable."""
    baseline = VannaBaseline()
    case = EvalCase(id="x", question="foo")
    report = await baseline.run([case])
    assert report.total == 1
    assert report.results[0].status == EvalStatus.skipped


# ── SpiderAgentBaseline ───────────────────────────────────────────────────────


def test_spider_agent_baseline_has_name() -> None:
    baseline = SpiderAgentBaseline()
    assert baseline.name == "spider-agent"


@pytest.mark.asyncio
async def test_spider_agent_baseline_returns_report() -> None:
    """SpiderAgentBaseline returns a structurally valid report."""
    baseline = SpiderAgentBaseline()
    case = EvalCase(id="x", question="foo")
    report = await baseline.run([case])
    assert isinstance(report, EvalReport)
    assert report.total == 1


# ── ComparisonReport ──────────────────────────────────────────────────────────


def test_comparison_report_contains_labrat_report() -> None:
    labrat = _make_report("spider2-dbt", n_correct=3, n_wrong=1)
    comparison = ComparisonReport(labrat=labrat, baselines=[])
    assert comparison.labrat is labrat


def test_comparison_report_to_markdown_has_header() -> None:
    labrat = _make_report("test", n_correct=2, n_wrong=2)
    naive = _make_report("test", n_correct=1, n_wrong=3)
    naive_baseline = NaiveShotBaseline(model="stub")
    naive_report = EvalReport(suite_name="test", results=naive.results)
    comparison = ComparisonReport(
        labrat=labrat,
        baselines=[(naive_baseline.name, naive_report)],
    )
    md = comparison.to_markdown()
    assert "LabRat" in md
    assert "naive-one-shot" in md


def test_comparison_report_to_dict_serializable() -> None:
    import json

    labrat = _make_report("test", n_correct=3, n_wrong=1)
    comparison = ComparisonReport(labrat=labrat, baselines=[])
    json.dumps(comparison.to_dict())


def test_comparison_report_save_and_load(tmp_path: object) -> None:
    from pathlib import Path

    labrat = _make_report("test", n_correct=2, n_wrong=1)
    comparison = ComparisonReport(labrat=labrat, baselines=[])
    out_dir = Path(str(tmp_path))  # type: ignore[arg-type]
    path = comparison.save(out_dir)
    assert path.exists()
    loaded = ComparisonReport.load(path)
    assert loaded.labrat.suite_name == "test"
