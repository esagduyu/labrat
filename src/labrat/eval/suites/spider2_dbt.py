"""Spider 2.0-DBT eval suite (M26).

Loads the 68-task Spider 2.0-DBT suite. Tasks require DuckDB / dbt-compatible
SQL. Data must be downloaded separately from xlang-ai/Spider2 on GitHub.
Set SPIDER2_DATA_DIR to a local clone; unset means no cases are loaded.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from labrat.eval.models import EvalCase


def _load_cases() -> list[EvalCase]:
    data_dir = os.environ.get("SPIDER2_DATA_DIR")
    if not data_dir:
        return []
    path = Path(data_dir) / "spider2-dbt" / "examples.json"
    if not path.exists():
        return []
    raw: list[dict[str, Any]] = json.loads(path.read_text())
    return [
        EvalCase(
            id=str(item.get("id", i)),
            question=str(item["question"]),
            expected_sql=item.get("sql"),
            gold_result=item.get("gold_result"),
            domain=item.get("domain"),
            dialect="duckdb",
        )
        for i, item in enumerate(raw)
    ]


class Spider2DBTSuite:
    """Spider 2.0-DBT benchmark (68 tasks). Set SPIDER2_DATA_DIR to load."""

    suite_name = "spider2-dbt"

    def __init__(self) -> None:
        self.cases: list[EvalCase] = _load_cases()
