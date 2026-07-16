"""GPT-5.5/5.6 via the user's ChatGPT subscription (Codex Responses API).

This personal/dev provider speaks the same Responses protocol as Codex at
``chatgpt.com/backend-api/codex/responses``, authenticated with the Codex CLI's
``~/.codex/auth.json`` tokens.

This provider is a native ``ModelProvider`` (not a proxy) so the Rat Core stays
embeddable. It translates LabRat's Anthropic-format history + tool schemas into
Responses ``input``/``tools`` items, streams the SSE response, and reduces it
back into ``TextBlock`` / ``ToolUseBlock``.

Reverse-engineered & unversioned: the path/headers/beta value can change without
notice. This is the personal/dev/benchmark path; the metered ``openai`` provider
is the distributable one. See
``docs/superpowers/specs/2026-06-01-codex-subscription-provider-design.md``.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import re
import time
import uuid
import weakref
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider, RateLimitError

_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
_TOKEN_URL = "https://auth.openai.com/oauth/token"
_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_DEFAULT_MODEL = "gpt-5.5"
_DEFAULT_AUTH_PATH = Path.home() / ".codex" / "auth.json"
# Refresh this many seconds before the JWT `exp` rather than waiting for a 401.
_REFRESH_SKEW_SECONDS = 300
_HTTP_TIMEOUT_SECONDS = 600
_GPT56_CACHE_KEY_MIN_INTERVAL_SECONDS = 4.0

# Exact model identifiers exposed by the current Codex subscription metadata.
CODEX_MODEL_IDS: tuple[str, ...] = (
    "gpt-5.5",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
)
GPT56_MODEL_IDS: tuple[str, ...] = CODEX_MODEL_IDS[1:]
GPT55_REASONING_EFFORTS: tuple[str, ...] = (
    "none",
    "minimal",  # retained for the existing subscription-provider CLI contract
    "low",
    "medium",
    "high",
    "xhigh",
)
GPT56_REASONING_EFFORTS: tuple[str, ...] = (
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)
CODEX_REASONING_EFFORTS: tuple[str, ...] = tuple(
    dict.fromkeys((*GPT55_REASONING_EFFORTS, *GPT56_REASONING_EFFORTS))
)
# Process-local capability memory prevents every trial from repeating a failed
# private-endpoint feature probe.
_CACHE_BREAKPOINT_SUPPORT: dict[str, bool] = {}
# GPT-5.6's documented reliable-cache path is approximately 15 RPM per
# prompt_cache_key. DAB runs trials sequentially, so a process-local monotonic
# gate is sufficient to share pacing across per-trial provider instances.
_CACHE_KEY_LAST_START: dict[tuple[str, str], float] = {}
_CACHE_KEY_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[str, str], asyncio.Lock]
] = weakref.WeakKeyDictionary()


def resolve_codex_model_config(model: str, reasoning_effort: str | None) -> tuple[str, str]:
    """Validate and return a concrete Codex model/effort pair."""
    if model not in CODEX_MODEL_IDS:
        valid = ", ".join(CODEX_MODEL_IDS)
        raise ValueError(f"Unsupported Codex model {model!r}. Valid models: {valid}.")
    effort = reasoning_effort or "medium"
    if model in {"gpt-5.6-sol", "gpt-5.6-terra"}:
        valid_efforts = GPT56_REASONING_EFFORTS
    elif model == "gpt-5.6-luna":
        valid_efforts = tuple(e for e in GPT56_REASONING_EFFORTS if e != "ultra")
    else:
        valid_efforts = GPT55_REASONING_EFFORTS
    if effort not in valid_efforts:
        valid = ", ".join(valid_efforts)
        raise ValueError(
            f"Unsupported reasoning effort {effort!r} for {model!r}. Valid efforts: {valid}."
        )
    return model, effort


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url JWT segment, restoring stripped ``=`` padding."""
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _account_id_from_jwt(id_token: str) -> str | None:
    """Pull the ChatGPT account id from the id_token's auth claim.

    The claim lives at ``["https://api.openai.com/auth"].chatgpt_account_id``.
    Returns None if the token can't be decoded or the claim is absent.
    """
    try:
        _header, payload, _sig = id_token.split(".")
        claims: dict[str, Any] = json.loads(_b64url_decode(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    auth = claims.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        account_id = cast(dict[str, Any], auth).get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    return None


def _jwt_exp(token: str) -> int | None:
    """Return the ``exp`` (unix seconds) claim of a JWT, or None if unreadable."""
    try:
        _header, payload, _sig = token.split(".")
        claims: dict[str, Any] = json.loads(_b64url_decode(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = claims.get("exp")
    return exp if isinstance(exp, int) else None


class CodexSubscriptionProvider(ModelProvider):
    """Run LabRat's loop on GPT-5.5/5.6 via the ChatGPT subscription."""

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        reasoning_effort: str = "medium",
        auth_path: Path | None = None,
        cache_key: str | None = None,
        *,
        _shared_usage: dict[str, int] | None = None,
        _shared_request_usage: list[dict[str, Any]] | None = None,
        _shared_features: dict[str, bool] | None = None,
        _conversation_id: str | None = None,
    ) -> None:
        self._model, self._reasoning_effort = resolve_codex_model_config(model, reasoning_effort)
        # On the Codex subscription surface, Ultra is Max wire reasoning plus
        # proactive task delegation (implemented by LabRat's subagent policy).
        self._wire_reasoning_effort = (
            "max" if self._reasoning_effort == "ultra" else self._reasoning_effort
        )
        self._auth_path = auth_path or _DEFAULT_AUTH_PATH
        # prompt-cache routing key. The caller should pass a key that is STABLE
        # across requests sharing a prompt prefix — e.g. per-task in a benchmark, so
        # the repeated trials of one task (identical prefix) route to the same warm
        # cache and reuse it. Defaults to a fresh per-instance uuid when unset.
        # A routing hint only — caching itself is automatic on the Responses API.
        self._cache_key = cache_key or uuid.uuid4().hex
        # Encrypted reasoning items captured per function_call (keyed by call_id).
        # GPT-5.5 is a reasoning model; passing these back on the next turn is the
        # #1 fix for cache misses — omitting them makes the prefix diverge and the
        # model restart its reasoning. See _consume (capture) + _to_responses_request
        # (re-emit before the matching function_call).
        self._reasoning_by_call_id: dict[str, dict[str, Any]] = {}
        # Safety net: if the codex endpoint ever rejects reasoning items in the input
        # (HTTP 400, as it does for some params), stream() flips this and retries
        # without them, degrading to the pre-passback behavior instead of stalling.
        self._reasoning_passback_disabled = False
        # Exact stateless transcript replay, scoped to one bound AgentLoop. Codex's
        # ChatGPT HTTP transport uses store=false and replays ordered model-visible
        # fields, with Responses Lite transport IDs normalized away.
        self._replay_items: list[dict[str, Any]] = []
        self._expected_assistant_cursor: str | None = None
        self._last_request_mode = "initial_full"
        self._last_cache_pacing_wait_seconds = 0.0
        self._conversation_id = _conversation_id or uuid.uuid4().hex
        self._shared_features = (
            _shared_features
            if _shared_features is not None
            else {"explicit_cache_breakpoints": _CACHE_BREAKPOINT_SUPPORT.get(self._model, True)}
        )
        # Token usage accumulated across every stream() call on this instance
        # (≈ per-trial totals). Populated from each response's `usage` block.
        self.usage = (
            _shared_usage
            if _shared_usage is not None
            else {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 0,
                "requests": 0,
                "http_attempts": 0,
                "cache_breakpoint_fallbacks": 0,
                "reasoning_passback_fallbacks": 0,
                "cache_pacing_wait_ms": 0,
            }
        )
        # Safe-to-persist per-request telemetry. Never contains auth headers,
        # prompts, tool outputs, or encrypted reasoning payloads.
        self.request_usage = _shared_request_usage if _shared_request_usage is not None else []

    def bind_conversation(self) -> ModelProvider:
        """Return isolated replay state while sharing aggregate usage/config."""
        return CodexSubscriptionProvider(
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            auth_path=self._auth_path,
            cache_key=self._cache_key,
            _shared_usage=self.usage,
            _shared_request_usage=self.request_usage,
            _shared_features=self._shared_features,
        )

    # ---- auth -----------------------------------------------------------------

    def _load_auth(self) -> dict[str, Any]:
        raw = json.loads(Path(self._auth_path).read_text())
        if not isinstance(raw, dict):
            raise ValueError(f"Malformed Codex auth file at {self._auth_path}")
        return cast(dict[str, Any], raw)

    def _maybe_refresh(self, auth: dict[str, Any]) -> dict[str, Any]:
        """Refresh the access token if it expires within the skew window.

        Returns the (possibly updated) auth dict. Re-persists atomically on a
        successful refresh. Fail-soft: if refresh fails but the current token is
        still valid we keep using it; the SSE call surfaces a real 401 otherwise.
        """
        tokens_obj = auth.get("tokens")
        if not isinstance(tokens_obj, dict):
            raise ValueError("Codex auth file has no `tokens` object")
        tokens = cast(dict[str, Any], tokens_obj)
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Codex auth file has no access_token")

        exp = _jwt_exp(access_token)
        if exp is not None and exp - time.time() > _REFRESH_SKEW_SECONDS:
            return auth  # still fresh

        refresh_token = tokens.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            return auth  # nothing to refresh with; let the call fail loudly if expired

        import httpx

        resp = httpx.post(
            _TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": _OAUTH_CLIENT_ID,
                "refresh_token": refresh_token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        new_access = data.get("access_token")
        if isinstance(new_access, str) and new_access:
            tokens["access_token"] = new_access
        new_id = data.get("id_token")
        if isinstance(new_id, str) and new_id:
            tokens["id_token"] = new_id
        new_refresh = data.get("refresh_token")
        if isinstance(new_refresh, str) and new_refresh:
            tokens["refresh_token"] = new_refresh
        self._persist_auth(auth)
        return auth

    def _persist_auth(self, auth: dict[str, Any]) -> None:
        path = Path(self._auth_path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(auth))
        tmp.chmod(0o600)
        tmp.replace(path)

    def _headers(self, auth: dict[str, Any]) -> dict[str, str]:
        tokens: dict[str, Any] = auth["tokens"]
        access_token: str = tokens["access_token"]
        id_token = tokens.get("id_token")
        account_id = None
        if isinstance(id_token, str):
            account_id = _account_id_from_jwt(id_token)
        if not account_id:
            account_id = tokens.get("account_id") or auth.get("account_id")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "originator": "codex_cli_rs",
            "User-Agent": "codex_cli_rs",
            "accept": "text/event-stream",
            "content-type": "application/json",
        }
        if isinstance(account_id, str) and account_id:
            headers["chatgpt-account-id"] = account_id
        if self._model in GPT56_MODEL_IDS:
            headers["x-openai-internal-codex-responses-lite"] = "true"
        else:
            headers["OpenAI-Beta"] = "responses=experimental"
        return headers

    # ---- translation ----------------------------------------------------------

    def _to_responses_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> dict[str, Any]:
        """Translate Anthropic-format history + tools into a Responses request body."""
        if self._replay_items:
            # The server already produced the latest assistant turn. Replay its exact
            # output items and translate only messages appended after that assistant
            # turn (normally function_call_output items or verifier feedback).
            continuation = _messages_after_last_assistant(messages)
            cursor_matches = (
                self._expected_assistant_cursor is None
                or _last_assistant_cursor(messages) == self._expected_assistant_cursor
            )
            if continuation is None or not cursor_matches:
                # A caller reused an unbound provider for an unrelated history.
                # Fail safe by starting a fresh stateless transcript.
                input_items = _messages_to_responses_items(
                    messages,
                    self._reasoning_by_call_id,
                    reasoning_passback_disabled=self._reasoning_passback_disabled,
                )
                self._last_request_mode = "reconstructed_full"
            else:
                input_items = copy.deepcopy(self._replay_items)
                input_items.extend(
                    _messages_to_responses_items(
                        continuation,
                        self._reasoning_by_call_id,
                        reasoning_passback_disabled=self._reasoning_passback_disabled,
                    )
                )
                self._last_request_mode = "exact_replay"
        else:
            input_items = _messages_to_responses_items(
                messages,
                self._reasoning_by_call_id,
                reasoning_passback_disabled=self._reasoning_passback_disabled,
            )
            self._last_request_mode = "initial_full"

        if self._reasoning_passback_disabled:
            _strip_reasoning_items(input_items)

        responses_tools = [
            {
                "type": "function",
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                "strict": False,
            }
            for t in tools
        ]

        use_responses_lite = self._model in GPT56_MODEL_IDS
        if use_responses_lite:
            if self._last_request_mode != "exact_replay":
                prefix: list[dict[str, Any]] = [
                    {
                        "type": "additional_tools",
                        "role": "developer",
                        "tools": responses_tools,
                    }
                ]
                if system:
                    prefix.append(_text_message_item("developer", system))
                input_items[0:0] = prefix
            if self._shared_features["explicit_cache_breakpoints"]:
                _mark_first_input_text_cache_breakpoint(input_items)
            else:
                _strip_cache_breakpoints(input_items)
        # ChatGPT uses store=false and item_ids is disabled on this transport.
        # Preserve function call_id while removing server-local top-level IDs.
        _strip_item_ids(input_items)

        reasoning: dict[str, Any] = {"effort": self._wire_reasoning_effort}
        if use_responses_lite:
            reasoning["context"] = "all_turns"
        else:
            reasoning["summary"] = "auto"

        body: dict[str, Any] = {
            "model": self._model,
            "input": input_items,
            "tool_choice": "auto",
            "parallel_tool_calls": not use_responses_lite,
            "reasoning": reasoning,
            "include": ["reasoning.encrypted_content"],
            "store": False,
            "stream": True,
            # Prompt caching is automatic for eligible prefixes. GPT-5.6 requires a
            # stable key for its more reliable matching; DAB supplies one per task.
            # We intentionally keep store=false: current Codex subscription HTTP
            # mirrors the official client and exact-replays response items. Stored
            # previous_response_id chaining is conversation state, not a billing fix.
            "prompt_cache_key": self._cache_key,
        }
        if not use_responses_lite:
            body["instructions"] = system or ""
            body["tools"] = responses_tools
        return body

    # ---- streaming ------------------------------------------------------------

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        import httpx

        auth = self._maybe_refresh(self._load_auth())
        headers = self._headers(auth)

        async def _emit() -> AsyncIterator[ContentBlock]:
            self._last_cache_pacing_wait_seconds = await _pace_cache_key(
                self._model, self._cache_key
            )
            self.usage["cache_pacing_wait_ms"] += round(self._last_cache_pacing_wait_seconds * 1000)
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                # One retry may disable a rejected GPT-5.6 cache breakpoint and a
                # second may disable explicitly rejected reasoning-item passback.
                for _attempt in range(3):
                    body = self._to_responses_request(messages, tools, system)
                    self.usage["http_attempts"] += 1
                    sent_reasoning = any(it.get("type") == "reasoning" for it in body["input"])
                    sent_cache_breakpoint = _has_cache_breakpoint(body["input"])
                    output_items: list[dict[str, Any]] = []
                    completion_state = {"completed": False}
                    request_mode = self._last_request_mode
                    try:
                        async with client.stream(
                            "POST", _RESPONSES_URL, headers=headers, json=body
                        ) as resp:
                            if resp.is_error:
                                # Read the error payload so fallback classification can
                                # name the rejected parameter instead of treating every
                                # HTTP 400 as a reasoning-item failure.
                                await resp.aread()
                            resp.raise_for_status()
                            async for block in self._consume(
                                resp.aiter_lines(),
                                output_items=output_items,
                                completion_state=completion_state,
                                request_mode=request_mode,
                                cache_breakpoint=sent_cache_breakpoint,
                            ):
                                yield block
                        if not completion_state["completed"]:
                            raise RuntimeError(
                                "Codex Responses stream ended without response.completed"
                            )
                        # Commit replay state only after a complete successful stream.
                        self._replay_items = copy.deepcopy(body["input"])
                        self._replay_items.extend(copy.deepcopy(output_items))
                        self._expected_assistant_cursor = _response_output_cursor(output_items)
                        if sent_cache_breakpoint:
                            _CACHE_BREAKPOINT_SUPPORT[self._model] = True
                        return
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 429:
                            raise RateLimitError(response=exc.response) from exc
                        if (
                            exc.response.status_code == 400
                            and sent_cache_breakpoint
                            and _http_error_mentions(exc, "prompt_cache")
                            and self._shared_features["explicit_cache_breakpoints"]
                        ):
                            self._shared_features["explicit_cache_breakpoints"] = False
                            _CACHE_BREAKPOINT_SUPPORT[self._model] = False
                            self.usage["cache_breakpoint_fallbacks"] += 1
                            continue
                        if (
                            exc.response.status_code == 400
                            and sent_reasoning
                            and _http_error_rejects_reasoning_item(exc)
                            and not self._reasoning_passback_disabled
                        ):
                            self._reasoning_passback_disabled = True
                            self.usage["reasoning_passback_fallbacks"] += 1
                            continue
                        raise

        return _emit()

    async def _consume(
        self,
        lines: AsyncIterator[str],
        *,
        output_items: list[dict[str, Any]] | None = None,
        completion_state: dict[str, bool] | None = None,
        request_mode: str = "direct",
        cache_breakpoint: bool = False,
    ) -> AsyncIterator[ContentBlock]:
        """Reduce an SSE line stream into content blocks while (a) accumulating
        per-call token usage onto ``self.usage`` and (b) capturing reasoning items
        and mapping each to the function_call it precedes, so we can pass them back
        next turn. Split out from ``stream`` so it's testable without real HTTP."""
        current_reasoning: dict[str, Any] | None = None
        async for event in _iter_sse_events(lines):
            if event.get("type") == "response.completed" and completion_state is not None:
                completion_state["completed"] = True
            usage = _extract_usage(event)
            if usage is not None:
                self._add_usage(
                    usage,
                    response_id=_response_id(event),
                    terminal_status=str(event.get("type", "response.completed")),
                    request_mode=request_mode,
                    cache_breakpoint=cache_breakpoint,
                    cache_write_tokens_reported=_input_usage_detail_reported(
                        event, "cache_write_tokens"
                    ),
                )
            item = _output_item_done(event)
            if item is not None:
                if output_items is not None:
                    output_items.append(copy.deepcopy(item))
                itype = item.get("type")
                if itype == "reasoning":
                    current_reasoning = _reasoning_input_item(item)
                elif itype == "function_call" and current_reasoning is not None:
                    call_id = str(item.get("call_id") or item.get("id") or "")
                    if call_id:
                        self._reasoning_by_call_id[call_id] = current_reasoning
            for block in _reduce_event(event):
                yield block

    def _add_usage(
        self,
        usage: dict[str, int],
        *,
        response_id: str | None = None,
        terminal_status: str = "response.completed",
        request_mode: str = "direct",
        cache_breakpoint: bool = False,
        cache_write_tokens_reported: bool = False,
    ) -> None:
        for key in (
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        ):
            self.usage[key] += usage.get(key, 0)
        self.usage["requests"] += 1
        input_tokens = usage.get("input_tokens", 0)
        self.request_usage.append(
            {
                "request": len(self.request_usage) + 1,
                "model": self._model,
                "reasoning_effort": self._reasoning_effort,
                "wire_reasoning_effort": self._wire_reasoning_effort,
                "conversation_id": self._conversation_id,
                "response_id": response_id,
                "terminal_status": terminal_status,
                "request_mode": request_mode,
                "cache_breakpoint": cache_breakpoint,
                "cache_pacing_wait_seconds": self._last_cache_pacing_wait_seconds,
                **usage,
                # The ChatGPT subscription endpoint may omit write-token telemetry.
                # Keep an explicit presence bit so an omitted field is not reported
                # as a measured zero even though aggregate arithmetic remains numeric.
                "cache_write_tokens_reported": cache_write_tokens_reported,
                "cache_hit_ratio": (
                    usage.get("cached_tokens", 0) / input_tokens if input_tokens else 0.0
                ),
                "reasoning_passback_disabled": self._reasoning_passback_disabled,
                "cache_breakpoint_fallbacks": self.usage["cache_breakpoint_fallbacks"],
                "reasoning_passback_fallbacks": self.usage["reasoning_passback_fallbacks"],
            }
        )


async def _pace_cache_key(model: str, cache_key: str) -> float:
    """Keep GPT-5.6 traffic at the documented ~15 RPM per cache-routing key."""
    if model not in GPT56_MODEL_IDS:
        return 0.0
    route = (model, cache_key)
    loop = asyncio.get_running_loop()
    locks = _CACHE_KEY_LOCKS.setdefault(loop, {})
    lock = locks.setdefault(route, asyncio.Lock())
    invoked_at = time.monotonic()
    async with lock:
        now = time.monotonic()
        last_start = _CACHE_KEY_LAST_START.get(route)
        wait_seconds = (
            max(0.0, last_start + _GPT56_CACHE_KEY_MIN_INTERVAL_SECONDS - now)
            if last_start is not None
            else 0.0
        )
        if wait_seconds:
            await asyncio.sleep(wait_seconds)
        started_at = time.monotonic()
        _CACHE_KEY_LAST_START[route] = started_at
    return max(0.0, started_at - invoked_at)


def _messages_to_responses_items(
    messages: list[dict[str, Any]],
    reasoning_by_call_id: dict[str, dict[str, Any]],
    *,
    reasoning_passback_disabled: bool,
) -> list[dict[str, Any]]:
    """Translate generic history when exact server output items are unavailable."""
    input_items: list[dict[str, Any]] = []
    emitted_reasoning: set[str] = set()
    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content", "")
        if isinstance(content, str):
            input_items.append(_text_message_item(role, content))
            continue
        if not isinstance(content, list):
            continue
        for raw_block in cast(list[Any], content):
            if not isinstance(raw_block, dict):
                continue
            block = cast(dict[str, Any], raw_block)
            btype = block.get("type")
            if btype == "text":
                input_items.append(_text_message_item(role, str(block.get("text", ""))))
            elif btype == "tool_use":
                call_id = str(block.get("id", ""))
                if not reasoning_passback_disabled:
                    reasoning = reasoning_by_call_id.get(call_id)
                    reasoning_id = str(reasoning.get("id", "")) if reasoning else ""
                    if reasoning is not None and reasoning_id not in emitted_reasoning:
                        input_items.append(copy.deepcopy(reasoning))
                        emitted_reasoning.add(reasoning_id)
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": str(block.get("name", "")),
                        "arguments": json.dumps(block.get("input", {})),
                    }
                )
            elif btype == "tool_result":
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(block.get("tool_use_id", "")),
                        "output": _tool_result_to_str(block.get("content", "")),
                    }
                )
    return input_items


def _messages_after_last_assistant(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "assistant":
            return messages[index + 1 :]
    return None


def _last_assistant_cursor(
    messages: list[dict[str, Any]],
) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        blocks: list[dict[str, Any]] = []
        content = message.get("content")
        if isinstance(content, list):
            for raw_block in cast(list[Any], content):
                if not isinstance(raw_block, dict):
                    continue
                block = cast(dict[str, Any], raw_block)
                if block.get("type") == "text":
                    blocks.append({"type": "text", "text": str(block.get("text", ""))})
                elif block.get("type") == "tool_use":
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(block.get("id", "")),
                            "name": str(block.get("name", "")),
                            "input": block.get("input", {}),
                        }
                    )
                else:
                    blocks.append({"type": str(block.get("type", "")), "block": block})
        return _assistant_content_digest(blocks)
    return None


def _response_output_cursor(
    output_items: list[dict[str, Any]],
) -> str:
    blocks: list[dict[str, Any]] = []
    for item in output_items:
        if item.get("type") == "message":
            content = item.get("content")
            if isinstance(content, list):
                for raw_block in cast(list[Any], content):
                    if not isinstance(raw_block, dict):
                        continue
                    block = cast(dict[str, Any], raw_block)
                    if block.get("type") == "output_text":
                        blocks.append({"type": "text", "text": str(block.get("text", ""))})
                    else:
                        blocks.append({"type": str(block.get("type", "")), "block": block})
        elif item.get("type") == "function_call":
            raw_arguments = item.get("arguments", "")
            try:
                arguments: Any = (
                    json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                )
            except json.JSONDecodeError:
                arguments = {"raw": raw_arguments}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(item.get("call_id") or item.get("id") or ""),
                    "name": str(item.get("name", "")),
                    "input": arguments,
                }
            )
    return _assistant_content_digest(blocks)


def _assistant_content_digest(blocks: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        blocks,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _mark_first_input_text_cache_breakpoint(input_items: list[dict[str, Any]]) -> None:
    """Mark the initial task prefix; implicit latest-message caching stays enabled."""
    for item in input_items:
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for raw_block in cast(list[Any], content):
            if not isinstance(raw_block, dict):
                continue
            block = cast(dict[str, Any], raw_block)
            if block.get("type") == "input_text":
                block.setdefault("prompt_cache_breakpoint", {"mode": "explicit"})
                return


def _strip_cache_breakpoints(input_items: list[dict[str, Any]]) -> None:
    for item in input_items:
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for raw_block in cast(list[Any], content):
            if isinstance(raw_block, dict):
                cast(dict[str, Any], raw_block).pop("prompt_cache_breakpoint", None)


def _has_cache_breakpoint(input_items: list[dict[str, Any]]) -> bool:
    for item in input_items:
        content = item.get("content")
        if isinstance(content, list) and any(
            isinstance(block, dict) and "prompt_cache_breakpoint" in block
            for block in cast(list[Any], content)
        ):
            return True
    return False


def _strip_reasoning_items(input_items: list[dict[str, Any]]) -> None:
    input_items[:] = [item for item in input_items if item.get("type") != "reasoning"]


def _strip_item_ids(input_items: list[dict[str, Any]]) -> None:
    for item in input_items:
        item.pop("id", None)


def _http_error_mentions(exc: Any, field: str) -> bool:
    try:
        text = str(exc.response.text)
    except Exception:
        return False
    return field.casefold() in text.casefold()


def _http_error_rejects_reasoning_item(exc: Any) -> bool:
    try:
        text = str(exc.response.text).casefold()
    except Exception:
        return False
    return any(
        marker in text
        for marker in (
            "encrypted_content",
            "reasoning input item",
            "item type 'reasoning'",
            'item type "reasoning"',
            "unsupported item: reasoning",
        )
    )


def _response_id(event: dict[str, Any]) -> str | None:
    response = event.get("response")
    if not isinstance(response, dict):
        return None
    response_id = cast(dict[str, Any], response).get("id")
    return response_id if isinstance(response_id, str) and response_id else None


def _text_message_item(role: str, text: str) -> dict[str, Any]:
    content_type = "output_text" if role == "assistant" else "input_text"
    return {
        "type": "message",
        "role": role,
        "content": [{"type": content_type, "text": text}],
    }


def _tool_result_to_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in cast(list[Any], content):
            if isinstance(block, dict):
                b = cast(dict[str, Any], block)
                if b.get("type") == "text":
                    parts.append(str(b.get("text", "")))
                else:
                    parts.append(str(b))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


async def _iter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Reduce a `text/event-stream` line iterator into parsed JSON event payloads.

    Events are blank-line delimited; we accumulate the ``data:`` payload of each.
    Tolerates ``[DONE]`` sentinels and malformed JSON lines (skipped).
    """
    data_buf: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r")
        if line == "":
            if data_buf:
                payload = "\n".join(data_buf)
                data_buf = []
                event = _parse_sse_data(payload)
                if event is not None:
                    yield event
            continue
        if line.startswith(":"):
            continue  # SSE comment / heartbeat
        if line.startswith("data:"):
            data_buf.append(line[len("data:") :].lstrip())
    if data_buf:
        event = _parse_sse_data("\n".join(data_buf))
        if event is not None:
            yield event


def _parse_sse_data(payload: str) -> dict[str, Any] | None:
    if payload == "[DONE]":
        return None
    try:
        parsed: Any = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return cast(dict[str, Any], parsed)
    return None


def _reduce_event(event: dict[str, Any]) -> list[ContentBlock]:
    """Map one Responses SSE event to zero or more LabRat content blocks."""
    etype = event.get("type")
    if etype == "response.output_text.delta":
        delta = event.get("delta")
        if isinstance(delta, str) and delta:
            return [TextBlock(text=delta)]
        return []
    if etype == "response.output_item.done":
        item = event.get("item")
        if isinstance(item, dict):
            item_d = cast(dict[str, Any], item)
            if item_d.get("type") == "function_call":
                return [_function_call_block(item_d)]
        return []
    if etype == "error":
        if _is_rate_limit_event(event):
            raise RateLimitError(rate_limit=_event_rate_limit_meta(event))
        raise RuntimeError("Codex Responses stream error")
    if etype in ("response.failed", "response.incomplete"):
        detail = _error_detail(event)
        if _is_rate_limit_event(event):
            raise RateLimitError(rate_limit=_event_rate_limit_meta(event))
        raise RuntimeError(f"Codex Responses stream {etype}: {detail}")
    return []


def _output_item_done(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return the ``item`` of a ``response.output_item.done`` event, else None."""
    if event.get("type") != "response.output_item.done":
        return None
    item = event.get("item")
    return cast(dict[str, Any], item) if isinstance(item, dict) else None


def _reasoning_input_item(item: dict[str, Any]) -> dict[str, Any]:
    """Preserve the complete server reasoning item for stateless replay."""
    return copy.deepcopy(item)


def _extract_usage(event: dict[str, Any]) -> dict[str, int] | None:
    """Pull normalized usage from a terminal Responses event when present.

    Handles completed, incomplete, and failed terminals plus both the Responses shape
    (``input_tokens_details.cached_tokens``) and the Chat-Completions shape
    (``prompt_tokens_details.cached_tokens``) since the Codex endpoint is
    reverse-engineered and may report either."""
    if event.get("type") not in {
        "response.completed",
        "response.incomplete",
        "response.failed",
    }:
        return None
    response = event.get("response")
    if not isinstance(response, dict):
        return None
    usage = cast(dict[str, Any], response).get("usage")
    if not isinstance(usage, dict):
        return None
    u = cast(dict[str, Any], usage)

    def _int(x: Any) -> int:
        return x if isinstance(x, int) else 0

    def _input_detail(field_name: str) -> int:
        for field in ("input_tokens_details", "prompt_tokens_details"):
            details = u.get(field)
            if isinstance(details, dict):
                value = cast(dict[str, Any], details).get(field_name)
                if isinstance(value, int):
                    return value
        return 0

    reasoning = 0
    for details_name in ("output_tokens_details", "completion_tokens_details"):
        out_details = u.get(details_name)
        if isinstance(out_details, dict):
            candidate = cast(dict[str, Any], out_details).get("reasoning_tokens")
            if isinstance(candidate, int):
                reasoning = candidate
                break

    return {
        "input_tokens": _int(u.get("input_tokens") or u.get("prompt_tokens")),
        "output_tokens": _int(u.get("output_tokens") or u.get("completion_tokens")),
        "cached_tokens": _input_detail("cached_tokens"),
        "cache_write_tokens": _input_detail("cache_write_tokens"),
        "reasoning_tokens": reasoning,
    }


def _input_usage_detail_reported(event: dict[str, Any], field_name: str) -> bool:
    """Whether a terminal usage payload explicitly reported an input detail field."""
    response = event.get("response")
    if not isinstance(response, dict):
        return False
    usage = cast(dict[str, Any], response).get("usage")
    if not isinstance(usage, dict):
        return False
    for details_name in ("input_tokens_details", "prompt_tokens_details"):
        details = cast(dict[str, Any], usage).get(details_name)
        if isinstance(details, dict) and isinstance(
            cast(dict[str, Any], details).get(field_name), int
        ):
            return True
    return False


def _function_call_block(item: dict[str, Any]) -> ToolUseBlock:
    raw_args = item.get("arguments", "")
    try:
        parsed_args: Any = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        parsed_args = {}
    args: dict[str, Any] = (
        cast(dict[str, Any], parsed_args) if isinstance(parsed_args, dict) else {}
    )
    return ToolUseBlock(
        id=str(item.get("call_id") or item.get("id") or ""),
        name=str(item.get("name", "")),
        input=args,
    )


def _error_detail(event: dict[str, Any]) -> str:
    response = event.get("response")
    if isinstance(response, dict):
        resp = cast(dict[str, Any], response)
        err = resp.get("error")
        if isinstance(err, dict):
            errd = cast(dict[str, Any], err)
            return str(errd.get("message") or errd)
        status = resp.get("status")
        if status:
            return str(status)
    return json.dumps(event)[:200]


def _is_rate_limit_event(event: dict[str, Any]) -> bool:
    sources = _structured_error_sources(event)
    rate_codes = {
        "rate_limit",
        "rate_limited",
        "rate_limit_error",
        "rate_limit_exceeded",
        "usage_limit",
        "usage_limit_reached",
        "quota_exceeded",
        "too_many_requests",
    }
    for source in sources:
        for field in ("status_code", "http_status", "status"):
            status = source.get(field)
            if status == 429 or str(status) == "429":
                return True
        for field in ("code", "type"):
            raw = source.get(field)
            if isinstance(raw, str):
                normalized = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
                if normalized in rate_codes:
                    return True

    message = _bounded_error_message(event)
    return bool(
        re.search(
            r"(?i)(?:\b(?:api error|http(?: status)?)\s*:?\s*429\b|"
            r"\btoo many requests\b|\brate[ _-]?limit(?:ed| exceeded| reached)?\b|"
            r"\busage[ _-]?limit\b|\bquota (?:exceeded|reached)\b)",
            message,
        )
    )


def _structured_error_sources(event: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [event]
    response = event.get("response")
    if isinstance(response, dict):
        response_obj = cast(dict[str, Any], response)
        sources.append(response_obj)
        error = response_obj.get("error")
        if isinstance(error, dict):
            sources.append(cast(dict[str, Any], error))
    top_error = event.get("error")
    if isinstance(top_error, dict):
        sources.append(cast(dict[str, Any], top_error))
    return sources


def _bounded_error_message(event: dict[str, Any]) -> str:
    candidates: list[Any] = []
    if event.get("type") == "error":
        candidates.append(event.get("message"))
    response = event.get("response")
    if isinstance(response, dict):
        error = cast(dict[str, Any], response).get("error")
        if isinstance(error, dict):
            candidates.append(cast(dict[str, Any], error).get("message"))
        elif isinstance(error, str):
            candidates.append(error)
    for candidate in candidates:
        if isinstance(candidate, str):
            return candidate[:500]
    return ""


def _event_rate_limit_meta(event: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for source in _structured_error_sources(event):
        for key in ("resets_at", "resets_in_seconds"):
            value = source.get(key)
            if key not in result and isinstance(value, int) and not isinstance(value, bool):
                result[key] = value
    return result
