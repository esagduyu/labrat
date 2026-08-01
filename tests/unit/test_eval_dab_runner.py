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


def test_config_records_git_provenance_of_the_executing_checkout(tmp_path: Path) -> None:
    """config.json must record enough of the executing checkout's git state to
    decide comparability later — not just flags. See provenance.py."""
    import subprocess

    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "run"

    assert main(["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)]) == 0

    cfg = json.loads((output_dir / "config.json").read_text())
    provenance = cfg["provenance"]
    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert provenance["git_commit"] == expected_commit
    assert provenance["git_unavailable"] is False
    assert isinstance(provenance["git_dirty"], bool)


def test_resume_at_a_drifted_commit_marks_provenance_mixed_and_keeps_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PLANTED (required plant d, I2): a shard's own retry loop can span commits
    across a resume (the runner scripts retry each shard up to 6-8 times with
    1h sleeps). config.json must not silently attest only the LAST attempt's
    code -- it must record that the run spans more than one code state, so
    check_comparability can refuse rather than certifying a commit mixture."""
    from labrat.eval.benchmarks.dab.provenance import check_comparability
    from scripts import eval_dab
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "run"

    provenance_calls = iter(
        [
            {
                "git_commit": "a" * 40,
                "git_branch": "main",
                "git_dirty": False,
                "git_diff_files": [],
                "git_diff_sha256": "0" * 64,
                "git_unavailable": False,
            },
            {
                "git_commit": "b" * 40,
                "git_branch": "main",
                "git_dirty": False,
                "git_diff_files": [],
                "git_diff_sha256": "1" * 64,
                "git_unavailable": False,
            },
        ]
    )
    monkeypatch.setattr(eval_dab, "capture_git_provenance", lambda *a, **k: next(provenance_calls))

    assert main(["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)]) == 0
    first_cfg = json.loads((output_dir / "config.json").read_text())
    assert first_cfg["provenance"]["git_commit"] == "a" * 40
    assert not first_cfg["provenance"].get("provenance_mixed")

    # Resume: the fresh capture now reports a different commit -- code moved
    # underneath this output dir between the first invocation and this one.
    assert main(["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)]) == 0
    second_cfg = json.loads((output_dir / "config.json").read_text())
    assert second_cfg["provenance"]["git_commit"] == "b" * 40
    assert second_cfg["provenance"]["provenance_mixed"] is True
    history = second_cfg["provenance"]["provenance_history"]
    assert history[-1]["git_commit"] == "a" * 40

    result = check_comparability(second_cfg["provenance"], second_cfg["provenance"])
    assert result.comparable is False
    assert result.verdict == "provenance_mixed"


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
    assert config["llm_classify_model"] == "gpt-5.6-luna"
    assert config["llm_classify_reasoning"] == "max"
    assert config["llm_classify_concurrency"] == 1


def test_codex_dedicated_classifier_config_is_persisted_and_resume_safe(
    tmp_path: Path,
) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "classifier-low"
    args = [
        "--dab-dir",
        str(dab_dir),
        "--output-dir",
        str(output_dir),
        "--driver",
        "labrat-agent",
        "--agent-provider",
        "codex",
        "--agent-model",
        "gpt-5.6-luna",
        "--agent-reasoning",
        "max",
        "--llm-classify-model",
        "gpt-5.6-luna",
        "--llm-classify-reasoning",
        "low",
        "--llm-classify-concurrency",
        "2",
    ]
    assert main(args) == 0
    config = json.loads((output_dir / "config.json").read_text())
    assert config["agent_reasoning"] == "max"
    assert config["llm_classify_model"] == "gpt-5.6-luna"
    assert config["llm_classify_reasoning"] == "low"
    assert config["llm_classify_concurrency"] == 2
    assert main(["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)]) == 0
    with pytest.raises(SystemExit, match=r"Resume conflict.*llm-classify-concurrency"):
        main([*args[:-1], "1"])


def test_bounded_classifier_and_terminal_timeout_config_is_resume_safe(tmp_path: Path) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "bounded"
    base = ["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)]
    args = [*base, "--llm-classify-row-budget", "200", "--terminalize-timeouts"]

    assert main(args) == 0
    config = json.loads((output_dir / "config.json").read_text())
    assert config["llm_classify_row_budget"] == 200
    assert config["terminalize_timeouts"] is True
    assert main(base) == 0
    with pytest.raises(SystemExit, match=r"Resume conflict.*llm-classify-row-budget"):
        main([*base, "--llm-classify-row-budget", "100"])
    with pytest.raises(SystemExit, match=r"Resume conflict.*terminalize-timeouts"):
        main([*base, "--no-terminalize-timeouts"])


def test_legacy_semantic_run_rejects_new_classifier_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "legacy"
    output_dir.mkdir()
    (output_dir / "config.json").write_text(
        json.dumps(
            {
                "driver": "labrat-agent",
                "agent_provider": "codex",
                "agent_model": "gpt-5.6-luna",
                "agent_reasoning": "max",
                "n_trials": 1,
            }
        )
    )
    (output_dir / "trials.jsonl").write_text(
        _make_trial("ds:1", trial_num=0, passed=True).model_dump_json() + "\n"
    )
    with pytest.raises(SystemExit):
        main(
            [
                "--dab-dir",
                str(dab_dir),
                "--output-dir",
                str(output_dir),
                "--llm-classify-reasoning",
                "low",
            ]
        )
    assert "fresh --output-dir" in capsys.readouterr().err


def test_legacy_resume_without_classifier_override_preserves_config_shape(
    tmp_path: Path,
) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "legacy-empty"
    output_dir.mkdir()
    (output_dir / "config.json").write_text(
        json.dumps(
            {
                "driver": "labrat-agent",
                "agent_provider": "codex",
                "agent_model": "gpt-5.6-luna",
                "agent_reasoning": "max",
                "n_trials": 1,
            }
        )
    )
    assert main(["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)]) == 0
    config = json.loads((output_dir / "config.json").read_text())
    assert "llm_classify_model" not in config
    assert "llm_classify_reasoning" not in config
    assert "llm_classify_concurrency" not in config


def test_configless_semantic_run_is_not_resumed(tmp_path: Path) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "configless"
    output_dir.mkdir()
    (output_dir / "trials.jsonl").write_text(
        _make_trial("ds:1", trial_num=0, passed=True).model_dump_json() + "\n"
    )
    with pytest.raises(SystemExit):
        main(["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)])


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


def test_agent_taxonomy_defaults_off_persists_and_rejects_resume_conflict(
    tmp_path: Path,
) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "run-tax"
    base = ["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)]

    assert main(base) == 0
    initial = json.loads((output_dir / "config.json").read_text())
    assert initial["agent_taxonomy"] is False

    output_dir2 = tmp_path / "run-tax-on"
    base2 = ["--dab-dir", str(dab_dir), "--output-dir", str(output_dir2)]
    assert main([*base2, "--agent-taxonomy"]) == 0
    enabled = json.loads((output_dir2 / "config.json").read_text())
    assert enabled["agent_taxonomy"] is True

    # Omitting the flag on resume restores the stored value.
    assert main(base2) == 0
    resumed = json.loads((output_dir2 / "config.json").read_text())
    assert resumed["agent_taxonomy"] is True

    with pytest.raises(SystemExit, match=r"Resume conflict.*agent-taxonomy"):
        main([*base2, "--no-agent-taxonomy"])


class _ResumeRerunSuite:
    """Fake suite for resume-dedup tests (Fix 5): returns a pre-scripted result per
    (task_id, trial_num) and records every dispatched call."""

    name = "dab"

    def __init__(self, task_ids: list[str], responses: dict[tuple[str, int], TrialResult]) -> None:
        self._task_ids = task_ids
        self._responses = responses
        self.calls: list[tuple[str, int]] = []

    def tasks(self) -> list[BenchmarkTask]:
        return [_make_task(tid) for tid in self._task_ids]

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult:
        self.calls.append((task.id, trial_num))
        return self._responses[(task.id, trial_num)]

    def aggregate(self, results: list[TrialResult]) -> AggregateScore:
        return AggregateScore(
            overall=1.0,
            per_task={},
            n_tasks=len(self._task_ids),
            n_trials=len(results),
            n_passes=sum(1 for r in results if r.passed),
        )


def _infra_trial(task_id: str, trial_num: int, reason: str = "infra:rate_limit") -> TrialResult:
    return TrialResult(
        task_id=task_id,
        trial_num=trial_num,
        passed=False,
        reason=reason,
        latency_seconds=0.0,
        artifact={"type": "text", "payload": "boom"},
    )


def test_resume_replaces_stale_infra_row_with_semantic_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 5: a rerun that finally succeeds must REPLACE the old infra row, not
    merely be appended alongside it — the final file has exactly one row for
    the (task_id, trial_num) key."""
    from scripts import eval_dab

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "resume-run"
    output_dir.mkdir()

    (output_dir / "trials.jsonl").write_text(_infra_trial("ds:1", 0).model_dump_json() + "\n")
    (output_dir / "config.json").write_text(json.dumps({"driver": "raw-bash", "n_trials": 1}))

    fresh_semantic = _make_trial("ds:1", 0, passed=True)
    suite = _ResumeRerunSuite(["ds:1"], {("ds:1", 0): fresh_semantic})
    monkeypatch.setattr(eval_dab, "DabSuite", lambda **_kwargs: suite)

    rc = eval_dab.main(["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)])

    assert rc == 0
    assert suite.calls == [("ds:1", 0)]  # the infra row was NOT treated as completed
    rows = [json.loads(line) for line in (output_dir / "trials.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["task_id"] == "ds:1"
    assert rows[0]["trial_num"] == 0
    assert rows[0]["reason"] is None
    assert rows[0]["passed"] is True


def test_resume_keeps_both_rows_when_rerun_is_still_infra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 5 counterpart: a rerun that is STILL infra keeps the append-only audit
    trail — both the old and new infra rows survive."""
    from scripts import eval_dab

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "still-infra-run"
    output_dir.mkdir()

    (output_dir / "trials.jsonl").write_text(_infra_trial("ds:2", 0).model_dump_json() + "\n")
    (output_dir / "config.json").write_text(json.dumps({"driver": "raw-bash", "n_trials": 1}))

    new_infra = _infra_trial("ds:2", 0, reason="infra:agent_error")
    suite = _ResumeRerunSuite(["ds:2"], {("ds:2", 0): new_infra})
    monkeypatch.setattr(eval_dab, "DabSuite", lambda **_kwargs: suite)

    rc = eval_dab.main(["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)])

    assert rc == 0
    rows = [json.loads(line) for line in (output_dir / "trials.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    assert all(r["task_id"] == "ds:2" and r["trial_num"] == 0 for r in rows)
    reasons = sorted(r["reason"] for r in rows)
    assert reasons == ["infra:agent_error", "infra:rate_limit"]


def test_resume_preserves_infra_rows_for_trials_not_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix 5 must not over-delete: an infra row for a trial_num outside this run's
    range (not re-attempted) stays exactly as it was."""
    from scripts import eval_dab

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "partial-rerun"
    output_dir.mkdir()

    (output_dir / "trials.jsonl").write_text(
        _infra_trial("ds:3", 0).model_dump_json()
        + "\n"
        + _infra_trial("ds:3", 1).model_dump_json()
        + "\n"
    )
    # n_trials=1 restored from config.json -> only trial_num 0 is in range(1); trial_num 1
    # is never touched by this run.
    (output_dir / "config.json").write_text(json.dumps({"driver": "raw-bash", "n_trials": 1}))

    fresh_semantic = _make_trial("ds:3", 0, passed=True)
    suite = _ResumeRerunSuite(["ds:3"], {("ds:3", 0): fresh_semantic})
    monkeypatch.setattr(eval_dab, "DabSuite", lambda **_kwargs: suite)

    rc = eval_dab.main(["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)])

    assert rc == 0
    assert suite.calls == [("ds:3", 0)]
    rows = [json.loads(line) for line in (output_dir / "trials.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    by_trial = {r["trial_num"]: r for r in rows}
    assert by_trial[0]["reason"] is None and by_trial[0]["passed"] is True
    assert by_trial[1]["reason"] == "infra:rate_limit"  # untouched, still infra


def test_resume_dedup_fix_does_not_change_normal_no_infra_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normal (no prior infra) path is unaffected by Fix 5 — trials.jsonl content
    is exactly what the plain append writer would have produced."""
    from scripts import eval_dab

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "normal-run"

    fresh_semantic = _make_trial("ds:4", 0, passed=True)
    suite = _ResumeRerunSuite(["ds:4"], {("ds:4", 0): fresh_semantic})
    monkeypatch.setattr(eval_dab, "DabSuite", lambda **_kwargs: suite)

    rc = eval_dab.main(
        ["--dab-dir", str(dab_dir), "--output-dir", str(output_dir), "--n-trials", "1"]
    )

    assert rc == 0
    assert (output_dir / "trials.jsonl").read_text() == fresh_semantic.model_dump_json() + "\n"


def test_llm_classify_backend_persists_and_rejects_resume_conflict(tmp_path: Path) -> None:
    from scripts.eval_dab import main

    dab_dir = tmp_path / "empty_dab"
    dab_dir.mkdir()
    output_dir = tmp_path / "run-backend"
    base = ["--dab-dir", str(dab_dir), "--output-dir", str(output_dir)]

    assert main([*base, "--llm-classify-backend", "local-embed"]) == 0
    cfg = json.loads((output_dir / "config.json").read_text())
    assert cfg["llm_classify_backend"] == "local-embed"

    assert main(base) == 0  # restored on resume
    assert (
        json.loads((output_dir / "config.json").read_text())["llm_classify_backend"]
        == "local-embed"
    )

    with pytest.raises(SystemExit, match=r"Resume conflict.*llm-classify-backend"):
        main([*base, "--llm-classify-backend", "llm"])
