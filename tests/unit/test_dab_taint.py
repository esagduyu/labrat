from __future__ import annotations

import json
from pathlib import Path

from labrat.eval.benchmarks.dab.taint import CHEATING, audit_run, classify_trial, gate


def test_classify_flags_answer_key_read() -> None:
    assert classify_trial("cat query1/ground_truth.csv -> 42") == "external-oracle-cheating"
    assert classify_trial("SELECT COUNT(*) FROM orders") == "clean"


def test_gate_blocks_on_contamination() -> None:
    ok, offenders = gate({"agnews:0": "external-oracle-cheating", "yelp:1": "clean"})
    assert ok is False and "agnews:0" in offenders


def test_gate_passes_when_clean() -> None:
    ok, offenders = gate({"yelp:1": "clean", "yelp:2": "clean"})
    assert ok is True and offenders == []


def test_audit_run_scans_labrat_agent_trace(tmp_path: Path) -> None:
    """agent_tool_calls.jsonl (labrat-agent driver) must be scanned, not just
    mcp_tool_calls.jsonl (claude-mcp driver) — both drivers write into the same
    scratch dir and either can leak the answer key."""
    scratch_dir = tmp_path / "scratch"
    trial_dir = scratch_dir / "agnews__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent_tool_calls.jsonl").write_text(
        json.dumps({"tool": "run_sql", "input": "cat query1/ground_truth.csv"}) + "\n"
    )

    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": "agnews", "trial_num": 0, "artifact": "", "reason": ""}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["agnews:0"] == CHEATING
    assert json.loads((tmp_path / "taint.json").read_text()) == verdicts
