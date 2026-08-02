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

Scope note: this module only inspects the labrat checkout. A DAB run's
behaviour and scoring also depend on the separate DataAgentBench repo (hints
text, ``validate.py``, ground truth) — drift there is a real, unrecorded axis
this module does not check. ``check_comparability``'s pass message names this
scope explicitly rather than claiming a fuller guarantee than it gives.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ProvenanceDict = dict[str, Any]

Verdict = Literal["comparable", "code_diff", "provenance_missing", "provenance_mixed"]

# Anchor for the default (no repo_root given) capture: the labrat package's own
# location, NOT the caller's cwd. eval_dab.py calls capture_git_provenance() bare;
# if it anchored to cwd, launching it from inside another git checkout (a plausible
# operator mistake) would silently record THAT repo's commit as valid labrat
# provenance. git resolves the real repo root from here via `rev-parse --show-toplevel`.
_PACKAGE_DIR = Path(__file__).resolve().parent

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

# Paths under these prefixes are ALWAYS affecting, regardless of extension. This
# must be checked before the .md suffix rule: src/**.md includes runtime-loaded
# assets (e.g. agent/prompts/system_base.md), and labrat_maze/**.md is the
# project Scent store — retrieval content served to the agent at runtime — so
# location has to win over suffix.
_AFFECTING_PREFIXES: tuple[str, ...] = ("src/", "scripts/", "labrat_maze/")


def _classify_path(path: str) -> Literal["inert", "affecting"]:
    """Classify a changed path as unable ("inert") or able ("affecting") to
    change a DAB run's behaviour. Anything not provably inert is "affecting" —
    this is the "block rather than allow" default for the unclassified case."""
    if path in _INERT_EXACT:
        return "inert"
    for prefix in _INERT_PREFIXES:
        if path.startswith(prefix):
            return "inert"
    for prefix in _AFFECTING_PREFIXES:
        if path.startswith(prefix):
            return "affecting"
    if path.endswith(_INERT_SUFFIXES):
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


def _unquote_git_path(raw: str) -> str:
    """Best-effort unquote of a git porcelain path field.

    git wraps a path in double quotes (and backslash-escapes it) when it
    contains characters core.quotePath considers unusual. A naive substring
    slice leaves the literal quote characters in the path, which then never
    matches any classification prefix/suffix and silently defaults to
    "affecting" for the wrong reason (an unrecognizable path) rather than the
    right one (an unclassified real path). Falls back to the raw string if it
    cannot be decoded — never raises.
    """
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        inner = raw[1:-1]
        try:
            return inner.encode("latin1").decode("unicode_escape").encode("latin1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return inner
    return raw


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
      state (tracked + untracked; both sides of a dirty rename).
    * ``git_diff_sha256`` — a hash of the *content* of that dirty state
      (the tracked diff plus each untracked file's content), so two dirty
      runs at the same commit can be told apart even when they touch the
      same path with different content, or touch an untracked path that
      `git diff` alone would never show.

    Returns an all-``None``/unavailable-flagged dict (never raises) if this is
    not a git checkout, git is not on PATH, or ANY git subcommand needed to
    build the record fails (including status/diff, not just the initial
    toplevel/HEAD resolution) — a degraded capture must never be reported as a
    verified-clean tree; it must be indistinguishable from "no provenance",
    which :func:`check_comparability` already refuses to certify.
    """
    start = repo_root or _PACKAGE_DIR
    toplevel_out = _run_git(["rev-parse", "--show-toplevel"], start)
    if toplevel_out is None:
        return _unavailable_provenance()
    root = Path(toplevel_out.strip())

    commit_out = _run_git(["rev-parse", "HEAD"], root)
    if commit_out is None:
        return _unavailable_provenance()
    commit = commit_out.strip()

    branch_out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    if branch_out is None:
        return _unavailable_provenance()
    branch_raw = branch_out.strip()
    branch = branch_raw if branch_raw and branch_raw != "HEAD" else None

    status_out = _run_git(["status", "--porcelain", "--untracked-files=all"], root)
    if status_out is None:
        return _unavailable_provenance()

    diff_out = _run_git(["diff", "HEAD"], root)
    if diff_out is None:
        return _unavailable_provenance()

    changed_files: set[str] = set()
    untracked_files: list[str] = []
    for line in status_out.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        rest = line[3:]
        raw_paths = rest.split(" -> ", 1) if " -> " in rest else [rest]
        paths = [_unquote_git_path(raw.strip()) for raw in raw_paths]
        changed_files.update(paths)
        if code == "??":
            untracked_files.extend(paths)

    hasher = hashlib.sha256()
    hasher.update(diff_out.encode("utf-8", errors="surrogateescape"))
    for path in sorted(untracked_files):
        try:
            content = (root / path).read_bytes()
        except OSError:
            # git status reported this path as untracked, but it can no longer
            # be read (deleted/permissions/a race between the status call and
            # this read). Treating it as empty content would let two genuinely
            # different untracked files both hash as a matching/clean diff --
            # the same "unknown read as verified" shape as C1. Degrade the
            # whole capture instead of silently smoothing over the gap.
            return _unavailable_provenance()
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
    equality from silence. Also refuses (``verdict="provenance_mixed"``) if
    either side's provenance is itself flagged as spanning more than one code
    state (see ``provenance_mixed`` in ``scripts/eval_dab.py``) — a run whose
    own trials.jsonl was produced across a resume-time commit drift cannot be
    attributed to a single commit, so no comparison involving it is
    trustworthy regardless of what commit its config.json currently shows.

    Otherwise resolves the actual code delta (across commits, and across each
    side's own uncommitted dirty state) and blocks on anything not provably
    inert (see :func:`_classify_path`). A diff-hash mismatch that no file list
    can explain (a degraded capture, or a hand-built provenance record) is
    itself treated as evidence of a difference and refused — it is never
    read as "no changed paths -> comparable".

    Scope: only the labrat checkout is inspected. A DAB run's behaviour and
    scoring also depend on the separate DataAgentBench repo (hints,
    validate.py, ground truth); this function does not check it, and its
    "comparable" reason says so rather than claiming a fuller guarantee.
    """
    missing: list[str] = []
    for prov, label in ((provenance_a, label_a), (provenance_b, label_b)):
        # `not prov.get("git_commit")` (falsy), not `is None`: an empty-string
        # commit is not a real identity either, and two empty strings compare
        # equal to each other -- letting that reach the commit-equality check
        # below would certify two "no commit" records as the same commit.
        if not prov or not prov.get("git_commit") or prov.get("git_unavailable"):
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

    mixed: list[str] = []
    for prov, label in ((provenance_a, label_a), (provenance_b, label_b)):
        if prov.get("provenance_mixed"):
            mixed.append(label)
    if mixed:
        return ComparabilityResult(
            comparable=False,
            verdict="provenance_mixed",
            reason=(
                f"Cannot certify comparability: {', '.join(mixed)} spans more than one "
                "code state (its config.json's provenance was recaptured to a different "
                "commit or dirty-diff across a resume/retry, so its trials.jsonl is not "
                "attributable to a single commit). Refusing to certify comparable."
            ),
        )

    root = repo_root or _PACKAGE_DIR
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
    diff_hash_a = provenance_a.get("git_diff_sha256")
    diff_hash_b = provenance_b.get("git_diff_sha256")
    dirty_hash_mismatch = diff_hash_a != diff_hash_b
    if dirty_hash_mismatch:
        dirty_files = set(provenance_a.get("git_diff_files") or []) | set(
            provenance_b.get("git_diff_files") or []
        )
        if not dirty_files:
            # The hashes disagree but neither side names a single touched path --
            # a degraded capture or a hand-built record. Treat the inequality
            # itself as evidence of a difference; never fall through to
            # "no changed paths -> comparable".
            return ComparabilityResult(
                comparable=False,
                verdict="code_diff",
                reason=(
                    f"{label_a} and {label_b} have different uncommitted-diff hashes "
                    f"({diff_hash_a!r} vs {diff_hash_b!r}) but no changed file paths "
                    "explain the difference. Refusing to certify comparable — the hash "
                    "inequality is treated as evidence of a difference even though it "
                    "cannot be named."
                ),
            )
        changed |= dirty_files

    if not changed:
        return ComparabilityResult(
            comparable=True,
            verdict="comparable",
            reason=(
                f"{label_a} and {label_b} ran from the same labrat checkout state "
                f"({commit_a[:12]}, no uncommitted delta difference). This does not check "
                "the separate DataAgentBench repo (hints/validate.py/ground truth), which "
                "is an unrecorded axis."
            ),
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
            f"{label_a} ({commit_a[:12]}) and {label_b} ({commit_b[:12]}) differ in the "
            f"labrat checkout only in paths classified as inert (cannot affect run "
            f"behaviour): {', '.join(inert)}. This does not check the separate "
            "DataAgentBench repo (hints/validate.py/ground truth), which is an "
            "unrecorded axis."
        ),
        differing_files=inert,
    )
