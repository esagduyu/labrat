"""DAB Cartographer pre-pass helper (FEATURE: cartographer DAB pre-pass)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.eval.benchmarks.dab.env import DabTaskEnv
from labrat.eval.types import BenchmarkTask
from labrat.eval.benchmarks.dab.suite import (
    DabSuite,
    _cartographer_prompt_line,
    _run_cartographer,
    _safe_name,
)


def _env(ecommerce_db: Path) -> DabTaskEnv:
    # primary connection is NOT connected (mirrors build_dab_task_env)
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    ctx = ToolContext(
        connections={"shop": conn},
        catalogs={"shop": Catalog(database_name="shop", schemas=[])},
        primary="shop",
    )
    return DabTaskEnv(ctx=ctx, attachable=[], mongo=[])


async def test_cartograph_populates_per_dataset_store(ecommerce_db: Path, tmp_path: Path) -> None:
    maze_root = await _run_cartographer(_env(ecommerce_db), "stockindex", tmp_path)
    assert maze_root == tmp_path / "stockindex"
    docs = list((maze_root / "labrat_maze" / "scent").glob("*.md"))
    assert docs, "pre-pass should have written at least one Scent doc"


async def test_cartograph_isolates_datasets(ecommerce_db: Path, tmp_path: Path) -> None:
    a = await _run_cartographer(_env(ecommerce_db), "ds_a", tmp_path)
    b = await _run_cartographer(_env(ecommerce_db), "ds_b", tmp_path)
    assert a != b  # different datasets -> different maze roots (no collision)
    assert (a / "labrat_maze" / "scent").exists()
    assert (b / "labrat_maze" / "scent").exists()


def test_safe_name_handles_unsafe_and_dotty_names() -> None:
    assert _safe_name("stockindex") == "stockindex"
    assert _safe_name("foo/bar") == "foo_bar"
    assert _safe_name(".") == "dataset"
    assert _safe_name("..") == "dataset"
    assert _safe_name("") == "dataset"


def test_cartographer_prompt_line_mentions_search_reference_docs() -> None:
    line = _cartographer_prompt_line()
    assert "search_reference_docs" in line


async def test_labrat_agent_timeout_is_classified_infra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # run_trial must turn a TimeoutError from the driver into reason="infra:timeout"
    suite = DabSuite(driver="labrat-agent")

    async def _boom(*a: object, **k: object) -> None:
        raise TimeoutError("simulated stall")

    monkeypatch.setattr(suite, "_run_trial_labrat_agent", _boom)
    # Synthetic task — the driver is stubbed (raises before touching these paths), so we
    # don't enumerate suite.tasks() (which needs the DAB checkout, absent in CI).
    task = BenchmarkTask(
        id="synthetic:1",
        benchmark="dab",
        prompt="?",
        config={"db_config_path": "unused", "validator_path": "unused"},
    )
    res = await suite.run_trial(task, 0, tmp_path / "scratch")
    assert res.reason == "infra:timeout"
    assert res.passed is False
