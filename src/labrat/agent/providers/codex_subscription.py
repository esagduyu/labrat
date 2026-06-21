"""GPT-5.5 via the user's ChatGPT subscription (Codex Responses API).

GPT-5.5 is subscription-only today (no metered API), so the only way to run
LabRat's own ``AgentLoop`` on it is to speak the same protocol the Codex CLI
uses: the OpenAI **Responses API** at ``chatgpt.com/backend-api/codex/responses``,
authenticated with the Codex CLI's ``~/.codex/auth.json`` tokens.

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

import base64
import json
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider

_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
_TOKEN_URL = "https://auth.openai.com/oauth/token"
_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_DEFAULT_MODEL = "gpt-5.5"
_DEFAULT_AUTH_PATH = Path.home() / ".codex" / "auth.json"
# Refresh this many seconds before the JWT `exp` rather than waiting for a 401.
_REFRESH_SKEW_SECONDS = 300
_HTTP_TIMEOUT_SECONDS = 600


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
    """Run LabRat's loop on GPT-5.5 via the ChatGPT subscription (Codex API)."""

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        reasoning_effort: str = "medium",
        auth_path: Path | None = None,
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._auth_path = auth_path or _DEFAULT_AUTH_PATH
        # Stable per-instance (≈ per-trial) prompt-cache routing key: all turns of
        # one trial share the growing prefix, so routing them to the same cache
        # maximizes hit rate. A routing hint only — caching itself is automatic.
        self._cache_key = uuid.uuid4().hex
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
        # Token usage accumulated across every stream() call on this instance
        # (≈ per-trial totals). Populated from each response's `usage` block.
        self.usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "requests": 0,
        }

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
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            "User-Agent": "codex_cli_rs",
            "accept": "text/event-stream",
            "content-type": "application/json",
        }
        if isinstance(account_id, str) and account_id:
            headers["chatgpt-account-id"] = account_id
        return headers

    # ---- translation ----------------------------------------------------------

    def _to_responses_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> dict[str, Any]:
        """Translate Anthropic-format history + tools into a Responses request body."""
        input_items: list[dict[str, Any]] = []
        emitted_reasoning: set[str] = set()
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, str):
                input_items.append(_text_message_item(role, content))
                continue
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    input_items.append(_text_message_item(role, block.get("text", "")))
                elif btype == "tool_use":
                    # Re-emit the captured reasoning item immediately before its
                    # function_call (deduped — one reasoning item can precede several
                    # parallel calls). Keeps the prefix byte-identical to what the
                    # model produced, so the cache hits and reasoning continues.
                    if not self._reasoning_passback_disabled:
                        reasoning = self._reasoning_by_call_id.get(block["id"])
                        if reasoning is not None and reasoning["id"] not in emitted_reasoning:
                            input_items.append(reasoning)
                            emitted_reasoning.add(reasoning["id"])
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": block["id"],
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input", {})),
                        }
                    )
                elif btype == "tool_result":
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": block["tool_use_id"],
                            "output": _tool_result_to_str(block.get("content", "")),
                        }
                    )

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

        body: dict[str, Any] = {
            "model": self._model,
            "instructions": system or "",
            "input": input_items,
            "tools": responses_tools,
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "reasoning": {"effort": self._reasoning_effort, "summary": "auto"},
            "include": ["reasoning.encrypted_content"],
            "store": False,
            "stream": True,
            # Prompt caching is automatic on the Responses API (caches the longest
            # stable prefix >1024 tok). We optimize it with a per-trial routing key
            # that keeps all of a trial's turns on the same cache. NOTE: the
            # `prompt_cache_retention` param is a valid OpenAI-API param but the
            # codex/ChatGPT backend rejects it with HTTP 400 ("Unsupported
            # parameter"), so we omit it — GPT-5.5 already defaults to 24h retention.
            "prompt_cache_key": self._cache_key,
        }
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
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                for _attempt in range(2):
                    body = self._to_responses_request(messages, tools, system)
                    sent_reasoning = any(it.get("type") == "reasoning" for it in body["input"])
                    try:
                        async with client.stream(
                            "POST", _RESPONSES_URL, headers=headers, json=body
                        ) as resp:
                            resp.raise_for_status()
                            async for block in self._consume(resp.aiter_lines()):
                                yield block
                        return
                    except httpx.HTTPStatusError as exc:
                        # If reasoning items triggered a 400, disable passback and retry
                        # once without them (the prefix-cache loss is far better than a
                        # stalled run). Any other 4xx/5xx propagates to per-trial isolation.
                        if (
                            exc.response.status_code == 400
                            and sent_reasoning
                            and not self._reasoning_passback_disabled
                        ):
                            self._reasoning_passback_disabled = True
                            continue
                        raise

        return _emit()

    async def _consume(self, lines: AsyncIterator[str]) -> AsyncIterator[ContentBlock]:
        """Reduce an SSE line stream into content blocks while (a) accumulating
        per-call token usage onto ``self.usage`` and (b) capturing reasoning items
        and mapping each to the function_call it precedes, so we can pass them back
        next turn. Split out from ``stream`` so it's testable without real HTTP."""
        current_reasoning: dict[str, Any] | None = None
        async for event in _iter_sse_events(lines):
            usage = _extract_usage(event)
            if usage is not None:
                self._add_usage(usage)
            item = _output_item_done(event)
            if item is not None:
                itype = item.get("type")
                if itype == "reasoning":
                    current_reasoning = _reasoning_input_item(item)
                elif itype == "function_call" and current_reasoning is not None:
                    call_id = str(item.get("call_id") or item.get("id") or "")
                    if call_id:
                        self._reasoning_by_call_id[call_id] = current_reasoning
            for block in _reduce_event(event):
                yield block

    def _add_usage(self, usage: dict[str, int]) -> None:
        for key in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens"):
            self.usage[key] += usage.get(key, 0)
        self.usage["requests"] += 1


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
    if etype in ("response.failed", "response.incomplete"):
        detail = _error_detail(event)
        raise RuntimeError(f"Codex Responses stream {etype}: {detail}")
    return []


def _output_item_done(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return the ``item`` of a ``response.output_item.done`` event, else None."""
    if event.get("type") != "response.output_item.done":
        return None
    item = event.get("item")
    return cast(dict[str, Any], item) if isinstance(item, dict) else None


def _reasoning_input_item(item: dict[str, Any]) -> dict[str, Any]:
    """Build the input-format reasoning item to pass back next turn (carries the
    encrypted_content the model needs to resume its chain of thought)."""
    out: dict[str, Any] = {"type": "reasoning", "id": str(item.get("id", ""))}
    enc = item.get("encrypted_content")
    if enc is not None:
        out["encrypted_content"] = enc
    summary = item.get("summary")
    out["summary"] = summary if isinstance(summary, list) else []
    return out


def _extract_usage(event: dict[str, Any]) -> dict[str, int] | None:
    """Pull a normalized token-usage dict from a ``response.completed`` event,
    or None if the event carries no usage. Handles both the Responses shape
    (``input_tokens_details.cached_tokens``) and the Chat-Completions shape
    (``prompt_tokens_details.cached_tokens``) since the Codex endpoint is
    reverse-engineered and may report either."""
    if event.get("type") != "response.completed":
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

    def _cached() -> int:
        for field in ("input_tokens_details", "prompt_tokens_details"):
            details = u.get(field)
            if isinstance(details, dict):
                c = cast(dict[str, Any], details).get("cached_tokens")
                if isinstance(c, int):
                    return c
        return 0

    reasoning = 0
    out_details = u.get("output_tokens_details")
    if isinstance(out_details, dict):
        reasoning = _int(cast(dict[str, Any], out_details).get("reasoning_tokens"))

    return {
        "input_tokens": _int(u.get("input_tokens") or u.get("prompt_tokens")),
        "output_tokens": _int(u.get("output_tokens") or u.get("completion_tokens")),
        "cached_tokens": _cached(),
        "reasoning_tokens": reasoning,
    }


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
