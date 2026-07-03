from __future__ import annotations

from labrat.eval.benchmarks.dab.taint import classify_trial, gate


def test_classify_flags_answer_key_read() -> None:
    assert classify_trial("cat query1/ground_truth.csv -> 42") == "external-oracle-cheating"
    assert classify_trial("SELECT COUNT(*) FROM orders") == "clean"


def test_gate_blocks_on_contamination() -> None:
    ok, offenders = gate({"agnews:0": "external-oracle-cheating", "yelp:1": "clean"})
    assert ok is False and "agnews:0" in offenders


def test_gate_passes_when_clean() -> None:
    ok, offenders = gate({"yelp:1": "clean", "yelp:2": "clean"})
    assert ok is True and offenders == []
