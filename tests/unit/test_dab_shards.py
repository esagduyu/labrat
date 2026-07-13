from __future__ import annotations

import json
from pathlib import Path

import pytest

from labrat.eval.benchmarks.dab.taint import task_trial_dir_name
from labrat.eval.types import TrialResult
from scripts.dab_shards import ShardError, merge_shards, prepare_shards


def _config() -> dict[str, object]:
    return {
        "driver": "labrat-agent",
        "agent_provider": "codex",
        "agent_model": "gpt-5.6-luna",
        "agent_reasoning": "max",
        "n_trials": 1,
        "task_filter": ["alpha:1", "beta:1"],
    }


def _prepare(tmp_path: Path) -> Path:
    config_path = tmp_path / "arm-config.json"
    config_path.write_text(json.dumps(_config()))
    shards_dir = tmp_path / "shards"
    prepare_shards(config_path, shards_dir)
    return shards_dir


def _trial(task_id: str, *, reason: str | None = None, passed: bool = True) -> TrialResult:
    return TrialResult(
        task_id=task_id,
        trial_num=0,
        passed=passed,
        reason=reason,
        latency_seconds=1.0,
        artifact={"type": "text", "payload": "answer"},
    )


def _write_rows(shard: Path, rows: list[TrialResult]) -> None:
    (shard / "trials.jsonl").write_text(
        "".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8"
    )


def _write_trace(shard: Path, task_id: str) -> None:
    trial_dir = shard / "scratch" / task_trial_dir_name(task_id, 0)
    trial_dir.mkdir(parents=True)
    record = {
        "tool": "query_sql",
        "input": {"sql": "select 1"},
        "ok": True,
        "output": "1",
        "latency_ms": 1,
    }
    (trial_dir / "agent_tool_calls.jsonl").write_text(json.dumps(record) + "\n")


def _complete(shards_dir: Path) -> None:
    _write_rows(
        shards_dir / "alpha",
        [
            _trial("alpha:1", reason="infra:rate_limit", passed=False),
            _trial("alpha:1"),
        ],
    )
    _write_rows(shards_dir / "beta", [_trial("beta:1", passed=False)])
    _write_trace(shards_dir / "alpha", "alpha:1")
    _write_trace(shards_dir / "beta", "beta:1")


def test_prepare_and_merge_complete_shards_preserves_infra_and_regenerates_artifacts(
    tmp_path: Path,
) -> None:
    shards_dir = _prepare(tmp_path)
    assert json.loads((shards_dir / "alpha" / "config.json").read_text())["task_filter"] == [
        "alpha:1"
    ]
    assert json.loads((shards_dir / "beta" / "config.json").read_text())["task_filter"] == [
        "beta:1"
    ]
    _complete(shards_dir)

    output_dir = tmp_path / "canonical"
    report = merge_shards(shards_dir, output_dir)

    merged_rows = [
        json.loads(line) for line in (output_dir / "trials.jsonl").read_text().splitlines()
    ]
    assert len(merged_rows) == 3
    assert sum(str(row.get("reason") or "").startswith("infra:") for row in merged_rows) == 1
    submission = json.loads((output_dir / "submission.json").read_text())
    assert [(entry["dataset"], entry["query"], entry["run"]) for entry in submission] == [
        ("alpha", "1", 0),
        ("beta", "1", 0),
    ]
    assert json.loads((output_dir / "taint.json").read_text()) == {
        "alpha:1:0": "clean",
        "beta:1:0": "clean",
    }
    assert (output_dir / "report.md").is_file()
    assert report.score.n_trials == 2
    assert report.score.overall == 0.5
    assert sorted(path.name for path in (output_dir / "scratch").iterdir()) == [
        "alpha_1__trial0",
        "beta_1__trial0",
    ]


def test_merge_rejects_config_difference_outside_task_filter(tmp_path: Path) -> None:
    shards_dir = _prepare(tmp_path)
    beta_config_path = shards_dir / "beta" / "config.json"
    beta_config = json.loads(beta_config_path.read_text())
    beta_config["agent_reasoning"] = "high"
    beta_config_path.write_text(json.dumps(beta_config))

    with pytest.raises(ShardError, match="config mismatch outside task_filter"):
        merge_shards(shards_dir, tmp_path / "canonical")


def test_merge_rejects_duplicate_semantic_attempt(tmp_path: Path) -> None:
    shards_dir = _prepare(tmp_path)
    _complete(shards_dir)
    _write_rows(shards_dir / "alpha", [_trial("alpha:1"), _trial("alpha:1", passed=False)])

    with pytest.raises(ShardError, match="duplicate semantic attempt"):
        merge_shards(shards_dir, tmp_path / "canonical")


def test_merge_rejects_trace_directory_collision(tmp_path: Path) -> None:
    shards_dir = _prepare(tmp_path)
    _complete(shards_dir)
    duplicate = shards_dir / "beta" / "scratch" / task_trial_dir_name("alpha:1", 0)
    duplicate.mkdir()
    (duplicate / "agent_tool_calls.jsonl").write_text("")

    with pytest.raises(ShardError, match="trace collision"):
        merge_shards(shards_dir, tmp_path / "canonical")
