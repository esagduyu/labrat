"""Temporary standalone host for restricted native-Codex DAB diagnostics.

This module intentionally stops short of submission eligibility. It launches a
locked-down ``codex exec`` process and retains only aggregate public usage plus
raw native and MCP traces.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import time
import tomllib
from collections.abc import Iterable, Mapping
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
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXPECTED_CLI_VERSION = "0.144.1"
_DIAGNOSTIC_MCP_ENV_KEYS = frozenset(
    {
        "LABRAT_MCP_CONNECTIONS",
        "LABRAT_MCP_PRIMARY",
        "LABRAT_MCP_LOG_DIR",
        "LABRAT_MCP_POLICY_PATH",
    }
)


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
    source_codex_home: Path
    codex_home: Path
    workspace_dir: Path
    artifact_dir: Path
    timeout_seconds: int
    mcp: McpLaunch | None


@dataclass(frozen=True)
class NativeRunResult:
    final_text: str
    tool_calls: int
    latency_seconds: float
    usage: dict[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "usage", dict(self.usage))


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


def _validate_diagnostic_mcp(mcp: McpLaunch) -> None:
    if not Path(mcp.command).is_absolute() or mcp.args != ("-m", "labrat.mcp.server"):
        raise ValueError("Diagnostic MCP launcher must use an absolute Python module command")
    environment = dict(mcp.env)
    if len(environment) != len(mcp.env) or set(environment) != set(_DIAGNOSTIC_MCP_ENV_KEYS):
        raise ValueError("Diagnostic MCP environment must use the exact safe schema")
    primary = environment["LABRAT_MCP_PRIMARY"]
    if not _ENV_NAME.fullmatch(primary):
        raise ValueError("Diagnostic MCP primary must be a plain identifier")
    for key in ("LABRAT_MCP_LOG_DIR", "LABRAT_MCP_POLICY_PATH"):
        if not Path(environment[key]).is_absolute():
            raise ValueError("Diagnostic MCP artifact paths must be absolute")
    try:
        raw_connections = json.loads(environment["LABRAT_MCP_CONNECTIONS"])
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("Diagnostic MCP connections must be valid JSON") from None
    if type(raw_connections) is not dict:
        raise ValueError("Diagnostic MCP must expose exactly one primary connection")
    connections = cast(dict[str, Any], raw_connections)
    if set(connections) != {primary}:
        raise ValueError("Diagnostic MCP must expose exactly one primary connection")
    raw_spec = connections[primary]
    if type(raw_spec) is not dict:
        raise ValueError("Diagnostic MCP connection must be one read-only DuckDB")
    spec = cast(dict[str, Any], raw_spec)
    if (
        set(spec) != {"db_type", "db_path", "read_only"}
        or spec.get("db_type") != "duckdb"
        or type(spec.get("db_path")) is not str
        or not Path(spec["db_path"]).is_absolute()
        or spec.get("read_only") is not True
    ):
        raise ValueError("Diagnostic MCP connection must be one read-only DuckDB")


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
    paths = (
        config.executable,
        config.source_codex_home,
        config.codex_home,
        config.workspace_dir,
        config.artifact_dir,
    )
    if any(not path.is_absolute() for path in paths):
        raise ValueError("Native Codex paths must be absolute")
    private_paths = (
        config.source_codex_home,
        config.codex_home,
        config.workspace_dir,
        config.artifact_dir,
    )
    if any(path.is_symlink() for path in private_paths if path.exists()):
        raise ValueError("Native Codex private paths cannot be symlinks")
    resolved = tuple(path.resolve() for path in private_paths)
    for index, left in enumerate(resolved):
        for right in resolved[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError("Source, Codex home, workspace, and artifacts must not overlap")
    if not config.workspace_dir.is_dir() or any(config.workspace_dir.iterdir()):
        raise ValueError("Native Codex workspace must exist and be empty")
    if config.mcp is not None:
        if not config.mcp.command or not config.mcp.cwd.is_absolute():
            raise ValueError("MCP command and cwd must be configured")
        if not config.mcp.enabled_tools or len(set(config.mcp.enabled_tools)) != len(
            config.mcp.enabled_tools
        ):
            raise ValueError("MCP enabled tools must be nonempty and unique")
        _validate_diagnostic_mcp(config.mcp)
        mcp_environment = dict(config.mcp.env)
        if Path(mcp_environment["LABRAT_MCP_LOG_DIR"]).resolve() != config.artifact_dir.resolve():
            raise ValueError("Diagnostic MCP trace directory must match native artifacts")


def build_codex_command(config: CodexHostConfig) -> list[str]:
    """Build the exact diagnostic-only ``codex exec`` command."""
    _validate_config(config)
    command = [
        str(config.executable),
        "-a",
        "never",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "--json",
        "--ephemeral",
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


def _usage(value: object, *, category: str) -> dict[str, int]:
    mapping = _mapping(value, category=category)
    parsed = {key: _nonnegative_int(mapping.get(key), category=category) for key in _USAGE_KEYS}
    if parsed["cached_input_tokens"] > parsed["input_tokens"]:
        raise CodexAuditError(f"Malformed {category}")
    return parsed


def _json_line(raw: str, *, category: str) -> dict[str, Any]:
    try:
        return _mapping(json.loads(raw), category=category)
    except (json.JSONDecodeError, RecursionError):
        raise CodexAuditError(f"Malformed {category}") from None


def parse_codex_events(lines: Iterable[str], *, enabled_tools: tuple[str, ...]) -> NativeRunResult:
    """Audit Codex CLI JSONL and return its normalized terminal evidence."""
    thread_id: str | None = None
    final_text: str | None = None
    terminal_usage: dict[str, int] | None = None
    tool_calls = 0
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
                tool_calls += 1
        elif event_type == "turn.completed":
            if thread_id is None or not turn_started or terminal_usage is not None:
                raise CodexAuditError("Duplicate native terminal turn")
            if started_items:
                raise CodexAuditError("Native turn ended with incomplete items")
            terminal_usage = _usage(event.get("usage"), category="native aggregate usage")
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
        tool_calls=tool_calls,
        latency_seconds=0.0,
        usage=terminal_usage,
    )


_SAFE_ENV_KEYS = ("PATH", "LANG", "LC_ALL")
_ARTIFACT_NAMES = (
    "codex_events.jsonl",
    "final_answer.txt",
    "mcp_tool_calls.jsonl",
)
_FINAL_ANSWER_SENTINEL = b"LABRAT_CODEX_FINAL_ANSWER_NOT_WRITTEN"


def _minimal_environment(
    config: CodexHostConfig, parent_env: Mapping[str, str] | None
) -> dict[str, str]:
    source = os.environ if parent_env is None else parent_env
    environment = {key: source[key] for key in _SAFE_ENV_KEYS if key in source}
    environment["HOME"] = str(config.codex_home)
    environment["TMPDIR"] = str(config.codex_home / "tmp")
    environment["CODEX_HOME"] = str(config.codex_home)
    return environment


def _create_private_file(path: Path, content: bytes = b"") -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
    except OSError:
        raise CodexAuditError("Unable to create native Codex private file") from None


def _prepare_private_files(config: CodexHostConfig) -> None:
    auth_source = config.source_codex_home / "auth.json"
    if not auth_source.is_file() or auth_source.is_symlink():
        raise CodexAuditError("Native Codex auth source is unavailable")
    if not config.codex_home.is_dir() or any(config.codex_home.iterdir()):
        raise CodexAuditError("Native Codex home must exist and be empty")
    if config.artifact_dir.exists():
        if not config.artifact_dir.is_dir() or any(config.artifact_dir.iterdir()):
            raise CodexAuditError("Native Codex artifact directory must be empty")
    else:
        config.artifact_dir.mkdir(parents=True, mode=0o700)

    auth_target = config.codex_home / "auth.json"
    try:
        config.codex_home.chmod(0o700)
        config.artifact_dir.chmod(0o700)
        (config.codex_home / "tmp").mkdir(mode=0o700)
        source_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        source_descriptor = os.open(auth_source, source_flags)
        try:
            with os.fdopen(source_descriptor, "rb") as source_handle:
                _create_private_file(auth_target, source_handle.read())
        except Exception:
            raise
        for name in _ARTIFACT_NAMES:
            content = _FINAL_ANSWER_SENTINEL if name == "final_answer.txt" else b""
            _create_private_file(config.artifact_dir / name, content)
    except OSError:
        raise CodexAuditError("Unable to prepare native Codex private files") from None


def _scrub_codex_home(codex_home: Path) -> None:
    for child in tuple(codex_home.iterdir()):
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError:
            # The caller owns this already-private temporary directory and removes
            # it after the diagnostic. Cleanup here is defense in depth.
            continue


def _failure_reason(
    stderr: bytes,
) -> Literal["auth", "transport", "process", "rate_limit"]:
    text = stderr.decode("utf-8", errors="replace")[:4096].lower()
    if any(marker in text for marker in ("429", "rate limit", "usage limit", "session limit")):
        return "rate_limit"
    if any(marker in text for marker in ("login", "auth", "unauthorized")):
        return "auth"
    if any(marker in text for marker in ("connection", "transport", "temporarily unavailable")):
        return "transport"
    return "process"


def _structured_failure_reason(
    stdout: bytes,
) -> Literal["auth", "transport", "process", "rate_limit"] | None:
    try:
        lines = stdout.decode("utf-8", errors="strict").splitlines()
    except UnicodeError:
        return "process"
    last_agent_text: str | None = None
    for raw in lines:
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, RecursionError):
            continue
        if type(event) is not dict:
            continue
        event = cast(dict[str, Any], event)
        event_type = event.get("type")
        encoded = json.dumps(event, ensure_ascii=True).encode("utf-8")[:4096]
        if event_type in {"error", "turn.failed"}:
            return _failure_reason(encoded)
        if event_type == "item.completed":
            item = event.get("item")
            if type(item) is dict:
                item_mapping = cast(dict[str, Any], item)
            else:
                continue
            if item_mapping.get("type") == "agent_message":
                text = item_mapping.get("text")
                if isinstance(text, str):
                    last_agent_text = text
    if last_agent_text is not None:
        text = last_agent_text.casefold()[:4096]
        if any(
            marker in text
            for marker in (
                "rate limit",
                "usage limit",
                "session limit",
                "too many requests",
                "http 429",
                "api error: 429",
            )
        ):
            return "rate_limit"
        if any(
            marker in text
            for marker in ("authentication required", "not logged in", "unauthorized")
        ):
            return "auth"
    return None


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    pid = getattr(process, "pid", None)
    try:
        if type(pid) is int:
            os.killpg(pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=1.0)
    except TimeoutError:
        try:
            if type(pid) is int:
                os.killpg(pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            return
        await process.wait()


async def _invoke(
    argv: list[str],
    *,
    environment: Mapping[str, str],
    stdin: bytes,
    timeout_seconds: int,
    cwd: Path,
) -> tuple[int, bytes, bytes]:
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(environment),
            cwd=str(cwd),
            start_new_session=True,
        )
    except OSError:
        raise CodexInfrastructureError("process") from None
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=stdin), timeout=timeout_seconds
        )
    except TimeoutError:
        await _stop_process(process)
        raise CodexInfrastructureError("timeout") from None
    except asyncio.CancelledError:
        await asyncio.shield(_stop_process(process))
        raise
    returncode = process.returncode
    if type(returncode) is not int:
        raise CodexInfrastructureError("process")
    return returncode, stdout, stderr


async def run_codex(
    prompt: str,
    config: CodexHostConfig,
    *,
    parent_env: Mapping[str, str] | None = None,
) -> NativeRunResult:
    """Run one isolated, diagnostic-only native Codex turn and audit its evidence."""
    command = build_codex_command(config)
    environment = _minimal_environment(config, parent_env)
    if not config.codex_home.is_dir() or any(config.codex_home.iterdir()):
        raise CodexAuditError("Native Codex home must exist and be empty")
    if config.artifact_dir.exists() and (
        not config.artifact_dir.is_dir() or any(config.artifact_dir.iterdir())
    ):
        raise CodexAuditError("Native Codex artifact directory must be empty")

    try:
        _prepare_private_files(config)
        if any(config.workspace_dir.iterdir()):
            raise CodexAuditError("Native Codex workspace changed before launch")
        version_code, version_stdout, version_stderr = await _invoke(
            [str(config.executable), "--version"],
            environment=environment,
            stdin=b"",
            timeout_seconds=min(config.timeout_seconds, 10),
            cwd=config.workspace_dir,
        )
        if version_code != 0:
            raise CodexInfrastructureError(
                _failure_reason(version_stderr), meta={"exit_code": version_code}
            )
        try:
            version = version_stdout.decode("utf-8", errors="strict").strip()
        except UnicodeError:
            raise CodexAuditError("Native Codex CLI version is malformed") from None
        if version not in {"codex-cli 0.144.1", "codex 0.144.1"}:
            raise CodexAuditError("Native Codex CLI version mismatch")

        started = time.monotonic()
        returncode, stdout, stderr = await _invoke(
            command,
            environment=environment,
            stdin=prompt.encode("utf-8"),
            timeout_seconds=config.timeout_seconds,
            cwd=config.workspace_dir,
        )
        latency = time.monotonic() - started
        events_path = config.artifact_dir / "codex_events.jsonl"
        try:
            events_path.write_bytes(stdout)
        except OSError:
            raise CodexAuditError("Unable to persist native Codex events") from None
        structured_reason = _structured_failure_reason(stdout)
        if returncode != 0:
            stderr_reason = _failure_reason(stderr)
            reason = structured_reason if stderr_reason == "process" else stderr_reason
            raise CodexInfrastructureError(reason or "process", meta={"exit_code": returncode})
        if structured_reason is not None:
            raise CodexInfrastructureError(structured_reason)
        try:
            event_lines = stdout.decode("utf-8", errors="strict").splitlines()
        except UnicodeError:
            raise CodexAuditError("Malformed native Codex events") from None

        parsed = parse_codex_events(
            event_lines,
            enabled_tools=() if config.mcp is None else config.mcp.enabled_tools,
        )
        final_path = config.artifact_dir / "final_answer.txt"
        try:
            final_text = final_path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            raise CodexAuditError("Native Codex final answer is malformed") from None
        if final_text == _FINAL_ANSWER_SENTINEL.decode("ascii") or final_text != parsed.final_text:
            raise CodexAuditError("Native Codex final answer disagrees with events")

        return NativeRunResult(
            final_text=parsed.final_text,
            tool_calls=parsed.tool_calls,
            latency_seconds=latency,
            usage=parsed.usage,
        )
    finally:
        _scrub_codex_home(config.codex_home)
