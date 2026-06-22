"""DAB AutoContext pre-pass helper (FEATURE: cartographer DAB pre-pass)."""

from __future__ import annotations

from pathlib import Path

from labrat.agent.tools.base import ToolContext
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.eval.benchmarks.dab.env import DabTaskEnv
from labrat.eval.benchmarks.dab.suite import _autocontext_prepass


def _env(ecommerce_db: Path) -> DabTaskEnv:
    # primary connection is NOT connected (mirrors build_dab_task_env)
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    ctx = ToolContext(
        connections={"shop": conn},
        catalogs={"shop": Catalog(database_name="shop", schemas=[])},
        primary="shop",
    )
    return DabTaskEnv(ctx=ctx, attachable=[], mongo=[])


async def test_autocontext_populates_per_dataset_store(ecommerce_db: Path, tmp_path: Path) -> None:
    maze_root = await _autocontext_prepass(_env(ecommerce_db), "stockindex", tmp_path)
    assert maze_root == tmp_path / "stockindex"
    docs = list((maze_root / "labrat_maze" / "scent").glob("*.md"))
    assert docs, "pre-pass should have written at least one Scent doc"


async def test_autocontext_isolates_datasets(ecommerce_db: Path, tmp_path: Path) -> None:
    a = await _autocontext_prepass(_env(ecommerce_db), "ds_a", tmp_path)
    b = await _autocontext_prepass(_env(ecommerce_db), "ds_b", tmp_path)
    assert a != b  # different datasets -> different maze roots (no collision)
    assert (a / "labrat_maze" / "scent").exists()
    assert (b / "labrat_maze" / "scent").exists()
