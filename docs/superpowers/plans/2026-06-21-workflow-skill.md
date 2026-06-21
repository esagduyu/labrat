# Workflow skill + SQL self-repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote LabRat's prescriptive prompt into a tracked, inspectable 9-step data-analysis SOP (a `workflow` tool + prompt), and make SQL repair effective by returning structured, repair-oriented diagnostics from `run_sql`.

**Architecture:** A new `src/labrat/agent/workflow.py` holds the canonical SOP + a `WorkflowState` (per-step status + repair counter + checklist render). A new `workflow` tool records progress per `profile_name`, fail-open (never blocks), registered in `build_data_tools_registry()`. `run_sql`'s error path gains a deterministic error classifier (category + hint + executed SQL). `system_base.md`'s Workflow section becomes the 9-step SOP.

**Tech Stack:** Python 3.12, Pydantic v2, pytest (`asyncio_mode = "auto"`), DuckDB (via the shared `ecommerce_db` conftest fixture).

## Global Constraints

- Branch: `feat/workflow-skill` (already created; the spec is committed there).
- Spec: `docs/superpowers/specs/2026-06-21-workflow-skill-design.md`.
- `from __future__ import annotations` at the top of every new/edited `.py` file.
- Pyright **strict** on all of `src/labrat/` — no Unknown leaks.
- Tool `name`/`description`/`input_model` are `@property` methods.
- Enforcement is **fail-open**: the workflow tool never raises to block flow except on a genuinely invalid step key; nothing gates the agent.
- Full gate after every task, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. All clean/green before the commit step.
- Every commit message ends with these two trailer lines verbatim:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj
  ```
- Run Python via `uv run python` / `uv run pytest`.
- The repair-attempt flag fires when `repair_attempts >= 3` (cap = 3); the render shows the count.
- Tests use the shared `ecommerce_db` fixture from `tests/conftest.py` (do NOT read the gitignored `tests/fixtures/sample_dbs/ecommerce.duckdb`).

---

### Task 1: `agent/workflow.py` — SOP definition + `WorkflowState`

**Files:**
- Create: `src/labrat/agent/workflow.py`
- Test: `tests/unit/test_workflow_state.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `WorkflowStep(BaseModel){key: str, label: str}`
  - `DATA_ANALYSIS_WORKFLOW: list[WorkflowStep]` (9 steps, ordered)
  - `STEP_KEYS: tuple[str, ...]`
  - `WorkflowState(BaseModel)`: `statuses: dict[str,str]`, `notes: dict[str,str]`, `repair_attempts: int`; classmethod `new() -> WorkflowState`; `mark(key, status, note=None)`; `note_repair_failure() -> int`; `render() -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_workflow_state.py
"""Data-analysis SOP state + render (FEATURE_ROADMAP #30)."""

from __future__ import annotations

import pytest

from labrat.agent.workflow import DATA_ANALYSIS_WORKFLOW, STEP_KEYS, WorkflowState


def test_canonical_workflow_has_nine_ordered_steps() -> None:
    keys = [s.key for s in DATA_ANALYSIS_WORKFLOW]
    assert keys == [
        "clarify",
        "consult_scent",
        "ground",
        "plan",
        "query",
        "repair",
        "verify_joins",
        "verify_answer",
        "review",
    ]
    assert STEP_KEYS == tuple(keys)


def test_new_state_all_pending() -> None:
    st = WorkflowState.new()
    assert set(st.statuses) == set(STEP_KEYS)
    assert all(v == "pending" for v in st.statuses.values())
    assert st.repair_attempts == 0


def test_mark_transitions_and_render_is_ordered() -> None:
    st = WorkflowState.new()
    st.mark("clarify", "done")
    st.mark("query", "doing", note="running step 1")
    r = st.render()
    assert r.index("clarify") < r.index("query") < r.index("verify_answer")
    assert "[x] clarify" in r
    assert "[~] query" in r
    assert "running step 1" in r


def test_mark_unknown_step_raises() -> None:
    st = WorkflowState.new()
    with pytest.raises(ValueError):
        st.mark("nonsense", "done")


def test_repair_flag_appears_at_cap() -> None:
    st = WorkflowState.new()
    for _ in range(3):
        st.note_repair_failure()
    assert st.repair_attempts == 3
    assert "failed attempts" in st.render()


def test_repair_no_flag_below_cap() -> None:
    st = WorkflowState.new()
    st.note_repair_failure()
    assert "failed attempts" not in st.render()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_workflow_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'labrat.agent.workflow'`.

- [ ] **Step 3: Write the implementation**

```python
# src/labrat/agent/workflow.py
"""Data-analysis workflow SOP + inspectable run-state (FEATURE_ROADMAP #30).

The procedural half of the article's two-layer skill pattern: a canonical, ordered
senior-analyst SOP the agent walks and tracks. Pure data + rendering — no I/O, no LLM.
"""

from __future__ import annotations

from pydantic import BaseModel

_REPAIR_ATTEMPT_CAP = 3


class WorkflowStep(BaseModel):
    key: str
    label: str


DATA_ANALYSIS_WORKFLOW: list[WorkflowStep] = [
    WorkflowStep(key="clarify", label="Clarify the question (+ decompose multi-part questions)"),
    WorkflowStep(key="consult_scent", label="Consult reference docs (search_reference_docs)"),
    WorkflowStep(
        key="ground",
        label="Ground in the schema (profile_dataset / link_schema / column values)",
    ),
    WorkflowStep(key="plan", label="State a numbered plan"),
    WorkflowStep(key="query", label="Execute one step at a time, reading each result"),
    WorkflowStep(key="repair", label="On a SQL error, use the diagnostics to fix and retry"),
    WorkflowStep(key="verify_joins", label="Verify joins (verify_join) before trusting them"),
    WorkflowStep(key="verify_answer", label="Verify the answer addresses the question"),
    WorkflowStep(key="review", label="(opt-in) adversarial review"),
]

STEP_KEYS: tuple[str, ...] = tuple(s.key for s in DATA_ANALYSIS_WORKFLOW)

_STATUS_GLYPH = {"pending": " ", "doing": "~", "done": "x"}


class WorkflowState(BaseModel):
    statuses: dict[str, str] = {}
    notes: dict[str, str] = {}
    repair_attempts: int = 0

    @classmethod
    def new(cls) -> WorkflowState:
        return cls(statuses={s.key: "pending" for s in DATA_ANALYSIS_WORKFLOW})

    def mark(self, key: str, status: str, note: str | None = None) -> None:
        if key not in STEP_KEYS:
            raise ValueError(f"unknown workflow step: {key!r}")
        self.statuses[key] = status
        if note is not None:
            self.notes[key] = note

    def note_repair_failure(self) -> int:
        self.repair_attempts += 1
        return self.repair_attempts

    def render(self) -> str:
        lines: list[str] = []
        for step in DATA_ANALYSIS_WORKFLOW:
            glyph = _STATUS_GLYPH.get(self.statuses.get(step.key, "pending"), " ")
            line = f"[{glyph}] {step.key} — {step.label}"
            note = self.notes.get(step.key)
            if note:
                line += f"  ({note})"
            if step.key == "repair" and self.repair_attempts >= _REPAIR_ATTEMPT_CAP:
                line += f"  (!) {self.repair_attempts} failed attempts — rethink the approach"
            lines.append(line)
        return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_workflow_state.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/workflow.py tests/unit/test_workflow_state.py
git commit -m "feat(workflow): data-analysis SOP definition + WorkflowState

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 2: `workflow` tool + registry registration

**Files:**
- Create: `src/labrat/agent/tools/workflow.py`
- Modify: `src/labrat/agent/data_tools.py`
- Test: `tests/unit/test_workflow_tool.py`

**Interfaces:**
- Consumes: `labrat.agent.workflow.{STEP_KEYS, WorkflowState}`; `labrat.agent.tools.base.{Tool, ToolContext}`.
- Produces: `WorkflowTool` (name `workflow`); output `_Output(checklist: str, statuses: dict[str,str], repair_attempts: int)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_workflow_tool.py
"""The workflow tracking tool (FEATURE_ROADMAP #30)."""

from __future__ import annotations

import pytest

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.workflow import WorkflowTool


async def test_no_arg_returns_full_checklist() -> None:
    tool = WorkflowTool()
    out = await tool.execute(ToolContext(profile_name="p"), tool.input_model())
    assert "clarify" in out.checklist
    assert "review" in out.checklist
    assert set(out.statuses) >= {"clarify", "query", "verify_joins"}


async def test_marking_a_step_advances_it() -> None:
    tool = WorkflowTool()
    ctx = ToolContext(profile_name="p")
    await tool.execute(ctx, tool.input_model(step="clarify", status="done"))
    out = await tool.execute(ctx, tool.input_model(step="query", status="doing"))
    assert out.statuses["clarify"] == "done"
    assert out.statuses["query"] == "doing"


async def test_unknown_step_raises() -> None:
    tool = WorkflowTool()
    with pytest.raises(ValueError):
        await tool.execute(ToolContext(profile_name="p"), tool.input_model(step="bogus"))


async def test_state_is_isolated_per_profile() -> None:
    tool = WorkflowTool()
    await tool.execute(ToolContext(profile_name="a"), tool.input_model(step="clarify", status="done"))
    out_b = await tool.execute(ToolContext(profile_name="b"), tool.input_model())
    assert out_b.statuses["clarify"] == "pending"  # profile b is independent


async def test_repair_doing_increments_attempts() -> None:
    tool = WorkflowTool()
    ctx = ToolContext(profile_name="p")
    await tool.execute(ctx, tool.input_model(step="repair", status="doing"))
    out = await tool.execute(ctx, tool.input_model(step="repair", status="doing"))
    assert out.repair_attempts == 2


async def test_registered_in_data_tools_registry() -> None:
    names = {s["name"] for s in build_data_tools_registry().to_anthropic_schemas()}
    assert "workflow" in names
```

> Note: `test_registered_in_data_tools_registry` fails until Step 4 (the registry edit). The other five pass after Step 3. Both edits land in this task, so the task's final commit is fully green.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_workflow_tool.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'labrat.agent.tools.workflow'`.

- [ ] **Step 3: Write the tool**

```python
# src/labrat/agent/tools/workflow.py
"""workflow tool: track progress through the data-analysis SOP (FEATURE_ROADMAP #30).

Record-and-inspect, fail-open: the agent marks each SOP step as it walks them; the tool
returns the rendered checklist so progress is visible. It never blocks the agent.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.agent.workflow import STEP_KEYS, WorkflowState


class _Input(BaseModel):
    step: str | None = Field(
        default=None,
        description=(
            "Workflow step key to update; one of: " + ", ".join(STEP_KEYS) + ". "
            "Omit to just return the current checklist."
        ),
    )
    status: str = Field(
        default="done",
        description="New status for the step: 'doing' (started) or 'done' (finished).",
    )
    note: str | None = Field(default=None, description="Optional short note recorded on the step.")


class _Output(BaseModel):
    checklist: str
    statuses: dict[str, str]
    repair_attempts: int


class WorkflowTool(Tool[_Input]):
    """Track and inspect progress through the data-analysis workflow (fail-open)."""

    def __init__(self) -> None:
        self._states: dict[str, WorkflowState] = {}

    @property
    def name(self) -> str:
        return "workflow"

    @property
    def description(self) -> str:
        return (
            "Track your progress through the data-analysis workflow. Call with a `step` key plus "
            "`status` ('doing' when you start a step, 'done' when you finish) to record progress; "
            "call with no arguments to see the current checklist. Walk the steps in order. Returns "
            "the rendered checklist. Advisory — it never blocks."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        state = self._states.setdefault(ctx.profile_name, WorkflowState.new())
        if args.step is not None:
            if args.step not in STEP_KEYS:
                raise ValueError(
                    f"unknown workflow step {args.step!r}; valid: {', '.join(STEP_KEYS)}"
                )
            if args.step == "repair" and args.status == "doing":
                state.note_repair_failure()
            state.mark(args.step, args.status, note=args.note)
        return _Output(
            checklist=state.render(),
            statuses=dict(state.statuses),
            repair_attempts=state.repair_attempts,
        )
```

- [ ] **Step 4: Register the tool in `data_tools.py`**

Add the import next to the other tool imports:

```python
from labrat.agent.tools.workflow import WorkflowTool
```

In `build_data_tools_registry()`, register it immediately after `SearchReferenceDocsTool()` (both are cross-cutting guidance tools):

```python
    registry.register(SearchReferenceDocsTool())
    registry.register(WorkflowTool())
```

Add `workflow` to the docstring's tool list.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_workflow_tool.py -q`
Expected: PASS (6 tests, including `registered`).

- [ ] **Step 6: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 7: Commit**

```bash
git add src/labrat/agent/tools/workflow.py src/labrat/agent/data_tools.py tests/unit/test_workflow_tool.py
git commit -m "feat(workflow): workflow tracking tool + register in data tools

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 3: `run_sql` repair-oriented error diagnostics

**Files:**
- Modify: `src/labrat/agent/tools/run_sql.py`
- Test: `tests/unit/test_run_sql_repair.py`

**Interfaces:**
- Consumes: existing `run_sql` (`RunSqlTool`, `_Output`).
- Produces: `_classify_sql_error(message: str) -> tuple[str, str]` (category, hint); `_Output` gains `error_category: str | None = None`, `executed_sql: str | None = None`, `hint: str | None = None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_run_sql_repair.py
"""run_sql repair-oriented error diagnostics (FEATURE_ROADMAP #30)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from labrat.agent.tools import run_sql as run_sql_mod
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.run_sql import RunSqlTool, _classify_sql_error
from labrat.db.duckdb_engine import DuckDBConnection


def test_classify_missing_column() -> None:
    cat, hint = _classify_sql_error('Binder Error: Referenced column "foo" not found in FROM clause')
    assert cat == "missing_column"
    assert hint


def test_classify_unknown_table() -> None:
    cat, hint = _classify_sql_error('Catalog Error: Table with name "bar" does not exist')
    assert cat == "unknown_table"
    assert hint


def test_classify_syntax() -> None:
    cat, _ = _classify_sql_error("Parser Error: syntax error at or near SELEC")
    assert cat == "syntax"


def test_classify_type_mismatch() -> None:
    cat, _ = _classify_sql_error("Conversion Error: Could not convert string to INTEGER")
    assert cat == "type_mismatch"


def test_classify_other_fallback() -> None:
    cat, hint = _classify_sql_error("some unexpected backend failure")
    assert cat == "other"
    assert hint


@pytest.fixture()
def ctx(ecommerce_db: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ToolContext]:
    monkeypatch.setattr(run_sql_mod._history_log, "append", lambda event: None)  # no log side effects
    conn = DuckDBConnection(ecommerce_db, read_only=True)
    conn.connect()
    yield ToolContext(connection=conn, catalog=conn.introspect_catalog())
    conn.disconnect()


async def test_bad_query_returns_diagnostics(ctx: ToolContext) -> None:
    tool = RunSqlTool()
    out = await tool.execute(ctx, tool.input_model(query="SELECT nonexistent_col FROM customers"))
    assert out.ok is False
    assert out.error_category == "missing_column"
    assert out.hint
    assert out.executed_sql and "customers" in out.executed_sql


async def test_valid_query_has_no_diagnostics(ctx: ToolContext) -> None:
    tool = RunSqlTool()
    out = await tool.execute(ctx, tool.input_model(query="SELECT customer_id FROM customers"))
    assert out.ok is True
    assert out.error_category is None
    assert out.hint is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_run_sql_repair.py -q`
Expected: FAIL — `cannot import name '_classify_sql_error'`.

- [ ] **Step 3: Edit `src/labrat/agent/tools/run_sql.py`**

Add the classifier as a module-level function (e.g. just below `_apply_limit`):

```python
def _classify_sql_error(message: str) -> tuple[str, str]:
    """Classify a DB exception message into (category, remediation hint).

    Deterministic substring matching — dialect-agnostic enough for the DuckDB primary;
    the categories generalize. Order matters: the column check (which requires "column")
    runs before the table check so "column ... does not exist" is not mis-tagged.
    """
    m = message.lower()
    if "column" in m and (
        "not found" in m or "does not have a column" in m or "does not exist" in m
    ):
        return (
            "missing_column",
            "Column not found — call describe_table / search_columns to confirm the column name.",
        )
    if ("table" in m or "catalog" in m) and ("does not exist" in m or "not found" in m):
        return (
            "unknown_table",
            "Table not found — call list_tables / profile_dataset to confirm the table name.",
        )
    if "parser error" in m or "syntax error" in m:
        return ("syntax", "Syntax error — re-check the SQL against the active dialect.")
    if "conversion" in m or "cast" in m or "type mismatch" in m or "no function matches" in m:
        return (
            "type_mismatch",
            "Type mismatch — check column types with describe_table and cast explicitly.",
        )
    return (
        "other",
        "Inspect the error; verify table/column names and types before retrying.",
    )
```

Add the three fields to `_Output` (after `error`):

```python
class _Output(BaseModel):
    ok: bool
    query: str
    columns: list[str] | None = None
    rows: list[list[str]] | None = None
    row_count: int | None = None
    refused: bool = False
    needs_confirmation: bool = False
    error: str | None = None
    error_category: str | None = None
    executed_sql: str | None = None
    hint: str | None = None
```

In `execute`, replace the `except Exception as exc:` block's return so it includes the diagnostics (the `sql` local — the post-auto-limit SQL — is already in scope):

```python
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            category, hint = _classify_sql_error(str(exc))
            _log(
                profile=ctx.profile_name,
                thread_id=thread_id,
                version_id=version_id,
                sql=sql,
                executed=True,
                success=False,
                execution_time_ms=elapsed_ms,
                error_message=str(exc),
            )
            return _Output(
                ok=False,
                query=args.query,
                error=str(exc),
                error_category=category,
                executed_sql=sql,
                hint=hint,
            )
```

Leave the statement-stacking and mutation refusal returns unchanged (those aren't execution errors; their new fields stay `None`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_run_sql_repair.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green (existing `run_sql` tests unaffected — the new fields default to `None`).

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/tools/run_sql.py tests/unit/test_run_sql_repair.py
git commit -m "feat(run_sql): repair-oriented error diagnostics (category + hint + executed SQL)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 4: System prompt — the 9-step SOP

**Files:**
- Modify: `src/labrat/agent/prompts/system_base.md`
- Test: `tests/unit/test_workflow_prompt.py`

**Interfaces:**
- Consumes: nothing (prose).
- Produces: a prompt that names the `workflow` tool, the repair guidance, and the SOP steps.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_workflow_prompt.py
"""system_base.md carries the #30 SOP + workflow instruction."""

from __future__ import annotations

from pathlib import Path


def test_prompt_has_workflow_sop_and_repair_guidance() -> None:
    text = Path("src/labrat/agent/prompts/system_base.md").read_text(encoding="utf-8")
    assert "`workflow`" in text  # the tracking tool is named
    assert "error_category" in text and "hint" in text  # repair guidance
    for word in ("Clarify", "Ground", "Repair", "Verify joins"):
        assert word in text  # representative SOP steps
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_workflow_prompt.py -q`
Expected: FAIL — the prompt has neither the `workflow` mention nor the repair guidance yet.

- [ ] **Step 3: Edit `src/labrat/agent/prompts/system_base.md`**

Replace the entire `## Workflow` section (currently the 5-step list from #26a) with:

```markdown
## Workflow

For anything beyond a trivial lookup, walk this senior-analyst loop **in order**, and call the `workflow` tool to mark each step `doing` when you start it and `done` when you finish — so your progress is tracked and inspectable:

1. **Clarify.** Restate the question and your assumptions. If it has multiple distinct parts, decompose it into sub-questions.
2. **Consult reference docs.** Call `search_reference_docs` for curated grounding — metric definitions, join keys, known data-quality gotchas. Treat returned Gotchas as authoritative; proceed if nothing is returned.
3. **Ground.** Call `profile_dataset` for the real schema, row counts, and sample values; use `link_schema` to narrow a wide schema and `search_columns` / `column_stats` to map values in the question to real column values. Never plan against assumed structure.
4. **Plan.** State a short numbered plan; revise as you learn, saying so.
5. **Query.** Execute one step at a time with `run_sql`, reading each result before the next. Prefer pushing aggregation into SQL over fetching broad data into memory.
6. **Repair.** If a query errors, read the returned `error_category` and `hint`, fix the SQL, and retry. After a few failed attempts, stop and rethink rather than retrying blindly.
7. **Verify joins.** Before trusting any join, confirm it with `verify_join` (match-rate + fan-out).
8. **Verify the answer.** Re-read the question and confirm your result answers *that* question — sanity-check magnitudes and units, and that joins didn't drop or fan out rows.
9. **Review (optional).** For a high-stakes answer, do an adversarial review pass before finishing.
```

Add this as the first bullet of the `## Tool Usage` list:

```markdown
- Use `workflow` to track your progress through the steps above (mark each `doing` then `done`); it returns your checklist and never blocks.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_workflow_prompt.py -q`
Expected: PASS.

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green (full suite).

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/prompts/system_base.md tests/unit/test_workflow_prompt.py
git commit -m "feat(workflow): promote system prompt into the 9-step data-analysis SOP

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

## Self-Review

**1. Spec coverage:**
- §3 SOP (9 steps, decomposition + column-value grounding + repair folded in) → Task 1 (`DATA_ANALYSIS_WORKFLOW`) + Task 4 (prompt). ✅
- §4 workflow tracking (WorkflowState, render, per-profile tool, registry) → Tasks 1 + 2. ✅
- §5(a) repair-oriented `run_sql` diagnostics (category + executed_sql + hint, classifier) → Task 3. ✅
- §5(b) bounded anti-thrash counter (increment on repair/doing; flag at cap) → Task 1 (`note_repair_failure`, render flag at `>=3`) + Task 2 (tool increments on `step=repair,status=doing`). ✅
- §6 prompt changes → Task 4. ✅
- §7 fail-open + benchmark safety → tool never blocks (only raises on an invalid step key); mechanism + generic diagnostics only. ✅
- §8 testing (state, tool, run_sql repair, registry, prompt) → Tasks 1-4. ✅
- Inspectability via return value + queryable state + existing query-history log (run_sql already logs failures) → no new audit wiring (Task 3 keeps `_log`). ✅

**2. Placeholder scan:** No TBD/TODO/"similar to". Every code step has complete code; every run step has an exact command + expected result.

**3. Type consistency:** `WorkflowState.new()/mark/note_repair_failure/render` and `STEP_KEYS`/`DATA_ANALYSIS_WORKFLOW` identical across Tasks 1→2. `WorkflowTool` `_Output{checklist,statuses,repair_attempts}` consistent Task 2. `_classify_sql_error(message)->tuple[str,str]` and `_Output` new fields (`error_category`/`executed_sql`/`hint`) consistent Task 3. The repair flag threshold is `repair_attempts >= 3` in both the Global Constraints and Task 1's `render`. The Task 2 registry test is authored with the registration in the same task (green at task end).
