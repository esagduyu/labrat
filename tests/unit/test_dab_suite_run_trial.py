from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import yaml

from labrat.eval.benchmarks.dab.suite import DabSuite


def _make_mixed_db_fixture(tmp_path: Path) -> None:
    """Fixture with both DuckDB and SQLite databases (like deps_dev_v1/music_brainz_20k)."""
    dataset_dir = tmp_path / "query_mixed1"
    dataset_dir.mkdir(parents=True)

    sqlite_path = tmp_path / "pkg.db"
    sqlite_path.touch()
    duckdb_path = tmp_path / "proj.duckdb"
    duckdb_path.touch()

    (dataset_dir / "db_config.yaml").write_text(
        yaml.safe_dump(
            {
                "db_clients": {
                    "package_database": {
                        "db_type": "sqlite",
                        "db_path": str(sqlite_path),
                    },
                    "project_database": {
                        "db_type": "duckdb",
                        "db_path": str(duckdb_path),
                    },
                }
            }
        )
    )
    (dataset_dir / "db_description.txt").write_text("Mixed DB dataset")
    (dataset_dir / "query1").mkdir()
    (dataset_dir / "query1" / "query.json").write_text('"Which packages are most used?"')
    (dataset_dir / "query1" / "validate.py").write_text("def validate(out): return (True, None)\n")


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


async def test_run_trial_prompt_includes_attach_for_duckdb_sqlite_mix(tmp_path: Path) -> None:
    """When a dataset has DuckDB + SQLite, the prompt must include the ATTACH idiom."""
    _make_mixed_db_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path)
    task = next(iter(suite.tasks()))

    captured: list[str] = []

    async def capture_invoke(prompt: str, ctx: Any, **kwargs: Any) -> dict[str, Any]:
        captured.append(prompt)
        return {"final_text": "answer", "tool_calls": 0}

    with patch("labrat.eval.benchmarks.dab.suite._invoke_agent", new=capture_invoke):
        await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")

    assert captured, "invoke_agent was never called"
    prompt = captured[0]
    assert "ATTACH" in prompt, "Cross-DB ATTACH idiom missing from prompt"
    assert "TYPE SQLITE" in prompt, "SQLITE attachment type missing from prompt"


def _make_real_duckdb_fixture(tmp_path: Path) -> None:
    """A dataset with a real (non-empty) DuckDB so the labrat-agent driver can connect."""
    dataset_dir = tmp_path / "query_real1"
    dataset_dir.mkdir(parents=True)
    duckdb_path = tmp_path / "main.duckdb"
    con = duckdb.connect(str(duckdb_path))
    con.execute("CREATE TABLE t(id INTEGER)")
    con.close()
    (dataset_dir / "db_config.yaml").write_text(
        yaml.safe_dump(
            {"db_clients": {"main_database": {"db_type": "duckdb", "db_path": str(duckdb_path)}}}
        )
    )
    (dataset_dir / "db_description.txt").write_text("Real")
    (dataset_dir / "query1").mkdir()
    (dataset_dir / "query1" / "query.json").write_text('"How many?"')
    (dataset_dir / "query1" / "validate.py").write_text("def validate(out): return (True, None)\n")


@patch("labrat.agent.providers.build_provider", return_value=MagicMock())
async def test_labrat_agent_driver_threads_verify_flag(
    _provider: MagicMock, tmp_path: Path
) -> None:
    """--agent-verify reaches run_agent_task as verify=True; default is False."""
    _make_real_duckdb_fixture(tmp_path)
    captured: dict[str, Any] = {}

    async def fake_run_agent_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(final_text="ok", tool_calls=0, latency_seconds=0.0)

    # verify on
    suite_on = DabSuite(dab_dir=tmp_path, driver="labrat-agent", agent_verify=True)
    task = next(iter(suite_on.tasks()))
    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        await suite_on.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch_on")
    assert captured.get("verify") is True

    # verify off by default
    captured.clear()
    suite_off = DabSuite(dab_dir=tmp_path, driver="labrat-agent")
    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        await suite_off.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch_off")
    assert captured.get("verify") is False


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
