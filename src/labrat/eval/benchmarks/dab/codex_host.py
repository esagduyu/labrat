"""Pure contracts for the diagnostic native-Codex DAB host.

This module intentionally stops short of submission eligibility.  It builds a
locked-down ``codex exec`` command and audits the resulting native, MCP, and
token-usage traces.  Process orchestration is added at the suite seam.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

_MODELS_AND_EFFORTS: dict[str, frozenset[str]] = {
    "gpt-5.6-luna": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "gpt-5.6-terra": frozenset({"low", "medium", "high", "xhigh", "max", "ultra"}),
    "gpt-5.6-sol": frozenset({"low", "medium", "high", "xhigh", "max", "ultra"}),
}

_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "shell_snapshot",
    "code_mode_host",
    "apps",
    "plugins",
    "remote_plugin",
    "tool_suggest",
    "in_app_browser",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "image_generation",
    "multi_agent",
    "goals",
    "workspace_dependencies",
    "hooks",
    "standalone_web_search",
)

_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
_TOTAL_USAGE_KEYS = (*_USAGE_KEYS, "total_tokens")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXPECTED_CLI_VERSION = "0.144.1"


class CodexAuditError(RuntimeError):
    """Raised when native evidence is incomplete, unsafe, or inconsistent."""


class CodexInfrastructureError(RuntimeError):
    """Sanitized failure raised by the native process runner."""

    def __init__(
        self,
        reason: Literal["auth", "transport", "timeout", "process", "rate_limit"],
        *,
        meta: dict[str, Any] | None = None,
    ) -> None:
        allowed_meta = {"exit_code", "resets_at", "resets_in_seconds"}
        sanitized_meta = {
            key: value
            for key, value in (meta or {}).items()
            if key in allowed_meta and type(value) is int and 0 <= value <= 2**63 - 1
        }
        super().__init__(f"Native Codex {reason} failure")
        self.reason = reason
        self.meta = sanitized_meta


@dataclass(frozen=True)
class McpLaunch:
    command: str
    args: tuple[str, ...]
    cwd: Path
    env: tuple[tuple[str, str], ...]
    enabled_tools: tuple[str, ...]


@dataclass(frozen=True)
class CodexHostConfig:
    executable: Path
    expected_version: str
    model: str
    reasoning_effort: str
    codex_home: Path
    workspace_dir: Path
    artifact_dir: Path
    timeout_seconds: int
    mcp: McpLaunch | None


@dataclass(frozen=True, init=False)
class NativeMcpCall:
    tool: str
    status: Literal["completed", "failed"]
    _arguments_json: str

    def __init__(
        self,
        tool: str,
        arguments: dict[str, Any],
        status: Literal["completed", "failed"],
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("Native MCP calls must have a terminal status")
        try:
            encoded = json.dumps(
                arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("Native MCP arguments must be JSON") from exc
        object.__setattr__(self, "tool", tool)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "_arguments_json", encoded)

    @property
    def arguments(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self._arguments_json))


@dataclass(frozen=True)
class RequestUsage:
    request_index: int
    input_tokens: int
    cached_input_tokens: int
    noncached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int


@dataclass(frozen=True, init=False)
class NativeRunResult:
    final_text: str
    tool_calls: int
    latency_seconds: float
    thread_id: str
    request_usage: tuple[RequestUsage, ...]
    mcp_calls: tuple[NativeMcpCall, ...]
    _usage_json: str

    def __init__(
        self,
        final_text: str,
        tool_calls: int,
        latency_seconds: float,
        thread_id: str,
        usage: dict[str, int],
        request_usage: tuple[RequestUsage, ...],
        mcp_calls: tuple[NativeMcpCall, ...],
    ) -> None:
        object.__setattr__(self, "final_text", final_text)
        object.__setattr__(self, "tool_calls", tool_calls)
        object.__setattr__(self, "latency_seconds", latency_seconds)
        object.__setattr__(self, "thread_id", thread_id)
        object.__setattr__(self, "request_usage", tuple(request_usage))
        object.__setattr__(self, "mcp_calls", tuple(mcp_calls))
        object.__setattr__(
            self,
            "_usage_json",
            json.dumps(usage, sort_keys=True, separators=(",", ":")),
        )

    @property
    def usage(self) -> dict[str, int]:
        return cast(dict[str, int], json.loads(self._usage_json))


def _toml_string(value: str) -> str:
    encoded = json.dumps(value, ensure_ascii=False)
    try:
        tomllib.loads(f"value={encoded}")
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("Value cannot be represented safely in TOML") from exc
    return encoded


def _toml_array(values: tuple[str, ...]) -> str:
    return "[" + ",".join(_toml_string(value) for value in values) + "]"


def _toml_env(values: tuple[tuple[str, str], ...]) -> str:
    seen: set[str] = set()
    fields: list[str] = []
    for key, value in values:
        if not _ENV_NAME.fullmatch(key) or key in seen:
            raise ValueError("MCP environment keys must be unique plain names")
        seen.add(key)
        fields.append(f"{key}={_toml_string(value)}")
    return "{" + ",".join(fields) + "}"


def _validate_config(config: CodexHostConfig) -> None:
    if config.expected_version != _EXPECTED_CLI_VERSION:
        raise ValueError(f"Native Codex CLI must be pinned to {_EXPECTED_CLI_VERSION}")
    allowed_efforts = _MODELS_AND_EFFORTS.get(config.model)
    if allowed_efforts is None:
        raise ValueError(f"Unsupported native Codex model: {config.model}")
    if config.reasoning_effort not in allowed_efforts:
        raise ValueError(f"Unsupported effort {config.reasoning_effort!r} for {config.model}")
    if config.timeout_seconds <= 0 or isinstance(config.timeout_seconds, bool):
        raise ValueError("Native Codex timeout must be positive")
    paths = (config.executable, config.codex_home, config.workspace_dir, config.artifact_dir)
    if any(not path.is_absolute() for path in paths):
        raise ValueError("Native Codex paths must be absolute")
    if len({config.codex_home, config.workspace_dir, config.artifact_dir}) != 3:
        raise ValueError("Codex home, workspace, and artifacts must be distinct")
    if not config.workspace_dir.is_dir() or any(config.workspace_dir.iterdir()):
        raise ValueError("Native Codex workspace must exist and be empty")
    if config.mcp is not None:
        if not config.mcp.command or not config.mcp.cwd.is_absolute():
            raise ValueError("MCP command and cwd must be configured")
        if not config.mcp.enabled_tools or len(set(config.mcp.enabled_tools)) != len(
            config.mcp.enabled_tools
        ):
            raise ValueError("MCP enabled tools must be nonempty and unique")


def build_codex_command(config: CodexHostConfig) -> list[str]:
    """Build the exact diagnostic-only ``codex exec`` command."""
    _validate_config(config)
    command = [
        str(config.executable),
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "--json",
        "--color",
        "never",
        "--sandbox",
        "read-only",
        "--cd",
        str(config.workspace_dir),
        "--model",
        config.model,
        "-c",
        f"model_reasoning_effort={_toml_string(config.reasoning_effort)}",
        "-c",
        'approval_policy="never"',
    ]
    for feature in _DISABLED_FEATURES:
        command.extend(("--disable", feature))

    if config.mcp is not None:
        mcp = config.mcp
        command.extend(("-c", f"mcp_servers.labrat.command={_toml_string(mcp.command)}"))
        command.extend(("-c", f"mcp_servers.labrat.args={_toml_array(mcp.args)}"))
        command.extend(("-c", f"mcp_servers.labrat.cwd={_toml_string(str(mcp.cwd))}"))
        command.extend(("-c", f"mcp_servers.labrat.env={_toml_env(mcp.env)}"))
        command.extend(("-c", "mcp_servers.labrat.required=true"))
        command.extend(("-c", f"mcp_servers.labrat.enabled_tools={_toml_array(mcp.enabled_tools)}"))

    command.extend(
        (
            "--output-last-message",
            str(config.artifact_dir / "final_answer.txt"),
            "-",
        )
    )
    return command


def _mapping(value: object, *, category: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise CodexAuditError(f"Malformed {category}")
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise CodexAuditError(f"Malformed {category}")
    return cast(dict[str, Any], mapping)


def _nonnegative_int(value: object, *, category: str) -> int:
    if type(value) is not int or value < 0:
        raise CodexAuditError(f"Malformed {category}")
    return value


def _usage(value: object, *, include_total: bool, category: str) -> dict[str, int]:
    mapping = _mapping(value, category=category)
    keys = _TOTAL_USAGE_KEYS if include_total else _USAGE_KEYS
    parsed = {key: _nonnegative_int(mapping.get(key), category=category) for key in keys}
    if parsed["cached_input_tokens"] > parsed["input_tokens"]:
        raise CodexAuditError(f"Malformed {category}")
    return parsed


def _json_line(raw: str, *, category: str) -> dict[str, Any]:
    try:
        return _mapping(json.loads(raw), category=category)
    except (json.JSONDecodeError, RecursionError):
        raise CodexAuditError(f"Malformed {category}") from None


def _read_utf8_lines(path: Path, *, category: str) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        raise CodexAuditError(f"Malformed {category}") from None


def parse_codex_events(lines: Iterable[str], *, enabled_tools: tuple[str, ...]) -> NativeRunResult:
    """Audit Codex CLI JSONL and return its normalized terminal evidence."""
    thread_id: str | None = None
    final_text: str | None = None
    terminal_usage: dict[str, int] | None = None
    calls: list[NativeMcpCall] = []
    terminal = False
    turn_started = False
    started_items: dict[str, tuple[str, str | None, str | None]] = {}
    allowed_tools = frozenset(enabled_tools)

    for raw in lines:
        if not raw.strip():
            continue
        if terminal:
            raise CodexAuditError("Native events continue after terminal turn")
        event = _json_line(raw, category="native event")
        event_type = event.get("type")
        if event_type == "thread.started":
            value = event.get("thread_id")
            if not isinstance(value, str) or not value or thread_id is not None or turn_started:
                raise CodexAuditError("Malformed native thread")
            thread_id = value
        elif event_type == "turn.started":
            if thread_id is None or turn_started:
                raise CodexAuditError("Malformed native turn lifecycle")
            turn_started = True
        elif event_type in {"item.started", "item.updated", "item.completed"}:
            if thread_id is None or not turn_started:
                raise CodexAuditError("Malformed native item lifecycle")
            item = _mapping(event.get("item"), category="native item")
            item_id = item.get("id")
            item_type = item.get("type")
            if (
                not isinstance(item_id, str)
                or not item_id
                or item_type not in {"reasoning", "agent_message", "mcp_tool_call"}
            ):
                raise CodexAuditError("Forbidden native item type")

            tool: str | None = None
            arguments_json: str | None = None
            mcp_status: Literal["completed", "failed"] | None = None
            if item_type == "mcp_tool_call":
                if item.get("server") != "labrat":
                    raise CodexAuditError("Forbidden native MCP server")
                raw_tool = item.get("tool")
                if not isinstance(raw_tool, str) or raw_tool not in allowed_tools:
                    raise CodexAuditError("Forbidden native MCP tool")
                tool = raw_tool
                arguments = _mapping(item.get("arguments"), category="native MCP arguments")
                arguments_json = json.dumps(
                    arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                )
                status = item.get("status")
                if event_type == "item.completed":
                    if status not in {"completed", "failed"}:
                        raise CodexAuditError("Native MCP call has no terminal status")
                    mcp_status = cast(Literal["completed", "failed"], status)
                elif status != "in_progress":
                    raise CodexAuditError("Malformed native MCP status")

            identity = (cast(str, item_type), tool, arguments_json)
            if event_type == "item.started":
                if item_id in started_items:
                    raise CodexAuditError("Duplicate native item start")
                started_items[item_id] = identity
                continue
            if event_type == "item.updated":
                if started_items.get(item_id) != identity:
                    raise CodexAuditError("Native item identity changed")
                continue

            prior_identity = started_items.pop(item_id, None)
            if item_type == "mcp_tool_call" and prior_identity != identity:
                raise CodexAuditError("Native MCP call lacks a matching start")
            if prior_identity is not None and prior_identity != identity:
                raise CodexAuditError("Native item identity changed")
            if item_type == "agent_message":
                text = item.get("text")
                if not isinstance(text, str):
                    raise CodexAuditError("Malformed native agent message")
                final_text = text
            elif item_type == "mcp_tool_call":
                arguments = _mapping(item.get("arguments"), category="native MCP arguments")
                calls.append(
                    NativeMcpCall(
                        tool=cast(str, tool),
                        arguments=arguments,
                        status=cast(Literal["completed", "failed"], mcp_status),
                    )
                )
        elif event_type == "turn.completed":
            if thread_id is None or not turn_started or terminal_usage is not None:
                raise CodexAuditError("Duplicate native terminal turn")
            if started_items:
                raise CodexAuditError("Native turn ended with incomplete items")
            terminal_usage = _usage(
                event.get("usage"), include_total=False, category="native aggregate usage"
            )
            terminal = True
        elif event_type in {"turn.failed", "error"}:
            raise CodexAuditError("Native Codex reported a failed turn")
        else:
            raise CodexAuditError("Unknown native event type")

    if (
        thread_id is None
        or not turn_started
        or final_text is None
        or terminal_usage is None
        or not terminal
    ):
        raise CodexAuditError("Incomplete native Codex trace")
    return NativeRunResult(
        final_text=final_text,
        tool_calls=len(calls),
        latency_seconds=0.0,
        thread_id=thread_id,
        usage=terminal_usage,
        request_usage=(),
        mcp_calls=tuple(calls),
    )


def extract_request_usage(rollout_path: Path, thread_id: str) -> tuple[RequestUsage, ...]:
    """Extract only scrubbed per-request token counters from one private rollout."""
    if not rollout_path.is_file():
        raise CodexAuditError("Native rollout is missing")
    session_ids: list[str] = []
    prior_total: dict[str, int] | None = None
    prior_fingerprint: tuple[int, ...] | None = None
    requests: list[RequestUsage] = []

    for raw in _read_utf8_lines(rollout_path, category="native rollout"):
        if not raw.strip():
            continue
        record = _json_line(raw, category="native rollout record")
        if record.get("type") == "session_meta":
            payload = _mapping(record.get("payload"), category="native session metadata")
            value = payload.get("id")
            if not isinstance(value, str):
                raise CodexAuditError("Malformed native session metadata")
            session_ids.append(value)
            continue
        if record.get("type") != "event_msg":
            continue
        payload = _mapping(record.get("payload"), category="native rollout event")
        if payload.get("type") != "token_count":
            continue
        info = _mapping(payload.get("info"), category="native token count")
        total = _usage(
            info.get("total_token_usage"),
            include_total=True,
            category="native cumulative usage",
        )
        last = _usage(
            info.get("last_token_usage"),
            include_total=True,
            category="native request usage",
        )
        fingerprint = tuple(total[key] for key in _TOTAL_USAGE_KEYS)
        if prior_total is not None and any(
            total[key] < prior_total[key] for key in _TOTAL_USAGE_KEYS
        ):
            raise CodexAuditError("Native cumulative usage regressed")
        if fingerprint == prior_fingerprint:
            continue
        prior_total = total
        prior_fingerprint = fingerprint
        requests.append(
            RequestUsage(
                request_index=len(requests) + 1,
                input_tokens=last["input_tokens"],
                cached_input_tokens=last["cached_input_tokens"],
                noncached_input_tokens=(last["input_tokens"] - last["cached_input_tokens"]),
                output_tokens=last["output_tokens"],
                reasoning_output_tokens=last["reasoning_output_tokens"],
            )
        )

    if not session_ids or set(session_ids) != {thread_id} or not requests:
        raise CodexAuditError("Native rollout does not match the completed thread")
    return tuple(requests)


def reconcile_mcp_trace(native_calls: tuple[NativeMcpCall, ...], server_trace_path: Path) -> None:
    """Require one ordered server record for every completed native MCP item."""
    if not server_trace_path.is_file():
        raise CodexAuditError("MCP server trace is missing")
    server_calls: list[tuple[str, dict[str, Any], bool]] = []
    for raw in _read_utf8_lines(server_trace_path, category="MCP server trace"):
        if not raw.strip():
            continue
        record = _json_line(raw, category="MCP server trace")
        tool = record.get("tool")
        ok = record.get("ok")
        if not isinstance(tool, str) or type(ok) is not bool:
            raise CodexAuditError("Malformed MCP server trace")
        arguments = _mapping(record.get("input"), category="MCP server arguments")
        server_calls.append((tool, arguments, ok))

    if len(server_calls) != len(native_calls):
        raise CodexAuditError("Native and MCP server trace lengths disagree")
    for native, (tool, arguments, ok) in zip(native_calls, server_calls, strict=True):
        expected_ok = native.status == "completed"
        if native.tool != tool or native.arguments != arguments or ok is not expected_ok:
            raise CodexAuditError("Native and MCP server traces disagree")
