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


def classify_trial(text: str) -> str:
    return CHEATING if detect_contamination(text) else CLEAN


def audit_run(trials_jsonl: Path, scratch_dir: Path) -> dict[str, str]:
    """Classify every trial in `trials_jsonl` and write `taint.json` beside it.

    Scans each trial's recorded answer text (artifact + reason) plus its per-call
    MCP trace (`<scratch>/<task>__trial<n>/mcp_tool_calls.jsonl`) if present, for
    the answer-key / external-dataset leakage patterns in scent_audit. Returns
    `{f"{task_id}:{trial_num}": verdict}`.
    """
    verdicts: dict[str, str] = {}
    lines = trials_jsonl.read_text().splitlines() if trials_jsonl.exists() else []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        r = cast(dict[str, Any], json.loads(line))
        task_id = str(r["task_id"])
        trial_num = int(r["trial_num"])
        key = f"{task_id}:{trial_num}"
        parts = [str(r.get("artifact") or ""), str(r.get("reason") or "")]
        safe = task_id.replace(":", "_")
        trace = scratch_dir / f"{safe}__trial{trial_num}" / "mcp_tool_calls.jsonl"
        if trace.exists():
            parts.append(trace.read_text())
        verdicts[key] = classify_trial("\n".join(parts))
    (trials_jsonl.parent / "taint.json").write_text(json.dumps(verdicts, indent=2))
    return verdicts


def gate(verdicts: dict[str, str]) -> tuple[bool, list[str]]:
    """Return (ok, offending_keys). `ok` is False if any trial verdict is CHEATING."""
    offenders = [k for k, v in verdicts.items() if v == CHEATING]
    return (not offenders, offenders)
