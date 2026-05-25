"""Unit tests for eval suite structure (M26)."""

from __future__ import annotations

from pathlib import Path

from labrat.eval.models import EvalCase
from labrat.eval.suites.custom_scenarios import CustomScenariosSuite


def test_custom_scenarios_suite_has_cases() -> None:
    suite = CustomScenariosSuite()
    assert len(suite.cases) >= 20


def test_custom_scenarios_suite_name() -> None:
    suite = CustomScenariosSuite()
    assert suite.suite_name == "custom-scenarios"


def test_custom_scenarios_cases_are_eval_cases() -> None:
    suite = CustomScenariosSuite()
    for case in suite.cases:
        assert isinstance(case, EvalCase)


def test_custom_scenarios_cases_have_questions() -> None:
    suite = CustomScenariosSuite()
    for case in suite.cases:
        assert case.question


def test_custom_scenarios_cases_have_unique_ids() -> None:
    suite = CustomScenariosSuite()
    ids = [c.id for c in suite.cases]
    assert len(ids) == len(set(ids))


def test_bird_suite_has_name() -> None:
    from labrat.eval.suites.bird import BirdSuite

    suite = BirdSuite()
    assert suite.suite_name == "bird"


def test_latency_suite_has_name() -> None:
    from labrat.eval.suites.latency import LatencySuite

    suite = LatencySuite()
    assert suite.suite_name == "latency"


# ── ADE-bench suite ───────────────────────────────────────────────────────────


def test_ade_bench_suite_name() -> None:
    from labrat.eval.suites.ade_bench import AdeBenchSuite

    suite = AdeBenchSuite()
    assert suite.suite_name == "ade-bench"


def test_ade_bench_suite_returns_empty_when_dir_missing(tmp_path: Path) -> None:
    from labrat.eval.suites.ade_bench import AdeBenchSuite

    suite = AdeBenchSuite(ade_bench_dir=tmp_path / "nonexistent")
    assert suite.cases == []


def test_ade_bench_suite_loads_cases_from_fixture(tmp_path: Path) -> None:
    """AdeBenchSuite reads task.yaml files and returns EvalCase objects."""
    import yaml

    from labrat.eval.suites.ade_bench import AdeBenchSuite

    task_dir = tmp_path / "tasks" / "test001"
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text(
        yaml.dump(
            {
                "task_id": "test001",
                "status": "ready",
                "description": "A test task",
                "prompts": [{"key": "base", "prompt": "Rename the dim_customer model."}],
                "difficulty": "easy",
                "tags": ["dbt"],
                "variants": [
                    {
                        "db_type": "duckdb",
                        "db_name": "simple",
                        "project_type": "dbt",
                        "project_name": "simple",
                    }
                ],
            }
        )
    )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    assert len(suite.cases) == 1
    case = suite.cases[0]
    assert case.id == "test001"
    assert "dim_customer" in case.question
    assert case.dialect == "duckdb"


def test_ade_bench_suite_skips_dev_status(tmp_path: Path) -> None:
    import yaml

    from labrat.eval.suites.ade_bench import AdeBenchSuite

    task_dir = tmp_path / "tasks" / "dev001"
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text(
        yaml.dump(
            {
                "task_id": "dev001",
                "status": "dev",
                "description": "Dev task",
                "prompts": [{"key": "base", "prompt": "Do something."}],
                "difficulty": "easy",
                "tags": [],
                "variants": [
                    {
                        "db_type": "duckdb",
                        "db_name": "simple",
                        "project_type": "dbt",
                        "project_name": "simple",
                    }
                ],
            }
        )
    )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    assert suite.cases == []


def test_ade_bench_suite_skips_non_duckdb_variants(tmp_path: Path) -> None:
    import yaml

    from labrat.eval.suites.ade_bench import AdeBenchSuite

    task_dir = tmp_path / "tasks" / "snow001"
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text(
        yaml.dump(
            {
                "task_id": "snow001",
                "status": "ready",
                "description": "Snowflake only task",
                "prompts": [{"key": "base", "prompt": "Do something."}],
                "difficulty": "easy",
                "tags": [],
                "variants": [
                    {
                        "db_type": "snowflake",
                        "db_name": "simple",
                        "project_type": "dbt",
                        "project_name": "simple",
                    }
                ],
            }
        )
    )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    assert suite.cases == []
