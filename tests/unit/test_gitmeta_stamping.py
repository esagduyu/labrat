"""git_sha provenance on derived Scent writes; None-safe everywhere."""

import subprocess
from pathlib import Path

from labrat.maze.document import Section
from labrat.maze.gitmeta import current_git_sha
from labrat.maze.harvest import apply_approved_sections
from labrat.maze.semantic_ingest import ingest_dbt_semantics
from labrat.maze.store import MazeStore

_MANIFEST_FIXTURE = Path("tests/fixtures/sample_dbt_project/manifest_semantic.json")


def _git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "f").write_text("x")
    subprocess.run(["git", "add", "f"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    return root


def test_current_git_sha_in_repo_and_out(tmp_path: Path) -> None:
    root = _git_repo(tmp_path)
    sha = current_git_sha(root)
    assert sha is not None and 6 <= len(sha) <= 12
    assert current_git_sha(tmp_path / "not-a-repo") is None


def test_apply_stamps_when_root_given(tmp_path: Path) -> None:
    root = _git_repo(tmp_path)
    store = MazeStore(project_root=root, home=tmp_path / "home", profile="p1")
    apply_approved_sections(
        store,
        "orders",
        [Section(heading="Gotchas", body="- keep", source="harvested")],
        git_root=root,
    )
    doc = store.load_domain("orders", scope="project")
    assert doc is not None and doc.sections[0].git_sha == current_git_sha(root)


def test_apply_without_root_byte_identical(tmp_path: Path) -> None:
    store = MazeStore(project_root=tmp_path / "p", home=tmp_path / "h", profile="p1")
    apply_approved_sections(
        store, "orders", [Section(heading="Gotchas", body="- b", source="harvested")]
    )
    doc = store.load_domain("orders", scope="project")
    assert doc is not None and doc.sections[0].git_sha is None


def test_reapply_keeps_existing_stamp(tmp_path: Path) -> None:
    root = _git_repo(tmp_path)
    store = MazeStore(project_root=root, home=tmp_path / "home", profile="p1")
    sec = [Section(heading="Gotchas", body="- same", source="harvested")]
    apply_approved_sections(store, "orders", sec, git_root=root)
    doc = store.load_domain("orders", scope="project")
    assert doc is not None
    first = doc.sections[0].git_sha
    # new commit, re-apply same body → dedup keeps the ORIGINAL stamp
    (root / "g").write_text("y")
    subprocess.run(["git", "add", "g"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "two"],
        cwd=root,
        check=True,
    )
    apply_approved_sections(store, "orders", sec, git_root=root)
    doc = store.load_domain("orders", scope="project")
    assert doc is not None
    assert len(doc.sections) == 1 and doc.sections[0].git_sha == first


def _semantic_store(tmp_path: Path) -> tuple[MazeStore, Path]:
    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")
    return store, tmp_path / "proj" / "labrat_maze" / "scent"


def test_ingest_stamps_when_root_given(tmp_path: Path) -> None:
    root = _git_repo(tmp_path)
    store, scent_dir = _semantic_store(tmp_path)
    outcome = ingest_dbt_semantics(
        manifest_path=_MANIFEST_FIXTURE,
        catalog=None,
        store=store,
        project_scent_dir=scent_dir,
        git_root=root,
    )
    assert not outcome.skipped
    sha = current_git_sha(root)
    for domain in outcome.domains:
        doc = store.load_domain(domain, scope="project")
        assert doc is not None
        semantic_sections = [s for s in doc.sections if s.source == "semantic_layer"]
        assert semantic_sections
        for s in semantic_sections:
            assert s.git_sha == sha


def test_ingest_without_root_byte_identical(tmp_path: Path) -> None:
    store, scent_dir = _semantic_store(tmp_path)
    outcome = ingest_dbt_semantics(
        manifest_path=_MANIFEST_FIXTURE,
        catalog=None,
        store=store,
        project_scent_dir=scent_dir,
    )
    assert not outcome.skipped
    for domain in outcome.domains:
        doc = store.load_domain(domain, scope="project")
        assert doc is not None
        semantic_sections = [s for s in doc.sections if s.source == "semantic_layer"]
        assert semantic_sections
        for s in semantic_sections:
            assert s.git_sha is None
