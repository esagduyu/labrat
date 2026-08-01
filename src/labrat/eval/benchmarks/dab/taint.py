"""Pre-submission taint gate: classify each DAB trial as clean / external-oracle-cheating /
audit-error, and refuse to assemble a submission from an unaudited run. Uses the single
contamination pattern list in maze/scent_audit."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, cast

from labrat.eval.benchmarks.dab.taint_structural import TaintFinding, scan_records
from labrat.maze.scent_audit import CONTAMINATION_PATTERNS, detect_contamination

CLEAN = "clean"
CHEATING = "external-oracle-cheating"
AUDIT_ERROR = "audit-error"

TRACE_FILENAMES = ("mcp_tool_calls.jsonl", "agent_tool_calls.jsonl")
TRACE_FILENAME_BY_DRIVER = {
    "claude-mcp": "mcp_tool_calls.jsonl",
    "labrat-agent": "agent_tool_calls.jsonl",
}
ANSWER_ONLY_DRIVERS = {"raw-bash"}
_TRACE_FIELDS = {"tool", "input", "ok", "output", "latency_ms"}
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*:[1-9][0-9]*$")


def task_trial_dir_name(task_id: str, trial_num: int) -> str:
    """Map one canonical DAB task key to its scratch directory name."""
    dataset = task_id.rsplit(":", 1)[0]
    if not _TASK_ID_RE.fullmatch(task_id) or ".." in dataset:
        raise ValueError(f"invalid DAB task_id: {task_id!r}")
    if isinstance(trial_num, bool) or trial_num < 0:
        raise ValueError(f"invalid DAB trial_num: {trial_num!r}")
    return f"{task_id.replace(':', '_')}__trial{trial_num}"


def resolve_trial_trace(
    scratch_dir: Path,
    task_id: str,
    trial_num: int,
    trace_filename: str,
) -> Path:
    """Return a contained, non-symlinked trace path or fail closed."""
    if trace_filename not in TRACE_FILENAMES:
        raise ValueError(f"invalid trace filename: {trace_filename!r}")
    if scratch_dir.is_symlink():
        raise ValueError("scratch directory must not be a symlink")
    try:
        scratch_root = scratch_dir.resolve(strict=True)
    except OSError as exc:
        raise ValueError("scratch directory is missing or unreadable") from exc
    trial_dir = scratch_dir / task_trial_dir_name(task_id, trial_num)
    if trial_dir.is_symlink():
        raise ValueError("trial directory must not be a symlink")
    try:
        resolved_trial = trial_dir.resolve(strict=True)
        resolved_trial.relative_to(scratch_root)
    except (OSError, ValueError) as exc:
        raise ValueError("trial directory is missing or escapes scratch") from exc
    if not resolved_trial.is_dir():
        raise ValueError("trial path is not a directory")

    trace = trial_dir / trace_filename
    if trace.is_symlink():
        raise ValueError("trace file must not be a symlink")
    try:
        resolved_trace = trace.resolve(strict=True)
        resolved_trace.relative_to(scratch_root)
    except (OSError, ValueError) as exc:
        raise ValueError("trace file is missing or escapes scratch") from exc
    if not resolved_trace.is_file():
        raise ValueError("trace path is not a regular file")
    return resolved_trace


def classify_trial(text: str) -> str:
    return CHEATING if detect_contamination(text) else CLEAN


# Needles that are ordinary prose, not evidence of access. Two ways they occur innocently:
# an analyst who hand-labels a validation sample calls it "ground truth", and the BENCHMARK
# DATA ITSELF contains such phrases — a bookreview Teacher's Edition listing reads "chapter
# and unit tests with answer keys", which an ordinary run_sql pulls into the tool trace and
# which blocked a whole ablation arm on 2026-07-31.
#
# The FILE-shaped forms ("ground_truth", "validate.py") stay unconditional: a filename is
# never innocent. So do the external-dataset needles — there is no benign reading of pulling
# labels from an upstream corpus.
#
# "gold answer" is deliberately NOT in this list. It was added alongside "answer key" on
# the strength of the SAME bookreview false positive ("chapter and unit tests with answer
# keys"), but that justification only covers "answer key" — "gold answer" never appeared in
# that finding and has no benign reading in benchmark row content. Corroboration-gating it
# reopened a memory-recited-leak path: a trial that recites "the gold answer is ..." from
# memory (no structural access) would otherwise pass. See ``audit_run``'s channel split
# below, which is what actually fixed the bookreview false positive without this.
_PROSE_ONLY_NEEDLES = ("ground truth", "ground-truth", "answer key")


def _has_unambiguous_needle(text: str) -> bool:
    """True when the text matches a contamination needle OTHER than the prose forms."""
    low = text.lower()
    return any(
        needle in low
        for needle, _tag in CONTAMINATION_PATTERNS
        if needle not in _PROSE_ONLY_NEEDLES
    )


def classify_trial_corroborated(text: str, structural_findings: int) -> str:
    """Classify a trial, requiring corroboration when the ONLY signal is prose.

    ``detect_contamination`` is a substring matcher, and its bluntness is what makes it
    hard to evade — it stays. But two of its needles, "ground truth" and "ground-truth",
    are ordinary methodological vocabulary: on 2026-07-31 two trials were condemned for
    saying they had "hand-labeled 250 randomly drawn articles as ground truth" to score
    their own classifier. The structural scan found nothing on either trace and the only
    benchmark path touched was the dataset the harness itself attaches, yet the shard
    was blocked from writing its submission and burned hours of retries.

    So a match on those two prose forms ALONE now needs corroboration — either a
    structural finding (an answer-shaped path/file/URL actually used) or any other
    needle in the list. This is deliberately narrow and is NOT a whitelist:

    * ``ground_truth`` (underscore — a filename) still condemns on its own.
    * ``validate.py``, ``answer key`` still condemn on their own.
    * ``gold answer`` is NOT in the prose-only list at all — it has no benign reading
      in benchmark row content, so it condemns unconditionally, same as ``ground_truth``.
    * ``load_dataset`` / ``huggingface`` / a named label corpus still condemn on their own.
    * Any trial that genuinely reads an answer-key path trips the structural scanner
      and is condemned regardless of its wording.
    """
    tag = detect_contamination(text)
    if tag is None:
        return CLEAN
    if structural_findings == 0 and not _has_unambiguous_needle(text):
        return CLEAN
    return CHEATING


def parse_trace_records(text: str) -> list[dict[str, Any]]:
    """Parse a schema-valid trace JSONL text into records (skip blank lines)."""
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed: Any = json.loads(line)
        if isinstance(parsed, dict):
            records.append(cast(dict[str, Any], parsed))
    return records


def scan_trace_text(text: str) -> list[TaintFinding]:
    """Taint v2 structural scan over one trace's JSONL text (already validated)."""
    return scan_records(parse_trace_records(text))


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
    driver = str(config.get("driver") or "")
    if driver in ANSWER_ONLY_DRIVERS:
        return ()
    filename = TRACE_FILENAME_BY_DRIVER.get(driver)
    return (filename,) if filename is not None else TRACE_FILENAMES


def validate_trace_jsonl(trace: Path) -> str | None:
    """Return an audit error message, or ``None`` when a trace is valid.

    An existing empty file is valid: it represents a completed agent attempt
    that made zero tool calls. Non-empty lines must match the shared tool-trace
    schema emitted by :func:`append_tool_trace`.
    """
    if trace.is_symlink():
        return f"unsafe trace {trace.name}: symlinks are not allowed"
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
    empty JSONL file is a valid zero-tool trace. The legacy ``raw-bash`` driver
    is explicitly answer-only/report-only, so its artifact is contamination-
    scanned without claiming trace completeness.

    The artifact and trace channels are classified separately (except on the
    ``raw-bash``/answer-only path, which has no trace channel): the artifact — the
    agent's own answer — always gets the full unconditional needle list via
    ``classify_trial``, since nothing about benchmark row content can explain what the
    agent chose to write. The trace(s) — where benchmark DATA lands, e.g. a
    Teacher's-Edition listing that legitimately reads "answer keys" — get
    ``classify_trial_corroborated``, which requires corroboration for the prose-only
    needles. Either channel condemning the trial is enough.

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
        if not expected_names:
            verdict = CHEATING if reason.startswith("contaminated:") else classify_trial(parts[0])
            verdicts[key] = _merge_verdict(verdicts.get(key), verdict)
            continue
        try:
            trial_dir = scratch_dir / task_trial_dir_name(task_id, trial_num)
        except ValueError:
            verdicts[key] = _merge_verdict(verdicts.get(key), AUDIT_ERROR)
            continue

        present_required: list[Path] = []
        unsafe_trace_path = False
        for trace_name in expected_names:
            candidate = trial_dir / trace_name
            if not candidate.exists() and not candidate.is_symlink():
                continue
            try:
                present_required.append(
                    resolve_trial_trace(scratch_dir, task_id, trial_num, trace_name)
                )
            except ValueError:
                unsafe_trace_path = True
                break
        if unsafe_trace_path or not present_required:
            verdicts[key] = _merge_verdict(verdicts.get(key), AUDIT_ERROR)
            continue

        # Scan any additional shared-format trace too. A stale second trace must
        # not become a blind spot for contamination or malformed JSONL.
        traces = list(present_required)
        for trace_name in TRACE_FILENAMES:
            candidate = trial_dir / trace_name
            if candidate.exists() or candidate.is_symlink():
                try:
                    resolved = resolve_trial_trace(scratch_dir, task_id, trial_num, trace_name)
                except ValueError:
                    unsafe_trace_path = True
                    break
                if resolved not in traces:
                    traces.append(resolved)

        if unsafe_trace_path:
            verdicts[key] = _merge_verdict(verdicts.get(key), AUDIT_ERROR)
            continue

        trace_error = False
        structural_findings: list[TaintFinding] = []
        for trace in traces:
            if validate_trace_jsonl(trace) is not None:
                trace_error = True
                break
            text = trace.read_text(encoding="utf-8")
            parts.append(text)
            # Taint v2: structural inspection of tool inputs (file sources,
            # folded SQL literals, composite-tool steps) — catches obfuscated
            # or needle-free answer-key access the text scan cannot see.
            structural_findings.extend(scan_trace_text(text))
        if trace_error:
            verdict = AUDIT_ERROR
        elif structural_findings or (
            reason.startswith("contaminated:") and reason != "contaminated:answer_key"
        ):
            # Structural evidence (an answer-shaped path/file/URL actually used) or an
            # external-label source condemns outright. A runtime
            # "contaminated:answer_key" reason does NOT, on its own: that detector is
            # the same substring matcher, and "ground truth" is ordinary analyst
            # vocabulary. It falls through to the corroborated check below, which
            # re-condemns it the moment any structural evidence exists.
            verdict = CHEATING
            # P3-5: surface the first few structural findings so a gate
            # offender is actionable without re-running the scan by hand.
            # taint.json / the verdict vocabulary stay unchanged.
            for finding in structural_findings[:3]:
                print(
                    f"taint-gate: {key}: structural[{finding.layer}:{finding.tag}] "
                    f"event {finding.event_index} {finding.tool}: {finding.detail}",
                    file=sys.stderr,
                )
        else:
            # Two different channels, two different rules. `parts[0]` is the agent's OWN
            # ANSWER (`_artifact_text`) — nothing about benchmark row content can explain
            # what the agent chose to write, so it gets the full unconditional needle list
            # via `classify_trial`, exactly like the answer-only (raw-bash) branch above.
            # `parts[1:]` is the tool trace(s), where BENCHMARK DATA lands (e.g. the
            # bookreview Teacher's Edition listing) — that channel keeps the corroboration
            # rule for the prose-only needles. Either channel condemning is enough.
            artifact_verdict = classify_trial(parts[0])
            trace_verdict = classify_trial_corroborated(
                "\n".join(parts[1:]), len(structural_findings)
            )
            verdict = CHEATING if CHEATING in (artifact_verdict, trace_verdict) else CLEAN
        verdicts[key] = _merge_verdict(verdicts.get(key), verdict)

    (trials_jsonl.parent / "taint.json").write_text(
        json.dumps(verdicts, indent=2), encoding="utf-8"
    )
    return verdicts


def gate(verdicts: dict[str, str]) -> tuple[bool, list[str]]:
    """Reject every verdict other than explicitly ``clean``.

    Fails CLOSED on an empty verdict set. ``audit_run`` derives verdicts by
    iterating ``trials.jsonl``, so an unwritten or truncated trials file yields
    ``{}`` — which used to pass the gate and let ``submission.json`` be written
    from a run where **nothing was audited**. That is precisely the shape of the
    2026-06 answer-key retraction, so an audit covering zero trials is treated as
    a failure, not a pass (observed live 2026-07-30 on shards whose trials.jsonl
    was orphaned mid-run).
    """
    if not verdicts:
        return (False, ["__empty_audit__"])
    offenders = sorted(k for k, verdict in verdicts.items() if verdict != CLEAN)
    return (not offenders, offenders)
