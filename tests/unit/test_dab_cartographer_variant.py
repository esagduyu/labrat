"""Variant-scoped `_run_cartographer` (M1 Unit 1a diversity plumbing)."""

from __future__ import annotations

from pathlib import Path

from labrat.agent.tools.base import ToolContext
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.eval.benchmarks.dab.env import DabTaskEnv
from labrat.eval.benchmarks.dab.suite import _run_cartographer


def _env(ecommerce_db: Path) -> DabTaskEnv:
    # primary connection is NOT connected (mirrors build_dab_task_env)
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    ctx = ToolContext(
        connections={"shop": conn},
        catalogs={"shop": Catalog(database_name="shop", schemas=[])},
        primary="shop",
    )
    return DabTaskEnv(ctx=ctx, attachable=[], mongo=[])


async def test_variant_seed_zero_matches_unsuffixed_path(
    ecommerce_db: Path, tmp_path: Path
) -> None:
    root = await _run_cartographer(_env(ecommerce_db), "ds", tmp_path, variant_seed=0)
    assert root == tmp_path / "ds"
    assert (root / "labrat_maze" / "scent").exists()


async def test_variant_seed_gives_distinct_maze_root(ecommerce_db: Path, tmp_path: Path) -> None:
    root0 = await _run_cartographer(_env(ecommerce_db), "ds", tmp_path, variant_seed=0)
    root1 = await _run_cartographer(_env(ecommerce_db), "ds", tmp_path, variant_seed=1)

    assert root0 != root1
    docs0 = list((root0 / "labrat_maze" / "scent").glob("*.md"))
    docs1 = list((root1 / "labrat_maze" / "scent").glob("*.md"))
    assert docs0, "variant-0 pre-pass should have written at least one Scent doc"
    assert docs1, "variant-1 pre-pass should have written at least one Scent doc"


async def test_different_variant_seeds_give_distinct_roots(
    ecommerce_db: Path, tmp_path: Path
) -> None:
    root1 = await _run_cartographer(_env(ecommerce_db), "ds", tmp_path, variant_seed=1)
    root2 = await _run_cartographer(_env(ecommerce_db), "ds", tmp_path, variant_seed=2)
    assert root1 != root2
