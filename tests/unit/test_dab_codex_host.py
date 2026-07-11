"""Focused tests for the pure diagnostic native-Codex host core."""

from __future__ import annotations

import asyncio
import json
import stat
import tomllib
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any, cast

import pytest

import labrat.eval.benchmarks.dab.codex_host as codex_host_module
from labrat.eval.benchmarks.dab.codex_host import (
    CodexAuditError,
    CodexHostConfig,
    CodexInfrastructureError,
    McpLaunch,
    NativeMcpCall,
    NativeRunResult,
    RequestUsage,
    build_codex_command,
    extract_request_usage,
    parse_codex_events,
    reconcile_mcp_trace,
    run_codex,
)


def _config(
    tmp_path: Path, *, mcp: bool = True, model: str = "gpt-5.6-luna", effort: str = "low"
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
                ("LABRAT_MCP_LOG_DIR", str(tmp_path / 'a"b')),
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
    [McpLaunch, CodexHostConfig, NativeMcpCall, RequestUsage, NativeRunResult],
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
    assert "--ephemeral" not in command
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
    config = _config(tmp_path)
    assert config.mcp is not None
    hostile = '😀\n" -c injected=true'
    config = CodexHostConfig(
        **{
            **config.__dict__,
            "mcp": McpLaunch(
                command=config.mcp.command,
                args=config.mcp.args,
                cwd=config.mcp.cwd,
                env=tuple(
                    (
                        key,
                        str(tmp_path / hostile) if key == "LABRAT_MCP_LOG_DIR" else value,
                    )
                    for key, value in config.mcp.env
                ),
                enabled_tools=config.mcp.enabled_tools,
            ),
        }
    )
    command = build_codex_command(config)
    overrides = [command[index + 1] for index, value in enumerate(command) if value == "-c"]
    for override in overrides:
        tomllib.loads(override)
    assert not any(value == "injected=true" for value in command)


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
    assert result.thread_id == "thread-1"
    assert result.final_text == "answer"
    assert result.tool_calls == 0
    assert result.usage == {
        "input_tokens": 8_517,
        "cached_input_tokens": 6_912,
        "output_tokens": 9,
        "reasoning_output_tokens": 0,
    }
    assert result.mcp_calls == ()


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
    assert result.mcp_calls == (
        NativeMcpCall("list_tables", {}, "completed"),
        NativeMcpCall("run_sql", {"query": "SELECT 1"}, "completed"),
    )


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


def test_parse_reconciles_failed_mcp_calls_and_rejects_unfinished_calls(
    tmp_path: Path,
) -> None:
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
    assert parsed.mcp_calls == (NativeMcpCall("run_sql", {"query": "SELECT 1"}, "failed"),)
    trace = tmp_path / "failed-mcp.jsonl"
    trace.write_text(
        json.dumps({"tool": "run_sql", "input": {"query": "SELECT 1"}, "ok": False}) + "\n",
        encoding="utf-8",
    )
    reconcile_mcp_trace(parsed.mcp_calls, trace)

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


def test_parsed_evidence_is_defensively_immutable() -> None:
    result = parse_codex_events(_zero_tool_events(), enabled_tools=())
    usage = result.usage
    usage["input_tokens"] = 0
    assert result.usage["input_tokens"] == 8_517

    call = NativeMcpCall("run_sql", {"nested": {"value": 1}}, "completed")
    arguments = call.arguments
    arguments["nested"]["value"] = 2
    assert call.arguments == {"nested": {"value": 1}}


def test_malformed_json_does_not_retain_raw_text_as_exception_cause() -> None:
    with pytest.raises(CodexAuditError) as raised:
        parse_codex_events(['{"secret":"raw token"'], enabled_tools=())
    assert raised.value.__cause__ is None


def test_private_trace_readers_sanitize_invalid_utf8(tmp_path: Path) -> None:
    private_bytes = b"\xffsecret SQL and token"
    rollout = tmp_path / "rollout.jsonl"
    server_trace = tmp_path / "mcp_tool_calls.jsonl"
    rollout.write_bytes(private_bytes)
    server_trace.write_bytes(private_bytes)

    with pytest.raises(CodexAuditError) as rollout_error:
        extract_request_usage(rollout, "thread-1")
    with pytest.raises(CodexAuditError) as trace_error:
        reconcile_mcp_trace((), server_trace)
    assert rollout_error.value.__cause__ is None
    assert trace_error.value.__cause__ is None


def _usage_values(input_tokens: int, cached: int, output: int, reasoning: int) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": input_tokens + output,
    }


def _write_rollout(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def test_extract_request_usage_deduplicates_cumulative_snapshots(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout.jsonl"
    first_last = _usage_values(100, 64, 10, 4)
    first_total = _usage_values(100, 64, 10, 4)
    second_last = _usage_values(150, 128, 20, 8)
    second_total = _usage_values(250, 192, 30, 12)
    token = lambda last, total: {  # noqa: E731 - compact fixture constructor
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {"last_token_usage": last, "total_token_usage": total},
        },
    }
    _write_rollout(
        rollout,
        [
            {"type": "session_meta", "payload": {"id": "thread-1"}},
            {"type": "session_meta", "payload": {"id": "thread-1"}},
            token(first_last, first_total),
            token(first_last, first_total),
            token(second_last, second_total),
        ],
    )

    usage = extract_request_usage(rollout, "thread-1")
    assert usage == (
        RequestUsage(1, 100, 64, 36, 10, 4),
        RequestUsage(2, 150, 128, 22, 20, 8),
    )


@pytest.mark.parametrize("wrong_thread", [True, False])
def test_extract_request_usage_rejects_wrong_thread_or_regression(
    tmp_path: Path, wrong_thread: bool
) -> None:
    rollout = tmp_path / "rollout.jsonl"
    first = _usage_values(100, 64, 10, 4)
    second = _usage_values(90, 64, 9, 3)
    _write_rollout(
        rollout,
        [
            {
                "type": "session_meta",
                "payload": {"id": "wrong" if wrong_thread else "thread-1"},
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"last_token_usage": first, "total_token_usage": first},
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"last_token_usage": second, "total_token_usage": second},
                },
            },
        ],
    )
    with pytest.raises(CodexAuditError):
        extract_request_usage(rollout, "thread-1")


def test_extract_request_usage_rejects_nonconsecutive_regression(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "rollout.jsonl"
    first = _usage_values(100, 64, 10, 4)
    second_total = _usage_values(250, 192, 30, 12)
    second_last = _usage_values(150, 128, 20, 8)
    records: list[dict[str, object]] = [
        {"type": "session_meta", "payload": {"id": "thread-1"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"last_token_usage": first, "total_token_usage": first},
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": second_last,
                    "total_token_usage": second_total,
                },
            },
        },
    ]
    _write_rollout(rollout, [*records, records[1]])
    with pytest.raises(CodexAuditError, match="regressed"):
        extract_request_usage(rollout, "thread-1")


def test_extract_request_usage_ignores_duplicate_total_with_changed_last_snapshot(
    tmp_path: Path,
) -> None:
    rollout = tmp_path / "rollout.jsonl"
    total = _usage_values(100, 64, 10, 4)
    changed_last = _usage_values(0, 0, 0, 0)
    _write_rollout(
        rollout,
        [
            {"type": "session_meta", "payload": {"id": "thread-1"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"last_token_usage": total, "total_token_usage": total},
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": changed_last,
                        "total_token_usage": total,
                    },
                },
            },
        ],
    )
    assert extract_request_usage(rollout, "thread-1") == (RequestUsage(1, 100, 64, 36, 10, 4),)


def test_reconcile_mcp_trace_accepts_exact_ordered_calls(tmp_path: Path) -> None:
    trace = tmp_path / "mcp_tool_calls.jsonl"
    trace.write_text(
        json.dumps({"tool": "list_tables", "input": {}, "ok": True})
        + "\n"
        + json.dumps({"tool": "run_sql", "input": {"query": "SELECT 1"}, "ok": True})
        + "\n",
        encoding="utf-8",
    )
    reconcile_mcp_trace(
        (
            NativeMcpCall("list_tables", {}, "completed"),
            NativeMcpCall("run_sql", {"query": "SELECT 1"}, "completed"),
        ),
        trace,
    )


def test_reconcile_mcp_trace_accepts_empty_and_rejects_mismatch(tmp_path: Path) -> None:
    trace = tmp_path / "mcp_tool_calls.jsonl"
    trace.write_text("", encoding="utf-8")
    reconcile_mcp_trace((), trace)

    trace.write_text(
        json.dumps({"tool": "run_sql", "input": {"query": "SELECT 2"}, "ok": True}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CodexAuditError):
        reconcile_mcp_trace((NativeMcpCall("run_sql", {"query": "SELECT 1"}, "completed"),), trace)


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


def _write_runner_rollouts(
    config: CodexHostConfig,
    *,
    count: int = 1,
    usage: dict[str, int] | None = None,
) -> None:
    sessions = config.codex_home / "sessions" / "2026" / "07"
    sessions.mkdir(parents=True, exist_ok=True)
    usage = usage or _usage_values(100, 64, 10, 4)
    records = [
        {"type": "session_meta", "payload": {"id": "thread-1"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {"last_token_usage": usage, "total_token_usage": usage},
            },
        },
    ]
    for index in range(count):
        _write_rollout(sessions / f"rollout-thread-1-{index}.jsonl", records)


def _install_fake_processes(
    monkeypatch: pytest.MonkeyPatch,
    config: CodexHostConfig,
    *,
    events: bytes | None = None,
    answer: str = "answer",
    rollout_count: int = 1,
    trace: tuple[dict[str, object], ...] = (),
    version: bytes = b"codex-cli 0.144.1\n",
    exec_returncode: int = 0,
    exec_stderr: bytes = b"",
    timeout: bool = False,
    cancelled: bool = False,
    filename_collision: bool = False,
    rollout_usage: dict[str, int] | None = None,
) -> tuple[list[tuple[tuple[str, ...], dict[str, Any]]], list[_FakeProcess]]:
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    processes: list[_FakeProcess] = []

    def finish_exec() -> None:
        (config.artifact_dir / "final_answer.txt").write_text(answer, encoding="utf-8")
        if rollout_count:
            _write_runner_rollouts(config, count=rollout_count, usage=rollout_usage)
        if filename_collision:
            collision = config.codex_home / "sessions" / "rollout-thread-1-collision.jsonl"
            _write_rollout(
                collision,
                [{"type": "session_meta", "payload": {"id": "different-thread"}}],
            )
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
    assert result.request_usage == (RequestUsage(1, 100, 64, 36, 10, 4),)
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
    assert (config.artifact_dir / "codex_events.jsonl").read_bytes() == _runner_events()
    usage_rows = [
        json.loads(line)
        for line in (config.artifact_dir / "codex_token_usage.jsonl").read_text().splitlines()
    ]
    assert set(usage_rows[0]) == {
        "request_index",
        "input_tokens",
        "cached_input_tokens",
        "noncached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    }


@pytest.mark.asyncio
async def test_run_codex_reconciles_two_mcp_calls(
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


@pytest.mark.asyncio
async def test_run_codex_rejects_version_answer_and_rollout_mismatches(
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
@pytest.mark.parametrize("rollout_count", [0, 2])
async def test_run_codex_requires_exactly_one_matching_rollout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rollout_count: int
) -> None:
    config = _config(tmp_path)
    _install_fake_processes(monkeypatch, config, rollout_count=rollout_count)
    with pytest.raises(CodexAuditError, match="rollout"):
        await run_codex("prompt", config)


@pytest.mark.asyncio
async def test_run_codex_ignores_filename_only_rollout_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _install_fake_processes(monkeypatch, config, filename_collision=True)
    result = await run_codex("prompt", config)
    assert result.thread_id == "thread-1"


@pytest.mark.asyncio
async def test_run_codex_reconciles_rollout_totals_with_terminal_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _install_fake_processes(
        monkeypatch,
        config,
        rollout_usage=_usage_values(101, 64, 10, 4),
    )
    with pytest.raises(CodexAuditError, match="terminal usage"):
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
