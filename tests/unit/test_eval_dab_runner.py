"""Tests for eval_dab.py runner logic — resumability and n_trials semantics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from labrat.eval.types import AggregateScore, BenchmarkTask, TrialResult


def _make_task(task_id: str = "ds:1") -> BenchmarkTask:
    return BenchmarkTask(
        id=task_id,
        benchmark="dab",
        prompt="How many?",
        config={
            "dataset": task_id.split(":")[0],
            "query_num": task_id.split(":")[1],
            "db_config_path": "/fake/db_config.yaml",
            "validator_path": "/fake/validate.py",
            "hints": False,
        },
    )


def _make_trial(task_id: str, trial_num: int, passed: bool) -> TrialResult:
    return TrialResult(
        task_id=task_id,
        trial_num=trial_num,
        passed=passed,
        reason=None,
        latency_seconds=1.0,
        tool_calls=0,
        artifact={"type": "text", "payload": "42"},
    )


def test_runner_skips_already_completed_trials(tmp_path: Path) -> None:
    """If trials.jsonl already contains (task_id, trial_num), that trial is not re-run."""
    from scripts.eval_dab import _load_completed_trials

    trials_jsonl = tmp_path / "trials.jsonl"
    existing = _make_trial("ds:1", trial_num=0, passed=True)
    trials_jsonl.write_text(existing.model_dump_json() + "\n")

    completed = _load_completed_trials(trials_jsonl)
    assert ("ds:1", 0) in completed
    assert ("ds:1", 1) not in completed


def test_runner_appends_new_trials_to_existing_jsonl(tmp_path: Path) -> None:
    """Re-running with a partial trials.jsonl appends only missing trials."""
    from scripts.eval_dab import _load_completed_trials

    trials_jsonl = tmp_path / "trials.jsonl"
    t0 = _make_trial("ds:1", trial_num=0, passed=True)
    trials_jsonl.write_text(t0.model_dump_json() + "\n")

    completed = _load_completed_trials(trials_jsonl)
    # trial 1 not in completed — would be run
    assert ("ds:1", 1) not in completed
    # trial 0 already there — would be skipped
    assert ("ds:1", 0) in completed


def test_n_trials_defaults_to_5_and_is_resume_safe(tmp_path: Path) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()

    default_run = tmp_path / "default-run"
    assert main(["--dab-dir", str(dab_dir), "--output-dir", str(default_run)]) == 0
    assert json.loads((default_run / "config.json").read_text())["n_trials"] == 5

    ablation_run = tmp_path / "n3-run"
    base = ["--dab-dir", str(dab_dir), "--output-dir", str(ablation_run)]
    assert main([*base, "--n-trials", "3"]) == 0
    assert json.loads((ablation_run / "config.json").read_text())["n_trials"] == 3

    # Omitting --n-trials on resume preserves the original ablation cardinality.
    assert main(base) == 0
    assert json.loads((ablation_run / "config.json").read_text())["n_trials"] == 3

    with pytest.raises(SystemExit, match=r"Resume conflict.*n-trials"):
        main([*base, "--n-trials", "5"])


def test_dab_ablation_flags_keep_backward_compatible_defaults(tmp_path: Path) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "run"

    assert main(["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)]) == 0

    cfg = json.loads((output_dir / "config.json").read_text())
    assert cfg["hints"] is False
    assert cfg["agent_levers"] is True
    assert cfg["agent_ledger"] is True


def test_raw_bash_nonempty_run_is_answer_audited_report_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import eval_dab

    class RawReportSuite:
        name = "dab"

        def tasks(self) -> list[BenchmarkTask]:
            return [_make_task("ds:1")]

        async def run_trial(
            self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
        ) -> TrialResult:
            return _make_trial(task.id, trial_num, passed=True)

        def aggregate(self, results: list[TrialResult]) -> AggregateScore:
            return AggregateScore(
                overall=1.0,
                per_task={"ds:1": 1.0},
                n_tasks=1,
                n_trials=len(results),
                n_passes=len(results),
            )

        def write_submission(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("raw-bash must never write submission.json")

    monkeypatch.setattr(eval_dab, "DabSuite", lambda **_kwargs: RawReportSuite())
    dab_dir = tmp_path / "empty-dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "raw-report"

    rc = eval_dab.main(
        [
            "--dab-dir",
            str(dab_dir),
            "--output-dir",
            str(output_dir),
            "--n-trials",
            "1",
            "--driver",
            "raw-bash",
        ]
    )

    assert rc == 0
    config = json.loads((output_dir / "config.json").read_text())
    assert config["submission_eligibility"] == "legacy-report-only"
    assert config["trace_contract"] == "answer-only"
    assert config["trace_attempt_policy"] == "not-applicable"
    assert json.loads((output_dir / "taint.json").read_text()) == {"ds:1:0": "clean"}
    assert "Legacy report-only" in (output_dir / "report.md").read_text()
    assert not (output_dir / "submission.json").exists()


def test_raw_bash_rejects_stale_submission_before_runner_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import eval_dab

    calls = 0

    class NoRunSuite:
        name = "dab"

        def tasks(self) -> list[BenchmarkTask]:
            return [_make_task("ds:1")]

        async def run_trial(
            self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
        ) -> TrialResult:
            nonlocal calls
            calls += 1
            raise AssertionError("stale raw-bash output must fail before execution")

    monkeypatch.setattr(eval_dab, "DabSuite", lambda **_kwargs: NoRunSuite())
    dab_dir = tmp_path / "empty-dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "stale-raw"
    output_dir.mkdir()
    stale = b'[{"answer":"historical"}]\n'
    (output_dir / "submission.json").write_bytes(stale)

    with pytest.raises(SystemExit) as captured:
        eval_dab.main(
            [
                "--dab-dir",
                str(dab_dir),
                "--output-dir",
                str(output_dir),
                "--driver",
                "raw-bash",
                "--n-trials",
                "1",
            ]
        )

    assert captured.value.code == 2
    assert "stale submission.json" in capsys.readouterr().err
    assert calls == 0
    assert (output_dir / "submission.json").read_bytes() == stale
    assert not (output_dir / "config.json").exists()
    assert not (output_dir / "trials.jsonl").exists()


def test_dab_boolean_flags_restore_on_resume_and_reject_conflicts(tmp_path: Path) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "run"
    base = ["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)]

    assert main([*base, "--hints", "--no-agent-levers", "--no-agent-ledger"]) == 0
    initial = json.loads((output_dir / "config.json").read_text())
    assert initial["hints"] is True
    assert initial["agent_levers"] is False
    assert initial["agent_ledger"] is False

    # Omitting all three flags on resume restores the concrete prior values.
    assert main(base) == 0
    resumed = json.loads((output_dir / "config.json").read_text())
    assert resumed["hints"] is True
    assert resumed["agent_levers"] is False
    assert resumed["agent_ledger"] is False

    with pytest.raises(SystemExit, match=r"Resume conflict.*agent-levers"):
        main([*base, "--agent-levers"])
    with pytest.raises(SystemExit, match=r"Resume conflict.*hints"):
        main([*base, "--no-hints"])


def test_new_codex_run_defaults_to_luna_max_and_persists_concrete_config(
    tmp_path: Path,
) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "run"

    assert (
        main(
            [
                "--dab-dir",
                str(dab_dir),
                "--output-dir",
                str(output_dir),
                "--driver",
                "labrat-agent",
                "--agent-provider",
                "codex",
            ]
        )
        == 0
    )
    config = json.loads((output_dir / "config.json").read_text())
    assert config["agent_model"] == "gpt-5.6-luna"
    assert config["agent_reasoning"] == "max"


def test_codex_gpt55_default_and_invalid_pair_fail_fast(tmp_path: Path) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "run"
    base = [
        "--dab-dir",
        str(dab_dir),
        "--output-dir",
        str(output_dir),
        "--driver",
        "labrat-agent",
        "--agent-provider",
        "codex",
        "--agent-model",
        "gpt-5.5",
    ]

    assert main(base) == 0
    config = json.loads((output_dir / "config.json").read_text())
    assert config["agent_reasoning"] == "medium"

    invalid_model = [
        "--dab-dir",
        str(dab_dir),
        "--output-dir",
        str(tmp_path / "invalid-model"),
        "--driver",
        "labrat-agent",
        "--agent-provider",
        "codex",
        "--agent-model",
        "gpt-5.6-moon",
    ]
    with pytest.raises(SystemExit):
        main(invalid_model)

    invalid_effort = [*base]
    invalid_effort[invalid_effort.index(str(output_dir))] = str(tmp_path / "invalid-effort")
    with pytest.raises(SystemExit):
        main([*invalid_effort, "--agent-reasoning", "max"])


def test_codex_sol_ultra_is_a_valid_subscription_configuration(tmp_path: Path) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "sol-ultra"

    assert (
        main(
            [
                "--dab-dir",
                str(dab_dir),
                "--output-dir",
                str(output_dir),
                "--driver",
                "labrat-agent",
                "--agent-provider",
                "codex",
                "--agent-model",
                "gpt-5.6-sol",
                "--agent-reasoning",
                "ultra",
            ]
        )
        == 0
    )
    config = json.loads((output_dir / "config.json").read_text())
    assert config["agent_model"] == "gpt-5.6-sol"
    assert config["agent_reasoning"] == "ultra"


def test_rate_limit_persists_one_retryable_row_then_stops_before_packaging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import eval_dab

    class RateLimitedSuite:
        name = "dab"

        def __init__(self) -> None:
            self.calls = 0

        def tasks(self) -> list[BenchmarkTask]:
            return [_make_task("ds:1"), _make_task("ds:2")]

        async def run_trial(
            self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
        ) -> TrialResult:
            self.calls += 1
            return TrialResult(
                task_id=task.id,
                trial_num=trial_num,
                passed=False,
                reason="infra:rate_limit",
                latency_seconds=0.0,
                artifact={"type": "text", "payload": "HTTPStatusError: 429"},
                meta={
                    "rate_limit": {
                        "resets_at": 1783767751,
                        "resets_in_seconds": 321,
                    }
                },
            )

    suite = RateLimitedSuite()
    monkeypatch.setattr(eval_dab, "DabSuite", lambda **_kwargs: suite)

    def forbidden_audit(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise AssertionError("taint packaging must not run after a rate limit")

    monkeypatch.setattr(eval_dab, "audit_run", forbidden_audit)
    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "rate-limited-run"

    rc = eval_dab.main(["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)])

    assert rc == eval_dab._RATE_LIMIT_EXIT_CODE == 4
    assert suite.calls == 1
    rows = (output_dir / "trials.jsonl").read_text().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["reason"] == "infra:rate_limit"
    assert row["meta"]["rate_limit"]["resets_in_seconds"] == 321
    assert ("ds:1", 0) not in eval_dab._load_completed_trials(output_dir / "trials.jsonl")
    assert not (output_dir / "taint.json").exists()
    assert not (output_dir / "submission.json").exists()
    assert not (output_dir / "report.md").exists()
    message = capsys.readouterr().err
    assert "DAB run paused after an API rate limit" in message
    assert "reset_at=2026-07-11T11:02:31+00:00" in message
    assert "resets_in_seconds=321" in message
    assert f"--output-dir {output_dir}" in message


def test_runner_normalizes_textual_429_to_retryable_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import eval_dab

    class TextRateLimitedSuite:
        name = "dab"

        def tasks(self) -> list[BenchmarkTask]:
            return [_make_task("ds:1"), _make_task("ds:2")]

        async def run_trial(
            self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
        ) -> TrialResult:
            return TrialResult(
                task_id=task.id,
                trial_num=trial_num,
                passed=False,
                reason="infra:agent_error",
                latency_seconds=0.0,
                artifact={"type": "text", "payload": "API Error: 429 Too Many Requests"},
            )

    monkeypatch.setattr(eval_dab, "DabSuite", lambda **_kwargs: TextRateLimitedSuite())
    monkeypatch.setattr(
        eval_dab,
        "audit_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("textual 429 must stop before audit")
        ),
    )
    dab_dir = tmp_path / "empty-dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "text-429"

    rc = eval_dab.main(
        ["--dab-dir", str(dab_dir), "--output-dir", str(output_dir), "--n-trials", "1"]
    )

    assert rc == eval_dab._RATE_LIMIT_EXIT_CODE
    rows = [json.loads(line) for line in (output_dir / "trials.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["reason"] == "infra:rate_limit"
