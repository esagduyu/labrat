"""Tests for the Maze dual store + precedence (FEATURE_ROADMAP #26a)."""

from __future__ import annotations

from pathlib import Path

from labrat.maze.store import MazeStore


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_reads_both_layers_and_tags_scope(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    home = tmp_path / "home"
    _write(project / "labrat_maze" / "scent" / "sales.md", "---\ndomain: sales\n---\n## A\nx")
    _write(
        home / ".labrat" / "maze" / "acme" / "scent" / "events.md",
        "---\ndomain: events\n---\n## B\ny",
    )

    docs = MazeStore(project_root=project, home=home, profile="acme").docs()
    by_domain = {d.domain: d for d in docs}
    assert set(by_domain) == {"sales", "events"}
    assert by_domain["sales"].scope == "project"
    assert by_domain["events"].scope == "user"


def test_domain_conflict_unions_sections_user_first(tmp_path: Path) -> None:
    # v2: whole-doc "project wins" precedence replaced by per-section UNION
    # (user layer first, project second; scope becomes "merged" when both contribute).
    project = tmp_path / "proj"
    home = tmp_path / "home"
    _write(
        project / "labrat_maze" / "scent" / "sales.md",
        "---\ndomain: sales\n---\n## P\nproject body",
    )
    _write(
        home / ".labrat" / "maze" / "acme" / "scent" / "sales.md",
        "---\ndomain: sales\n---\n## U\nuser body",
    )

    docs = MazeStore(project_root=project, home=home, profile="acme").docs()
    assert len(docs) == 1
    assert docs[0].scope == "merged"
    assert [s.heading for s in docs[0].sections] == ["U", "P"]


def test_missing_dirs_yield_empty(tmp_path: Path) -> None:
    docs = MazeStore(project_root=tmp_path / "nope", home=tmp_path / "alsonope", profile="x").docs()
    assert docs == []


def test_from_env_uses_labrat_maze_dir(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _write(tmp_path / "labrat_maze" / "scent" / "s.md", "---\ndomain: s\n---\n## A\nx")
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    docs = MazeStore.from_env(profile="default").docs()
    assert [d.domain for d in docs] == ["s"]
