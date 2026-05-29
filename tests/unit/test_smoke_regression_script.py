"""Tests for scripts.run_smoke_regression — the comparison logic, not the live runs."""

from __future__ import annotations

from scripts.run_smoke_regression import RegressionVerdict, compare_against_baseline


def test_compare_pass_when_all_within_envelope():
    # With early-exit baseline: passes ∈ {0..n_runs}, current rate ∈ {0, 1/n_attempts}
    baseline = {"t1": {"passes": 3, "attempts": 9}, "t2": {"passes": 3, "attempts": 9}}
    current = {"t1": 1 / 3, "t2": 1 / 3}
    verdict = compare_against_baseline(baseline, current)
    assert verdict.kind == "pass"


def test_compare_hard_fail_on_strong_drop():
    # Hard fail: task that passed all 3 baseline runs now passes zero check attempts.
    baseline = {"t1": {"passes": 3, "attempts": 9}}
    current = {"t1": 0.0}
    verdict = compare_against_baseline(baseline, current)
    assert verdict.kind == "hard_fail"
    assert "t1" in verdict.message


def test_compare_soft_signal_on_small_drop():
    # t3 was solvable (passes=1 > 0) but not "always passing" (passes < 3),
    # so it can't trigger hard_fail; it going to 0 triggers soft_signal only.
    baseline = {
        "t1": {"passes": 3, "attempts": 9},
        "t2": {"passes": 3, "attempts": 9},
        "t3": {"passes": 1, "attempts": 9},
    }
    current = {"t1": 1 / 3, "t2": 1 / 3, "t3": 0.0}
    verdict = compare_against_baseline(baseline, current)
    assert verdict.kind == "soft_signal"


def test_verdict_exit_codes():
    assert RegressionVerdict(kind="pass", message="").exit_code == 0
    assert RegressionVerdict(kind="soft_signal", message="").exit_code == 0
    assert RegressionVerdict(kind="hard_fail", message="").exit_code == 1
