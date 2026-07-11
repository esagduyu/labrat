import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from labrat.agent.tools.base import DispatchResult, Tool, ToolContext, ToolRegistry
from labrat.mcp.policy import McpPolicy, PolicyDenied, PolicySession
from labrat.mcp.server import (
    _build_context_from_env,
    _dispatch_tool_call,
    _listed_tools,
    _log_tool_call,
    _serve,
)


class _Input(BaseModel):
    value: str = ""


class _Tool(Tool[_Input]):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"{self._name} description"

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> object:
        return {"value": args.value}


class _SpyRegistry(ToolRegistry):
    def __init__(self, result: DispatchResult) -> None:
        super().__init__()
        self.result = result
        self.dispatch_calls: list[tuple[str, dict[str, Any], ToolContext]] = []
        self.events: list[str] | None = None

    async def dispatch(self, name: str, args: dict[str, Any], ctx: ToolContext) -> DispatchResult:
        self.dispatch_calls.append((name, args, ctx))
        if self.events is not None:
            self.events.append("dispatch")
        return self.result


class _RecordingPolicy(PolicySession):
    def __init__(self, policy: McpPolicy, events: list[str]) -> None:
        super().__init__(policy)
        self.events = events

    def authorize(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> None:
        self.events.append("authorize")
        super().authorize(name, arguments, ctx)

    def record_success(self, name: str, arguments: dict[str, Any]) -> None:
        self.events.append("record")
        super().record_success(name, arguments)


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(_Tool(name))
    return registry


def _policy(*allowed_tools: str) -> McpPolicy:
    return McpPolicy.model_validate(
        {
            "schema_version": 1,
            "run_manifest_sha256": "a" * 64,
            "task_id": "task-42",
            "trial_num": 0,
            "attempt_num": 0,
            "primary_database": "main",
            "allowed_tools": allowed_tools,
            "source_grants": [],
            "mongo_grants": [],
            "limits": {
                "max_rows": 1000,
                "max_sample_rows": 100,
                "max_tables": 50,
                "max_output_chars": 20000,
                "max_sql_chars": 4000,
                "max_identifier_chars": 128,
                "max_mongo_depth": 8,
                "max_mongo_filter_bytes": 4096,
            },
            "cartographer_enabled": False,
            "builder_sha256": "b" * 64,
            "digest": "d" * 64,
        }
    )


def test_build_context_from_env_allows_in_memory_primary(monkeypatch: Any) -> None:
    """A :memory: primary (the DAB federation workspace) must come up writable —
    DuckDB cannot even open :memory: read-only, and the agent ATTACHes / loads
    Mongo into it. Regression for the sandbox smoke that exposed this."""
    monkeypatch.setenv(
        "LABRAT_MCP_CONNECTIONS", '{"__federation": {"db_type": "duckdb", "db_path": ":memory:"}}'
    )
    monkeypatch.setenv("LABRAT_MCP_PRIMARY", "__federation")
    ctx, live = _build_context_from_env()
    try:
        assert ctx.primary == "__federation"
        # Writable check: CREATE would raise on a read-only connection.
        ctx.connections["__federation"]._conn.execute("CREATE TABLE t(x INTEGER)")  # type: ignore[attr-defined]
    finally:
        for c in live:
            c.disconnect()


def test_log_tool_call_writes_jsonl_line(tmp_path: Path) -> None:
    _log_tool_call(
        str(tmp_path),
        name="run_sql",
        arguments={"sql": "SELECT 1"},
        ok=True,
        output="[[1]]",
        latency_ms=12.5,
    )
    log_file = tmp_path / "mcp_tool_calls.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["tool"] == "run_sql"
    assert rec["input"] == {"sql": "SELECT 1"}
    assert rec["ok"] is True
    assert rec["output"] == "[[1]]"
    assert rec["latency_ms"] == 12.5


def test_log_tool_call_appends(tmp_path: Path) -> None:
    for i in range(3):
        _log_tool_call(
            str(tmp_path), name="list_tables", arguments={}, ok=True, output=str(i), latency_ms=1.0
        )
    lines = (tmp_path / "mcp_tool_calls.jsonl").read_text().splitlines()
    assert len(lines) == 3


def test_log_tool_call_noop_without_dir(tmp_path: Path) -> None:
    # No directory configured → silent no-op, no file, no exception.
    _log_tool_call(None, name="run_sql", arguments={}, ok=True, output="x", latency_ms=1.0)
    assert list(tmp_path.iterdir()) == []


def test_build_context_profiles_path(tmp_path: Path, monkeypatch: Any) -> None:
    import duckdb

    from labrat.profile.manager import ProfileManager, make_profile

    db = tmp_path / "s.duckdb"
    duckdb.connect(str(db)).close()
    mgr = ProfileManager(profiles_path=tmp_path / "profiles.json")
    mgr.add(make_profile(name="served", dialect="duckdb", path=str(db)))
    # Route the module-level default manager at the config seam:
    import labrat.mcp.config as mcp_config

    monkeypatch.setattr(mcp_config, "ProfileManager", lambda: mgr)
    monkeypatch.setenv("LABRAT_MCP_PROFILES", "served")
    monkeypatch.delenv("LABRAT_MCP_CONNECTIONS", raising=False)

    from labrat.mcp.server import _build_context_from_env

    ctx, live = _build_context_from_env()
    assert ctx.profile_name == "served" and ctx.read_only is True
    assert set(ctx.connections) == {"served"} and len(live) == 1
    for conn in live:
        conn.disconnect()


def test_listed_tools_uses_exact_policy_order_and_hides_everything_else() -> None:
    registry = _registry("hidden", "run_sql", "list_tables")
    session = PolicySession(_policy("list_tables", "run_sql"))

    listed = _listed_tools(registry, session)

    assert [tool.name for tool in listed] == ["list_tables", "run_sql"]
    assert [tool.description for tool in listed] == [
        "list_tables description",
        "run_sql description",
    ]


def test_listed_tools_without_policy_preserves_registry_order() -> None:
    registry = _registry("second", "first")

    listed = _listed_tools(registry, None)

    assert [tool.name for tool in listed] == ["second", "first"]


def test_dispatch_denial_is_logged_once_and_never_reaches_registry(tmp_path: Path) -> None:
    registry = _SpyRegistry(DispatchResult(ok=True, value={"should": "not happen"}))
    session = PolicySession(_policy("allowed"))
    arguments = {"secret": "must not appear in error output"}

    with pytest.raises(PolicyDenied, match="MCP policy denied tool call") as exc_info:
        asyncio.run(
            _dispatch_tool_call(
                "hidden",
                arguments,
                ctx=ToolContext(),
                registry=registry,
                policy=session,
                log_dir=str(tmp_path),
            )
        )

    assert registry.dispatch_calls == []
    assert "secret" not in str(exc_info.value)
    records = [
        json.loads(line) for line in (tmp_path / "mcp_tool_calls.jsonl").read_text().splitlines()
    ]
    assert len(records) == 1
    assert records[0]["ok"] is False
    assert records[0]["output"] == "Error: MCP policy denied tool call"
    assert "secret" not in records[0]["output"]


def test_success_dispatch_records_only_after_ok_result() -> None:
    events: list[str] = []
    registry = _SpyRegistry(DispatchResult(ok=True, value={"answer": 1}))
    registry.events = events
    session = _RecordingPolicy(_policy("run_sql"), events)

    content = asyncio.run(
        _dispatch_tool_call(
            "run_sql",
            {"value": "x"},
            ctx=ToolContext(),
            registry=registry,
            policy=session,
            log_dir=None,
        )
    )

    assert events == ["authorize", "dispatch", "record"]
    assert [item.text for item in content] == ['{"answer": 1}']


def test_failed_dispatch_does_not_record_success() -> None:
    events: list[str] = []
    registry = _SpyRegistry(DispatchResult(ok=False, value=None, error="boom"))
    registry.events = events
    session = _RecordingPolicy(_policy("run_sql"), events)

    content = asyncio.run(
        _dispatch_tool_call(
            "run_sql",
            {},
            ctx=ToolContext(),
            registry=registry,
            policy=session,
            log_dir=None,
        )
    )

    assert events == ["authorize", "dispatch"]
    assert [item.text for item in content] == ["Error: boom"]


def test_dispatch_without_policy_preserves_success_payload_and_log(tmp_path: Path) -> None:
    registry = _SpyRegistry(DispatchResult(ok=True, value={"answer": 1}))
    ctx = ToolContext()

    content = asyncio.run(
        _dispatch_tool_call(
            "anything",
            {"value": "x"},
            ctx=ctx,
            registry=registry,
            policy=None,
            log_dir=str(tmp_path),
        )
    )

    assert registry.dispatch_calls == [("anything", {"value": "x"}, ctx)]
    assert [item.text for item in content] == ['{"answer": 1}']
    record = json.loads((tmp_path / "mcp_tool_calls.jsonl").read_text())
    assert record["ok"] is True and record["output"] == '{"answer": 1}'


def test_dispatch_without_policy_preserves_error_contract_and_log(tmp_path: Path) -> None:
    registry = _SpyRegistry(DispatchResult(ok=False, value=None, error="legacy failure"))

    content = asyncio.run(
        _dispatch_tool_call(
            "anything",
            {},
            ctx=ToolContext(),
            registry=registry,
            policy=None,
            log_dir=str(tmp_path),
        )
    )

    assert [item.text for item in content] == ["Error: legacy failure"]
    record = json.loads((tmp_path / "mcp_tool_calls.jsonl").read_text())
    assert record["ok"] is False and record["output"] == "Error: legacy failure"


def test_serve_loads_policy_before_context_and_stdio(monkeypatch: Any) -> None:
    import labrat.mcp.server as mcp_server

    events: list[str] = []
    raw_policy = object()
    session = object()
    registry = ToolRegistry()

    class _Connection:
        def disconnect(self) -> None:
            events.append("disconnect")

    class _Server:
        def get_capabilities(self, **kwargs: object) -> object:
            events.append("capabilities")
            return object()

        async def run(
            self, read_stream: object, write_stream: object, init_options: object
        ) -> None:
            events.append("run")

    def load_policy(env: object) -> object:
        events.append("load_policy")
        return raw_policy

    def make_session(policy: object) -> object:
        assert policy is raw_policy
        events.append("policy_session")
        return session

    def build_context() -> tuple[ToolContext, list[_Connection]]:
        events.append("build_context")
        return ToolContext(), [_Connection()]

    def build_registry() -> ToolRegistry:
        events.append("build_registry")
        return registry

    def build_server(
        ctx: ToolContext, passed_registry: ToolRegistry, policy: object = None
    ) -> _Server:
        assert passed_registry is registry and policy is session
        events.append("build_server")
        return _Server()

    @asynccontextmanager
    async def fake_stdio() -> AsyncIterator[tuple[object, object]]:
        events.append("stdio")
        yield object(), object()

    def init_options(**kwargs: object) -> object:
        events.append("init_options")
        return object()

    monkeypatch.setattr(mcp_server, "load_policy_from_env", load_policy)
    monkeypatch.setattr(mcp_server, "PolicySession", make_session)
    monkeypatch.setattr(mcp_server, "_build_context_from_env", build_context)
    monkeypatch.setattr(mcp_server, "build_data_tools_registry", build_registry)
    monkeypatch.setattr(mcp_server, "_build_server", build_server)
    monkeypatch.setattr(mcp_server, "stdio_server", fake_stdio)
    monkeypatch.setattr(mcp_server, "InitializationOptions", init_options)

    asyncio.run(_serve())

    assert events.index("load_policy") < events.index("build_context")
    assert events.index("load_policy") < events.index("stdio")
    assert events == [
        "load_policy",
        "policy_session",
        "build_context",
        "build_registry",
        "build_server",
        "capabilities",
        "init_options",
        "stdio",
        "run",
        "disconnect",
    ]
