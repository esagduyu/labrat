"""BIRD-bench eval suite (M26).

Loads BIRD-bench cases. Set BIRD_DATA_DIR to the local BIRD dataset directory
(contains train.json / dev.json from the BIRD paper).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from labrat.eval.models import EvalCase


def _load_cases() -> list[EvalCase]:
    data_dir = os.environ.get("BIRD_DATA_DIR")
    if not data_dir:
        return []
    path = Path(data_dir) / "dev.json"
    if not path.exists():
        path = Path(data_dir) / "train.json"
    if not path.exists():
        return []
    raw: list[dict[str, Any]] = json.loads(path.read_text())
    return [
        EvalCase(
            id=str(item.get("question_id", i)),
            question=str(item["question"]),
            expected_sql=item.get("SQL"),
            domain=item.get("db_id"),
            dialect="duckdb",
        )
        for i, item in enumerate(raw)
    ]


class BirdSuite:
    """BIRD-bench benchmark. Set BIRD_DATA_DIR to load cases."""

    suite_name = "bird"

    def __init__(self) -> None:
        self.cases: list[EvalCase] = _load_cases()
