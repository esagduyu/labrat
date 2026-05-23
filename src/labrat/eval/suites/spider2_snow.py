"""Spider 2.0-Snow eval suite (M26).

Loads the 547-example Spider 2.0-Snow suite (Snowflake dialect).
Set SPIDER2_DATA_DIR to a local clone of xlang-ai/Spider2.
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
    path = Path(data_dir) / "spider2-snow" / "examples.json"
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
            dialect="snowflake",
        )
        for i, item in enumerate(raw)
    ]


class Spider2SnowSuite:
    """Spider 2.0-Snow benchmark (547 examples). Set SPIDER2_DATA_DIR to load."""

    suite_name = "spider2-snow"

    def __init__(self) -> None:
        self.cases: list[EvalCase] = _load_cases()
