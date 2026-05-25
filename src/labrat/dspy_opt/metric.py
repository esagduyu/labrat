"""Spider2-DBT evaluation metric for DSPy.

Ports the duckdb_match + compare_pandas_table logic from the Spider2 eval suite
so we don't need to add the Spider2 repo to sys.path.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

# ── low-level comparison helpers (ported from Spider2/spider2-dbt/eval_utils.py) ──


def _vectors_match(v1: list, v2: list, tol: float = 1e-2, ignore_order: bool = False) -> bool:
    try:
        if ignore_order:
            key = lambda x: (x is None, str(x), isinstance(x, (int, float)))  # noqa: E731
            v1 = sorted(v1, key=key)
            v2 = sorted(v2, key=key)
        if len(v1) != len(v2):
            return False
        for a, b in zip(v1, v2, strict=False):
            if pd.isna(a) and pd.isna(b):
                continue
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                if not math.isclose(float(a), float(b), abs_tol=tol):
                    return False
            elif a != b:
                return False
        return True
    except Exception:
        return False


def _compare_pandas_table(
    pred: pd.DataFrame,
    gold: pd.DataFrame,
    condition_cols: list[int],
    ignore_order: bool = False,
) -> bool:
    gold_sub = gold.iloc[:, condition_cols] if condition_cols else gold
    t_gold = gold_sub.transpose().values.tolist()
    t_pred = pred.transpose().values.tolist()

    for gold_col in t_gold:
        if not any(_vectors_match(gold_col, p, ignore_order=ignore_order) for p in t_pred):
            return False
    return True


def _read_table(db_path: str, table_name: str) -> pd.DataFrame:
    con = duckdb.connect(database=db_path, read_only=True)
    try:
        return con.execute(f"SELECT * FROM {table_name}").fetchdf()
    finally:
        con.close()


def duckdb_match(
    pred_path: str,
    gold_path: str,
    condition_tabs: list[str],
    condition_cols: list[list[int]],
    ignore_orders: list[bool],
) -> int:
    """Return 1 if all condition tables in pred match gold, else 0."""
    if condition_cols is None:
        condition_cols = [[] for _ in condition_tabs]
    if ignore_orders is None:
        ignore_orders = [False] * len(condition_tabs)

    try:
        pred_tables = [_read_table(pred_path, t) for t in condition_tabs]
    except Exception:
        return 0

    gold_tables = [_read_table(gold_path, t) for t in condition_tabs]

    for pred_df, gold_df, cols, ignore in zip(
        pred_tables, gold_tables, condition_cols, ignore_orders, strict=False
    ):
        if not _compare_pandas_table(pred_df, gold_df, cols or [], ignore):
            return 0
    return 1


# ── gold eval loader ──────────────────────────────────────────────────────────


def load_gold_eval(spider2_dbt_dir: Path) -> dict[str, dict[str, Any]]:
    """Load spider2_eval.jsonl into {instance_id: entry}."""
    gold_file = spider2_dbt_dir / "evaluation_suite" / "gold" / "spider2_eval.jsonl"
    return {
        entry["instance_id"]: entry
        for line in gold_file.read_text().splitlines()
        if (entry := json.loads(line))
    }


# ── DSPy metric factory ───────────────────────────────────────────────────────


def make_metric(
    executor: Any,
    spider2_dbt_dir: Path,
    gold_eval: dict[str, dict[str, Any]],
) -> Any:
    """Return a DSPy-compatible metric(example, prediction, trace) -> float."""
    gold_dir = spider2_dbt_dir / "evaluation_suite" / "gold"

    def metric(example: Any, prediction: Any, trace: Any = None) -> float:
        instance_id: str = example.instance_id
        gold_entry = gold_eval.get(instance_id)
        if not gold_entry:
            return 0.0

        completed_sql: str = getattr(prediction, "completed_sql", "") or ""
        target_file: str = example.target_file

        output_db = executor.run_task(instance_id, {target_file: completed_sql})
        if output_db is None:
            return 0.0

        eval_cfg: dict[str, Any] = gold_entry["evaluation"]
        params = eval_cfg["parameters"]

        gold_db = gold_dir / instance_id / params["gold"]
        if not gold_db.exists():
            return 0.0

        try:
            score = duckdb_match(
                pred_path=str(output_db),
                gold_path=str(gold_db),
                condition_tabs=params.get("condition_tabs", []),
                condition_cols=params.get("condition_cols", []),
                ignore_orders=params.get("ignore_orders", []),
            )
        except Exception:
            score = 0

        return float(score)

    return metric


def make_agent_metric(
    spider2_dbt_dir: Path,
    gold_eval: dict[str, dict[str, Any]],
) -> Any:
    """Metric for Spider2AgentModule: reads DB path from prediction rather than running dbt.

    The agent runs dbt internally, so we just compare the already-built
    output DuckDB against the gold database.
    """
    gold_dir = spider2_dbt_dir / "evaluation_suite" / "gold"

    def metric(example: Any, prediction: Any, trace: Any = None) -> float:
        instance_id: str = example.instance_id
        gold_entry = gold_eval.get(instance_id)
        if not gold_entry:
            return 0.0

        db_path_str: str | None = getattr(prediction, "db_path", None)
        if not db_path_str:
            return 0.0
        output_db = Path(db_path_str)
        if not output_db.exists():
            return 0.0

        eval_cfg: dict[str, Any] = gold_entry["evaluation"]
        params = eval_cfg["parameters"]
        gold_db = gold_dir / instance_id / params["gold"]
        if not gold_db.exists():
            return 0.0

        try:
            score = duckdb_match(
                pred_path=str(output_db),
                gold_path=str(gold_db),
                condition_tabs=params.get("condition_tabs", []),
                condition_cols=params.get("condition_cols", []),
                ignore_orders=params.get("ignore_orders", []),
            )
        except Exception:
            score = 0

        return float(score)

    return metric
