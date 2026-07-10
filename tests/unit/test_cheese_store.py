"""FindingDataStore (pin-time capture) + CheeseStore (versioned artifacts)."""

import polars as pl
import pytest

from labrat.cheese.store import CheeseStore, FindingDataStore


def test_capture_bounds_to_50_rows_and_reports_total(tmp_path):
    ds = FindingDataStore(tmp_path)
    df = pl.DataFrame({"x": list(range(120))})
    ref = ds.capture("f1", df)
    assert ref == "cheese://f1"
    loaded = ds.load(ref)
    assert loaded is not None
    bounded, total = loaded
    assert bounded.height == 50
    assert total == 120


def test_load_foreign_or_missing_ref_is_none(tmp_path):
    ds = FindingDataStore(tmp_path)
    assert ds.load("result://abc/0001") is None
    assert ds.load("cheese://nope") is None


def test_chart_png_roundtrip(tmp_path):
    ds = FindingDataStore(tmp_path)
    ds.capture("f1", pl.DataFrame({"x": [1]}), chart_png=b"\x89PNG-fake")
    assert ds.load_chart_png("cheese://f1") == b"\x89PNG-fake"
    ds.capture("f2", pl.DataFrame({"x": [1]}))
    assert ds.load_chart_png("cheese://f2") is None


def test_version_lifecycle_linear_and_immutable(tmp_path):
    cs = CheeseStore(tmp_path)
    m = cs.create_or_get("single", ["f1"], "My insight")
    p1 = cs.add_version(m.cheese_id, "<html>v1</html>", "preview")
    v1_bytes = p1.read_bytes()
    p2 = cs.add_version(m.cheese_id, "<html>v2</html>", "none")
    m2 = cs.get(m.cheese_id)
    assert m2 is not None
    assert [v.n for v in m2.versions] == [1, 2]
    assert m2.current == 2
    assert p1.read_bytes() == v1_bytes  # immutability
    assert p2.name == "v2.html"


def test_rollback_then_iterate_continues_linearly(tmp_path):
    cs = CheeseStore(tmp_path)
    m = cs.create_or_get("single", ["f1"], "t")
    cs.add_version(m.cheese_id, "a", "preview")
    cs.add_version(m.cheese_id, "b", "preview")
    cs.rollback(m.cheese_id, 1)
    got = cs.get(m.cheese_id)
    assert got is not None and got.current == 1
    cs.add_version(m.cheese_id, "c", "preview")
    got = cs.get(m.cheese_id)
    assert got is not None and got.current == 3 and len(got.versions) == 3


def test_rollback_validates_range(tmp_path):
    cs = CheeseStore(tmp_path)
    m = cs.create_or_get("single", ["f1"], "t")
    cs.add_version(m.cheese_id, "a", "preview")
    with pytest.raises(ValueError):
        cs.rollback(m.cheese_id, 2)


def test_capture_rejects_traversal_finding_id(tmp_path):
    ds = FindingDataStore(tmp_path)
    with pytest.raises(ValueError):
        ds.capture("../evil", pl.DataFrame({"x": [1]}))
    assert not (tmp_path.parent / "evil.parquet").exists()
    assert not (tmp_path.parent / "evil.meta.json").exists()
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_load_corrupt_meta_json_returns_none(tmp_path):
    ds = FindingDataStore(tmp_path)
    ds.capture("f1", pl.DataFrame({"x": [1]}))
    (tmp_path / "f1.meta.json").write_text("{not valid json", encoding="utf-8")
    assert ds.load("cheese://f1") is None


def test_list_cheeses_skips_corrupt_manifest_and_lists_valid(tmp_path):
    cs = CheeseStore(tmp_path)
    good = cs.create_or_get("single", ["f1"], "good")
    bad_dir = tmp_path / "corrupt123"
    bad_dir.mkdir()
    (bad_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")
    listed = cs.list_cheeses()
    assert [m.cheese_id for m in listed] == [good.cheese_id]


def test_identity_same_set_same_cheese_different_set_new(tmp_path):
    cs = CheeseStore(tmp_path)
    a = cs.create_or_get("single", ["f1"], "t")
    b = cs.create_or_get("single", ["f1"], "different title ignored")
    c = cs.create_or_get("report", ["f1"], "t")
    d = cs.create_or_get("single", ["f1", "f2"], "t")
    assert a.cheese_id == b.cheese_id
    assert b.title == "t"  # existing manifest not overwritten
    assert len({a.cheese_id, c.cheese_id, d.cheese_id}) == 3
