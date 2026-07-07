# TUI M4 — Verification Toggle Live + Provenance Footer (T3c) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the M1 `verify_enabled` toggle observably real, and stamp each agent chat answer with a one-line provenance footer aggregated from the turn's tool activity (Scent retrievals + freshness, join verification, lineage, query count, verifier outcome).

**Architecture:** A pure `TurnProvenance` accumulator (`widgets/turn_provenance.py`, pyright-strict, no Textual imports) is fed by `ChatPanel`'s existing `on_tool_call` hook and rendered as a dim footer line when the turn ends. Freshness comes from `MainScreen._scent_stale` (M2) via an injected provider callable; verifier outcome from `AgentLoop.verify_rounds_used` + whether a verifier is attached. Zero core/agent changes — this phase is UI aggregation only.

**Tech Stack:** Python 3.12, Textual, pytest (`asyncio_mode = "auto"`), ruff, pyright strict (applies to `src/labrat/widgets/`; `screens/` exempt).

**Spec:** `docs/superpowers/specs/2026-07-06-tui-integration-design.md` §7. **Prerequisites: M1 and M2 merged** (M1: factory wiring, `verify_enabled`, ChatPanel hooks; M2: `MainScreen._scent_stale`). M3 not required.

## Global Constraints

- Branch: `feat/tui-m4-verify-provenance` off master (after M1+M2 merged).
- **The verifier is the loop `LLMVerifier` only** (already wired in M1's factory call). Do NOT import `agent/verification/consensus.py` — consensus stays benchmark-only (spec D7).
- **No footer when there's nothing to say** — a turn with no scent/join/lineage/sql/verifier signal appends no line.
- Footer parsing of tool outputs must be tolerant: outputs may be ledger-summarized strings; on any parse failure degrade to counting the call, never raise.
- Zero changes under `src/labrat/agent/` or `src/labrat/maze/` in this phase.
- Repo gates before every commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.

---

## File Structure

- Create: `src/labrat/widgets/turn_provenance.py`.
- Modify: `src/labrat/widgets/chat_panel.py` (feed + render), `src/labrat/screens/main.py` (stale provider hookup), `TESTING.md`, `decisions.md`.
- Tests: `tests/widgets/test_turn_provenance.py`, `tests/widgets/test_chat_panel.py` (extend), `tests/tui/test_main_screen_verify.py`.

---

### Task 1: `TurnProvenance` accumulator

**Files:**
- Create: `src/labrat/widgets/turn_provenance.py`
- Test: `tests/widgets/test_turn_provenance.py`

**Interfaces:**
- Consumes: the `on_tool_call` arg shape `(name: str, args: dict, ok: bool, output: str, latency_ms: float)` — only `(name, ok, output)` matter here.
- Produces (Task 2 uses):
  - `TurnProvenance(scent_stale: bool = False)`
  - `.record_tool(name: str, ok: bool, output: str) -> None`
  - `.set_verifier(rounds_used: int | None) -> None` — `None` = verification off this turn
  - `.footer() -> str | None` — plain-text footer (no Rich markup), `None` when empty

- [ ] **Step 1: Write the failing tests**

```python
# tests/widgets/test_turn_provenance.py
"""TurnProvenance: aggregate a turn's grounding signals into one footer line."""

import json

from labrat.widgets.turn_provenance import TurnProvenance


def _scent_output(domains: list[str]) -> str:
    return json.dumps({"question": "q", "results": [{"domain": d, "quick_reference": None, "sections": []} for d in domains]})


def test_empty_turn_has_no_footer() -> None:
    assert TurnProvenance().footer() is None


def test_scent_hits_counted_with_freshness() -> None:
    prov = TurnProvenance(scent_stale=False)
    prov.record_tool("search_reference_docs", True, _scent_output(["orders", "general"]))
    footer = prov.footer()
    assert footer is not None
    assert "scent ×2" in footer and "fresh" in footer


def test_stale_scent_labelled() -> None:
    prov = TurnProvenance(scent_stale=True)
    prov.record_tool("search_reference_docs", True, _scent_output(["orders"]))
    assert "stale" in (prov.footer() or "")


def test_unparseable_scent_output_degrades_to_count() -> None:
    prov = TurnProvenance()
    prov.record_tool("search_reference_docs", True, "result://abc/0001 (summarized)")
    assert "scent ×1" in (prov.footer() or "")


def test_join_lineage_and_query_count() -> None:
    prov = TurnProvenance()
    prov.record_tool("verify_join", True, "{}")
    prov.record_tool("explain_lineage", True, "{}")
    prov.record_tool("run_sql", True, "{}")
    prov.record_tool("run_sql", True, "{}")
    footer = prov.footer() or ""
    assert "join verified" in footer
    assert "lineage" in footer
    assert "2 queries" in footer


def test_failed_calls_not_counted() -> None:
    prov = TurnProvenance()
    prov.record_tool("run_sql", False, "error")
    prov.record_tool("verify_join", False, "error")
    assert prov.footer() is None


def test_verifier_outcome() -> None:
    prov = TurnProvenance()
    prov.set_verifier(rounds_used=0)
    assert "verifier ✓" in (prov.footer() or "")
    prov2 = TurnProvenance()
    prov2.set_verifier(rounds_used=2)
    assert "verifier ✓ (2 rounds)" in (prov2.footer() or "")
    prov3 = TurnProvenance()
    prov3.set_verifier(rounds_used=None)  # verification off → no verifier segment
    assert prov3.footer() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/widgets/test_turn_provenance.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/labrat/widgets/turn_provenance.py
"""Per-turn provenance aggregation for the chat footer (T3c).

Pure accumulator — no Textual imports, no LLM, no I/O. Fed from ChatPanel's
on_tool_call hook; tolerant of ledger-summarized tool outputs (any parse
failure degrades to call-counting, never raises).
"""

from __future__ import annotations

import json


class TurnProvenance:
    """Aggregates one chat turn's grounding signals into a footer line."""

    def __init__(self, scent_stale: bool = False) -> None:
        self._scent_stale = scent_stale
        self._scent_hits = 0
        self._scent_domains: set[str] = set()
        self._join_verified = False
        self._lineage_used = False
        self._sql_runs = 0
        self._verifier_rounds: int | None = None

    def record_tool(self, name: str, ok: bool, output: str) -> None:
        if not ok:
            return
        if name == "search_reference_docs":
            self._scent_hits += 1
            try:
                payload = json.loads(output)
                results = payload.get("results", []) if isinstance(payload, dict) else []
                for doc in results:
                    if isinstance(doc, dict) and isinstance(doc.get("domain"), str):
                        self._scent_domains.add(doc["domain"])
            except (ValueError, TypeError):
                pass  # summarized/non-JSON output: keep the hit count only
        elif name == "verify_join":
            self._join_verified = True
        elif name == "explain_lineage":
            self._lineage_used = True
        elif name == "run_sql":
            self._sql_runs += 1

    def set_verifier(self, rounds_used: int | None) -> None:
        self._verifier_rounds = rounds_used

    def footer(self) -> str | None:
        parts: list[str] = []
        if self._scent_hits:
            freshness = "stale" if self._scent_stale else "fresh"
            parts.append(f"scent ×{self._scent_hits} ({freshness})")
        if self._join_verified:
            parts.append("join verified")
        if self._lineage_used:
            parts.append("lineage")
        if self._sql_runs:
            noun = "query" if self._sql_runs == 1 else "queries"
            parts.append(f"{self._sql_runs} {noun}")
        if self._verifier_rounds is not None:
            if self._verifier_rounds > 0:
                parts.append(f"verifier ✓ ({self._verifier_rounds} rounds)")
            else:
                parts.append("verifier ✓")
        if not parts:
            return None
        return "⚑ grounded: " + " · ".join(parts)
```

- [ ] **Step 4: Run tests, gates, commit**

Run: `uv run pytest tests/widgets/test_turn_provenance.py -v` — 7 PASS.

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/widgets/turn_provenance.py tests/widgets/test_turn_provenance.py
git commit -m "feat(widgets): TurnProvenance — per-turn grounding footer accumulator"
```

---

### Task 2: ChatPanel renders the footer

**Files:**
- Modify: `src/labrat/widgets/chat_panel.py`, `src/labrat/screens/main.py`
- Test: `tests/widgets/test_chat_panel.py` (extend)

**Interfaces:**
- Consumes: `TurnProvenance` (Task 1); M1's `on_tool_call` hook inside `_start_agent`; `AgentLoop.verify_rounds_used: int` and `loop._verifier` (None when verification off); `MainScreen._scent_stale` (M2).
- Produces: `ChatPanel.set_scent_stale_provider(fn: Callable[[], bool]) -> None`; a dim `⚑ grounded: …` history line appended after the agent response when the footer is non-empty.

- [ ] **Step 1: Write the failing test**

Extend `tests/widgets/test_chat_panel.py` (reuse `_PanelHost` and the M1 `_FakeLoop` pattern):

```python
class _GroundedFakeLoop:
    """Emits one scent lookup + one run_sql, then text; exposes verifier attrs."""

    verify_rounds_used = 0
    _verifier = None  # verification off

    async def run(self, message, *, on_text=None, on_status=None, on_tool_call=None):
        if on_tool_call:
            on_tool_call(
                "search_reference_docs", {"question": "q"}, True,
                '{"question": "q", "results": [{"domain": "orders", "quick_reference": null, "sections": []}]}',
                8.0,
            )
            on_tool_call("run_sql", {"query": "SELECT 1"}, True, '{"ok": true}', 5.0)
        if on_text:
            on_text("here you go")


async def test_footer_appended_after_turn() -> None:
    async with _PanelHost().run_test() as pilot:
        panel = pilot.app.query_one(ChatPanel)
        panel.set_scent_stale_provider(lambda: False)
        panel.set_agent_loop(_GroundedFakeLoop())
        await pilot.click("#user-input")
        await pilot.press(*"hi", "enter")
        await pilot.pause()
        assert "⚑ grounded: scent ×1 (fresh) · 1 query" in panel.transcript


async def test_no_footer_on_plain_turn() -> None:
    async with _PanelHost().run_test() as pilot:
        panel = pilot.app.query_one(ChatPanel)

        class _PlainLoop:
            verify_rounds_used = 0
            _verifier = None

            async def run(self, message, *, on_text=None, on_status=None, on_tool_call=None):
                if on_text:
                    on_text("hello")

        panel.set_agent_loop(_PlainLoop())
        await pilot.click("#user-input")
        await pilot.press(*"hi", "enter")
        await pilot.pause()
        assert "⚑ grounded" not in panel.transcript
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/widgets/test_chat_panel.py -v`
Expected: new tests FAIL (`AttributeError: set_scent_stale_provider`).

- [ ] **Step 3: Implement**

`widgets/chat_panel.py`:

(a) `__init__`: add `self._scent_stale_provider: Callable[[], bool] | None = None` (import `Callable` from `collections.abc`).

(b) Public setter next to `set_agent_loop`:

```python
    def set_scent_stale_provider(self, fn: Callable[[], bool]) -> None:
        """Inject the screen's scent-staleness flag for footer freshness labels."""
        self._scent_stale_provider = fn
```

(c) In `_start_agent`, build the accumulator before calling the loop and feed it from the existing `on_tool_call` closure:

```python
        from labrat.widgets.turn_provenance import TurnProvenance

        stale = self._scent_stale_provider() if self._scent_stale_provider else False
        provenance = TurnProvenance(scent_stale=stale)
```

inside the existing `on_tool_call` closure (M1, after the trace-line append):

```python
            provenance.record_tool(name, ok, output)
```

(d) In the `finally` block, right AFTER the `if full_response:` history append and BEFORE the error append:

```python
            verifier_on = getattr(self._agent_loop, "_verifier", None) is not None
            provenance.set_verifier(
                getattr(self._agent_loop, "verify_rounds_used", 0) if verifier_on else None
            )
            footer = provenance.footer()
            if footer and full_response:
                self._append_history(f"[dim]{footer}[/dim]", footer)
```

(e) `screens/main.py`, in the connected `on_mount` block right after `set_agent_loop(loop)`:

```python
        chat_panel = self.query_one("#chat-content", ChatPanel)
        chat_panel.set_agent_loop(loop)
        chat_panel.set_scent_stale_provider(lambda: self._scent_stale)
```

- [ ] **Step 4: Run tests, gates, commit**

Run: `uv run pytest tests/widgets/test_chat_panel.py tests/tui -v` — new PASS, existing PASS (the M1 hook test's `_FakeLoop` needs the two class attrs `verify_rounds_used = 0` / `_verifier = None` added if `getattr` defaults don't cover it — they do, since `getattr(..., 0)` is used; verify).

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/widgets/chat_panel.py src/labrat/screens/main.py tests/widgets/test_chat_panel.py
git commit -m "feat(tui): provenance footer on agent answers (scent/join/lineage/queries/verifier)"
```

---

### Task 3: Verify-toggle end-to-end check + docs + finish

**Files:**
- Test: `tests/tui/test_main_screen_verify.py`
- Modify: `TESTING.md`, `decisions.md`

- [ ] **Step 1: Write the wiring test (M1 promised it; M4 owns proving it)**

```python
# tests/tui/test_main_screen_verify.py
"""Profile.verify_enabled must reach the loop as an attached LLMVerifier."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.profile.model import Profile
from labrat.screens.main import MainScreen
from labrat.widgets.chat_panel import ChatPanel


class _Host(App[None]):
    def __init__(self, screen: MainScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _screen(ecommerce_db: Path, *, verify: bool) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    return MainScreen(
        profile="vprof", dialect="duckdb",
        catalog=conn.introspect_catalog(), connection=conn,
        profile_obj=Profile(
            name="vprof", dialect="duckdb", path=str(ecommerce_db), verify_enabled=verify
        ),
    )


async def test_verify_enabled_attaches_verifier(ecommerce_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with _Host(_screen(ecommerce_db, verify=True)).run_test() as pilot:
        await pilot.pause()
        loop = pilot.app.screen.query_one("#chat-content", ChatPanel)._agent_loop
        assert loop._verifier is not None
        # Guard: it must be the sufficiency LLMVerifier, never consensus.
        from labrat.agent.verifier import LLMVerifier

        assert isinstance(loop._verifier, LLMVerifier)


async def test_verify_disabled_attaches_none(ecommerce_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with _Host(_screen(ecommerce_db, verify=False)).run_test() as pilot:
        await pilot.pause()
        loop = pilot.app.screen.query_one("#chat-content", ChatPanel)._agent_loop
        assert loop._verifier is None
```

Run: `uv run pytest tests/tui/test_main_screen_verify.py -v`
Expected: PASS immediately (M1 wired it). If it FAILS, M1's `verify=profile_obj.verify_enabled` wiring regressed — fix `screens/main.py`, not the test.

- [ ] **Step 2: TESTING.md section**

```markdown
## M4 — verification toggle + provenance footer (manual gate)

1. Ctrl+, → "Verify answers" ON → Save → restart. Ask a question the agent will answer thinly
   ("how many rows?" with no table named) → occasionally a dim `verifier: insufficient — …`
   status line appears and the agent continues; final answer then carries `verifier ✓ (1 rounds)`
   in its footer. With a good first answer the footer shows `verifier ✓`.
2. Ask "any reference notes on orders? then count the orders" → footer like
   `⚑ grounded: scent ×1 (fresh) · 1 query`.
3. Corrupt `.schema_fingerprint` (see M2 gate) and relaunch → the same flow shows `scent ×1 (stale)`.
4. A pure-prose turn (e.g. "thanks") → no footer line at all.
5. Verify OFF (default): no verifier segment ever appears in footers.
```

- [ ] **Step 3: decisions.md entry**

```markdown
## 2026-07-XX — TUI M4: verification toggle live + provenance footer (T3c)

Chat answers now end with a dim `⚑ grounded: …` footer aggregated purely in the UI from the
turn's on_tool_call stream (scent hits + fresh/stale from the M2 fingerprint, verify_join,
explain_lineage, run_sql count) plus AgentLoop.verify_rounds_used. Verification in the TUI is
the loop sufficiency LLMVerifier only (Profile.verify_enabled, wired in M1); K-of-N consensus
remains benchmark-only per spec D7 (ablated within-noise, wrong shape for interactive chat).
No agent/maze core changes in this phase.
```

- [ ] **Step 4: Full gates, manual gate, finish**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add tests/tui/test_main_screen_verify.py TESTING.md decisions.md
git commit -m "test+docs: M4 verify-toggle wiring proof + manual gate + decisions entry"
```

Run the TESTING.md M4 manual gate (or hand to the human). Then use superpowers:finishing-a-development-branch. This completes the four-phase TUI-integration roadmap — update memory `project_tui_integration_next` and `docs/tui-integration-handoff.md` status accordingly.
