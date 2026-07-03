from __future__ import annotations

import json
from pathlib import Path

from labrat.eval.benchmarks.dab.taint import CHEATING, CLEAN, audit_run, classify_trial, gate


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


def test_audit_run_does_not_flag_clean_pass_with_validator_gt_message(tmp_path: Path) -> None:
    """C1 regression: the official DAB validators (agnews, bookreview, music_brainz)
    return `reason="Ground truth found in LLM output."` on a clean PASS — that text
    lives in the VALIDATOR's message, not agent output, and must never be
    substring-scanned. A passing trial with a benign artifact/trace must classify
    clean, and the gate must pass."""
    scratch_dir = tmp_path / "scratch"
    trial_dir = scratch_dir / "music_brainz_20k__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "mcp_tool_calls.jsonl").write_text(
        json.dumps({"tool": "run_sql", "input": "SELECT COUNT(*) FROM artists"}) + "\n"
    )

    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps(
            {
                "task_id": "music_brainz_20k",
                "trial_num": 0,
                "artifact": {"type": "text", "payload": "42"},
                "reason": "Ground truth found in LLM output.",
            }
        )
        + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["music_brainz_20k:0"] == CLEAN
    ok, offenders = gate(verdicts)
    assert ok is True and offenders == []


def test_audit_run_flags_answer_key_in_artifact_payload(tmp_path: Path) -> None:
    """A real answer-key read reflected in the artifact payload (agent's actual
    answer text) must still be flagged, even with a benign `reason`."""
    scratch_dir = tmp_path / "scratch"
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps(
            {
                "task_id": "agnews",
                "trial_num": 0,
                "artifact": {"type": "text", "payload": "cat query1/ground_truth.csv -> 42"},
                "reason": "some benign validator message",
            }
        )
        + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["agnews:0"] == CHEATING


def test_audit_run_trusts_suite_contamination_backstop(tmp_path: Path) -> None:
    """`reason="contaminated:<tag>"` is the suite's own backstop signal and must be
    trusted directly as CHEATING, without needing the tag text itself to match a
    contamination pattern."""
    scratch_dir = tmp_path / "scratch"
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps(
            {
                "task_id": "agnews",
                "trial_num": 0,
                "artifact": {"type": "text", "payload": "42"},
                "reason": "contaminated:unrestricted_bash",
            }
        )
        + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["agnews:0"] == CHEATING
