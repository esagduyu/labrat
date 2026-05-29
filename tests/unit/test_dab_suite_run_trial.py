from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

from labrat.eval.benchmarks.dab.suite import DabSuite


def _make_synthetic_fixture(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "query_synthetic1"
    dataset_dir.mkdir(parents=True)

    duckdb_path = tmp_path / "main.duckdb"
    duckdb_path.touch()

    (dataset_dir / "db_config.yaml").write_text(
        yaml.safe_dump(
            {
                "db_clients": {
                    "main_database": {
                        "db_type": "duckdb",
                        "db_path": str(duckdb_path),
                    }
                }
            }
        )
    )
    (dataset_dir / "db_description.txt").write_text("Synthetic")
    (dataset_dir / "query1").mkdir()
    (dataset_dir / "query1" / "query.json").write_text('"How many?"')
    (dataset_dir / "query1" / "validate.py").write_text(
        "def validate(out): return ('3' in out, 'expected 3')\n"
    )


async def test_run_trial_records_passing_answer(tmp_path: Path) -> None:
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path)
    task = next(iter(suite.tasks()))

    with patch(
        "labrat.eval.benchmarks.dab.suite._invoke_agent",
        new=AsyncMock(return_value={"final_text": "The answer is 3", "tool_calls": 4}),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")

    assert result.passed is True
    assert result.task_id == task.id
    assert result.trial_num == 0
    assert result.artifact["type"] == "text"
    assert result.artifact["payload"] == "The answer is 3"
    assert result.tool_calls == 4


async def test_run_trial_records_failing_answer(tmp_path: Path) -> None:
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path)
    task = next(iter(suite.tasks()))

    with patch(
        "labrat.eval.benchmarks.dab.suite._invoke_agent",
        new=AsyncMock(return_value={"final_text": "no number", "tool_calls": 1}),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")

    assert result.passed is False
    assert result.reason


async def test_run_trial_records_validator_error(tmp_path: Path) -> None:
    _make_synthetic_fixture(tmp_path)
    dataset_dir = tmp_path / "query_synthetic1"
    (dataset_dir / "query1" / "validate.py").write_text(
        "def validate(out): raise RuntimeError('boom')\n"
    )
    suite = DabSuite(dab_dir=tmp_path)
    task = next(iter(suite.tasks()))

    with patch(
        "labrat.eval.benchmarks.dab.suite._invoke_agent",
        new=AsyncMock(return_value={"final_text": "whatever", "tool_calls": 0}),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")

    assert result.passed is False
    assert result.reason is not None
    assert result.reason.startswith("validator_error")
