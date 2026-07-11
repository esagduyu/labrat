from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest

from labrat.agent.providers.base import RateLimitError
from labrat.eval.benchmarks.dab.codex_host import CodexInfrastructureError
from labrat.mcp.policy import load_policy_from_env


def test_fixture_is_deterministic_and_supports_the_join_aggregate(tmp_path: Path) -> None:
    from scripts.diagnose_codex_host_cache import _ANSWER_SQL, _create_fixture

    first = tmp_path / "first.duckdb"
    second = tmp_path / "second.duckdb"
    _create_fixture(first)
    _create_fixture(second)

    expected = [("Software", 360, 2, 2)]
    for path in (first, second):
        connection = duckdb.connect(str(path), read_only=True)
        try:
            tables = connection.execute("SHOW TABLES").fetchall()
            answer = connection.execute(_ANSWER_SQL).fetchall()
        finally:
            connection.close()
        assert tables == [("customers",), ("order_items",), ("orders",), ("products",)]
        assert answer == expected


def test_prompt_requires_one_call_per_turn_in_the_fixed_sequence() -> None:
    from scripts.diagnose_codex_host_cache import _PROMPT, _REQUIRED_TOOL_SEQUENCE

    assert len(_REQUIRED_TOOL_SEQUENCE) == 9
    assert _REQUIRED_TOOL_SEQUENCE == (
        "list_tables",
        "describe_table",
        "describe_table",
        "describe_table",
        "describe_table",
        "verify_join",
        "verify_join",
        "verify_join",
        "run_sql",
    )
    assert "one tool call per assistant response" in _PROMPT.lower()
    assert "exact order" in _PROMPT.lower()
    cursor = -1
    for step, tool_name in enumerate(_REQUIRED_TOOL_SEQUENCE, start=1):
        cursor = _PROMPT.index(f"{step}. {tool_name}", cursor + 1)


@pytest.mark.asyncio
async def test_responses_arm_uses_luna_low_stable_cache_core_tools_and_raw_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import diagnose_codex_host_cache as diagnostic

    database = tmp_path / "fixture.duckdb"
    diagnostic._create_fixture(database)
    captured: dict[str, Any] = {}

    class FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            captured["provider"] = kwargs
            self.usage = {
                "input_tokens": 100,
                "cached_tokens": 60,
                "output_tokens": 20,
                "requests": 10,
            }
            self.request_usage = [
                {"request": index, "input_tokens": 10, "cached_tokens": 6} for index in range(1, 11)
            ]

    async def fake_run_agent_task(**kwargs: Any) -> SimpleNamespace:
        captured["runner"] = kwargs
        callback = kwargs["on_tool_call"]
        for tool_name, arguments in diagnostic._CALLS:
            callback(tool_name, dict(arguments), True, '{"ok":true}', 1.25)
        return SimpleNamespace(final_text="Software, 360, 2 orders, 2 customers", tool_calls=9)

    monkeypatch.setattr(diagnostic, "CodexSubscriptionProvider", FakeProvider)
    monkeypatch.setattr(diagnostic, "run_agent_task", fake_run_agent_task)

    metrics = await diagnostic._run_responses(
        database,
        tmp_path / "responses",
        prompt=diagnostic._PROMPT,
    )

    assert captured["provider"] == {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "cache_key": diagnostic._CACHE_KEY,
    }
    runner = captured["runner"]
    assert runner["prompt"] == diagnostic._PROMPT
    assert runner["verify"] is False
    assert runner["enable_ledger"] is False
    assert runner["ctx"].read_only is True
    assert tuple(tool.name for tool in runner["registry"].tools) == diagnostic._CORE_TOOLS
    assert metrics == {
        "status": "ok",
        "input_tokens": 100,
        "cached_input_tokens": 60,
        "noncached_input_tokens": 40,
        "output_tokens": 20,
        "request_count": 10,
        "request_count_source": "provider_reported",
        "tool_call_count": 9,
        "cache_ratio": 0.6,
        "valid": True,
    }
    tool_rows = [
        json.loads(line)
        for line in (tmp_path / "responses" / "tool_calls.jsonl").read_text().splitlines()
    ]
    request_rows = [
        json.loads(line)
        for line in (tmp_path / "responses" / "request_usage.jsonl").read_text().splitlines()
    ]
    aggregate = json.loads((tmp_path / "responses" / "aggregate_usage.json").read_text())
    assert [row["tool"] for row in tool_rows] == list(diagnostic._REQUIRED_TOOL_SEQUENCE)
    assert len(request_rows) == 10
    assert aggregate == {
        "input_tokens": 100,
        "cached_tokens": 60,
        "output_tokens": 20,
        "requests": 10,
    }
    assert (tmp_path / "responses" / "final_answer.txt").read_text().startswith("Software")


@pytest.mark.asyncio
async def test_native_arm_uses_same_prompt_db_core_policy_and_public_aggregate_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import diagnose_codex_host_cache as diagnostic

    database = tmp_path / "fixture.duckdb"
    diagnostic._create_fixture(database)
    captured: dict[str, Any] = {}

    async def fake_run_codex(prompt: str, config: Any) -> SimpleNamespace:
        captured["prompt"] = prompt
        captured["config"] = config
        artifact_dir = config.artifact_dir
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "mcp_tool_calls.jsonl").write_text(
            "".join(
                json.dumps({"tool": name, "input": arguments, "ok": True}) + "\n"
                for name, arguments in diagnostic._CALLS
            )
        )
        (artifact_dir / "codex_events.jsonl").write_text('{"type":"raw"}\n')
        return SimpleNamespace(
            final_text="Software, 360, 2 orders, 2 customers",
            tool_calls=9,
            latency_seconds=1.0,
            usage={
                "input_tokens": 120,
                "cached_input_tokens": 90,
                "output_tokens": 15,
                "reasoning_output_tokens": 4,
            },
        )

    monkeypatch.setattr(diagnostic, "run_codex", fake_run_codex)
    monkeypatch.setattr(diagnostic.shutil, "which", lambda _name: "/usr/local/bin/codex")

    metrics = await diagnostic._run_native(
        database,
        tmp_path / "native",
        prompt=diagnostic._PROMPT,
    )

    assert captured["prompt"] == diagnostic._PROMPT
    config = captured["config"]
    assert config.model == "gpt-5.6-luna"
    assert config.reasoning_effort == "low"
    assert (tmp_path / "native") not in config.codex_home.parents
    assert (tmp_path / "native") not in config.workspace_dir.parents
    assert not config.codex_home.exists()
    assert not config.workspace_dir.exists()
    assert config.mcp.enabled_tools == diagnostic._CORE_TOOLS
    environment = dict(config.mcp.env)
    connection_spec = json.loads(environment["LABRAT_MCP_CONNECTIONS"])
    assert connection_spec == {
        "main": {
            "db_type": "duckdb",
            "db_path": str(database.resolve()),
            "read_only": True,
        }
    }
    policy = load_policy_from_env({"LABRAT_MCP_POLICY_PATH": environment["LABRAT_MCP_POLICY_PATH"]})
    assert policy is not None
    assert policy.allowed_tools == diagnostic._CORE_TOOLS
    assert policy.source_grants == ()
    assert metrics == {
        "status": "ok",
        "input_tokens": 120,
        "cached_input_tokens": 90,
        "noncached_input_tokens": 30,
        "output_tokens": 15,
        "request_count": 10,
        "request_count_source": "inferred_one_tool_per_response",
        "tool_call_count": 9,
        "cache_ratio": 0.75,
        "valid": True,
    }
    assert (config.artifact_dir / "codex_events.jsonl").exists()


def _ok_metrics(input_tokens: int) -> dict[str, object]:
    return {
        "status": "ok",
        "input_tokens": input_tokens,
        "cached_input_tokens": input_tokens // 2,
        "noncached_input_tokens": input_tokens - input_tokens // 2,
        "output_tokens": 10,
        "request_count": 10,
        "request_count_source": "provider_reported",
        "tool_call_count": 9,
        "cache_ratio": (input_tokens // 2) / input_tokens,
        "valid": True,
    }


def test_validity_requires_minimum_requests_calls_and_exact_successful_sequence() -> None:
    from scripts import diagnose_codex_host_cache as diagnostic

    trace = [
        {"tool": name, "input": dict(arguments), "ok": True}
        for name, arguments in diagnostic._CALLS
    ]
    usage = {"input_tokens": 100, "cached_tokens": 50, "output_tokens": 10}

    assert (
        diagnostic._metrics(usage, "cached_tokens", 4, "provider_reported", 9, trace)["valid"]
        is False
    )
    assert (
        diagnostic._metrics(usage, "cached_tokens", 10, "provider_reported", 4, trace[:4])["valid"]
        is False
    )
    trace[-1] = {"tool": "check_sql", "ok": True}
    assert (
        diagnostic._metrics(usage, "cached_tokens", 10, "provider_reported", 9, trace)["valid"]
        is False
    )
    trace[1]["input"] = dict(diagnostic._CALLS[1][1])
    malformed = diagnostic._metrics(
        {"input_tokens": 100, "cached_tokens": 101, "output_tokens": 10},
        "cached_tokens",
        10,
        "provider_reported",
        9,
        trace,
    )
    assert malformed["cached_input_tokens"] == 101
    assert malformed["noncached_input_tokens"] == 0
    assert malformed["valid"] is False
    trace = [
        {"tool": name, "input": dict(arguments), "ok": True}
        for name, arguments in diagnostic._CALLS
    ]
    trace[1]["input"] = {"table": "wrong"}
    assert (
        diagnostic._metrics(usage, "cached_tokens", 10, "provider_reported", 9, trace)["valid"]
        is False
    )


def test_cli_defaults_to_both_luna_low_and_persists_one_diagnostic_comparison(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import diagnose_codex_host_cache as diagnostic

    output_dir = tmp_path / "default-run"
    calls: list[tuple[str, Path, str]] = []

    async def fake_responses(
        database: Path,
        _artifact_dir: Path,
        *,
        prompt: str,
    ) -> dict[str, object]:
        calls.append(("responses", database, prompt))
        return _ok_metrics(100)

    async def fake_native(
        database: Path,
        _artifact_dir: Path,
        *,
        prompt: str,
    ) -> dict[str, object]:
        calls.append(("native", database, prompt))
        return _ok_metrics(120)

    monkeypatch.setattr(diagnostic, "_default_output_dir", lambda: output_dir)
    monkeypatch.setattr(diagnostic, "_run_responses", fake_responses)
    monkeypatch.setattr(diagnostic, "_run_native", fake_native)

    assert diagnostic.main([]) == 0

    assert [call[0] for call in calls] == ["responses", "native"]
    assert calls[0][1] == calls[1][1] == output_dir / "fixture.duckdb"
    assert calls[0][2] == calls[1][2] == diagnostic._PROMPT
    comparison = json.loads((output_dir / "comparison.json").read_text())
    assert comparison["diagnostic_only"] is True
    assert comparison["valid"] is True
    assert comparison["comparison_valid"] is True
    assert comparison["model"] == "gpt-5.6-luna"
    assert comparison["reasoning_effort"] == "low"
    assert comparison["tool_profile"] == "dab-core-v1"
    assert comparison["cache_key"] == diagnostic._CACHE_KEY
    assert comparison["prompt_sha256"] == hashlib.sha256(diagnostic._PROMPT.encode()).hexdigest()
    assert comparison["required_tool_sequence"] == list(diagnostic._REQUIRED_TOOL_SEQUENCE)
    assert comparison["selected_arms"] == ["responses", "native"]
    assert comparison["arms"] == {
        "responses": _ok_metrics(100),
        "native": _ok_metrics(120),
    }
    for forbidden in ("submission.json", "trials.jsonl", "report.md", "config.json"):
        assert not (output_dir / forbidden).exists()

    calls.clear()
    single_dir = tmp_path / "single-arm"
    assert diagnostic.main(["--output-dir", str(single_dir), "--arm", "responses"]) == 0
    assert [call[0] for call in calls] == ["responses"]
    single = json.loads((single_dir / "comparison.json").read_text())
    assert single["valid"] is True
    assert single["comparison_valid"] is False
    assert single["selected_arms"] == ["responses"]


@pytest.mark.asyncio
async def test_responses_rate_limit_is_classified_without_retry_and_preserves_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import diagnose_codex_host_cache as diagnostic

    database = tmp_path / "fixture.duckdb"
    diagnostic._create_fixture(database)
    calls = 0

    class FakeProvider:
        def __init__(self, **_kwargs: object) -> None:
            self.usage = {
                "input_tokens": 50,
                "cached_tokens": 20,
                "output_tokens": 5,
                "requests": 4,
            }
            self.request_usage = [{"request": 1, "input_tokens": 50}]

    async def rate_limited(**_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise RateLimitError(rate_limit={"resets_in_seconds": 60})

    monkeypatch.setattr(diagnostic, "CodexSubscriptionProvider", FakeProvider)
    monkeypatch.setattr(diagnostic, "run_agent_task", rate_limited)

    metrics = await diagnostic._run_responses(
        database,
        tmp_path / "responses",
        prompt=diagnostic._PROMPT,
    )

    assert calls == 1
    assert metrics["status"] == "rate_limit"
    assert metrics["input_tokens"] == 50
    assert metrics["cached_input_tokens"] == 20
    assert metrics["valid"] is False
    assert (tmp_path / "responses" / "request_usage.jsonl").exists()


@pytest.mark.asyncio
async def test_native_rate_limit_is_classified_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import diagnose_codex_host_cache as diagnostic

    database = tmp_path / "fixture.duckdb"
    diagnostic._create_fixture(database)
    calls = 0

    async def rate_limited(_prompt: str, _config: object) -> None:
        nonlocal calls
        calls += 1
        raise CodexInfrastructureError("rate_limit", meta={"resets_in_seconds": 60})

    monkeypatch.setattr(diagnostic, "run_codex", rate_limited)
    monkeypatch.setattr(diagnostic.shutil, "which", lambda _name: "/usr/local/bin/codex")

    metrics = await diagnostic._run_native(
        database,
        tmp_path / "native",
        prompt=diagnostic._PROMPT,
    )

    assert calls == 1
    assert metrics["status"] == "rate_limit"
    assert metrics["valid"] is False


def test_cli_rate_limit_stops_before_the_other_arm_and_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import diagnose_codex_host_cache as diagnostic

    native_calls = 0

    async def rate_limited(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            **_ok_metrics(10),
            "status": "rate_limit",
            "valid": False,
        }

    async def unexpected_native(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal native_calls
        native_calls += 1
        return _ok_metrics(10)

    monkeypatch.setattr(diagnostic, "_run_responses", rate_limited)
    monkeypatch.setattr(diagnostic, "_run_native", unexpected_native)
    output_dir = tmp_path / "rate-limit"

    assert diagnostic.main(["--output-dir", str(output_dir), "--arm", "both"]) == 4
    assert native_calls == 0
    comparison = json.loads((output_dir / "comparison.json").read_text())
    assert comparison["valid"] is False
    assert comparison["arms"]["responses"]["status"] == "rate_limit"
    assert "native" not in comparison["arms"]


def test_nonempty_output_is_rejected_before_an_arm_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import diagnose_codex_host_cache as diagnostic

    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    (output_dir / "historical.txt").write_text("do not overwrite")
    calls = 0

    async def unexpected(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _ok_metrics(10)

    monkeypatch.setattr(diagnostic, "_run_responses", unexpected)

    with pytest.raises(SystemExit) as raised:
        diagnostic.main(["--output-dir", str(output_dir), "--arm", "responses"])

    assert raised.value.code == 2
    assert calls == 0
    assert (output_dir / "historical.txt").read_text() == "do not overwrite"


@pytest.mark.parametrize(
    "override",
    [
        ["--model", "gpt-5.6-terra"],
        ["--reasoning-effort", "high"],
        ["--effort", "high"],
    ],
)
def test_cli_rejects_model_and_effort_overrides_before_an_arm_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: list[str],
) -> None:
    from scripts import diagnose_codex_host_cache as diagnostic

    calls = 0

    async def unexpected(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _ok_metrics(10)

    monkeypatch.setattr(diagnostic, "_run_responses", unexpected)
    output_dir = tmp_path / "fixed-tier"
    with pytest.raises(SystemExit) as raised:
        diagnostic.main(["--output-dir", str(output_dir), "--arm", "responses", *override])

    assert raised.value.code == 2
    assert calls == 0
    assert not output_dir.exists()
