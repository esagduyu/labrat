"""Cheese renderer: one Jinja template for single + report artifacts.

Produces a fully self-contained HTML document — inline CSS, no JS, and the
single external URL is the footer's attribution link. Every value shown in
the trust block comes from a ``FindingProvenance`` captured at pin time;
nothing is inferred or re-derived at render time, and when provenance is
absent the recipient sees an explicit "unattested" line rather than a
fabricated one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from jinja2 import Environment, select_autoescape

from labrat.cheese.model import FindingProvenance

UNATTESTED_LINE = "unattested (pinned before provenance capture)"
ROWS_OMITTED_LINE = "Result rows omitted at export."
RESULTS_UNAVAILABLE_LINE = "Results unavailable for this finding."
FOOTER_TEXT = "Made with LabRat — terminal-native data agent"
FOOTER_URL = "https://github.com/esagduyu/labrat"


@dataclass(frozen=True)
class FindingRender:
    """One finding, flattened to exactly what the template needs to show."""

    question: str
    note: str
    sql: str
    provenance: FindingProvenance | None
    chart_png_b64: str | None
    table_columns: list[str] | None
    table_rows: list[list[str]] | None
    total_rows: int | None
    pinned_at: datetime


def _freshness_label(fresh: bool | None) -> str:
    if fresh is True:
        return "fresh"
    if fresh is False:
        return "stale"
    return "freshness unknown"


def _trust_context(provenance: FindingProvenance | None) -> dict[str, Any] | None:
    """Precompute the trust-block strings from a pin-time provenance snapshot.

    Returns ``None`` when there is no provenance to show (unattested case) —
    nothing here is inferred, every string traces back to a captured field.
    """
    if provenance is None:
        return None

    source_lines = [
        f"{src.domain} · {src.tier if src.tier else 'tier unknown'} · {_freshness_label(src.fresh)}"
        for src in provenance.scent_sources
    ]

    marks: list[str] = []
    if provenance.joins_verified > 0:
        marks.append("join verified")
    if provenance.lineage_used:
        marks.append("lineage")
    if provenance.verifier_verdict:
        marks.append(f"verifier: {provenance.verifier_verdict}")
    marks.append(f"{provenance.run_sql_count} queries run")

    stamp_parts: list[str] = []
    if provenance.schema_fingerprint:
        stamp_parts.append(f"schema {provenance.schema_fingerprint}")
    if provenance.git_sha:
        stamp_parts.append(f"code {provenance.git_sha}")
    if provenance.model_id:
        stamp_parts.append(provenance.model_id)
    if provenance.captured_at:
        stamp_parts.append(f"captured {provenance.captured_at:%Y-%m-%d}")

    return {
        "source_lines": source_lines,
        "marks": marks,
        "stamp": " · ".join(stamp_parts) if stamp_parts else None,
    }


_TEMPLATE_SRC = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ title }}</title>
  <style>
    /* self-contained styles — no external assets */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      line-height: 1.6;
      color: #1a1a2e;
      background: #f5f5f5;
      padding: 2rem;
    }
    .header {
      max-width: 900px;
      margin: 0 auto 2rem;
      padding-bottom: 1rem;
      border-bottom: 2px solid #4a4e69;
    }
    .header h1 { font-size: 1.8rem; color: #4a4e69; }
    .header .meta {
      color: #6c757d;
      font-size: 0.9rem;
      margin-top: 0.25rem;
      text-transform: uppercase;
      letter-spacing: .03em;
    }
    .finding {
      background: #fff;
      border-radius: 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,.1);
      padding: 1.5rem;
      max-width: 900px;
      margin: 0 auto 1.5rem;
    }
    .finding-number {
      font-size: 0.8rem;
      color: #6c757d;
      text-transform: uppercase;
      letter-spacing: .05em;
      margin-bottom: 0.5rem;
    }
    .question { font-size: 1.2rem; font-weight: 600; margin-bottom: 1rem; }
    .note {
      background: #fffbeb;
      border-left: 3px solid #f59e0b;
      padding: 0.5rem 1rem;
      margin-bottom: 1rem;
      font-style: italic;
      color: #78350f;
    }
    .chart { margin-bottom: 1rem; }
    .chart img { max-width: 100%; border-radius: 6px; display: block; }
    .results { margin-bottom: 1rem; }
    .results table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
    }
    .results th, .results td {
      text-align: left;
      padding: 0.4rem 0.6rem;
      border-bottom: 1px solid #e9ecef;
    }
    .results th {
      color: #4a4e69;
      font-weight: 600;
      border-bottom: 2px solid #4a4e69;
    }
    .results .rows-caption {
      font-size: 0.82rem;
      color: #6c757d;
      margin-top: 0.4rem;
    }
    .results .rows-note {
      font-size: 0.9rem;
      color: #6c757d;
      font-style: italic;
    }
    details.sql-details {
      margin-bottom: 1rem;
    }
    details.sql-details summary {
      cursor: pointer;
      font-size: 0.85rem;
      color: #4a4e69;
      font-weight: 600;
      margin-bottom: 0.5rem;
    }
    .sql-block {
      background: #1e1e2e;
      color: #cdd6f4;
      border-radius: 6px;
      padding: 1rem 1.25rem;
      overflow-x: auto;
      font-family: "SF Mono", "Fira Code", Consolas, monospace;
      font-size: 0.88rem;
      margin-top: 0.5rem;
    }
    .trust {
      font-size: 0.82rem;
      color: #6c757d;
      background: #f8f9fa;
      border-radius: 6px;
      padding: 0.75rem 1rem;
      margin-top: 1rem;
    }
    .trust .source-line { margin-bottom: 0.2rem; }
    .trust .marks { margin-top: 0.3rem; }
    .trust .mark {
      display: inline-block;
      background: #e9ecef;
      color: #4a4e69;
      border-radius: 4px;
      padding: 0.1rem 0.5rem;
      margin: 0.15rem 0.3rem 0.15rem 0;
      font-size: 0.78rem;
    }
    .trust .stamp {
      margin-top: 0.4rem;
      color: #adb5bd;
      font-size: 0.78rem;
    }
    .trust .unattested {
      font-style: italic;
      color: #adb5bd;
    }
    .footer {
      max-width: 900px;
      margin: 2rem auto 0;
      text-align: center;
      font-size: 0.8rem;
      color: #adb5bd;
    }
    .footer a { color: #4a4e69; text-decoration: none; }
    .footer a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <div class="header">
    <h1>{{ title }}</h1>
    <div class="meta">
      {{ kind }} &nbsp;|&nbsp; v{{ version_n }} &middot; exported {{ exported_at_str }} UTC
    </div>
  </div>

  {% for f in findings %}
  <div class="finding">
    <div class="finding-number">Finding {{ loop.index }} of {{ findings | length }}</div>
    <h2 class="question">{{ f.question }}</h2>
    {% if f.note %}
    <div class="note">{{ f.note }}</div>
    {% endif %}
    {% if f.chart_data_uri %}
    <div class="chart">
      <img src="{{ f.chart_data_uri | safe }}" alt="Chart for: {{ f.question }}">
    </div>
    {% endif %}
    <div class="results">
      {% if rows_mode == "none" %}
      <p class="rows-note">{{ rows_omitted_line }}</p>
      {% elif f.table_rows is not none and f.table_columns is not none %}
      <table>
        <thead>
          <tr>
            {% for col in f.table_columns %}
            <th>{{ col }}</th>
            {% endfor %}
          </tr>
        </thead>
        <tbody>
          {% for row in f.table_rows %}
          <tr>
            {% for cell in row %}
            <td>{{ cell }}</td>
            {% endfor %}
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <p class="rows-caption">Showing {{ f.table_rows | length }} of {{ f.total_rows }} rows</p>
      {% else %}
      <p class="rows-note">{{ results_unavailable_line }}</p>
      {% endif %}
    </div>
    <details class="sql-details">
      <summary>SQL</summary>
      <div class="sql-block"><pre>{{ f.sql }}</pre></div>
    </details>
    <div class="trust">
      {% if f.trust %}
        {% for line in f.trust.source_lines %}
        <div class="source-line">{{ line }}</div>
        {% endfor %}
        <div class="marks">
          {% for mark in f.trust.marks %}
          <span class="mark">{{ mark }}</span>
          {% endfor %}
        </div>
        {% if f.trust.stamp %}
        <div class="stamp">{{ f.trust.stamp }}</div>
        {% endif %}
      {% else %}
        <div class="unattested">{{ unattested_line }}</div>
      {% endif %}
    </div>
  </div>
  {% endfor %}

  <div class="footer">
    <a href="{{ footer_url }}">{{ footer_text }}</a>
  </div>
</body>
</html>
"""

_ENV = Environment(autoescape=select_autoescape(default=True, default_for_string=True))
_TMPL = _ENV.from_string(_TEMPLATE_SRC)


def render_cheese(
    findings: list[FindingRender],
    *,
    title: str,
    kind: Literal["single", "report"],
    version_n: int,
    exported_at: datetime,
    rows_mode: Literal["preview", "none"],
) -> str:
    """Render findings + trust provenance as a self-contained HTML document.

    ``rows_mode="none"`` omits result values regardless of what the finding
    carries; a finding with no captured table (``table_rows is None``) under
    ``rows_mode="preview"`` renders the results-unavailable line instead of a
    table. Provenance is rendered verbatim from the pin-time snapshot — a
    finding pinned before provenance capture existed shows the unattested
    line rather than any inferred trust signal.
    """
    finding_ctx: list[dict[str, Any]] = [
        {
            "question": f.question,
            "note": f.note,
            "sql": f.sql,
            "chart_data_uri": (
                f"data:image/png;base64,{f.chart_png_b64}" if f.chart_png_b64 else None
            ),
            "table_columns": f.table_columns,
            "table_rows": f.table_rows,
            "total_rows": f.total_rows,
            "pinned_at": f.pinned_at,
            "trust": _trust_context(f.provenance),
        }
        for f in findings
    ]

    return _TMPL.render(
        title=title,
        kind=kind,
        version_n=version_n,
        exported_at_str=f"{exported_at:%Y-%m-%d %H:%M}",
        rows_mode=rows_mode,
        findings=finding_ctx,
        unattested_line=UNATTESTED_LINE,
        rows_omitted_line=ROWS_OMITTED_LINE,
        results_unavailable_line=RESULTS_UNAVAILABLE_LINE,
        footer_text=FOOTER_TEXT,
        footer_url=FOOTER_URL,
    )
