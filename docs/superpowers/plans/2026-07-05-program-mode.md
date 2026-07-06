# Program Mode (`run_program`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-07-05-program-mode-design.md` (M4 sub-project 2.2)
**Branch:** `feat/program-mode` (already checked out)

**Goal:** Add a `run_program` tool that executes one model-emitted JSON pipeline of registered-tool steps with handle-bound intermediates (DuckDB temp tables), returning only a bounded execution summary — preventing intermediate round-trips instead of just bounding them.

**Architecture:** A new `src/labrat/agent/program/` package holds the pure DSL (`dsl.py`: `Program`/`ProgramStep` models + `resolve_refs` handle substitution) and the interpreter (`interpreter.py`: sequential `registry.dispatch` per step, table materialization as `program_<bind>` temp tables, bounded `ProgramResult`). A thin `RunProgramTool` (`src/labrat/agent/tools/run_program.py`) wraps the interpreter and builds its step-dispatch sub-registry via `build_data_tools_registry(include_program=False)` at execute time (deferred import breaks the module cycle; the exclusion is the recursion guard).

**Tech Stack:** Python 3.12+, Pydantic v2 models, existing `ToolRegistry`/`ToolContext`/`DispatchResult` (`src/labrat/agent/tools/base.py`), `LedgerPayloadProvider` protocol (`src/labrat/agent/tools/serialization.py`), `DuckDBConnection.materialize_table` (`src/labrat/db/duckdb_engine.py`), polars, pytest with `asyncio_mode="auto"` (no decorator on async tests), ruff + pyright strict.

## Global Constraints

Copied verbatim from the spec's Non-negotiables — every task's requirements implicitly include this section:

- **Safe by construction:** the interpreter ONLY dispatches registered tools via the existing `ToolRegistry`, with the SAME `ToolContext` — so a program inherits every existing gate (M3 read-only `is_mutating`, taint/contamination, per-tool caps like `llm_extract`'s `max_rows`) and can do nothing a tool can't. No `eval`, no arbitrary code, no new sandbox.
- **Bounded:** a hard **max-steps cap** (default 20) per program; each step keeps its own caps. **Stop-on-error** — a failed step halts and returns the partial summary + the failing step index.
- **Context prevention:** only a bounded `_Output` summary returns to model history (per-step status + handles + small previews) — intermediate table payloads live in temp tables / the `ResultStore`, never round-tripped. `mutating=True` (creates temp tables → composes with the M3 read-only gate).
- **Additive / backward-compatible:** a new tool + new modules; no change to the loop, existing tools, or any existing path. Not a claude-mcp leaderboard lever (composes tools on the AgentLoop path).
- `run_program` is **excluded from its own sub-registry** (a step `{tool: "run_program"}` → unknown-tool error; no nested programs / recursion in this slice).

Repo-wide gates before **every** commit, in this order (CI enforces all):

```bash
uv run ruff format .   # first — fixes formatting in-place
uv run ruff check .    # must be clean (bugbear: any zip() needs strict=)
uv run pyright         # must be clean (strict on src/labrat/)
uv run pytest -q       # must pass (env-sensitive tests/tui/test_app_renders.py is unrelated — ignore if it is the only failure and it also fails on a clean checkout)
```

Repo conventions that apply throughout: tool `name`/`description`/`input_model` are `@property` methods, not class attributes; `asyncio_mode="auto"`; `json.loads`/`model_dump` values under pyright strict are handled via `isinstance` narrowing + `cast` where needed; plain `python3` doesn't see project deps — always `uv run`.

## Pinned design decisions (resolved during planning — do not re-litigate)

1. **Handle-ref token:** `_REF_TOKEN = re.compile(r"\$([A-Za-z_]\w*)(?:\.(\w+))?")`. Verified live: `$100` (SQL money literal) never matches (digit after `$`); `FROM $facts f JOIN $docs d` yields two bare tokens; `$facts.rows_failed` yields `("facts", "rows_failed")`. Rules:
   - A string that is **exactly** one token (`_REF_TOKEN.fullmatch`): bare `$handle` → the handle's temp-table name (`program_<handle>`, a `str`); `$handle.field` → the **native** value of `handles[handle].output[field]` (may be non-str, e.g. an `int`).
   - A string **containing** tokens as substrings (`_REF_TOKEN.sub`): each token replaced by its string form (temp-table name; `str(field_value)` for `.field`).
   - Unknown handle, `.field` missing from the output dump, or a **bare `$handle` whose step produced no table** → raise `ProgramError` (typed). Substitution is textual — a token inside SQL quotes is still substituted (documented behavior).
2. **Duplicate/invalid `bind` validation lives on the Pydantic models** (a `field_validator` for `_SAFE_IDENT` on `ProgramStep.bind`, a `model_validator` for duplicates on `Program`) — so the tool path gets it for free at `registry.dispatch` input validation (`ValidationError` → `DispatchResult(ok=False)`), and a hand-built `Program` fails at construction.
3. **Sub-registry-minus-self mechanism:** `build_data_tools_registry(include_program: bool = True)`. The builder registers `RunProgramTool()` only when `include_program` is True; `RunProgramTool.execute` calls `build_data_tools_registry(include_program=False)` via a **deferred import inside `execute`** (`data_tools` imports `run_program` at module top to register the tool; a top-level reverse import would be circular). No construction recursion: the builder only *constructs* the tool (cheap, no registry built); the sub-registry is built at execute time only.
4. **A step fails if `DispatchResult.ok` is False OR the step output's `model_dump()` has `ok is False`** — tools like `run_sql`/`llm_extract` report failures as structured `ok=False` outputs without raising, and a "successful" failed step would poison later `$handle` refs.
5. **Table materialization:** after an ok step, if `isinstance(dispatch.value, LedgerPayloadProvider)` and `ledger_payload()` returns `("table", df)` with `df` a `pl.DataFrame` → materialize `program_<bind>` on the **primary** connection (`ctx.connections.get(ctx.primary)`); non-`DuckDBConnection` primary → structured step failure (mirrors `llm_extract`'s guard), stop-on-error. `df.to_arrow()` carries the same `# type: ignore[arg-type]` as `llm_extract.py`.
6. **`RunProgramTool.execute` returns `ProgramResult` directly** — it already *is* the bounded output shape the spec calls `_Output` (`ok, steps, final_bind, final_table, error`); a duplicate `_Output` class would only invite type drift. The spec's `final_handle` is realized as `final_bind`.
7. **Schema export:** `Program.model_json_schema()` puts `ProgramStep` under `$defs`, which the base `anthropic_schema()`/`openai_schema()` drop (dangling `$ref`). `RunProgramTool` overrides both to pass `$defs` through — a per-tool additive override; `base.py` is untouched.
8. **`run_sql`'s real input field is `query`** (not `sql` as in the spec's illustrative example) — all program tests use `{"tool": "run_sql", "args": {"query": ...}}`. Tests dispatching `run_sql` monkeypatch the history singleton: `monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))`.

## File map

| File | Role |
|---|---|
| Create `src/labrat/agent/program/__init__.py` | package marker |
| Create `src/labrat/agent/program/dsl.py` | `ProgramError`, `ProgramStep`, `Program`, `ResolvedHandle`, `resolve_refs` (pure, no DB/LLM) |
| Create `src/labrat/agent/program/interpreter.py` | `StepSummary`, `ProgramResult`, `run_program`, `DEFAULT_MAX_STEPS` |
| Create `src/labrat/agent/tools/run_program.py` | `RunProgramTool` |
| Modify `src/labrat/agent/data_tools.py` | `include_program` flag + registration |
| Modify `decisions.md`, `CLAUDE.md` | dated design-log entry; tool-list mention |
| Create `tests/unit/test_program_dsl.py` | Tasks 1–2 |
| Create `tests/unit/test_program_interpreter.py` | Tasks 3–5 |
| Create `tests/unit/test_run_program_tool.py` | Task 6 |
| Create `tests/unit/test_run_program_safety.py` | Task 7 |
| Create `tests/unit/test_program_composition.py` | Task 8 |

---

# Phase A — DSL models + handle resolution

### Task 1: `ProgramStep` / `Program` models + `ProgramError`

**Files:**
- Create: `src/labrat/agent/program/__init__.py`
- Create: `src/labrat/agent/program/dsl.py`
- Test: `tests/unit/test_program_dsl.py`

**Interfaces:**
- Consumes: nothing (pure Pydantic).
- Produces: `ProgramError(Exception)`; `ProgramStep(BaseModel)` with `tool: str`, `args: dict[str, Any]` (default `{}`), `bind: str` (validated `\w+`-fullmatch); `Program(BaseModel)` with `steps: list[ProgramStep]` (min length 1, duplicate `bind` rejected). Module constants `_SAFE_IDENT`, `_REF_TOKEN`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_program_dsl.py`:

```python
"""Program DSL models + handle resolution: pure, no DB, no LLM."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from labrat.agent.program.dsl import Program, ProgramStep


def test_program_step_holds_tool_args_bind() -> None:
    step = ProgramStep(tool="run_sql", args={"query": "SELECT 1"}, bind="docs")
    assert step.tool == "run_sql"
    assert step.args == {"query": "SELECT 1"}
    assert step.bind == "docs"


def test_program_step_args_default_empty() -> None:
    step = ProgramStep(tool="list_tables", bind="t")
    assert step.args == {}


def test_bind_rejects_unsafe_ident() -> None:
    with pytest.raises(ValidationError, match="alphanumeric"):
        ProgramStep(tool="run_sql", args={}, bind="bad name; drop")


def test_bind_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        ProgramStep(tool="run_sql", args={}, bind="")


def test_duplicate_bind_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate bind"):
        Program(
            steps=[
                ProgramStep(tool="run_sql", args={"query": "SELECT 1"}, bind="x"),
                ProgramStep(tool="run_sql", args={"query": "SELECT 2"}, bind="x"),
            ]
        )


def test_empty_program_rejected() -> None:
    with pytest.raises(ValidationError):
        Program(steps=[])


def test_valid_program_accepted() -> None:
    program = Program(
        steps=[
            ProgramStep(tool="run_sql", args={"query": "SELECT 1"}, bind="a"),
            ProgramStep(tool="run_sql", args={"query": "SELECT 2"}, bind="b"),
        ]
    )
    assert [s.bind for s in program.steps] == ["a", "b"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_program_dsl.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'labrat.agent.program'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/labrat/agent/program/__init__.py`:

```python
"""Program mode: restricted tool-pipeline DSL + interpreter (M4 2.2).

A program is an ordered JSON pipeline of registered-tool steps with handle
binding — NOT arbitrary code. Safe by construction: execution only ever
dispatches registered tools through the existing ToolRegistry.
"""
```

Create `src/labrat/agent/program/dsl.py`:

```python
"""Program-mode DSL: pipeline models + handle-reference resolution.

Pure and deterministic — no DB access and no LLM call anywhere in this module.
Handle refs in step args: ``$handle`` resolves to that step's materialized
temp-table name (``program_<handle>``); ``$handle.field`` resolves to a scalar
field of that step's output dump. The token must start with a letter or
underscore, so SQL dollar-literals like ``$100`` are never touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# Same identifier guard shape as llm_primitives.py / sample_rows.py.
_SAFE_IDENT = re.compile(r"\w+")

# $handle or $handle.field — verified: `$100` never matches (digit after $);
# `FROM $facts f JOIN $docs d` yields two bare tokens.
_REF_TOKEN = re.compile(r"\$([A-Za-z_]\w*)(?:\.(\w+))?")


class ProgramError(Exception):
    """A structured program-level failure (bad ref, bad bind). Never a crash —

    the interpreter converts it into a failed-step summary (stop-on-error)."""


class ProgramStep(BaseModel):
    """One pipeline step: dispatch ``tool`` with ``args``, bind the result to ``bind``."""

    tool: str = Field(description="Name of a registered tool to dispatch.")
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Tool arguments; strings may reference earlier steps via $handle / $handle.field.",
    )
    bind: str = Field(description="Handle name for this step's result (alphanumeric/underscore).")

    @field_validator("bind")
    @classmethod
    def _bind_is_safe_ident(cls, v: str) -> str:
        if not _SAFE_IDENT.fullmatch(v):
            raise ValueError(f"bind must be alphanumeric/underscore: {v!r}")
        return v


class Program(BaseModel):
    """An ordered pipeline of tool steps with unique handle binds."""

    steps: list[ProgramStep] = Field(min_length=1, description="Steps, run sequentially.")

    @model_validator(mode="after")
    def _binds_unique(self) -> Program:
        seen: set[str] = set()
        for step in self.steps:
            if step.bind in seen:
                raise ValueError(f"duplicate bind: {step.bind!r}")
            seen.add(step.bind)
        return self


@dataclass
class ResolvedHandle:
    """What a completed step exposes to later steps' $refs."""

    table: str | None  # program_<bind> temp-table name, when the step produced a table
    output: dict[str, Any]  # the step output's model_dump(), for $handle.field lookups
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_program_dsl.py -v`
Expected: 7 PASSED

- [ ] **Step 5: Gates + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/program/ tests/unit/test_program_dsl.py
git commit -m "feat(program): Program/ProgramStep DSL models with bind validation (U1)"
```

---

### Task 2: `resolve_refs` handle substitution

**Files:**
- Modify: `src/labrat/agent/program/dsl.py` (append after `ResolvedHandle`)
- Test: `tests/unit/test_program_dsl.py` (append)

**Interfaces:**
- Consumes: `ResolvedHandle`, `ProgramError`, `_REF_TOKEN` (Task 1).
- Produces: `def resolve_refs(args: dict[str, Any], handles: dict[str, ResolvedHandle]) -> dict[str, Any]` — the interpreter (Task 3) calls this on every step's args.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_program_dsl.py`, first extend the existing top-of-file import to:

```python
from labrat.agent.program.dsl import (
    Program,
    ProgramError,
    ProgramStep,
    ResolvedHandle,
    resolve_refs,
)
```

then append at the end of the file:

```python
# ── resolve_refs ────────────────────────────────────────────────────────────


def _handles() -> dict[str, ResolvedHandle]:
    return {
        "docs": ResolvedHandle(table="program_docs", output={"ok": True, "row_count": 3}),
        "facts": ResolvedHandle(
            table="program_facts",
            output={"ok": True, "rows_processed": 3, "rows_failed": 1},
        ),
        "note": ResolvedHandle(table=None, output={"ok": True, "row_count": 0}),
    }


def test_whole_string_bare_handle_resolves_to_table_name() -> None:
    out = resolve_refs({"table": "$docs"}, _handles())
    assert out == {"table": "program_docs"}


def test_whole_string_field_ref_resolves_to_native_value() -> None:
    out = resolve_refs({"n": "$facts.rows_failed"}, _handles())
    assert out == {"n": 1}  # native int, not "1"


def test_embedded_refs_substituted_inline() -> None:
    out = resolve_refs(
        {"query": "SELECT d.id, f.drug FROM $facts f JOIN $docs d USING (id)"},
        _handles(),
    )
    assert out == {"query": "SELECT d.id, f.drug FROM program_facts f JOIN program_docs d USING (id)"}


def test_embedded_field_ref_substituted_as_str() -> None:
    out = resolve_refs({"query": "SELECT * FROM t LIMIT $facts.rows_failed"}, _handles())
    assert out == {"query": "SELECT * FROM t LIMIT 1"}


def test_dollar_digit_literal_untouched() -> None:
    out = resolve_refs({"query": "SELECT * FROM t WHERE price > $100"}, _handles())
    assert out == {"query": "SELECT * FROM t WHERE price > $100"}


def test_recurses_through_nested_dicts_and_lists() -> None:
    out = resolve_refs(
        {"outer": {"tables": ["$docs", "$facts"], "keep": 7}, "flag": True},
        _handles(),
    )
    assert out == {"outer": {"tables": ["program_docs", "program_facts"], "keep": 7}, "flag": True}


def test_non_string_scalars_pass_through() -> None:
    out = resolve_refs({"limit": 5, "force": False, "opt": None}, _handles())
    assert out == {"limit": 5, "force": False, "opt": None}


def test_unknown_handle_raises_program_error() -> None:
    with pytest.raises(ProgramError, match="unknown handle"):
        resolve_refs({"table": "$nope"}, _handles())


def test_embedded_unknown_handle_raises_program_error() -> None:
    with pytest.raises(ProgramError, match="unknown handle"):
        resolve_refs({"query": "SELECT * FROM $nope x"}, _handles())


def test_missing_field_raises_program_error() -> None:
    with pytest.raises(ProgramError, match="no field"):
        resolve_refs({"n": "$docs.nonexistent"}, _handles())


def test_bare_ref_to_tableless_handle_raises_program_error() -> None:
    with pytest.raises(ProgramError, match="produced no table"):
        resolve_refs({"table": "$note"}, _handles())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_program_dsl.py -v`
Expected: the 11 new tests FAIL/ERROR with `ImportError: cannot import name 'resolve_refs'`

- [ ] **Step 3: Write the minimal implementation**

Append to `src/labrat/agent/program/dsl.py` (also add `cast` to the `typing` import: `from typing import Any, cast`):

```python
def _resolve_token(
    handle: str, field_name: str | None, handles: dict[str, ResolvedHandle]
) -> object:
    if handle not in handles:
        raise ProgramError(f"unknown handle: ${handle} (bound so far: {sorted(handles)})")
    resolved = handles[handle]
    if field_name is None:
        if resolved.table is None:
            raise ProgramError(
                f"${handle} refers to a step that produced no table; "
                f"use ${handle}.<field> to pass a scalar output forward"
            )
        return resolved.table
    if field_name not in resolved.output:
        raise ProgramError(
            f"${handle}.{field_name}: no field {field_name!r} in that step's output "
            f"(available: {sorted(resolved.output)})"
        )
    return resolved.output[field_name]


def _resolve_str(value: str, handles: dict[str, ResolvedHandle]) -> object:
    whole = _REF_TOKEN.fullmatch(value)
    if whole is not None:
        # A whole-string ref may resolve to a non-str value ($handle.field).
        return _resolve_token(whole.group(1), whole.group(2), handles)

    def repl(m: re.Match[str]) -> str:
        return str(_resolve_token(m.group(1), m.group(2), handles))

    return _REF_TOKEN.sub(repl, value)


def _resolve_value(value: object, handles: dict[str, ResolvedHandle]) -> object:
    if isinstance(value, str):
        return _resolve_str(value, handles)
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        return {k: _resolve_value(v, handles) for k, v in d.items()}
    if isinstance(value, list):
        items = cast(list[Any], value)
        return [_resolve_value(v, handles) for v in items]
    return value


def resolve_refs(args: dict[str, Any], handles: dict[str, ResolvedHandle]) -> dict[str, Any]:
    """Recursively substitute $handle / $handle.field refs in a step's args tree.

    Raises :class:`ProgramError` on an unknown handle, a missing field, or a
    bare ``$handle`` whose step produced no table.
    """
    return {k: _resolve_value(v, handles) for k, v in args.items()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_program_dsl.py -v`
Expected: 18 PASSED

- [ ] **Step 5: Gates + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/program/dsl.py tests/unit/test_program_dsl.py
git commit -m "feat(program): resolve_refs handle substitution with typed ProgramError (U1)"
```

---

# Phase B — Interpreter

### Task 3: `StepSummary`/`ProgramResult` + happy-path interpreter + max-steps cap

**Files:**
- Create: `src/labrat/agent/program/interpreter.py`
- Test: `tests/unit/test_program_interpreter.py`

**Interfaces:**
- Consumes: `Program`, `ProgramError`, `ResolvedHandle`, `resolve_refs` (Tasks 1–2); `ToolRegistry.dispatch(name: str, args: dict[str, Any], ctx: ToolContext) -> DispatchResult` and `Tool`/`ToolContext` from `labrat.agent.tools.base`.
- Produces: `DEFAULT_MAX_STEPS = 20`; `StepSummary(BaseModel)` with `index: int, tool: str, ok: bool, bind: str, handle_table: str | None = None, rows: int | None = None, rows_failed: int | None = None, error: str | None = None`; `ProgramResult(BaseModel)` with `ok: bool, steps: list[StepSummary] = [], final_bind: str | None = None, final_table: str | None = None, error: str | None = None`; `async def run_program(program: Program, ctx: ToolContext, registry: ToolRegistry, *, max_steps: int = DEFAULT_MAX_STEPS) -> ProgramResult`. Tasks 4–6 rely on these exact names/signatures.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_program_interpreter.py`:

```python
"""Program interpreter: sequential dispatch, handles, stop-on-error, max-steps."""

from __future__ import annotations

from pydantic import BaseModel

from labrat.agent.program.dsl import Program, ProgramStep
from labrat.agent.program.interpreter import (
    DEFAULT_MAX_STEPS,
    ProgramResult,
    StepSummary,
    run_program,
)
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry

# ── stub tools (no DB, no LLM) ──────────────────────────────────────────────


class _EchoInput(BaseModel):
    text: str = ""


class _EchoOutput(BaseModel):
    ok: bool = True
    echoed: str = ""


class _EchoTool(Tool[_EchoInput]):
    """Records every call so tests can assert which steps actually dispatched."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the input text."

    @property
    def input_model(self) -> type[_EchoInput]:
        return _EchoInput

    async def execute(self, ctx: ToolContext, args: _EchoInput) -> _EchoOutput:
        self.calls.append(args.text)
        return _EchoOutput(ok=True, echoed=args.text)


def _echo_registry() -> tuple[ToolRegistry, _EchoTool]:
    registry = ToolRegistry()
    echo = _EchoTool()
    registry.register(echo)
    return registry, echo


def _ctx() -> ToolContext:
    return ToolContext(connections={}, catalogs={})


# ── happy path ──────────────────────────────────────────────────────────────


async def test_two_step_program_runs_sequentially() -> None:
    registry, echo = _echo_registry()
    program = Program(
        steps=[
            ProgramStep(tool="echo", args={"text": "one"}, bind="a"),
            ProgramStep(tool="echo", args={"text": "two"}, bind="b"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert isinstance(result, ProgramResult)
    assert result.ok
    assert echo.calls == ["one", "two"]
    assert [s.bind for s in result.steps] == ["a", "b"]
    assert all(isinstance(s, StepSummary) and s.ok for s in result.steps)
    assert [s.index for s in result.steps] == [0, 1]
    assert result.final_bind == "b"
    assert result.final_table is None  # echo produces no table
    assert result.error is None


async def test_field_ref_passes_scalar_between_steps() -> None:
    registry, echo = _echo_registry()
    program = Program(
        steps=[
            ProgramStep(tool="echo", args={"text": "hello"}, bind="a"),
            ProgramStep(tool="echo", args={"text": "$a.echoed"}, bind="b"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert result.ok
    assert echo.calls == ["hello", "hello"]


# ── max-steps cap ───────────────────────────────────────────────────────────


async def test_default_max_steps_is_twenty() -> None:
    assert DEFAULT_MAX_STEPS == 20


async def test_over_max_steps_is_structured_error_and_nothing_runs() -> None:
    registry, echo = _echo_registry()
    program = Program(
        steps=[
            ProgramStep(tool="echo", args={"text": str(i)}, bind=f"s{i}") for i in range(3)
        ]
    )
    result = await run_program(program, _ctx(), registry, max_steps=2)
    assert not result.ok
    assert result.error is not None
    assert "max_steps" in result.error
    assert result.steps == []  # nothing was run
    assert echo.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_program_interpreter.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'labrat.agent.program.interpreter'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/labrat/agent/program/interpreter.py`:

```python
"""Program interpreter: sequential registry dispatch with handle binding.

Safe by construction — the ONLY thing the interpreter does with a step is
``registry.dispatch(step.tool, resolved_args, ctx)``, so every existing gate
(read-only ``is_mutating``, per-tool caps, input validation) applies per step.
Only the bounded :class:`ProgramResult` summary is ever returned; intermediate
payloads never round-trip through model context.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from labrat.agent.program.dsl import Program, ResolvedHandle, resolve_refs
from labrat.agent.tools.base import ToolContext, ToolRegistry

DEFAULT_MAX_STEPS = 20


class StepSummary(BaseModel):
    """Bounded per-step record — never carries rows or payloads."""

    index: int
    tool: str
    ok: bool
    bind: str
    handle_table: str | None = None
    rows: int | None = None
    rows_failed: int | None = None
    error: str | None = None


class ProgramResult(BaseModel):
    """Bounded program outcome — the only thing that returns to model context."""

    ok: bool
    steps: list[StepSummary] = []
    final_bind: str | None = None
    final_table: str | None = None
    error: str | None = None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _rows_of(dump: dict[str, Any]) -> int | None:
    """Row count from a step output's dump: run_sql row_count / llm_* rows_processed."""
    for key in ("row_count", "rows_processed"):
        v = _int_or_none(dump.get(key))
        if v is not None:
            return v
    return None


async def run_program(
    program: Program,
    ctx: ToolContext,
    registry: ToolRegistry,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> ProgramResult:
    """Run a program's steps sequentially, binding each result to its handle."""
    if len(program.steps) > max_steps:
        return ProgramResult(
            ok=False,
            error=(
                f"program has {len(program.steps)} steps, exceeding max_steps={max_steps}; "
                "nothing was run"
            ),
        )

    handles: dict[str, ResolvedHandle] = {}
    summaries: list[StepSummary] = []
    final_bind: str | None = None

    for index, step in enumerate(program.steps):
        resolved_args = resolve_refs(step.args, handles)
        dispatch = await registry.dispatch(step.tool, resolved_args, ctx)

        dump: dict[str, Any] = {}
        if dispatch.ok and isinstance(dispatch.value, BaseModel):
            dump = dispatch.value.model_dump()

        summaries.append(
            StepSummary(
                index=index,
                tool=step.tool,
                ok=dispatch.ok,
                bind=step.bind,
                rows=_rows_of(dump),
                rows_failed=_int_or_none(dump.get("rows_failed")),
                error=dispatch.error,
            )
        )
        handles[step.bind] = ResolvedHandle(table=None, output=dump)
        final_bind = step.bind

    return ProgramResult(ok=True, steps=summaries, final_bind=final_bind, final_table=None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_program_interpreter.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Gates + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/program/interpreter.py tests/unit/test_program_interpreter.py
git commit -m "feat(program): interpreter happy path + max-steps cap + bounded summaries (U2)"
```

---

### Task 4: Stop-on-error (dispatch failure, tool self-error, ref failure, unknown tool)

**Files:**
- Modify: `src/labrat/agent/program/interpreter.py` (replace `run_program`)
- Test: `tests/unit/test_program_interpreter.py` (append)

**Interfaces:**
- Consumes: `ProgramError` from `labrat.agent.program.dsl`; everything from Task 3.
- Produces: the same `run_program` signature, now with the four failure modes returning partial `ProgramResult(ok=False, error=...)` — a failed step is recorded and later steps are NOT dispatched. Failure modes: (a) `DispatchResult.ok is False` (tool raised / unknown tool / input validation), (b) step output `model_dump()["ok"] is False` (structured self-error like a `run_sql` refusal), (c) `resolve_refs` raised `ProgramError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_program_interpreter.py`:

```python
# ── stop-on-error ───────────────────────────────────────────────────────────


class _BoomInput(BaseModel):
    pass


class _BoomTool(Tool[_BoomInput]):
    """Raises inside execute — dispatch converts it to DispatchResult(ok=False)."""

    @property
    def name(self) -> str:
        return "boom"

    @property
    def description(self) -> str:
        return "Always raises."

    @property
    def input_model(self) -> type[_BoomInput]:
        return _BoomInput

    async def execute(self, ctx: ToolContext, args: _BoomInput) -> object:
        raise RuntimeError("boom exploded")


class _SoftFailOutput(BaseModel):
    ok: bool = False
    error: str | None = "soft failure: refused"


class _SoftFailTool(Tool[_BoomInput]):
    """Returns a structured ok=False output — dispatch itself SUCCEEDS."""

    @property
    def name(self) -> str:
        return "soft_fail"

    @property
    def description(self) -> str:
        return "Always returns ok=False without raising."

    @property
    def input_model(self) -> type[_BoomInput]:
        return _BoomInput

    async def execute(self, ctx: ToolContext, args: _BoomInput) -> _SoftFailOutput:
        return _SoftFailOutput()


async def test_raising_middle_step_stops_program() -> None:
    registry, echo = _echo_registry()
    registry.register(_BoomTool())
    program = Program(
        steps=[
            ProgramStep(tool="echo", args={"text": "one"}, bind="a"),
            ProgramStep(tool="boom", args={}, bind="b"),
            ProgramStep(tool="echo", args={"text": "never"}, bind="c"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert not result.ok
    assert echo.calls == ["one"]  # step 3 was NOT dispatched
    assert len(result.steps) == 2  # partial summary incl. the failing step
    assert result.steps[1].ok is False
    assert result.steps[1].error is not None
    assert "boom exploded" in result.steps[1].error
    assert result.error is not None
    assert "step 1" in result.error
    assert result.final_bind == "a"  # last OK step


async def test_soft_fail_output_stops_program() -> None:
    registry, echo = _echo_registry()
    registry.register(_SoftFailTool())
    program = Program(
        steps=[
            ProgramStep(tool="soft_fail", args={}, bind="a"),
            ProgramStep(tool="echo", args={"text": "never"}, bind="b"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert not result.ok
    assert echo.calls == []
    assert result.steps[0].ok is False
    assert result.steps[0].error is not None
    assert "soft failure" in result.steps[0].error


async def test_unknown_tool_stops_program() -> None:
    registry, echo = _echo_registry()
    program = Program(
        steps=[
            ProgramStep(tool="no_such_tool", args={}, bind="a"),
            ProgramStep(tool="echo", args={"text": "never"}, bind="b"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert not result.ok
    assert echo.calls == []
    assert result.steps[0].error is not None
    assert "Unknown tool" in result.steps[0].error


async def test_bad_ref_stops_program_as_failed_step() -> None:
    registry, echo = _echo_registry()
    program = Program(
        steps=[
            ProgramStep(tool="echo", args={"text": "one"}, bind="a"),
            ProgramStep(tool="echo", args={"text": "$missing.field"}, bind="b"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert not result.ok
    assert echo.calls == ["one"]  # step 2 never dispatched
    assert result.steps[1].ok is False
    assert result.steps[1].error is not None
    assert "unknown handle" in result.steps[1].error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_program_interpreter.py -v`
Expected: the 4 new tests FAIL (`assert not result.ok` fails, or `ProgramError` propagates uncaught in the bad-ref test)

- [ ] **Step 3: Write the implementation**

In `src/labrat/agent/program/interpreter.py`, add `ProgramError` to the dsl import:

```python
from labrat.agent.program.dsl import Program, ProgramError, ResolvedHandle, resolve_refs
```

and replace the whole `run_program` function with:

```python
async def run_program(
    program: Program,
    ctx: ToolContext,
    registry: ToolRegistry,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> ProgramResult:
    """Run a program's steps sequentially, binding each result to its handle.

    Stop-on-error: the first failing step (dispatch failure, structured
    ``ok=False`` tool output, or a bad $ref) is recorded and later steps are
    never dispatched.
    """
    if len(program.steps) > max_steps:
        return ProgramResult(
            ok=False,
            error=(
                f"program has {len(program.steps)} steps, exceeding max_steps={max_steps}; "
                "nothing was run"
            ),
        )

    handles: dict[str, ResolvedHandle] = {}
    summaries: list[StepSummary] = []
    final_bind: str | None = None

    for index, step in enumerate(program.steps):
        try:
            resolved_args = resolve_refs(step.args, handles)
        except ProgramError as exc:
            summaries.append(
                StepSummary(index=index, tool=step.tool, ok=False, bind=step.bind, error=str(exc))
            )
            return ProgramResult(
                ok=False,
                steps=summaries,
                final_bind=final_bind,
                error=f"step {index} ({step.tool}): {exc}",
            )

        dispatch = await registry.dispatch(step.tool, resolved_args, ctx)

        step_ok = dispatch.ok
        error = dispatch.error
        dump: dict[str, Any] = {}
        if dispatch.ok and isinstance(dispatch.value, BaseModel):
            dump = dispatch.value.model_dump()
            if dump.get("ok") is False:
                # Tools like run_sql/llm_extract report failure as a structured
                # ok=False output without raising — still a failed step.
                step_ok = False
                err_val = dump.get("error")
                error = err_val if isinstance(err_val, str) else "tool reported ok=False"

        summaries.append(
            StepSummary(
                index=index,
                tool=step.tool,
                ok=step_ok,
                bind=step.bind,
                rows=_rows_of(dump),
                rows_failed=_int_or_none(dump.get("rows_failed")),
                error=error,
            )
        )

        if not step_ok:
            return ProgramResult(
                ok=False,
                steps=summaries,
                final_bind=final_bind,
                error=f"step {index} ({step.tool}) failed: {error}",
            )

        handles[step.bind] = ResolvedHandle(table=None, output=dump)
        final_bind = step.bind

    return ProgramResult(ok=True, steps=summaries, final_bind=final_bind, final_table=None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_program_interpreter.py -v`
Expected: 8 PASSED

- [ ] **Step 5: Gates + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/program/interpreter.py tests/unit/test_program_interpreter.py
git commit -m "feat(program): stop-on-error with partial summaries + failing step index (U2)"
```

---

### Task 5: Table materialization — `program_<bind>` handles

**Files:**
- Modify: `src/labrat/agent/program/interpreter.py` (replace `run_program`; add imports)
- Test: `tests/unit/test_program_interpreter.py` (append)

**Interfaces:**
- Consumes: `LedgerPayloadProvider` protocol (`ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None`) from `labrat.agent.tools.serialization`; `DuckDBConnection.materialize_table(table_name: str, arrow_table: object) -> None` from `labrat.db.duckdb_engine`; `RunSqlTool` (real) for the fixture test.
- Produces: final `run_program` behavior — an ok step whose value is a `LedgerPayloadProvider` returning `("table", pl.DataFrame)` gets materialized as TEMP table `program_<bind>` on the primary connection; `ResolvedHandle.table` / `StepSummary.handle_table` / `ProgramResult.final_table` carry that name; a table-producing step on a non-DuckDB primary is a structured step failure. Tasks 6/8 rely on `final_table == f"program_{final_bind}"`.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_program_interpreter.py`, first replace the file's entire import header (the docstring stays; everything from `from __future__ ...` down to the `# ── stub tools` comment) with:

```python
from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl
import pytest
from pydantic import BaseModel, PrivateAttr

import labrat.agent.tools.run_sql as run_sql_mod
from labrat.agent.program.dsl import Program, ProgramStep
from labrat.agent.program.interpreter import (
    DEFAULT_MAX_STEPS,
    ProgramResult,
    StepSummary,
    run_program,
)
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.agent.tools.serialization import LedgerPayloadKind
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.history.log import QueryHistoryLog
```

(Ordering matters for ruff's isort rule: straight imports before `from` imports within each section — `import pytest` before `from pydantic import ...`, and `import labrat...run_sql as run_sql_mod` before the first-party `from` imports.)

Then append at the end of the file:

```python
# ── table materialization ───────────────────────────────────────────────────


class _TableOutput(BaseModel):
    ok: bool = True
    row_count: int = 2

    _df: pl.DataFrame | None = PrivateAttr(default=None)

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        if self._df is not None:
            return ("table", self._df)
        return None

    def attach(self, df: pl.DataFrame) -> None:
        self._df = df


class _TableInput(BaseModel):
    pass


class _TableTool(Tool[_TableInput]):
    """Emits a 2-row table payload via the LedgerPayloadProvider hook."""

    @property
    def name(self) -> str:
        return "make_table"

    @property
    def description(self) -> str:
        return "Emit a fixed 2-row table."

    @property
    def input_model(self) -> type[_TableInput]:
        return _TableInput

    async def execute(self, ctx: ToolContext, args: _TableInput) -> _TableOutput:
        out = _TableOutput()
        out.attach(pl.DataFrame({"id": [1, 2], "v": ["sentinel_cell_a", "sentinel_cell_b"]}))
        return out


def _make_duckdb(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "prog.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE patents (id INTEGER, abstract VARCHAR)")
    raw.execute("INSERT INTO patents VALUES (1, 'about aspirin'), (2, 'about ibuprofen')")
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def test_table_step_materializes_program_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = ToolRegistry()
    registry.register(_TableTool())
    registry.register(RunSqlTool())
    ctx = ToolContext(connection=conn, catalog=None)
    program = Program(
        steps=[
            ProgramStep(tool="make_table", args={}, bind="docs"),
            ProgramStep(tool="run_sql", args={"query": "SELECT v FROM $docs ORDER BY id"}, bind="final"),
        ]
    )
    result = await run_program(program, ctx, registry)
    assert result.ok
    assert result.steps[0].handle_table == "program_docs"
    assert result.steps[0].rows == 2
    # The temp table is real and queryable outside the program.
    df = conn.execute("SELECT COUNT(*) AS n FROM program_docs")
    assert df["n"].to_list() == [2]
    # Step 2's SQL read the substituted temp table and itself materialized.
    assert result.steps[1].ok
    assert result.steps[1].rows == 2
    assert result.final_bind == "final"
    assert result.final_table == "program_final"
    df2 = conn.execute("SELECT v FROM program_final ORDER BY v")
    assert df2["v"].to_list() == ["sentinel_cell_a", "sentinel_cell_b"]
    conn.disconnect()


async def test_program_result_is_bounded_no_cell_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = ToolRegistry()
    registry.register(_TableTool())
    ctx = ToolContext(connection=conn, catalog=None)
    program = Program(steps=[ProgramStep(tool="make_table", args={}, bind="docs")])
    result = await run_program(program, ctx, registry)
    assert result.ok
    dumped = result.model_dump_json()
    assert "sentinel_cell_a" not in dumped  # NEVER embeds row data
    assert "sentinel_cell_b" not in dumped
    conn.disconnect()


async def test_table_step_on_non_duckdb_primary_is_structured_failure() -> None:
    registry = ToolRegistry()
    registry.register(_TableTool())
    ctx = ToolContext(connection=object(), catalog=None)
    program = Program(
        steps=[
            ProgramStep(tool="make_table", args={}, bind="docs"),
            ProgramStep(tool="make_table", args={}, bind="never"),
        ]
    )
    result = await run_program(program, ctx, registry)
    assert not result.ok
    assert len(result.steps) == 1  # stop-on-error
    assert result.steps[0].ok is False
    assert result.steps[0].error is not None
    assert "DuckDB" in result.steps[0].error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_program_interpreter.py -v`
Expected: the 3 new tests FAIL — `handle_table` is `None`, `program_docs` doesn't exist (`CatalogException` from the run_sql step → `result.ok` False), non-DuckDB test's `assert not result.ok` fails

- [ ] **Step 3: Write the implementation**

In `src/labrat/agent/program/interpreter.py`, add the imports:

```python
import polars as pl

from labrat.agent.tools.serialization import LedgerPayloadProvider
from labrat.db.duckdb_engine import DuckDBConnection
```

and replace `run_program` with the final version:

```python
async def run_program(
    program: Program,
    ctx: ToolContext,
    registry: ToolRegistry,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> ProgramResult:
    """Run a program's steps sequentially, binding each result to its handle.

    Stop-on-error: the first failing step (dispatch failure, structured
    ``ok=False`` tool output, a bad $ref, or a table result the primary
    connection cannot materialize) is recorded and later steps never dispatch.
    A step whose output declares a ``("table", df)`` ledger payload is
    materialized as the DuckDB TEMP table ``program_<bind>``.
    """
    if len(program.steps) > max_steps:
        return ProgramResult(
            ok=False,
            error=(
                f"program has {len(program.steps)} steps, exceeding max_steps={max_steps}; "
                "nothing was run"
            ),
        )

    handles: dict[str, ResolvedHandle] = {}
    summaries: list[StepSummary] = []
    final_bind: str | None = None
    final_table: str | None = None

    for index, step in enumerate(program.steps):
        try:
            resolved_args = resolve_refs(step.args, handles)
        except ProgramError as exc:
            summaries.append(
                StepSummary(index=index, tool=step.tool, ok=False, bind=step.bind, error=str(exc))
            )
            return ProgramResult(
                ok=False,
                steps=summaries,
                final_bind=final_bind,
                final_table=final_table,
                error=f"step {index} ({step.tool}): {exc}",
            )

        dispatch = await registry.dispatch(step.tool, resolved_args, ctx)

        step_ok = dispatch.ok
        error = dispatch.error
        dump: dict[str, Any] = {}
        if dispatch.ok and isinstance(dispatch.value, BaseModel):
            dump = dispatch.value.model_dump()
            if dump.get("ok") is False:
                # Tools like run_sql/llm_extract report failure as a structured
                # ok=False output without raising — still a failed step.
                step_ok = False
                err_val = dump.get("error")
                error = err_val if isinstance(err_val, str) else "tool reported ok=False"

        handle_table: str | None = None
        if step_ok and isinstance(dispatch.value, LedgerPayloadProvider):
            payload = dispatch.value.ledger_payload()
            if payload is not None:
                kind, obj = payload
                if kind == "table" and isinstance(obj, pl.DataFrame):
                    conn = ctx.connections.get(ctx.primary)
                    if isinstance(conn, DuckDBConnection):
                        handle_table = f"program_{step.bind}"
                        try:
                            conn.materialize_table(handle_table, obj.to_arrow())  # type: ignore[arg-type]
                        except Exception as exc:
                            step_ok = False
                            error = f"failed to materialize handle ${step.bind}: {exc}"
                            handle_table = None
                    else:
                        step_ok = False
                        error = (
                            f"step produced a table but the primary connection is "
                            f"{type(conn).__name__}, not DuckDB — cannot materialize ${step.bind}"
                        )

        summaries.append(
            StepSummary(
                index=index,
                tool=step.tool,
                ok=step_ok,
                bind=step.bind,
                handle_table=handle_table,
                rows=_rows_of(dump),
                rows_failed=_int_or_none(dump.get("rows_failed")),
                error=error,
            )
        )

        if not step_ok:
            return ProgramResult(
                ok=False,
                steps=summaries,
                final_bind=final_bind,
                final_table=final_table,
                error=f"step {index} ({step.tool}) failed: {error}",
            )

        handles[step.bind] = ResolvedHandle(table=handle_table, output=dump)
        final_bind = step.bind
        final_table = handle_table

    return ProgramResult(ok=True, steps=summaries, final_bind=final_bind, final_table=final_table)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_program_interpreter.py -v`
Expected: 11 PASSED

- [ ] **Step 5: Gates + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/program/interpreter.py tests/unit/test_program_interpreter.py
git commit -m "feat(program): materialize table handles as program_<bind> temp tables (U2)"
```

---

# Phase C — RunProgramTool + registration

### Task 6: `RunProgramTool`, `include_program` flag, registration, `$defs` schema export

**Files:**
- Create: `src/labrat/agent/tools/run_program.py`
- Modify: `src/labrat/agent/data_tools.py` (signature + import + registration)
- Test: `tests/unit/test_run_program_tool.py`

**Interfaces:**
- Consumes: `Program` (Task 1), `run_program`/`ProgramResult`/`DEFAULT_MAX_STEPS` (Tasks 3–5), `build_data_tools_registry` (modified here).
- Produces: `class RunProgramTool(Tool[Program])` with `mutating = True`, `name == "run_program"`, `input_model` returning `Program`, `async def execute(self, ctx: ToolContext, args: Program) -> ProgramResult` (builds `build_data_tools_registry(include_program=False)` via deferred import); `def build_data_tools_registry(include_program: bool = True) -> ToolRegistry`. Tasks 7–8 dispatch `"run_program"` on the default registry.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_program_tool.py`:

```python
"""RunProgramTool: registration, sub-registry-minus-self, e2e dispatch, schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import duckdb
import pytest

import labrat.agent.tools.run_sql as run_sql_mod
from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.program.interpreter import ProgramResult
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.run_program import RunProgramTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.history.log import QueryHistoryLog


def _make_duckdb(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "tool.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE patents (id INTEGER, abstract VARCHAR)")
    raw.execute("INSERT INTO patents VALUES (1, 'about aspirin'), (2, 'about ibuprofen')")
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


def test_run_program_registered_by_default() -> None:
    names = {t.name for t in build_data_tools_registry().tools}
    assert "run_program" in names


def test_sub_registry_excludes_run_program() -> None:
    names = {t.name for t in build_data_tools_registry(include_program=False).tools}
    assert "run_program" not in names
    assert "run_sql" in names  # everything else still there


def test_run_program_is_mutating() -> None:
    assert RunProgramTool.mutating is True


async def test_dispatch_one_step_program_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None)
    result = await registry.dispatch(
        "run_program",
        {
            "steps": [
                {
                    "tool": "run_sql",
                    "args": {"query": "SELECT id, abstract FROM patents"},
                    "bind": "docs",
                }
            ]
        },
        ctx,
    )
    assert result.ok
    out = result.value
    assert isinstance(out, ProgramResult)
    assert out.ok
    assert out.final_bind == "docs"
    assert out.final_table == "program_docs"
    # The final table is queryable by a follow-up run_sql on the SAME registry.
    follow = await registry.dispatch(
        "run_sql", {"query": "SELECT COUNT(*) AS n FROM program_docs"}, ctx
    )
    assert follow.ok
    dump = cast(dict[str, Any], follow.value.model_dump())  # type: ignore[union-attr]
    assert dump["rows"] == [["2"]]
    conn.disconnect()


def test_anthropic_schema_includes_program_step_defs() -> None:
    schema = RunProgramTool().anthropic_schema()
    defs = schema["input_schema"].get("$defs", {})
    assert "ProgramStep" in defs


def test_openai_schema_includes_program_step_defs() -> None:
    schema = RunProgramTool().openai_schema()
    defs = schema["function"]["parameters"].get("$defs", {})
    assert "ProgramStep" in defs
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_run_program_tool.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'labrat.agent.tools.run_program'`

- [ ] **Step 3: Write the implementation**

Create `src/labrat/agent/tools/run_program.py`:

```python
"""run_program tool: execute a multi-step tool pipeline in ONE call.

Program mode (M4 2.2): the model emits one JSON pipeline of registered-tool
steps with handle binding; the interpreter runs them sequentially and
materializes intermediate tables as DuckDB temp tables (``program_<bind>``) —
only the bounded ProgramResult summary returns to model context. Safe by
construction: steps dispatch through the standard registry MINUS run_program
itself (no nested programs), so every existing gate applies per step.
"""

from __future__ import annotations

from typing import Any

from labrat.agent.program.dsl import Program
from labrat.agent.program.interpreter import DEFAULT_MAX_STEPS, ProgramResult, run_program
from labrat.agent.tools.base import Tool, ToolContext


class RunProgramTool(Tool[Program]):
    """Run a bounded pipeline of tool steps with handle-bound intermediates."""

    mutating = True  # materializes temp tables → blocked under read-only Analyst mode

    @property
    def name(self) -> str:
        return "run_program"

    @property
    def description(self) -> str:
        return (
            f"Execute a pipeline of up to {DEFAULT_MAX_STEPS} tool steps in ONE call, e.g. "
            '{"steps": [{"tool": "run_sql", "args": {"query": "SELECT id, abstract FROM t"}, '
            '"bind": "docs"}, {"tool": "llm_extract", "args": {"table": "$docs", ...}, '
            '"bind": "facts"}]}. Later steps reference earlier ones: $handle is that '
            "step's materialized temp table (program_<handle>, usable inside SQL), and "
            "$handle.field is a scalar field of that step's output. Steps run "
            "sequentially and stop on the first error. Intermediate results stay in "
            "temp tables — only a bounded per-step summary returns; query final_table "
            "with run_sql to read the data. Programs cannot call run_program."
        )

    @property
    def input_model(self) -> type[Program]:
        return Program

    # ── schema export: Program nests ProgramStep under $defs, which the base
    # helpers drop (dangling $ref). Pass $defs through — additive per-tool
    # override; base.py is untouched.

    def anthropic_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
                "$defs": schema.get("$defs", {}),
            },
        }

    def openai_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", []),
                    "$defs": schema.get("$defs", {}),
                },
            },
        }

    async def execute(self, ctx: ToolContext, args: Program) -> ProgramResult:
        # Deferred import: data_tools imports this module at top level to
        # register the tool, so a top-level reverse import would be circular.
        # The sub-registry is built at EXECUTE time and EXCLUDES run_program —
        # a step {"tool": "run_program"} is an unknown-tool error (no nested
        # programs / recursion by construction).
        from labrat.agent.data_tools import build_data_tools_registry

        registry = build_data_tools_registry(include_program=False)
        return await run_program(args, ctx, registry, max_steps=DEFAULT_MAX_STEPS)
```

Modify `src/labrat/agent/data_tools.py` — add the import directly ABOVE the existing `RunSqlTool` import (`run_program` sorts before `run_sql`):

```python
from labrat.agent.tools.run_program import RunProgramTool
from labrat.agent.tools.run_sql import RunSqlTool
```

change the signature and docstring:

```python
def build_data_tools_registry(include_program: bool = True) -> ToolRegistry:
    """Return a registry with the standard read-only data-access tools.

    Tools included: search_reference_docs, workflow, profile_dataset, list_tables,
    describe_table, search_columns, link_schema, sample_rows, column_stats,
    run_sql, explain_sql, explain_lineage, verify_join, attach_database, load_file,
    load_mongo_collection, llm_extract, llm_classify, run_program.

    llm_extract / llm_classify are per-row LLM primitives: they self-error with a
    structured result whenever ``ctx.llm_fn`` is None (every path except the
    labrat-agent runner, which injects it) — so registering them here adds no LLM
    dependency to deterministic consumers.

    ``include_program`` (default True) registers the run_program pipeline tool.
    RunProgramTool builds its own step-dispatch registry with
    ``include_program=False`` at execute time, so a program can never dispatch
    run_program (no nested programs / recursion by construction). Constructing
    the tool here builds NO registry — no construction recursion.

    Excluded by design: draft_sql / create_chart (TUI callbacks),
    run_validations / recall_memories / search_query_history (profile-keyed,
    TUI-specific).
    """
```

and at the end of the function body, just before `return registry`:

```python
    if include_program:
        registry.register(RunProgramTool())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_run_program_tool.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Run neighbors to catch registry regressions**

Run: `uv run pytest tests/unit/test_llm_tools_registration.py tests/unit/test_tool_registry.py tests/unit/test_program_interpreter.py -q`
Expected: all PASS (the flag defaults True; existing callers unchanged)

- [ ] **Step 6: Gates + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/run_program.py src/labrat/agent/data_tools.py tests/unit/test_run_program_tool.py
git commit -m "feat(tools): RunProgramTool + sub-registry-minus-self registration (U3)"
```

---

# Phase D — Safety wiring, composition, regression, docs

### Task 7: Safety wiring — recursion guard, read-only gate, dispatch-time validation

**Files:**
- Test: `tests/unit/test_run_program_safety.py` (test-only task — the behaviors were built in Tasks 1–6 by construction; this task pins them against regression)

**Interfaces:**
- Consumes: `build_data_tools_registry` (Task 6), `ProgramResult` (Task 3), `ToolContext(read_only=True)` + `DispatchResult` from `labrat.agent.tools.base`.
- Produces: regression tests only; no new runtime symbols.

- [ ] **Step 1: Write the tests**

Create `tests/unit/test_run_program_safety.py`:

```python
"""run_program safety wiring: recursion guard, read-only gate, dispatch validation."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

import labrat.agent.tools.run_sql as run_sql_mod
from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.program.interpreter import ProgramResult
from labrat.agent.tools.base import ToolContext
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.history.log import QueryHistoryLog


def _make_duckdb(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "safety.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE t (id INTEGER)")
    raw.execute("INSERT INTO t VALUES (1)")
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def test_nested_run_program_step_is_unknown_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recursion guard: run_program is NOT in its own sub-registry."""
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None)
    result = await registry.dispatch(
        "run_program",
        {
            "steps": [
                {
                    "tool": "run_program",
                    "args": {
                        "steps": [
                            {"tool": "run_sql", "args": {"query": "SELECT 1"}, "bind": "x"}
                        ]
                    },
                    "bind": "inner",
                }
            ]
        },
        ctx,
    )
    # The OUTER dispatch succeeds (the tool ran); the inner step fails cleanly.
    assert result.ok
    out = result.value
    assert isinstance(out, ProgramResult)
    assert not out.ok
    assert out.steps[0].ok is False
    assert out.steps[0].error is not None
    assert "Unknown tool" in out.steps[0].error
    conn.disconnect()


async def test_read_only_ctx_blocks_run_program_at_dispatch(tmp_path: Path) -> None:
    """mutating=True composes with the M3 read-only Analyst-mode gate."""
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None, read_only=True)
    result = await registry.dispatch(
        "run_program",
        {"steps": [{"tool": "list_tables", "args": {}, "bind": "t"}]},
        ctx,
    )
    assert not result.ok
    assert result.error == "blocked: read-only Analyst mode"
    conn.disconnect()


async def test_duplicate_bind_rejected_at_dispatch(tmp_path: Path) -> None:
    """Model validators fire during registry input validation — DispatchResult, not a crash."""
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None)
    result = await registry.dispatch(
        "run_program",
        {
            "steps": [
                {"tool": "run_sql", "args": {"query": "SELECT 1"}, "bind": "x"},
                {"tool": "run_sql", "args": {"query": "SELECT 2"}, "bind": "x"},
            ]
        },
        ctx,
    )
    assert not result.ok
    assert result.error is not None
    assert "duplicate bind" in result.error
    conn.disconnect()


async def test_unsafe_bind_rejected_at_dispatch(tmp_path: Path) -> None:
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None)
    result = await registry.dispatch(
        "run_program",
        {"steps": [{"tool": "run_sql", "args": {"query": "SELECT 1"}, "bind": "x; drop"}]},
        ctx,
    )
    assert not result.ok
    assert result.error is not None
    assert "alphanumeric" in result.error
    conn.disconnect()
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/unit/test_run_program_safety.py -v`
Expected: 4 PASSED (these behaviors exist by construction from Tasks 1–6). If any FAILS, that is a real bug in an earlier task — fix the implementation, not the test, then re-run the earlier task's tests too.

- [ ] **Step 3: Gates + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add tests/unit/test_run_program_safety.py
git commit -m "test(program): safety wiring — recursion guard, read-only gate, dispatch validation (U4)"
```

---

### Task 8: End-to-end composition test (the M4 payoff)

**Files:**
- Test: `tests/unit/test_program_composition.py` (test-only task; exercises the spec's marquee flow: run_sql → llm_extract[stub] → run_sql-join in ONE dispatch)

**Interfaces:**
- Consumes: everything shipped in Tasks 1–6; `llm_extract` (existing tool) with a stubbed `ctx.llm_fn`.
- Produces: regression tests only.

- [ ] **Step 1: Write the test**

Create `tests/unit/test_program_composition.py`:

```python
"""Program-mode composition: run_sql -> llm_extract(stub) -> run_sql join, ONE call.

The M4 payoff test: three tool calls collapse into one run_program dispatch;
intermediate tables live as program_<bind> temp tables; only the bounded
summary returns — the row payloads NEVER appear in the tool result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest

import labrat.agent.tools.run_sql as run_sql_mod
from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.program.interpreter import ProgramResult
from labrat.agent.tools.base import ToolContext
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.history.log import QueryHistoryLog

_SENTINEL_1 = "sentinel_abstract_alpha"
_SENTINEL_2 = "sentinel_abstract_beta"


def _make_duckdb(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "compose.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE patents (id INTEGER, abstract VARCHAR)")
    raw.execute(
        "INSERT INTO patents VALUES "
        f"(1, '{_SENTINEL_1} mentions aspirin'), (2, '{_SENTINEL_2} mentions ibuprofen')"
    )
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def _fake_llm(prompt: str) -> str:
    if "aspirin" in prompt:
        return json.dumps({"drug": "aspirin"})
    return json.dumps({"drug": "ibuprofen"})


_PROGRAM_ARGS: dict[str, Any] = {
    "steps": [
        {
            "tool": "run_sql",
            "args": {"query": "SELECT id, abstract FROM patents"},
            "bind": "docs",
        },
        {
            "tool": "llm_extract",
            "args": {
                "table": "$docs",
                "text_column": "abstract",
                "json_schema": {"properties": {"drug": {"type": "string"}}},
                "key_columns": ["id"],
            },
            "bind": "facts",
        },
        {
            "tool": "run_sql",
            "args": {
                "query": "SELECT d.id, f.drug FROM $facts f JOIN $docs d USING (id) ORDER BY d.id"
            },
            "bind": "final",
        },
    ]
}


async def test_three_step_composition_single_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)

    result = await registry.dispatch("run_program", _PROGRAM_ARGS, ctx)
    assert result.ok
    out = result.value
    assert isinstance(out, ProgramResult)
    assert out.ok
    assert [s.ok for s in out.steps] == [True, True, True]

    # Handle temp tables exist and were what the join read.
    assert conn.execute("SELECT COUNT(*) AS n FROM program_docs")["n"].to_list() == [2]
    assert conn.execute("SELECT COUNT(*) AS n FROM program_facts")["n"].to_list() == [2]

    # Per-step summaries are populated (rows from row_count / rows_processed).
    assert out.steps[0].handle_table == "program_docs"
    assert out.steps[0].rows == 2
    assert out.steps[1].handle_table == "program_facts"
    assert out.steps[1].rows == 2
    assert out.steps[1].rows_failed == 0
    assert out.final_bind == "final"
    assert out.final_table == "program_final"

    # Bounded: NO intermediate row data in the returned summary.
    dumped = out.model_dump_json()
    assert _SENTINEL_1 not in dumped
    assert _SENTINEL_2 not in dumped
    assert "aspirin" not in dumped

    # The model's follow-up: read the final handle with a plain run_sql.
    follow = await registry.dispatch(
        "run_sql", {"query": "SELECT drug FROM program_final ORDER BY id"}, ctx
    )
    assert follow.ok
    dump = cast(dict[str, Any], follow.value.model_dump())  # type: ignore[union-attr]
    assert dump["rows"] == [["aspirin"], ["ibuprofen"]]
    conn.disconnect()


async def test_composition_without_llm_fn_stops_at_extract_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """llm_extract self-errors (ok=False) without llm_fn — the program stops there."""
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None)  # llm_fn defaults None

    result = await registry.dispatch("run_program", _PROGRAM_ARGS, ctx)
    assert result.ok
    out = result.value
    assert isinstance(out, ProgramResult)
    assert not out.ok
    assert len(out.steps) == 2  # step 3 never dispatched
    assert out.steps[1].ok is False
    assert out.steps[1].error is not None
    assert "LLM-enabled context" in out.steps[1].error
    assert out.final_bind == "docs"  # last OK step
    conn.disconnect()
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/unit/test_program_composition.py -v`
Expected: 2 PASSED. If the first fails on the join step, debug with `uv run pytest tests/unit/test_program_composition.py -v -x` and inspect `out.steps` — the most likely cause is a `resolve_refs` or materialization regression from Tasks 2/5 (fix there; earlier tests must stay green).

- [ ] **Step 3: Gates + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add tests/unit/test_program_composition.py
git commit -m "test(program): 3-step composition e2e — one dispatch, bounded summary, joinable handles (U5)"
```

---

### Task 9: Docs (decisions.md, CLAUDE.md) + full regression gate

**Files:**
- Modify: `decisions.md` (append entry at end)
- Modify: `CLAUDE.md` (tool-list sentence in the Agent loop section)

**Interfaces:**
- Consumes: shipped feature (Tasks 1–8).
- Produces: documentation only; no runtime symbols.

- [ ] **Step 1: Append the decisions.md entry**

Append at the end of `decisions.md`:

```markdown
## 2026-07-05 — Program mode: `run_program` tool-pipeline DSL (M4 2.2)

**Decision:** Ship program mode as a restricted tool-pipeline DSL — `run_program`
takes `{"steps": [{tool, args, bind}, ...]}` and the interpreter
(`src/labrat/agent/program/`) dispatches each step through the standard
`ToolRegistry` with the same `ToolContext`. NOT arbitrary code: no eval, no new
sandbox; a program inherits every existing gate per step (read-only
`is_mutating`, per-tool caps, input validation).

**Key mechanics:**
- Handle refs in step args: `$handle` → that step's materialized temp table
  (`program_<handle>`, via `LedgerPayloadProvider` → `materialize_table` on the
  DuckDB primary); `$handle.field` → a scalar from the step output's
  `model_dump()`. Token regex `\$([A-Za-z_]\w*)(?:\.(\w+))?` — `$100`-style SQL
  literals never match. Bad refs raise a typed `ProgramError` → failed step.
- Bounded by construction: max 20 steps (`DEFAULT_MAX_STEPS`); stop-on-error
  with partial summaries + failing step index; only `ProgramResult`
  (per-step `StepSummary`, no row payloads) returns to model context —
  intermediate tables never round-trip. The model reads `final_table`
  (`program_<final_bind>`) with a follow-up `run_sql`.
- A step also fails when its output reports `ok=False` (run_sql refusal /
  llm_extract self-error) even though dispatch succeeded — otherwise later
  `$refs` would read a poisoned handle.
- Recursion guard: `RunProgramTool.execute` builds its sub-registry via
  `build_data_tools_registry(include_program=False)` (deferred import breaks
  the data_tools↔run_program cycle) — a step `{tool: "run_program"}` is an
  unknown-tool error. `mutating=True` → blocked under read-only Analyst mode.
- `RunProgramTool` overrides `anthropic_schema`/`openai_schema` to pass the
  nested `ProgramStep` `$defs` through (the base helpers drop them).

**Why:** extends the Context Ledger from *bounding* tool-result re-entry to
*preventing* it (PromptQL/MinusX/Pi convergent "plan-then-execute" ground).
AgentLoop/product lever, NOT a claude-mcp leaderboard lever. Additive:
new modules + one registry flag; no change to the loop or existing tools.
```

- [ ] **Step 2: Update CLAUDE.md**

In `CLAUDE.md`, in the Agent loop section, apply this edit — old string:

```
`attach_database`, `load_file`, `load_mongo_collection`, `search_reference_docs`, `workflow`.
```

new string:

```
`attach_database`, `load_file`, `load_mongo_collection`, `search_reference_docs`, `workflow`, `run_program`. `run_program` (M4 2.2) executes a JSON pipeline of registered-tool steps in one call (max 20, stop-on-error, `$handle` refs → `program_<bind>` temp tables); only a bounded summary returns — sub-registry excludes `run_program` (no recursion), `mutating=True`.
```

(If the old string does not match exactly, locate the `build_data_tools_registry()` tool-list sentence in CLAUDE.md's "Agent loop" section and append `run_program` plus the one-sentence description there.)

- [ ] **Step 3: Full regression gate**

```bash
cd /Users/ege/repos/labrat
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q
```

Expected: all clean / all tests pass (~700+; the pre-existing env-sensitive `tests/tui/test_app_renders.py` is the only tolerated failure, and only if it also fails on a clean checkout — verify with `git stash && uv run pytest tests/tui/test_app_renders.py -q && git stash pop` before tolerating it).

- [ ] **Step 4: Verify the additive contract**

```bash
git -C /Users/ege/repos/labrat diff master --stat -- src/labrat
```

Expected: only `src/labrat/agent/program/*` (new), `src/labrat/agent/tools/run_program.py` (new), and `src/labrat/agent/data_tools.py` (flag + registration) appear. `loop.py`, `base.py`, `serialization.py`, `duckdb_engine.py`, and every existing tool are untouched.

- [ ] **Step 5: Commit**

```bash
cd /Users/ege/repos/labrat
git add decisions.md CLAUDE.md
git commit -m "docs: program-mode decision entry + CLAUDE.md run_program mention (U5)"
```

---

## Self-review (performed while writing; verify once more at execution end)

1. **Spec coverage:** U1 → Tasks 1–2; U2 → Tasks 3–5; U3 → Task 6; U4 → Task 7 (+ mutating/read-only in Tasks 6–7); U5 → Tasks 8–9. Spec testing bullets: resolve_refs substitution/native-field/errors (Task 2), 3-step fixture program + temp tables + bounded + join (Tasks 5, 8), failing middle step with call-counting stub (Task 4), max-steps nothing-run (Task 3), tool self-error without llm_fn (Task 8), recursion guard + read-only compose (Task 7), regression + additive check (Task 9). Spec's `final_handle` realized as `final_bind` (pinned decision 6).
2. **Placeholder scan:** no TBD/TODO/"similar to Task N"; every code step carries complete code; every run step has an exact command + expectation.
3. **Type consistency:** `resolve_refs(args: dict[str, Any], handles: dict[str, ResolvedHandle]) -> dict[str, Any]`; `run_program(program, ctx, registry, *, max_steps=DEFAULT_MAX_STEPS) -> ProgramResult`; `StepSummary{index, tool, ok, bind, handle_table, rows, rows_failed, error}`; `ProgramResult{ok, steps, final_bind, final_table, error}`; `RunProgramTool.execute(ctx, args: Program) -> ProgramResult`; `build_data_tools_registry(include_program: bool = True)` — identical in every task that names them. `run_sql` args use `query` (not the spec example's `sql`) throughout.
