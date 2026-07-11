"""Offline tests for CodexSubscriptionProvider — no network, no real token."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import pytest

from labrat.agent.loop import TextBlock, ToolUseBlock
from labrat.agent.providers import build_provider
from labrat.agent.providers.codex_subscription import (
    CodexSubscriptionProvider,
    _account_id_from_jwt,
    _iter_sse_events,
    _reduce_event,
)


def _make_jwt(claims: dict[str, Any]) -> str:
    """Build an unsigned JWT (header.payload.sig) carrying the given claims."""

    def seg(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{seg({'alg': 'none'})}.{seg(claims)}.sig"


# ---- 1. account id from JWT --------------------------------------------------


def test_account_id_from_jwt_reads_claim() -> None:
    token = _make_jwt(
        {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-123"}, "exp": 9999999999}
    )
    assert _account_id_from_jwt(token) == "acct-123"


def test_account_id_from_jwt_missing_claim_returns_none() -> None:
    assert _account_id_from_jwt(_make_jwt({"exp": 1})) is None
    assert _account_id_from_jwt("not-a-jwt") is None


# ---- 2. request translation --------------------------------------------------


def test_to_responses_request_translates_tool_round_trip() -> None:
    provider = CodexSubscriptionProvider(model="gpt-5.5", reasoning_effort="medium")
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "How many rows?"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "run_sql",
                    "input": {"sql": "SELECT 1"},
                },
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "42"}],
        },
    ]
    tools = [
        {
            "name": "run_sql",
            "description": "run a query",
            "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}},
        }
    ]

    body = provider._to_responses_request(messages, tools, system="You are LabRat.")

    assert body["model"] == "gpt-5.5"
    assert body["instructions"] == "You are LabRat."
    assert body["store"] is False
    assert body["reasoning"]["effort"] == "medium"
    assert body["parallel_tool_calls"] is True

    items = body["input"]
    # user text → input_text
    assert items[0] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "How many rows?"}],
    }
    # assistant text → output_text
    assert items[1]["content"][0]["type"] == "output_text"
    # tool_use → function_call with matching call_id and json-encoded arguments
    fc = items[2]
    assert fc["type"] == "function_call"
    assert fc["call_id"] == "call_1"
    assert fc["name"] == "run_sql"
    assert json.loads(fc["arguments"]) == {"sql": "SELECT 1"}
    # tool_result → function_call_output with the same call_id
    out = items[3]
    assert out["type"] == "function_call_output"
    assert out["call_id"] == "call_1"
    assert out["output"] == "42"

    # tools translated to Responses function shape
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["name"] == "run_sql"
    assert body["tools"][0]["parameters"]["properties"]["sql"]["type"] == "string"
    assert body["tools"][0]["strict"] is False


# ---- 3. SSE reducer ----------------------------------------------------------


async def _alines(text: str):
    for line in text.split("\n"):
        yield line


class _FakeStreamResponse:
    def __init__(self, status_code: int, *, sse: str = "", error: str = "") -> None:
        import httpx

        request = httpx.Request("POST", "https://example.test/responses")
        self._response = httpx.Response(
            status_code,
            request=request,
            json={"error": {"message": error}} if error else {},
        )
        self._sse = sse

    @property
    def is_error(self) -> bool:
        return self._response.is_error

    async def aread(self) -> bytes:
        return self._response.content

    def raise_for_status(self) -> None:
        self._response.raise_for_status()

    async def aiter_lines(self):
        async for line in _alines(self._sse):
            yield line

    async def __aenter__(self) -> _FakeStreamResponse:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _FakeAsyncClient:
    def __init__(self, responses: list[_FakeStreamResponse]) -> None:
        self.responses = list(responses)
        self.bodies: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def stream(self, *_args: Any, **kwargs: Any) -> _FakeStreamResponse:
        self.bodies.append(kwargs["json"])
        return self.responses.pop(0)


def _stub_provider_auth(provider: CodexSubscriptionProvider, monkeypatch: Any) -> None:
    monkeypatch.setattr(provider, "_load_auth", lambda: {})
    monkeypatch.setattr(provider, "_maybe_refresh", lambda auth: auth)
    monkeypatch.setattr(provider, "_headers", lambda _auth: {})


def _successful_sse(text: str = "done") -> str:
    return (
        f'data: {{"type":"response.output_text.delta","delta":{json.dumps(text)}}}\n\n'
        'data: {"type":"response.output_item.done","item":{"type":"message",'
        f'"id":"msg_1","role":"assistant","phase":"final_answer","content":['
        f'{{"type":"output_text","text":{json.dumps(text)}}}]}}}}\n\n'
        'data: {"type":"response.completed","response":{"id":"resp_1","usage":{'
        '"input_tokens":1200,"output_tokens":10,"input_tokens_details":{'
        '"cached_tokens":1024,"cache_write_tokens":0}}}}\n\n'
    )


async def test_sse_reducer_emits_text_then_tool_call() -> None:
    stream = (
        'data: {"type": "response.output_text.delta", "delta": "Hello"}\n'
        "\n"
        ": heartbeat\n"
        'data: {"type": "response.output_item.done", "item": '
        '{"type": "function_call", "call_id": "call_9", "name": "run_sql", '
        '"arguments": "{\\"sql\\": \\"SELECT 1\\"}"}}\n'
        "\n"
        'data: {"type": "response.completed"}\n'
        "\n"
        "data: [DONE]\n"
        "\n"
    )
    blocks = []
    async for event in _iter_sse_events(_alines(stream)):
        blocks.extend(_reduce_event(event))

    assert len(blocks) == 2
    assert isinstance(blocks[0], TextBlock)
    assert blocks[0].text == "Hello"
    assert isinstance(blocks[1], ToolUseBlock)
    assert blocks[1].id == "call_9"
    assert blocks[1].name == "run_sql"
    assert blocks[1].input == {"sql": "SELECT 1"}


def test_reduce_event_raises_on_failed() -> None:
    with pytest.raises(RuntimeError, match=r"response\.failed"):
        _reduce_event(
            {"type": "response.failed", "response": {"error": {"message": "rate_limited"}}}
        )


def test_reduce_event_ignores_unknown_events() -> None:
    assert _reduce_event({"type": "response.created"}) == []
    assert _reduce_event({"type": "response.completed"}) == []


# ---- 4. refresh decision -----------------------------------------------------


def _write_auth(path: Path, exp: int, refresh: str = "refresh-abc") -> None:
    access = _make_jwt({"exp": exp})
    id_token = _make_jwt(
        {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-xyz"}, "exp": exp}
    )
    path.write_text(
        json.dumps(
            {"tokens": {"access_token": access, "id_token": id_token, "refresh_token": refresh}}
        )
    )


def test_maybe_refresh_skips_when_token_fresh(tmp_path: Path, monkeypatch: Any) -> None:
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path, exp=int(time.time()) + 3600)
    provider = CodexSubscriptionProvider(auth_path=auth_path)

    import httpx

    def _boom(*_a: Any, **_k: Any) -> Any:  # refresh must NOT be called
        raise AssertionError("refresh should not be called for a fresh token")

    monkeypatch.setattr(httpx, "post", _boom)
    out = provider._maybe_refresh(provider._load_auth())
    assert out["tokens"]["access_token"]


def test_maybe_refresh_triggers_when_token_near_expiry(tmp_path: Path, monkeypatch: Any) -> None:
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path, exp=int(time.time()) + 10)  # within the skew window
    provider = CodexSubscriptionProvider(auth_path=auth_path)

    calls: dict[str, Any] = {}

    class _Resp:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, Any]:
            return {"access_token": "NEW-ACCESS", "refresh_token": "NEW-REFRESH"}

    def _fake_post(url: str, **kwargs: Any) -> _Resp:
        calls["url"] = url
        calls["json"] = kwargs.get("json")
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "post", _fake_post)
    out = provider._maybe_refresh(provider._load_auth())

    assert calls["json"]["grant_type"] == "refresh_token"
    assert out["tokens"]["access_token"] == "NEW-ACCESS"
    # persisted back to disk
    on_disk = json.loads(auth_path.read_text())
    assert on_disk["tokens"]["access_token"] == "NEW-ACCESS"


# ---- 5. registration ---------------------------------------------------------


def test_build_provider_registers_codex() -> None:
    provider = build_provider("codex", "gpt-5.5", reasoning="medium")
    assert isinstance(provider, CodexSubscriptionProvider)
    assert provider._reasoning_effort == "medium"
    assert provider._model == "gpt-5.5"


def test_build_provider_codex_defaults_to_medium() -> None:
    provider = build_provider("codex", "gpt-5.5")
    assert isinstance(provider, CodexSubscriptionProvider)
    assert provider._reasoning_effort == "medium"


def test_build_provider_rejects_unknown_codex_config_but_not_other_provider_models() -> None:
    with pytest.raises(ValueError, match="Valid models"):
        build_provider("codex", "gpt-5.6-moon", reasoning="low")


# ---- 6. usage capture (response.completed.usage) ----------------------------


def test_extract_usage_responses_shape() -> None:
    from labrat.agent.providers.codex_subscription import _extract_usage

    ev = {
        "type": "response.completed",
        "response": {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 50,
                "input_tokens_details": {"cached_tokens": 900},
                "output_tokens_details": {"reasoning_tokens": 30},
            }
        },
    }
    assert _extract_usage(ev) == {
        "input_tokens": 1000,
        "output_tokens": 50,
        "cached_tokens": 900,
        "cache_write_tokens": 0,
        "reasoning_tokens": 30,
    }


def test_extract_usage_chat_completions_cached_field_fallback() -> None:
    from labrat.agent.providers.codex_subscription import _extract_usage

    # Some endpoints report the Chat-Completions shape (prompt_tokens_details).
    ev = {
        "type": "response.completed",
        "response": {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 800},
            }
        },
    }
    usage = _extract_usage(ev)
    assert usage is not None
    assert usage["cached_tokens"] == 800


def test_extract_usage_ignores_non_completed_and_missing() -> None:
    from labrat.agent.providers.codex_subscription import _extract_usage

    assert _extract_usage({"type": "response.output_text.delta", "delta": "x"}) is None
    assert _extract_usage({"type": "response.completed", "response": {}}) is None


def test_extract_usage_keeps_billed_tokens_from_incomplete_terminal() -> None:
    from labrat.agent.providers.codex_subscription import _extract_usage

    event = {
        "type": "response.incomplete",
        "response": {"usage": {"input_tokens": 700, "output_tokens": 12}},
    }
    usage = _extract_usage(event)
    assert usage is not None
    assert usage["input_tokens"] == 700
    assert usage["output_tokens"] == 12


async def test_consume_accumulates_usage_and_yields_blocks() -> None:
    provider = CodexSubscriptionProvider(model="gpt-5.5")
    assert provider.usage["input_tokens"] == 0  # starts at zero
    stream = (
        'data: {"type": "response.output_text.delta", "delta": "Hi"}\n'
        "\n"
        'data: {"type": "response.completed", "response": {"usage": {'
        '"input_tokens": 1200, "output_tokens": 40, '
        '"input_tokens_details": {"cached_tokens": 1000}}}}\n'
        "\n"
    )
    blocks = [b async for b in provider._consume(_alines(stream))]
    assert any(isinstance(b, TextBlock) and b.text == "Hi" for b in blocks)
    assert provider.usage["input_tokens"] == 1200
    assert provider.usage["output_tokens"] == 40
    assert provider.usage["cached_tokens"] == 1000
    assert provider.usage["requests"] == 1
    assert provider.request_usage[0]["cache_write_tokens"] == 0
    assert provider.request_usage[0]["cache_write_tokens_reported"] is False


async def test_gpt56_cache_key_pacing_is_shared_process_wide(monkeypatch: Any) -> None:
    import asyncio

    from labrat.agent.providers import codex_subscription as module

    clock = [100.0]
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await real_sleep(0)
        clock[0] += seconds

    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
    module._CACHE_KEY_LAST_START.clear()
    module._CACHE_KEY_LOCKS.clear()

    assert await module._pace_cache_key("gpt-5.6-luna", "task") == 0.0
    clock[0] += 1.0
    waits = await asyncio.gather(
        module._pace_cache_key("gpt-5.6-luna", "task"),
        module._pace_cache_key("gpt-5.6-luna", "task"),
    )
    assert waits == [3.0, 7.0]
    assert sleeps == [3.0, 4.0]
    # Routes are independent across keys and models.
    assert await module._pace_cache_key("gpt-5.6-luna", "other-task") == 0.0
    assert await module._pace_cache_key("gpt-5.6-terra", "task") == 0.0
    # Older models keep their pre-5.6 caching behavior and are not rate-gated.
    assert await module._pace_cache_key("gpt-5.5", "task") == 0.0


# ---- 7. prompt caching parameters -------------------------------------------


def test_request_includes_cache_key_but_not_unsupported_retention() -> None:
    provider = CodexSubscriptionProvider(model="gpt-5.5")
    b1 = provider._to_responses_request([{"role": "user", "content": "hi"}], [], "sys")
    # prompt_cache_key is a routing hint that improves hit rate (accepted by codex).
    assert isinstance(b1["prompt_cache_key"], str) and b1["prompt_cache_key"]
    # prompt_cache_retention is rejected by the codex endpoint (HTTP 400), so it
    # must NOT be sent — GPT-5.5 defaults to 24h retention anyway.
    assert "prompt_cache_retention" not in b1
    # Key must be STABLE across turns of the same trial (same provider instance) so
    # all turns route to the same cache.
    b2 = provider._to_responses_request([{"role": "user", "content": "again"}], [], "sys")
    assert b2["prompt_cache_key"] == b1["prompt_cache_key"]
    # Different provider instances (different trials) get different keys.
    other = CodexSubscriptionProvider(model="gpt-5.5")
    assert other._to_responses_request([], [], "")["prompt_cache_key"] != b1["prompt_cache_key"]


# ---- 8. reasoning-item passback (the top cause of cache misses for reasoning models) ----


async def test_consume_captures_reasoning_and_maps_to_call_id() -> None:
    provider = CodexSubscriptionProvider(model="gpt-5.5")
    stream = (
        'data: {"type": "response.output_item.done", "item": {"type": "reasoning", '
        '"id": "rs_1", "encrypted_content": "ENC", "summary": []}}\n'
        "\n"
        'data: {"type": "response.output_item.done", "item": {"type": "function_call", '
        '"call_id": "call_1", "name": "run_sql", "arguments": "{}"}}\n'
        "\n"
        'data: {"type": "response.completed", "response": {"usage": '
        '{"input_tokens": 10, "output_tokens": 1}}}\n'
        "\n"
    )
    blocks = [b async for b in provider._consume(_alines(stream))]
    assert any(isinstance(b, ToolUseBlock) and b.id == "call_1" for b in blocks)
    r = provider._reasoning_by_call_id["call_1"]
    assert r["type"] == "reasoning"
    assert r["encrypted_content"] == "ENC"
    assert r["id"] == "rs_1"


def test_to_responses_request_passes_reasoning_before_function_call() -> None:
    provider = CodexSubscriptionProvider(model="gpt-5.5")
    provider._reasoning_by_call_id = {
        "call_1": {"type": "reasoning", "id": "rs_1", "encrypted_content": "ENC", "summary": []}
    }
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": "run_sql", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}],
        },
    ]
    items = provider._to_responses_request(messages, [], "sys")["input"]
    fc_idx = next(
        i
        for i, it in enumerate(items)
        if it.get("type") == "function_call" and it["call_id"] == "call_1"
    )
    # The reasoning item must sit immediately before the function_call it preceded.
    assert items[fc_idx - 1]["type"] == "reasoning"
    assert items[fc_idx - 1]["encrypted_content"] == "ENC"
    assert "id" not in items[fc_idx - 1]


def test_reasoning_item_emitted_once_for_parallel_calls() -> None:
    provider = CodexSubscriptionProvider(model="gpt-5.5")
    shared = {"type": "reasoning", "id": "rs_1", "encrypted_content": "ENC", "summary": []}
    provider._reasoning_by_call_id = {"call_1": shared, "call_2": shared}
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call_1", "name": "a", "input": {}},
                {"type": "tool_use", "id": "call_2", "name": "b", "input": {}},
            ],
        }
    ]
    items = provider._to_responses_request(messages, [], "")["input"]
    reasoning = [it for it in items if it.get("type") == "reasoning"]
    assert len(reasoning) == 1  # one reasoning item, not duplicated across parallel calls
    assert items[0]["type"] == "reasoning"  # and it leads the group


def test_to_responses_request_unchanged_without_reasoning() -> None:
    # Back-compat: no captured reasoning → no reasoning items injected.
    provider = CodexSubscriptionProvider(model="gpt-5.5")
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": "a", "input": {}}],
        }
    ]
    items = provider._to_responses_request(messages, [], "")["input"]
    assert not any(it.get("type") == "reasoning" for it in items)


def test_reasoning_passback_can_be_disabled_for_safety_fallback() -> None:
    # If the codex endpoint rejects reasoning items (HTTP 400), stream() flips this
    # flag and retries without them; _to_responses_request must then omit reasoning.
    provider = CodexSubscriptionProvider(model="gpt-5.5")
    provider._reasoning_by_call_id = {
        "call_1": {"type": "reasoning", "id": "rs_1", "encrypted_content": "E", "summary": []}
    }
    messages: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": "a", "input": {}}],
        }
    ]
    # enabled by default → reasoning emitted
    assert any(
        it.get("type") == "reasoning"
        for it in provider._to_responses_request(messages, [], "")["input"]
    )
    # disabled → omitted
    provider._reasoning_passback_disabled = True
    assert not any(
        it.get("type") == "reasoning"
        for it in provider._to_responses_request(messages, [], "")["input"]
    )


# ---- prompt_cache_key routing (per-task, FEATURE: cache reuse across trials) --


def test_cache_key_defaults_to_unique_value() -> None:
    a = CodexSubscriptionProvider()
    b = CodexSubscriptionProvider()
    assert a._cache_key and b._cache_key
    assert a._cache_key != b._cache_key  # fresh per-instance default


def test_cache_key_can_be_set_explicitly() -> None:
    p = CodexSubscriptionProvider(cache_key="stockindex:1")
    assert p._cache_key == "stockindex:1"


def test_build_provider_threads_cache_key() -> None:
    p = build_provider("codex", "gpt-5.5", cache_key="stockindex:1")
    assert isinstance(p, CodexSubscriptionProvider)
    assert p._cache_key == "stockindex:1"


def test_request_body_uses_cache_key() -> None:
    p = CodexSubscriptionProvider(cache_key="stockindex:1")
    body = p._to_responses_request([], [], "")
    assert body["prompt_cache_key"] == "stockindex:1"


# ---- GPT-5.6 config + exact replay ------------------------------------------


@pytest.mark.parametrize(
    ("model", "effort"),
    [
        ("gpt-5.5", "minimal"),
        ("gpt-5.5", "xhigh"),
        ("gpt-5.6-sol", "ultra"),
        ("gpt-5.6-terra", "ultra"),
        ("gpt-5.6-luna", "low"),
    ],
)
def test_supported_codex_model_effort_pairs(model: str, effort: str) -> None:
    provider = CodexSubscriptionProvider(model=model, reasoning_effort=effort)
    assert provider._model == model
    assert provider._reasoning_effort == effort


def test_invalid_codex_model_and_effort_fail_before_network() -> None:
    with pytest.raises(ValueError, match="Valid models"):
        CodexSubscriptionProvider(model="gpt-5.6-moon")
    with pytest.raises(ValueError, match="Valid efforts"):
        CodexSubscriptionProvider(model="gpt-5.5", reasoning_effort="max")
    with pytest.raises(ValueError, match="Valid efforts"):
        CodexSubscriptionProvider(model="gpt-5.6-luna", reasoning_effort="ultra")
    with pytest.raises(ValueError, match="Valid efforts"):
        CodexSubscriptionProvider(model="gpt-5.6-sol", reasoning_effort="none")


def test_sol_ultra_maps_to_max_wire_reasoning() -> None:
    provider = CodexSubscriptionProvider(model="gpt-5.6-sol", reasoning_effort="ultra")
    body = provider._to_responses_request([{"role": "user", "content": "q"}], [], "sys")

    assert provider._reasoning_effort == "ultra"
    assert provider._wire_reasoning_effort == "max"
    assert body["reasoning"]["effort"] == "max"


def test_gpt56_headers_enable_responses_lite_only_for_new_family() -> None:
    auth = {"tokens": {"access_token": "token"}}
    gpt56 = CodexSubscriptionProvider(model="gpt-5.6-luna", reasoning_effort="low")
    legacy = CodexSubscriptionProvider(model="gpt-5.5", reasoning_effort="medium")

    assert gpt56._headers(auth)["x-openai-internal-codex-responses-lite"] == "true"
    assert "OpenAI-Beta" not in gpt56._headers(auth)
    assert "x-openai-internal-codex-responses-lite" not in legacy._headers(auth)
    assert legacy._headers(auth)["OpenAI-Beta"] == "responses=experimental"


def test_gpt56_request_marks_initial_prefix_but_remains_stateless_http() -> None:
    provider = CodexSubscriptionProvider(
        model="gpt-5.6-luna", reasoning_effort="low", cache_key="stockindex:1"
    )
    body = provider._to_responses_request([{"role": "user", "content": "question"}], [], "sys")

    assert [item["type"] for item in body["input"]] == [
        "additional_tools",
        "message",
        "message",
    ]
    block = body["input"][1]["content"][0]
    assert block["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert body["store"] is False
    assert "previous_response_id" not in body
    assert "instructions" not in body
    assert "tools" not in body
    assert body["parallel_tool_calls"] is False
    assert body["reasoning"] == {"effort": "low", "context": "all_turns"}

    legacy = CodexSubscriptionProvider(model="gpt-5.5")
    legacy_body = legacy._to_responses_request([{"role": "user", "content": "question"}], [], "sys")
    assert "prompt_cache_breakpoint" not in legacy_body["input"][0]["content"][0]


def test_exact_replay_preserves_raw_order_phase_and_reasoning() -> None:
    provider = CodexSubscriptionProvider(model="gpt-5.6-luna", reasoning_effort="low")
    messages: list[dict[str, Any]] = [{"role": "user", "content": "question"}]
    first = provider._to_responses_request(messages, [], "sys")
    raw_output = [
        {
            "type": "reasoning",
            "id": "rs_1",
            "content": [{"type": "reasoning_text", "text": "summary"}],
            "encrypted_content": "ENC",
            "phase": "commentary",
        },
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "Checking."}],
        },
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "run_sql",
            "arguments": '{"sql":"SELECT 1"}',
            "phase": "commentary",
        },
    ]
    provider._replay_items = [*first["input"], *raw_output]
    messages.extend(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Checking."},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "run_sql",
                        "input": {"sql": "SELECT 1"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "1"}],
            },
        ]
    )

    second = provider._to_responses_request(messages, [], "sys")

    assert second["input"][: len(first["input"])] == first["input"]
    replayed_output = second["input"][len(first["input"]) : -1]
    assert replayed_output == [
        {key: value for key, value in item.items() if key != "id"} for item in raw_output
    ]
    assert replayed_output[0]["phase"] == "commentary"
    assert replayed_output[0]["encrypted_content"] == "ENC"
    assert second["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "1",
    }


def test_replay_cursor_mismatch_reconstructs_fresh_lite_history() -> None:
    provider = CodexSubscriptionProvider(model="gpt-5.6-luna", reasoning_effort="low")
    first = provider._to_responses_request([{"role": "user", "content": "original"}], [], "sys")
    provider._replay_items = [
        *first["input"],
        {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "old answer"}],
        },
    ]
    provider._expected_assistant_cursor = ("not-the-current-digest", ())

    body = provider._to_responses_request(
        [
            {"role": "user", "content": "different"},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "different answer"}],
            },
            {"role": "user", "content": "continue"},
        ],
        [],
        "sys",
    )

    assert provider._last_request_mode == "reconstructed_full"
    assert body["input"][0]["type"] == "additional_tools"
    assert sum(item.get("type") == "additional_tools" for item in body["input"]) == 1
    assert "old answer" not in json.dumps(body["input"])


def test_bound_conversations_isolate_replay_but_share_usage() -> None:
    root = CodexSubscriptionProvider(model="gpt-5.6-luna", reasoning_effort="low")
    first = root.bind_conversation()
    second = root.bind_conversation()
    assert isinstance(first, CodexSubscriptionProvider)
    assert isinstance(second, CodexSubscriptionProvider)

    first._replay_items.append({"type": "message", "role": "user", "content": []})
    first._add_usage(
        {
            "input_tokens": 100,
            "output_tokens": 5,
            "cached_tokens": 80,
            "cache_write_tokens": 0,
            "reasoning_tokens": 2,
        },
        response_id="resp_1",
        request_mode="exact_replay",
    )

    assert second._replay_items == []
    assert root.usage["input_tokens"] == 100
    assert root.request_usage[0]["response_id"] == "resp_1"
    assert first.request_usage is root.request_usage
    assert second.request_usage is root.request_usage


async def test_consume_collects_raw_items_and_per_request_cache_usage() -> None:
    provider = CodexSubscriptionProvider(model="gpt-5.6-luna", reasoning_effort="low")
    stream = (
        'data: {"type":"response.output_item.done","item":{"type":"reasoning",'
        '"id":"rs_1","encrypted_content":"ENC","phase":"commentary"}}\n\n'
        'data: {"type":"response.completed","response":{"id":"resp_1","usage":{'
        '"input_tokens":2000,"output_tokens":50,"input_tokens_details":{'
        '"cached_tokens":1500,"cache_write_tokens":256},"output_tokens_details":{'
        '"reasoning_tokens":20}}}}\n\n'
    )
    raw_items: list[dict[str, Any]] = []
    completion_state = {"completed": False}
    blocks = [
        block
        async for block in provider._consume(
            _alines(stream),
            output_items=raw_items,
            completion_state=completion_state,
            request_mode="exact_replay",
            cache_breakpoint=True,
        )
    ]

    assert blocks == []
    assert completion_state["completed"] is True
    assert raw_items[0]["phase"] == "commentary"
    assert provider.usage["cache_write_tokens"] == 256
    assert len(provider.request_usage) == 1
    request_usage = dict(provider.request_usage[0])
    assert isinstance(request_usage.pop("conversation_id"), str)
    assert request_usage == {
        "request": 1,
        "model": "gpt-5.6-luna",
        "reasoning_effort": "low",
        "wire_reasoning_effort": "low",
        "response_id": "resp_1",
        "terminal_status": "response.completed",
        "request_mode": "exact_replay",
        "cache_breakpoint": True,
        "cache_pacing_wait_seconds": 0.0,
        "input_tokens": 2000,
        "output_tokens": 50,
        "cached_tokens": 1500,
        "cache_write_tokens": 256,
        "cache_write_tokens_reported": True,
        "reasoning_tokens": 20,
        "cache_hit_ratio": 0.75,
        "reasoning_passback_disabled": False,
        "cache_breakpoint_fallbacks": 0,
        "reasoning_passback_fallbacks": 0,
    }


async def test_stream_commits_exact_replay_only_after_completed(
    monkeypatch: Any,
) -> None:
    import httpx

    provider = CodexSubscriptionProvider(model="gpt-5.6-luna", reasoning_effort="low")
    _stub_provider_auth(provider, monkeypatch)
    client = _FakeAsyncClient([_FakeStreamResponse(200, sse=_successful_sse())])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)

    stream = await provider.stream([{"role": "user", "content": "question"}], [], "sys")
    blocks = [block async for block in stream]

    assert [block.text for block in blocks if isinstance(block, TextBlock)] == ["done"]
    assert [item["type"] for item in provider._replay_items] == [
        "additional_tools",
        "message",
        "message",
        "message",
    ]
    assert provider._replay_items[-1]["phase"] == "final_answer"
    assert provider.request_usage[0]["request_mode"] == "initial_full"


async def test_incomplete_stream_never_commits_partial_replay(monkeypatch: Any) -> None:
    import httpx

    provider = CodexSubscriptionProvider(model="gpt-5.6-luna", reasoning_effort="low")
    _stub_provider_auth(provider, monkeypatch)
    incomplete = (
        'data: {"type":"response.output_item.done","item":{"type":"message",'
        '"id":"msg_1","role":"assistant","content":[]}}\n\n'
    )
    client = _FakeAsyncClient([_FakeStreamResponse(200, sse=incomplete)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)

    stream = await provider.stream([{"role": "user", "content": "question"}], [], "sys")
    with pytest.raises(RuntimeError, match=r"without response\.completed"):
        _ = [block async for block in stream]
    assert provider._replay_items == []


async def test_unrelated_400_does_not_disable_reasoning_passback(monkeypatch: Any) -> None:
    import httpx

    provider = CodexSubscriptionProvider(model="gpt-5.5", reasoning_effort="medium")
    provider._reasoning_by_call_id = {
        "call_1": {"type": "reasoning", "id": "rs_1", "encrypted_content": "ENC"}
    }
    _stub_provider_auth(provider, monkeypatch)
    client = _FakeAsyncClient(
        [_FakeStreamResponse(400, error="Unsupported parameter: temperature")]
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": "run_sql"}],
        }
    ]

    stream = await provider.stream(messages, [], "sys")
    with pytest.raises(httpx.HTTPStatusError):
        _ = [block async for block in stream]
    assert provider._reasoning_passback_disabled is False
    assert len(client.bodies) == 1


async def test_cache_breakpoint_rejection_is_retried_once_and_remembered(
    monkeypatch: Any,
) -> None:
    import httpx

    from labrat.agent.providers import codex_subscription as codex_module

    monkeypatch.setitem(codex_module._CACHE_BREAKPOINT_SUPPORT, "gpt-5.6-luna", True)
    provider = CodexSubscriptionProvider(model="gpt-5.6-luna", reasoning_effort="low")
    _stub_provider_auth(provider, monkeypatch)
    client = _FakeAsyncClient(
        [
            _FakeStreamResponse(400, error="Unsupported parameter: prompt_cache_breakpoint"),
            _FakeStreamResponse(200, sse=_successful_sse()),
        ]
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)

    stream = await provider.stream([{"role": "user", "content": "question"}], [], "sys")
    _ = [block async for block in stream]

    first_blocks = [
        block
        for item in client.bodies[0]["input"]
        for block in item.get("content", [])
        if isinstance(block, dict)
    ]
    second_blocks = [
        block
        for item in client.bodies[1]["input"]
        for block in item.get("content", [])
        if isinstance(block, dict)
    ]
    assert any("prompt_cache_breakpoint" in block for block in first_blocks)
    assert not any("prompt_cache_breakpoint" in block for block in second_blocks)
    assert codex_module._CACHE_BREAKPOINT_SUPPORT["gpt-5.6-luna"] is False
    assert provider.usage["http_attempts"] == 2
    assert provider.usage["cache_breakpoint_fallbacks"] == 1
    assert provider.usage["reasoning_passback_fallbacks"] == 0
    assert provider.request_usage[0]["cache_breakpoint_fallbacks"] == 1
    assert provider.request_usage[0]["reasoning_passback_fallbacks"] == 0


async def test_cache_then_reasoning_rejections_use_separate_fallbacks(
    monkeypatch: Any,
) -> None:
    import httpx

    from labrat.agent.providers import codex_subscription as codex_module

    monkeypatch.setitem(codex_module._CACHE_BREAKPOINT_SUPPORT, "gpt-5.6-luna", True)
    provider = CodexSubscriptionProvider(model="gpt-5.6-luna", reasoning_effort="low")
    provider._reasoning_by_call_id = {
        "call_1": {"type": "reasoning", "id": "rs_1", "encrypted_content": "ENC"}
    }
    _stub_provider_auth(provider, monkeypatch)
    client = _FakeAsyncClient(
        [
            _FakeStreamResponse(400, error="Unsupported parameter: prompt_cache_breakpoint"),
            _FakeStreamResponse(400, error="Unsupported item type 'reasoning'"),
            _FakeStreamResponse(200, sse=_successful_sse()),
        ]
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: client)
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call_1", "name": "run_sql"}],
        }
    ]

    stream = await provider.stream(messages, [], "sys")
    _ = [block async for block in stream]

    assert len(client.bodies) == 3
    assert any(item.get("type") == "reasoning" for item in client.bodies[0]["input"])
    assert any(item.get("type") == "reasoning" for item in client.bodies[1]["input"])
    assert not any(item.get("type") == "reasoning" for item in client.bodies[2]["input"])
    assert provider._reasoning_passback_disabled is True
    assert provider._replay_items
    assert provider.usage["http_attempts"] == 3
    assert provider.usage["cache_breakpoint_fallbacks"] == 1
    assert provider.usage["reasoning_passback_fallbacks"] == 1
    assert provider.request_usage[0]["cache_breakpoint_fallbacks"] == 1
    assert provider.request_usage[0]["reasoning_passback_fallbacks"] == 1
    assert provider.request_usage[0]["reasoning_passback_disabled"] is True
