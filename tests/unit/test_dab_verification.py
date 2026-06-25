"""Trial-level verification: consensus + re-derive (FEATURE: verification layer)."""

from __future__ import annotations

from pathlib import Path

from labrat.eval.benchmarks.dab.suite import DabSuite
from labrat.eval.types import BenchmarkTask


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        id="demo:1",
        benchmark="dab",
        prompt="how many?",
        config={"db_config_path": "x", "validator_path": "y", "dataset": "demo"},
    )


async def test_consensus_returns_modal(tmp_path: Path, monkeypatch) -> None:
    suite = DabSuite(driver="claude-mcp", consensus_k=3)
    answers = iter([("A", 5, 1.0), ("B", 5, 1.0), ("A", 5, 1.0)])  # modal = A

    async def _disp(self, task, dbp, sd, *, extra_instructions=""):
        return next(answers)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    # judge: only exact-equal answers agree (answers_agree short-circuits those)
    monkeypatch.setattr(suite, "_verify_llm_fn", lambda: lambda p: _never_same(p))
    text, _tc, _lat = await suite._run_trial_verified(_task(), Path("x"), tmp_path)
    assert text == "A"


async def test_reverify_keeps_primary_when_agree(tmp_path: Path, monkeypatch) -> None:
    suite = DabSuite(driver="claude-mcp", reverify=True)
    runs = iter([("42", 5, 1.0), ("42", 5, 1.0)])  # primary, re-derive — identical → agree

    async def _disp(self, task, dbp, sd, *, extra_instructions=""):
        return next(runs)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    text, _, _ = await suite._run_trial_verified(_task(), Path("x"), tmp_path)
    assert text == "42"  # agreement → primary unchanged, no reconcile run consumed


async def test_off_path_single_dispatch(tmp_path: Path, monkeypatch) -> None:
    suite = DabSuite(driver="claude-mcp")  # both off
    calls = {"n": 0}

    async def _disp(self, task, dbp, sd, *, extra_instructions=""):
        calls["n"] += 1
        return ("once", 1, 0.5)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    text, _, _ = await suite._run_trial_verified(_task(), Path("x"), tmp_path)
    assert text == "once" and calls["n"] == 1  # exactly one dispatch when verification off


async def _never_same(prompt: str) -> str:
    return "different"
