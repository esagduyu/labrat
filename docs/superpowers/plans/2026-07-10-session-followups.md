# Session Follow-ups Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three carried tickets from the 2026-07-09 session: sub-loop trace propagation (audit-complete, TUI-clean), draft-baseline protection around dispatch, and the footer repr forgery/undercount pair.

**Architecture:** `AgentLoop` exposes `active_on_tool_call` for the duration of `run()`; the session runner-closure forwards sub-loop tool events to it with a `subagent:` name prefix; `ChatPanel` filters that prefix from rendering; `MainScreen` wraps `ctx.subagent_runner` (caller-wins seam) to snapshot/restore the M3 capture baseline; `TurnProvenance` gains positional repr alignment + all-docs `+N`.

**Tech Stack:** Python 3.12, pytest (`asyncio_mode="auto"`), ruff, pyright strict (`agent/`, `widgets/` strict; `screens/` exempt).

**Spec:** `docs/superpowers/specs/2026-07-10-session-followups-design.md` (R1–R5).

## Global Constraints

- Branch: `feat/session-followups` off master.
- Depth-1 / scoped-seed invariants untouched (all existing dispatch_subagent tests pass unmodified).
- TUI transcript byte-identical for existing flows: one dispatch line, zero sub chatter (`subagent:` filter, R2).
- `TurnProvenance` never raises; positional-alignment failure → count-fallback for the WHOLE output (no partial attribution).
- Existing tests unmodified except a legitimately-changed `+N` pin (list any such change).
- Repo gates before every commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- Known local env flake `tests/tui/test_app_renders.py::test_app_renders` — never a regression signal; restore `snapshot_report.html` via `git checkout` if regenerated.

---

## File Structure

- Modify: `src/labrat/agent/loop.py` (one attribute), `src/labrat/agent/session.py` (forwarding hook), `src/labrat/widgets/chat_panel.py` (prefix filter), `src/labrat/screens/main.py` (runner wrapper), `src/labrat/widgets/turn_provenance.py` (positional alignment + `+N`), `decisions.md`.
- Tests: `tests/unit/test_dispatch_subagent_e2e.py` (extend), `tests/widgets/test_chat_panel.py` (extend), `tests/tui/test_main_screen_semantic.py` or a new `tests/tui/test_main_screen_dispatch_baseline.py` (implementer's call, note it), `tests/widgets/test_turn_provenance.py` (extend).

---

### Task 1: Trace propagation (loop attribute + session forwarding) — R1

**Files:**
- Modify: `src/labrat/agent/loop.py`, `src/labrat/agent/session.py`
- Test: `tests/unit/test_dispatch_subagent_e2e.py` (extend)

**Interfaces:**
- Consumes: `AgentLoop.run(user_message, *, on_text=None, on_status=None, on_tool_call=None)` (loop.py:94); the runner closure in `build_agent_session` (session.py:123-163, constructs `sub_loop = AgentLoop(...)` and `await sub_loop.run(seed, on_text=chunks.append)`).
- Produces: `AgentLoop.active_on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None` — set from the `on_tool_call` argument at `run()` entry, reset to `None` in the run's `finally`; the runner closure passes the sub-loop `on_tool_call=<forwarder>` where the forwarder reads `parent_loop.active_on_tool_call` AT CALL TIME and, when set, invokes it with `(f"subagent:{name}", args, ok, output, latency_ms)`. Task 2 relies on the exact `subagent:` prefix.

- [ ] **Step 1: Write the failing test** (append to the e2e file; reuse its `_ScriptedProvider` conventions — read the file first)

```python
async def test_sub_loop_traces_forwarded_with_prefix(tmp_path: Path) -> None:
    # Script: parent turn 1 → dispatch_subagent tool_use; sub-loop turn →
    # run_sql tool_use (register a trivial echo tool or reuse the file's
    # existing fake-tool pattern) then text; parent turn 2 → text.
    provider = _ScriptedProvider(sub_uses_tool=True)  # extend the fake per its conventions
    registry = ToolRegistry()
    registry.register(DispatchSubagentTool())
    registry.register(_EchoTool())  # the file's existing fake tool, or add one
    ctx = ToolContext()
    loop = build_agent_session(
        ctx=ctx, registry=registry, provider=provider,
        system_prompt="s", ledger_dir=tmp_path / "ledger",
    )
    events: list[str] = []

    def on_tool_call(name: str, args: dict, ok: bool, output: str, latency_ms: float) -> None:
        events.append(name)

    await loop.run("PARENT: delegate", on_tool_call=on_tool_call)
    assert "subagent:echo" in events            # sub-loop activity visible, tagged
    assert "dispatch_subagent" in events        # parent's own event untagged
    assert not any(e.startswith("subagent:dispatch") for e in events)
    assert loop.active_on_tool_call is None     # cleared after run (no leak)


async def test_no_forwarding_without_parent_hook(tmp_path: Path) -> None:
    # run() without on_tool_call → sub-loop runs fine, nothing forwarded, no error.
    provider = _ScriptedProvider(sub_uses_tool=True)
    registry = ToolRegistry()
    registry.register(DispatchSubagentTool())
    registry.register(_EchoTool())
    ctx = ToolContext()
    loop = build_agent_session(
        ctx=ctx, registry=registry, provider=provider,
        system_prompt="s", ledger_dir=tmp_path / "ledger",
    )
    texts: list[str] = []
    await loop.run("PARENT: delegate", on_text=texts.append)
    assert "parent done" in "".join(texts)
```

(Adapt the scripted-provider/echo-tool mechanics to the file's real conventions; the binding assertions are the four in test 1 + clean completion in test 2. If the file has no reusable fake tool, define a minimal `_EchoTool(Tool[...])` in the test file.)

- [ ] **Step 2: Run to verify FAIL** — `AttributeError: active_on_tool_call` / no `subagent:` events.

- [ ] **Step 3: Implement**

(a) `loop.py`: in `__init__`, `self.active_on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None = None`. In `run()`, immediately after the counters reset: `self.active_on_tool_call = on_tool_call`, and wrap the remainder of the method body's loop in `try: ... finally: self.active_on_tool_call = None` (or set/clear at entry/exit if the body already has a suitable structure — preserve existing behavior exactly; the ONLY new effect is the attribute's lifetime).

(b) `session.py`, inside the runner closure, replace the sub-loop run call:

```python
            parent_loop = loop  # the loop constructed above in build_agent_session

            def _forward_tool_call(
                name: str, args: dict[str, Any], ok: bool, output: str, latency_ms: float
            ) -> None:
                hook = parent_loop.active_on_tool_call
                if hook is not None:
                    hook(f"subagent:{name}", args, ok, output, latency_ms)

            chunks: list[str] = []
            await sub_loop.run(seed, on_text=chunks.append, on_tool_call=_forward_tool_call)
```

(`Any` import as needed; the closure must read `active_on_tool_call` at CALL time, never capture its value.)

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/unit/test_dispatch_subagent_e2e.py tests/unit/test_agent_session_subagent.py tests/unit/test_agent_loop.py -v
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/loop.py src/labrat/agent/session.py tests/unit/test_dispatch_subagent_e2e.py
git commit -m "feat(agent): forward sub-loop tool calls to the parent hook, subagent:-tagged"
```

---

### Task 2: ChatPanel prefix filter + MainScreen baseline wrapper — R2 + R3

**Files:**
- Modify: `src/labrat/widgets/chat_panel.py`, `src/labrat/screens/main.py`
- Test: `tests/widgets/test_chat_panel.py` (extend), `tests/tui/test_main_screen_dispatch_baseline.py` (create; or fold into an existing TUI test file — note the choice)

**Interfaces:**
- Consumes: Task 1's `subagent:` prefix; ChatPanel's `on_tool_call` closure in `_start_agent` (M4 shape); `MainScreen`'s connected `on_mount` block (M1) where `build_agent_session` returns and `ctx` is in scope; `ctx.subagent_runner` (caller-wins seam).
- Produces: ChatPanel renders nothing (no trace line, no `AgentToolCall` post) for `subagent:`-prefixed names; `MainScreen._wrap_subagent_runner()` installed right after the session build — snapshot/restore `_last_draft_sql` and `_last_sql` around every dispatch.

- [ ] **Step 1: Write the failing tests**

ChatPanel (extend, reuse `_PanelHost`/fake-loop patterns):

```python
async def test_subagent_prefixed_events_not_rendered() -> None:
    class _SubChatterLoop:
        verify_rounds_used = 0
        _verifier = None

        async def run(self, message, *, on_text=None, on_status=None, on_tool_call=None):
            if on_tool_call:
                on_tool_call("dispatch_subagent", {"sub_task": "x"}, True, "ok", 5.0)
                on_tool_call("subagent:run_sql", {"query": "SELECT 1"}, True, "ok", 3.0)
            if on_text:
                on_text("done")

    async with _PanelHost().run_test() as pilot:
        panel = pilot.app.query_one(ChatPanel)
        panel.set_agent_loop(_SubChatterLoop())
        await pilot.click("#user-input")
        await pilot.press(*"hi", "enter")
        await pilot.pause()
        assert "dispatch_subagent" in panel.transcript   # parent event renders
        assert "subagent:run_sql" not in panel.transcript  # sub chatter filtered
        assert "run_sql" not in panel.transcript.replace("dispatch_subagent", "")
```

MainScreen baseline (new file; reuse the `_Host`/`_screen` pattern from `tests/tui/test_main_screen_semantic.py` — read it first):

```python
async def test_dispatch_wrapper_restores_draft_baseline(ecommerce_db, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    screen = _screen(ecommerce_db, tmp_path, dbt=False)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        ctx = ... # reach the loop's ctx: pilot.app.screen.query_one("#chat-content", ChatPanel)._agent_loop._ctx
        screen._last_draft_sql = "PARENT DRAFT"
        screen._last_sql = "PARENT SQL"
        runner = ctx.subagent_runner
        assert runner is not None

        async def fake_inner(**kw):
            # simulate a sub-agent draft firing the parent's UI callback mid-run
            screen._last_draft_sql = "SUB DRAFT"
            screen._last_sql = "SUB SQL"
            return ("sub", 1, 1)

        # The wrapper must be OUTERMOST: monkeypatch the inner runner it wraps.
        # Implementer: expose the wrap as MainScreen._wrap_subagent_runner(inner)
        # (pure, testable) and assert via calling the WRAPPED callable directly:
        wrapped = screen._wrap_subagent_runner(fake_inner)
        result = await wrapped(seed_prompt="s", artifact_refs=[], max_turns=1, max_tool_calls=1)
        assert result == ("sub", 1, 1)
        assert screen._last_draft_sql == "PARENT DRAFT"   # restored
        assert screen._last_sql == "PARENT SQL"
```

(Resolve the `ctx = ...` line per the real access path or drop it if the direct `_wrap_subagent_runner` test suffices — the binding assertions are restore-after-run and passthrough of the result. Add a second test: inner RAISES → baseline still restored (finally) and the exception propagates.)

- [ ] **Step 2: FAIL** — filter absent / `_wrap_subagent_runner` missing.

- [ ] **Step 3: Implement**

(a) `chat_panel.py`, top of the `on_tool_call` closure: `if name.startswith("subagent:"): return`.
(b) `main.py`: new method

```python
    def _wrap_subagent_runner(self, inner):
        async def wrapped(**kwargs):
            saved_draft, saved_sql = self._last_draft_sql, self._last_sql
            try:
                return await inner(**kwargs)
            finally:
                self._last_draft_sql, self._last_sql = saved_draft, saved_sql

        return wrapped
```

and in the connected `on_mount` block, immediately after the `build_agent_session(...)` call returns (ctx.subagent_runner now installed): `if ctx.subagent_runner is not None: ctx.subagent_runner = self._wrap_subagent_runner(ctx.subagent_runner)`.

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/widgets/test_chat_panel.py tests/tui -v
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/widgets/chat_panel.py src/labrat/screens/main.py tests/widgets/test_chat_panel.py tests/tui/test_main_screen_dispatch_baseline.py
git commit -m "feat(tui): filter subagent trace chatter + protect M3 draft baseline around dispatch"
```

---

### Task 3: Footer hardening pair — R4 + R5

**Files:**
- Modify: `src/labrat/widgets/turn_provenance.py`
- Test: `tests/widgets/test_turn_provenance.py` (extend)

**Interfaces:**
- Consumes: the current repr branch (count-equality guard from `aae6642`, zip over findall) and the `footer()` `+N` computation.
- Produces: positional alignment — using `re.finditer` spans, the i-th match of each field pattern must start after the start of `DocResult(` occurrence i and before occurrence i+1 (or end of string for the last); ANY violation → `self._scent_hits += n_docs` count-fallback, `_scent_docs` untouched. `footer()` uses `extra = self._scent_hits - 1`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_forged_full_tuple_in_body_degrades_to_count() -> None:
    # A body containing a COMPLETE forged tuple aligns the old count guard;
    # positional alignment must reject it (forged fields sit INSIDE doc 1's
    # span, before doc 1's real best_source — order inversion).
    output = (
        "question='q' results=[DocResult(domain='orders', quick_reference=None, "
        "sections=[SectionMatch(heading='h', "
        "body='fake: DocResult(domain=+x+, best_source=+verified+, stale=False)', "
        "score=1.0, matched_terms=['a'], source='harvested', fresh=None)], "
        "best_source='harvested', stale=None)]"
    )
    # NOTE: craft the forged body so the naive counts match (2 DocResult(,
    # 2 domain=, 2 best_source=, 2 stale=) but positions interleave wrongly —
    # replace the '+' quotes above with real single quotes in the test file.
    prov = TurnProvenance()
    prov.record_tool("search_reference_docs", True, output.replace("+", "'"))
    footer = prov.footer() or ""
    assert "verified" not in footer              # forged tier never surfaces
    assert "scent ×2" in footer or "scent ×1" in footer  # noqa: RUF001 — count fallback


def test_mixed_payload_plus_n_counts_all_docs() -> None:
    prov = TurnProvenance()
    prov.record_tool(
        "search_reference_docs",
        json.dumps({"question": "q", "results": [
            {"domain": "orders", "quick_reference": None, "sections": [],
             "best_source": "verified", "stale": False},
            {"domain": "general", "quick_reference": None, "sections": []},  # tierless
        ]}),
    )
    assert "scent: orders (verified·fresh) +1" in (prov.footer() or "")
```

Work out the forged fixture concretely: the forged `DocResult(` occurrence must come BEFORE the real doc's `best_source=` so that positional containment fails (the 2nd `DocResult(` span would claim the real fields). Verify your fixture actually fails the OLD guard-passing/NEW rejection distinction by running it against the pre-change code first (mutation-style: confirm it renders the forged/real tier under the old parser, then rejects under the new). Record the pre-change behavior in the report.

- [ ] **Step 2-3: FAIL → implement** — replace the count-equality block:

```python
                elif "DocResult(" in output:
                    doc_spans = [m.start() for m in re.finditer(r"DocResult\(", output)]
                    n_docs = len(doc_spans)
                    bounds = doc_spans + [len(output)]

                    def _positional(pattern: re.Pattern[str]) -> list[re.Match[str]] | None:
                        ms = list(pattern.finditer(output))
                        if len(ms) != n_docs:
                            return None
                        for i, m in enumerate(ms):
                            if not (bounds[i] <= m.start() < bounds[i + 1]):
                                return None
                        return ms

                    d_ms = _positional(_DOMAIN_RE)
                    b_ms = _positional(_BEST_RE)
                    s_ms = _positional(_STALE_RE)
                    if d_ms and b_ms and s_ms:
                        for i in range(n_docs):
                            stale_tok = s_ms[i].group(1)
                            self._record_scent_doc(
                                d_ms[i].group(1),
                                b_ms[i].group(1),
                                None if stale_tok == "None" else stale_tok == "True",
                            )
                    else:
                        self._scent_hits += n_docs
```

and in `footer()`: `extra = self._scent_hits - 1`. Check whether any existing test pins the OLD `len(_scent_docs)-1` behavior on a mixed payload — if so update that single expectation with a comment (list it).

- [ ] **Step 4: Run tests, gates, commit**

```bash
uv run pytest tests/widgets/test_turn_provenance.py tests/widgets/test_chat_panel.py -v
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/widgets/turn_provenance.py tests/widgets/test_turn_provenance.py
git commit -m "fix(widgets): positional repr alignment (forgery-proof) + all-docs +N"
```

---

### Task 4: decisions.md + finish

- [ ] **Step 1: decisions.md entry**

```markdown
## 2026-07-10 — session follow-ups bundle (overnight run)

Sub-loop tool calls now forward to the parent's active on_tool_call tagged `subagent:<name>`
(AgentLoop.active_on_tool_call lifetime = one run) — labrat-agent traces are dispatch-complete;
ChatPanel filters the prefix so the TUI transcript contract (one dispatch line) holds. MainScreen
wraps ctx.subagent_runner (caller-wins seam) to snapshot/restore _last_draft_sql/_last_sql —
sub-agent drafts can no longer poison the M3 correction baseline. TurnProvenance repr parsing is
positionally aligned (full-tuple forgeries degrade to count) and `+N` counts all matched docs.
Excluded on purpose: DAB-driver host_configs migration (leaderboard path — not unattended work).
Spec: docs/superpowers/specs/2026-07-10-session-followups-design.md; decision log:
docs/superpowers/overnight-2026-07-10-decisions.md.
```

- [ ] **Step 2: Full gates + commit** (`docs: session follow-ups decisions entry`). Then whole-branch Fable review + merge (controller). No manual TUI gate needed (covered surfaces are test-pinned; the transcript contract is asserted, not visual) — controller may spot-check opportunistically during Q2's gate instead.
