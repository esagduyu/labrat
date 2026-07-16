#!/usr/bin/env python3
"""Prepare and merge isolated per-dataset DAB run shards.

Each shard is a normal ``scripts/eval_dab.py`` output directory, so workers
never share a ``trials.jsonl``.  ``merge`` validates a complete matched arm and
builds its canonical artifacts in a temporary directory before publishing it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

from labrat.eval.benchmarks.dab.reporter import write_submission_json
from labrat.eval.benchmarks.dab.suite import aggregate_dab_results
from labrat.eval.benchmarks.dab.taint import audit_run, gate, task_trial_dir_name
from labrat.eval.reporting import report_to_markdown
from labrat.eval.types import BenchmarkReport, TrialResult

_MANIFEST = "shards.json"
_CONFIG = "config.json"
_TRIALS = "trials.jsonl"

_RECOVERY_COMPAT_KEYS = (
    "hints",
    "driver",
    "agent_model",
    "agent_provider",
    "agent_verify",
    "agent_cartograph",
    "cartograph_semantics",
    "cartograph_semantics_model",
    "cartograph_semantics_provider",
    "agent_consensus",
    "agent_reverify",
    "agent_argue_rounds",
    "agent_postverify",
    "consensus_diversity",
    "agent_reasoning",
    "agent_levers",
    "agent_ledger",
    "n_trials",
)


class ShardError(RuntimeError):
    """A shard set is unsafe or incomplete."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ShardError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ShardError(f"{path} must contain a JSON object")
    return cast(dict[str, Any], value)


def _task_filter(config: dict[str, Any], *, source: Path) -> list[str]:
    value = config.get("task_filter")
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ShardError(f"{source} must contain a non-empty string task_filter")
    tasks = cast(list[str], value)
    if len(tasks) != len(set(tasks)):
        raise ShardError(f"{source} contains duplicate task_filter entries")
    for task_id in tasks:
        try:
            task_trial_dir_name(task_id, 0)
        except ValueError as exc:
            raise ShardError(f"{source} contains invalid task {task_id!r}") from exc
    return tasks


def _without_filter(config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in config.items() if key != "task_filter"}


def prepare_shards(config_path: Path, shards_dir: Path) -> list[Path]:
    """Create one resumable eval directory per dataset from a matched-arm config."""
    config = _read_object(config_path)
    expected_tasks = _task_filter(config, source=config_path)
    if shards_dir.exists() or shards_dir.is_symlink():
        raise ShardError(f"shards directory already exists: {shards_dir}")

    by_dataset: dict[str, list[str]] = defaultdict(list)
    for task_id in expected_tasks:
        by_dataset[task_id.split(":", 1)[0]].append(task_id)

    shards_dir.mkdir(parents=True)
    shard_paths: list[Path] = []
    shard_names: list[str] = []
    for dataset, tasks in by_dataset.items():
        shard = shards_dir / dataset
        shard.mkdir()
        shard_config = {**config, "task_filter": tasks}
        (shard / _CONFIG).write_text(json.dumps(shard_config, indent=2) + "\n", encoding="utf-8")
        shard_paths.append(shard)
        shard_names.append(dataset)

    manifest = {
        "version": 1,
        "config": config,
        "expected_tasks": expected_tasks,
        "shards": shard_names,
    }
    (shards_dir / _MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return shard_paths


def _load_trials(shard: Path, allowed_tasks: set[str], n_trials: int) -> list[TrialResult]:
    path = shard / _TRIALS
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ShardError(f"cannot read {path}: {exc}") from exc

    results: list[TrialResult] = []
    semantic_keys: set[tuple[str, int]] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            result = TrialResult.model_validate_json(line)
        except Exception as exc:
            raise ShardError(f"invalid trial row {path}:{line_number}: {exc}") from exc
        key = (result.task_id, result.trial_num)
        if result.task_id not in allowed_tasks:
            raise ShardError(
                f"{path}:{line_number} has task outside its task_filter: {result.task_id}"
            )
        if result.trial_num < 0 or result.trial_num >= n_trials:
            raise ShardError(f"{path}:{line_number} has out-of-range trial_num: {result.trial_num}")
        if not (result.reason or "").startswith("infra:"):
            if key in semantic_keys:
                raise ShardError(
                    f"duplicate semantic attempt in {path}: {result.task_id} trial {result.trial_num}"
                )
            semantic_keys.add(key)
        results.append(result)
    return results


def _copy_scratch(shards: list[Path], destination: Path, expected_names: set[str]) -> None:
    destination.mkdir()
    copied: dict[str, Path] = {}
    for shard in shards:
        scratch = shard / "scratch"
        if scratch.is_symlink() or not scratch.is_dir():
            raise ShardError(f"missing or unsafe scratch directory: {scratch}")
        for source in sorted(scratch.iterdir()):
            if source.is_symlink() or not source.is_dir():
                raise ShardError(f"unsafe scratch entry: {source}")
            for nested in source.rglob("*"):
                if nested.is_symlink() or not (nested.is_dir() or nested.is_file()):
                    raise ShardError(f"unsafe scratch entry: {nested}")
            if source.name in copied:
                raise ShardError(
                    f"trace collision for {source.name}: {copied[source.name]} and {source}"
                )
            copied[source.name] = source
            shutil.copytree(source, destination / source.name, symlinks=False)
    actual_names = set(copied)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        raise ShardError(f"scratch coverage mismatch: missing={missing}, extra={extra}")


def merge_shards(shards_dir: Path, output_dir: Path) -> BenchmarkReport:
    """Validate and atomically merge a complete set of dataset shards."""
    manifest_path = shards_dir / _MANIFEST
    manifest = _read_object(manifest_path)
    if manifest.get("version") != 1:
        raise ShardError(f"unsupported shard manifest version in {manifest_path}")
    config_value = manifest.get("config")
    shard_names_value = manifest.get("shards")
    expected_value = manifest.get("expected_tasks")
    if not isinstance(config_value, dict):
        raise ShardError(f"{manifest_path} has no valid config")
    config = cast(dict[str, Any], config_value)
    expected_tasks = _task_filter(config, source=manifest_path)
    if expected_value != expected_tasks:
        raise ShardError("manifest expected_tasks does not exactly match config.task_filter")
    if not isinstance(shard_names_value, list) or not all(
        isinstance(name, str) and name for name in shard_names_value
    ):
        raise ShardError(f"{manifest_path} has no valid shards list")
    shard_names = cast(list[str], shard_names_value)
    if len(shard_names) != len(set(shard_names)):
        raise ShardError("manifest has duplicate shard names")
    expected_datasets = list(dict.fromkeys(task.split(":", 1)[0] for task in expected_tasks))
    if shard_names != expected_datasets:
        raise ShardError("manifest shards do not exactly match expected task datasets")
    if output_dir.exists() or output_dir.is_symlink():
        raise ShardError(f"output directory already exists: {output_dir}")

    n_trials = config.get("n_trials")
    if isinstance(n_trials, bool) or not isinstance(n_trials, int) or n_trials < 1:
        raise ShardError("config.n_trials must be a positive integer")

    shards: list[Path] = []
    shard_tasks: dict[Path, list[str]] = {}
    assigned: list[str] = []
    all_rows: list[TrialResult] = []
    semantic: dict[tuple[str, int], TrialResult] = {}
    expected_base = _without_filter(config)
    for name in shard_names:
        shard = shards_dir / name
        shard_config_path = shard / _CONFIG
        shard_config = _read_object(shard_config_path)
        if _without_filter(shard_config) != expected_base:
            raise ShardError(f"config mismatch outside task_filter: {shard_config_path}")
        tasks = _task_filter(shard_config, source=shard_config_path)
        expected_for_dataset = [task for task in expected_tasks if task.split(":", 1)[0] == name]
        if tasks != expected_for_dataset:
            raise ShardError(
                f"{shard_config_path} task_filter does not exactly match dataset {name!r}"
            )
        assigned.extend(tasks)
        shard_tasks[shard] = tasks
        shards.append(shard)

    if assigned != expected_tasks:
        raise ShardError("shard task_filter union does not exactly match expected tasks")

    for shard in shards:
        rows = _load_trials(shard, set(shard_tasks[shard]), n_trials)
        for row in rows:
            if not (row.reason or "").startswith("infra:"):
                key = (row.task_id, row.trial_num)
                if key in semantic:
                    raise ShardError(
                        f"duplicate semantic attempt across shards: {row.task_id} trial {row.trial_num}"
                    )
                semantic[key] = row
        all_rows.extend(rows)
    expected_keys = {(task, trial) for task in expected_tasks for trial in range(n_trials)}
    if set(semantic) != expected_keys:
        missing = sorted(expected_keys - set(semantic))
        extra = sorted(set(semantic) - expected_keys)
        raise ShardError(f"semantic trial coverage mismatch: missing={missing}, extra={extra}")

    task_order = {task: index for index, task in enumerate(expected_tasks)}
    indexed_rows = list(enumerate(all_rows))
    indexed_rows.sort(key=lambda item: (task_order[item[1].task_id], item[1].trial_num, item[0]))
    ordered_rows = [row for _, row in indexed_rows]
    ordered_semantic = [
        semantic[(task, trial)] for task in expected_tasks for trial in range(n_trials)
    ]

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-merge-", dir=output_dir.parent))
    try:
        (temp_dir / _CONFIG).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        (temp_dir / _TRIALS).write_text(
            "".join(row.model_dump_json() + "\n" for row in ordered_rows), encoding="utf-8"
        )
        expected_trace_dirs = {
            task_trial_dir_name(task, trial) for task in expected_tasks for trial in range(n_trials)
        }
        _copy_scratch(shards, temp_dir / "scratch", expected_trace_dirs)

        verdicts = audit_run(temp_dir / _TRIALS, temp_dir / "scratch")
        audit_ok, offenders = gate(verdicts)
        if not audit_ok:
            raise ShardError(f"canonical taint audit failed: {offenders}")
        if set(verdicts) != {f"{task}:{trial}" for task, trial in expected_keys}:
            raise ShardError("canonical taint audit did not cover every expected semantic trial")

        score = aggregate_dab_results(ordered_semantic)
        report = BenchmarkReport(
            benchmark="dab",
            run_id=output_dir.name,
            score=score,
            trials=ordered_semantic,
            config={"n_trials": n_trials, "task_filter": expected_tasks},
        )
        write_submission_json(report, temp_dir)
        (temp_dir / "report.md").write_text(report_to_markdown(report), encoding="utf-8")
        temp_dir.replace(output_dir)
        return report
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _config_delta(config: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    keys = sorted((set(config) | set(base)) - {"task_filter"})
    return {key: config.get(key) for key in keys if config.get(key) != base.get(key)}


def _validate_terminal_recovery_trace(run: Path, row: TrialResult) -> None:
    if not (row.reason or "").startswith("terminal:"):
        return
    trace = run / "scratch" / task_trial_dir_name(row.task_id, row.trial_num)
    trace = trace / "agent_tool_calls.jsonl"
    try:
        lines = [line for line in trace.read_text(encoding="utf-8").splitlines() if line]
        final = json.loads(lines[-1])
    except (OSError, IndexError, UnicodeError, json.JSONDecodeError) as exc:
        raise ShardError(f"terminal recovery has no readable final trace event: {trace}") from exc
    artifact = row.artifact.get("payload") if row.artifact.get("type") == "text" else None
    if not isinstance(final, dict) or final.get("tool") not in {
        "runner_timeout",
        "runner_turn_budget",
    }:
        raise ShardError(f"terminal recovery trace lacks a runner terminal event: {trace}")
    if final.get("output") != artifact:
        raise ShardError(f"terminal recovery artifact does not match final trace event: {trace}")


def merge_bounded_recovery(
    shards_dir: Path,
    recovery_runs: list[Path],
    output_dir: Path,
) -> BenchmarkReport:
    """Fill only missing semantic keys from declared bounded recovery runs."""
    if not recovery_runs:
        raise ShardError("at least one recovery run is required")
    manifest_path = shards_dir / _MANIFEST
    manifest = _read_object(manifest_path)
    if manifest.get("version") != 1:
        raise ShardError(f"unsupported shard manifest version in {manifest_path}")
    config_value = manifest.get("config")
    shard_names_value = manifest.get("shards")
    if not isinstance(config_value, dict):
        raise ShardError(f"{manifest_path} has no valid config")
    if not isinstance(shard_names_value, list) or not all(
        isinstance(name, str) and name for name in shard_names_value
    ):
        raise ShardError(f"{manifest_path} has no valid shards list")
    base_config = cast(dict[str, Any], config_value)
    shard_names = cast(list[str], shard_names_value)
    expected_tasks = _task_filter(base_config, source=manifest_path)
    n_trials = base_config.get("n_trials")
    if isinstance(n_trials, bool) or not isinstance(n_trials, int) or n_trials < 1:
        raise ShardError("config.n_trials must be a positive integer")
    expected_keys = {(task, trial) for task in expected_tasks for trial in range(n_trials)}

    selected: dict[tuple[str, int], tuple[TrialResult, Path]] = {}
    shard_deltas: dict[str, dict[str, Any]] = {}
    for name in shard_names:
        shard = shards_dir / name
        shard_config_path = shard / _CONFIG
        shard_config = _read_object(shard_config_path)
        tasks = _task_filter(shard_config, source=shard_config_path)
        expected_for_dataset = [task for task in expected_tasks if task.split(":", 1)[0] == name]
        if tasks != expected_for_dataset:
            raise ShardError(
                f"{shard_config_path} task_filter does not exactly match dataset {name!r}"
            )
        delta = _config_delta(shard_config, base_config)
        if delta:
            shard_deltas[name] = delta
        for row in _load_trials(shard, set(tasks), n_trials):
            if (row.reason or "").startswith("infra:"):
                continue
            key = (row.task_id, row.trial_num)
            if key in selected:
                raise ShardError(f"duplicate base semantic attempt: {key}")
            selected[key] = (row, shard)

    task_overrides: dict[str, dict[str, Any]] = {}
    recovery_sources: list[str] = []
    for run in recovery_runs:
        recovery_config_path = run / _CONFIG
        recovery_config = _read_object(recovery_config_path)
        tasks = _task_filter(recovery_config, source=recovery_config_path)
        for key in _RECOVERY_COMPAT_KEYS:
            if recovery_config.get(key) != base_config.get(key):
                raise ShardError(
                    f"recovery config mismatch for {key}: "
                    f"{recovery_config.get(key)!r} != {base_config.get(key)!r}"
                )
        if recovery_config.get("terminalize_timeouts") is not True:
            raise ShardError(f"{recovery_config_path} must enable terminalize_timeouts")
        row_budget = recovery_config.get("llm_classify_row_budget")
        max_turns = recovery_config.get("agent_max_turns")
        if isinstance(row_budget, bool) or not isinstance(row_budget, int) or row_budget < 1:
            raise ShardError(f"{recovery_config_path} has no positive classifier row budget")
        if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
            raise ShardError(f"{recovery_config_path} has no positive turn budget")

        recovery_rows = _load_trials(run, set(tasks), n_trials)
        recovery_semantic = [
            row for row in recovery_rows if not (row.reason or "").startswith("infra:")
        ]
        recovery_expected = {(task, trial) for task in tasks for trial in range(n_trials)}
        recovery_actual = {(row.task_id, row.trial_num) for row in recovery_semantic}
        if recovery_actual != recovery_expected:
            raise ShardError(
                f"recovery semantic coverage mismatch for {run}: "
                f"missing={sorted(recovery_expected - recovery_actual)} "
                f"extra={sorted(recovery_actual - recovery_expected)}"
            )
        for row in recovery_semantic:
            _validate_terminal_recovery_trace(run, row)
            key = (row.task_id, row.trial_num)
            if key in selected:
                raise ShardError(f"recovery refuses to replace existing semantic key: {key}")
            selected[key] = (row, run)
        declared = _config_delta(recovery_config, base_config)
        for task in tasks:
            task_overrides[task] = declared
        recovery_sources.append(str(run.resolve()))

    if set(selected) != expected_keys:
        raise ShardError(
            "recovered semantic trial coverage mismatch: "
            f"missing={sorted(expected_keys - set(selected))} "
            f"extra={sorted(set(selected) - expected_keys)}"
        )
    if output_dir.exists() or output_dir.is_symlink():
        raise ShardError(f"output directory already exists: {output_dir}")

    ordered = [selected[(task, trial)][0] for task in expected_tasks for trial in range(n_trials)]
    final_config = {
        **base_config,
        "task_filter": expected_tasks,
        "bounded_recovery": {
            "base_shards": str(shards_dir.resolve()),
            "recovery_runs": recovery_sources,
            "base_shard_config_deltas": shard_deltas,
            "task_overrides": task_overrides,
            "infra_rows_preserved_in_source_runs": True,
        },
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-recover-", dir=output_dir.parent))
    try:
        (temp_dir / _CONFIG).write_text(json.dumps(final_config, indent=2) + "\n", encoding="utf-8")
        (temp_dir / _TRIALS).write_text(
            "".join(row.model_dump_json() + "\n" for row in ordered), encoding="utf-8"
        )
        scratch = temp_dir / "scratch"
        scratch.mkdir()
        for task in expected_tasks:
            for trial in range(n_trials):
                source = selected[(task, trial)][1] / "scratch" / task_trial_dir_name(task, trial)
                destination = scratch / source.name
                if not source.is_dir() or source.is_symlink():
                    raise ShardError(f"missing or unsafe selected trace directory: {source}")
                if destination.exists():
                    raise ShardError(f"selected trace collision: {destination.name}")
                shutil.copytree(source, destination, symlinks=False)

        verdicts = audit_run(temp_dir / _TRIALS, scratch)
        audit_ok, offenders = gate(verdicts)
        if not audit_ok:
            raise ShardError(f"recovered taint audit failed: {offenders}")
        expected_verdicts = {f"{task}:{trial}" for task, trial in expected_keys}
        if set(verdicts) != expected_verdicts:
            raise ShardError("recovered taint audit did not cover every expected trial")

        report = BenchmarkReport(
            benchmark="dab",
            run_id=output_dir.name,
            score=aggregate_dab_results(ordered),
            trials=ordered,
            config=final_config,
        )
        write_submission_json(report, temp_dir)
        (temp_dir / "report.md").write_text(report_to_markdown(report), encoding="utf-8")
        temp_dir.replace(output_dir)
        return report
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="create one run directory per dataset")
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--shards-dir", type=Path, required=True)

    merge = subparsers.add_parser("merge", help="validate and merge completed shards")
    merge.add_argument("--shards-dir", type=Path, required=True)
    merge.add_argument("--output-dir", type=Path, required=True)

    recover = subparsers.add_parser(
        "recover", help="fill missing semantic keys from bounded recovery runs"
    )
    recover.add_argument("--shards-dir", type=Path, required=True)
    recover.add_argument("--recovery-run", type=Path, action="append", required=True)
    recover.add_argument("--output-dir", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            shards = prepare_shards(args.config, args.shards_dir)
            for shard in shards:
                print(f"uv run python scripts/eval_dab.py --output-dir {shard}")
        elif args.command == "recover":
            report = merge_bounded_recovery(args.shards_dir, args.recovery_run, args.output_dir)
            print(
                f"Recovered {report.score.n_trials} semantic trials into "
                f"{args.output_dir} ({report.score.n_passes} passes)"
            )
        else:
            report = merge_shards(args.shards_dir, args.output_dir)
            print(f"Merged {report.score.n_trials} semantic trials into {args.output_dir}")
    except ShardError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
