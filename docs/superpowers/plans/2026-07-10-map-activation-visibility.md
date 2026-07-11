# Map v1.1 — Activation Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** make Map activation visible and discoverable — a persistent status-bar indicator of active Maps, a first-connect nudge when a dbt project is configured, and a footer-visible binding — closing the "invisible, hidden mode" gap surfaced by the in-context screenshots.

**Architecture:** the `_StatusBar` widget gains an active-Maps segment updated when the Maps modal closes (same `query(_StatusBar)` refresh pattern as `set_thread`); the first-connect flow adds a one-time nudge; the binding becomes `show=True`.

**Tech Stack:** existing `screens/main.py` (`_StatusBar`, `action_manage_maps`, on-mount/first-connect), Textual. No new deps, no engine changes.

## Global Constraints

- **No engine/behavior change to activation itself** — this is purely making the *existing* `_active_maps` state visible. The retrieval filter, benchmark-safety, and `_active_maps`-is-the-same-list-as-ToolContext invariant are untouched (do NOT reassign `_active_maps`).
- **No new agent tool, no retrieval change** — UI only.
- `screens/` is pyright-exempt; keep it clean. Gates per commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. `test_app_renders` env flake non-signal; `git checkout -- snapshot_report.html` if regenerated.

---

### Task 1: status-bar active-Maps indicator + first-connect nudge + discoverable binding

**Files:** Modify `src/labrat/screens/main.py`; Test `tests/tui/test_map_activation_visibility.py`

**Interfaces (all in `main.py`):**
- `_StatusBar`: add `self._active_maps: list[str] = []` in `__init__`; a `set_active_maps(self, maps: list[str]) -> None` that stores a copy + `self.refresh()` (mirror `set_thread`); `render()` appends `  🗺 Maps: <a>, <b>` when non-empty (if >3 active, show `🗺 Maps: <a>, <b>, +N`). When empty, render is byte-identical to today (no segment).
- `MainScreen.action_manage_maps`: push the modal with a dismiss callback — `self.app.push_screen(MapActivateScreen(...), lambda _=None: self._refresh_map_indicator())`. Add `_refresh_map_indicator(self)` that does `for bar in self.query(_StatusBar): bar.set_active_maps(self._active_maps)` (mirror the existing `set_thread` fan-out at main.py:306). (The modal covers the screen while open, so refreshing on dismiss is sufficient and correct.)
- **First-connect nudge:** in the first-connect flow (after the Cartographer prepass / where the other first-connect notifies fire, ~main.py:452), if `self._profile_obj` has a truthy `dbt_project_path` AND the Map store has zero `kind="map"` docs (`not self._resolve_map_store().docs(kind="map")`), `self.notify("🗺 dbt project detected — press Ctrl+Shift+P → Auto-seed to sketch domain Maps", timeout=8)`. Fire at most once (guard with a flag or the zero-maps check). Do NOT auto-run the seed (the explicit-action decision stands — this only points the user at it).
- **Binding:** change `Binding("ctrl+shift+p", "manage_maps", "Maps", show=False)` → `show=True` (footer discoverability).

- [ ] **Step 1: Write the failing pilot tests**

`tests/tui/test_map_activation_visibility.py` (follow `tests/tui/test_main_screen_scent.py`'s full-`MainScreen` `_Host` + ecommerce_db fixture pattern):

```python
async def test_status_bar_shows_active_maps_after_modal_closes(ecommerce_db, tmp_path, monkeypatch):
    # build a MainScreen (project_root_override=tmp_path); seed a "revenue" Map;
    # open action_manage_maps, activate revenue (mutate _active_maps), dismiss →
    # assert a _StatusBar renders "🗺 Maps: revenue"; deactivate + refresh → segment gone.
    ...

async def test_status_bar_empty_when_no_active_maps(ecommerce_db, tmp_path, monkeypatch):
    # no active maps → _StatusBar.render() contains no "🗺 Maps:" segment (byte-identical to today).
    ...

async def test_first_connect_nudges_when_dbt_and_no_maps(ecommerce_db, tmp_path, monkeypatch):
    # profile_obj.dbt_project_path set + zero maps on disk → a "Ctrl+Shift+P" nudge notify fires;
    # with maps already present → no nudge. (Capture notifies via a monkeypatched self.notify or the app's _notifications.)
    ...
```

(Complete the stubs against the real `_StatusBar`/`MainScreen`; the assertions are the contract. For the nudge test, the simplest capture is monkeypatching `MainScreen.notify` to record messages, or asserting on `_refresh_map_indicator`/the render string directly.)

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement** per the Interfaces block. Keep `_active_maps` mutate-not-reassign (the indicator reads it, never rebinds it). `set_active_maps` stores `list(maps)` (a copy for display) — the live link stays `self._active_maps`.
- [ ] **Step 4: Run tests** → PASS. Re-run `tests/tui/test_maps_tui.py` + `tests/tui/test_main_screen_scent.py` (unchanged behavior) → green.
- [ ] **Step 5: Docs + gates + commit.** `TESTING.md`: note the active-Maps status segment + the first-connect nudge in the "Maps (v1)" section. `decisions.md`: short dated entry (Map v1.1 activation-visibility: status-bar indicator + first-connect nudge + footer binding; no engine change). Commit `"feat(map): status-bar active-Maps indicator + first-connect nudge + discoverable binding"`.

---

## Manual gate (after Task 1)

Re-capture the pty composite (or a pilot render): with Revenue active, the main screen's status bar shows `🗺 Maps: revenue` when the modal is closed; the footer lists the Maps binding; connecting a dbt-configured profile with no Maps fires the nudge. Confirm the empty-state status bar is unchanged.

## Execution notes

- Branch: `feat/map-activation-visibility` off master; merge after Fable review + the gate.
- One task (cohesive UI increment; screens/-only).
- Does NOT touch the retrieval filter, `active_maps` plumbing, or any engine path — benchmark-safety is unaffected by construction (no engine change).
