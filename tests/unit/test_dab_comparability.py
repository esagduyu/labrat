"""Tests for the DAB run-comparability guard.

Two completed DAB run directories are "comparable" only if the code that
produced their numbers was identical (or differs only in ways that provably
cannot affect run behaviour, e.g. doc-only edits). The check must REFUSE to
certify comparability when provenance is missing from either side — it must
never read silence as equality. See
src/labrat/eval/benchmarks/dab/provenance.py::check_comparability.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from labrat.eval.benchmarks.dab.provenance import capture_git_provenance, check_comparability


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
    (repo / "docs").mkdir()
    (repo / "docs" / "notes.md").write_text("hello\n")
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "initial"], repo)
    return repo


def test_identical_clean_commit_is_comparable(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    prov_a = capture_git_provenance(repo_root=repo)
    prov_b = capture_git_provenance(repo_root=repo)

    result = check_comparability(prov_a, prov_b, repo_root=repo)

    assert result.comparable is True
    assert result.verdict == "comparable"


def test_missing_provenance_on_one_side_refuses_to_certify(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    prov_a = capture_git_provenance(repo_root=repo)

    result = check_comparability(prov_a, None, repo_root=repo, label_b="baseline run")

    assert result.comparable is False
    assert result.verdict == "provenance_missing"
    assert "baseline run" in result.reason


def test_missing_provenance_on_both_sides_refuses_to_certify(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    result = check_comparability(None, None, repo_root=repo)

    assert result.comparable is False
    assert result.verdict == "provenance_missing"


def test_empty_dict_provenance_is_treated_as_missing(tmp_path: Path) -> None:
    """A legacy config.json with no 'provenance' key at all decodes to an empty
    dict, not None — the check must treat that as missing too, not as a silent pass."""
    repo = _init_repo(tmp_path)
    prov_a = capture_git_provenance(repo_root=repo)

    result = check_comparability(prov_a, {}, repo_root=repo)

    assert result.comparable is False
    assert result.verdict == "provenance_missing"


def test_same_commit_different_dirty_diffs_is_not_comparable(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    prov_a = capture_git_provenance(repo_root=repo)

    (repo / "src" / "main.py").write_text("x = 999\n")
    prov_b = capture_git_provenance(repo_root=repo)
    # revert so repo stays clean for any later capture in this test
    (repo / "src" / "main.py").write_text("x = 1\n")

    result = check_comparability(prov_a, prov_b, repo_root=repo)

    assert result.comparable is False
    assert result.verdict == "code_diff"
    assert "src/main.py" in result.differing_files


def test_different_commits_touching_only_docs_is_comparable(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    prov_a = capture_git_provenance(repo_root=repo)

    (repo / "docs" / "notes.md").write_text("updated notes\n")
    _git(["commit", "-am", "docs only"], repo)
    prov_b = capture_git_provenance(repo_root=repo)

    result = check_comparability(prov_a, prov_b, repo_root=repo)

    assert result.comparable is True
    assert result.verdict == "comparable"


def test_different_commits_touching_source_is_not_comparable(tmp_path: Path) -> None:
    """The minimum planted shape this guard exists to catch: a single commit
    changing one line of one source file between baseline and comparand."""
    repo = _init_repo(tmp_path)
    prov_a = capture_git_provenance(repo_root=repo)

    (repo / "src" / "main.py").write_text("x = 2\n")
    _git(["commit", "-am", "one-line source change"], repo)
    prov_b = capture_git_provenance(repo_root=repo)

    result = check_comparability(prov_a, prov_b, repo_root=repo)

    assert result.comparable is False
    assert result.verdict == "code_diff"
    assert result.differing_files == ["src/main.py"]


def test_unresolvable_commit_blocks_instead_of_passing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    prov_a = capture_git_provenance(repo_root=repo)
    prov_b = dict(prov_a)
    prov_b["git_commit"] = "0" * 40  # well-formed sha that does not exist in this repo

    result = check_comparability(prov_a, prov_b, repo_root=repo)

    assert result.comparable is False
    assert result.verdict != "comparable"
