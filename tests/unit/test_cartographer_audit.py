"""generate_scent freeze-time contamination audit (FEATURE: LLM-semantic Scent / T1c)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import generate_scent
from labrat.maze.scent_audit import ScentContaminationError


def _conns(db: Path) -> tuple[dict[str, object], dict[str, object]]:
    conn = DuckDBConnection(db, read_only=True)
    conn.connect()
    return {"shop": conn}, {"shop": conn.introspect_catalog()}


async def test_audit_raises_on_answer_shaped_semantics(ecommerce_db: Path) -> None:
    connections, catalogs = _conns(ecommerce_db)

    async def _leaky(prompt: str) -> str:
        return "## Gotchas\n- The ground truth answer for revenue is 12345."

    try:
        with pytest.raises(ScentContaminationError):
            await generate_scent(
                connections=connections,
                catalogs=catalogs,
                primary="shop",
                with_semantics=True,
                llm_fn=_leaky,
            )
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]


async def test_audit_passes_clean_semantics(ecommerce_db: Path) -> None:
    connections, catalogs = _conns(ecommerce_db)

    async def _clean(prompt: str) -> str:
        return "## Gotchas\n- Exclude is_test rows from revenue metrics."

    try:
        docs = await generate_scent(
            connections=connections,
            catalogs=catalogs,
            primary="shop",
            with_semantics=True,
            llm_fn=_clean,
        )
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]
    assert any(s.heading == "Gotchas" and s.source == "draft" for s in docs[0].sections)


async def test_audit_still_fail_loud_with_code_name_and_prune(tmp_path: Path) -> None:
    # Full pipeline: a doc with a C2 Code Columns section + a leaky drafted/pruned bullet
    # must still raise at the freeze-time audit (GT-firewall preserved).
    import duckdb

    p = str(tmp_path / "clinical.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE clinical_info(icd_o_3_histology VARCHAR, histological_type VARCHAR)")
    raw.execute(
        "INSERT INTO clinical_info VALUES "
        "('9400/3','Astrocytoma'),('9401/3','Astrocytoma'),"
        "('9450/3','Oligodendroglioma'),('9382/3','Oligoastrocytoma')"
    )
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()

    async def _leaky(prompt: str) -> str:
        # returned for BOTH the draft and the PRUNE call (prune echoes it back as kept)
        return "## Gotchas\n- The ground truth answer for revenue is 12345."

    try:
        with pytest.raises(ScentContaminationError):
            await generate_scent(
                connections={"clin": conn},
                catalogs={"clin": conn.introspect_catalog()},
                primary="clin",
                with_semantics=True,
                llm_fn=_leaky,
            )
    finally:
        conn.disconnect()
