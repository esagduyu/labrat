"""Run provenance capture and cross-run comparability for DAB eval runs.

A DAB run's ``config.json`` records its flags (model, driver, levers, ...), but
flags matching is not sufficient to trust a comparison between two runs: the
*code* that executed those flags can have drifted between when a baseline run
was captured and when a comparand run executed, silently confounding the
result. This module records enough of the executing checkout's git state to
answer, later and by a third party, whether two runs are legitimately
comparable — without guessing from file timestamps.

Two pieces:

* :func:`capture_git_provenance` — call once per DAB run invocation and store
  the result under the ``"provenance"`` key of ``config.json``.
* :func:`check_comparability` — given two runs' provenance dicts (as loaded
  back out of their config.json files), decide whether their numbers may be
  compared. Provenance absent from either side always refuses to certify —
  it never assumes equality from silence.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ProvenanceDict = dict[str, Any]

Verdict = Literal["comparable", "code_diff", "provenance_missing"]

# Paths whose content cannot affect what a DAB run's agent actually does. Kept
# deliberately narrow: anything not matched here is treated as potentially
# behaviour-affecting (block rather than allow when unsure).
_INERT_PREFIXES: tuple[str, ...] = (
    "docs/",
    "tests/",
    ".superpowers/",
    "runs/",
    ".github/",
)
_INERT_SUFFIXES: tuple[str, ...] = (".md",)
_INERT_EXACT: frozenset[str] = frozenset({".gitignore", "LICENSE"})


def _classify_path(path: str) -> Literal["inert", "affecting"]:
    """Classify a changed path as unable ("inert") or able ("affecting") to
    change a DAB run's behaviour. Anything not provably inert is "affecting" —
    this is the "block rather than allow" default for the unclassified case."""
    if path in _INERT_EXACT:
        return "inert"
    if path.endswith(_INERT_SUFFIXES):
        return "inert"
    for prefix in _INERT_PREFIXES:
        if path.startswith(prefix):
            return "inert"
    return "affecting"


def _run_git(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _unavailable_provenance() -> ProvenanceDict:
    return {
        "git_commit": None,
        "git_branch": None,
        "git_dirty": None,
        "git_diff_files": [],
        "git_diff_sha256": None,
        "git_unavailable": True,
    }


def capture_git_provenance(repo_root: Path | None = None) -> ProvenanceDict:
    """Capture the git state of the checkout that is about to execute a DAB run.

    Resolves to the repo's top level first (so subsequent commands and the
    returned ``git_diff_files`` paths are always root-relative, regardless of
    the caller's cwd), then records:

    * ``git_commit`` — full HEAD sha.
    * ``git_branch`` — current branch name, or ``None`` if detached.
    * ``git_dirty`` — whether the working tree has any uncommitted changes
      (tracked modifications *or* untracked files).
    * ``git_diff_files`` — sorted root-relative paths touched by the dirty
      state (tracked + untracked).
    * ``git_diff_sha256`` — a hash of the *content* of that dirty state
      (the tracked diff plus each untracked file's content), so two dirty
      runs at the same commit can be told apart even when they touch the
      same path with different content, or touch an untracked path that
      `git diff` alone would never show.

    Returns an all-``None``/unavailable-flagged dict (never raises) if this
    is not a git checkout or git is not on PATH — a run outside version
    control cannot be certified comparable to anything, which is exactly the
    verdict :func:`check_comparability` gives it.
    """
    start = repo_root or Path.cwd()
    toplevel_out = _run_git(["rev-parse", "--show-toplevel"], start)
    if toplevel_out is None:
        return _unavailable_provenance()
    root = Path(toplevel_out.strip())

    commit_out = _run_git(["rev-parse", "HEAD"], root)
    if commit_out is None:
        return _unavailable_provenance()
    commit = commit_out.strip()

    branch_out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    branch_raw = (branch_out or "").strip()
    branch = branch_raw if branch_raw and branch_raw != "HEAD" else None

    status_out = _run_git(["status", "--porcelain", "--untracked-files=all"], root) or ""
    diff_out = _run_git(["diff", "HEAD"], root) or ""

    changed_files: set[str] = set()
    untracked_files: list[str] = []
    for line in status_out.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            # rename/copy lines: "old -> new"
            path = path.split(" -> ", 1)[1]
        changed_files.add(path)
        if code == "??":
            untracked_files.append(path)

    hasher = hashlib.sha256()
    hasher.update(diff_out.encode("utf-8", errors="surrogateescape"))
    for path in sorted(untracked_files):
        try:
            content = (root / path).read_bytes()
        except OSError:
            content = b""
        hasher.update(path.encode("utf-8", errors="surrogateescape"))
        hasher.update(hashlib.sha256(content).digest())

    return {
        "git_commit": commit,
        "git_branch": branch,
        "git_dirty": bool(changed_files),
        "git_diff_files": sorted(changed_files),
        "git_diff_sha256": hasher.hexdigest(),
        "git_unavailable": False,
    }


@dataclass
class ComparabilityResult:
    """Verdict from :func:`check_comparability`."""

    comparable: bool
    verdict: Verdict
    reason: str
    differing_files: list[str] = field(default_factory=lambda: [])


def _diff_between_commits(commit_a: str, commit_b: str, repo_root: Path) -> list[str] | None:
    """Sorted paths that differ between two commits, or None if undeterminable
    (e.g. a commit unknown to this checkout)."""
    if commit_a == commit_b:
        return []
    out = _run_git(["diff", "--name-only", commit_a, commit_b], repo_root)
    if out is None:
        return None
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def check_comparability(
    provenance_a: ProvenanceDict | None,
    provenance_b: ProvenanceDict | None,
    *,
    repo_root: Path | None = None,
    label_a: str = "run A",
    label_b: str = "run B",
) -> ComparabilityResult:
    """Decide whether two DAB runs' numbers may legitimately be compared.

    Refuses to certify (``verdict="provenance_missing"``) if provenance is
    absent, empty, or unavailable on either side — this never assumes
    equality from silence. Otherwise resolves the actual code delta (across
    commits, and across each side's own uncommitted dirty state) and blocks
    on anything not provably inert (see :func:`_classify_path`).
    """
    missing: list[str] = []
    for prov, label in ((provenance_a, label_a), (provenance_b, label_b)):
        if not prov or prov.get("git_commit") is None or prov.get("git_unavailable"):
            missing.append(label)
    if missing:
        return ComparabilityResult(
            comparable=False,
            verdict="provenance_missing",
            reason=(
                "Cannot certify comparability: no git provenance recorded for "
                f"{', '.join(missing)}. Refusing to assume equality from silence — "
                "this run predates provenance capture, ran outside a git checkout, "
                "or its config.json was hand-edited."
            ),
        )
    assert provenance_a is not None and provenance_b is not None  # narrowed by the loop above

    root = repo_root or Path.cwd()
    commit_a = str(provenance_a["git_commit"])
    commit_b = str(provenance_b["git_commit"])

    changed: set[str] = set()
    if commit_a != commit_b:
        between = _diff_between_commits(commit_a, commit_b, root)
        if between is None:
            return ComparabilityResult(
                comparable=False,
                verdict="code_diff",
                reason=(
                    f"{label_a} ran at {commit_a[:12]} and {label_b} at {commit_b[:12]}, "
                    "and this checkout cannot resolve a diff between them (one or both "
                    "commits are not reachable here). Cannot certify comparable — "
                    "blocking conservatively rather than assuming they match."
                ),
            )
        changed |= set(between)

    # Uncommitted deltas are additional, unmeasured differences unless byte-identical.
    if provenance_a.get("git_diff_sha256") != provenance_b.get("git_diff_sha256"):
        changed |= set(provenance_a.get("git_diff_files") or [])
        changed |= set(provenance_b.get("git_diff_files") or [])

    if not changed:
        return ComparabilityResult(
            comparable=True,
            verdict="comparable",
            reason=f"{label_a} and {label_b} executed from identical code state ({commit_a[:12]}).",
        )

    affecting = sorted(p for p in changed if _classify_path(p) == "affecting")
    if affecting:
        return ComparabilityResult(
            comparable=False,
            verdict="code_diff",
            reason=(
                f"{label_a} ({commit_a[:12]}) and {label_b} ({commit_b[:12]}) differ in "
                f"{len(affecting)} path(s) that can affect run behaviour: "
                f"{', '.join(affecting)}. These runs' numbers are NOT comparable."
            ),
            differing_files=affecting,
        )

    inert = sorted(changed)
    return ComparabilityResult(
        comparable=True,
        verdict="comparable",
        reason=(
            f"{label_a} ({commit_a[:12]}) and {label_b} ({commit_b[:12]}) differ only in "
            f"paths classified as inert (cannot affect run behaviour): {', '.join(inert)}."
        ),
        differing_files=inert,
    )
