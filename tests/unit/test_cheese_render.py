"""Cheese renderer: single/report, trust block, bounded table, escaping."""

from datetime import UTC, datetime

from labrat.cheese.model import FindingProvenance, ScentSourceRef
from labrat.cheese.render import FindingRender, render_cheese


def _fr(**overrides: object) -> FindingRender:
    base: dict[str, object] = {
        "question": "Which region grew fastest?",
        "note": "",
        "sql": "SELECT region FROM t",
        "provenance": None,
        "chart_png_b64": None,
        "table_columns": ["region", "growth"],
        "table_rows": [["EMEA", "12%"]],
        "total_rows": 4,
        "pinned_at": datetime.now(tz=UTC),
    }
    base.update(overrides)
    return FindingRender(**base)  # type: ignore[arg-type]


def _render(findings: list[FindingRender], **kw: object) -> str:
    args: dict[str, object] = {
        "title": "T",
        "kind": "single",
        "version_n": 1,
        "exported_at": datetime.now(tz=UTC),
        "rows_mode": "preview",
    }
    args.update(kw)
    return render_cheese(findings, **args)  # type: ignore[arg-type]


def test_attested_trust_block():
    prov = FindingProvenance(
        scent_sources=[ScentSourceRef(domain="orders", tier="semantic_layer", fresh=True)],
        joins_verified=1,
        lineage_used=True,
        verifier_verdict="sufficient",
        run_sql_count=3,
        schema_fingerprint="fp123",
        git_sha="a1b2c3d",
        model_id="claude-sonnet-4-6",
        captured_at=datetime(2026, 7, 10, tzinfo=UTC),
    )
    html = _render([_fr(provenance=prov)])
    for needle in (
        "orders",
        "semantic_layer",
        "fresh",
        "join verified",
        "lineage",
        "sufficient",
        "fp123",
        "a1b2c3d",
        "claude-sonnet-4-6",
    ):
        assert needle in html, needle


def test_unattested_line_and_never_fabricated():
    html = _render([_fr(provenance=None)])
    assert "unattested (pinned before provenance capture)" in html
    assert "semantic_layer" not in html


def test_rows_mode_none_omits_values():
    html = _render([_fr()], rows_mode="none")
    assert "Result rows omitted at export." in html
    assert "EMEA" not in html


def test_preview_table_and_total():
    html = _render([_fr()])
    assert "EMEA" in html and "region" in html
    assert "4" in html  # total row count shown


def test_results_unavailable_when_no_table():
    html = _render([_fr(table_columns=None, table_rows=None, total_rows=None)])
    assert "Results unavailable for this finding." in html


def test_escaping():
    html = _render([_fr(question="<script>alert(1)</script>")])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_chart_data_uri_and_report_numbering():
    html = _render(
        [_fr(chart_png_b64="QUJD"), _fr()],
        kind="report",
        version_n=3,
    )
    assert "data:image/png;base64,QUJD" in html
    assert "Finding 1 of 2" in html and "Finding 2 of 2" in html
    assert "v3" in html


def test_self_contained_and_footer():
    html = _render([_fr()])
    assert "http" not in html.replace("https://github.com/esagduyu/labrat", "")
    assert "Made with LabRat" in html and "https://github.com/esagduyu/labrat" in html
