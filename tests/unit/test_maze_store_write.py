from __future__ import annotations

from pathlib import Path

import pytest

from labrat.maze.document import ScentDoc, Section
from labrat.maze.store import MazeStore


def _store(tmp_path: Path) -> MazeStore:
    return MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="default")


def test_write_doc_round_trips_through_docs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = ScentDoc(
        domain="sales",
        tables=["orders"],
        sections=[Section(heading="Gotchas", body="- Soft deletes.", source="harvested")],
    )
    path = store.write_doc(doc)
    assert path.exists()
    loaded = store.load_domain("sales")
    assert loaded is not None
    assert any("Soft deletes." in s.body and s.source == "harvested" for s in loaded.sections)


def test_load_domain_missing_returns_none(tmp_path: Path) -> None:
    assert _store(tmp_path).load_domain("nope") is None


def test_write_doc_rejects_mismatched_kind(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = ScentDoc(domain="x", kind="trail")
    with pytest.raises(ValueError):
        store.write_doc(doc)  # default kind="scent" != doc.kind="trail"
