"""Tests for the scripts/check_dab_comparability.py CLI.

This is the operator-facing entry point for the comparability guard: it reads
two DAB run directories' config.json (or one directory plus the live
checkout) and exits non-zero unless it can positively certify the runs are
comparable.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.check_dab_comparability import main

# sha256 of an empty diff -- what capture_git_provenance() records for a clean tree.
# Tests that compare a hand-built provenance record against a --live capture of a
# real clean repo must use this, not an arbitrary placeholder, or the hash-mismatch
# guard (which now refuses on ANY unequal hash, explained or not) rejects them.
_EMPTY_DIFF_SHA256 = hashlib.sha256(b"").hexdigest()


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("x = 1\n")
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "initial"], repo)
    return repo


def _write_run(run_dir: Path, provenance: dict[str, object] | None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"n_trials": 3}
    if provenance is not None:
        payload["provenance"] = provenance
    (run_dir / "config.json").write_text(json.dumps(payload))


def test_identical_provenance_exits_zero(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    provenance = {
        "git_commit": commit,
        "git_branch": "master",
        "git_dirty": False,
        "git_diff_files": [],
        "git_diff_sha256": "e" * 64,
        "git_unavailable": False,
    }
    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    _write_run(run_a, provenance)
    _write_run(run_b, provenance)

    exit_code = main([str(run_a), str(run_b), "--repo-root", str(repo)])

    assert exit_code == 0


def test_missing_provenance_directory_exits_2(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    _write_run(run_a, None)  # legacy run: no provenance key at all
    _write_run(run_b, None)

    exit_code = main([str(run_a), str(run_b), "--repo-root", str(repo)])

    assert exit_code == 2


def test_nonexistent_run_directory_exits_2(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_a = tmp_path / "does-not-exist"
    run_b = tmp_path / "run-b"
    _write_run(run_b, {"git_commit": "a" * 40, "git_unavailable": False})

    exit_code = main([str(run_a), str(run_b), "--repo-root", str(repo)])

    assert exit_code == 2


def test_provenance_mixed_run_exits_2(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    base_provenance = {
        "git_commit": commit,
        "git_dirty": False,
        "git_diff_files": [],
        "git_diff_sha256": "e" * 64,
        "git_unavailable": False,
    }
    mixed_provenance = dict(base_provenance, provenance_mixed=True)

    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    _write_run(run_a, mixed_provenance)
    _write_run(run_b, base_provenance)

    exit_code = main([str(run_a), str(run_b), "--repo-root", str(repo)])

    assert exit_code == 2


def test_source_diff_between_commits_exits_1(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    commit_a = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    (repo / "src" / "main.py").write_text("x = 2\n")
    _git(["commit", "-am", "one-line source change"], repo)
    commit_b = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    _write_run(
        run_a,
        {
            "git_commit": commit_a,
            "git_dirty": False,
            "git_diff_files": [],
            "git_diff_sha256": "e" * 64,
            "git_unavailable": False,
        },
    )
    _write_run(
        run_b,
        {
            "git_commit": commit_b,
            "git_dirty": False,
            "git_diff_files": [],
            "git_diff_sha256": "e" * 64,
            "git_unavailable": False,
        },
    )

    exit_code = main([str(run_a), str(run_b), "--repo-root", str(repo)])

    assert exit_code == 1


def test_live_flag_compares_against_current_checkout(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    run_a = tmp_path / "run-a"
    _write_run(
        run_a,
        {
            "git_commit": commit,
            "git_dirty": False,
            "git_diff_files": [],
            "git_diff_sha256": _EMPTY_DIFF_SHA256,
            "git_unavailable": False,
        },
    )

    exit_code = main([str(run_a), "--live", "--repo-root", str(repo)])

    assert exit_code == 0


def test_live_and_run_b_together_is_a_usage_error(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    _write_run(run_a, None)
    _write_run(run_b, None)

    with pytest.raises(SystemExit):
        main([str(run_a), str(run_b), "--live", "--repo-root", str(repo)])


def test_missing_run_b_without_live_is_a_usage_error(tmp_path: Path) -> None:
    run_a = tmp_path / "run-a"
    _write_run(run_a, None)

    with pytest.raises(SystemExit):
        main([str(run_a)])
