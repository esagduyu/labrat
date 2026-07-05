"""Per-row LLM primitives engine: SELECT rows, fan out ``ctx.llm_fn`` per row.

Powers the ``llm_extract`` / ``llm_classify`` tools — the codebase's first
LLM-calling tools (an intentional, bounded departure; every other tool is
deterministic). The engine is pure orchestration over an injected ``ctx.llm_fn``
— no provider construction here, so it is fully testable with a stub. Functional
only on the labrat-agent / AgentLoop path (``run_agent_task`` injects
``ctx.llm_fn``); the tools self-error with a structured result everywhere else.

``extract_rows`` is the deterministic per-row fan-out loop: SELECT up to
``max_rows`` rows, call ``ctx.llm_fn`` once per row via ``_extract_one``, parse,
and assemble the result DataFrame. Extract mode (``spec`` a JSON-schema dict)
and classify mode (``spec`` a label list, single ``category`` column) are both
fully wired.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import cast

import polars as pl

from labrat.agent.tools.base import LLMFn, ToolContext

# Reuse run_sql's statement-stacking guard (sqlglot-based) for safety parity (F2):
# a `where` fragment that stacks a second statement (e.g. "1=1; DROP TABLE t")
# must be refused the same way run_sql refuses "SELECT 1; DROP TABLE t".
from labrat.agent.tools.run_sql import _statement_count  # pyright: ignore[reportPrivateUsage]
from labrat.db.base import Connection

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


# JSON-schema meta-keywords: when a schema has no `properties` dict, the fallback
# (an authoring shorthand, e.g. `{"name": "string", "year": "integer"}`) treats
# top-level keys as field names — EXCEPT these, so a schema shaped like real
# JSON-schema but missing `properties` (e.g. `{"type": "object"}`) yields no
# fields instead of extracting a field literally named `type` (F5).
_JSON_SCHEMA_META_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "description",
        "title",
        "default",
        "$schema",
        "$id",
        "definitions",
        "$defs",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "format",
        "pattern",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "const",
    }
)


def _schema_fields(schema: dict[str, object]) -> list[str]:
    """Field names to extract: JSON-schema ``properties`` keys, else non-keyword top-level keys.

    Full JSON-schema form (a ``properties`` dict) always wins. The fallback supports
    an authoring shorthand — a plain ``{"field": "type"}`` dict with no ``properties``
    wrapper — but excludes JSON-schema meta-keywords (``type``, ``required``, ...), so
    a schema shaped like real JSON-schema but missing its ``properties`` (e.g.
    ``{"type": "object"}``) yields NO fields rather than extracting a field literally
    named ``type``; the caller then raises a structured error instead of spending an
    LLM call per row on a keyword.
    """
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return list(cast("dict[str, object]", properties).keys())
    return [key for key in schema if key not in _JSON_SCHEMA_META_KEYS]


def _stringify_value(value: object) -> str | None:
    """Stringify one parsed field value for a VARCHAR result column.

    A dict/list value is stored as ``json.dumps`` (JSON-parseable downstream);
    ``str(value)`` on a dict/list would use Python repr (single quotes), which is
    NOT valid JSON. Scalars stay ``str(value)``; JSON null stays ``None``.
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _parse_extract(raw: str, fields: list[str]) -> dict[str, str | None] | None:
    """Parse one extract reply into stringified fields.

    None on ANY failure: non-JSON, non-object JSON, or a missing requested field.
    Values are stringified (result columns are always VARCHAR); JSON null stays None;
    a dict/list value is JSON-encoded rather than Python-repr'd (see _stringify_value).
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
    return {field: _stringify_value(data[field]) for field in fields}


def _parse_classify(raw: str, labels: list[str]) -> str | None:
    """Validate one classify reply against ``labels``; return the canonical label.

    Exact match first, then a case-insensitive match mapped back to the canonical
    spelling. Anything else (out-of-label value, prose) → None (a failed row).
    """
    text = _strip_fences(raw).strip().strip("\"'")
    if text in labels:
        return text
    by_lower = {label.lower(): label for label in labels}
    return by_lower.get(text.lower())


async def extract_rows(
    ctx: ToolContext,
    *,
    table: str,
    text_column: str,
    key_columns: list[str],
    spec: dict[str, object] | list[str],
    where: str | None = None,
    limit: int | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> ExtractResult:
    """SELECT up to ``max_rows`` rows and fan out one ``ctx.llm_fn`` call per row.

    ``spec`` is a JSON-schema dict (extract mode: one Utf8 column per schema field)
    or a label list (classify mode: a single Utf8 ``category`` column constrained to
    the labels). A per-row parse/LLM failure — or a NULL text cell — yields a
    null-filled row and increments ``rows_failed``; the batch never aborts. ``where``
    is an agent-provided raw SQL fragment — TRUE safety parity with run_sql's query:
    statement-stacking (e.g. ``"1=1; DROP TABLE t"``) is refused outright, the same
    sqlglot-based guard run_sql uses, BEFORE any execution or LLM spend.
    ``table``/``text_column``/``key_columns`` ARE validated as identifiers. The row
    cap is ``min(limit, max_rows)`` — the hard cap always wins and is enforced TWICE:
    a SQL ``LIMIT`` (fast path) plus a Python-layer ``DataFrame.head(cap)`` backstop,
    so a ``where`` fragment ending in a trailing SQL comment (which comments out the
    appended LIMIT) still cannot fan out past ``cap``. Raises RuntimeError when
    ``ctx.llm_fn`` is None and ValueError on an unsafe identifier, statement-stacking,
    a key/text/field name collision, or an empty spec; the tools convert these into
    structured errors.
    """
    llm_fn = ctx.llm_fn
    if llm_fn is None:
        raise RuntimeError("extract_rows requires an LLM-enabled context (ctx.llm_fn is None)")
    for ident in (table, text_column, *key_columns):
        if not _SAFE_IDENT.fullmatch(ident):
            raise ValueError(f"unsafe SQL identifier: {ident!r}")

    if isinstance(spec, dict):
        fields = _schema_fields(spec)
        if not fields:
            raise ValueError(
                "json_schema declares no fields to extract — define a `properties` dict"
            )
    else:
        if not spec:
            raise ValueError("labels must be a non-empty list")
        fields = ["category"]

    # F4: catch a key_columns/text_column/schema-field name collision BEFORE any
    # per-row LLM spend. Left unchecked, this only surfaces as a polars
    # DuplicateError at DataFrame-assembly time — AFTER the whole batch has
    # already been spent on LLM calls.
    all_names = [*key_columns, text_column, *fields]
    if len(set(all_names)) != len(all_names):
        seen: dict[str, int] = {}
        for name in all_names:
            seen[name] = seen.get(name, 0) + 1
        dupes = sorted(name for name, count in seen.items() if count > 1)
        raise ValueError(
            f"key_columns/text_column/extracted field names collide: {dupes} — "
            "rename to avoid a column-name clash in the result table."
        )

    cap = max_rows if limit is None else min(limit, max_rows)
    select_cols = ", ".join([*key_columns, text_column])
    sql = f"SELECT {select_cols} FROM {table}"
    if where is not None:
        sql += f" WHERE {where}"

    # F2: refuse statement-stacking in the composed SELECT — same guard run_sql
    # applies to the agent-raw query. Checked on the pre-LIMIT SQL, BEFORE the
    # LIMIT clause is appended: appending "LIMIT n" after a stacked second
    # statement (e.g. "...; DROP TABLE t") can turn the whole string into
    # unparseable SQL, which would fail-open the statement count back to 1 and
    # defeat this guard. Checking pre-LIMIT sees the stacked statements cleanly.
    if _statement_count(sql) > 1:
        raise ValueError(
            "Multiple SQL statements are not allowed in `where`; submit a single "
            "predicate fragment per call."
        )

    sql += f" LIMIT {cap}"
    source = cast(Connection, ctx.connection).execute(sql)
    # F1 (BLOCKING): Python-layer backstop. A `where` fragment ending in a
    # trailing SQL comment (e.g. "1=1 --") comments out the appended LIMIT, so
    # the SQL fast path alone cannot be trusted to bound the fan-out — DuckDB
    # silently drops the LIMIT and returns the whole table. This guarantees the
    # hard cap regardless of what `where` does to the SQL LIMIT clause.
    source = source.head(cap)

    values: dict[str, list[str | None]] = {field: [] for field in fields}
    rows_failed = 0
    for row in source.iter_rows(named=True):
        parsed = await _extract_one(llm_fn, spec, fields, row[text_column])
        if parsed is None:
            rows_failed += 1
            for field in fields:
                values[field].append(None)
        else:
            for field in fields:
                values[field].append(parsed[field])

    series = [source[key] for key in key_columns]
    series.extend(pl.Series(field, values[field], dtype=pl.Utf8) for field in fields)
    return ExtractResult(
        df=pl.DataFrame(series), rows_processed=source.height, rows_failed=rows_failed
    )


async def _extract_one(
    llm_fn: LLMFn,
    spec: dict[str, object] | list[str],
    fields: list[str],
    text: object,
) -> dict[str, str | None] | None:
    """One row: build the prompt, call the LLM, parse. None on ANY failure.

    A NULL text cell fails without spending an LLM call.
    """
    if text is None:
        return None
    if isinstance(spec, dict):
        prompt = _EXTRACT_PROMPT_TEMPLATE.format(schema=json.dumps(spec, indent=2), text=str(text))
    else:
        prompt = _CLASSIFY_PROMPT_TEMPLATE.format(
            labels="\n".join(f"- {label}" for label in spec), text=str(text)
        )
    try:
        raw = await llm_fn(prompt)
    except Exception:
        return None
    if isinstance(spec, dict):
        return _parse_extract(raw, fields)
    category = _parse_classify(raw, spec)
    return None if category is None else {"category": category}
