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
    assert _extract_usage(ev)["cached_tokens"] == 800


def test_extract_usage_ignores_non_completed_and_missing() -> None:
    from labrat.agent.providers.codex_subscription import _extract_usage

    assert _extract_usage({"type": "response.output_text.delta", "delta": "x"}) is None
    assert _extract_usage({"type": "response.completed", "response": {}}) is None


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
