"""Per-row LLM primitives engine: SELECT rows, fan out ``ctx.llm_fn`` per row.

Powers the ``llm_extract`` / ``llm_classify`` tools — the codebase's first
LLM-calling tools (an intentional, bounded departure; every other tool is
deterministic). The engine is pure orchestration over an injected ``ctx.llm_fn``
— no provider construction here, so it is fully testable with a stub. Functional
only on the labrat-agent / AgentLoop path (``run_agent_task`` injects
``ctx.llm_fn``); the tools self-error with a structured result everywhere else.

Note: ``_schema_fields``/``_parse_extract``/``_parse_classify`` currently have no
in-module caller — the orchestrating ``extract_rows``/``classify_rows`` that wire
them in lands in a follow-up task, hence the local ``reportUnusedFunction``
suppressions below (real callers exist today in
``tests/unit/test_llm_primitives_parsing.py``, which pyright does not scan).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import cast

import polars as pl

# Hard fan-out cap: per-row calls multiply cost; never exceed this many rows.
DEFAULT_MAX_ROWS = 200

_SAFE_IDENT = re.compile(r"\w+")

# ``` or ```json fences wrapping the whole reply (models add them despite instructions).
_FENCE_RE = re.compile(r"\A```(?:json)?\s*\n?(.*?)\n?```\s*\Z", re.DOTALL)

_EXTRACT_PROMPT_TEMPLATE = (
    "Extract structured fields from the text below.\n\n"
    "JSON schema of the fields to extract:\n{schema}\n\n"
    "Text:\n{text}\n\n"
    "Respond with ONLY a JSON object containing exactly these fields, matching the "
    "schema above. No prose, no markdown fences, no explanation. Use null for any "
    "field not present in the text."
)

_CLASSIFY_PROMPT_TEMPLATE = (
    "Classify the text below into exactly one category.\n\n"
    "Allowed categories:\n{labels}\n\n"
    "Text:\n{text}\n\n"
    "Respond with ONLY the single best category from the list above, verbatim. "
    "No prose, no punctuation, no explanation."
)


@dataclass
class ExtractResult:
    """Assembled outcome of a per-row extraction/classification batch."""

    df: pl.DataFrame
    rows_processed: int
    rows_failed: int


def _strip_fences(raw: str) -> str:
    """Peel a markdown code fence (``` / ```json) wrapping the whole reply."""
    stripped = raw.strip()
    match = _FENCE_RE.match(stripped)
    return match.group(1).strip() if match else stripped


def _schema_fields(schema: dict[str, object]) -> list[str]:  # pyright: ignore[reportUnusedFunction]
    """Field names to extract: JSON-schema ``properties`` keys, else top-level keys."""
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return list(cast("dict[str, object]", properties).keys())
    return list(schema.keys())


def _parse_extract(  # pyright: ignore[reportUnusedFunction]
    raw: str, fields: list[str]
) -> dict[str, str | None] | None:
    """Parse one extract reply into stringified fields.

    None on ANY failure: non-JSON, non-object JSON, or a missing requested field.
    Values are stringified (result columns are always VARCHAR); JSON null stays None.
    """
    try:
        obj = json.loads(_strip_fences(raw))
    except ValueError:  # json.JSONDecodeError subclasses ValueError
        return None
    if not isinstance(obj, dict):
        return None
    data = cast("dict[str, object]", obj)
    if any(field not in data for field in fields):
        return None
    return {field: None if data[field] is None else str(data[field]) for field in fields}


def _parse_classify(  # pyright: ignore[reportUnusedFunction]
    raw: str, labels: list[str]
) -> str | None:
    """Validate one classify reply against ``labels``; return the canonical label.

    Exact match first, then a case-insensitive match mapped back to the canonical
    spelling. Anything else (out-of-label value, prose) → None (a failed row).
    """
    text = _strip_fences(raw).strip().strip("\"'")
    if text in labels:
        return text
    by_lower = {label.lower(): label for label in labels}
    return by_lower.get(text.lower())
