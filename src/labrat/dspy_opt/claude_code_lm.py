"""Custom DSPy LM that uses the `claude --print` CLI (no API key required).

Works with a Claude Max subscription via the stored credentials from
`claude auth login` or CLAUDE_CODE_OAUTH_TOKEN.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import types
from typing import Any

import dspy


def _make_response(text: str, model: str) -> Any:
    """Build a minimal OpenAI-chat-compatible response object."""
    choice = types.SimpleNamespace(
        message=types.SimpleNamespace(content=text),
        finish_reason="stop",
    )
    return types.SimpleNamespace(
        choices=[choice],
        model=model,
        # usage must support dict() — use a dict subclass
        usage={},
    )


_SUPPORTED_MODELS = {
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "opus": "claude-opus-4-7",
}


class ClaudeCodeLM(dspy.BaseLM):
    """DSPy LM backed by the `claude --print` subprocess (Max subscription).

    Args:
        model: Short alias ("sonnet", "haiku", "opus") or full model ID.
               Defaults to the CLI's own default (currently Sonnet 4.6).
        timeout: Max seconds per call.
        max_tokens: Maximum tokens in the response.
    """

    def __init__(
        self,
        model: str | None = None,
        timeout: int = 120,
        max_tokens: int = 4096,
    ) -> None:
        resolved = _SUPPORTED_MODELS.get(model or "", model) or "claude-sonnet-4-6"
        super().__init__(model=f"claude-code/{resolved}", max_tokens=max_tokens)
        self._model_id = resolved
        self._timeout = timeout
        self._binary = shutil.which("claude")
        if not self._binary:
            raise RuntimeError(
                "claude CLI not found. Install: npm install -g @anthropic-ai/claude-code"
            )

    def forward(
        self,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Any:
        prompt_text = _build_prompt(messages, prompt)

        cmd = [
            self._binary,
            "--print",
            "--output-format",
            "json",
            "--max-turns",
            "1",
            "--tools",
            "",
            "--model",
            self._model_id,
        ]

        proc = subprocess.run(
            cmd,
            input=prompt_text.encode(),
            capture_output=True,
            timeout=self._timeout,
        )

        raw = proc.stdout.decode(errors="replace").strip()

        # claude --output-format json wraps: {"type":"result","result":"..."}
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "result" in data:
                text = str(data["result"])
            else:
                text = raw
        except json.JSONDecodeError:
            text = raw

        if proc.returncode != 0 and not text:
            err = proc.stderr.decode(errors="replace").strip()[:400]
            text = f"[claude CLI error {proc.returncode}]: {err}"

        return _make_response(text, self.model)


def _build_prompt(
    messages: list[dict[str, Any]] | None,
    fallback_prompt: str | None,
) -> str:
    if not messages:
        return fallback_prompt or ""
    parts: list[str] = []
    for m in messages:
        role = str(m.get("role", "user")).upper()
        content = m.get("content", "")
        if isinstance(content, list):
            # Multi-part content (text + images etc.) — join text parts
            content = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        parts.append(f"{role}: {content}")
    return "\n".join(parts)
