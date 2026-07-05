# llm_extract / llm_classify — Per-Row LLM Primitives Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `llm_extract` and `llm_classify` — per-row LLM primitives that fan out one mini-LLM-call per row of a text column from a deterministic loop, binding results outside model context (a queryable DuckDB temp table + a Context Ledger artifact).

**Architecture:** A new optional `ToolContext.llm_fn` (injected only by `run_agent_task` from its own provider via `provider_llm_fn`) powers a shared per-row engine (`agent/tools/llm_primitives.py::extract_rows`) that SELECTs capped rows, calls `ctx.llm_fn` per row, parses JSON/label replies failure-tolerantly, and assembles a Polars DataFrame. Two thin tools (`LlmExtractTool`, `LlmClassifyTool`) wrap the engine, materialize the result via `DuckDBConnection.materialize_table`, and declare it to the ledger via the run_sql `PrivateAttr`/`ledger_payload` idiom. Both are registered in the shared `build_data_tools_registry()` and self-error with a structured `ok=False` wherever `ctx.llm_fn is None`.

**Tech Stack:** Python 3.12, Pydantic v2, Polars (+ Arrow via `df.to_arrow()`), DuckDB, pytest (`asyncio_mode = "auto"`), ruff + pyright strict. **No live LLM calls anywhere in tests — `llm_fn` is always a stub coroutine.**

**Spec:** `docs/superpowers/specs/2026-07-05-llm-extract-classify-design.md`. **Branch:** `feat/llm-extract` (already checked out).

## Global Constraints

Copied verbatim from the spec's Non-negotiables. Every task's requirements implicitly include this section.

- **Functional only where an `llm_fn` is injected (labrat-agent / AgentLoop path).** The tools are registered in the shared builder (so no conditional-registration complexity), but they **self-error with a structured `ok=False` result whenever `ctx.llm_fn is None`** — which is the case on the claude-mcp path (bypasses AgentLoop), the MCP server, the TUI, and any deterministic context. So they are effectively inert everywhere except the labrat-agent path that injects the provider. Not a claude-mcp leaderboard lever; no per-row LLM calls happen on the leaderboard path.
- **Deterministic contexts unaffected / byte-identity preserved:** `ToolContext.llm_fn` defaults `None`; when `None`, the tools return a structured "no LLM available" error rather than raising. Adding the optional field must not change any existing `ToolContext` construction or tool behavior.
- **Bounded fan-out:** a hard `max_rows` cap (default 200) — per-row calls multiply cost; the tool must never fan out unboundedly.
- **Failure-tolerant:** a per-row parse/LLM failure yields a null row + increments `rows_failed`; it never aborts the whole batch.
- **Results bound outside context (reuse the ledger):** the extracted table declares `ledger_payload() -> ("table", df)` so the model sees a bounded summary while the full extraction lives in the `ResultStore` + a queryable DuckDB temp table.

Additional project-wide rules that bind every task:

- Gates before **every** commit, in this order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. All four must be clean/green.
- Pyright strict applies to all of `src/labrat/`. Tool `name`/`description`/`input_model` must be `@property` methods, not class attributes.
- Line length 100 (ruff). `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` decorator on async tests.
- `tests/tui/test_app_renders.py` is env-sensitive and unrelated to this work — a failure there is pre-existing; do not chase it.
- The `where` parameter is an agent-provided raw SQL fragment — same trust model as `run_sql`'s query string. Do not over-engineer it; identifiers (`table`, `text_column`, `key_columns`, `result_table`) ARE guarded.

---

## Pinned design decisions (shared vocabulary for all tasks)

These are load-bearing across tasks — later tasks use these exact names and types.

1. **`LLMFn` alias:** `LLMFn = Callable[[str], Awaitable[str]]`, declared in `src/labrat/agent/tools/base.py` (structurally identical to the existing alias at `src/labrat/agent/verifier.py:21`, which stays untouched — the two are interchangeable to pyright).
2. **Engine signature (Task 4):**
   ```python
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
   ) -> ExtractResult: ...
   ```
   `spec` is a JSON-schema dict (extract mode) or a label list (classify mode). `DEFAULT_MAX_ROWS = 200`. The SELECT row cap is `max_rows if limit is None else min(limit, max_rows)` — the hard cap always wins.
3. **`ExtractResult`:** a dataclass with `df: pl.DataFrame`, `rows_processed: int` (rows SELECTed), `rows_failed: int` (subset of processed whose parse/LLM call failed).
4. **Extracted columns are always Utf8 (VARCHAR):** every extracted field value is stringified (`str(v)`); `None` stays `None`. This keeps the assembled DataFrame schema stable even when a column is all-null.
5. **Failure = null row:** ANY per-row failure — NULL source text (no LLM call made), the `llm_fn` call raising, non-JSON reply, non-object JSON, a missing requested field, an out-of-label category — appends `None` for every extracted field and increments `rows_failed`. The batch never aborts.
6. **Result table default names:** `llm_extract_result` / `llm_classify_result` (fixed valid identifiers that pass `materialize_table`'s `replace("_","").isalnum()` guard) unless `result_table` is given.
7. **`ctx.llm_fn is None` in a tool:** return `_Output(ok=False, error=...)` where the error message contains the phrase `"LLM-enabled context"`. No raise, no LLM call, `ledger_payload()` returns `None`.
8. **Engine error contract:** the engine (not the tools) raises `RuntimeError` when `ctx.llm_fn is None` and `ValueError` on an unsafe identifier / empty spec; the tools catch `Exception` around the engine + materialize call and convert to a structured `_Output(ok=False, error=str(exc))`.
9. **Primary connection only:** the engine reads via `ctx.connection` (the primary); the tools require the primary to be a `DuckDBConnection` (structured error otherwise). No `database:` routing field — matches the spec's U3/U4 input contracts.
10. **Both tools set `mutating = True`** (they materialize a temp table — mirrors `LoadMongoCollectionTool`), so the read-only Analyst-mode dispatch gate blocks them.

---

### Task 1: `ToolContext.llm_fn` — optional per-row LLM callable

**Files:**
- Modify: `src/labrat/agent/tools/base.py` (the `ToolContext.__init__` at line 24 and module imports)
- Test: `tests/unit/test_tool_context_llm_fn.py` (new)

**Interfaces:**
- Consumes: existing `ToolContext.__init__(connection=None, catalog=None, *, connections=None, catalogs=None, primary="primary", profile_name="default", read_only=False)`.
- Produces: `LLMFn = Callable[[str], Awaitable[str]]` exported from `labrat.agent.tools.base`; `ToolContext(..., llm_fn: LLMFn | None = None)` keyword-only, default `None`, stored as the mutable attribute `ctx.llm_fn`. All later tasks import `LLMFn` from here and read/write `ctx.llm_fn`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_tool_context_llm_fn.py`:

```python
"""ToolContext.llm_fn: optional per-row LLM callable, default None."""

from __future__ import annotations

from labrat.agent.tools.base import ToolContext


def test_llm_fn_defaults_none_single_db() -> None:
    ctx = ToolContext(connection=object(), catalog=object())
    assert ctx.llm_fn is None


def test_llm_fn_defaults_none_multi_db() -> None:
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": object()}, primary="main")
    assert ctx.llm_fn is None


async def test_llm_fn_stored_and_callable() -> None:
    async def fake_llm(prompt: str) -> str:
        return f"echo:{prompt}"

    ctx = ToolContext(connection=object(), catalog=object(), llm_fn=fake_llm)
    assert ctx.llm_fn is fake_llm
    assert await ctx.llm_fn("hi") == "echo:hi"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tool_context_llm_fn.py -v`
Expected: FAIL — `AttributeError: 'ToolContext' object has no attribute 'llm_fn'` (first two tests) and `TypeError: ToolContext.__init__() got an unexpected keyword argument 'llm_fn'` (third).

- [ ] **Step 3: Write minimal implementation**

In `src/labrat/agent/tools/base.py`, extend the imports (currently `from abc import ...` / `from dataclasses import ...` / `from typing import Any`):

```python
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

# One-shot async LLM call: prompt in, raw reply text out. Structurally identical to
# the alias in labrat.agent.verifier (kept there for its own callers); tools import
# this one. run_agent_task injects an implementation onto ToolContext.llm_fn.
LLMFn = Callable[[str], Awaitable[str]]
```

Then change `ToolContext.__init__` — add the new keyword-only parameter LAST, preserving every existing parameter and its order, and store it:

```python
    def __init__(
        self,
        connection: object = None,
        catalog: object = None,
        *,
        connections: dict[str, object] | None = None,
        catalogs: dict[str, object] | None = None,
        primary: str = "primary",
        profile_name: str = "default",
        read_only: bool = False,
        llm_fn: LLMFn | None = None,
    ) -> None:
        if connection is not None:
            self.connections: dict[str, object] = {primary: connection}
        else:
            self.connections = dict(connections) if connections is not None else {}

        if catalog is not None:
            self.catalogs: dict[str, object] = {primary: catalog}
        else:
            self.catalogs = dict(catalogs) if catalogs is not None else {}

        self.primary = primary
        self.profile_name = profile_name
        self.read_only = read_only
        self.llm_fn = llm_fn
```

Also extend the `ToolContext` class docstring's last line with one sentence:

```
    ``llm_fn`` is an optional one-shot LLM callable (prompt -> reply) injected by
    ``run_agent_task`` for the per-row llm_extract/llm_classify tools; it defaults
    to None, and every deterministic context leaves it None.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_tool_context_llm_fn.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Gates**

Run in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`
Expected: all clean/green (the full suite proves existing `ToolContext` constructions are unaffected).

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/tools/base.py tests/unit/test_tool_context_llm_fn.py
git commit -m "feat(agent): ToolContext.llm_fn — optional per-row LLM callable (default None)"
```

---

### Task 2: `run_agent_task` injects `ctx.llm_fn` from its provider

**Files:**
- Modify: `src/labrat/agent/runner.py` (imports, a module constant, and `run_agent_task` around line 74-88)
- Test: `tests/unit/test_agent_runner_llm_fn.py` (new)

**Interfaces:**
- Consumes: `ToolContext.llm_fn` (Task 1); `provider_llm_fn(provider, *, system="") -> LLMFn` from `labrat.agent.verifier` (existing, line 81 — adapts a `ModelProvider` to a one-shot tool-less `provider.stream` call).
- Produces: after `run_agent_task(...)` starts, `ctx.llm_fn` is non-None on the labrat-agent path (same model + billing as the loop). Caller-injected `llm_fn` wins (only set when `None`). Module constant `_LLM_FN_SYSTEM: str` in `runner.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_agent_runner_llm_fn.py`:

```python
"""run_agent_task injects ctx.llm_fn from its provider (per-row LLM primitives)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from labrat.agent.loop import ContentBlock, TextBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.runner import run_agent_task
from labrat.agent.tools.base import ToolContext, ToolRegistry


class _FakeProvider(ModelProvider):
    """Replay a scripted sequence of content-block lists across successive stream() calls."""

    def __init__(self, script: list[list[ContentBlock]]) -> None:
        self._script = script
        self._call = 0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        blocks = self._script[self._call]
        self._call += 1

        async def _emit() -> AsyncIterator[ContentBlock]:
            for b in blocks:
                yield b

        return _emit()


async def test_runner_injects_llm_fn() -> None:
    ctx = ToolContext(connections={"primary": object()}, catalogs={"primary": object()})
    provider = _FakeProvider(
        [
            [TextBlock(text="Direct answer.")],  # consumed by the agent run itself
            [TextBlock(text="per-row reply")],  # consumed by the injected llm_fn below
        ]
    )
    await run_agent_task(
        prompt="q", ctx=ctx, registry=ToolRegistry(), provider=provider, system_prompt="s"
    )
    assert ctx.llm_fn is not None
    assert await ctx.llm_fn("extract this") == "per-row reply"


async def test_runner_preserves_caller_injected_llm_fn() -> None:
    async def mine(prompt: str) -> str:
        return "mine"

    ctx = ToolContext(
        connections={"primary": object()}, catalogs={"primary": object()}, llm_fn=mine
    )
    provider = _FakeProvider([[TextBlock(text="Direct answer.")]])
    await run_agent_task(
        prompt="q", ctx=ctx, registry=ToolRegistry(), provider=provider, system_prompt="s"
    )
    assert ctx.llm_fn is mine
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_agent_runner_llm_fn.py -v`
Expected: `test_runner_injects_llm_fn` FAILS with `AssertionError` on `assert ctx.llm_fn is not None`; `test_runner_preserves_caller_injected_llm_fn` already passes (that is fine — it locks the caller-wins contract).

- [ ] **Step 3: Write minimal implementation**

In `src/labrat/agent/runner.py`, add a top-level import (the existing lazy `LLMVerifier, provider_llm_fn` import inside `if verify:` stays as-is; `labrat.agent.verifier` has no top-level labrat imports, so no cycle):

```python
from labrat.agent.verifier import provider_llm_fn
```

Add a module constant after the imports:

```python
# System prompt for the injected per-row llm_fn (llm_extract / llm_classify).
# Kept terse and format-obsessed: each per-row prompt carries its own full
# instructions; this only reinforces the output discipline.
_LLM_FN_SYSTEM = (
    "You are a precise per-row data-extraction engine. Follow the output-format "
    "instructions in each request exactly: reply with ONLY the requested JSON object "
    "or category value — no prose, no markdown fences, no explanation."
)
```

In `run_agent_task`, immediately after the `verifier = None / if verify:` block and before the `ledger: ContextLedger | None = None` block, insert:

```python
    # Per-row LLM primitives (llm_extract / llm_classify) need an injected llm_fn.
    # The loop's own provider doubles as the per-row caller (same model + billing).
    # Only set when the caller hasn't provided one — caller injection wins; every
    # other ToolContext builder (TUI, MCP server, DAB claude-mcp) leaves it None.
    if ctx.llm_fn is None:
        ctx.llm_fn = provider_llm_fn(provider, system=_LLM_FN_SYSTEM)
```

Append one sentence to the `run_agent_task` docstring (after the `enable_ledger` paragraph):

```
    This runner also injects ``ctx.llm_fn`` (via ``provider_llm_fn``) when the caller
    left it None, enabling the per-row llm_extract/llm_classify tools on this path.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_agent_runner_llm_fn.py tests/unit/test_agent_runner.py tests/unit/test_agent_runner_ledger.py -v`
Expected: all PASS (including the pre-existing runner tests — byte-identity of existing behavior).

- [ ] **Step 5: Gates**

Run in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`
Expected: all clean/green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/runner.py tests/unit/test_agent_runner_llm_fn.py
git commit -m "feat(agent): run_agent_task injects ctx.llm_fn from its provider"
```

---

### Task 3: Engine parsing helpers + prompt templates (`llm_primitives.py`)

**Files:**
- Create: `src/labrat/agent/tools/llm_primitives.py`
- Test: `tests/unit/test_llm_primitives_parsing.py` (new)

**Interfaces:**
- Consumes: nothing from earlier tasks (pure functions).
- Produces (Task 4/5 build on these exact names):
  - `DEFAULT_MAX_ROWS: int = 200`
  - `ExtractResult` dataclass: `df: pl.DataFrame`, `rows_processed: int`, `rows_failed: int`
  - `_SAFE_IDENT: re.Pattern[str]` (the `\w+`-fullmatch guard, same shape as `maze/semantic_claims.py:23`)
  - `_EXTRACT_PROMPT_TEMPLATE: str` (placeholders `{schema}`, `{text}`), `_CLASSIFY_PROMPT_TEMPLATE: str` (placeholders `{labels}`, `{text}`)
  - `_strip_fences(raw: str) -> str`
  - `_schema_fields(schema: dict[str, object]) -> list[str]`
  - `_parse_extract(raw: str, fields: list[str]) -> dict[str, str | None] | None`
  - `_parse_classify(raw: str, labels: list[str]) -> str | None`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_llm_primitives_parsing.py`:

```python
"""llm_primitives parsing helpers: fences, schema fields, extract/classify parses."""

from __future__ import annotations

from labrat.agent.tools.llm_primitives import (
    _parse_classify,
    _parse_extract,
    _schema_fields,
    _strip_fences,
)


def test_strip_fences_plain_passthrough() -> None:
    assert _strip_fences('{"a": 1}') == '{"a": 1}'


def test_strip_fences_json_fence() -> None:
    assert _strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_fences_bare_fence() -> None:
    assert _strip_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_schema_fields_json_schema_properties() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "year": {"type": "integer"}},
    }
    assert _schema_fields(schema) == ["name", "year"]


def test_schema_fields_shorthand_dict() -> None:
    assert _schema_fields({"name": "string", "year": "integer"}) == ["name", "year"]


def test_parse_extract_happy_stringifies_values() -> None:
    assert _parse_extract('{"name": "Ada", "year": 1815}', ["name", "year"]) == {
        "name": "Ada",
        "year": "1815",
    }


def test_parse_extract_null_field_kept_as_none() -> None:
    assert _parse_extract('{"name": null, "year": 1815}', ["name", "year"]) == {
        "name": None,
        "year": "1815",
    }


def test_parse_extract_fenced_reply() -> None:
    assert _parse_extract('```json\n{"name": "Ada"}\n```', ["name"]) == {"name": "Ada"}


def test_parse_extract_non_json_fails() -> None:
    assert _parse_extract("Sure! The name is Ada.", ["name"]) is None


def test_parse_extract_non_object_fails() -> None:
    assert _parse_extract('["Ada"]', ["name"]) is None


def test_parse_extract_missing_field_fails() -> None:
    assert _parse_extract('{"name": "Ada"}', ["name", "year"]) is None


def test_parse_classify_exact_match() -> None:
    assert _parse_classify("Business", ["Business", "Sports"]) == "Business"


def test_parse_classify_fenced_and_quoted() -> None:
    assert _parse_classify('```\n"Sports"\n```', ["Business", "Sports"]) == "Sports"


def test_parse_classify_case_insensitive_canonicalizes() -> None:
    assert _parse_classify("sports", ["Business", "Sports"]) == "Sports"


def test_parse_classify_out_of_label_fails() -> None:
    assert _parse_classify("Politics", ["Business", "Sports"]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_llm_primitives_parsing.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'labrat.agent.tools.llm_primitives'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/labrat/agent/tools/llm_primitives.py` (note: only import what this task uses — `ToolContext`/`Connection`/`LLMFn` imports arrive in Task 4 with `extract_rows`, otherwise ruff flags unused imports):

```python
"""Per-row LLM primitives engine: SELECT rows, fan out ``ctx.llm_fn`` per row.

Powers the ``llm_extract`` / ``llm_classify`` tools — the codebase's first
LLM-calling tools (an intentional, bounded departure; every other tool is
deterministic). The engine is pure orchestration over an injected ``ctx.llm_fn``
— no provider construction here, so it is fully testable with a stub. Functional
only on the labrat-agent / AgentLoop path (``run_agent_task`` injects
``ctx.llm_fn``); the tools self-error with a structured result everywhere else.
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


def _schema_fields(schema: dict[str, object]) -> list[str]:
    """Field names to extract: JSON-schema ``properties`` keys, else top-level keys."""
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return list(cast("dict[str, object]", properties).keys())
    return list(schema.keys())


def _parse_extract(raw: str, fields: list[str]) -> dict[str, str | None] | None:
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_llm_primitives_parsing.py -v`
Expected: 15 PASS.

- [ ] **Step 5: Gates**

Run in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`
Expected: all clean/green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/tools/llm_primitives.py tests/unit/test_llm_primitives_parsing.py
git commit -m "feat(tools): llm_primitives parsing helpers + per-row prompt templates"
```

---

### Task 4: `extract_rows` engine — extract mode (capped, failure-tolerant)

**Files:**
- Modify: `src/labrat/agent/tools/llm_primitives.py`
- Test: `tests/unit/test_llm_primitives_engine.py` (new)

**Interfaces:**
- Consumes: Task 1's `ToolContext.llm_fn` + `LLMFn` (from `labrat.agent.tools.base`); Task 3's helpers/templates/`ExtractResult`; `Connection.execute(sql) -> pl.DataFrame` (from `labrat.db.base`).
- Produces: `async def extract_rows(ctx, *, table, text_column, key_columns, spec, where=None, limit=None, max_rows=DEFAULT_MAX_ROWS) -> ExtractResult` (exact signature in Pinned decisions §2) — extract mode (dict spec) fully working; classify mode (list spec) raises `NotImplementedError` until Task 5. Also the internal helper `_extract_one(llm_fn, spec, fields, text) -> dict[str, str | None] | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_llm_primitives_engine.py`:

```python
"""extract_rows engine: SELECT + per-row llm_fn fan-out + assembly (LLM stubbed)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl
import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.llm_primitives import ExtractResult, extract_rows
from labrat.db.duckdb_engine import DuckDBConnection

_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"inventor": {"type": "string"}, "year": {"type": "string"}},
}


def _make_conn(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "engine.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE patents (id INTEGER, abstract VARCHAR)")
    raw.execute(
        "INSERT INTO patents VALUES "
        "(1, 'Invented by Ada in 1843'), "
        "(2, 'Invented by Grace in 1952'), "
        "(3, 'Invented by Alan in 1936')"
    )
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def _fake_extract_llm(prompt: str) -> str:
    if "Ada" in prompt:
        return json.dumps({"inventor": "Ada", "year": "1843"})
    if "Grace" in prompt:
        return json.dumps({"inventor": "Grace", "year": "1952"})
    return json.dumps({"inventor": "Alan", "year": "1936"})


async def test_extract_rows_assembles_dataframe(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_extract_llm)
    result = await extract_rows(
        ctx, table="patents", text_column="abstract", key_columns=["id"], spec=_SCHEMA
    )
    assert isinstance(result, ExtractResult)
    assert result.rows_processed == 3
    assert result.rows_failed == 0
    assert result.df.columns == ["id", "inventor", "year"]
    assert result.df.height == 3
    by_id = dict(zip(result.df["id"].to_list(), result.df["inventor"].to_list(), strict=True))
    assert by_id == {1: "Ada", 2: "Grace", 3: "Alan"}
    assert result.df["inventor"].dtype == pl.Utf8
    assert result.df["year"].dtype == pl.Utf8
    conn.disconnect()


async def test_extract_rows_requires_llm_fn(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None)
    with pytest.raises(RuntimeError, match="llm_fn"):
        await extract_rows(
            ctx, table="patents", text_column="abstract", key_columns=["id"], spec=_SCHEMA
        )
    conn.disconnect()


async def test_extract_rows_rejects_unsafe_identifier(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_extract_llm)
    with pytest.raises(ValueError, match="identifier"):
        await extract_rows(
            ctx,
            table="patents; DROP TABLE patents",
            text_column="abstract",
            key_columns=["id"],
            spec=_SCHEMA,
        )
    conn.disconnect()


async def test_extract_rows_rejects_empty_schema(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_extract_llm)
    with pytest.raises(ValueError, match="field"):
        await extract_rows(
            ctx, table="patents", text_column="abstract", key_columns=["id"], spec={}
        )
    conn.disconnect()


async def test_extract_rows_malformed_reply_yields_null_row(tmp_path: Path) -> None:
    async def flaky(prompt: str) -> str:
        if "Grace" in prompt:
            return "Sure! Grace invented it."  # not JSON → failed row
        return json.dumps({"inventor": "x", "year": "y"})

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=flaky)
    result = await extract_rows(
        ctx, table="patents", text_column="abstract", key_columns=["id"], spec=_SCHEMA
    )
    assert result.rows_processed == 3
    assert result.rows_failed == 1
    row = result.df.filter(pl.col("id") == 2)
    assert row["inventor"].to_list() == [None]
    assert row["year"].to_list() == [None]
    conn.disconnect()


async def test_extract_rows_llm_exception_yields_null_row(tmp_path: Path) -> None:
    async def exploding(prompt: str) -> str:
        if "Alan" in prompt:
            raise TimeoutError("provider timeout")
        return json.dumps({"inventor": "x", "year": "y"})

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=exploding)
    result = await extract_rows(
        ctx, table="patents", text_column="abstract", key_columns=["id"], spec=_SCHEMA
    )
    assert result.rows_processed == 3
    assert result.rows_failed == 1
    conn.disconnect()


async def test_extract_rows_max_rows_caps_select_and_calls(tmp_path: Path) -> None:
    calls: list[str] = []

    async def counting(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"inventor": "x", "year": "y"})

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=counting)
    result = await extract_rows(
        ctx,
        table="patents",
        text_column="abstract",
        key_columns=["id"],
        spec=_SCHEMA,
        max_rows=2,
    )
    assert result.rows_processed == 2
    assert len(calls) == 2
    conn.disconnect()


async def test_extract_rows_limit_clamped_to_max_rows(tmp_path: Path) -> None:
    calls: list[str] = []

    async def counting(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"inventor": "x", "year": "y"})

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=counting)
    result = await extract_rows(
        ctx,
        table="patents",
        text_column="abstract",
        key_columns=["id"],
        spec=_SCHEMA,
        limit=50,
        max_rows=2,
    )
    assert result.rows_processed == 2
    assert len(calls) == 2
    conn.disconnect()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_llm_primitives_engine.py -v`
Expected: FAIL at collection — `ImportError: cannot import name 'extract_rows'`.

- [ ] **Step 3: Write minimal implementation**

In `src/labrat/agent/tools/llm_primitives.py`, add two imports (after `import polars as pl`):

```python
from labrat.agent.tools.base import LLMFn, ToolContext
from labrat.db.base import Connection
```

Append at the end of the module:

```python
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
    is an agent-provided raw SQL fragment (same trust model as run_sql's query);
    ``table``/``text_column``/``key_columns`` ARE validated as identifiers. The row
    cap is ``min(limit, max_rows)`` — the hard cap always wins. Raises RuntimeError
    when ``ctx.llm_fn`` is None and ValueError on an unsafe identifier or an empty
    spec; the tools convert these into structured errors.
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
            raise ValueError("json_schema declares no fields to extract")
    else:
        raise NotImplementedError("classify mode lands with LlmClassifyTool")

    cap = max_rows if limit is None else min(limit, max_rows)
    select_cols = ", ".join([*key_columns, text_column])
    sql = f"SELECT {select_cols} FROM {table}"
    if where is not None:
        sql += f" WHERE {where}"
    sql += f" LIMIT {cap}"
    source = cast(Connection, ctx.connection).execute(sql)

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
```

(`_extract_one` already carries the classify prompt/parse branch — Task 5's failing test drives replacing `extract_rows`'s `NotImplementedError` line with the classify `fields` setup.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_llm_primitives_engine.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Gates**

Run in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`
Expected: all clean/green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/tools/llm_primitives.py tests/unit/test_llm_primitives_engine.py
git commit -m "feat(tools): extract_rows per-row engine — extract mode, capped + failure-tolerant"
```

---

### Task 5: `extract_rows` engine — classify mode (label-constrained)

**Files:**
- Modify: `src/labrat/agent/tools/llm_primitives.py` (the `else: raise NotImplementedError` branch in `extract_rows`)
- Test: `tests/unit/test_llm_primitives_engine.py` (append)

**Interfaces:**
- Consumes: Task 4's `extract_rows` + `_extract_one` (classify prompt/parse branch already present in `_extract_one`).
- Produces: `extract_rows(..., spec=<list[str]>)` returns an `ExtractResult` whose `df` has columns `[*key_columns, "category"]`; `category` values are constrained to the labels (canonical spelling), with out-of-label replies counted as failed rows. Empty label list raises `ValueError`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_llm_primitives_engine.py`:

```python
async def test_extract_rows_classify_mode(tmp_path: Path) -> None:
    async def classify_llm(prompt: str) -> str:
        return "Sports" if "Alan" in prompt else "Business"

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=classify_llm)
    result = await extract_rows(
        ctx,
        table="patents",
        text_column="abstract",
        key_columns=["id"],
        spec=["Business", "Sports"],
    )
    assert result.df.columns == ["id", "category"]
    assert result.rows_failed == 0
    by_id = dict(
        zip(result.df["id"].to_list(), result.df["category"].to_list(), strict=True)
    )
    assert by_id == {1: "Business", 2: "Business", 3: "Sports"}
    conn.disconnect()


async def test_extract_rows_classify_out_of_label_fails_row(tmp_path: Path) -> None:
    async def rogue_llm(prompt: str) -> str:
        return "Politics" if "Grace" in prompt else "Business"

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=rogue_llm)
    result = await extract_rows(
        ctx,
        table="patents",
        text_column="abstract",
        key_columns=["id"],
        spec=["Business", "Sports"],
    )
    assert result.rows_failed == 1
    assert result.df.filter(pl.col("id") == 2)["category"].to_list() == [None]
    conn.disconnect()


async def test_extract_rows_classify_empty_labels_rejected(tmp_path: Path) -> None:
    async def never(prompt: str) -> str:
        raise AssertionError("must not be called")

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=never)
    with pytest.raises(ValueError, match="labels"):
        await extract_rows(
            ctx, table="patents", text_column="abstract", key_columns=["id"], spec=[]
        )
    conn.disconnect()


async def test_extract_rows_where_filters(tmp_path: Path) -> None:
    async def classify_llm(prompt: str) -> str:
        return "Business"

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=classify_llm)
    result = await extract_rows(
        ctx,
        table="patents",
        text_column="abstract",
        key_columns=["id"],
        spec=["Business", "Sports"],
        where="id > 1",
    )
    assert result.rows_processed == 2
    assert set(result.df["id"].to_list()) == {2, 3}
    conn.disconnect()


async def test_extract_rows_null_text_fails_row_without_llm_call(tmp_path: Path) -> None:
    path = str(tmp_path / "nulls.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE notes (id INTEGER, body VARCHAR)")
    raw.execute("INSERT INTO notes VALUES (1, 'hello'), (2, NULL)")
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()

    calls: list[str] = []

    async def counting(prompt: str) -> str:
        calls.append(prompt)
        return "Business"

    ctx = ToolContext(connection=conn, catalog=None, llm_fn=counting)
    result = await extract_rows(
        ctx, table="notes", text_column="body", key_columns=["id"], spec=["Business", "Sports"]
    )
    assert result.rows_processed == 2
    assert result.rows_failed == 1
    assert len(calls) == 1
    conn.disconnect()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_llm_primitives_engine.py -v`
Expected: the 5 new tests FAIL with `NotImplementedError: classify mode lands with LlmClassifyTool` (the empty-labels test fails because `NotImplementedError` is raised instead of `ValueError`); the 8 Task-4 tests still PASS.

- [ ] **Step 3: Write minimal implementation**

In `src/labrat/agent/tools/llm_primitives.py`, inside `extract_rows`, replace:

```python
    else:
        raise NotImplementedError("classify mode lands with LlmClassifyTool")
```

with:

```python
    else:
        if not spec:
            raise ValueError("labels must be a non-empty list")
        fields = ["category"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_llm_primitives_engine.py -v`
Expected: 13 PASS.

- [ ] **Step 5: Gates**

Run in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`
Expected: all clean/green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/tools/llm_primitives.py tests/unit/test_llm_primitives_engine.py
git commit -m "feat(tools): extract_rows classify mode — label-constrained category column"
```

---

### Task 6: `LlmExtractTool` — materialize + ledger_payload + llm_fn-gating

**Files:**
- Create: `src/labrat/agent/tools/llm_extract.py`
- Test: `tests/unit/test_llm_extract_tool.py` (new)

**Interfaces:**
- Consumes: Task 5's complete `extract_rows`; `DuckDBConnection.materialize_table(table_name: str, arrow_table: object) -> None` (`src/labrat/db/duckdb_engine.py:81` — validates the identifier, `CREATE OR REPLACE TEMP TABLE` from Arrow); `LedgerPayloadKind` from `labrat.agent.tools.serialization`; the run_sql `_result_df` PrivateAttr / `attach_result_df` / `ledger_payload` idiom (`src/labrat/agent/tools/run_sql.py` `_Output`, mirrored EXACTLY).
- Produces: `LlmExtractTool` (name `"llm_extract"`, `mutating = True`) with input `{table, text_column, json_schema: dict, key_columns=[], where=None, limit=None, result_table=None}`; output `_Output{ok, result_table, rows_processed, rows_failed, columns, error}` implementing `ledger_payload() -> ("table", df)`. Module constant `DEFAULT_EXTRACT_RESULT_TABLE = "llm_extract_result"`. Task 8 imports `LlmExtractTool` from `labrat.agent.tools.llm_extract`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_llm_extract_tool.py`:

```python
"""LlmExtractTool: materialize + ledger_payload + llm_fn-gating (LLM stubbed)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.llm_extract import LlmExtractTool
from labrat.agent.tools.serialization import LedgerPayloadProvider
from labrat.db.duckdb_engine import DuckDBConnection

_SCHEMA: dict[str, object] = {
    "properties": {"brand": {"type": "string"}, "product": {"type": "string"}}
}


def _make_conn(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "tool.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE reviews (id INTEGER, body VARCHAR)")
    raw.execute(
        "INSERT INTO reviews VALUES (1, 'Great phone by Acme'), (2, 'Bad laptop by Zenith')"
    )
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def _fake_llm(prompt: str) -> str:
    if "Acme" in prompt:
        return json.dumps({"brand": "Acme", "product": "phone"})
    return json.dumps({"brand": "Zenith", "product": "laptop"})


async def test_extract_tool_materializes_queryable_table(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="reviews", text_column="body", json_schema=_SCHEMA, key_columns=["id"]
        ),
    )
    assert out.ok
    assert out.result_table == "llm_extract_result"
    assert out.rows_processed == 2
    assert out.rows_failed == 0
    assert out.columns == ["id", "brand", "product"]
    # The result table is queryable/joinable by a follow-up SQL call.
    df = conn.execute("SELECT brand FROM llm_extract_result ORDER BY id")
    assert df["brand"].to_list() == ["Acme", "Zenith"]
    conn.disconnect()


async def test_extract_tool_ledger_payload(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="reviews", text_column="body", json_schema=_SCHEMA, key_columns=["id"]
        ),
    )
    assert isinstance(out, LedgerPayloadProvider)
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "table"
    assert isinstance(obj, pl.DataFrame)
    assert obj.height == 2
    assert obj.columns == ["id", "brand", "product"]
    conn.disconnect()


async def test_extract_tool_errors_without_llm_fn(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None)  # llm_fn defaults None
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx, tool.input_model(table="reviews", text_column="body", json_schema=_SCHEMA)
    )
    assert not out.ok
    assert out.error is not None
    assert "LLM-enabled context" in out.error
    assert out.ledger_payload() is None
    conn.disconnect()


async def test_extract_tool_custom_result_table(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="reviews",
            text_column="body",
            json_schema=_SCHEMA,
            key_columns=["id"],
            result_table="brands",
        ),
    )
    assert out.ok
    assert out.result_table == "brands"
    assert conn.execute("SELECT COUNT(*) AS n FROM brands")["n"].to_list() == [2]
    conn.disconnect()


async def test_extract_tool_structured_error_on_bad_result_table(tmp_path: Path) -> None:
    calls: list[str] = []

    async def counting(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"brand": "x", "product": "y"})

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=counting)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="reviews",
            text_column="body",
            json_schema=_SCHEMA,
            result_table="bad name; drop",
        ),
    )
    assert not out.ok
    assert out.error is not None
    assert len(calls) == 0  # validated up-front: no per-row calls were burned
    conn.disconnect()


async def test_extract_tool_structured_error_on_engine_failure(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(table="no_such_table", text_column="body", json_schema=_SCHEMA),
    )
    assert not out.ok
    assert out.error is not None
    conn.disconnect()


async def test_extract_tool_requires_duckdb_primary() -> None:
    ctx = ToolContext(connection=object(), catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx, tool.input_model(table="reviews", text_column="body", json_schema=_SCHEMA)
    )
    assert not out.ok
    assert out.error is not None
    assert "DuckDB" in out.error
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_llm_extract_tool.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'labrat.agent.tools.llm_extract'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/labrat/agent/tools/llm_extract.py`:

```python
"""llm_extract tool: per-row LLM field extraction over a text column.

One of the codebase's first LLM-calling tools (with llm_classify) — an
intentional, bounded departure; every other tool is deterministic. Functional
ONLY where ``ctx.llm_fn`` is injected (the labrat-agent / AgentLoop path via
``run_agent_task``); everywhere else (claude-mcp, MCP server, TUI) it
self-errors with a structured ``ok=False`` result. Results are materialized as
a DuckDB TEMP table (joinable via run_sql) and declared to the Context Ledger
via ``ledger_payload()``.
"""

from __future__ import annotations

from typing import Any

import polars as pl
from pydantic import BaseModel, Field, PrivateAttr

from labrat.agent.tools.base import Tool, ToolContext
from labrat.agent.tools.llm_primitives import extract_rows
from labrat.agent.tools.serialization import LedgerPayloadKind
from labrat.db.duckdb_engine import DuckDBConnection

DEFAULT_EXTRACT_RESULT_TABLE = "llm_extract_result"

_NO_LLM_ERROR = (
    "llm_extract requires an LLM-enabled context (no llm_fn is injected on this path). "
    "Use run_sql string functions (regexp_extract, string_split, ...) instead."
)


class _Input(BaseModel):
    table: str = Field(description="Source table (or temp table) holding the text column.")
    text_column: str = Field(description="Column of unstructured text to extract from.")
    json_schema: dict[str, Any] = Field(
        description=(
            "JSON schema of the fields to extract, e.g. "
            '{"properties": {"inventor": {"type": "string"}}}. '
            "Extracted columns are stored as VARCHAR."
        )
    )
    key_columns: list[str] = Field(
        default_factory=list,
        description="Key columns carried into the result table so it can be joined back.",
    )
    where: str | None = Field(default=None, description="Optional SQL WHERE fragment.")
    limit: int | None = Field(
        default=None,
        description="Optional row cap; always clamped to the hard max of 200 rows.",
    )
    result_table: str | None = Field(
        default=None,
        description="Result temp-table name (default 'llm_extract_result').",
    )


class _Output(BaseModel):
    ok: bool
    result_table: str | None = None
    rows_processed: int = 0
    rows_failed: int = 0
    columns: list[str] = []
    error: str | None = None

    # The extracted result frame, carried outside the serialised surface so the
    # ContextLedger can store it as a Parquet artifact. PrivateAttr → excluded
    # from model_dump/JSON and from str(); off-ledger behavior is unchanged.
    _result_df: pl.DataFrame | None = PrivateAttr(default=None)

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        if self.ok and self._result_df is not None:
            return ("table", self._result_df)
        return None

    def attach_result_df(self, df: pl.DataFrame) -> None:
        """Set the private result frame from outside the class (pyright-clean)."""
        self._result_df = df


class LlmExtractTool(Tool[_Input]):
    """Fan out one LLM call per row to extract structured fields from text."""

    mutating = True  # materializes a (temp) result table

    @property
    def name(self) -> str:
        return "llm_extract"

    @property
    def description(self) -> str:
        return (
            "Extract structured fields from an unstructured text column, one LLM call "
            "per row (hard cap 200 rows). Provide a JSON schema of the fields; the "
            "result is materialized as a DuckDB temp table (default "
            "'llm_extract_result') with your key_columns plus one VARCHAR column per "
            "field, joinable with run_sql. Rows whose extraction fails are kept with "
            "NULL fields and counted in rows_failed. Only available when the agent "
            "runtime injects an LLM; otherwise returns a structured error."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        if ctx.llm_fn is None:
            return _Output(ok=False, error=_NO_LLM_ERROR)
        conn = ctx.connection
        if not isinstance(conn, DuckDBConnection):
            return _Output(
                ok=False,
                error=(
                    f"llm_extract requires a DuckDB primary connection; "
                    f"got {type(conn).__name__}."
                ),
            )
        result_table = args.result_table or DEFAULT_EXTRACT_RESULT_TABLE
        # Same guard as materialize_table, applied up-front so a bad name fails
        # BEFORE any per-row LLM calls are spent.
        if not result_table.replace("_", "").isalnum():
            return _Output(
                ok=False,
                error=f"result_table must be alphanumeric/underscore: {result_table!r}",
            )
        try:
            result = await extract_rows(
                ctx,
                table=args.table,
                text_column=args.text_column,
                key_columns=args.key_columns,
                spec=args.json_schema,
                where=args.where,
                limit=args.limit,
            )
            conn.materialize_table(result_table, result.df.to_arrow())  # type: ignore[arg-type]
        except Exception as exc:
            return _Output(ok=False, error=str(exc))
        out = _Output(
            ok=True,
            result_table=result_table,
            rows_processed=result.rows_processed,
            rows_failed=result.rows_failed,
            columns=result.df.columns,
        )
        out.attach_result_df(result.df)
        return out
```

(The `# type: ignore[arg-type]` on the `materialize_table` call mirrors the existing caller in `load_mongo_collection.py:147` — same call shape, proven pyright-clean in this repo.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_llm_extract_tool.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Gates**

Run in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`
Expected: all clean/green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/tools/llm_extract.py tests/unit/test_llm_extract_tool.py
git commit -m "feat(tools): LlmExtractTool — per-row extraction to a queryable temp table"
```

---

### Task 7: `LlmClassifyTool` — label-constrained classification

**Files:**
- Create: `src/labrat/agent/tools/llm_classify.py`
- Test: `tests/unit/test_llm_classify_tool.py` (new)

**Interfaces:**
- Consumes: Task 5's `extract_rows` (classify mode: `spec=<list[str]>` → a `category` column); the same materialize + PrivateAttr/ledger idiom as Task 6.
- Produces: `LlmClassifyTool` (name `"llm_classify"`, `mutating = True`) with input `{table, text_column, labels: list[str], key_columns=[], where=None, limit=None, result_table=None}`; output `_Output` identical in shape to Task 6's (per-tool-module `_Output` classes are the codebase convention — run_sql/sample_rows each own theirs). Module constant `DEFAULT_CLASSIFY_RESULT_TABLE = "llm_classify_result"`. Task 8 imports `LlmClassifyTool` from `labrat.agent.tools.llm_classify`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_llm_classify_tool.py`:

```python
"""LlmClassifyTool: label-constrained per-row classification (LLM stubbed)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.llm_classify import LlmClassifyTool
from labrat.agent.tools.serialization import LedgerPayloadProvider
from labrat.db.duckdb_engine import DuckDBConnection

_LABELS = ["Business", "Sports", "Tech"]


def _make_conn(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "classify.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE articles (id INTEGER, headline VARCHAR)")
    raw.execute(
        "INSERT INTO articles VALUES "
        "(1, 'Stocks rally on earnings'), "
        "(2, 'Local team wins the cup'), "
        "(3, 'New chip breaks records')"
    )
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def _fake_llm(prompt: str) -> str:
    if "Stocks" in prompt:
        return "Business"
    if "team" in prompt:
        return "Sports"
    return "Tech"


async def test_classify_tool_materializes_queryable_table(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmClassifyTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="articles", text_column="headline", labels=_LABELS, key_columns=["id"]
        ),
    )
    assert out.ok
    assert out.result_table == "llm_classify_result"
    assert out.rows_processed == 3
    assert out.rows_failed == 0
    assert out.columns == ["id", "category"]
    df = conn.execute("SELECT category FROM llm_classify_result ORDER BY id")
    assert df["category"].to_list() == ["Business", "Sports", "Tech"]
    conn.disconnect()


async def test_classify_tool_out_of_label_is_failed_row(tmp_path: Path) -> None:
    async def rogue(prompt: str) -> str:
        return "Politics" if "team" in prompt else "Business"

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=rogue)
    tool = LlmClassifyTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="articles", text_column="headline", labels=_LABELS, key_columns=["id"]
        ),
    )
    assert out.ok
    assert out.rows_failed == 1
    df = conn.execute("SELECT category FROM llm_classify_result WHERE id = 2")
    assert df["category"].to_list() == [None]
    conn.disconnect()


async def test_classify_tool_ledger_payload(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmClassifyTool()
    out = await tool.execute(
        ctx, tool.input_model(table="articles", text_column="headline", labels=_LABELS)
    )
    assert isinstance(out, LedgerPayloadProvider)
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "table"
    assert isinstance(obj, pl.DataFrame)
    assert obj.columns == ["category"]
    conn.disconnect()


async def test_classify_tool_errors_without_llm_fn(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None)
    tool = LlmClassifyTool()
    out = await tool.execute(
        ctx, tool.input_model(table="articles", text_column="headline", labels=_LABELS)
    )
    assert not out.ok
    assert out.error is not None
    assert "LLM-enabled context" in out.error
    assert out.ledger_payload() is None
    conn.disconnect()


async def test_classify_tool_empty_labels_structured_error(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmClassifyTool()
    out = await tool.execute(
        ctx, tool.input_model(table="articles", text_column="headline", labels=[])
    )
    assert not out.ok
    assert out.error is not None
    assert "labels" in out.error
    conn.disconnect()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_llm_classify_tool.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'labrat.agent.tools.llm_classify'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/labrat/agent/tools/llm_classify.py`:

```python
"""llm_classify tool: per-row LLM classification of a text column into fixed labels.

One of the codebase's first LLM-calling tools (with llm_extract) — an
intentional, bounded departure; every other tool is deterministic. Functional
ONLY where ``ctx.llm_fn`` is injected (the labrat-agent / AgentLoop path via
``run_agent_task``); everywhere else (claude-mcp, MCP server, TUI) it
self-errors with a structured ``ok=False`` result. Results are materialized as
a DuckDB TEMP table (joinable via run_sql) and declared to the Context Ledger
via ``ledger_payload()``.
"""

from __future__ import annotations

import polars as pl
from pydantic import BaseModel, Field, PrivateAttr

from labrat.agent.tools.base import Tool, ToolContext
from labrat.agent.tools.llm_primitives import extract_rows
from labrat.agent.tools.serialization import LedgerPayloadKind
from labrat.db.duckdb_engine import DuckDBConnection

DEFAULT_CLASSIFY_RESULT_TABLE = "llm_classify_result"

_NO_LLM_ERROR = (
    "llm_classify requires an LLM-enabled context (no llm_fn is injected on this path). "
    "Use run_sql CASE/string expressions instead."
)


class _Input(BaseModel):
    table: str = Field(description="Source table (or temp table) holding the text column.")
    text_column: str = Field(description="Column of unstructured text to classify.")
    labels: list[str] = Field(
        description=(
            "Allowed categories. Each row's category is constrained to this list; "
            "an out-of-list reply becomes a NULL row counted in rows_failed."
        )
    )
    key_columns: list[str] = Field(
        default_factory=list,
        description="Key columns carried into the result table so it can be joined back.",
    )
    where: str | None = Field(default=None, description="Optional SQL WHERE fragment.")
    limit: int | None = Field(
        default=None,
        description="Optional row cap; always clamped to the hard max of 200 rows.",
    )
    result_table: str | None = Field(
        default=None,
        description="Result temp-table name (default 'llm_classify_result').",
    )


class _Output(BaseModel):
    ok: bool
    result_table: str | None = None
    rows_processed: int = 0
    rows_failed: int = 0
    columns: list[str] = []
    error: str | None = None

    # The classified result frame, carried outside the serialised surface so the
    # ContextLedger can store it as a Parquet artifact. PrivateAttr → excluded
    # from model_dump/JSON and from str(); off-ledger behavior is unchanged.
    _result_df: pl.DataFrame | None = PrivateAttr(default=None)

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        if self.ok and self._result_df is not None:
            return ("table", self._result_df)
        return None

    def attach_result_df(self, df: pl.DataFrame) -> None:
        """Set the private result frame from outside the class (pyright-clean)."""
        self._result_df = df


class LlmClassifyTool(Tool[_Input]):
    """Fan out one LLM call per row to classify text into a fixed label set."""

    mutating = True  # materializes a (temp) result table

    @property
    def name(self) -> str:
        return "llm_classify"

    @property
    def description(self) -> str:
        return (
            "Classify an unstructured text column into a fixed set of labels, one LLM "
            "call per row (hard cap 200 rows). The result is materialized as a DuckDB "
            "temp table (default 'llm_classify_result') with your key_columns plus a "
            "VARCHAR 'category' column constrained to the labels, joinable with "
            "run_sql. Rows whose classification fails (including out-of-label replies) "
            "are kept with a NULL category and counted in rows_failed. Only available "
            "when the agent runtime injects an LLM; otherwise returns a structured "
            "error."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        if ctx.llm_fn is None:
            return _Output(ok=False, error=_NO_LLM_ERROR)
        conn = ctx.connection
        if not isinstance(conn, DuckDBConnection):
            return _Output(
                ok=False,
                error=(
                    f"llm_classify requires a DuckDB primary connection; "
                    f"got {type(conn).__name__}."
                ),
            )
        result_table = args.result_table or DEFAULT_CLASSIFY_RESULT_TABLE
        # Same guard as materialize_table, applied up-front so a bad name fails
        # BEFORE any per-row LLM calls are spent.
        if not result_table.replace("_", "").isalnum():
            return _Output(
                ok=False,
                error=f"result_table must be alphanumeric/underscore: {result_table!r}",
            )
        try:
            result = await extract_rows(
                ctx,
                table=args.table,
                text_column=args.text_column,
                key_columns=args.key_columns,
                spec=args.labels,
                where=args.where,
                limit=args.limit,
            )
            conn.materialize_table(result_table, result.df.to_arrow())  # type: ignore[arg-type]
        except Exception as exc:
            return _Output(ok=False, error=str(exc))
        out = _Output(
            ok=True,
            result_table=result_table,
            rows_processed=result.rows_processed,
            rows_failed=result.rows_failed,
            columns=result.df.columns,
        )
        out.attach_result_df(result.df)
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_llm_classify_tool.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Gates**

Run in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`
Expected: all clean/green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/tools/llm_classify.py tests/unit/test_llm_classify_tool.py
git commit -m "feat(tools): LlmClassifyTool — per-row classification to a queryable temp table"
```

---

### Task 8: Register both tools in the shared data-tools registry

**Files:**
- Modify: `src/labrat/agent/data_tools.py` (imports, docstring, and `build_data_tools_registry`)
- Test: `tests/unit/test_llm_tools_registration.py` (new)

**Interfaces:**
- Consumes: `LlmExtractTool` (Task 6), `LlmClassifyTool` (Task 7).
- Produces: `build_data_tools_registry()` includes tools named `"llm_extract"` and `"llm_classify"` — available on the labrat-agent path, structurally inert (structured self-error) everywhere `ctx.llm_fn is None`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_llm_tools_registration.py`:

```python
"""llm_extract / llm_classify are registered in the shared data-tools registry."""

from __future__ import annotations

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import ToolContext


def test_llm_tools_in_default_registry() -> None:
    names = {t.name for t in build_data_tools_registry().tools}
    assert "llm_extract" in names
    assert "llm_classify" in names


async def test_llm_extract_dispatch_without_llm_fn_is_structured_error() -> None:
    """On a deterministic context the tool self-errors — dispatch succeeds, no raise."""
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=object(), catalog=object())  # llm_fn defaults None
    result = await registry.dispatch(
        "llm_extract",
        {"table": "t", "text_column": "c", "json_schema": {"properties": {"x": {}}}},
        ctx,
    )
    assert result.ok  # the dispatch itself succeeded
    ok_flag = getattr(result.value, "ok", None)
    assert ok_flag is False  # ... and the tool returned a structured error


async def test_llm_classify_dispatch_without_llm_fn_is_structured_error() -> None:
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=object(), catalog=object())
    result = await registry.dispatch(
        "llm_classify",
        {"table": "t", "text_column": "c", "labels": ["a", "b"]},
        ctx,
    )
    assert result.ok
    ok_flag = getattr(result.value, "ok", None)
    assert ok_flag is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_llm_tools_registration.py -v`
Expected: FAIL — `assert "llm_extract" in names` raises `AssertionError`; the dispatch tests fail with `DispatchResult.error == "Unknown tool: 'llm_extract'"` tripping `assert result.ok`.

- [ ] **Step 3: Write minimal implementation**

In `src/labrat/agent/data_tools.py`:

1. Add two imports, keeping the block alphabetically sorted (`llm_*` sorts between `list_tables` and `load_file`):

```python
from labrat.agent.tools.llm_classify import LlmClassifyTool
from labrat.agent.tools.llm_extract import LlmExtractTool
```

2. In `build_data_tools_registry()`, after `registry.register(LoadMongoCollectionTool())`, add:

```python
    registry.register(LlmExtractTool())
    registry.register(LlmClassifyTool())
```

3. Update the function docstring's tool list sentence to:

```
    Tools included: search_reference_docs, workflow, profile_dataset, list_tables,
    describe_table, search_columns, link_schema, sample_rows, column_stats,
    run_sql, explain_sql, explain_lineage, verify_join, attach_database, load_file,
    load_mongo_collection, llm_extract, llm_classify.

    llm_extract / llm_classify are per-row LLM primitives: they self-error with a
    structured result whenever ``ctx.llm_fn`` is None (every path except the
    labrat-agent runner, which injects it) — so registering them here adds no LLM
    dependency to deterministic consumers.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_llm_tools_registration.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Gates**

Run in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`
Expected: all clean/green. (The full suite here proves no registry consumer — MCP server, DAB drivers, smoke tests — breaks from two extra registered tools.)

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/data_tools.py tests/unit/test_llm_tools_registration.py
git commit -m "feat(tools): register llm_extract + llm_classify in the shared data-tools registry"
```

---

### Task 9: Ledger-composition test — over-budget extract bounded + retrievable

**Files:**
- Test: `tests/unit/test_llm_extract_ledger_composition.py` (new; no src changes expected — this locks the U-composition contract from the spec's Testing section)

**Interfaces:**
- Consumes: `LlmExtractTool` (Task 6); `ContextLedger(store, budget=LedgerBudget(max_rows, max_bytes))` + `.record(tool_name, DispatchResult) -> ModelVisibleToolResult` from `labrat.runtime.context_ledger`; `ResultStore(root)` + `.get(ref)` from `labrat.results.store`.
- Produces: a pinned regression test — an over-budget extract result enters history truncated with an `artifact_ref` while the full table is retrievable from the store AND queryable in DuckDB.

- [ ] **Step 1: Write the test (expected to pass — it composes already-shipped pieces; treat a failure as a real bug and stop)**

Create `tests/unit/test_llm_extract_ledger_composition.py`:

```python
"""An over-budget llm_extract result is bounded in history, retrievable in full."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl

from labrat.agent.tools.base import DispatchResult, ToolContext
from labrat.agent.tools.llm_extract import LlmExtractTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.results.store import ResultStore
from labrat.runtime.context_ledger import ContextLedger, LedgerBudget


async def _fake_llm(prompt: str) -> str:
    return json.dumps({"topic": "energy"})


async def test_over_budget_extract_is_bounded_and_retrievable(tmp_path: Path) -> None:
    path = str(tmp_path / "ledger.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE docs (id INTEGER, body VARCHAR)")
    raw.executemany(
        "INSERT INTO docs VALUES (?, ?)", [(i, f"doc number {i}") for i in range(20)]
    )
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()

    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="docs",
            text_column="body",
            json_schema={"properties": {"topic": {"type": "string"}}},
            key_columns=["id"],
        ),
    )
    assert out.ok
    assert out.rows_processed == 20

    # A tight budget forces truncation: the model sees a bounded summary + ref...
    ledger = ContextLedger(
        ResultStore(tmp_path / "artifacts"), budget=LedgerBudget(max_rows=5, max_bytes=200)
    )
    visible = ledger.record("llm_extract", DispatchResult(ok=True, value=out))
    assert visible.truncated
    assert visible.full_row_count == 20
    assert visible.artifact_ref is not None

    # ...while the FULL extraction is retrievable from the store...
    stored = ledger.store.get(visible.artifact_ref)
    assert isinstance(stored, pl.DataFrame)
    assert stored.height == 20
    assert stored.columns == ["id", "topic"]

    # ...AND queryable/joinable in DuckDB by table name.
    joined = conn.execute(
        "SELECT COUNT(*) AS n FROM docs d JOIN llm_extract_result r ON d.id = r.id"
    )
    assert joined["n"].to_list() == [20]
    conn.disconnect()
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/unit/test_llm_extract_ledger_composition.py -v`
Expected: 1 PASS. (If it fails, the `ledger_payload` wiring from Task 6 has a real bug — debug with superpowers:systematic-debugging before proceeding; do not weaken the assertions.)

- [ ] **Step 3: Gates**

Run in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`
Expected: all clean/green.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_llm_extract_ledger_composition.py
git commit -m "test(ledger): over-budget llm_extract result bounded in history, retrievable in full"
```

---

### Task 10: decisions.md + CLAUDE.md doc entries + final full gate

**Files:**
- Modify: `decisions.md` (append at end — the log is chronological)
- Modify: `CLAUDE.md` (the data-tools sentence in the "Agent loop" section)

**Interfaces:**
- Consumes: everything shipped in Tasks 1-9.
- Produces: the dated design-log entry the repo requires for significant decisions, and an accurate CLAUDE.md tool list.

- [ ] **Step 1: Append the decisions.md entry**

Append to the end of `decisions.md`:

```markdown
## llm_extract / llm_classify — first LLM-calling tools (per-row primitives) (2026-07-05)

Shipped per-row LLM primitives (`llm_extract`, `llm_classify`) as registered data
tools backed by a shared engine (`agent/tools/llm_primitives.py::extract_rows`)
that fans out one `ctx.llm_fn` call per row from a deterministic loop. PromptQL-
style per-row primitives are white space on the DAB leaderboard (competitive
analysis 2026-07-03) and attack bulk unstructured extraction. Builds on the
Context Ledger: results bind outside model context (`ledger_payload() ->
("table", df)`) AND materialize as a queryable DuckDB temp table
(`llm_extract_result` / `llm_classify_result` by default).

Boundaries (non-negotiable): functional only where `run_agent_task` injects
`ctx.llm_fn` (labrat-agent/AgentLoop path; the runner adapts its own provider via
`provider_llm_fn` — same model + billing) — structured `ok=False` self-error
everywhere else (claude-mcp, MCP server, TUI); hard `max_rows` cap of 200;
per-row failures (NULL text, LLM error, bad JSON, missing field, out-of-label)
yield null rows + `rows_failed`, never aborting the batch; extracted columns are
always VARCHAR. NOT a claude-mcp leaderboard lever (that path bypasses
AgentLoop). Live DAB/patents validation is a deferred follow-on run. Sequential
fan-out for now; concurrency is a later optimization behind the same engine
interface.
```

- [ ] **Step 2: Update the CLAUDE.md tool list**

In `CLAUDE.md`, find the sentence in the "Agent loop" section:

```
Standard data tools come from `data_tools.py::build_data_tools_registry()` — `profile_dataset`, `list_tables`, `describe_table`, `search_columns`, `link_schema`, `sample_rows`, `column_stats`, `run_sql`, `explain_sql`, `verify_join`, `attach_database`, `load_file`, `load_mongo_collection`, `search_reference_docs`, `workflow`.
```

and replace it with:

```
Standard data tools come from `data_tools.py::build_data_tools_registry()` — `profile_dataset`, `list_tables`, `describe_table`, `search_columns`, `link_schema`, `sample_rows`, `column_stats`, `run_sql`, `explain_sql`, `verify_join`, `attach_database`, `load_file`, `load_mongo_collection`, `search_reference_docs`, `workflow`, plus the per-row LLM primitives `llm_extract`/`llm_classify` (labrat-agent path only — they self-error with a structured result when `ctx.llm_fn` is `None`, i.e. on claude-mcp, the MCP server, and the TUI; hard 200-row fan-out cap; results land in a queryable temp table + the ledger).
```

- [ ] **Step 3: Final full gate**

Run in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`
Expected: all clean/green (~700+ tests; the env-sensitive `tests/tui/test_app_renders.py` is the only tolerated pre-existing wobble).

- [ ] **Step 4: Commit**

```bash
git add decisions.md CLAUDE.md
git commit -m "docs: decisions.md + CLAUDE.md entries for llm_extract/llm_classify per-row primitives"
```

---

## Spec-coverage self-review (performed at plan-writing time)

- **U1 (`ToolContext.llm_fn` + runner injection)** → Tasks 1-2. Byte-identity: default `None`, keyword-only, appended last; full-suite gate in both tasks; caller-injection-wins test.
- **U2 (per-row engine)** → Tasks 3-5. Prompts, fence-stripping, JSON/label parsing, identifier guard, `max_rows`/`limit` clamp, failure tolerance (bad JSON, LLM exception, NULL text, out-of-label), `where` pass-through, `ExtractResult{df, rows_processed, rows_failed}`, no provider construction inside the engine (stub-tested).
- **U3 (`LlmExtractTool`)** → Task 6. `ctx.llm_fn is None` → structured error (no raise, no LLM call); DuckDB-primary check; up-front `result_table` guard; `materialize_table(..., df.to_arrow())`; run_sql PrivateAttr/`attach_result_df`/`ledger_payload` idiom mirrored exactly; follow-up-SQL queryability asserted.
- **U4 (`LlmClassifyTool`)** → Task 7. Same engine with `spec = labels`; `category` column constrained to labels; out-of-label = failed row.
- **U5 (registration + regression + gates)** → Tasks 8-10. Shared-builder registration (no conditional-registration complexity) + inert-dispatch tests; ledger-composition test (Task 9, spec Testing bullet 4); decisions.md entry; full gates on every task.
- **Type consistency check:** `LLMFn` (base.py) / `extract_rows` keyword signature / `ExtractResult` fields / `_Output{ok, result_table, rows_processed, rows_failed, columns, error}` / `attach_result_df` / `ledger_payload() -> tuple[LedgerPayloadKind, object] | None` / `DEFAULT_MAX_ROWS = 200` / `DEFAULT_EXTRACT_RESULT_TABLE` / `DEFAULT_CLASSIFY_RESULT_TABLE` are used with identical spellings in every task that references them.
- **Placeholder scan:** all steps carry complete code, exact commands, and exact commit messages; the one intentional intermediate (`NotImplementedError` for classify mode in Task 4) is removed by Task 5's TDD cycle and never ships past that task.
- **Non-goals honored:** no live DAB/patents run, no separate cheap model, no claude-mcp exposure, no concurrency, no program-mode handles.
