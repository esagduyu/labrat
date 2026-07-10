"""MazeStore v2: per-section merge-at-read + scoped load_domain (I2 fix foundation)."""

from pathlib import Path

from labrat.maze.document import ScentDoc, Section, render_document
from labrat.maze.store import MazeStore


def _store(tmp_path: Path) -> MazeStore:
    return MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")


def _write(tmp_path: Path, layer: str, doc: ScentDoc) -> None:
    base = (
        tmp_path / "proj" / "labrat_maze" / "scent"
        if layer == "project"
        else tmp_path / "home" / ".labrat" / "maze" / "p1" / "scent"
    )
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{doc.domain}.md").write_text(render_document(doc), encoding="utf-8")


def _cart_doc() -> ScentDoc:
    return ScentDoc(
        domain="orders",
        tables=["orders"],
        sections=[
            Section(heading="Key Tables", body="- orders: 8 rows", source="verified"),
        ],
    )


def _harvest_doc() -> ScentDoc:
    return ScentDoc(
        domain="orders",
        tables=["orders"],
        sections=[
            Section(heading="Gotchas", body="- exclude test orders", source="harvested"),
        ],
    )


def test_single_layer_domain_reads_identically(tmp_path: Path) -> None:
    # Golden regression: a domain present in ONE layer must round-trip exactly as today.
    _write(tmp_path, "user", _cart_doc())
    docs = _store(tmp_path).docs()
    assert len(docs) == 1
    doc = docs[0]
    assert doc.scope == "user"
    assert [s.heading for s in doc.sections] == ["Key Tables"]
    assert doc.sections[0].source == "verified"


def test_colliding_domain_unions_sections_user_first(tmp_path: Path) -> None:
    _write(tmp_path, "user", _cart_doc())
    _write(tmp_path, "project", _harvest_doc())
    docs = _store(tmp_path).docs()
    assert len(docs) == 1
    doc = docs[0]
    assert doc.scope == "merged"
    assert [s.heading for s in doc.sections] == ["Key Tables", "Gotchas"]
    assert [s.source for s in doc.sections] == ["verified", "harvested"]
    assert doc.tables == ["orders"]


def test_duplicate_bodies_dedup_project_copy_absorbed(tmp_path: Path) -> None:
    # Legacy pre-v2 apply copied user sections into the project doc; union must absorb them.
    legacy = _harvest_doc()
    legacy.sections.insert(0, _cart_doc().sections[0].model_copy())
    _write(tmp_path, "user", _cart_doc())
    _write(tmp_path, "project", legacy)
    doc = _store(tmp_path).docs()[0]
    assert [s.heading for s in doc.sections] == ["Key Tables", "Gotchas"]  # no double Key Tables


def test_load_domain_scope_filters(tmp_path: Path) -> None:
    _write(tmp_path, "user", _cart_doc())
    _write(tmp_path, "project", _harvest_doc())
    store = _store(tmp_path)
    assert store.load_domain("orders", scope="user") is not None
    assert store.load_domain("orders", scope="user").sections[0].heading == "Key Tables"
    assert store.load_domain("orders", scope="project").sections[0].heading == "Gotchas"
    assert store.load_domain("orders").scope == "merged"  # default = merged view
    assert store.load_domain("nope", scope="project") is None


def test_i2_scenario_refresh_regeneration_visible_through_merge(tmp_path: Path) -> None:
    # The I2 cross-seam regression: harvest exists project-side; the user-layer doc is
    # regenerated (schema changed) — the merged read must reflect the NEW user content.
    _write(tmp_path, "user", _cart_doc())
    _write(tmp_path, "project", _harvest_doc())
    regenerated = _cart_doc()
    regenerated.sections[0] = Section(
        heading="Key Tables", body="- orders: 9 rows (new col added)", source="verified"
    )
    _write(tmp_path, "user", regenerated)  # simulates M2 refresh rewrite
    doc = _store(tmp_path).docs()[0]
    bodies = [s.body for s in doc.sections]
    assert "- orders: 9 rows (new col added)" in bodies  # fresh content visible
    assert "- orders: 8 rows" not in bodies  # stale copy NOT shadowing
    assert "- exclude test orders" in bodies  # harvested content preserved
