"""Build a self-contained, reviewable DAB submission trace bundle.

The bundle is assembled from first-class per-trial traces under a completed run
directory. It never reads Codex/Claude credentials or reconstructs traces from
provider-global history.

Usage::

    uv run python scripts/build_dab_trace_bundle.py \
      --run-dir runs/dab/<run-id> \
      --strict-official
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from labrat.eval.benchmarks.dab.taint import (
    TRACE_FILENAME_BY_DRIVER,
    audit_run,
    gate,
    validate_trace_jsonl,
)

CORE_ARTIFACTS = ("config.json", "trials.jsonl", "submission.json", "report.md")
OFFICIAL_QUERY_COUNTS = {
    "agnews": 4,
    "bookreview": 3,
    "crmarenapro": 13,
    "deps_dev_v1": 2,
    "github_repos": 4,
    "googlelocal": 4,
    "music_brainz_20k": 3,
    "pancancer_atlas": 3,
    "patents": 3,
    "stockindex": 3,
    "stockmarket": 5,
    "yelp": 7,
}


class BundleError(RuntimeError):
    """The run cannot be packaged without weakening trace integrity."""


@dataclass(frozen=True)
class TrialAttempt:
    line_number: int
    record: dict[str, Any]

    @property
    def key(self) -> tuple[str, int]:
        return (str(self.record["task_id"]), int(self.record["trial_num"]))

    @property
    def reason(self) -> str:
        return str(self.record.get("reason") or "")

    @property
    def is_infra(self) -> bool:
        return self.reason.startswith("infra:")


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value).strip(".")
    return cleaned or "trial"


def _scrub_text(text: str) -> str:
    home = str(Path.home())
    if home:
        text = text.replace(home, "<HOME>")
    return re.sub(r"/Users/[^/\s\"']+", "<HOME>", text)


def _scrub_obj(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, list):
        return [_scrub_obj(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _scrub_obj(item) for key, item in value.items()}
    return value


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BundleError(f"required artifact missing: {path.name}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"invalid {path.name}: {exc}") from exc


def _load_attempts(path: Path) -> list[TrialAttempt]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise BundleError("required artifact missing: trials.jsonl") from exc
    except (OSError, UnicodeError) as exc:
        raise BundleError(f"invalid trials.jsonl: {exc}") from exc

    attempts: list[TrialAttempt] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BundleError(f"invalid trials.jsonl line {line_number}: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise BundleError(f"invalid trials.jsonl line {line_number}: expected object")
        task_id = record.get("task_id")
        trial_num = record.get("trial_num")
        if not isinstance(task_id, str) or not task_id:
            raise BundleError(f"invalid trials.jsonl line {line_number}: invalid task_id")
        if isinstance(trial_num, bool) or not isinstance(trial_num, int) or trial_num < 0:
            raise BundleError(f"invalid trials.jsonl line {line_number}: invalid trial_num")
        attempts.append(TrialAttempt(line_number=line_number, record=record))
    if not attempts:
        raise BundleError("trials.jsonl has no trial records")
    return attempts


def _select_attempts(
    attempts: list[TrialAttempt],
) -> tuple[dict[tuple[str, int], TrialAttempt], dict[tuple[str, int], list[TrialAttempt]]]:
    grouped: dict[tuple[str, int], list[TrialAttempt]] = defaultdict(list)
    for attempt in attempts:
        grouped[attempt.key].append(attempt)

    selected: dict[tuple[str, int], TrialAttempt] = {}
    for key, group in grouped.items():
        semantic = [attempt for attempt in group if not attempt.is_infra]
        if len(semantic) != 1:
            task_id, trial_num = key
            raise BundleError(
                f"{task_id} trial {trial_num} has {len(semantic)} non-infra attempts; "
                "exactly one is required for unambiguous submission packaging"
            )
        selected[key] = semantic[0]
    return selected, grouped


def _submission_keys(submission: Any) -> set[tuple[str, int]]:
    if not isinstance(submission, list):
        raise BundleError("submission.json must contain a JSON array")
    keys: set[tuple[str, int]] = set()
    for index, entry in enumerate(submission):
        if not isinstance(entry, dict):
            raise BundleError(f"submission.json entry {index} is not an object")
        dataset = entry.get("dataset")
        query = entry.get("query")
        run = entry.get("run")
        if not isinstance(dataset, str) or not dataset or not isinstance(query, str) or not query:
            raise BundleError(f"submission.json entry {index} has an invalid dataset/query")
        if isinstance(run, bool) or not isinstance(run, int) or run < 0:
            raise BundleError(f"submission.json entry {index} has an invalid run")
        if "answer" not in entry:
            raise BundleError(f"submission.json entry {index} is missing answer")
        key = (f"{dataset}:{query}", run)
        if key in keys:
            raise BundleError(f"submission.json has duplicate entry for {key[0]} trial {key[1]}")
        keys.add(key)
    return keys


def _official_task_ids() -> set[str]:
    return {
        f"{dataset}:{query_num}"
        for dataset, query_count in OFFICIAL_QUERY_COUNTS.items()
        for query_num in range(1, query_count + 1)
    }


def _validate_strict_official(config: dict[str, Any], selected_keys: set[tuple[str, int]]) -> None:
    expected_tasks = _official_task_ids()
    expected_keys = {(task_id, trial_num) for task_id in expected_tasks for trial_num in range(5)}
    if selected_keys != expected_keys:
        missing = len(expected_keys - selected_keys)
        extra = len(selected_keys - expected_keys)
        raise BundleError(
            "strict official check requires the exact 54-query x 5-trial matrix "
            f"(missing={missing}, extra={extra})"
        )
    if config.get("n_trials") != 5:
        raise BundleError("strict official check requires config n_trials=5")
    task_filter = config.get("task_filter")
    if not isinstance(task_filter, list) or set(task_filter) != expected_tasks:
        raise BundleError(
            "strict official check requires the exact 54 official task_filter entries"
        )


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(_scrub_obj(value), indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(_scrub_obj(record), default=str) + "\n")


def _copy_trace(source: Path, destination: Path) -> int:
    error = validate_trace_jsonl(source)
    if error is not None:
        raise BundleError(error)
    destination.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if not isinstance(record, dict):  # guarded by validate_trace_jsonl
                raise BundleError(f"malformed trace {source.name}: expected object")
            records.append(record)
    _write_jsonl(destination, records)
    return len(records)


def _replace_output(temp_dir: Path, output_dir: Path, *, force: bool) -> None:
    if output_dir.exists():
        if not force:
            raise BundleError(f"output already exists: {output_dir} (pass --force to replace)")
        if output_dir.is_dir():
            shutil.rmtree(output_dir)
        else:
            output_dir.unlink()
    temp_dir.replace(output_dir)


def build_bundle(
    run_dir: Path,
    *,
    output_dir: Path | None = None,
    strict_official: bool = False,
    force: bool = False,
) -> Path:
    """Validate ``run_dir`` and atomically build its trace bundle."""
    run_dir = run_dir.expanduser().resolve()
    if not run_dir.is_dir():
        raise BundleError(f"run directory not found: {run_dir}")
    output_dir = (
        output_dir.expanduser().resolve()
        if output_dir is not None
        else (run_dir / "trace_bundle").resolve()
    )
    if output_dir == run_dir:
        raise BundleError("output directory cannot be the run directory itself")

    for artifact in CORE_ARTIFACTS:
        if not (run_dir / artifact).is_file():
            raise BundleError(f"required artifact missing: {artifact}")

    config = _load_json(run_dir / "config.json")
    if not isinstance(config, dict):
        raise BundleError("config.json must contain a JSON object")
    driver = str(config.get("driver") or "")
    trace_filename = TRACE_FILENAME_BY_DRIVER.get(driver)
    if trace_filename is None:
        raise BundleError(
            f"driver {driver!r} has no first-class scratch trace contract; "
            f"supported drivers: {', '.join(sorted(TRACE_FILENAME_BY_DRIVER))}"
        )

    attempts = _load_attempts(run_dir / "trials.jsonl")
    selected, grouped = _select_attempts(attempts)
    selected_keys = set(selected)

    submission = _load_json(run_dir / "submission.json")
    submission_keys = _submission_keys(submission)
    if submission_keys != selected_keys:
        missing = len(selected_keys - submission_keys)
        extra = len(submission_keys - selected_keys)
        raise BundleError(
            "submission.json does not match selected semantic trials "
            f"(missing={missing}, extra={extra})"
        )
    if strict_official:
        _validate_strict_official(config, selected_keys)

    verdicts = audit_run(run_dir / "trials.jsonl", run_dir / "scratch")
    audit_ok, offenders = gate(verdicts)
    if not audit_ok:
        preview = ", ".join(offenders[:8])
        suffix = "" if len(offenders) <= 8 else f", +{len(offenders) - 8} more"
        raise BundleError(f"trace audit failed for: {preview}{suffix}")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=output_dir.parent))
    try:
        _write_json(temp_dir / "config.json", config)
        _write_jsonl(temp_dir / "trials.jsonl", [attempt.record for attempt in attempts])
        _write_json(temp_dir / "submission.json", submission)
        try:
            report = (run_dir / "report.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise BundleError(f"invalid report.md: {exc}") from exc
        (temp_dir / "report.md").write_text(_scrub_text(report), encoding="utf-8")
        _write_json(temp_dir / "taint.json", verdicts)

        manifest_trials: list[dict[str, Any]] = []
        total_infra_attempts = 0
        for key in sorted(selected):
            task_id, trial_num = key
            attempt = selected[key]
            group = grouped[key]
            infra_attempts = [item for item in group if item.is_infra]
            total_infra_attempts += len(infra_attempts)

            source = (
                run_dir
                / "scratch"
                / f"{task_id.replace(':', '_')}__trial{trial_num}"
                / trace_filename
            )
            relative_trace = (
                Path("traces") / f"{_safe_name(task_id)}__trial{trial_num}" / trace_filename
            )
            destination = temp_dir / relative_trace
            trace_records = _copy_trace(source, destination)
            trace_digest = hashlib.sha256(destination.read_bytes()).hexdigest()

            manifest_trials.append(
                {
                    "task_id": task_id,
                    "trial_num": trial_num,
                    "passed": bool(attempt.record.get("passed", False)),
                    "reason": attempt.reason,
                    "selected_line_number": attempt.line_number,
                    "attempt_count": len(group),
                    "infra_attempt_count": len(infra_attempts),
                    "trace_scope": (
                        "selected-attempt"
                        if config.get("trace_attempt_policy") == "reset_on_attempt"
                        or len(group) == 1
                        else "trial-scratch-cumulative"
                    ),
                    "attempts": [
                        {
                            "line_number": item.line_number,
                            "reason": item.reason,
                            "infra": item.is_infra,
                        }
                        for item in group
                    ],
                    "trace": relative_trace.as_posix(),
                    "trace_records": trace_records,
                    "trace_sha256": trace_digest,
                    "audit_verdict": verdicts[f"{task_id}:{trial_num}"],
                }
            )

        manifest = {
            "schema_version": 1,
            "run_id": run_dir.name,
            "driver": driver,
            "strict_official": strict_official,
            "artifacts": {
                "config": "config.json",
                "trials": "trials.jsonl",
                "submission": "submission.json",
                "report": "report.md",
                "taint": "taint.json",
            },
            "summary": {
                "unique_trials": len(selected),
                "trial_attempts": len(attempts),
                "infra_attempts": total_infra_attempts,
                "trace_files": len(manifest_trials),
                "audit_clean": True,
            },
            "trials": manifest_trials,
        }
        _write_json(temp_dir / "manifest.json", manifest)
        _replace_output(temp_dir, output_dir, force=force)
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise
    return output_dir


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Bundle destination (default: <run-dir>/trace_bundle)",
    )
    parser.add_argument(
        "--strict-official",
        action="store_true",
        help="Require the exact 12-dataset / 54-query / five-trial official matrix",
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing bundle")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output = build_bundle(
            args.run_dir,
            output_dir=args.output_dir,
            strict_official=args.strict_official,
            force=args.force,
        )
    except BundleError as exc:
        print(f"trace bundle error: {exc}", file=sys.stderr)
        return 2
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    summary = manifest["summary"]
    print(f"Trace bundle: {output}")
    print(
        f"Trials: {summary['unique_trials']} selected / {summary['trial_attempts']} attempts; "
        f"traces: {summary['trace_files']}; audit clean"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
