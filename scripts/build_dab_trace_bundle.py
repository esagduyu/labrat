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
import os
import re
import shutil
import stat
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from labrat.eval.benchmarks.dab.taint import (
    TRACE_FILENAME_BY_DRIVER,
    audit_run,
    gate,
    resolve_trial_trace,
    validate_trace_jsonl,
)

CORE_ARTIFACTS = ("config.json", "trials.jsonl", "submission.json", "report.md")
PROTECTED_RUN_ARTIFACTS = (*CORE_ARTIFACTS, "taint.json")
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
_CREDENTIAL_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "auth_token",
    "refresh_token",
    "authorization",
    "password",
    "passwd",
    "client_secret",
    "secret_key",
    "credentials",
    "credential",
    "dsn",
    "connection_uri",
    "database_url",
    "token",
    "private_key",
    "secret_access_key",
}
_SAFE_NON_CREDENTIAL_KEYS = {
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "cache_write_tokens",
    "max_tokens",
    "refresh_token_count",
}
_CREDENTIAL_KEY_SUFFIXES = (
    "_api_key",
    "_access_token",
    "_auth_token",
    "_refresh_token",
    "_secret_access_key",
    "_client_secret",
    "_private_key",
    "_password",
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk-(?:proj-)?|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|access[_ -]?token|auth[_ -]?token|refresh[_ -]?token|"
        r"password|passwd|client[_ -]?secret|authorization|dsn)\b\s*[:=]\s*"
        r"(?!<redacted>|redacted|none|null|\*{3})[\"']?[^\s,\"']{4,}"
    ),
)
_SAFE_CREDENTIAL_PLACEHOLDERS = {
    "",
    "none",
    "null",
    "redacted",
    "<redacted>",
    "***",
    "not-set",
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


def _credential_value_present(value: Any) -> bool:
    if value is None or value is False or value == 0:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized not in _SAFE_CREDENTIAL_PLACEHOLDERS and not (
            normalized.startswith("${") and normalized.endswith("}")
        )
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _is_credential_key(normalized_key: str) -> bool:
    if normalized_key in _SAFE_NON_CREDENTIAL_KEYS:
        return False
    return normalized_key in _CREDENTIAL_KEYS or normalized_key.endswith(_CREDENTIAL_KEY_SUFFIXES)


def _secret_reason(value: Any, *, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized_key = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
            child_path = f"{path}.{key}"
            if _is_credential_key(normalized_key) and _credential_value_present(item):
                return f"credential-shaped field {child_path}"
            nested = _secret_reason(item, path=child_path)
            if nested is not None:
                return nested
        return None
    if isinstance(value, list):
        for index, item in enumerate(value):
            nested = _secret_reason(item, path=f"{path}[{index}]")
            if nested is not None:
                return nested
        return None
    if isinstance(value, str):
        for pattern in _SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                return f"secret-shaped value at {path}"
    return None


def _assert_no_secrets(value: Any, *, source: str) -> None:
    reason = _secret_reason(value)
    if reason is not None:
        raise BundleError(f"credential material detected in {source}: {reason}")


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


def _submission_entries(submission: Any) -> dict[tuple[str, int], dict[str, Any]]:
    if not isinstance(submission, list):
        raise BundleError("submission.json must contain a JSON array")
    entries: dict[tuple[str, int], dict[str, Any]] = {}
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
        if key in entries:
            raise BundleError(f"submission.json has duplicate entry for {key[0]} trial {key[1]}")
        entries[key] = entry
    return entries


def _canonical_answer(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _trial_answer(attempt: TrialAttempt) -> Any:
    artifact = attempt.record.get("artifact")
    if isinstance(artifact, dict):
        return artifact.get("payload", "")
    return "" if artifact is None else artifact


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


def _load_trace_records(source: Path) -> list[dict[str, Any]]:
    error = validate_trace_jsonl(source)
    if error is not None:
        raise BundleError(error)
    records: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            if not isinstance(record, dict):  # guarded by validate_trace_jsonl
                raise BundleError(f"malformed trace {source.name}: expected object")
            records.append(record)
    return records


def _copy_trace_records(records: list[dict[str, Any]], destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(destination, records)
    return len(records)


def _replace_output(
    temp_dir: Path,
    output_dir: Path,
    *,
    run_dir: Path,
    force: bool,
) -> None:
    # Revalidate immediately before mutation so a swapped-in symlink cannot turn
    # --force into deletion of an external target.
    _validate_output_destination(run_dir, output_dir)
    _reject_symlink_components(temp_dir)
    if output_dir.exists():
        if not force:
            raise BundleError(f"output already exists: {output_dir} (pass --force to replace)")
        if output_dir.is_dir():
            shutil.rmtree(output_dir)
        else:
            output_dir.unlink()
    temp_dir.replace(output_dir)


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _reject_symlink_components(path: Path) -> Path:
    """Return a lexical absolute path after lstat-checking every existing component."""
    lexical = _lexical_absolute(path)
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        except OSError as exc:
            raise BundleError(f"cannot inspect path component {current}: {exc}") from exc
        if stat.S_ISLNK(mode):
            raise BundleError(f"symlink path component is not allowed: {current}")
    return lexical


def _validate_run_artifact(run_dir: Path, name: str, *, required: bool) -> Path | None:
    path = _reject_symlink_components(run_dir / name)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        if required:
            raise BundleError(f"required artifact missing: {name}") from exc
        return None
    except OSError as exc:
        raise BundleError(f"invalid {name}: {exc}") from exc
    if stat.S_ISLNK(mode):
        raise BundleError(f"symlink run artifact is not allowed: {name}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BundleError(f"invalid {name}: {exc}") from exc
    if resolved.parent != run_dir or not stat.S_ISREG(mode):
        raise BundleError(f"run artifact must be a regular file directly under run_dir: {name}")
    return resolved


def _validate_output_destination(run_dir: Path, destination: Path) -> Path:
    """Resolve and reject destinations that could destroy bundle inputs."""
    run = run_dir.expanduser().resolve()
    lexical = _reject_symlink_components(destination)
    resolved = lexical.resolve()
    core_inputs = tuple(run / name for name in PROTECTED_RUN_ARTIFACTS)
    scratch = (run / "scratch").resolve()

    unsafe = resolved == run or run.is_relative_to(resolved)
    unsafe = unsafe or resolved == scratch or resolved.is_relative_to(scratch)
    unsafe = unsafe or any(
        resolved == artifact or resolved.is_relative_to(artifact) for artifact in core_inputs
    )
    if unsafe:
        raise BundleError(f"unsafe output destination overlaps protected run inputs: {resolved}")
    return resolved


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
    output_dir = _validate_output_destination(
        run_dir,
        output_dir if output_dir is not None else run_dir / "trace_bundle",
    )

    for artifact in CORE_ARTIFACTS:
        _validate_run_artifact(run_dir, artifact, required=True)
    # audit_run writes this path. Reject a pre-existing link before that write so
    # the audit cannot clobber an external file.
    _validate_run_artifact(run_dir, "taint.json", required=False)

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
    submission_entries = _submission_entries(submission)
    submission_keys = set(submission_entries)
    if submission_keys != selected_keys:
        missing = len(selected_keys - submission_keys)
        extra = len(submission_keys - selected_keys)
        raise BundleError(
            "submission.json does not match selected semantic trials "
            f"(missing={missing}, extra={extra})"
        )
    for key, attempt in selected.items():
        if _canonical_answer(submission_entries[key]["answer"]) != _canonical_answer(
            _trial_answer(attempt)
        ):
            raise BundleError(
                f"submission answer does not match selected semantic trial for "
                f"{key[0]} trial {key[1]}"
            )
    if strict_official:
        _validate_strict_official(config, selected_keys)

    verdicts = audit_run(run_dir / "trials.jsonl", run_dir / "scratch")
    _validate_run_artifact(run_dir, "taint.json", required=True)
    audit_ok, offenders = gate(verdicts)
    if not audit_ok:
        preview = ", ".join(offenders[:8])
        suffix = "" if len(offenders) <= 8 else f", +{len(offenders) - 8} more"
        raise BundleError(f"trace audit failed for: {preview}{suffix}")

    try:
        report = (run_dir / "report.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BundleError(f"invalid report.md: {exc}") from exc

    _assert_no_secrets(config, source="config.json")
    _assert_no_secrets([attempt.record for attempt in attempts], source="trials.jsonl")
    _assert_no_secrets(submission, source="submission.json")
    _assert_no_secrets(report, source="report.md")
    _assert_no_secrets(verdicts, source="taint.json")

    trace_sources: dict[tuple[str, int], tuple[Path, list[dict[str, Any]]]] = {}
    for task_id, trial_num in sorted(selected):
        try:
            source = resolve_trial_trace(run_dir / "scratch", task_id, trial_num, trace_filename)
        except ValueError as exc:
            raise BundleError(
                f"unsafe trace source for {task_id} trial {trial_num}: {exc}"
            ) from exc
        records = _load_trace_records(source)
        _assert_no_secrets(records, source=f"trace {task_id} trial {trial_num}")
        trace_sources[(task_id, trial_num)] = (source, records)

    temp_dir = _validate_output_destination(
        run_dir,
        output_dir.parent / f".{output_dir.name}.tmp-{uuid.uuid4().hex}",
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir()
    try:
        _write_json(temp_dir / "config.json", config)
        _write_jsonl(temp_dir / "trials.jsonl", [attempt.record for attempt in attempts])
        _write_json(temp_dir / "submission.json", submission)
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

            _source, trace_records_payload = trace_sources[key]
            relative_trace = (
                Path("traces") / f"{_safe_name(task_id)}__trial{trial_num}" / trace_filename
            )
            destination = temp_dir / relative_trace
            trace_records = _copy_trace_records(trace_records_payload, destination)
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
        _replace_output(temp_dir, output_dir, run_dir=run_dir, force=force)
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
