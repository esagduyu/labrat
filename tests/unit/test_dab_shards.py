from __future__ import annotations

import json
from pathlib import Path

import pytest

from labrat.eval.benchmarks.dab.taint import task_trial_dir_name
from labrat.eval.types import TrialResult
from scripts.dab_shards import (
    ShardError,
    merge_bounded_recovery,
    merge_shards,
    prepare_shards,
)


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


def _bounded_beta_recovery(tmp_path: Path) -> Path:
    run = tmp_path / "beta-recovery"
    run.mkdir()
    config = {
        **_config(),
        "task_filter": ["beta:1"],
        "agent_max_turns": 10,
        "llm_classify_row_budget": 200,
        "terminalize_timeouts": True,
    }
    (run / "config.json").write_text(json.dumps(config))
    terminal = TrialResult(
        task_id="beta:1",
        trial_num=0,
        passed=False,
        reason="terminal:turn_budget",
        latency_seconds=2.0,
        artifact={
            "type": "text",
            "payload": "[trial exhausted 10-turn budget without a final answer]",
        },
    )
    _write_rows(run, [terminal])
    trial_dir = run / "scratch" / task_trial_dir_name("beta:1", 0)
    trial_dir.mkdir(parents=True)
    record = {
        "tool": "runner_turn_budget",
        "input": {"max_turns": 10},
        "ok": False,
        "output": terminal.artifact["payload"],
        "latency_ms": 0,
    }
    (trial_dir / "agent_tool_calls.jsonl").write_text(json.dumps(record) + "\n")
    return run


def test_bounded_recovery_fills_only_missing_semantic_keys(tmp_path: Path) -> None:
    shards_dir = _prepare(tmp_path)
    _write_rows(shards_dir / "alpha", [_trial("alpha:1")])
    _write_rows(shards_dir / "beta", [_trial("beta:1", reason="infra:timeout", passed=False)])
    _write_trace(shards_dir / "alpha", "alpha:1")
    _write_trace(shards_dir / "beta", "beta:1")
    recovery = _bounded_beta_recovery(tmp_path)

    output = tmp_path / "recovered"
    report = merge_bounded_recovery(shards_dir, [recovery], output)

    rows = [json.loads(line) for line in (output / "trials.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    assert [row["reason"] for row in rows] == [None, "terminal:turn_budget"]
    assert report.score.n_trials == 2
    assert report.score.n_passes == 1
    assert report.score.overall == 0.5
    config = json.loads((output / "config.json").read_text())
    assert config["bounded_recovery"]["infra_rows_preserved_in_source_runs"] is True
    assert config["bounded_recovery"]["task_overrides"]["beta:1"] == {
        "agent_max_turns": 10,
        "llm_classify_row_budget": 200,
        "terminalize_timeouts": True,
    }
    assert json.loads((output / "taint.json").read_text()) == {
        "alpha:1:0": "clean",
        "beta:1:0": "clean",
    }


def _recovery_scenario(tmp_path: Path) -> tuple[Path, Path]:
    """Base shards with one infra-only gap plus a bounded recovery run for it."""
    shards_dir = _prepare(tmp_path)
    _write_rows(shards_dir / "alpha", [_trial("alpha:1")])
    _write_rows(shards_dir / "beta", [_trial("beta:1", reason="infra:timeout", passed=False)])
    _write_trace(shards_dir / "alpha", "alpha:1")
    _write_trace(shards_dir / "beta", "beta:1")
    return shards_dir, _bounded_beta_recovery(tmp_path)


def _rewrite_config(path: Path, mutate: dict[str, object], *, drop: str | None = None) -> None:
    config = json.loads(path.read_text())
    config.update(mutate)
    if drop is not None:
        del config[drop]
    path.write_text(json.dumps(config))


def test_bounded_recovery_rejects_disallowed_base_shard_delta(tmp_path: Path) -> None:
    shards_dir, recovery = _recovery_scenario(tmp_path)
    _rewrite_config(shards_dir / "alpha" / "config.json", {"agent_model": "gpt-5.6-other"})

    with pytest.raises(ShardError, match=r"base shard 'alpha'.*agent_model"):
        merge_bounded_recovery(shards_dir, [recovery], tmp_path / "recovered")


@pytest.mark.parametrize(
    ("target", "label_pattern"),
    [
        ("base_shard", r"base shard 'alpha'"),
        ("recovery_run", r"recovery run .*beta-recovery"),
    ],
)
def test_compat_mismatch_raises_identically_shaped_error(
    tmp_path: Path, target: str, label_pattern: str
) -> None:
    shards_dir, recovery = _recovery_scenario(tmp_path)
    config_path = (
        shards_dir / "alpha" / "config.json" if target == "base_shard" else recovery / "config.json"
    )
    _rewrite_config(config_path, {"agent_model": "gpt-5.6-other"})

    expected = (
        label_pattern + r" config mismatch for agent_model: 'gpt-5\.6-other' != 'gpt-5\.6-luna'"
    )
    with pytest.raises(ShardError, match=expected):
        merge_bounded_recovery(shards_dir, [recovery], tmp_path / "recovered")


@pytest.mark.parametrize(
    ("target", "label_pattern"),
    [
        ("base_shard", r"base shard 'alpha'"),
        ("recovery_run", r"recovery run .*beta-recovery"),
    ],
)
def test_compat_missing_key_reports_absence_distinctly(
    tmp_path: Path, target: str, label_pattern: str
) -> None:
    shards_dir, recovery = _recovery_scenario(tmp_path)
    config_path = (
        shards_dir / "alpha" / "config.json" if target == "base_shard" else recovery / "config.json"
    )
    _rewrite_config(config_path, {}, drop="agent_reasoning")

    expected = label_pattern + r" config missing required key 'agent_reasoning' \(base: 'max'\)"
    with pytest.raises(ShardError, match=expected):
        merge_bounded_recovery(shards_dir, [recovery], tmp_path / "recovered")


def test_bounded_recovery_records_allowed_base_shard_delta(tmp_path: Path) -> None:
    shards_dir = _prepare(tmp_path)
    _write_rows(shards_dir / "alpha", [_trial("alpha:1")])
    _write_rows(shards_dir / "beta", [_trial("beta:1", reason="infra:timeout", passed=False)])
    _write_trace(shards_dir / "alpha", "alpha:1")
    _write_trace(shards_dir / "beta", "beta:1")
    alpha_config_path = shards_dir / "alpha" / "config.json"
    alpha_config = json.loads(alpha_config_path.read_text())
    alpha_config["agent_timeout"] = 2400
    alpha_config_path.write_text(json.dumps(alpha_config))
    recovery = _bounded_beta_recovery(tmp_path)

    output = tmp_path / "recovered"
    merge_bounded_recovery(shards_dir, [recovery], output)

    config = json.loads((output / "config.json").read_text())
    assert config["bounded_recovery"]["base_shard_config_deltas"] == {
        "alpha": {"agent_timeout": 2400}
    }


def test_bounded_recovery_refuses_to_replace_semantic_result(tmp_path: Path) -> None:
    shards_dir = _prepare(tmp_path)
    _complete(shards_dir)
    recovery = _bounded_beta_recovery(tmp_path)

    with pytest.raises(ShardError, match="refuses to replace"):
        merge_bounded_recovery(shards_dir, [recovery], tmp_path / "recovered")
