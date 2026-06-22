"""First-contact Scent pre-pass (FEATURE: cartographer DAB pre-pass)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import cartograph_prepass


def _conn(ecommerce_db: Path) -> tuple[dict[str, object], dict[str, object]]:
    conn = DuckDBConnection(ecommerce_db, read_only=True)
    conn.connect()
    return {"shop": conn}, {"shop": conn.introspect_catalog()}


async def test_prepass_generates_then_caches(ecommerce_db: Path, tmp_path: Path) -> None:
    connections, catalogs = _conn(ecommerce_db)
    scent_dir = tmp_path / "labrat_maze" / "scent"
    try:
        paths = await cartograph_prepass(connections, catalogs, "shop", scent_dir)
        assert paths and all(p.exists() for p in paths)
        # mark a doc, then re-run: first-contact cache must NOT regenerate (mark survives)
        paths[0].write_text(paths[0].read_text(encoding="utf-8") + "\nSENTINEL", encoding="utf-8")
        again = await cartograph_prepass(connections, catalogs, "shop", scent_dir)
        assert again == paths
        assert "SENTINEL" in paths[0].read_text(encoding="utf-8")  # reused, not regenerated
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]


async def test_prepass_deterministic_makes_zero_llm_calls(
    ecommerce_db: Path, tmp_path: Path
) -> None:
    connections, catalogs = _conn(ecommerce_db)
    calls = {"n": 0}

    async def _spy(prompt: str) -> str:
        calls["n"] += 1
        return "## Gotchas\n- x"

    try:
        await cartograph_prepass(
            connections,
            catalogs,
            "shop",
            tmp_path / "labrat_maze" / "scent",
            with_semantics=False,
            llm_fn=_spy,
        )
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]
    assert calls["n"] == 0  # deterministic pre-pass never calls the model


async def test_prepass_output_is_retrievable(
    ecommerce_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections, catalogs = _conn(ecommerce_db)
    try:
        await cartograph_prepass(connections, catalogs, "shop", tmp_path / "labrat_maze" / "scent")
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="how do I join orders to customers?"),
    )
    assert any(r.domain == "shop" for r in out.results)
