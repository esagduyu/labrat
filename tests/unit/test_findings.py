"""Tests for Findings pin/unpin lifecycle and persistence (M18)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.thread.findings import FindingsManager


@pytest.fixture()
def mgr(tmp_path: Path) -> FindingsManager:
    """FindingsManager backed by a temporary directory."""
    return FindingsManager(store_dir=tmp_path)


def _pin(mgr: FindingsManager, *, version_id: str = "v1", note: str = "") -> str:
    """Helper: pin a finding and return its ID."""
    f = mgr.pin(
        version_id=version_id,
        question="how many orders?",
        sql="SELECT COUNT(*) FROM orders",
        results_ref=None,
        chart_spec=None,
        note=note,
    )
    return f.id


def test_pin_creates_finding(mgr: FindingsManager) -> None:
    """pin() returns a Finding with the given fields."""
    f = mgr.pin(
        version_id="v1",
        question="how many rows?",
        sql="SELECT COUNT(*) FROM t",
        results_ref=None,
        chart_spec=None,
        note="baseline",
    )
    assert f.version_id == "v1"
    assert f.question == "how many rows?"
    assert f.note == "baseline"
    assert f.id  # non-empty


def test_list_findings_empty(mgr: FindingsManager) -> None:
    """list_findings returns [] when nothing is pinned."""
    assert mgr.list_findings() == []


def test_list_findings_after_pin(mgr: FindingsManager) -> None:
    """list_findings returns all pinned findings."""
    _pin(mgr, version_id="v1")
    _pin(mgr, version_id="v2")
    findings = mgr.list_findings()
    assert len(findings) == 2


def test_unpin_removes_finding(mgr: FindingsManager) -> None:
    """unpin() removes the finding by ID."""
    fid = _pin(mgr)
    mgr.unpin(fid)
    assert mgr.list_findings() == []


def test_unpin_nonexistent_is_no_op(mgr: FindingsManager) -> None:
    """unpin() on an unknown ID does not raise."""
    mgr.unpin("does-not-exist")


def test_re_pin_after_unpin(mgr: FindingsManager) -> None:
    """Pin → unpin → pin again results in one finding."""
    fid = _pin(mgr)
    mgr.unpin(fid)
    _pin(mgr, version_id="v1")
    assert len(mgr.list_findings()) == 1


def test_update_note(mgr: FindingsManager) -> None:
    """update_note() changes the note on an existing finding."""
    fid = _pin(mgr, note="original note")
    mgr.update_note(fid, "updated note")
    findings = mgr.list_findings()
    assert findings[0].note == "updated note"


def test_reorder_findings(mgr: FindingsManager) -> None:
    """reorder() changes the position of a finding in the list."""
    _pin(mgr, version_id="v1")
    _pin(mgr, version_id="v2")
    id3 = _pin(mgr, version_id="v3")
    # Move id3 to position 0
    mgr.reorder(id3, new_index=0)
    ordered = [f.id for f in mgr.list_findings()]
    assert ordered[0] == id3


def test_persistence_survives_reload(tmp_path: Path) -> None:
    """Findings written by one manager are readable by another."""
    mgr1 = FindingsManager(store_dir=tmp_path)
    fid = _pin(mgr1, note="persistent")
    mgr2 = FindingsManager(store_dir=tmp_path)
    findings = mgr2.list_findings()
    assert any(f.id == fid for f in findings)
    assert findings[0].note == "persistent"


def test_finding_carries_standalone_info(mgr: FindingsManager) -> None:
    """A finding has all info needed to be exported standalone."""
    f = mgr.pin(
        version_id="v1",
        question="revenue by month?",
        sql="SELECT DATE_TRUNC('month', ts), SUM(amount) FROM sales GROUP BY 1",
        results_ref="/tmp/result.parquet",
        chart_spec={"type": "bar"},
        note="Q1 analysis",
    )
    assert f.question
    assert f.sql
    assert f.results_ref == "/tmp/result.parquet"
    assert f.chart_spec == {"type": "bar"}
    assert f.pinned_at
