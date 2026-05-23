"""Tests for HTML export of findings (M20)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from labrat.audit.export import export_findings
from labrat.thread.model import Finding


def _finding(
    *,
    fid: str = "f1",
    question: str = "how many orders?",
    sql: str = "SELECT COUNT(*) FROM orders",
    note: str = "baseline count",
    chart_spec: dict | None = None,
) -> Finding:
    return Finding(
        id=fid,
        version_id="v1",
        question=question,
        sql=sql,
        results_ref=None,
        chart_spec=chart_spec,
        note=note,
        pinned_at=datetime.now(tz=UTC),
    )


def test_export_produces_html_file(tmp_path: Path) -> None:
    """export_findings writes an HTML file and returns its path."""
    findings = [_finding()]
    out = export_findings(findings, output_dir=tmp_path)
    assert out.exists()
    assert out.suffix == ".html"


def test_export_html_contains_question(tmp_path: Path) -> None:
    """The exported HTML includes each finding's question text."""
    findings = [_finding(question="revenue by month?")]
    out = export_findings(findings, output_dir=tmp_path)
    html = out.read_text()
    assert "revenue by month?" in html


def test_export_html_contains_sql(tmp_path: Path) -> None:
    """The exported HTML includes the SQL query."""
    sql = "SELECT DATE_TRUNC('month', ts), SUM(amount) FROM sales GROUP BY 1"
    findings = [_finding(sql=sql)]
    out = export_findings(findings, output_dir=tmp_path)
    html = out.read_text()
    assert "SELECT" in html
    assert "sales" in html


def test_export_html_contains_note(tmp_path: Path) -> None:
    """The exported HTML includes the user's note."""
    findings = [_finding(note="important baseline measurement")]
    out = export_findings(findings, output_dir=tmp_path)
    html = out.read_text()
    assert "important baseline measurement" in html


def test_export_5_findings(tmp_path: Path) -> None:
    """A 5-finding session produces one report with all 5 questions."""
    questions = [f"question {i}" for i in range(5)]
    findings = [_finding(fid=f"f{i}", question=q) for i, q in enumerate(questions)]
    out = export_findings(findings, output_dir=tmp_path)
    html = out.read_text()
    for q in questions:
        assert q in html


def test_export_self_contained_no_external_assets(tmp_path: Path) -> None:
    """The exported HTML does not reference external CDN URLs."""
    findings = [_finding()]
    out = export_findings(findings, output_dir=tmp_path)
    html = out.read_text()
    # No CDN links to external servers
    for cdn in ["cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com", "googleapis.com"]:
        assert cdn not in html


def test_export_empty_findings(tmp_path: Path) -> None:
    """Exporting an empty list produces a valid (empty) report."""
    out = export_findings([], output_dir=tmp_path)
    html = out.read_text()
    assert "<html" in html.lower()


def test_export_includes_labrat_branding(tmp_path: Path) -> None:
    """The report mentions LabRat as the generating tool."""
    out = export_findings([_finding()], output_dir=tmp_path)
    html = out.read_text()
    assert "labrat" in html.lower() or "LabRat" in html
