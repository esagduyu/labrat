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
