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
