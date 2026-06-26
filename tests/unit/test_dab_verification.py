"""Trial-level verification: consensus + re-derive (FEATURE: verification layer)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from labrat.eval.benchmarks.dab.suite import DabSuite
from labrat.eval.types import AggregateScore, BenchmarkReport, BenchmarkTask


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


# ── FIX 1: judge provider routing ────────────────────────────────────────────


async def test_verify_judge_uses_claude_code_on_mcp(monkeypatch: Any) -> None:
    """claude-mcp driver must route the judge through claude-code (Max-plan OAuth)."""
    import labrat.eval.benchmarks.dab.suite as _suite_mod

    captured: dict[str, str] = {}

    def _fake_build(name: str, model: str, *a: Any, **k: Any) -> Any:
        captured["name"] = name

        class _P:
            pass

        return _P()

    monkeypatch.setattr(_suite_mod, "build_provider", _fake_build)
    # provider_llm_fn only wraps the provider in a closure — no .stream() call yet
    DabSuite(driver="claude-mcp")._verify_llm_fn()
    assert captured["name"] == "claude-code"


async def test_verify_judge_uses_agent_provider_on_labrat(monkeypatch: Any) -> None:
    """Non-mcp drivers keep the agent's own provider for the judge."""
    import labrat.eval.benchmarks.dab.suite as _suite_mod

    captured: dict[str, str] = {}

    def _fake_build(name: str, model: str, *a: Any, **k: Any) -> Any:
        captured["name"] = name

        class _P:
            pass

        return _P()

    monkeypatch.setattr(_suite_mod, "build_provider", _fake_build)
    DabSuite(driver="labrat-agent", agent_provider="anthropic")._verify_llm_fn()
    assert captured["name"] == "anthropic"


# ── FIX 2: verification.json persistence ────────────────────────────────────


async def test_verification_json_written(tmp_path: Path, monkeypatch: Any) -> None:
    """With consensus_k=2, verification.json must appear in the trial scratch dir."""
    suite = DabSuite(driver="claude-mcp", consensus_k=2)
    answers = iter([("A", 5, 1.0), ("B", 5, 1.0)])

    async def _disp(
        self: Any, task: Any, dbp: Any, sd: Any, *, extra_instructions: str = ""
    ) -> tuple[str, int, float]:
        return next(answers)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    monkeypatch.setattr(suite, "_verify_llm_fn", lambda: _never_same)
    await suite._run_trial_verified(_task(), Path("x"), tmp_path)

    vfile = tmp_path / "verification.json"
    assert vfile.exists(), "verification.json should be written when consensus_k=2"
    vdata = json.loads(vfile.read_text())
    assert "modal_index" in vdata
    assert "low_confidence" in vdata
    assert vdata["consensus_k"] == 2


async def test_no_verification_json_on_off_path(tmp_path: Path, monkeypatch: Any) -> None:
    """With both flags off, NO verification.json must be written (off-path invariant)."""
    suite = DabSuite(driver="claude-mcp")  # consensus_k=None, reverify=False

    async def _disp(
        self: Any, task: Any, dbp: Any, sd: Any, *, extra_instructions: str = ""
    ) -> tuple[str, int, float]:
        return ("once", 1, 0.5)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    await suite._run_trial_verified(_task(), Path("x"), tmp_path)
    assert not (tmp_path / "verification.json").exists()


# ── FIX 4: summed latency ────────────────────────────────────────────────────


async def test_summed_latency_across_sub_runs(tmp_path: Path, monkeypatch: Any) -> None:
    """Returned latency for consensus_k=2 must equal the sum of both sub-run latencies."""
    suite = DabSuite(driver="claude-mcp", consensus_k=2)
    answers = iter([("A", 5, 1.5), ("A", 5, 2.5)])  # both agree → modal = A

    async def _disp(
        self: Any, task: Any, dbp: Any, sd: Any, *, extra_instructions: str = ""
    ) -> tuple[str, int, float]:
        return next(answers)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    # both answers are "A" → exact-equal short-circuit in answers_agree → no LLM judge needed
    monkeypatch.setattr(suite, "_verify_llm_fn", lambda: _never_same)
    _text, _tc, latency = await suite._run_trial_verified(_task(), Path("x"), tmp_path)
    assert latency == pytest.approx(4.0)  # 1.5 + 2.5


def test_eval_dab_threads_verification_flags(monkeypatch: Any, tmp_path: Path) -> None:
    """--agent-consensus and --agent-reverify must reach DabSuite(consensus_k=, reverify=)."""
    import scripts.eval_dab as ed

    captured: dict[str, Any] = {}

    class _FakeSuite:
        name = "dab"

        def __init__(self, **kw: Any) -> None:
            captured.update(kw)

        def tasks(self) -> list[Any]:
            return []

        def write_submission(self, report: Any, output_dir: Any) -> None:
            pass

    async def _fake_interim(*a: Any, **kw: Any) -> BenchmarkReport:
        return BenchmarkReport(
            benchmark="dab",
            run_id="test",
            score=AggregateScore(overall=0.0, per_task={}, n_tasks=0, n_trials=0, n_passes=0),
            trials=[],
            config={},
        )

    monkeypatch.setattr(ed, "DabSuite", _FakeSuite)
    monkeypatch.setattr(ed, "_run_interim", _fake_interim)
    ed.main(
        [
            "--driver",
            "claude-mcp",
            "--agent-consensus",
            "3",
            "--agent-reverify",
            "--output-dir",
            str(tmp_path / "r"),
            "--datasets",
            "deps_dev_v1",
        ]
    )
    assert captured.get("consensus_k") == 3
    assert captured.get("reverify") is True
