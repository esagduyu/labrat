# dispatch_subagent (T1d Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A model-callable `dispatch_subagent` tool that runs a scoped, depth-1, budget-bounded sub-`AgentLoop` (seeded with sub-task + artifact previews + Scent, never parent history) and returns through the parent's Context Ledger.

**Architecture:** `SubagentRunner` protocol + `ToolContext.subagent_runner` seam (the `llm_fn` precedent); `build_agent_session` installs a closure over the parent's provider/registry/ledger that derives the sub-registry from the HOSTING registry minus the tool, builds a shared-substrate sub-ctx with `subagent_runner=None`, splices artifact previews into the seed, and runs a fresh `AgentLoop` reusing the parent ledger. The tool itself only composes the seed and returns a typed `_Output` with `ledger_payload()` so the parent ledger bounds it uniformly.

**Tech Stack:** Python 3.12, Pydantic v2, pytest (`asyncio_mode="auto"`), ruff, pyright strict (`src/labrat/agent/` strict).

**Spec:** `docs/superpowers/specs/2026-07-09-dispatch-subagent-design.md` — read before starting. Baseline rationale: the 2026-06-26 spec on branch `feat/context-ledger` (adopted; do not re-litigate).

## Global Constraints

- Branch: `feat/dispatch-subagent` off master.
- Depth-1 via TWO structural guards: sub-registry excludes `dispatch_subagent` AND sub-ctx has `subagent_runner=None`; both test-asserted.
- Scoped seed: parent history provably absent from every sub-loop provider call (captured-messages test).
- Benchmark isolation: no `eval/`/`mcp/` path acquires a runner; the tool self-errors structurally there (never raises).
- Parent budget: one dispatch = one parent tool call (existing dispatch-site semantics).
- Sub-registry derived from the HOSTING registry minus the tool (never a fresh full build).
- Uniform ledger flow: the tool never touches ResultStore directly; `_Output.ledger_payload() -> ("json", …)`.
- Byte-identical unused-path guarantee: `ToolContext` gains ONLY a defaulted kwarg/attr; existing tests pass unmodified.
- Deterministic seed (no clock); runner exceptions → `_Output(ok=False, error=…)`, parent turn survives.
- Budgets: input `max_turns=6` (clamp 1..8), `max_tool_calls=10` (clamp 1..15).
- Pyright strict on `src/labrat/agent/`. Repo gates before every commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- Known local env flake `tests/tui/test_app_renders.py::test_app_renders` (fails on unmodified master, CI-skipped) — never a regression signal; restore `snapshot_report.html` via `git checkout` if regenerated.

---

## File Structure

- Create: `src/labrat/agent/tools/dispatch_subagent.py`.
- Modify: `src/labrat/agent/tools/base.py` (protocol + ctx field), `src/labrat/agent/session.py` (runner closure), `src/labrat/agent/data_tools.py` (register), `CLAUDE.md` (tool counts), `TESTING.md`, `decisions.md`.
- Tests: `tests/unit/test_dispatch_subagent_tool.py`, `tests/unit/test_agent_session_subagent.py`, `tests/unit/test_dispatch_subagent_e2e.py`.

---

### Task 1: Seam (`SubagentRunner` + ctx field) + tool with self-error, clamping, registration

**Files:**
- Modify: `src/labrat/agent/tools/base.py` (two additions), `src/labrat/agent/data_tools.py` (one registration)
- Create: `src/labrat/agent/tools/dispatch_subagent.py`
- Test: `tests/unit/test_dispatch_subagent_tool.py`

**Interfaces:**
- Consumes: `LLMFn` alias location (`base.py:15`), `ToolContext.__init__` kwargs (`base.py:35-59`), `Tool[InputT]` property conventions, `LedgerPayloadProvider` shape (`serialization.py:23`: `ledger_payload() -> tuple[LedgerPayloadKind, object] | None`, kind `"json"` here).
- Produces (Tasks 2–4 rely on): `SubagentRunner` protocol — `async def __call__(self, *, seed_prompt: str, artifact_refs: list[str], max_turns: int, max_tool_calls: int) -> tuple[str, int, int]`; `ToolContext(..., subagent_runner: SubagentRunner | None = None)` + `self.subagent_runner`; `DispatchSubagentTool` with `_Input(sub_task, artifact_refs=[], context_hint=None, max_turns=6, max_tool_calls=10)` (clamped) and `_Output(ok, final_text, turns_used, tool_calls_used, error=None)` with `ledger_payload()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_dispatch_subagent_tool.py
"""dispatch_subagent: self-error without a runner, budget clamps, registration."""

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.dispatch_subagent import DispatchSubagentTool


def _args(**over: object) -> object:
    tool = DispatchSubagentTool()
    base: dict[str, object] = {"sub_task": "count the orders"}
    base.update(over)
    return tool.input_model.model_validate(base)


async def test_self_error_without_runner() -> None:
    tool = DispatchSubagentTool()
    out = await tool.execute(ToolContext(), _args())
    assert out.ok is False
    assert out.final_text == ""
    assert out.error is not None and "no subagent runner" in out.error


def test_budgets_clamped() -> None:
    args = _args(max_turns=99, max_tool_calls=0)
    assert args.max_turns == 8
    assert args.max_tool_calls == 1
    defaults = _args()
    assert defaults.max_turns == 6 and defaults.max_tool_calls == 10


def test_output_declares_json_ledger_payload() -> None:
    from labrat.agent.tools.dispatch_subagent import _Output

    out = _Output(ok=True, final_text="answer", turns_used=1, tool_calls_used=2)
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "json"
    assert isinstance(obj, dict) and obj["final_text"] == "answer"


def test_registered_in_standard_registry() -> None:
    names = {t.name for t in build_data_tools_registry().tools}
    assert "dispatch_subagent" in names


def test_ctx_field_defaults_none() -> None:
    assert ToolContext().subagent_runner is None
```

- [ ] **Step 2: Run to verify FAIL** — `uv run pytest tests/unit/test_dispatch_subagent_tool.py -v` → `ModuleNotFoundError` / `AttributeError: subagent_runner`.

- [ ] **Step 3: Implement**

(a) `src/labrat/agent/tools/base.py` — next to the `LLMFn` alias add:

```python
class SubagentRunner(Protocol):
    """Runs a scoped sub-agent loop; injected by build_agent_session (llm_fn precedent).

    The runner owns the ResultStore, so it resolves ``artifact_refs`` to previews
    and splices them into the seed; the tool never touches the store. Returns
    ``(final_text, turns_used, tool_calls_used)``.
    """

    async def __call__(
        self,
        *,
        seed_prompt: str,
        artifact_refs: list[str],
        max_turns: int,
        max_tool_calls: int,
    ) -> tuple[str, int, int]: ...
```

(`from typing import Protocol` — extend the existing typing import.) In `ToolContext.__init__`, add kwarg `subagent_runner: "SubagentRunner | None" = None` after `llm_fn` and `self.subagent_runner = subagent_runner` after `self.llm_fn = llm_fn`; extend the class docstring's llm_fn paragraph with one sentence ("``subagent_runner`` follows the same pattern for scoped sub-agent dispatch; deterministic contexts leave it None.").

(b) `src/labrat/agent/tools/dispatch_subagent.py`:

```python
"""dispatch_subagent: delegate a scoped sub-task to a fresh, bounded agent loop.

T1d Phase 2 (spec docs/superpowers/specs/2026-07-09-dispatch-subagent-design.md).
The tool composes the seed (sub-task + context hint + Scent) and delegates
execution to ctx.subagent_runner — the build_agent_session-injected closure
that owns provider/registry/ledger. Hosts without an in-process loop (MCP
server, claude-mcp) have no runner: the tool returns a structured self-error,
exactly the llm_extract capability precedent. Depth-1 is structural: the
runner's sub-registry excludes this tool AND the sub-ctx runner is None.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from labrat.agent.tools.base import Tool, ToolContext
from labrat.agent.tools.serialization import LedgerPayloadKind

_MAX_TURNS_CEILING = 8
_MAX_TOOL_CALLS_CEILING = 15


class _Input(BaseModel):
    sub_task: str = Field(description="The self-contained task for the sub-agent.")
    artifact_refs: list[str] = Field(
        default_factory=list,
        description="result:// refs whose previews the sub-agent should receive.",
    )
    context_hint: str | None = Field(
        default=None, description="Optional extra grounding for the sub-agent."
    )
    max_turns: int = Field(default=6, description="Sub-agent turn budget (1-8).")
    max_tool_calls: int = Field(default=10, description="Sub-agent tool-call budget (1-15).")

    @field_validator("max_turns")
    @classmethod
    def _clamp_turns(cls, v: int) -> int:
        return max(1, min(v, _MAX_TURNS_CEILING))

    @field_validator("max_tool_calls")
    @classmethod
    def _clamp_calls(cls, v: int) -> int:
        return max(1, min(v, _MAX_TOOL_CALLS_CEILING))


class _Output(BaseModel):
    ok: bool
    final_text: str
    turns_used: int = 0
    tool_calls_used: int = 0
    error: str | None = None

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("json", self.model_dump())


class DispatchSubagentTool(Tool[_Input]):
    """Delegate a bounded sub-task to a scoped sub-agent; returns by ledger ref."""

    @property
    def name(self) -> str:
        return "dispatch_subagent"

    @property
    def description(self) -> str:
        return (
            "Delegate a self-contained sub-task (an exploration, a side-query, a "
            "verification) to a scoped sub-agent with its own small budget. The "
            "sub-agent sees ONLY the sub_task, optional context_hint, previews of "
            "any artifact_refs you pass, and relevant reference notes — never this "
            "conversation. Use it to keep your own context lean. Unavailable on "
            "hosts without an in-process agent loop."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        runner = ctx.subagent_runner
        if runner is None:
            return _Output(
                ok=False,
                final_text="",
                error=(
                    "dispatch_subagent unavailable: no subagent runner on this host "
                    "(requires an in-process AgentLoop provider)"
                ),
            )
        seed = await _compose_seed(ctx, args)
        try:
            final_text, turns, calls = await runner(
                seed_prompt=seed,
                artifact_refs=list(args.artifact_refs),
                max_turns=args.max_turns,
                max_tool_calls=args.max_tool_calls,
            )
        except Exception as exc:  # sub-loop failure must not kill the parent turn
            return _Output(ok=False, final_text="", error=str(exc))
        return _Output(
            ok=True, final_text=final_text, turns_used=turns, tool_calls_used=calls
        )


async def _compose_seed(ctx: ToolContext, args: _Input) -> str:
    parts: list[str] = ["## Sub-task", args.sub_task.strip()]
    if args.context_hint:
        parts.append(args.context_hint.strip())
    scent = await _scent_notes(ctx, args.sub_task)
    if scent:
        parts.extend(["## Relevant reference notes", scent])
    return "\n\n".join(parts)


async def _scent_notes(ctx: ToolContext, question: str, top_k: int = 3) -> str:
    """Top-k Scent sections for the sub-task via the real retrieval tool (deterministic)."""
    from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool

    try:
        tool = SearchReferenceDocsTool()
        out = await tool.execute(
            ctx, tool.input_model.model_validate({"question": question, "top_k": top_k})
        )
    except Exception:
        return ""  # retrieval must never block dispatch
    lines: list[str] = []
    for doc in out.results:
        for sec in doc.sections:
            lines.append(f"### {doc.domain} — {sec.heading}\n{sec.body}")
    return "\n\n".join(lines)
```

(c) `src/labrat/agent/data_tools.py` — import `DispatchSubagentTool` with the other tool imports and add `registry.register(DispatchSubagentTool())` beside the `llm_extract`/`llm_classify` registrations (they share the self-gating pattern; keep the registration UNCONDITIONAL and outside the `include_program` branch). Update the builder docstring's tool count.

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/unit/test_dispatch_subagent_tool.py -v
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/base.py src/labrat/agent/tools/dispatch_subagent.py src/labrat/agent/data_tools.py tests/unit/test_dispatch_subagent_tool.py
git commit -m "feat(agent): dispatch_subagent tool + SubagentRunner seam (self-gating, clamped budgets)"
```

Note: registering a new tool changes registry-count/name-set expectations — run `uv run pytest tests/unit -k "data_tools or registry" -v` and `tests/tui/test_main_screen_agent_wiring.py` (superset assertion — should still pass since it asserts subset, not equality). If any test pins an exact tool count, update THAT count with a comment; never delete assertions.

---

### Task 2: Seed-composition tests (tool-level, fake runner)

**Files:**
- Test: `tests/unit/test_dispatch_subagent_tool.py` (extend)

**Interfaces:**
- Consumes: Task 1's tool + `SubagentRunner` contract.
- Produces: pinned seed format (sections order; hint inclusion; Scent omission when store empty; artifact_refs passed through untouched).

- [ ] **Step 1: Write the failing tests** (append)

```python
class _CapturingRunner:
    def __init__(self) -> None:
        self.seed: str | None = None
        self.refs: list[str] | None = None
        self.budgets: tuple[int, int] | None = None

    async def __call__(
        self, *, seed_prompt: str, artifact_refs: list[str],
        max_turns: int, max_tool_calls: int,
    ) -> tuple[str, int, int]:
        self.seed = seed_prompt
        self.refs = artifact_refs
        self.budgets = (max_turns, max_tool_calls)
        return ("sub answer", 2, 3)


async def test_seed_sections_and_passthrough(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))  # empty store → no Scent section
    runner = _CapturingRunner()
    ctx = ToolContext(subagent_runner=runner)
    tool = DispatchSubagentTool()
    out = await tool.execute(
        ctx,
        _args(
            sub_task="count the orders",
            context_hint="only completed ones",
            artifact_refs=["result://abc/0001"],
            max_turns=4,
        ),
    )
    assert out.ok is True and out.final_text == "sub answer"
    assert out.turns_used == 2 and out.tool_calls_used == 3
    assert runner.seed is not None
    assert runner.seed.startswith("## Sub-task\n\ncount the orders")
    assert "only completed ones" in runner.seed
    assert "## Relevant reference notes" not in runner.seed  # empty store → omitted
    assert "result://abc/0001" not in runner.seed  # refs go to the runner, not the seed text
    assert runner.refs == ["result://abc/0001"]
    assert runner.budgets == (4, 10)


async def test_seed_includes_scent_when_docs_exist(tmp_path, monkeypatch) -> None:
    from labrat.maze.document import ScentDoc, Section, render_document

    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    scent = tmp_path / "labrat_maze" / "scent"
    scent.mkdir(parents=True)
    doc = ScentDoc(
        domain="orders",
        sections=[Section(heading="Gotchas", body="- exclude test orders", source="verified")],
    )
    (scent / "orders.md").write_text(render_document(doc), encoding="utf-8")
    runner = _CapturingRunner()
    out = await DispatchSubagentTool().execute(
        ToolContext(subagent_runner=runner), _args(sub_task="orders gotchas please")
    )
    assert out.ok is True
    assert "## Relevant reference notes" in (runner.seed or "")
    assert "exclude test orders" in (runner.seed or "")


async def test_runner_exception_fails_open() -> None:
    class _Boom:
        async def __call__(self, **_: object) -> tuple[str, int, int]:
            raise RuntimeError("provider melted")

    out = await DispatchSubagentTool().execute(
        ToolContext(subagent_runner=_Boom()), _args()
    )
    assert out.ok is False and "provider melted" in (out.error or "")
```

(Seed-format note: `"\n\n".join(["## Sub-task", task, …])` renders `## Sub-task\n\ncount the orders` — the startswith assertion pins that exact shape.)

- [ ] **Step 2: Run to verify FAIL** — the three new tests fail (`AttributeError`/assertions) until Task 1's implementation is present; if Task 1 is already complete they should PASS — in that case this task is pure test-hardening: verify each assertion actually exercises the intended branch by temporarily breaking it (e.g. comment out the hint append) and watching the test fail, then restore. Record which method you used in the report.

- [ ] **Step 3: Run tests, gates, commit**

```bash
uv run pytest tests/unit/test_dispatch_subagent_tool.py -v
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add tests/unit/test_dispatch_subagent_tool.py
git commit -m "test(agent): dispatch_subagent seed composition + fail-open pinning"
```

---

### Task 3: `build_agent_session` runner closure

**Files:**
- Modify: `src/labrat/agent/session.py`
- Test: `tests/unit/test_agent_session_subagent.py` (create)

**Interfaces:**
- Consumes: Task 1's `SubagentRunner`/ctx field; `AgentLoop(*, provider, registry, ctx, system, dialect, max_turns, max_tool_calls, ledger)` (`loop.py:59`); `ContextLedger.store` property (`context_ledger.py:51`); `ResultStore.preview(ref, *, max_rows=50, max_bytes=8000)` (`results/store.py:129`); `ToolRegistry.register`/`.tools`.
- Produces: after `build_agent_session` returns, `ctx.subagent_runner` is installed (caller injection wins); the closure contract of Task 1's protocol.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_agent_session_subagent.py
"""build_agent_session installs the subagent runner (scoped, guarded, ledger-shared)."""

from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.session import PINNED_DEFAULT_MODEL, build_agent_session
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry
from labrat.agent.tools.dispatch_subagent import DispatchSubagentTool


def _session(ctx: ToolContext, registry: ToolRegistry):
    return build_agent_session(
        ctx=ctx,
        registry=registry,
        provider=AnthropicProvider(model=PINNED_DEFAULT_MODEL),
        system_prompt="s",
        enable_ledger=True,
    )


def test_runner_installed_and_caller_wins() -> None:
    ctx = ToolContext()
    _session(ctx, ToolRegistry())
    assert ctx.subagent_runner is not None

    async def mine(**_: object) -> tuple[str, int, int]:
        return ("", 0, 0)

    ctx2 = ToolContext(subagent_runner=mine)
    _session(ctx2, ToolRegistry())
    assert ctx2.subagent_runner is mine  # caller injection wins (llm_fn precedent)


def test_sub_registry_derived_from_hosting_registry() -> None:
    from labrat.agent.session import _sub_registry
    from labrat.agent.tools.run_sql import RunSqlTool

    hosting = ToolRegistry()
    hosting.register(RunSqlTool())
    hosting.register(DispatchSubagentTool())
    sub = _sub_registry(hosting)
    names = {t.name for t in sub.tools}
    assert names == {"run_sql"}  # subset of the HOST, minus the dispatch tool


def test_sub_ctx_shares_substrate_and_is_guarded() -> None:
    from labrat.agent.session import _sub_ctx

    conn, cat = object(), object()
    parent = ToolContext(
        connections={"main": conn}, catalogs={"main": cat}, primary="main",
        profile_name="p1", read_only=True,
    )

    async def fake_llm(prompt: str) -> str:
        return "x"

    parent.llm_fn = fake_llm
    sub = _sub_ctx(parent)
    assert sub.connections["main"] is conn and sub.catalogs["main"] is cat
    assert sub.primary == "main" and sub.profile_name == "p1"
    assert sub.read_only is True and sub.llm_fn is fake_llm
    assert sub.subagent_runner is None  # depth-1 guard #2
```

- [ ] **Step 2: Run to verify FAIL** — `ImportError: _sub_registry` etc.

- [ ] **Step 3: Implement** (in `src/labrat/agent/session.py`)

Module-level helpers (below `build_agent_session`):

```python
def _sub_registry(hosting: ToolRegistry) -> ToolRegistry:
    """The hosting registry minus dispatch_subagent — depth-1 guard #1.

    Derived from the HOST (never rebuilt from the standard set) so restricted
    hosts cannot be confused-deputied into wider tool access (retires the M4
    review's I1 advisory for this consumer).
    """
    sub = ToolRegistry()
    for tool in hosting.tools:
        if tool.name != "dispatch_subagent":
            sub.register(tool)
    return sub


def _sub_ctx(parent: ToolContext) -> ToolContext:
    """Shared execution substrate, fresh guard: subagent_runner stays None."""
    return ToolContext(
        connections=parent.connections,
        catalogs=parent.catalogs,
        primary=parent.primary,
        profile_name=parent.profile_name,
        read_only=parent.read_only,
        llm_fn=parent.llm_fn,
        subagent_runner=None,
    )
```

Inside `build_agent_session`, AFTER the `AgentLoop` is constructed (the loop variable exists) and BEFORE `return`, install the runner (caller wins):

```python
    if ctx.subagent_runner is None:
        parent_registry = registry
        parent_ledger = ledger  # may be None (enable_ledger=False)

        async def _run_subagent(
            *,
            seed_prompt: str,
            artifact_refs: list[str],
            max_turns: int,
            max_tool_calls: int,
        ) -> tuple[str, int, int]:
            seed = seed_prompt
            if artifact_refs and parent_ledger is not None:
                previews: list[str] = []
                for ref in artifact_refs:
                    try:
                        previews.append(f"### {ref}\n{parent_ledger.store.preview(ref)}")
                    except Exception:
                        previews.append(f"[unresolvable ref: {ref}]")
                seed = seed + "\n\n## Provided artifacts\n\n" + "\n\n".join(previews)
            elif artifact_refs:
                seed = seed + "\n\n## Provided artifacts\n\n" + "\n\n".join(
                    f"[unresolvable ref: {ref}]" for ref in artifact_refs
                )
            sub_loop = AgentLoop(
                provider=provider,
                registry=_sub_registry(parent_registry),
                ctx=_sub_ctx(ctx),
                system=system_prompt,
                dialect=dialect,
                max_turns=max_turns,
                max_tool_calls=max_tool_calls,
                ledger=parent_ledger,
            )
            chunks: list[str] = []
            await sub_loop.run(seed, on_text=chunks.append)
            return ("".join(chunks), sub_loop.turns_used, sub_loop.tool_calls_used)

        ctx.subagent_runner = _run_subagent
```

(Seed-splice ordering: artifacts append AFTER the tool-composed sections — acceptable divergence from the spec's illustrative 1-2-3 ordering; the spec's binding property is content, not position. If pyright flags the closure assignment against the Protocol, annotate `_run_subagent` explicitly or `cast(SubagentRunner, _run_subagent)` — prefer matching the protocol signature exactly so no cast is needed.)

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/unit/test_agent_session_subagent.py tests/unit/test_agent_session.py -v
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/session.py tests/unit/test_agent_session_subagent.py
git commit -m "feat(agent): build_agent_session installs the scoped subagent runner"
```

Existing `test_agent_session.py` must pass unchanged (the injection only adds a ctx attribute).

---

### Task 4: E2E — scoped seed, budgets, ledgered return

**Files:**
- Test: `tests/unit/test_dispatch_subagent_e2e.py` (create)

**Interfaces:**
- Consumes: everything above + the `_CapturingProvider`/scripted-provider pattern from `tests/unit/test_agent_runner_ledger.py` (read it first and mirror its provider-fake conventions — the loop consumes `provider.stream(...)`; script it to emit a tool_use for `dispatch_subagent` on the parent's first turn, then plain text; and plain text for the sub-loop's turns).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_dispatch_subagent_e2e.py
"""E2E: parent dispatches; sub-loop is scoped; result returns via the parent ledger."""

from pathlib import Path

from labrat.agent.session import build_agent_session
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.tools.dispatch_subagent import DispatchSubagentTool

# Read tests/unit/test_agent_runner_ledger.py FIRST and reuse its scripted-provider
# shape. Requirements for the fake used here:
#   - it records every `messages` list it is called with (per stream() call);
#   - call 1 (parent turn 1): emits a ToolUseBlock for dispatch_subagent with
#     input {"sub_task": "count the widgets", "max_turns": 2};
#   - call 2 (sub-loop turn 1): emits TextBlock("SUB ANSWER: 42");
#   - call 3 (parent turn 2): emits TextBlock("parent done").
# Adapt block/class names to the real provider contract found in that file.


async def test_dispatch_e2e_scoped_and_ledgered(tmp_path: Path) -> None:
    provider = _ScriptedProvider()  # per the note above
    registry = ToolRegistry()
    registry.register(DispatchSubagentTool())
    ctx = ToolContext()
    loop = build_agent_session(
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="parent system",
        ledger_dir=tmp_path / "ledger",
    )
    texts: list[str] = []
    await loop.run("PARENT TASK: delegate the widget count", on_text=texts.append)

    # 1. Parent completed and the dispatch counted against the parent budget.
    assert loop.tool_calls_used == 1
    assert "parent done" in "".join(texts)

    # 2. The sub-loop's provider call saw ONLY the seed — never parent history.
    sub_messages = provider.calls[1]  # second stream() call = sub-loop turn 1
    flat = str(sub_messages)
    assert "count the widgets" in flat
    assert "PARENT TASK" not in flat            # scoped: parent user msg absent
    assert "parent system" not in flat or True  # system prompt sharing is allowed

    # 3. The sub answer reached the parent as a tool result (via history).
    parent_followup = provider.calls[2]
    assert "SUB ANSWER: 42" in str(parent_followup)


async def test_oversized_sub_result_returns_by_ref(tmp_path: Path) -> None:
    # Spec §5 composition pin: a sub answer over the ledger byte budget (8000)
    # must reach the PARENT's history as a bounded {summary, artifact_ref} block,
    # not the raw text. Same scripted-provider shape as above, but the sub-loop's
    # text turn emits "X" * 20_000. Assert: the parent follow-up messages contain
    # "artifact_ref: result://" and do NOT contain the full 20k payload; the file
    # exists under tmp_path / "ledger".
    provider = _ScriptedProvider(big_sub_answer=True)
    registry = ToolRegistry()
    registry.register(DispatchSubagentTool())
    ctx = ToolContext()
    loop = build_agent_session(
        ctx=ctx, registry=registry, provider=provider,
        system_prompt="parent system", ledger_dir=tmp_path / "ledger",
    )
    await loop.run("PARENT TASK: delegate", on_text=lambda _t: None)
    followup = str(provider.calls[2])
    assert "artifact_ref: result://" in followup
    assert "X" * 20_000 not in followup
    assert any((tmp_path / "ledger").rglob("*")), "artifact file written"


async def test_dispatch_unknown_inside_subloop(tmp_path: Path) -> None:
    # Depth-1 guard #1 end-to-end: the sub-loop's registry lacks dispatch_subagent.
    # Script the sub-loop's turn to attempt a dispatch_subagent tool_use and assert
    # the tool_result it receives is the registry's unknown-tool error, after which
    # the scripted sub-loop returns text and the parent completes normally.
    ...
```

The second test's scripting is intricate — implement it fully (no `...` may survive; the pattern: provider script emits, on the sub-loop call, a ToolUseBlock for `dispatch_subagent`, then on the sub-loop's next call a TextBlock; assert the sub-loop's second `messages` contains the unknown-tool error string used by `ToolRegistry.dispatch` for missing tools — read `base.py`'s dispatch to quote it exactly). If the scripted-provider plumbing for nested loops turns out to need >1 fake class, that's acceptable — keep them in this test file.

- [ ] **Step 2: Run to verify FAIL, then implement the fakes until PASS.** The production code should need NO changes in this task — if it does, report the seam that was broken (that's the point of the e2e).

- [ ] **Step 3: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add tests/unit/test_dispatch_subagent_e2e.py
git commit -m "test(agent): dispatch_subagent e2e — scoped seed, parent budget, depth-1"
```

---

### Task 5: Docs + finish

**Files:**
- Modify: `CLAUDE.md`, `TESTING.md`, `decisions.md`

- [ ] **Step 1: CLAUDE.md** — update the two tool-count sentences (agent-loop section "plus the per-row LLM primitives … and `run_program`" list gains `dispatch_subagent` with a one-line description mirroring this plan's Goal; "Current tools (25)" → recount: verify with `uv run python3 -c "from labrat.agent.data_tools import build_data_tools_registry; print(len(build_data_tools_registry().tools))"` and update both the registered count and the total (+5 TUI). Note the self-gating: "self-errors without ctx.subagent_runner — i.e. on MCP/claude-mcp; injected by build_agent_session on labrat-agent + TUI paths."

- [ ] **Step 2: TESTING.md** — append:

```markdown
## dispatch_subagent (manual spot-check)

1. In the TUI (any connected profile), ask: "delegate a sub-task to a sub-agent: count the
   orders and report just the number". → expect a `▸ dispatch_subagent({...}) ✓` trace, the
   final answer citing the sub-agent's result, and NO sub-agent tool chatter in the parent
   transcript (the sub-loop's own run_sql traces do not appear — only the one dispatch line).
2. Budget echo: the answer/trace completes within the default budgets (no hang).
```

- [ ] **Step 3: decisions.md** —

```markdown
## 2026-07-09 — dispatch_subagent (T1d Phase 2): scoped sub-agent dispatch

The 2026-06-26 Phase-2 design (stranded on feat/context-ledger) shipped with refreshes:
ctx.subagent_runner seam injected by build_agent_session (llm_fn precedent — MCP/claude-mcp
hosts self-error structurally), sub-registry derived from the HOSTING registry minus the tool
(retires the M4 I1 confused-deputy advisory), two structural depth-1 guards, shared
connections/ledger substrate, budgets clamped 1-8 turns / 1-15 calls, and a ledger_payload
return so oversized sub-results bound through the parent ledger like any other output.
Spec: docs/superpowers/specs/2026-07-09-dispatch-subagent-design.md.
```

- [ ] **Step 4: Full gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add CLAUDE.md TESTING.md decisions.md
git commit -m "docs: dispatch_subagent — CLAUDE.md counts, manual spot-check, decisions entry"
```

- [ ] **Step 5: Manual pty spot-check** (controller): TESTING.md step 1 flow live. Then superpowers:finishing-a-development-branch.
