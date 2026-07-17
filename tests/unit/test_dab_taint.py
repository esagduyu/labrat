from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from labrat.eval.benchmarks.dab.taint import (
    AUDIT_ERROR,
    CHEATING,
    CLEAN,
    audit_run,
    classify_trial,
    gate,
    task_trial_dir_name,
)


def _trace_record(*, tool_input: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "tool": "run_sql",
        "input": tool_input or {"query": "SELECT 1"},
        "ok": True,
        "output": "1",
        "latency_ms": 1.5,
    }


def test_classify_flags_answer_key_read() -> None:
    assert classify_trial("cat query1/ground_truth.csv -> 42") == "external-oracle-cheating"
    assert classify_trial("SELECT COUNT(*) FROM orders") == "clean"


@pytest.mark.parametrize(
    ("task_id", "expected"),
    [
        ("dataset.v2:3", "dataset.v2_3__trial0"),
        ("dataset-v2:3", "dataset-v2_3__trial0"),
        ("dataset_v2:3", "dataset_v2_3__trial0"),
    ],
)
def test_task_trial_dir_name_accepts_existing_dataset_name_grammar(
    task_id: str,
    expected: str,
) -> None:
    assert task_trial_dir_name(task_id, 0) == expected


@pytest.mark.parametrize(
    "task_id",
    ["../escape:1", "nested/escape:1", "dataset..escape:1", "dataset:0"],
)
def test_task_trial_dir_name_rejects_traversal_and_invalid_query(task_id: str) -> None:
    with pytest.raises(ValueError, match="invalid DAB task_id"):
        task_trial_dir_name(task_id, 0)


def test_gate_blocks_on_contamination() -> None:
    ok, offenders = gate({"agnews:0": "external-oracle-cheating", "yelp:1": "clean"})
    assert ok is False and "agnews:0" in offenders


def test_gate_blocks_on_audit_error_and_unknown_verdict() -> None:
    ok, offenders = gate({"agnews:0": AUDIT_ERROR, "yelp:1": CLEAN, "stockindex:0": "unexpected"})

    assert ok is False
    assert offenders == ["agnews:0", "stockindex:0"]


def test_gate_passes_when_clean() -> None:
    ok, offenders = gate({"yelp:1": "clean", "yelp:2": "clean"})
    assert ok is True and offenders == []


def test_audit_run_scans_labrat_agent_trace(tmp_path: Path) -> None:
    """agent_tool_calls.jsonl (labrat-agent driver) must be scanned, not just
    mcp_tool_calls.jsonl (claude-mcp driver) — both drivers write into the same
    scratch dir and either can leak the answer key."""
    scratch_dir = tmp_path / "scratch"
    trial_dir = scratch_dir / "agnews_1__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent_tool_calls.jsonl").write_text(
        json.dumps(_trace_record(tool_input={"query": "cat query1/ground_truth.csv"})) + "\n"
    )

    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": "agnews:1", "trial_num": 0, "artifact": "", "reason": ""}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["agnews:1:0"] == CHEATING
    assert json.loads((tmp_path / "taint.json").read_text()) == verdicts


def test_audit_run_does_not_flag_clean_pass_with_validator_gt_message(tmp_path: Path) -> None:
    """C1 regression: the official DAB validators (agnews, bookreview, music_brainz)
    return `reason="Ground truth found in LLM output."` on a clean PASS — that text
    lives in the VALIDATOR's message, not agent output, and must never be
    substring-scanned. A passing trial with a benign artifact/trace must classify
    clean, and the gate must pass."""
    scratch_dir = tmp_path / "scratch"
    trial_dir = scratch_dir / "music_brainz_20k_1__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "mcp_tool_calls.jsonl").write_text(
        json.dumps(_trace_record(tool_input={"query": "SELECT COUNT(*) FROM artists"})) + "\n"
    )

    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps(
            {
                "task_id": "music_brainz_20k:1",
                "trial_num": 0,
                "artifact": {"type": "text", "payload": "42"},
                "reason": "Ground truth found in LLM output.",
            }
        )
        + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["music_brainz_20k:1:0"] == CLEAN
    ok, offenders = gate(verdicts)
    assert ok is True and offenders == []


def test_audit_run_flags_answer_key_in_artifact_payload(tmp_path: Path) -> None:
    """A real answer-key read reflected in the artifact payload (agent's actual
    answer text) must still be flagged, even with a benign `reason`."""
    scratch_dir = tmp_path / "scratch"
    trial_dir = scratch_dir / "agnews_1__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent_tool_calls.jsonl").write_text("")
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps(
            {
                "task_id": "agnews:1",
                "trial_num": 0,
                "artifact": {"type": "text", "payload": "cat query1/ground_truth.csv -> 42"},
                "reason": "some benign validator message",
            }
        )
        + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["agnews:1:0"] == CHEATING


def test_audit_run_marks_missing_trace_as_audit_error(tmp_path: Path) -> None:
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": "stockindex:1", "trial_num": 0, "artifact": "42"}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, tmp_path / "scratch")

    assert verdicts["stockindex:1:0"] == AUDIT_ERROR
    assert gate(verdicts) == (False, ["stockindex:1:0"])


def test_audit_run_marks_malformed_trace_as_audit_error(tmp_path: Path) -> None:
    trial_dir = tmp_path / "scratch" / "stockindex_1__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent_tool_calls.jsonl").write_text("{not-json}\n")
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": "stockindex:1", "trial_num": 0, "artifact": "42"}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, tmp_path / "scratch")

    assert verdicts["stockindex:1:0"] == AUDIT_ERROR


def test_audit_run_accepts_existing_empty_zero_tool_trace(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"driver": "labrat-agent"}))
    trial_dir = tmp_path / "scratch" / "stockindex_1__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent_tool_calls.jsonl").write_text("")
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": "stockindex:1", "trial_num": 0, "artifact": "42"}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, tmp_path / "scratch")

    assert verdicts["stockindex:1:0"] == CLEAN


def test_raw_bash_answer_only_audit_flags_contamination_without_trace(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"driver": "raw-bash"}))
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps(
            {
                "task_id": "stockindex:1",
                "trial_num": 0,
                "artifact": {"type": "text", "payload": "read ground_truth.csv"},
            }
        )
        + "\n"
    )

    verdicts = audit_run(trials_jsonl, tmp_path / "scratch")

    assert verdicts["stockindex:1:0"] == CHEATING


def test_audit_run_requires_driver_specific_trace(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"driver": "labrat-agent"}))
    trial_dir = tmp_path / "scratch" / "stockindex_1__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "mcp_tool_calls.jsonl").write_text("")
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": "stockindex:1", "trial_num": 0, "artifact": "42"}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, tmp_path / "scratch")

    assert verdicts["stockindex:1:0"] == AUDIT_ERROR


def test_audit_run_trusts_suite_contamination_backstop(tmp_path: Path) -> None:
    """`reason="contaminated:<tag>"` is the suite's own backstop signal and must be
    trusted directly as CHEATING, without needing the tag text itself to match a
    contamination pattern."""
    scratch_dir = tmp_path / "scratch"
    trial_dir = scratch_dir / "agnews_1__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent_tool_calls.jsonl").write_text("")
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps(
            {
                "task_id": "agnews:1",
                "trial_num": 0,
                "artifact": {"type": "text", "payload": "42"},
                "reason": "contaminated:unrestricted_bash",
            }
        )
        + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["agnews:1:0"] == CHEATING


def test_audit_run_missing_trace_takes_precedence_over_contamination_tag(tmp_path: Path) -> None:
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps(
            {
                "task_id": "agnews:1",
                "trial_num": 0,
                "artifact": {"type": "text", "payload": "42"},
                "reason": "contaminated:unrestricted_bash",
            }
        )
        + "\n"
    )

    verdicts = audit_run(trials_jsonl, tmp_path / "scratch")

    assert verdicts["agnews:1:0"] == AUDIT_ERROR


@pytest.mark.parametrize(
    ("task_id", "escaped_dir"),
    [
        ("../escape:1", "escape_1__trial0"),
        ("nested/escape:1", "scratch/nested/escape_1__trial0"),
    ],
)
def test_audit_run_rejects_task_id_path_traversal(
    tmp_path: Path,
    task_id: str,
    escaped_dir: str,
) -> None:
    (tmp_path / "scratch").mkdir(exist_ok=True)
    trial_dir = tmp_path / escaped_dir
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent_tool_calls.jsonl").write_text("")
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": task_id, "trial_num": 0, "artifact": "42"}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, tmp_path / "scratch")

    assert verdicts[f"{task_id}:0"] == AUDIT_ERROR


@pytest.mark.parametrize("symlink_target", ["trial-dir", "trace-file"])
def test_audit_run_rejects_symlinked_trace_paths(
    tmp_path: Path,
    symlink_target: str,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "agent_tool_calls.jsonl").write_text("")
    trial_dir = scratch / "stockindex_1__trial0"
    if symlink_target == "trial-dir":
        trial_dir.symlink_to(outside, target_is_directory=True)
    else:
        trial_dir.mkdir()
        (trial_dir / "agent_tool_calls.jsonl").symlink_to(outside / "agent_tool_calls.jsonl")
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": "stockindex:1", "trial_num": 0, "artifact": "42"}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch)

    assert verdicts["stockindex:1:0"] == AUDIT_ERROR


def test_audit_run_flags_obfuscated_answer_key_read_in_trace(tmp_path: Path) -> None:
    """Taint v2: concat-folded literals must be caught even when no raw needle
    appears anywhere in the trace text (evasion case from the 2026-07-16 audit)."""
    scratch_dir = tmp_path / "scratch"
    trial_dir = scratch_dir / "agnews_1__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent_tool_calls.jsonl").write_text(
        json.dumps(
            _trace_record(
                tool_input={
                    "query": "SELECT * FROM read_csv_auto(concat('ground_', 'tr' || 'uth.csv'))"
                }
            )
        )
        + "\n"
    )
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": "agnews:1", "trial_num": 0, "artifact": "", "reason": ""}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["agnews:1:0"] == CHEATING


def test_audit_run_flags_unsanctioned_attach_path(tmp_path: Path) -> None:
    """Taint v2: a file source outside the benchmark's query_dataset layout is
    flagged structurally even though its text matches no needle."""
    scratch_dir = tmp_path / "scratch"
    trial_dir = scratch_dir / "yelp_1__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent_tool_calls.jsonl").write_text(
        json.dumps(
            {
                "tool": "attach_database",
                "input": {"path": "/opt/elsewhere/answers.db", "alias": "a"},
                "ok": True,
                "output": "attached",
                "latency_ms": 1,
            }
        )
        + "\n"
    )
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": "yelp:1", "trial_num": 0, "artifact": "", "reason": ""}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["yelp:1:0"] == CHEATING


def test_audit_run_reports_structural_findings_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P3-5: when the structural layer fires, the audit surfaces the first few
    findings (task:trial, tag, detail) on stderr so gate offenders are
    actionable without re-running the scan by hand."""
    scratch_dir = tmp_path / "scratch"
    trial_dir = scratch_dir / "yelp_1__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent_tool_calls.jsonl").write_text(
        json.dumps(
            {
                "tool": "attach_database",
                "input": {"path": "/opt/elsewhere/answers.db", "alias": "a"},
                "ok": True,
                "output": "attached",
                "latency_ms": 1,
            }
        )
        + "\n"
    )
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": "yelp:1", "trial_num": 0, "artifact": "", "reason": ""}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["yelp:1:0"] == CHEATING
    err = capsys.readouterr().err
    assert "yelp:1:0" in err
    assert "unsanctioned_path" in err
    assert "attach_database" in err


def _submission_corpus_dir() -> Path | None:
    """The shipped final-270 package, wherever this checkout lives (worktrees
    share the repo but not untracked runs/, so fall back to the main clone)."""
    candidates = (
        Path(__file__).resolve().parents[2] / "runs/dab/submission-gpt56-luna-max-ledger-final-270",
        Path.home() / "repos/labrat/runs/dab/submission-gpt56-luna-max-ledger-final-270",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


@pytest.mark.skipif(
    _submission_corpus_dir() is None,
    reason="final-270 submission corpus not present on this machine",
)
def test_final_270_submission_corpus_audits_clean(tmp_path: Path) -> None:
    """P3-4: the shipped 270-trace evidence package must re-audit 270/270 clean
    under the full gate (v1 needles + v2 structural layers). The run directory
    itself stays read-only: inputs are copied so taint.json lands in tmp."""
    corpus = _submission_corpus_dir()
    assert corpus is not None
    shutil.copy(corpus / "trials.jsonl", tmp_path / "trials.jsonl")
    shutil.copy(corpus / "config.json", tmp_path / "config.json")

    verdicts = audit_run(tmp_path / "trials.jsonl", corpus / "scratch")

    assert len(verdicts) == 270
    assert all(verdict == CLEAN for verdict in verdicts.values())
    ok, offenders = gate(verdicts)
    assert ok is True and offenders == []


def test_audit_run_keeps_sanctioned_absolute_attach_clean(tmp_path: Path) -> None:
    """Taint v2 regression guard: the real corpus' attach pattern (absolute path
    under query_dataset) must stay clean."""
    scratch_dir = tmp_path / "scratch"
    trial_dir = scratch_dir / "yelp_1__trial0"
    trial_dir.mkdir(parents=True)
    (trial_dir / "agent_tool_calls.jsonl").write_text(
        json.dumps(
            {
                "tool": "attach_database",
                "input": {
                    "path": "/Users/x/repos/DataAgentBench/query_yelp/query_dataset/yelp_user.db",
                    "alias": "yelp_user",
                },
                "ok": True,
                "output": "attached",
                "latency_ms": 1,
            }
        )
        + "\n"
    )
    trials_jsonl = tmp_path / "trials.jsonl"
    trials_jsonl.write_text(
        json.dumps({"task_id": "yelp:1", "trial_num": 0, "artifact": "", "reason": ""}) + "\n"
    )

    verdicts = audit_run(trials_jsonl, scratch_dir)

    assert verdicts["yelp:1:0"] == CLEAN
