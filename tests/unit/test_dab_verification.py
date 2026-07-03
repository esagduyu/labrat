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

    async def _disp(self, task, dbp, sd, *, extra_instructions="", diversity_index=None):
        return next(answers)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    # judge: only exact-equal answers agree (answers_agree short-circuits those)
    monkeypatch.setattr(suite, "_verify_llm_fn", lambda: lambda p: _never_same(p))
    text, _tc, _lat = await suite._run_trial_verified(_task(), Path("x"), tmp_path)
    assert text == "A"


async def test_reverify_keeps_primary_when_agree(tmp_path: Path, monkeypatch) -> None:
    suite = DabSuite(driver="claude-mcp", reverify=True)
    runs = iter([("42", 5, 1.0), ("42", 5, 1.0)])  # primary, re-derive — identical → agree

    async def _disp(self, task, dbp, sd, *, extra_instructions="", diversity_index=None):
        return next(runs)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    text, _, _ = await suite._run_trial_verified(_task(), Path("x"), tmp_path)
    assert text == "42"  # agreement → primary unchanged, no reconcile run consumed


async def test_off_path_single_dispatch(tmp_path: Path, monkeypatch) -> None:
    suite = DabSuite(driver="claude-mcp")  # both off
    calls = {"n": 0}

    async def _disp(self, task, dbp, sd, *, extra_instructions="", diversity_index=None):
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
        self: Any,
        task: Any,
        dbp: Any,
        sd: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
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
        self: Any,
        task: Any,
        dbp: Any,
        sd: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
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
        self: Any,
        task: Any,
        dbp: Any,
        sd: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
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
    assert captured.get("consensus_diversity") is True  # default on, no --no-consensus-diversity
    assert captured.get("argue_rounds") == 0  # default off, no --agent-argue-rounds


def test_eval_dab_threads_argue_rounds(monkeypatch: Any, tmp_path: Path) -> None:
    """--agent-argue-rounds must reach DabSuite(argue_rounds=)."""
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
            "--agent-argue-rounds",
            "2",
            "--output-dir",
            str(tmp_path / "r"),
            "--datasets",
            "deps_dev_v1",
        ]
    )
    assert captured.get("consensus_k") == 3
    assert captured.get("argue_rounds") == 2


def test_eval_dab_threads_no_consensus_diversity(monkeypatch: Any, tmp_path: Path) -> None:
    """--no-consensus-diversity must reach DabSuite(consensus_diversity=False)."""
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
            "--no-consensus-diversity",
            "--output-dir",
            str(tmp_path / "r"),
            "--datasets",
            "deps_dev_v1",
        ]
    )
    assert captured.get("consensus_k") == 3
    assert captured.get("consensus_diversity") is False


# ── Diverse consensus (M1 Unit 1b) ───────────────────────────────────────────


async def test_consensus_passes_diversity_index_when_on(tmp_path: Path, monkeypatch: Any) -> None:
    """K-of-N consensus sub-runs each get a distinct diversity_index when on (default)."""
    from labrat.agent.verification import consensus as _consensus_mod

    seen: list[int | None] = []

    async def _fake_dispatch(
        self: Any,
        task: Any,
        dbc: Any,
        sub: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        seen.append(diversity_index)
        return (f"ans{diversity_index}", 1, 0.1)

    async def _fake_choose_modal(
        answers: list[str], *, question: str, llm_fn: Any
    ) -> tuple[int, bool]:
        return (0, False)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _fake_dispatch)
    monkeypatch.setattr(_consensus_mod, "choose_modal", _fake_choose_modal)

    suite = DabSuite(driver="claude-mcp", consensus_k=2, consensus_diversity=True)
    await suite._run_trial_verified(_task(), Path("x"), tmp_path)

    assert 0 in seen and 1 in seen  # both sub-runs got distinct diversity indices


async def test_consensus_passes_none_diversity_index_when_off(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """With consensus_diversity=False, every K sub-run dispatches with diversity_index=None."""
    from labrat.agent.verification import consensus as _consensus_mod

    seen: list[int | None] = []

    async def _fake_dispatch(
        self: Any,
        task: Any,
        dbc: Any,
        sub: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        seen.append(diversity_index)
        return ("same", 1, 0.1)

    async def _fake_choose_modal(
        answers: list[str], *, question: str, llm_fn: Any
    ) -> tuple[int, bool]:
        return (0, False)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _fake_dispatch)
    monkeypatch.setattr(_consensus_mod, "choose_modal", _fake_choose_modal)

    suite = DabSuite(driver="claude-mcp", consensus_k=2, consensus_diversity=False)
    await suite._run_trial_verified(_task(), Path("x"), tmp_path)

    assert seen == [None, None]


# ── Argumentation rounds on a split vote (M1 Unit 2) ─────────────────────────


async def test_argue_round_resolves_split(tmp_path: Path, monkeypatch: Any) -> None:
    """A split (low-confidence) vote resolves once argue rounds surface the other
    sub-runs' answers and the sub-runs converge."""
    from labrat.agent.verification import consensus as _consensus_mod

    async def _fake_dispatch(
        self: Any,
        task: Any,
        dbc: Any,
        sub: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        argued = "Other analysts concluded" in extra_instructions
        return ("CONVERGED" if argued else f"ans{diversity_index}", 1, 0.1)

    async def _fake_modal(answers: list[str], *, question: str, llm_fn: Any) -> tuple[int, bool]:
        distinct = set(answers)
        return (0, len(distinct) > 1)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _fake_dispatch)
    monkeypatch.setattr(_consensus_mod, "choose_modal", _fake_modal)

    suite = DabSuite(driver="claude-mcp", consensus_k=2, argue_rounds=2)
    final, _tc, _lat = await suite._run_trial_verified(_task(), Path("x"), tmp_path)

    assert final == "CONVERGED"

    vdata = json.loads((tmp_path / "verification.json").read_text())
    assert vdata["argue_rounds_used"] == 1
    assert vdata["low_confidence"] is False


async def test_argue_round_fail_open_never_converges(tmp_path: Path, monkeypatch: Any) -> None:
    """If the argue rounds never converge within the cap, the trial returns the
    current modal answer without raising (fail-open, bounded)."""
    from labrat.agent.verification import consensus as _consensus_mod

    async def _fake_dispatch(
        self: Any,
        task: Any,
        dbc: Any,
        sub: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        # Always disagreeing, whether or not an argue block was appended.
        return (f"ans{diversity_index}", 1, 0.1)

    async def _fake_modal(answers: list[str], *, question: str, llm_fn: Any) -> tuple[int, bool]:
        distinct = set(answers)
        return (0, len(distinct) > 1)  # always low_confidence — never converges

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _fake_dispatch)
    monkeypatch.setattr(_consensus_mod, "choose_modal", _fake_modal)

    suite = DabSuite(driver="claude-mcp", consensus_k=2, argue_rounds=2)
    final, _tc, _lat = await suite._run_trial_verified(_task(), Path("x"), tmp_path)

    assert final == "ans0"  # modal (index 0) from the final, still-split round

    vdata = json.loads((tmp_path / "verification.json").read_text())
    assert vdata["argue_rounds_used"] == 2
    assert vdata["low_confidence"] is True


async def test_argue_rounds_zero_no_argue_loop(tmp_path: Path, monkeypatch: Any) -> None:
    """argue_rounds=0 (default) must never invoke the argue loop, even on a split
    vote — byte-identical to pre-argue-loop behavior."""
    from labrat.agent.verification import consensus as _consensus_mod

    calls = {"n": 0}

    async def _fake_dispatch(
        self: Any,
        task: Any,
        dbc: Any,
        sub: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        calls["n"] += 1
        return (f"ans{diversity_index}", 1, 0.1)

    async def _fake_modal(answers: list[str], *, question: str, llm_fn: Any) -> tuple[int, bool]:
        return (0, True)  # always low_confidence

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _fake_dispatch)
    monkeypatch.setattr(_consensus_mod, "choose_modal", _fake_modal)

    suite = DabSuite(driver="claude-mcp", consensus_k=2)  # argue_rounds defaults to 0
    _final, _tc, _lat = await suite._run_trial_verified(_task(), Path("x"), tmp_path)

    assert calls["n"] == 2  # only the initial K=2 sub-runs, no argue dispatches
    vdata = json.loads((tmp_path / "verification.json").read_text())
    assert vdata["argue_rounds_used"] == 0


async def test_reverify_rederive_diversity_index_always_none(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The independent re-derive run (_run_once(900)) never diversifies, even with
    consensus_diversity=True — diversifying the re-derivation would defeat its purpose
    as an independent check.
    """
    seen: list[int | None] = []

    async def _fake_dispatch(
        self: Any,
        task: Any,
        dbc: Any,
        sub: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        seen.append(diversity_index)
        return ("42", 1, 0.1)  # primary + re-derive identical → agree, no reconcile round

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _fake_dispatch)

    suite = DabSuite(driver="claude-mcp", reverify=True, consensus_diversity=True)
    await suite._run_trial_verified(_task(), Path("x"), tmp_path)

    # k==1 path: primary dispatch (diversity_index=None) + re-derive dispatch
    # (diversity_index=None) — neither the single-primary nor the re-derive diversify.
    assert seen == [None, None]


async def test_rederive_does_not_see_primary_transcript(tmp_path: Path, monkeypatch: Any) -> None:
    """Spacedock invariant: the re-derive dispatch (_run_once(900)) must be an
    independent context — it must NOT receive the primary sub-run's answer/transcript
    in its extra_instructions. Seeding the re-derivation with the primary's answer
    would defeat its purpose as an independent check."""
    calls: list[tuple[str, str]] = []

    async def _fake_dispatch(
        self: Any,
        task: Any,
        dbc: Any,
        sub: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        calls.append((sub.name, extra_instructions))
        # Primary and re-derive both return the same sentinel so they agree exactly
        # and no reconcile (subrun901) dispatch is needed.
        return ("PRIMARY_ANSWER", 5, 1.0)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _fake_dispatch)

    suite = DabSuite(driver="claude-mcp", reverify=True)
    await suite._run_trial_verified(_task(), Path("x"), tmp_path)

    rederive_calls = [extra for name, extra in calls if name == "subrun900"]
    assert rederive_calls, "expected a subrun900 (re-derive) dispatch"
    for extra in rederive_calls:
        assert "PRIMARY_ANSWER" not in extra, (
            "re-derive dispatch must not receive the primary sub-run's answer/transcript"
        )


# ── Post-verify: deterministic constraint check + one bounded revise (M1 Unit 3b) ──


def _topn_task() -> BenchmarkTask:
    return BenchmarkTask(
        id="demo:1",
        benchmark="dab",
        prompt="What are the top 5 products?",
        config={"db_config_path": "x", "validator_path": "y", "dataset": "demo"},
    )


async def test_postverify_revises_on_violation(tmp_path: Path, monkeypatch: Any) -> None:
    """A chosen answer that violates a constraint (asks for top 5, answer lists 3)
    triggers exactly one bounded revise dispatch; the revised answer is returned."""
    calls: list[str] = []

    async def _disp(
        self: Any,
        task: Any,
        dbp: Any,
        sd: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        calls.append(extra_instructions)
        if "does not satisfy" in extra_instructions:
            return ("A, B, C, D, E", 5, 1.0)
        return ("A, B, C", 5, 1.0)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    suite = DabSuite(driver="claude-mcp", postverify=True)  # standalone — no consensus/reverify
    text, _tc, _lat = await suite._run_trial_verified(_topn_task(), Path("x"), tmp_path)

    assert text == "A, B, C, D, E"
    assert len(calls) == 2  # primary + exactly one revise dispatch

    vdata = json.loads((tmp_path / "verification.json").read_text())
    assert vdata["postverify"] is True
    assert vdata["postverify_violations"]
    assert vdata["postverify_revised"] is True


async def test_postverify_no_revise_when_satisfied(tmp_path: Path, monkeypatch: Any) -> None:
    """A chosen answer that already satisfies the constraint triggers no revise dispatch."""
    calls = {"n": 0}

    async def _disp(
        self: Any,
        task: Any,
        dbp: Any,
        sd: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        calls["n"] += 1
        return ("A, B, C, D, E", 5, 1.0)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    suite = DabSuite(driver="claude-mcp", postverify=True)
    text, _tc, _lat = await suite._run_trial_verified(_topn_task(), Path("x"), tmp_path)

    assert text == "A, B, C, D, E"
    assert calls["n"] == 1  # no revise dispatch

    vdata = json.loads((tmp_path / "verification.json").read_text())
    assert vdata["postverify_violations"] == []
    assert vdata["postverify_revised"] is False


async def test_postverify_fail_open_on_revise_error(tmp_path: Path, monkeypatch: Any) -> None:
    """If the revise dispatch raises, the original (violating) answer is kept — fail-open."""

    async def _disp(
        self: Any,
        task: Any,
        dbp: Any,
        sd: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        if "does not satisfy" in extra_instructions:
            raise RuntimeError("boom")
        return ("A, B, C", 5, 1.0)

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    suite = DabSuite(driver="claude-mcp", postverify=True)
    text, _tc, _lat = await suite._run_trial_verified(_topn_task(), Path("x"), tmp_path)

    assert text == "A, B, C"  # original kept despite the violation — fail-open

    vdata = json.loads((tmp_path / "verification.json").read_text())
    assert vdata["postverify_violations"]  # violation was detected
    assert vdata["postverify_revised"] is False  # but the revise dispatch failed


async def test_postverify_off_byte_identical(tmp_path: Path, monkeypatch: Any) -> None:
    """postverify=False (default) must be byte-identical to the pre-postverify off-path:
    a single dispatch, no verification.json."""
    calls = {"n": 0}

    async def _disp(
        self: Any,
        task: Any,
        dbp: Any,
        sd: Any,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        calls["n"] += 1
        return ("A, B, C", 5, 1.0)  # would violate "top 5" if postverify were on

    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    suite = DabSuite(driver="claude-mcp")  # postverify defaults to False
    text, _tc, _lat = await suite._run_trial_verified(_topn_task(), Path("x"), tmp_path)

    assert text == "A, B, C" and calls["n"] == 1
    assert not (tmp_path / "verification.json").exists()


def test_eval_dab_threads_postverify(monkeypatch: Any, tmp_path: Path) -> None:
    """--agent-postverify must reach DabSuite(postverify=True)."""
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
            "--agent-postverify",
            "--output-dir",
            str(tmp_path / "r"),
            "--datasets",
            "deps_dev_v1",
        ]
    )
    assert captured.get("postverify") is True
