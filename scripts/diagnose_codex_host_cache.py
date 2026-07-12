#!/usr/bin/env python3
"""Run a synthetic, diagnostic-only cache comparison for two Codex hosts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import duckdb

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.providers.base import RateLimitError
from labrat.agent.providers.codex_subscription import CodexSubscriptionProvider
from labrat.agent.runner import run_agent_task
from labrat.agent.tools.base import ToolContext
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.eval.benchmarks.dab.codex_host import (
    CodexAuditError,
    CodexHostConfig,
    CodexInfrastructureError,
    McpLaunch,
    run_codex,
)
from labrat.eval.benchmarks.dab.tool_profiles import filter_registry, resolve_tool_profile
from labrat.mcp.policy import McpPolicy, PolicyLimits, policy_digest

_PROFILE = "dab-core-v1"
_CACHE_KEY = "labrat-codex-host-cache-v1"
_MODEL = "gpt-5.6-luna"
_EFFORT = "low"
_RATE_LIMIT_EXIT_CODE = 4
_REPO_ROOT = Path(__file__).resolve().parents[1]
_ANSWER_SQL = (
    "SELECT p.category, SUM(oi.quantity * p.unit_price) AS revenue, "
    "COUNT(DISTINCT o.order_id) AS order_count, COUNT(DISTINCT c.customer_id) AS customer_count "
    "FROM customers c JOIN orders o ON o.customer_id=c.customer_id "
    "JOIN order_items oi ON oi.order_id=o.order_id "
    "JOIN products p ON p.product_id=oi.product_id WHERE o.status='paid' "
    "AND o.ordered_at>=DATE '2025-01-01' AND o.ordered_at<DATE '2026-01-01' "
    "GROUP BY p.category ORDER BY revenue DESC, p.category LIMIT 1"
)


def _join_args(left: str, left_key: str, right: str, right_key: str) -> dict[str, str]:
    return dict(left_table=left, left_column=left_key, right_table=right, right_column=right_key)


_CALLS = (
    ("list_tables", {}),
    ("describe_table", {"table": "customers"}),
    ("describe_table", {"table": "orders"}),
    ("describe_table", {"table": "order_items"}),
    ("describe_table", {"table": "products"}),
    ("verify_join", _join_args("orders", "customer_id", "customers", "customer_id")),
    ("verify_join", _join_args("order_items", "order_id", "orders", "order_id")),
    ("verify_join", _join_args("order_items", "product_id", "products", "product_id")),
    ("run_sql", {"query": _ANSWER_SQL}),
)
_REQUIRED_TOOL_SEQUENCE = tuple(name for name, _ in _CALLS)
_STEPS = "\n".join(
    f"{index}. {name} {json.dumps(arguments, separators=(',', ':'))}"
    for index, (name, arguments) in enumerate(_CALLS, 1)
)
_PROMPT = (
    "This is a deterministic cache diagnostic against the read-only synthetic DuckDB alias `main`.\n\n"
    "Question: Among paid orders placed in 2025, which product category generated the most "
    "revenue, and how many distinct orders and customers contributed to it?\n\n"
    "Make one tool call per assistant response, in this exact order. "
    f"Do not skip, reorder, combine, or add calls. Use exactly these arguments.\n\n{_STEPS}\n\n"
    "After run_sql, answer concisely without another tool call."
)
_SYSTEM = "You are a read-only SQL analyst. Use only supplied tools and follow the fixed sequence."
_CORE_PROFILE = resolve_tool_profile(_PROFILE, build_data_tools_registry())
_CORE_TOOLS = _CORE_PROFILE.tools
Metrics = dict[str, object]
Trace = list[dict[str, Any]]


def _mcp_interpreter(repo_root: Path = _REPO_ROOT) -> Path:
    """Return the repo venv launcher path without resolving its final symlink."""
    root = repo_root.expanduser().resolve()
    interpreter = root / ".venv/bin/python"
    if not interpreter.is_file():
        raise FileNotFoundError(
            f"Native diagnostic requires a repo virtualenv interpreter: {interpreter}"
        )
    if not os.access(interpreter, os.X_OK):
        raise PermissionError(
            f"Repo virtualenv interpreter is not executable: {interpreter}"
        )
    return interpreter


def _create_fixture(path: Path) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Fixture already exists: {path}")
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE customers(customer_id INTEGER PRIMARY KEY,name VARCHAR,region VARCHAR);
            CREATE TABLE products(product_id INTEGER PRIMARY KEY,category VARCHAR,unit_price INTEGER);
            CREATE TABLE orders(order_id INTEGER PRIMARY KEY,customer_id INTEGER REFERENCES customers(customer_id),ordered_at DATE,status VARCHAR);
            CREATE TABLE order_items(order_id INTEGER REFERENCES orders(order_id),product_id INTEGER REFERENCES products(product_id),quantity INTEGER,PRIMARY KEY(order_id,product_id));
            INSERT INTO customers VALUES (1,'Ada','North'),(2,'Lin','South'),(3,'Grace','North'),(4,'Edsger','West');
            INSERT INTO products VALUES (10,'Hardware',50),(11,'Software',120),(12,'Services',80);
            INSERT INTO orders VALUES (100,1,'2025-01-10','paid'),(101,2,'2025-02-10','paid'),
                (102,3,'2025-03-01','refunded'),(103,4,'2025-04-02','paid'),(104,1,'2024-12-01','paid');
            INSERT INTO order_items VALUES (100,10,2),(100,11,1),(101,11,2),(102,12,5),
                (103,10,1),(103,12,3),(104,11,10);
            """
        )
    finally:
        connection.close()


def _runtime(database: Path) -> tuple[Any, ToolContext, DuckDBConnection]:
    full = build_data_tools_registry()
    registry = filter_registry(full, _CORE_PROFILE)
    connection = DuckDBConnection(database.resolve(), read_only=True)
    connection.connect()
    context = ToolContext(
        connections={"main": connection},
        catalogs={"main": connection.introspect_catalog()},
        primary="main",
        profile_name="codex-host-cache-diagnostic",
        read_only=True,
    )
    return registry, context, connection


def _jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    )


def _read_trace(path: Path) -> Trace:
    try:
        values: list[object] = [json.loads(line) for line in path.read_text().splitlines()]
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        return []
    return cast(Trace, values) if all(isinstance(value, dict) for value in values) else []


def _count(usage: Mapping[str, Any], key: str) -> int:
    value = usage.get(key, 0)
    return value if type(value) is int and value >= 0 else 0


def _metrics(
    usage: Mapping[str, Any],
    cached_key: str,
    requests: int,
    request_source: Literal["provider_reported", "inferred_one_tool_per_response", "unavailable"],
    tools: int,
    trace: Trace,
    status: Literal["ok", "rate_limit"] = "ok",
) -> Metrics:
    inputs = _count(usage, "input_tokens")
    cached = _count(usage, cached_key)
    usage_ok = inputs > 0 and cached <= inputs
    sequence_ok = len(trace) == len(_CALLS) and all(
        row.get("tool") == name and row.get("input") == arguments and row.get("ok") is True
        for row, (name, arguments) in zip(trace, _CALLS, strict=True)
    )
    requests_ok = requests >= 5 if request_source == "provider_reported" else tools >= 5
    valid = (
        status == "ok"
        and usage_ok
        and requests_ok
        and tools >= 5
        and tools == len(trace)
        and sequence_ok
    )
    return {
        "status": status,
        "input_tokens": inputs,
        "cached_input_tokens": cached,
        "noncached_input_tokens": max(inputs - cached, 0),
        "output_tokens": _count(usage, "output_tokens"),
        "request_count": max(requests, 0),
        "request_count_source": request_source,
        "tool_call_count": max(tools, 0),
        "cache_ratio": round(cached / inputs, 6) if inputs else 0.0,
        "valid": valid,
    }


async def _run_responses(
    database: Path,
    artifact_dir: Path,
    *,
    prompt: str,
) -> Metrics:
    artifact_dir.mkdir(parents=True, mode=0o700)
    registry, context, connection = _runtime(database)
    provider = CodexSubscriptionProvider(
        model=_MODEL, reasoning_effort=_EFFORT, cache_key=_CACHE_KEY
    )
    trace: Trace = []

    def record(name: str, arguments: dict[str, Any], ok: bool, output: str, latency: float) -> None:
        trace.append(
            {
                "tool": name,
                "input": arguments,
                "ok": ok,
                "output": output,
                "latency_ms": round(latency, 3),
            }
        )

    result: Any = None
    status: Literal["ok", "rate_limit"] = "ok"
    try:
        result = await run_agent_task(
            prompt=prompt,
            ctx=context,
            registry=registry,
            provider=provider,
            system_prompt=_SYSTEM,
            max_turns=len(_CALLS) + 2,
            max_tool_calls=len(_CALLS),
            verify=False,
            on_tool_call=record,
            enable_ledger=False,
        )
    except RateLimitError:
        status = "rate_limit"
    finally:
        _jsonl(artifact_dir / "tool_calls.jsonl", trace)
        _jsonl(artifact_dir / "request_usage.jsonl", provider.request_usage)
        (artifact_dir / "aggregate_usage.json").write_text(
            json.dumps(provider.usage, indent=2, sort_keys=True) + "\n"
        )
        connection.disconnect()
    if result is not None:
        (artifact_dir / "final_answer.txt").write_text(result.final_text)
    tools = int(result.tool_calls) if result is not None else len(trace)
    return _metrics(
        provider.usage,
        "cached_tokens",
        _count(provider.usage, "requests"),
        "provider_reported",
        tools,
        trace,
        status,
    )


def _policy(path: Path, schema_hash: str) -> None:
    policy = McpPolicy(
        schema_version=1,
        run_manifest_sha256=hashlib.sha256(_PROMPT.encode()).hexdigest(),
        task_id="codex-host-cache-v1",
        trial_num=0,
        attempt_num=0,
        primary_database="main",
        allowed_tools=_CORE_TOOLS,
        source_grants=(),
        mongo_grants=(),
        limits=PolicyLimits(
            max_rows=1000,
            max_sample_rows=100,
            max_tables=50,
            max_output_chars=20000,
            max_sql_chars=10000,
            max_identifier_chars=128,
            max_mongo_depth=8,
            max_mongo_filter_bytes=4096,
        ),
        cartographer_enabled=False,
        builder_sha256=schema_hash,
        digest="0" * 64,
    )
    policy = policy.model_copy(update={"digest": policy_digest(policy)})
    path.write_text(policy.model_dump_json())
    path.chmod(0o600)


def _mcp_launch(database: Path, artifacts: Path, policy_path: Path) -> McpLaunch:
    connections = json.dumps(
        {"main": {"db_type": "duckdb", "db_path": str(database.resolve()), "read_only": True}},
        separators=(",", ":"),
        sort_keys=True,
    )
    return McpLaunch(
        command=str(_mcp_interpreter()),
        args=("-m", "labrat.mcp.server"),
        cwd=_REPO_ROOT,
        env=(
            ("LABRAT_MCP_CONNECTIONS", connections),
            ("LABRAT_MCP_PRIMARY", "main"),
            ("LABRAT_MCP_LOG_DIR", str(artifacts)),
            ("LABRAT_MCP_POLICY_PATH", str(policy_path.resolve())),
        ),
        enabled_tools=_CORE_TOOLS,
    )


async def _run_native(
    database: Path,
    native_dir: Path,
    *,
    prompt: str,
) -> Metrics:
    native_dir.mkdir(parents=True, mode=0o700)
    artifacts = (native_dir / "artifacts").resolve()
    policy_path = native_dir / "membership-policy.json"
    _policy(policy_path, _CORE_PROFILE.schema_sha256)
    executable = shutil.which("codex")
    if executable is None:
        raise FileNotFoundError("codex executable was not found on PATH")
    launch = _mcp_launch(database, artifacts, policy_path)
    with tempfile.TemporaryDirectory(prefix="labrat-codex-cache-") as private:
        home, workspace = Path(private) / "home", Path(private) / "workspace"
        home.mkdir(mode=0o700)
        workspace.mkdir(mode=0o700)
        config = CodexHostConfig(
            executable=Path(executable).resolve(),
            expected_version="0.144.1",
            model=_MODEL,
            reasoning_effort=_EFFORT,
            source_codex_home=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve(),
            codex_home=home.resolve(),
            workspace_dir=workspace.resolve(),
            artifact_dir=artifacts,
            timeout_seconds=900,
            mcp=launch,
        )
        try:
            result = await run_codex(prompt, config)
        except CodexInfrastructureError as exc:
            if exc.reason != "rate_limit":
                raise
            return _metrics({}, "cached_input_tokens", 0, "unavailable", 0, [], "rate_limit")
    trace = _read_trace(artifacts / "mcp_tool_calls.jsonl")
    tools = int(result.tool_calls)
    return _metrics(
        result.usage,
        "cached_input_tokens",
        tools + 1,
        "inferred_one_tool_per_response",
        tools,
        trace,
    )


def _default_output_dir() -> Path:
    return Path("runs/codex-host-cache") / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


async def _diagnose(
    output: Path,
    *,
    arm: Literal["responses", "native", "both"],
) -> int:
    database = output / "fixture.duckdb"
    selected = ("responses", "native") if arm == "both" else (arm,)
    arms: dict[str, Metrics] = {}
    for name in selected:
        runner = _run_responses if name == "responses" else _run_native
        arms[name] = await runner(database, output / name, prompt=_PROMPT)
        if arms[name]["status"] == "rate_limit":
            break
    valid = len(arms) == len(selected) and all(value["valid"] is True for value in arms.values())
    comparison = {
        "diagnostic_only": True,
        "valid": valid,
        "comparison_valid": arm == "both" and valid,
        "model": _MODEL,
        "reasoning_effort": _EFFORT,
        "tool_profile": _PROFILE,
        "cache_key": _CACHE_KEY,
        "prompt_sha256": hashlib.sha256(_PROMPT.encode()).hexdigest(),
        "required_tool_sequence": list(_REQUIRED_TOOL_SEQUENCE),
        "selected_arms": list(selected),
        "arms": arms,
    }
    (output / "comparison.json").write_text(json.dumps(comparison, indent=2, sort_keys=True) + "\n")
    if any(value["status"] == "rate_limit" for value in arms.values()):
        return _RATE_LIMIT_EXIT_CODE
    return 0 if valid else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare native and Responses cache telemetry.")
    parser.add_argument("--arm", choices=("responses", "native", "both"), default="both")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    output = (args.output_dir or _default_output_dir()).expanduser().resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error(f"--output-dir must be absent or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "prompt.txt").write_text(_PROMPT)
    _create_fixture(output / "fixture.duckdb")
    try:
        code = asyncio.run(_diagnose(output, arm=args.arm))
    except (CodexAuditError, CodexInfrastructureError, OSError, ValueError) as exc:
        print(f"Diagnostic failed: {exc}", file=sys.stderr)
        return 2
    if code == _RATE_LIMIT_EXIT_CODE:
        print("Codex rate limit reached; no retry was attempted.", file=sys.stderr)
    print(f"Diagnostic artifacts: {output}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
