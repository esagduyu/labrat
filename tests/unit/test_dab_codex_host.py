"""Focused tests for the pure diagnostic native-Codex host core."""

from __future__ import annotations

import asyncio
import json
import stat
import tomllib
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, cast

import pytest

import labrat.eval.benchmarks.dab.codex_host as codex_host_module
from labrat.eval.benchmarks.dab.codex_host import (
    CodexAuditError,
    CodexHostConfig,
    CodexInfrastructureError,
    McpLaunch,
    NativeRunResult,
    build_codex_command,
    parse_codex_events,
    run_codex,
)


def _config(
    tmp_path: Path,
    *,
    mcp: bool = True,
    model: str = "gpt-5.6-luna",
    effort: str = "low",
    mcp_log_dir: Path | None = None,
) -> CodexHostConfig:
    (tmp_path / "repo").mkdir(exist_ok=True)
    (tmp_path / "workspace").mkdir(exist_ok=True)
    (tmp_path / "source-home").mkdir(exist_ok=True)
    (tmp_path / "source-home" / "auth.json").write_text('{"token":"auth"}', encoding="utf-8")
    (tmp_path / "home").mkdir(exist_ok=True)
    launch = (
        McpLaunch(
            command=str(tmp_path / "bin" / "python"),
            args=("-m", "labrat.mcp.server"),
            cwd=tmp_path / "repo",
            env=(
                (
                    "LABRAT_MCP_CONNECTIONS",
                    json.dumps(
                        {
                            "main": {
                                "db_type": "duckdb",
                                "db_path": str(tmp_path / "db.duckdb"),
                                "read_only": True,
                            }
                        }
                    ),
                ),
                ("LABRAT_MCP_PRIMARY", "main"),
                ("LABRAT_MCP_LOG_DIR", str(mcp_log_dir or tmp_path / "artifacts")),
                ("LABRAT_MCP_POLICY_PATH", str(tmp_path / "policy.json")),
            ),
            enabled_tools=("list_tables", "run_sql"),
        )
        if mcp
        else None
    )
    return CodexHostConfig(
        executable=tmp_path / "bin" / "codex",
        expected_version="0.144.1",
        model=model,
        reasoning_effort=effort,
        source_codex_home=tmp_path / "source-home",
        codex_home=tmp_path / "home",
        workspace_dir=tmp_path / "workspace",
        artifact_dir=tmp_path / "artifacts",
        timeout_seconds=120,
        mcp=launch,
    )


@pytest.mark.parametrize(
    "record_type",
    [McpLaunch, CodexHostConfig, NativeRunResult],
)
def test_public_records_are_frozen_dataclasses(record_type: type[object]) -> None:
    assert is_dataclass(record_type)
    assert cast(Any, record_type).__dataclass_params__.frozen is True


def test_infrastructure_error_has_sanitized_typed_fields() -> None:
    error = CodexInfrastructureError(
        "rate_limit",
        meta={
            "resets_at": 123,
            "resets_in_seconds": "raw stderr",
            "secret": "do-not-retain",
        },
    )
    assert str(error) == "Native Codex rate_limit failure"
    assert error.reason == "rate_limit"
    assert error.meta == {"resets_at": 123}


def test_build_command_is_locked_down_and_injects_exact_mcp_config(tmp_path: Path) -> None:
    command = build_codex_command(_config(tmp_path))
    joined = " ".join(command)

    assert command[:2] == [str(tmp_path / "bin" / "codex"), "exec"]
    for required in (
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "--json",
        "--sandbox",
        "read-only",
    ):
        assert required in command
    assert "--ephemeral" in command
    assert "--yolo" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert command[-1] == "-"
    assert command[command.index("--cd") + 1] == str(tmp_path / "workspace")
    assert command[command.index("--output-last-message") + 1] == str(
        tmp_path / "artifacts" / "final_answer.txt"
    )
    for feature in (
        "shell_tool",
        "unified_exec",
        "apps",
        "plugins",
        "browser_use",
        "computer_use",
        "image_generation",
        "multi_agent",
        "goals",
        "hooks",
        "standalone_web_search",
    ):
        assert f"--disable {feature}" in joined
    assert f'mcp_servers.labrat.command="{tmp_path}/bin/python"' in command
    assert 'mcp_servers.labrat.args=["-m","labrat.mcp.server"]' in command
    assert 'mcp_servers.labrat.enabled_tools=["list_tables","run_sql"]' in command
    assert any(value.startswith("mcp_servers.labrat.env={") for value in command)


def test_build_command_without_mcp_has_no_server_configuration(tmp_path: Path) -> None:
    command = build_codex_command(_config(tmp_path, mcp=False))
    assert not any("mcp_servers." in value for value in command)


def test_build_command_emits_parseable_toml_for_unicode_and_injection_text(
    tmp_path: Path,
) -> None:
    hostile = '😀\n" -c injected=true'
    artifact_dir = tmp_path / hostile
    config = _config(tmp_path, mcp_log_dir=artifact_dir)
    config = CodexHostConfig(
        **{
            **config.__dict__,
            "artifact_dir": artifact_dir,
        }
    )
    command = build_codex_command(config)
    overrides = [command[index + 1] for index, value in enumerate(command) if value == "-c"]
    for override in overrides:
        tomllib.loads(override)
    assert not any(value == "injected=true" for value in command)


def test_build_command_requires_mcp_trace_in_artifact_directory(tmp_path: Path) -> None:
    config = _config(tmp_path, mcp_log_dir=tmp_path / "outside")
    with pytest.raises(ValueError, match="trace directory"):
        build_codex_command(config)


def test_build_command_rejects_secret_capable_mcp_environment(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.mcp is not None
    unsafe = CodexHostConfig(
        **{
            **config.__dict__,
            "mcp": McpLaunch(
                command=config.mcp.command,
                args=config.mcp.args,
                cwd=config.mcp.cwd,
                env=(("DATABASE_PASSWORD", "secret"),),
                enabled_tools=config.mcp.enabled_tools,
            ),
        }
    )
    with pytest.raises(ValueError, match="safe schema"):
        build_codex_command(unsafe)

    environment = dict(config.mcp.env)
    environment["LABRAT_MCP_CONNECTIONS"] = json.dumps(
        {
            "main": {
                "db_type": "duckdb",
                "db_path": str(tmp_path / "db.duckdb"),
                "read_only": True,
                "api_key": "hunter2",
            }
        }
    )
    unsafe_connection = CodexHostConfig(
        **{
            **config.__dict__,
            "mcp": McpLaunch(
                command=config.mcp.command,
                args=config.mcp.args,
                cwd=config.mcp.cwd,
                env=tuple(environment.items()),
                enabled_tools=config.mcp.enabled_tools,
            ),
        }
    )
    with pytest.raises(ValueError, match="read-only DuckDB"):
        build_codex_command(unsafe_connection)


def test_build_command_requires_pinned_version_and_empty_workspace(tmp_path: Path) -> None:
    config = _config(tmp_path)
    wrong_version = CodexHostConfig(**{**config.__dict__, "expected_version": "0.999.0"})
    with pytest.raises(ValueError, match="pinned"):
        build_codex_command(wrong_version)

    (config.workspace_dir / "unexpected.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        build_codex_command(config)


def test_build_command_rejects_overlapping_and_symlinked_private_paths(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    nested = CodexHostConfig(
        **{**config.__dict__, "artifact_dir": config.workspace_dir / "artifacts"}
    )
    with pytest.raises(ValueError, match="overlap"):
        build_codex_command(nested)

    config.codex_home.rmdir()
    config.codex_home.symlink_to(config.workspace_dir, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        build_codex_command(config)


@pytest.mark.parametrize(
    ("model", "effort"),
    [
        ("gpt-5.6-luna", "max"),
        ("gpt-5.6-terra", "ultra"),
        ("gpt-5.6-sol", "ultra"),
    ],
)
def test_build_command_accepts_supported_model_effort_pairs(
    tmp_path: Path, model: str, effort: str
) -> None:
    command = build_codex_command(_config(tmp_path, model=model, effort=effort))
    assert model in command
    assert f'model_reasoning_effort="{effort}"' in command


@pytest.mark.parametrize(
    ("model", "effort"),
    [("gpt-5.6-moon", "low"), ("gpt-5.6-luna", "ultra")],
)
def test_build_command_rejects_unsupported_model_effort_pairs(
    tmp_path: Path, model: str, effort: str
) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        build_codex_command(_config(tmp_path, model=model, effort=effort))


def _event_lines(*records: dict[str, object]) -> list[str]:
    return [json.dumps(record) for record in records]


def _zero_tool_events() -> list[str]:
    return _event_lines(
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": "answer"},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 8_517,
                "cached_input_tokens": 6_912,
                "output_tokens": 9,
                "reasoning_output_tokens": 0,
            },
        },
    )


def test_parse_observed_zero_tool_jsonl_shape() -> None:
    result = parse_codex_events(_zero_tool_events(), enabled_tools=())
    assert result.final_text == "answer"
    assert result.tool_calls == 0
    assert result.usage == {
        "input_tokens": 8_517,
        "cached_input_tokens": 6_912,
        "output_tokens": 9,
        "reasoning_output_tokens": 0,
    }
    assert [field.name for field in fields(result)] == [
        "final_text",
        "tool_calls",
        "latency_seconds",
        "usage",
    ]


def test_parse_two_completed_mcp_calls_and_final_message() -> None:
    lines = _event_lines(
        {"type": "thread.started", "thread_id": "thread-2"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "r", "type": "reasoning", "text": "hidden"},
        },
        {
            "type": "item.started",
            "item": {
                "id": "m1",
                "type": "mcp_tool_call",
                "server": "labrat",
                "tool": "list_tables",
                "arguments": {},
                "status": "in_progress",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "m1",
                "type": "mcp_tool_call",
                "server": "labrat",
                "tool": "list_tables",
                "arguments": {},
                "status": "completed",
            },
        },
        {
            "type": "item.started",
            "item": {
                "id": "m2",
                "type": "mcp_tool_call",
                "server": "labrat",
                "tool": "run_sql",
                "arguments": {"query": "SELECT 1"},
                "status": "in_progress",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "m2",
                "type": "mcp_tool_call",
                "server": "labrat",
                "tool": "run_sql",
                "arguments": {"query": "SELECT 1"},
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {"id": "a", "type": "agent_message", "text": "1"},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 64,
                "output_tokens": 10,
                "reasoning_output_tokens": 4,
            },
        },
    )
    result = parse_codex_events(lines, enabled_tools=("list_tables", "run_sql"))
    assert result.tool_calls == 2


@pytest.mark.parametrize(
    "mutated",
    [
        {"type": "item.completed", "item": {"type": "command_execution"}},
        {"type": "mystery.event"},
        {"type": "turn.failed"},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1,
                "cached_input_tokens": 2,
                "output_tokens": 0,
                "reasoning_output_tokens": 0,
            },
        },
    ],
)
def test_parse_rejects_forbidden_or_malformed_events(mutated: dict[str, object]) -> None:
    lines = _zero_tool_events()
    lines[-1] = json.dumps(mutated)
    with pytest.raises(CodexAuditError):
        parse_codex_events(lines, enabled_tools=())


def test_parse_requires_ordered_lifecycle_and_audits_started_mcp_items() -> None:
    missing_turn = _zero_tool_events()
    del missing_turn[1]
    with pytest.raises(CodexAuditError, match="lifecycle"):
        parse_codex_events(missing_turn, enabled_tools=())

    forbidden_start = _event_lines(
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "turn.started"},
        {
            "type": "item.started",
            "item": {
                "id": "m",
                "type": "mcp_tool_call",
                "server": "other",
                "tool": "run_sql",
                "arguments": {},
                "status": "in_progress",
            },
        },
    )
    with pytest.raises(CodexAuditError, match="server"):
        parse_codex_events(forbidden_start, enabled_tools=("run_sql",))


def test_parse_counts_failed_mcp_calls_and_rejects_unfinished_calls() -> None:
    common = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "turn.started"},
        {
            "type": "item.started",
            "item": {
                "id": "m",
                "type": "mcp_tool_call",
                "server": "labrat",
                "tool": "run_sql",
                "arguments": {"query": "SELECT 1"},
                "status": "in_progress",
            },
        },
    ]
    failed = [
        *common,
        {
            "type": "item.completed",
            "item": {
                "id": "m",
                "type": "mcp_tool_call",
                "server": "labrat",
                "tool": "run_sql",
                "arguments": {"query": "SELECT 1"},
                "status": "failed",
            },
        },
        {
            "type": "item.completed",
            "item": {"id": "a", "type": "agent_message", "text": "recovered"},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
                "reasoning_output_tokens": 0,
            },
        },
    ]
    parsed = parse_codex_events(_event_lines(*failed), enabled_tools=("run_sql",))
    assert parsed.tool_calls == 1

    unfinished = [
        *common,
        {
            "type": "item.completed",
            "item": {"id": "a", "type": "agent_message", "text": "answer"},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
                "reasoning_output_tokens": 0,
            },
        },
    ]
    with pytest.raises(CodexAuditError, match="incomplete"):
        parse_codex_events(_event_lines(*unfinished), enabled_tools=("run_sql",))


def test_native_result_copies_aggregate_usage_on_construction() -> None:
    usage = {"input_tokens": 10}
    result = NativeRunResult("answer", 0, 0.1, usage)
    usage["input_tokens"] = 0
    assert result.usage == {"input_tokens": 10}


def test_malformed_json_does_not_retain_raw_text_as_exception_cause() -> None:
    with pytest.raises(CodexAuditError) as raised:
        parse_codex_events(['{"secret":"raw token"'], enabled_tools=())
    assert raised.value.__cause__ is None


class _FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        callback: Any = None,
        timeout: bool = False,
        cancelled: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout_bytes = stdout
        self.stderr_bytes = stderr
        self.callback = callback
        self.timeout = timeout
        self.cancelled = cancelled
        self.input: bytes | None = None
        self.terminated = False
        self.killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.input = input
        if self.timeout:
            raise TimeoutError
        if self.cancelled:
            raise asyncio.CancelledError
        if self.callback is not None:
            self.callback()
        return self.stdout_bytes, self.stderr_bytes

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def _runner_events(*, mcp: bool = False, answer: str = "answer") -> bytes:
    records: list[dict[str, object]] = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "turn.started"},
    ]
    if mcp:
        for item_id, tool, arguments in (
            ("m1", "list_tables", {}),
            ("m2", "run_sql", {"query": "SELECT 1"}),
        ):
            base = {
                "id": item_id,
                "type": "mcp_tool_call",
                "server": "labrat",
                "tool": tool,
                "arguments": arguments,
            }
            records.extend(
                (
                    {"type": "item.started", "item": {**base, "status": "in_progress"}},
                    {"type": "item.completed", "item": {**base, "status": "completed"}},
                )
            )
    records.extend(
        (
            {
                "type": "item.completed",
                "item": {"id": "answer", "type": "agent_message", "text": answer},
            },
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 64,
                    "output_tokens": 10,
                    "reasoning_output_tokens": 4,
                },
            },
        )
    )
    return b"".join(json.dumps(record).encode() + b"\n" for record in records)



def _install_fake_processes(
    monkeypatch: pytest.MonkeyPatch,
    config: CodexHostConfig,
    *,
    events: bytes | None = None,
    answer: str = "answer",
    trace: tuple[dict[str, object], ...] = (),
    version: bytes = b"codex-cli 0.144.1\n",
    exec_returncode: int = 0,
    exec_stderr: bytes = b"",
    timeout: bool = False,
    cancelled: bool = False,
) -> tuple[list[tuple[tuple[str, ...], dict[str, Any]]], list[_FakeProcess]]:
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    processes: list[_FakeProcess] = []

    def finish_exec() -> None:
        (config.artifact_dir / "final_answer.txt").write_text(answer, encoding="utf-8")
        trace_path = config.artifact_dir / "mcp_tool_calls.jsonl"
        trace_path.write_text(
            "".join(json.dumps(record) + "\n" for record in trace), encoding="utf-8"
        )

    async def create(*argv: str, **kwargs: Any) -> _FakeProcess:
        calls.append((argv, kwargs))
        if argv[1:] == ("--version",):
            process = _FakeProcess(stdout=version)
        else:
            process = _FakeProcess(
                returncode=exec_returncode,
                stdout=_runner_events() if events is None else events,
                stderr=exec_stderr,
                callback=finish_exec if exec_returncode == 0 and not timeout else None,
                timeout=timeout,
                cancelled=cancelled,
            )
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    return calls, processes


@pytest.mark.asyncio
async def test_run_codex_success_is_auth_only_scrubbed_and_trace_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, mcp=False)
    (config.source_codex_home / "config.toml").write_text("secret=true", encoding="utf-8")
    (config.source_codex_home / "plugins").mkdir()
    calls, processes = _install_fake_processes(monkeypatch, config)
    parent = {
        "PATH": "/safe/bin",
        "HOME": "/safe/home",
        "TMPDIR": "/safe/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "OPENAI_API_KEY": "secret",
        "CODEX_API_KEY": "secret",
        "PYTHONPATH": "/unsafe",
        "OTHER": "drop",
    }

    result = await run_codex("private prompt", config, parent_env=parent)

    assert result.final_text == "answer"
    assert result.usage == {
        "input_tokens": 100,
        "cached_input_tokens": 64,
        "output_tokens": 10,
        "reasoning_output_tokens": 4,
    }
    assert calls[0][0] == (str(config.executable), "--version")
    assert calls[1][0][-1] == "-"
    assert processes[1].input == b"private prompt"
    assert calls[1][1]["env"] == {
        "PATH": "/safe/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(config.codex_home),
        "TMPDIR": str(config.codex_home / "tmp"),
        "CODEX_HOME": str(config.codex_home),
    }
    assert calls[1][1]["cwd"] == str(config.workspace_dir)
    assert calls[1][1]["start_new_session"] is True
    assert tuple(config.codex_home.iterdir()) == ()
    assert stat.S_IMODE(config.artifact_dir.stat().st_mode) == 0o700
    for artifact in config.artifact_dir.iterdir():
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert {artifact.name for artifact in config.artifact_dir.iterdir()} == {
        "codex_events.jsonl",
        "final_answer.txt",
        "mcp_tool_calls.jsonl",
    }
    assert (config.artifact_dir / "codex_events.jsonl").read_bytes() == _runner_events()
    assert (config.artifact_dir / "final_answer.txt").read_text() == "answer"
    assert (config.artifact_dir / "mcp_tool_calls.jsonl").read_text() == ""


@pytest.mark.asyncio
async def test_run_codex_counts_two_mcp_calls_and_preserves_raw_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    trace = (
        {"tool": "list_tables", "input": {}, "ok": True},
        {"tool": "run_sql", "input": {"query": "SELECT 1"}, "ok": True},
    )
    _install_fake_processes(monkeypatch, config, events=_runner_events(mcp=True), trace=trace)

    result = await run_codex("prompt", config)

    assert result.tool_calls == 2
    assert result.latency_seconds >= 0
    assert [
        json.loads(line)
        for line in (config.artifact_dir / "mcp_tool_calls.jsonl").read_text().splitlines()
    ] == list(trace)


@pytest.mark.asyncio
async def test_run_codex_rejects_version_and_answer_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _install_fake_processes(monkeypatch, config, version=b"codex-cli 0.143.0\n")
    with pytest.raises(CodexAuditError, match="version"):
        await run_codex("prompt", config)

    answer_root = tmp_path / "answer-mismatch"
    answer_root.mkdir()
    config = _config(answer_root)
    _install_fake_processes(monkeypatch, config, answer="different")
    with pytest.raises(CodexAuditError, match="answer"):
        await run_codex("prompt", config)



@pytest.mark.asyncio
async def test_run_codex_classifies_structured_zero_exit_rate_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    events = (json.dumps({"type": "error", "message": "HTTP 429 usage limit"}) + "\n").encode()
    _install_fake_processes(monkeypatch, config, events=events)
    with pytest.raises(CodexInfrastructureError) as raised:
        await run_codex("prompt", config)
    assert raised.value.reason == "rate_limit"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["429", "The author count is 12."])
async def test_run_codex_does_not_misclassify_normal_numeric_or_author_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    config = _config(tmp_path)
    _install_fake_processes(
        monkeypatch,
        config,
        events=_runner_events(answer=answer),
        answer=answer,
    )
    result = await run_codex("prompt", config)
    assert result.final_text == answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "reason"),
    [
        (b"HTTP 429 usage limit private-prompt", "rate_limit"),
        (b"unauthorized login private-prompt", "auth"),
        (b"connection temporarily unavailable private-prompt", "transport"),
        (b"unexpected private-prompt", "process"),
    ],
)
async def test_run_codex_classifies_nonzero_without_leaking_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stderr: bytes,
    reason: str,
) -> None:
    config = _config(tmp_path)
    _install_fake_processes(monkeypatch, config, exec_returncode=7, exec_stderr=stderr)
    with pytest.raises(CodexInfrastructureError) as raised:
        await run_codex("private-prompt", config)
    assert raised.value.reason == reason
    assert raised.value.meta == {"exit_code": 7}
    assert "private-prompt" not in str(raised.value)
    assert "private-prompt" not in repr(raised.value.meta)


@pytest.mark.asyncio
async def test_run_codex_timeout_terminates_process_without_leaking_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _, processes = _install_fake_processes(monkeypatch, config, timeout=True)
    with pytest.raises(CodexInfrastructureError) as raised:
        await run_codex("private prompt", config)
    assert raised.value.reason == "timeout"
    assert processes[-1].terminated is True
    assert "private prompt" not in str(raised.value)
    assert tuple(config.codex_home.iterdir()) == ()


@pytest.mark.asyncio
async def test_run_codex_cancellation_terminates_process_group_and_scrubs_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _, processes = _install_fake_processes(monkeypatch, config, cancelled=True)
    with pytest.raises(asyncio.CancelledError):
        await run_codex("private prompt", config)
    assert processes[-1].terminated is True
    assert tuple(config.codex_home.iterdir()) == ()


@pytest.mark.asyncio
async def test_run_codex_refuses_nonempty_private_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    (config.codex_home / "stale").write_text("x", encoding="utf-8")
    _install_fake_processes(monkeypatch, config)
    with pytest.raises(CodexAuditError, match="home"):
        await run_codex("prompt", config)


@pytest.mark.asyncio
async def test_run_codex_refuses_to_overwrite_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    config.artifact_dir.mkdir()
    (config.artifact_dir / "stale").write_text("x", encoding="utf-8")
    _install_fake_processes(monkeypatch, config)
    with pytest.raises(CodexAuditError, match="artifact"):
        await run_codex("prompt", config)


@pytest.mark.asyncio
async def test_run_codex_scrubs_auth_after_partial_preparation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    original = codex_host_module._create_private_file  # pyright: ignore[reportPrivateUsage]
    calls = 0

    def fail_after_auth(path: Path, content: bytes = b"") -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise CodexAuditError("synthetic preparation failure")
        original(path, content)

    monkeypatch.setattr(codex_host_module, "_create_private_file", fail_after_auth)
    with pytest.raises(CodexAuditError, match="synthetic"):
        await run_codex("prompt", config)
    assert tuple(config.codex_home.iterdir()) == ()
