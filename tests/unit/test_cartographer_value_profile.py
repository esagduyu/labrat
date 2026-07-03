"""Value-range + stratified format-sampling in Dimensions (M0 Deterministic Data-
Intelligence Pack, task 4)."""

from __future__ import annotations

import duckdb

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import build_dimensions
from labrat.maze.document import ScentDoc
from labrat.maze.scent_audit import audit_scent_doc


async def test_ranges_and_format_samples(tmp_path) -> None:
    p = str(tmp_path / "v.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(n INT, path VARCHAR)")
    raw.execute("INSERT INTO t VALUES (1,'a>b>c'),(50,'plain'),(999,'x::y::z')")
    raw.close()
    conn = DuckDBConnection(path=p)
    conn.connect()
    catalog = conn.introspect_catalog()
    prof = await ProfileDatasetTool().execute(
        ToolContext(connection=conn, catalog=catalog, primary="main"),
        ProfileDatasetTool().input_model(sample_rows=0, max_tables=100),
    )
    body = build_dimensions(prof, conn).body
    assert "1..999" in body or "min 1" in body  # numeric range for n
    assert "a>b>c" in body or "x::y::z" in body  # unusual-structure sample surfaced
    conn.disconnect()


async def test_format_samples_truncated_and_contamination_filtered(tmp_path) -> None:
    """I2 regression: a >60-char free-text value containing an answer-key pattern
    ("ground truth") must never be emitted verbatim into the deterministic Scent
    doc — this path never runs audit_scent_doc, so it must be clean by construction.
    A benign long value is still surfaced, but truncated to ~80 chars."""
    p = str(tmp_path / "v2.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE notes(id INT, body VARCHAR)")
    tainted = "the ground truth answer for this task is stored right here in this note " + "z" * 20
    benign_long = "y" * 90  # >80 chars, plain, no unusual chars but >60 triggers "odd"
    raw.execute(
        "INSERT INTO notes VALUES (1, ?), (2, ?)",
        [tainted, benign_long],
    )
    raw.close()
    conn = DuckDBConnection(path=p)
    conn.connect()
    catalog = conn.introspect_catalog()
    prof = await ProfileDatasetTool().execute(
        ToolContext(connection=conn, catalog=catalog, primary="main"),
        ProfileDatasetTool().input_model(sample_rows=0, max_tables=100),
    )
    section = build_dimensions(prof, conn)
    body = section.body

    assert "ground truth" not in body.lower()
    assert "…" in body  # the benign long value was truncated, not dropped
    assert benign_long[:80] in body
    assert benign_long not in body  # full untruncated string must not appear

    doc = ScentDoc(domain="test-scent", kind="scent", tables=["notes"], sections=[section])
    assert audit_scent_doc(doc) is None

    conn.disconnect()
