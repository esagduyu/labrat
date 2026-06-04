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
    assert "profile_dataset first" in prompt  # the prescriptive discipline
    assert "single plain answer" in prompt  # DAB scoring contract preserved


def test_detect_contamination_flags_markers() -> None:
    from labrat.eval.benchmarks.dab.suite import _detect_contamination

    assert _detect_contamination("The ground truth from validate.py is 42") == "answer_key"
    assert _detect_contamination("read ground_truth.csv") == "answer_key"
    assert _detect_contamination("I used load_dataset('ag_news') for labels") == "external_dataset"
    assert _detect_contamination("from the HuggingFace dataset") == "external_dataset"
    assert _detect_contamination("the answer is 3 from a normal SQL aggregation") is None


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
