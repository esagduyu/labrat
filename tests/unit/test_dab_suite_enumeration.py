from pathlib import Path

from labrat.eval.benchmarks.dab.suite import DabSuite
from labrat.eval.types import BenchmarkSuite

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "dab"


def test_dab_suite_implements_protocol() -> None:
    suite = DabSuite(dab_dir=FIXTURE_DIR)
    assert isinstance(suite, BenchmarkSuite)
    assert suite.name == "dab"


def test_dab_suite_enumerates_synthetic_dataset() -> None:
    suite = DabSuite(dab_dir=FIXTURE_DIR)
    tasks = list(suite.tasks())
    ids = sorted(t.id for t in tasks)
    assert "synthetic1:1" in ids


def test_dab_suite_task_carries_dataset_in_config() -> None:
    suite = DabSuite(dab_dir=FIXTURE_DIR)
    t = next(t for t in suite.tasks() if t.id == "synthetic1:1")
    assert t.benchmark == "dab"
    assert t.config["dataset"] == "synthetic1"
    assert t.config["validator_path"].endswith("validate.py")
    assert t.prompt


def test_dab_suite_prompt_contains_description_and_question() -> None:
    suite = DabSuite(dab_dir=FIXTURE_DIR)
    t = next(t for t in suite.tasks() if t.id == "synthetic1:1")
    assert "DuckDB" in t.prompt
    assert "How many rows" in t.prompt


def test_dab_suite_hints_flag_uses_withhint_description() -> None:
    suite = DabSuite(dab_dir=FIXTURE_DIR, hints=True)
    t = next(t for t in suite.tasks() if t.id == "synthetic1:1")
    assert "Hint:" in t.prompt
