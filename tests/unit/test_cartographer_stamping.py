"""Cartographer sections carry schema_hash (freshness activation, T1b D3)."""

from pathlib import Path

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import generate_scent
from labrat.maze.staleness import fingerprint_from_catalog


async def test_all_sections_stamped_no_clock(ecommerce_db: Path) -> None:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    docs = await generate_scent(
        connections={"main": conn}, catalogs={"main": catalog}, primary="main"
    )
    expected = fingerprint_from_catalog(catalog)
    assert docs
    for doc in docs:
        for s in doc.sections:
            assert s.schema_hash == expected
            assert s.generated_at is None and s.model_id is None and s.git_sha is None


async def test_stamping_is_deterministic(ecommerce_db: Path) -> None:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    kwargs = dict(connections={"main": conn}, catalogs={"main": catalog}, primary="main")
    a = await generate_scent(**kwargs)
    b = await generate_scent(**kwargs)
    from labrat.maze.document import render_document

    assert [render_document(d) for d in a] == [render_document(d) for d in b]
