"""Pre-submission taint gate: classify each DAB trial as clean / external-oracle-cheating /
audit-error, and refuse to assemble a submission from an unaudited run. Uses the single
contamination pattern list in maze/scent_audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from labrat.maze.scent_audit import detect_contamination

CLEAN = "clean"
CHEATING = "external-oracle-cheating"
AUDIT_ERROR = "audit-error"

TRACE_FILENAMES = ("mcp_tool_calls.jsonl", "agent_tool_calls.jsonl")
TRACE_FILENAME_BY_DRIVER = {
    "claude-mcp": "mcp_tool_calls.jsonl",
    "labrat-agent": "agent_tool_calls.jsonl",
}
_TRACE_FIELDS = {"tool", "input", "ok", "output", "latency_ms"}


def classify_trial(text: str) -> str:
    return CHEATING if detect_contamination(text) else CLEAN


def expected_trace_filenames(run_dir: Path) -> tuple[str, ...]:
    """Return the trace filename(s) acceptable for a run.

    A configured driver has one exact trace contract. Older unit fixtures and
    hand-built runs without ``config.json`` may use either shared trace format,
    but still need at least one trace file.
    """
    config_path = run_dir / "config.json"
    if not config_path.exists():
        return TRACE_FILENAMES
    try:
        parsed: Any = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return TRACE_FILENAMES
    if not isinstance(parsed, dict):
        return TRACE_FILENAMES
    config = cast(dict[str, Any], parsed)
    filename = TRACE_FILENAME_BY_DRIVER.get(str(config.get("driver") or ""))
    return (filename,) if filename is not None else TRACE_FILENAMES


def validate_trace_jsonl(trace: Path) -> str | None:
    """Return an audit error message, or ``None`` when a trace is valid.

    An existing empty file is valid: it represents a completed agent attempt
    that made zero tool calls. Non-empty lines must match the shared tool-trace
    schema emitted by :func:`append_tool_trace`.
    """
    try:
        lines = trace.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return f"unreadable trace {trace.name}: {exc}"

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            parsed: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            return f"malformed trace {trace.name} line {line_number}: {exc.msg}"
        if not isinstance(parsed, dict):
            return f"malformed trace {trace.name} line {line_number}: expected object"
        record = cast(dict[str, Any], parsed)
        missing = _TRACE_FIELDS - record.keys()
        if missing:
            return (
                f"malformed trace {trace.name} line {line_number}: "
                f"missing {', '.join(sorted(missing))}"
            )
        if not isinstance(record["tool"], str) or not record["tool"]:
            return f"malformed trace {trace.name} line {line_number}: invalid tool"
        if not isinstance(record["input"], dict):
            return f"malformed trace {trace.name} line {line_number}: invalid input"
        if not isinstance(record["ok"], bool):
            return f"malformed trace {trace.name} line {line_number}: invalid ok"
        if not isinstance(record["output"], str):
            return f"malformed trace {trace.name} line {line_number}: invalid output"
        latency = record["latency_ms"]
        if isinstance(latency, bool) or not isinstance(latency, int | float):
            return f"malformed trace {trace.name} line {line_number}: invalid latency_ms"
    return None


def _merge_verdict(current: str | None, new: str) -> str:
    """Keep the strongest verdict when retry rows share a trial key."""
    if current == CHEATING or new == CHEATING:
        return CHEATING
    if current == AUDIT_ERROR or new == AUDIT_ERROR:
        return AUDIT_ERROR
    return CLEAN


def _artifact_text(r: dict[str, Any]) -> str:
    """Extract the agent-produced answer text from a trial record's `artifact` field.

    `artifact` is `{"type": "text", "payload": <final answer>}` when present — the
    `payload` is what the agent actually wrote. Never falls back to `reason`: that
    field holds the DAB VALIDATOR's message (e.g. the official validators literally
    return "Ground truth found in LLM output." on a clean PASS), not agent output.
    """
    artifact = r.get("artifact")
    if isinstance(artifact, dict):
        artifact = cast(dict[str, Any], artifact)
        payload = artifact.get("payload")
        return str(payload) if payload is not None else ""
    if artifact is None:
        return ""
    return str(artifact)


def audit_run(trials_jsonl: Path, scratch_dir: Path) -> dict[str, str]:
    """Classify every trial in `trials_jsonl` and write `taint.json` beside it.

    Scans ONLY agent-produced channels: the artifact payload (the agent's actual
    answer, see `_artifact_text`) plus its per-call trace(s) —
    `<scratch>/<task>__trial<n>/mcp_tool_calls.jsonl` (claude-mcp driver) and/or
    `agent_tool_calls.jsonl` (labrat-agent driver). The configured driver's trace
    is required and every non-empty line must match the shared trace schema.
    Missing or malformed traces classify as :data:`AUDIT_ERROR`; an existing
    empty JSONL file is a valid zero-tool trace.

    Deliberately does NOT substring-scan `reason` — that's the DAB validator's PASS/
    FAIL message, not agent output, and several official validators (agnews,
    bookreview, music_brainz) literally emit "Ground truth found in LLM output." on a
    clean PASS. The one exception: if the suite's own contamination backstop already
    flagged the trial (`reason` starting with `"contaminated:"`), that verdict is
    trusted after the required trace passes its integrity check.

    Returns `{f"{task_id}:{trial_num}": verdict}`.
    """
    verdicts: dict[str, str] = {}
    try:
        lines = trials_jsonl.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        verdicts["__run__"] = AUDIT_ERROR
        (trials_jsonl.parent / "taint.json").write_text(
            json.dumps(verdicts, indent=2), encoding="utf-8"
        )
        return verdicts

    expected_names = expected_trace_filenames(trials_jsonl.parent)
    for source_line, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if not isinstance(parsed, dict):
                raise TypeError("trial record is not an object")
            r = cast(dict[str, Any], parsed)
            task_id = str(r["task_id"])
            trial_num = int(r["trial_num"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            verdicts[f"__line_{source_line}__"] = AUDIT_ERROR
            continue
        key = f"{task_id}:{trial_num}"

        reason = str(r.get("reason") or "")
        parts = [_artifact_text(r)]
        safe = task_id.replace(":", "_")
        trial_dir = scratch_dir / f"{safe}__trial{trial_num}"
        required_paths = [trial_dir / name for name in expected_names]
        present_required = [trace for trace in required_paths if trace.is_file()]
        if not present_required:
            verdicts[key] = _merge_verdict(verdicts.get(key), AUDIT_ERROR)
            continue

        # Scan any additional shared-format trace too. A stale second trace must
        # not become a blind spot for contamination or malformed JSONL.
        traces = list(present_required)
        for trace_name in TRACE_FILENAMES:
            candidate = trial_dir / trace_name
            if candidate.is_file() and candidate not in traces:
                traces.append(candidate)

        trace_error = False
        for trace in traces:
            if validate_trace_jsonl(trace) is not None:
                trace_error = True
                break
            parts.append(trace.read_text(encoding="utf-8"))
        if trace_error:
            verdict = AUDIT_ERROR
        elif reason.startswith("contaminated:"):
            verdict = CHEATING
        else:
            verdict = classify_trial("\n".join(parts))
        verdicts[key] = _merge_verdict(verdicts.get(key), verdict)

    (trials_jsonl.parent / "taint.json").write_text(
        json.dumps(verdicts, indent=2), encoding="utf-8"
    )
    return verdicts


def gate(verdicts: dict[str, str]) -> tuple[bool, list[str]]:
    """Reject every verdict other than explicitly ``clean``."""
    offenders = sorted(k for k, verdict in verdicts.items() if verdict != CLEAN)
    return (not offenders, offenders)
