"""Tests for git-based DAB run provenance capture.

A DAB run's config.json must record the code state it executed from — not just
its flags — so that two runs' comparability is decidable later without
guessing from file timestamps. See src/labrat/eval/benchmarks/dab/provenance.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from labrat.eval.benchmarks.dab.provenance import capture_git_provenance


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


def test_capture_on_clean_repo_records_commit_and_not_dirty(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    provenance = capture_git_provenance(repo_root=repo)

    assert provenance["git_commit"] == expected_commit
    assert provenance["git_dirty"] is False
    assert provenance["git_diff_files"] == []
    assert provenance["git_unavailable"] is False


def test_capture_on_dirty_tracked_file_records_dirty_and_changed_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "main.py").write_text("x = 2\n")

    provenance = capture_git_provenance(repo_root=repo)

    assert provenance["git_dirty"] is True
    assert provenance["git_diff_files"] == ["src/main.py"]
    assert provenance["git_diff_sha256"]


def test_capture_on_untracked_new_file_records_dirty_and_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "src" / "new_module.py").write_text("y = 1\n")

    provenance = capture_git_provenance(repo_root=repo)

    assert provenance["git_dirty"] is True
    assert "src/new_module.py" in provenance["git_diff_files"]


def test_capture_distinguishes_different_untracked_content(tmp_path: Path) -> None:
    """Two dirty states that touch the same untracked path with different content
    must not hash identically — otherwise a comparability check could wrongly pass."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    repo_a = _init_repo(tmp_path / "a")
    (repo_a / "src" / "scratch.py").write_text("value = 1\n")
    prov_a = capture_git_provenance(repo_root=repo_a)

    repo_b = _init_repo(tmp_path / "b")
    (repo_b / "src" / "scratch.py").write_text("value = 2\n")
    prov_b = capture_git_provenance(repo_root=repo_b)

    assert prov_a["git_diff_sha256"] != prov_b["git_diff_sha256"]


def test_capture_outside_git_repo_reports_unavailable_without_raising(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    provenance = capture_git_provenance(repo_root=not_a_repo)

    assert provenance["git_unavailable"] is True
    assert provenance["git_commit"] is None
