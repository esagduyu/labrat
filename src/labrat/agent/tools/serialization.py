"""Model-visible tool-result contract: what the Context Ledger lets into history.

Two pieces:
  - ``ModelVisibleToolResult`` + ``render()`` — the bounded string that replaces
    a raw tool payload in AgentLoop history. When ``truncated`` is False the
    rendered string is EXACTLY the preview (== today's ``str(dispatch.value)``),
    so an under-budget result is byte-identical to the no-ledger path.
  - ``LedgerPayloadProvider`` — the explicit typed hook by which a tool output
    declares its large payload. The ledger never sniffs value shapes.

Purely mechanical — no LLM call anywhere in this module.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

LedgerPayloadKind = Literal["table", "json", "trace"]


@runtime_checkable
class LedgerPayloadProvider(Protocol):
    """A tool output that declares its large payload for the ledger to store.

    Return ``(kind, payload)`` — payload is a ``pl.DataFrame`` for ``"table"``,
    a JSON-serialisable object for ``"json"``, or a list of JSON-serialisable
    items for ``"trace"`` — or ``None`` when this particular result carries no
    large payload (e.g. an error/refused output). Tool outputs WITHOUT this
    hook fall back to today's ``str(value)`` (byte-bounded only).
    """

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None: ...


class ModelVisibleToolResult(BaseModel):
    """The bounded representation of one tool result that enters model history."""

    summary: str
    preview: str
    artifact_ref: str | None = None
    full_row_count: int | None = None
    truncated: bool = False


def render(result: ModelVisibleToolResult) -> str:
    """Compact string for AgentLoop history.

    Not truncated → exactly the preview. Truncated → a framed block with the
    mechanical summary, optional full_row_count, the artifact_ref (the model can
    cite it; provenance resolves through the ResultStore), and the preview.
    """
    if not result.truncated:
        return result.preview
    lines = [f"[context ledger] {result.summary}"]
    if result.full_row_count is not None:
        lines.append(f"full_row_count: {result.full_row_count}")
    if result.artifact_ref is not None:
        lines.append(f"artifact_ref: {result.artifact_ref}")
    lines.append("preview:")
    lines.append(result.preview)
    return "\n".join(lines)
