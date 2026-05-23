"""Unit tests for eval suite structure (M26)."""

from __future__ import annotations

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


def test_spider2_dbt_suite_has_name() -> None:
    from labrat.eval.suites.spider2_dbt import Spider2DBTSuite

    suite = Spider2DBTSuite()
    assert suite.suite_name == "spider2-dbt"


def test_spider2_snow_suite_has_name() -> None:
    from labrat.eval.suites.spider2_snow import Spider2SnowSuite

    suite = Spider2SnowSuite()
    assert suite.suite_name == "spider2-snow"


def test_spider2_lite_suite_has_name() -> None:
    from labrat.eval.suites.spider2_lite import Spider2LiteSuite

    suite = Spider2LiteSuite()
    assert suite.suite_name == "spider2-lite"


def test_bird_suite_has_name() -> None:
    from labrat.eval.suites.bird import BirdSuite

    suite = BirdSuite()
    assert suite.suite_name == "bird"


def test_latency_suite_has_name() -> None:
    from labrat.eval.suites.latency import LatencySuite

    suite = LatencySuite()
    assert suite.suite_name == "latency"
