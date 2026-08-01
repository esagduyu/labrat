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


def test_source_tree_markdown_is_not_inert(tmp_path: Path) -> None:
    """PLANTED (required plant a, I1): a .md file UNDER src/ (e.g. the agent's own
    system prompt, system_base.md) must classify as affecting, not inert. The
    original plant only ever exercised a .py file the classifier already handled
    correctly; this probes the classifier's actual floor -- suffix-before-location
    would silently certify a prompt-drift commit as harmless."""
    repo = _init_repo(tmp_path)
    (repo / "src" / "labrat" / "agent" / "prompts").mkdir(parents=True)
    (repo / "src" / "labrat" / "agent" / "prompts" / "system_base.md").write_text(
        "You are a data analysis agent.\n"
    )
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "add system prompt"], repo)
    prov_a = capture_git_provenance(repo_root=repo)

    (repo / "src" / "labrat" / "agent" / "prompts" / "system_base.md").write_text(
        "You are a data analysis agent. Always guess if unsure.\n"
    )
    _git(["commit", "-aqm", "edit system prompt"], repo)
    prov_b = capture_git_provenance(repo_root=repo)

    result = check_comparability(prov_a, prov_b, repo_root=repo)

    assert result.comparable is False
    assert result.verdict == "code_diff"
    assert "src/labrat/agent/prompts/system_base.md" in result.differing_files


def test_root_level_markdown_outside_src_stays_inert(tmp_path: Path) -> None:
    """Sanity check for the I1 fix: it must not become "no .md is ever inert" --
    a root-level doc like decisions.md (not under src/ or scripts/) stays inert."""
    repo = _init_repo(tmp_path)
    (repo / "decisions.md").write_text("- decided X\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "add decisions.md"], repo)
    prov_a = capture_git_provenance(repo_root=repo)

    (repo / "decisions.md").write_text("- decided X\n- decided Y\n")
    _git(["commit", "-aqm", "update decisions.md"], repo)
    prov_b = capture_git_provenance(repo_root=repo)

    result = check_comparability(prov_a, prov_b, repo_root=repo)

    assert result.comparable is True


def test_hash_mismatch_with_no_explaining_files_refuses(tmp_path: Path) -> None:
    """PLANTED (required plant c, C1 second half): a diff-hash mismatch with an
    empty file-list union (a degraded/failed capture, or a hand-built record)
    must refuse -- it must NOT fall through to "no changed paths -> comparable".
    The hash inequality is direct evidence of a difference even when it cannot
    be named."""
    repo = _init_repo(tmp_path)
    prov_a = capture_git_provenance(repo_root=repo)
    prov_b = dict(prov_a)
    prov_b["git_diff_sha256"] = "f" * 64  # different hash, but no file list explains it
    prov_b["git_diff_files"] = []

    result = check_comparability(prov_a, prov_b, repo_root=repo)

    assert result.comparable is False
    assert result.verdict != "comparable"


def test_provenance_mixed_refuses_even_at_matching_commit(tmp_path: Path) -> None:
    """PLANTED (required plant d, I2): a run whose config.json was flagged
    provenance_mixed (it spans more than one code state across resumes/retries)
    must refuse, even when compared against itself at the same recorded commit
    -- the mixed flag means its trials.jsonl is not attributable to one commit,
    so no comparison involving it can be certified."""
    repo = _init_repo(tmp_path)
    prov = capture_git_provenance(repo_root=repo)
    mixed = dict(prov)
    mixed["provenance_mixed"] = True
    mixed["provenance_history"] = [dict(prov, git_commit="0" * 40)]

    result = check_comparability(mixed, dict(prov), repo_root=repo)

    assert result.comparable is False
    assert result.verdict == "provenance_mixed"


def test_comparable_message_names_the_labrat_checkout_scope(tmp_path: Path) -> None:
    """I4: the pass message must not overclaim "identical code state" without
    scoping it -- a DAB run's behavior also depends on ~/repos/DataAgentBench
    (hints, validate.py, ground truth), which this checker does not inspect."""
    repo = _init_repo(tmp_path)
    prov_a = capture_git_provenance(repo_root=repo)
    prov_b = capture_git_provenance(repo_root=repo)

    result = check_comparability(prov_a, prov_b, repo_root=repo)

    assert result.comparable is True
    assert "identical code state" not in result.reason
    assert "labrat" in result.reason.lower()
