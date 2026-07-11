"""Map doc model + member resolution (maze/map.py)."""

from labrat.maze.document import ScentDoc, Section
from labrat.maze.map import (
    build_map_doc,
    map_prompts,
    resolve_members,
    scent_members,
    trail_members,
)
from labrat.maze.store import MazeStore


def test_build_and_read_map_doc():
    doc = build_map_doc(
        "revenue",
        scent=["subscriptions", "invoices"],
        trails=["compute-mrr"],
        prompts=["What's our ARR?"],
    )
    assert doc.kind == "map" and doc.domain == "revenue"
    assert scent_members(doc) == ["subscriptions", "invoices"]
    assert trail_members(doc) == ["compute-mrr"]
    assert map_prompts(doc) == ["What's our ARR?"]


def test_resolve_members_soft_miss(tmp_path):
    store = MazeStore(project_root=tmp_path, home=tmp_path / "h", profile="default")
    # a real scent domain + a real trail exist; the map references one missing of each
    store.write_doc(
        ScentDoc(
            domain="subscriptions",
            kind="scent",
            sections=[Section(heading="Quick Reference", body="subs")],
        ),
        kind="scent",
    )
    store.write_doc(
        ScentDoc(
            domain="compute-mrr",
            kind="trail",
            sections=[Section(heading="When to use", body="mrr")],
        ),
        kind="trail",
    )
    m = build_map_doc(
        "revenue",
        scent=["subscriptions", "gone_domain"],
        trails=["compute-mrr", "gone_trail"],
        prompts=[],
    )
    resolved = resolve_members([m], store)
    assert set(resolved.scent) == {"subscriptions"}
    assert set(resolved.trails) == {"compute-mrr"}
    assert set(resolved.misses) == {"gone_domain", "gone_trail"}


def test_resolve_members_union_across_maps(tmp_path):
    store = MazeStore(project_root=tmp_path, home=tmp_path / "h", profile="default")
    for d in ("subscriptions", "events"):
        store.write_doc(
            ScentDoc(domain=d, kind="scent", sections=[Section(heading="Quick Reference", body=d)]),
            kind="scent",
        )
    m1 = build_map_doc("revenue", scent=["subscriptions"], trails=[], prompts=[])
    m2 = build_map_doc("product", scent=["events"], trails=[], prompts=[])
    resolved = resolve_members([m1, m2], store)
    assert set(resolved.scent) == {"subscriptions", "events"}


def test_resolve_cross_namespace_same_slug_not_dropped(tmp_path):
    store = MazeStore(project_root=tmp_path, home=tmp_path / "h", profile="default")
    # A trail doc "shared" exists, but no scent doc "shared" exists. A shared
    # miss-tracking dict across the scent/trail loops would record the scent miss
    # first and then wrongly skip the trail lookup for the same slug — dropping an
    # existing trail doc. Per-kind miss tracking must keep them independent.
    store.write_doc(
        ScentDoc(
            domain="shared",
            kind="trail",
            sections=[Section(heading="When to use", body="shared trail")],
        ),
        kind="trail",
    )
    m = build_map_doc("bundle", scent=["shared"], trails=["shared"], prompts=[])
    resolved = resolve_members([m], store)
    assert resolved.trails == ["shared"]
    assert resolved.scent == []
    assert "shared" in resolved.misses

    # Mirror case: a scent doc "foo" exists, no trail doc "foo" exists.
    store2 = MazeStore(project_root=tmp_path / "p2", home=tmp_path / "h2", profile="default")
    store2.write_doc(
        ScentDoc(
            domain="foo",
            kind="scent",
            sections=[Section(heading="Quick Reference", body="foo scent")],
        ),
        kind="scent",
    )
    m2 = build_map_doc("bundle2", scent=["foo"], trails=["foo"], prompts=[])
    resolved2 = resolve_members([m2], store2)
    assert resolved2.scent == ["foo"]
    assert resolved2.trails == []
    assert "foo" in resolved2.misses
