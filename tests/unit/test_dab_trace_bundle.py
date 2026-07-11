from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_dab_trace_bundle import (
    OFFICIAL_QUERY_COUNTS,
    BundleError,
    build_bundle,
)


def _trial(task_id: str, trial_num: int, *, reason: str = "validated") -> dict[str, object]:
    return {
        "task_id": task_id,
        "trial_num": trial_num,
        "passed": True,
        "reason": reason,
        "latency_seconds": 1.0,
        "tool_calls": 0,
        "artifact": {"type": "text", "payload": "42"},
    }


def _submission(task_id: str, trial_num: int) -> dict[str, object]:
    dataset, query = task_id.split(":", 1)
    return {"dataset": dataset, "query": query, "run": trial_num, "answer": "42"}


def _write_run(
    run_dir: Path,
    keys: list[tuple[str, int]],
    *,
    n_trials: int = 1,
) -> None:
    run_dir.mkdir()
    task_ids = sorted({task_id for task_id, _ in keys})
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "driver": "labrat-agent",
                "n_trials": n_trials,
                "task_filter": task_ids,
                "cartograph_scent_dir": str(Path.home() / "labrat-scent"),
            }
        )
    )
    (run_dir / "trials.jsonl").write_text(
        "".join(json.dumps(_trial(task_id, trial_num)) + "\n" for task_id, trial_num in keys)
    )
    (run_dir / "submission.json").write_text(
        json.dumps([_submission(task_id, trial_num) for task_id, trial_num in keys])
    )
    (run_dir / "report.md").write_text(f"Run stored under {Path.home()}/labrat-run\n")
    for task_id, trial_num in keys:
        trial_dir = run_dir / "scratch" / f"{task_id.replace(':', '_')}__trial{trial_num}"
        trial_dir.mkdir(parents=True)
        # An existing empty JSONL is a valid zero-tool execution trace.
        (trial_dir / "agent_tool_calls.jsonl").write_text("")


def test_build_bundle_includes_artifacts_manifest_and_zero_tool_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-one"
    _write_run(run_dir, [("stockindex:1", 0)])

    output = build_bundle(run_dir)

    assert output == run_dir / "trace_bundle"
    for name in ("config.json", "trials.jsonl", "submission.json", "report.md", "taint.json"):
        assert (output / name).is_file()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["summary"] == {
        "unique_trials": 1,
        "trial_attempts": 1,
        "infra_attempts": 0,
        "trace_files": 1,
        "audit_clean": True,
    }
    entry = manifest["trials"][0]
    assert entry["task_id"] == "stockindex:1"
    assert entry["trace_records"] == 0
    assert (output / entry["trace"]).read_text() == ""
    assert str(Path.home()) not in (output / "config.json").read_text()
    assert "<HOME>" in (output / "report.md").read_text()


def test_build_bundle_rejects_missing_trace(tmp_path: Path) -> None:
    run_dir = tmp_path / "missing-trace"
    _write_run(run_dir, [("stockindex:1", 0)])
    (run_dir / "scratch" / "stockindex_1__trial0" / "agent_tool_calls.jsonl").unlink()

    with pytest.raises(BundleError, match="trace audit failed"):
        build_bundle(run_dir)


def test_build_bundle_rejects_duplicate_semantic_attempts(tmp_path: Path) -> None:
    run_dir = tmp_path / "duplicate-semantic"
    _write_run(run_dir, [("stockindex:1", 0)])
    with (run_dir / "trials.jsonl").open("a") as handle:
        handle.write(json.dumps(_trial("stockindex:1", 0, reason="second result")) + "\n")

    with pytest.raises(BundleError, match="2 non-infra attempts"):
        build_bundle(run_dir)


def test_build_bundle_selects_one_semantic_attempt_after_infra_retry(tmp_path: Path) -> None:
    run_dir = tmp_path / "infra-retry"
    _write_run(run_dir, [("stockindex:1", 0)])
    semantic = (run_dir / "trials.jsonl").read_text()
    infra = json.dumps(_trial("stockindex:1", 0, reason="infra:rate_limit")) + "\n"
    (run_dir / "trials.jsonl").write_text(infra + semantic)

    output = build_bundle(run_dir)

    entry = json.loads((output / "manifest.json").read_text())["trials"][0]
    assert entry["selected_line_number"] == 2
    assert entry["attempt_count"] == 2
    assert entry["infra_attempt_count"] == 1
    assert entry["trace_scope"] == "trial-scratch-cumulative"
    assert entry["attempts"] == [
        {"line_number": 1, "reason": "infra:rate_limit", "infra": True},
        {"line_number": 2, "reason": "validated", "infra": False},
    ]


def test_strict_official_rejects_partial_matrix(tmp_path: Path) -> None:
    run_dir = tmp_path / "partial"
    _write_run(run_dir, [("stockindex:1", 0)])

    with pytest.raises(BundleError, match="exact 54-query x 5-trial matrix"):
        build_bundle(run_dir, strict_official=True)


def test_strict_official_accepts_exact_54_by_5_matrix(tmp_path: Path) -> None:
    task_ids = [
        f"{dataset}:{query_num}"
        for dataset, query_count in OFFICIAL_QUERY_COUNTS.items()
        for query_num in range(1, query_count + 1)
    ]
    keys = [(task_id, trial_num) for task_id in task_ids for trial_num in range(5)]
    run_dir = tmp_path / "official"
    _write_run(run_dir, keys, n_trials=5)

    output = build_bundle(run_dir, strict_official=True)

    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["strict_official"] is True
    assert manifest["summary"]["unique_trials"] == 270
    assert manifest["summary"]["trace_files"] == 270
