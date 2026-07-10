"""export_cheese: capture-store resolution, honest degradation, version write."""

from datetime import UTC, datetime

import polars as pl

from labrat.cheese.export import export_cheese
from labrat.cheese.store import CheeseStore, FindingDataStore
from labrat.thread.model import Finding


def _finding(fid: str, ref: str | None) -> Finding:
    return Finding(
        id=fid,
        version_id="v",
        question=f"Q-{fid}",
        sql="select 1",
        results_ref=ref,
        chart_spec=None,
        note="",
        pinned_at=datetime.now(tz=UTC),
    )


def test_export_embeds_captured_rows_and_chart(tmp_path):
    ds = FindingDataStore(tmp_path / "data")
    cs = CheeseStore(tmp_path / "cheese")
    ref = ds.capture("f1", pl.DataFrame({"region": ["EMEA"], "n": [7]}), chart_png=b"PNGBYTES")
    path = export_cheese(
        [_finding("f1", ref)], kind="single", title="T", cheese_store=cs, data_store=ds
    )
    html = path.read_text()
    assert "EMEA" in html and "data:image/png;base64," in html
    assert path.name == "v1.html"


def test_export_degrades_honestly_on_missing_ref(tmp_path):
    ds = FindingDataStore(tmp_path / "data")
    cs = CheeseStore(tmp_path / "cheese")
    for ref in (None, "result://old-session/0004", "cheese://gone"):
        path = export_cheese(
            [_finding("fx", ref)], kind="single", title="T", cheese_store=cs, data_store=ds
        )
        assert "Results unavailable for this finding." in path.read_text()


def test_reexport_same_set_bumps_version(tmp_path):
    ds = FindingDataStore(tmp_path / "data")
    cs = CheeseStore(tmp_path / "cheese")
    f = [_finding("f1", None)]
    p1 = export_cheese(f, kind="single", title="T", cheese_store=cs, data_store=ds)
    p2 = export_cheese(f, kind="single", title="T", cheese_store=cs, data_store=ds)
    assert (p1.name, p2.name) == ("v1.html", "v2.html")
    assert p1.parent == p2.parent


def test_rows_mode_none_passthrough(tmp_path):
    ds = FindingDataStore(tmp_path / "data")
    cs = CheeseStore(tmp_path / "cheese")
    ref = ds.capture("f1", pl.DataFrame({"secret": ["s3cr3t"]}))
    path = export_cheese(
        [_finding("f1", ref)],
        kind="single",
        title="T",
        rows_mode="none",
        cheese_store=cs,
        data_store=ds,
    )
    html = path.read_text()
    assert "s3cr3t" not in html and "Result rows omitted at export." in html
