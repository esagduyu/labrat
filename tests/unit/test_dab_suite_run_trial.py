from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import duckdb
import pytest
import yaml

from labrat.agent.loop import TextBlock
from labrat.eval.benchmarks.dab.suite import DabSuite, DriverOutcome


class _UsageJudgeProvider:
    def __init__(self) -> None:
        self.usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "requests": 0,
        }
        self.request_usage: list[dict[str, Any]] = []

    def bind_conversation(self) -> _UsageJudgeProvider:
        return self

    async def stream(self, **_kwargs: Any) -> Any:
        self.usage["input_tokens"] += 100
        self.usage["output_tokens"] += 5
        self.usage["cached_tokens"] += 80
        self.usage["requests"] += 1
        self.request_usage.append(
            {
                "request": len(self.request_usage) + 1,
                "input_tokens": 100,
                "output_tokens": 5,
                "cached_tokens": 80,
            }
        )

        async def blocks() -> Any:
            yield TextBlock(text="judge")

        return blocks()


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


@patch("labrat.agent.providers.build_provider", return_value=MagicMock())
async def test_labrat_agent_driver_opts_into_rate_limit_fail_fast(
    _provider: MagicMock, tmp_path: Path
) -> None:
    """The benchmark driver keeps fail-fast: it sets raise_rate_limits on the ctx it owns."""
    _make_real_duckdb_fixture(tmp_path)
    captured: dict[str, Any] = {}

    async def fake_run_agent_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(final_text="ok", tool_calls=0, latency_seconds=0.0)

    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent")
    task = next(iter(suite.tasks()))
    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch_rl")
    assert "raise_rate_limits" not in captured  # the runner kwarg is gone
    ctx = captured.get("ctx")
    assert ctx is not None and ctx.raise_rate_limits is True


@patch("labrat.agent.providers.build_provider")
async def test_labrat_agent_driver_records_provider_token_usage(
    mock_build: MagicMock, tmp_path: Path
) -> None:
    """When the provider exposes per-trial token usage (codex), it lands in TrialResult.meta."""
    _make_real_duckdb_fixture(tmp_path)
    usage = {
        "input_tokens": 5000,
        "output_tokens": 100,
        "cached_tokens": 4200,
        "reasoning_tokens": 0,
        "requests": 8,
    }
    provider = MagicMock()
    provider.usage = usage
    provider.request_usage = [{"request": 1, "input_tokens": 5000}]
    mock_build.return_value = provider

    suite = DabSuite(
        dab_dir=tmp_path, driver="labrat-agent", agent_provider="codex", agent_model="gpt-5.5"
    )
    task = next(iter(suite.tasks()))

    async def fake_run_agent_task(**kwargs: Any) -> Any:
        return SimpleNamespace(final_text="42", tool_calls=8, latency_seconds=1.0)

    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch_usage")

    assert result.meta.get("usage") == usage
    assert result.meta.get("request_usage") == provider.request_usage


@patch("labrat.agent.providers.build_provider")
async def test_labrat_agent_uses_and_accounts_dedicated_classifier_provider(
    mock_build: MagicMock, tmp_path: Path
) -> None:
    _make_real_duckdb_fixture(tmp_path)
    main = MagicMock()
    main.usage = {
        "input_tokens": 1_000,
        "output_tokens": 100,
        "cached_tokens": 800,
        "reasoning_tokens": 90,
        "requests": 1,
    }
    main.request_usage = [{"request": 1, "model": "gpt-5.6-luna", "reasoning_effort": "max"}]
    classifier = MagicMock()
    classifier.usage = {
        "input_tokens": 2_000,
        "output_tokens": 200,
        "cached_tokens": 1_500,
        "reasoning_tokens": 20,
        "requests": 2,
    }
    classifier.request_usage = [
        {"request": 1, "model": "gpt-5.6-luna", "reasoning_effort": "low"},
        {"request": 2, "model": "gpt-5.6-luna", "reasoning_effort": "low"},
    ]
    mock_build.side_effect = [main, classifier]
    captured: dict[str, Any] = {}

    async def fake_run_agent_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(final_text="42", tool_calls=1, latency_seconds=1.0)

    suite = DabSuite(
        dab_dir=tmp_path,
        driver="labrat-agent",
        agent_provider="codex",
        agent_model="gpt-5.6-luna",
        agent_reasoning="max",
        llm_classify_model="gpt-5.6-luna",
        llm_classify_reasoning="low",
        llm_classify_concurrency=2,
    )
    task = next(iter(suite.tasks()))
    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        result = await suite.run_trial(
            task, trial_num=0, scratch_dir=tmp_path / "scratch_classifier"
        )

    assert mock_build.call_count == 2
    assert mock_build.call_args_list[0].kwargs["reasoning"] == "max"
    assert mock_build.call_args_list[1].kwargs["reasoning"] == "low"
    assert ":llm_classify:gpt-5.6-luna:low" in mock_build.call_args_list[1].kwargs["cache_key"]
    assert captured["llm_classify_provider"] is classifier
    assert captured["ctx"].llm_classify_concurrency == 2
    assert result.meta["usage"]["input_tokens"] == 3_000
    assert result.meta["usage"]["requests"] == 3
    request_usage = result.meta["request_usage"]
    assert request_usage[0]["reasoning_effort"] == "max"
    assert "usage_role" not in request_usage[0]
    assert [item["usage_role"] for item in request_usage[1:]] == [
        "llm_classify",
        "llm_classify",
    ]
    assert [item["request"] for item in request_usage] == [1, 2, 3]
    assert [item["provider_request"] for item in request_usage[1:]] == [1, 2]
    assert {item["reasoning_effort"] for item in request_usage[1:]} == {"low"}


@patch("labrat.agent.providers.build_provider")
async def test_labrat_agent_terminalizes_turn_budget_with_trace(
    mock_build: MagicMock, tmp_path: Path
) -> None:
    _make_real_duckdb_fixture(tmp_path)
    provider = MagicMock()
    provider.usage = {}
    provider.request_usage = []
    mock_build.return_value = provider

    async def fake_run_agent_task(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            final_text="",
            tool_calls=10,
            latency_seconds=1.0,
            turn_budget_exhausted=True,
        )

    suite = DabSuite(
        dab_dir=tmp_path,
        driver="labrat-agent",
        agent_max_turns=10,
    )
    task = next(iter(suite.tasks()))
    scratch = tmp_path / "turn-budget"
    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=scratch)

    expected = "[trial exhausted 10-turn budget without a final answer]"
    assert result.passed is False
    assert result.reason == "terminal:turn_budget"
    assert result.artifact == {"type": "text", "payload": expected}
    assert result.tool_calls == 10
    trace = [
        json.loads(line) for line in (scratch / "agent_tool_calls.jsonl").read_text().splitlines()
    ]
    assert trace[-1] == {
        "tool": "runner_turn_budget",
        "input": {"max_turns": 10},
        "ok": False,
        "output": expected,
        "latency_ms": 0.0,
    }


@patch("labrat.agent.providers.build_provider")
async def test_agent_authored_exhausted_prefix_is_a_normal_answer(
    mock_build: MagicMock, tmp_path: Path
) -> None:
    """An agent-AUTHORED answer starting with the turn-budget sentinel literal must
    NOT be classified terminal when the loop did not exhaust its budget — terminal
    classification is driven by the structured flag, never by string matching."""
    _make_real_duckdb_fixture(tmp_path)
    provider = MagicMock()
    provider.usage = {}
    provider.request_usage = []
    mock_build.return_value = provider

    authored = "[trial exhausted 10-turn budget without a final answer] — kidding; the answer is 7"

    async def fake_run_agent_task(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            final_text=authored,
            tool_calls=3,
            latency_seconds=1.0,
            turn_budget_exhausted=False,
        )

    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent", agent_max_turns=10)
    task = next(iter(suite.tasks()))
    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "authored")

    # The fixture validator passes everything, so a normal semantic answer scores.
    assert result.reason != "terminal:turn_budget"
    assert result.passed is True
    assert result.artifact == {"type": "text", "payload": authored}


@patch("labrat.agent.providers.build_provider")
async def test_agent_authored_exhausted_prefix_still_hits_contamination_backstop(
    mock_build: MagicMock, tmp_path: Path
) -> None:
    """The old string short-circuit ran BEFORE the contamination backstop; an
    agent-authored '[trial exhausted ...' answer with a leakage marker must be
    withdrawn as contaminated, not laundered into a terminal row."""
    _make_real_duckdb_fixture(tmp_path)
    provider = MagicMock()
    provider.usage = {}
    provider.request_usage = []
    mock_build.return_value = provider

    authored = "[trial exhausted 10-turn budget without a final answer] matches the ground truth"

    async def fake_run_agent_task(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            final_text=authored,
            tool_calls=3,
            latency_seconds=1.0,
            turn_budget_exhausted=False,
        )

    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent", agent_max_turns=10)
    task = next(iter(suite.tasks()))
    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "contaminated")

    assert result.passed is False
    assert (result.reason or "").startswith("contaminated:")


@patch("labrat.agent.providers.build_provider")
async def test_labrat_agent_driver_preserves_completed_usage_on_later_failure(
    mock_build: MagicMock, tmp_path: Path
) -> None:
    _make_real_duckdb_fixture(tmp_path)
    provider = MagicMock()
    provider.usage = {
        "input_tokens": 2000,
        "output_tokens": 50,
        "cached_tokens": 1500,
        "cache_write_tokens": 0,
        "reasoning_tokens": 20,
        "requests": 1,
    }
    provider.request_usage = [{"request": 1, "response_id": "resp_1"}]
    mock_build.return_value = provider
    suite = DabSuite(
        dab_dir=tmp_path,
        driver="labrat-agent",
        agent_provider="codex",
        agent_model="gpt-5.5",
    )
    task = next(iter(suite.tasks()))

    async def failing_run_agent_task(**_kwargs: Any) -> Any:
        raise TimeoutError("later turn timed out")

    with patch("labrat.agent.runner.run_agent_task", new=failing_run_agent_task):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch_fail")

    assert result.reason == "infra:timeout"
    assert result.meta["usage"]["input_tokens"] == 2000
    assert result.meta["request_usage"] == provider.request_usage


@patch("labrat.agent.providers.build_provider")
async def test_labrat_agent_driver_threads_ablation_config_and_touches_trace(
    mock_build: MagicMock, tmp_path: Path
) -> None:
    """Model settings stay intact while lever/ledger controls reach the agent.

    The per-trial trace is also present when the model makes zero tool calls.
    """
    _make_real_duckdb_fixture(tmp_path)
    provider = MagicMock()
    mock_build.return_value = provider
    captured: dict[str, Any] = {}

    async def fake_run_agent_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(final_text="42", tool_calls=0, latency_seconds=1.0)

    suite = DabSuite(
        dab_dir=tmp_path,
        driver="labrat-agent",
        agent_provider="codex",
        agent_model="gpt-5.5",
        agent_reasoning="high",
        agent_levers=False,
        agent_ledger=False,
    )
    task = next(iter(suite.tasks()))
    scratch = tmp_path / "scratch_ablation"

    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=scratch)

    assert result.tool_calls == 0
    mock_build.assert_called_once_with(
        "codex",
        "gpt-5.5",
        timeout=None,
        reasoning="high",
        cache_key=task.id,
    )
    assert captured["enable_ledger"] is False
    assert captured["ledger_dir"] == scratch
    assert "never answer from prior" not in captured["system_prompt"]
    trace = scratch / "agent_tool_calls.jsonl"
    assert trace.exists()
    assert trace.read_text() == ""


@patch("labrat.agent.providers.build_provider")
async def test_sol_ultra_enables_proactive_labrat_delegation_prompt(
    mock_build: MagicMock, tmp_path: Path
) -> None:
    _make_real_duckdb_fixture(tmp_path)
    mock_build.return_value = MagicMock()
    captured: dict[str, Any] = {}

    async def fake_run_agent_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(final_text="42", tool_calls=0, latency_seconds=1.0)

    suite = DabSuite(
        dab_dir=tmp_path,
        driver="labrat-agent",
        agent_provider="codex",
        agent_model="gpt-5.6-sol",
        agent_reasoning="ultra",
    )
    task = next(iter(suite.tasks()))
    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch_ultra")

    assert "Proactive multi-agent delegation is active" in captured["system_prompt"]


@patch("labrat.agent.providers.build_provider")
async def test_sol_max_does_not_enable_proactive_labrat_delegation_prompt(
    mock_build: MagicMock, tmp_path: Path
) -> None:
    _make_real_duckdb_fixture(tmp_path)
    mock_build.return_value = MagicMock()
    captured: dict[str, Any] = {}

    async def fake_run_agent_task(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(final_text="42", tool_calls=0, latency_seconds=1.0)

    suite = DabSuite(
        dab_dir=tmp_path,
        driver="labrat-agent",
        agent_provider="codex",
        agent_model="gpt-5.6-sol",
        agent_reasoning="max",
    )
    task = next(iter(suite.tasks()))
    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch_max")

    assert "Proactive multi-agent delegation is active" not in captured["system_prompt"]
    assert "dispatch_subagent" not in captured["system_prompt"]


@patch("labrat.agent.providers.build_provider", return_value=MagicMock())
async def test_labrat_agent_driver_threads_diversity_index_to_cartographer(
    _provider: MagicMock, tmp_path: Path
) -> None:
    """FIX 3: the labrat-agent driver must pass variant_seed=diversity_index or 0 to
    _run_cartographer, same as the claude-mcp driver — otherwise consensus sub-runs on
    this driver all share seed-0 Scent and the decorrelation mechanism is absent."""
    _make_real_duckdb_fixture(tmp_path)
    captured: dict[str, Any] = {}

    async def fake_run_cartographer(*args: Any, **kwargs: Any) -> Path:
        captured.update(kwargs)
        root = tmp_path / "scent"
        root.mkdir(exist_ok=True)
        return root

    async def fake_run_agent_task(**kwargs: Any) -> Any:
        return SimpleNamespace(final_text="ok", tool_calls=0, latency_seconds=0.0)

    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent", cartograph=True)
    task = next(iter(suite.tasks()))

    with (
        patch("labrat.eval.benchmarks.dab.suite._run_cartographer", new=fake_run_cartographer),
        patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task),
    ):
        await suite._run_trial_labrat_agent(
            task,
            tmp_path / "query_real1" / "db_config.yaml",
            tmp_path / "scratch",
            diversity_index=2,
        )

    assert captured.get("variant_seed") == 2


async def test_run_trial_isolates_agent_timeout_as_infra(tmp_path: Path) -> None:
    """A TimeoutError from the agent fails only this trial (infra:timeout), not the run."""
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent")
    task = next(iter(suite.tasks()))
    with patch.object(
        DabSuite,
        "_run_trial_labrat_agent",
        new=AsyncMock(side_effect=TimeoutError("claude --print timed out after 120s")),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")
    assert result.passed is False
    assert result.reason == "infra:timeout"


async def test_run_trial_can_terminalize_timeout_as_traced_failure(tmp_path: Path) -> None:
    """A declared bounded run records timeout as one trace-complete scored failure."""
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(
        dab_dir=tmp_path,
        driver="labrat-agent",
        agent_timeout=17,
        terminalize_timeouts=True,
    )
    task = next(iter(suite.tasks()))
    scratch = tmp_path / "terminal-timeout"
    with patch.object(
        DabSuite,
        "_run_trial_labrat_agent",
        new=AsyncMock(side_effect=TimeoutError()),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=scratch)

    assert result.passed is False
    assert result.reason == "terminal:timeout"
    assert result.artifact == {"type": "text", "payload": "[trial exceeded 17s timeout]"}
    trace_path = scratch / "agent_tool_calls.jsonl"
    trace = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert trace == [
        {
            "tool": "runner_timeout",
            "input": {"timeout_seconds": 17},
            "ok": False,
            "output": "[trial exceeded 17s timeout]",
            "latency_ms": pytest.approx(0.0, abs=100.0),
        }
    ]


async def test_consensus_promotes_trace_from_actual_retained_subrun(tmp_path: Path) -> None:
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent", consensus_k=2)
    task = next(iter(suite.tasks()))
    scratch = tmp_path / "consensus-trace"
    winning_trace = '{"tool":"run_sql","input":{},"ok":true,"output":"winner","latency_ms":1}\n'

    async def dispatch(
        _task: Any,
        _db_config_path: Path,
        subrun: Path,
        **_kwargs: Any,
    ) -> DriverOutcome:
        if subrun.name == "subrun0":
            raise RuntimeError("infra failure")
        (subrun / "agent_tool_calls.jsonl").write_text(winning_trace)
        return DriverOutcome("winner", 1, 0.1)

    with (
        patch.object(suite, "_dispatch_driver_once", new=dispatch),
        patch.object(suite, "_verify_llm_fn", return_value=AsyncMock(return_value="0")),
        patch(
            "labrat.agent.verification.consensus.choose_modal",
            new=AsyncMock(return_value=(0, False)),
        ),
    ):
        answer = await suite._run_trial_verified(
            task,
            Path(task.config["db_config_path"]),
            scratch,
        )

    assert answer[0] == "winner"
    assert (scratch / "agent_tool_calls.jsonl").read_text() == winning_trace


async def test_failed_argumentation_round_retains_prior_answer_and_trace(
    tmp_path: Path,
) -> None:
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(
        dab_dir=tmp_path,
        driver="labrat-agent",
        consensus_k=2,
        argue_rounds=1,
    )
    task = next(iter(suite.tasks()))
    scratch = tmp_path / "argumentation-atomic"
    prior_trace = '{"tool":"run_sql","input":{},"ok":true,"output":"prior","latency_ms":1}\n'
    tentative_trace = (
        '{"tool":"run_sql","input":{},"ok":true,"output":"tentative","latency_ms":1}\n'
    )
    calls: dict[str, int] = {}

    async def dispatch(
        _task: Any,
        _db_config_path: Path,
        subrun: Path,
        **_kwargs: Any,
    ) -> DriverOutcome:
        calls[subrun.name] = calls.get(subrun.name, 0) + 1
        if subrun.name == "subrun0" and calls[subrun.name] == 1:
            (subrun / "agent_tool_calls.jsonl").write_text(prior_trace)
            return DriverOutcome("primary-old", 1, 0.1)
        if subrun.name == "subrun1" and calls[subrun.name] == 1:
            (subrun / "agent_tool_calls.jsonl").write_text("")
            return DriverOutcome("other-old", 0, 0.1)
        if subrun.name in {"subrun0", "argue-round1-subrun0"}:
            (subrun / "agent_tool_calls.jsonl").write_text(tentative_trace)
            return DriverOutcome("primary-tentative", 1, 0.1)
        raise RuntimeError("later tentative rerun failed")

    with (
        patch.object(suite, "_dispatch_driver_once", new=dispatch),
        patch.object(suite, "_verify_llm_fn", return_value=AsyncMock(return_value="0")),
        patch(
            "labrat.agent.verification.consensus.choose_modal",
            new=AsyncMock(return_value=(0, True)),
        ),
    ):
        answer = await suite._run_trial_verified(
            task,
            Path(task.config["db_config_path"]),
            scratch,
        )

    assert answer[0] == "primary-old"
    assert (scratch / "agent_tool_calls.jsonl").read_text() == prior_trace
    assert (scratch / "subrun0" / "agent_tool_calls.jsonl").read_text() == prior_trace


async def test_consensus_judge_usage_is_included_in_trial_telemetry(tmp_path: Path) -> None:
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent", consensus_k=2)
    task = next(iter(suite.tasks()))
    judge = _UsageJudgeProvider()

    async def dispatch(
        _task: Any,
        _db_config_path: Path,
        subrun: Path,
        **_kwargs: Any,
    ) -> DriverOutcome:
        (subrun / "agent_tool_calls.jsonl").write_text("")
        return DriverOutcome("3", 0, 0.1)

    async def choose_modal(_answers: list[str], *, question: str, llm_fn: Any) -> tuple[int, bool]:
        await llm_fn(question)
        return (0, False)

    with (
        patch.object(suite, "_dispatch_driver_once", new=dispatch),
        patch("labrat.eval.benchmarks.dab.suite.build_provider", return_value=judge),
        patch("labrat.agent.verification.consensus.choose_modal", new=choose_modal),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "consensus")

    assert result.meta["usage"]["input_tokens"] == 100
    assert result.meta["usage"]["requests"] == 1
    assert result.meta["request_usage"] == [
        {
            "request": 1,
            "input_tokens": 100,
            "output_tokens": 5,
            "cached_tokens": 80,
            "usage_role": "verification",
        }
    ]


async def test_reverify_judge_usage_is_included_in_trial_telemetry(tmp_path: Path) -> None:
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent", reverify=True)
    task = next(iter(suite.tasks()))
    judge = _UsageJudgeProvider()

    async def dispatch(
        _task: Any,
        _db_config_path: Path,
        subrun: Path,
        **_kwargs: Any,
    ) -> DriverOutcome:
        (subrun / "agent_tool_calls.jsonl").write_text("")
        return DriverOutcome("3", 0, 0.1)

    async def answers_agree(_first: str, _second: str, *, question: str, llm_fn: Any) -> bool:
        await llm_fn(question)
        return True

    with (
        patch.object(suite, "_dispatch_driver_once", new=dispatch),
        patch("labrat.eval.benchmarks.dab.suite.build_provider", return_value=judge),
        patch("labrat.agent.verification.agreement.answers_agree", new=answers_agree),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "reverify")

    assert result.meta["usage"]["input_tokens"] == 100
    assert result.meta["usage"]["requests"] == 1
    assert result.meta["request_usage"][0]["usage_role"] == "verification"


async def test_verification_usage_delta_does_not_double_count_cumulative_provider(
    tmp_path: Path,
) -> None:
    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent")
    suite._last_usage = None
    suite._last_request_usage = []
    judge = _UsageJudgeProvider()

    with patch("labrat.eval.benchmarks.dab.suite.build_provider", return_value=judge):
        llm_fn = suite._verify_llm_fn()
        await llm_fn("first")
        await llm_fn("second")

    assert suite._last_usage is not None
    assert suite._last_usage["input_tokens"] == 200
    assert suite._last_usage["requests"] == 2
    assert len(suite._last_request_usage) == 2


async def test_run_trial_classifies_http_429_and_sanitizes_reset_metadata(
    tmp_path: Path,
) -> None:
    import httpx

    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent")
    task = next(iter(suite.tasks()))
    request = httpx.Request("POST", "https://chatgpt.com/backend-api/codex/responses")
    response = httpx.Response(
        429,
        request=request,
        json={
            "error": {
                "type": "usage_limit_reached",
                "message": "raw provider message must not be persisted",
                "plan_type": "prolite",
                "resets_at": 1783767751,
                "resets_in_seconds": 321,
            }
        },
    )
    error = httpx.HTTPStatusError("429 Too Many Requests", request=request, response=response)

    with patch.object(
        DabSuite,
        "_run_trial_labrat_agent",
        new=AsyncMock(side_effect=error),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch_429")

    assert result.passed is False
    assert result.reason == "infra:rate_limit"
    assert result.meta["rate_limit"] == {
        "resets_at": 1783767751,
        "resets_in_seconds": 321,
    }
    assert "raw provider message" not in result.model_dump_json()
    assert "plan_type" not in result.model_dump_json()


async def test_run_trial_classifies_cli_api_error_429_as_rate_limit(tmp_path: Path) -> None:
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="raw-bash")
    task = next(iter(suite.tasks()))

    with patch.object(
        DabSuite,
        "_run_trial_raw_bash",
        new=AsyncMock(return_value=DriverOutcome("API Error: 429 Too Many Requests", 0, 0.1)),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")

    assert result.reason == "infra:rate_limit"


async def test_rate_limit_exception_persists_only_sanitized_allowlisted_data(
    tmp_path: Path,
) -> None:
    from labrat.agent.providers.base import RATE_LIMIT_MESSAGE, RateLimitError

    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent")
    task = next(iter(suite.tasks()))
    error = RateLimitError(
        "raw provider quota detail must not persist",
        rate_limit={
            "resets_at": 1783767751,
            "resets_in_seconds": 321,
            "plan_type": "secret-plan",
        },
    )

    with patch.object(
        DabSuite,
        "_run_trial_labrat_agent",
        new=AsyncMock(side_effect=error),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")

    assert result.reason == "infra:rate_limit"
    assert result.meta["rate_limit"] == {
        "resets_at": 1783767751,
        "resets_in_seconds": 321,
    }
    assert result.artifact["payload"] == f"RateLimitError: {RATE_LIMIT_MESSAGE}"
    persisted = result.model_dump_json()
    assert "raw provider quota detail" not in persisted
    assert "secret-plan" not in persisted


async def test_run_trial_isolates_generic_agent_error_as_infra(tmp_path: Path) -> None:
    """Any other agent exception is recorded as infra:agent_error, not propagated."""
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent")
    task = next(iter(suite.tasks()))
    with patch.object(
        DabSuite, "_run_trial_labrat_agent", new=AsyncMock(side_effect=RuntimeError("boom"))
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")
    assert result.passed is False
    assert result.reason == "infra:agent_error"


def test_labrat_agent_prompt_surfaces_new_tools_and_discipline(tmp_path: Path) -> None:
    """The DAB labrat-agent system prompt lists profile_dataset + load_file and the
    profile->plan->verify discipline, while keeping the single-answer instruction."""
    from labrat.eval.benchmarks.dab.env import build_dab_task_env
    from labrat.eval.benchmarks.dab.suite import _build_labrat_agent_system_prompt

    _make_synthetic_fixture(tmp_path)
    env = build_dab_task_env(tmp_path / "query_synthetic1" / "db_config.yaml")
    prompt = _build_labrat_agent_system_prompt(env)
    assert "profile_dataset" in prompt
    assert "load_file" in prompt
    assert "link_schema" in prompt  # grounding tool #25
    assert "verify_join" in prompt  # grounding tool #25
    assert "profile_dataset first" in prompt  # the prescriptive discipline
    assert "single plain answer" in prompt  # DAB scoring contract preserved


def test_detect_contamination_flags_markers() -> None:
    from labrat.eval.benchmarks.dab.suite import _detect_contamination

    assert _detect_contamination("The ground truth from validate.py is 42") == "answer_key"
    assert _detect_contamination("read ground_truth.csv") == "answer_key"
    assert _detect_contamination("I used load_dataset('ag_news') for labels") == "external_dataset"
    assert _detect_contamination("from the HuggingFace dataset") == "external_dataset"
    assert _detect_contamination("the answer is 3 from a normal SQL aggregation") is None

    # Natural-language ground-truth assertions from subagent summaries — the gap the
    # DAB maintainers caught (PR #54): the leak happens inside a Task subagent whose
    # internal calls aren't in the transcript, and its summary asserts gold-answer
    # knowledge in plain English (no underscore filename, no validate.py mention).
    assert _detect_contamination("confirmed from the ground truth file") == "answer_key"
    assert _detect_contamination("matches the ground truth answer 2020") == "answer_key"
    assert _detect_contamination("this matches the ground truth") == "answer_key"
    assert _detect_contamination("verified against the answer key") == "answer_key"
    assert _detect_contamination("equals the gold answer") == "answer_key"
    # Don't over-trigger: ordinary ML/eval vocabulary that isn't a leak claim.
    assert _detect_contamination("I built a confusion matrix against my predictions") is None


async def test_run_trial_marks_contaminated_answer_as_not_passed(tmp_path: Path) -> None:
    """An answer that would score correct but shows leakage markers is withdrawn, not passed."""
    _make_synthetic_fixture(tmp_path)  # validator passes when '3' in output
    suite = DabSuite(dab_dir=tmp_path)
    task = next(iter(suite.tasks()))
    # Contains '3' (validator would pass) AND a leakage marker.
    leaked = "Reading validate.py, the ground truth is 3."
    with patch(
        "labrat.eval.benchmarks.dab.suite._invoke_agent",
        new=AsyncMock(return_value={"final_text": leaked, "tool_calls": 9}),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")

    assert result.passed is False
    assert (result.reason or "").startswith("contaminated:")


async def test_claude_mcp_driver_sandboxes_tools_and_cwd(tmp_path: Path) -> None:
    """The claude-mcp subprocess must restrict tools to the MCP server and run in
    an isolated scratch cwd so the benchmark repo (answer keys) is unreachable."""
    _make_real_duckdb_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="claude-mcp")
    task = next(iter(suite.tasks()))

    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=b'{"result": "answer 1", "num_turns": 2}',
            stderr=b"",
        )

    scratch = tmp_path / "scratch_mcp"
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", new=fake_run),
    ):
        await suite.run_trial(task, trial_num=0, scratch_dir=scratch)

    cmd = captured["cmd"]
    joined = " ".join(cmd)
    # Tools restricted to the MCP server; native tools explicitly blocked.
    assert "--allowedTools" in cmd
    assert "mcp__labrat" in joined
    assert "--disallowedTools" in cmd
    for blocked in ("Bash", "WebFetch", "Task"):
        assert blocked in joined, f"{blocked} not in disallowed tools"
    # Subprocess runs in the isolated scratch cwd, not the benchmark repo.
    assert str(captured["kwargs"].get("cwd")) == str(scratch)

    # The MCP server is told where to write audit-grade per-call traces.
    import json as _json

    mcp_config = _json.loads((scratch / "mcp-config.json").read_text())
    server_env = mcp_config["mcpServers"]["labrat"]["env"]
    assert server_env.get("LABRAT_MCP_LOG_DIR")
    # classify backend flows through to the MCP server (default "llm" here).
    assert server_env.get("LABRAT_MCP_LLM_CLASSIFY_BACKEND") == "llm"


async def test_claude_mcp_passes_local_embed_classify_backend(tmp_path: Path) -> None:
    """A local-embed configured run must tell the MCP server to use it — the only
    classification backend that works over MCP (no llm_fn)."""
    import json as _json
    import subprocess
    from unittest.mock import patch

    _make_real_duckdb_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="claude-mcp", llm_classify_backend="local-embed")
    task = next(iter(suite.tasks()))
    scratch = tmp_path / "scratch_backend"

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            cmd, 0, stdout=b'{"result": "x", "num_turns": 1}', stderr=b""
        )

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", new=fake_run),
    ):
        await suite.run_trial(task, trial_num=0, scratch_dir=scratch)

    server_env = _json.loads((scratch / "mcp-config.json").read_text())["mcpServers"]["labrat"][
        "env"
    ]
    assert server_env["LABRAT_MCP_LLM_CLASSIFY_BACKEND"] == "local-embed"


async def test_claude_mcp_maps_agent_reasoning_to_cli_effort(tmp_path: Path) -> None:
    """On the claude-mcp path agent_reasoning must map to the CLI's --effort flag
    (the codex provider consumes it differently); None omits the flag entirely so
    the CLI keeps its default."""
    import subprocess

    _make_real_duckdb_fixture(tmp_path)
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout=b'{"result": "x", "num_turns": 1}', stderr=b""
        )

    # High-effort run: --effort high is appended.
    suite_hi = DabSuite(dab_dir=tmp_path, driver="claude-mcp", agent_reasoning="high")
    task = next(iter(suite_hi.tasks()))
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", new=fake_run),
    ):
        await suite_hi.run_trial(task, trial_num=0, scratch_dir=tmp_path / "s_hi")
    cmd = captured["cmd"]
    assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "high"

    # Default run: no --effort flag, so the CLI keeps its own default (medium).
    suite_def = DabSuite(dab_dir=tmp_path, driver="claude-mcp")
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", new=fake_run),
    ):
        await suite_def.run_trial(task, trial_num=0, scratch_dir=tmp_path / "s_def")
    assert "--effort" not in captured["cmd"]


async def test_claude_mcp_zero_tool_attempt_creates_empty_trace(tmp_path: Path) -> None:
    _make_real_duckdb_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="claude-mcp")
    task = next(iter(suite.tasks()))
    scratch = tmp_path / "zero-tool-mcp"

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch(
            "subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout=b'{"result": "answer", "num_turns": 1}',
                stderr=b"",
            ),
        ),
    ):
        await suite.run_trial(task, trial_num=0, scratch_dir=scratch)

    trace = scratch / "mcp_tool_calls.jsonl"
    assert trace.is_file()
    assert trace.read_text() == ""


async def test_claude_mcp_retry_truncates_prior_attempt_trace(tmp_path: Path) -> None:
    _make_real_duckdb_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="claude-mcp")
    task = next(iter(suite.tasks()))
    scratch = tmp_path / "retry-mcp"
    scratch.mkdir()
    trace = scratch / "mcp_tool_calls.jsonl"
    trace.write_text('{"tool":"run_sql","input":{},"ok":true,"output":"old","latency_ms":1}\n')

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch(
            "subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout=b'{"result": "answer", "num_turns": 1}',
                stderr=b"",
            ),
        ),
    ):
        await suite.run_trial(task, trial_num=0, scratch_dir=scratch)

    assert trace.read_text() == ""


async def test_claude_mcp_mcp_config_path_absolute_under_relative_scratch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """--mcp-config must be absolute: with cwd set to the scratch dir, a relative
    config path is re-resolved by the claude CLI against cwd and doubles (the live
    agnews smoke caught this). Mimic the real harness, which passes a repo-relative
    scratch dir."""
    import os

    _make_real_duckdb_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path, driver="claude-mcp")
    task = next(iter(suite.tasks()))

    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=b'{"result": "x", "num_turns": 1}', stderr=b"")

    # Run from a base dir and hand run_trial a RELATIVE scratch path.
    monkeypatch.chdir(tmp_path)
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", new=fake_run),
    ):
        await suite.run_trial(task, trial_num=0, scratch_dir=Path("rel_scratch/agnews_1__trial0"))

    cmd = captured["cmd"]
    cfg_arg = cmd[cmd.index("--mcp-config") + 1]
    assert os.path.isabs(cfg_arg), f"--mcp-config must be absolute, got {cfg_arg!r}"
    # And it must actually point at the file that was written.
    assert Path(cfg_arg).exists()


async def test_claude_mcp_subprocess_timeout_default_and_override(tmp_path: Path) -> None:
    """claude-mcp per-trial wall-clock defaults to 1200s and honours --agent-timeout."""
    _make_real_duckdb_fixture(tmp_path)
    task = next(iter(DabSuite(dab_dir=tmp_path).tasks()))
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=b'{"result": "x", "num_turns": 1}', stderr=b"")

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", new=fake_run),
    ):
        await DabSuite(dab_dir=tmp_path, driver="claude-mcp").run_trial(
            task, trial_num=0, scratch_dir=tmp_path / "s1"
        )
    assert captured["timeout"] == 1200

    captured.clear()
    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("subprocess.run", new=fake_run),
    ):
        await DabSuite(dab_dir=tmp_path, driver="claude-mcp", agent_timeout=900).run_trial(
            task, trial_num=0, scratch_dir=tmp_path / "s2"
        )
    assert captured["timeout"] == 900


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


@patch("labrat.agent.providers.build_provider")
async def test_labrat_agent_writes_opening_prompt_file(
    mock_build: MagicMock, tmp_path: Path
) -> None:
    _make_real_duckdb_fixture(tmp_path)
    provider = MagicMock()
    provider.usage = {}
    provider.request_usage = []
    mock_build.return_value = provider

    async def fake_run_agent_task(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            final_text="42",
            tool_calls=0,
            latency_seconds=1.0,
            turn_budget_exhausted=False,
        )

    suite = DabSuite(dab_dir=tmp_path, driver="labrat-agent", agent_taxonomy=True)
    task = next(iter(suite.tasks()))
    scratch = tmp_path / "prompt-file"
    with patch("labrat.agent.runner.run_agent_task", new=fake_run_agent_task):
        await suite.run_trial(task, trial_num=0, scratch_dir=scratch)

    prompt_file = scratch / "opening_prompt.txt"
    assert prompt_file.is_file()
    content = prompt_file.read_text()
    assert "=== SYSTEM PROMPT ===" in content
    assert "=== OPENING USER MESSAGE ===" in content
    assert task.prompt in content
    assert "Answer discipline:" in content  # taxonomy lever reflected in disclosure
