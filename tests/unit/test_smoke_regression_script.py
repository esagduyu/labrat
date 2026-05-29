"""Tests for scripts.run_smoke_regression — the comparison logic, not the live runs."""

from __future__ import annotations

from scripts.run_smoke_regression import RegressionVerdict, compare_against_baseline


def test_compare_pass_when_all_within_envelope():
    baseline = {"t1": {"passes": 8, "attempts": 9}, "t2": {"passes": 9, "attempts": 9}}
    current = {"t1": 8 / 9, "t2": 9 / 9}
    verdict = compare_against_baseline(baseline, current)
    assert verdict.kind == "pass"


def test_compare_hard_fail_on_strong_drop():
    baseline = {"t1": {"passes": 8, "attempts": 9}}
    current = {"t1": 3 / 9}
    verdict = compare_against_baseline(baseline, current)
    assert verdict.kind == "hard_fail"
    assert "t1" in verdict.message


def test_compare_soft_signal_on_small_drop():
    baseline = {
        "t1": {"passes": 9, "attempts": 9},
        "t2": {"passes": 9, "attempts": 9},
        "t3": {"passes": 9, "attempts": 9},
    }
    current = {"t1": 1.0, "t2": 1.0, "t3": 0.66}  # one task drops a bit
    verdict = compare_against_baseline(baseline, current)
    assert verdict.kind in ("soft_signal", "pass")


def test_verdict_exit_codes():
    assert RegressionVerdict(kind="pass", message="").exit_code == 0
    assert RegressionVerdict(kind="soft_signal", message="").exit_code == 0
    assert RegressionVerdict(kind="hard_fail", message="").exit_code == 1
