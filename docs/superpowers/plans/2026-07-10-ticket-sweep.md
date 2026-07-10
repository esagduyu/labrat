# Cheese + Trail Ticket Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** clear the genuine correctness/robustness minors from the Cheese v1 and Trail v1 whole-branch reviews (skipping the documented-acceptable ones), each pinned by a test.

**Architecture:** two independent bundles — Cheese (pin-path try-guard, join-count undercount) and Trail (silent rules-read, invalid-widget-id guard, overwrite warning, non-ASCII slug) + a doc note. No new modules.

## Global Constraints

- Provenance never fabricated (Cheese/Trail invariant) — the join-count fix only makes an existing honest signal more accurate; it must not invent joins.
- Contamination audit + fail-closed opt-in on Trail promotion unchanged.
- Pyright strict for `maze/`, `widgets/`; `screens/` exempt. Gates per commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. `test_app_renders` env flake non-signal; `git checkout -- snapshot_report.html` if regenerated.

---

### Task 1: Cheese fixes (pin-path try-guard + join-count)

**Files:**
- Modify: `src/labrat/screens/main.py` (`_capture_finding`), `src/labrat/widgets/turn_provenance.py`
- Test: `tests/widgets/test_turn_provenance.py`, a TUI test in `tests/tui/test_cheese_capture.py`

**Fixes:**
- **F4 (pin-path try-guard):** `_capture_finding` (`main.py:748`) calls `data_store.capture` (:786) and `mgr.pin` (:800) unguarded — an OSError (disk full/permissions) raises into Textual, violating the "never raises into the TUI" docstring. Wrap the capture+pin body in `try/except Exception` → `self.notify("Couldn't save finding: <err>", severity="error")` + `return None`. (`screens/` is pyright-exempt; keep the broad except with a `# noqa: BLE001`.)
- **F5 (join-count undercount):** `TurnProvenance._join_verified: bool` (`turn_provenance.py:36`) makes `FindingProvenance.joins_verified` only ever 0/1 (:215), so N verified joins undersell. Change to a counter: `self._joins_verified: int = 0` (init), increment in `record_tool` where `verify_join` succeeds (:114), footer uses `if self._joins_verified:` (:154), the empty-turn guard uses `self._joins_verified` (:191), and `snapshot()` sets `joins_verified=self._joins_verified` (:215). The footer text may stay "join verified" (count not shown) — only the persisted provenance count changes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/widgets/test_turn_provenance.py`:

```python
def test_multiple_join_verifications_counted():
    from labrat.widgets.turn_provenance import TurnProvenance

    tp = TurnProvenance()
    tp.record_tool("verify_join", True, "")
    tp.record_tool("verify_join", True, "")
    tp.record_tool("verify_join", True, "")
    snap = tp.snapshot()
    assert snap is not None and snap.joins_verified == 3  # was capped at 1


def test_join_failure_not_counted():
    from labrat.widgets.turn_provenance import TurnProvenance

    tp = TurnProvenance()
    tp.record_tool("verify_join", False, "")  # ok=False → not a verification
    assert tp.snapshot() is None  # nothing recorded
```

TUI test in `tests/tui/test_cheese_capture.py` (follow the `_MainHost` pattern already there):

```python
async def test_capture_finding_survives_store_error(main_screen_app, tmp_path, monkeypatch):
    """A data-store OSError degrades to a notify, never raises into Textual."""
    import labrat.cheese.store as cheese_store_mod
    monkeypatch.setattr(cheese_store_mod, "DEFAULT_DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(cheese_store_mod, "DEFAULT_CHEESE_ROOT", tmp_path / "cheese")
    async with main_screen_app.run_test() as pilot:
        screen = main_screen_app.screen
        screen._last_sql = "SELECT 1 AS x"
        screen._last_user_prompt = "q"
        screen.query_one("ResultsTable").load(__import__("polars").DataFrame({"x": [1]}))
        # force capture to raise
        monkeypatch.setattr(
            "labrat.cheese.store.FindingDataStore.capture",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )
        result = screen._capture_finding(question="q", sql="SELECT 1 AS x")
        assert result is None  # degraded, did not raise
        await pilot.pause()
```

(Adapt `main_screen_app`/`ResultsTable` lookups to the real fixture used in that file — the assertions are the contract.)

- [ ] **Step 2: Run to verify failure** → the join test fails (asserts 3, gets 1); the store-error test raises OSError.

- [ ] **Step 3: Implement** per the Fixes block.

- [ ] **Step 4: Run tests** → PASS.

- [ ] **Step 5: Gates + commit** (`git add src/labrat/screens/main.py src/labrat/widgets/turn_provenance.py tests/widgets/test_turn_provenance.py tests/tui/test_cheese_capture.py`; message `"fix(cheese): pin-path try-guard + accurate join-verification count (review F4/F5)"`).

---

### Task 2: Trail fixes (rules-read hint, widget-id guard, overwrite warning, non-ASCII slug) + doc

**Files:**
- Modify: `src/labrat/maze/trail.py` (`intent_slug`), `src/labrat/screens/findings_viewer.py`, `src/labrat/screens/trail_review.py`, `docs/dab-integration.md`
- Test: `tests/unit/test_trail.py`, `tests/tui/` (trail tests)

**Fixes:**
- **non-ASCII slug** (`trail.py:27` `intent_slug`): today `re.sub(r"[^a-z0-9]+", "-", q.lower())` strips accented chars, so `"Résumé analizi"` → `"r-sum-analizi"`. Normalize first: `unicodedata.normalize("NFKD", question).encode("ascii", "ignore").decode()` before the lowercasing+regex, so `"Résumé"` → `"resume"`. Keep the `"untitled-trail"` fallback only when the result is empty.
- **t3-low2 (invalid widget id):** `trail_review.py:78,91` `_FIELD_IDS.get(section.heading, section.heading.lower())` — a non-canonical heading yields an id with spaces (`"my heading"`) → invalid Textual widget id → compose crash. Sanitize the fallback: `re.sub(r"[^a-z0-9]+", "-", section.heading.lower()).strip("-") or "field"`. (Unreachable from the fixed-5-heading production draft, but a latent crash.)
- **t3-low1 (silent rules-read):** `findings_viewer.py:217` `except Exception: all_rules = []` swallows a rules-store read error silently. Add `self.notify("Couldn't load validation rules; Trail will list none.", severity="warning")` in the except (keep the `all_rules = []` fallback so drafting still proceeds).
- **overwrite warning:** in `action_save_as_trail` (findings_viewer.py, after computing the intent-slug via `intent_slug(finding.question)` — or inside `TrailReviewScreen`), check `MazeStore.from_env(profile).load_domain(slug, kind="trail", scope="project")`; if it exists, surface a warning in the review screen header ("⚠ overwrites existing Trail '<slug>'") so the analyst knows apply will replace it (replace-semantics is intended, but silent overwrite isn't). Simplest: pass an `overwrites: bool` into `TrailReviewScreen` and render the warning line when true.
- **doc note:** `docs/dab-integration.md` — add a sentence that a non-`--agent-cartograph` claude-mcp run reads the real `~/.labrat/maze/<profile>/trail/` (and `scent/`) user layer (byte-identical exposure to `search_reference_docs`; the submission path is cartograph-hermetic).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_trail.py`:

```python
def test_intent_slug_transliterates_accents():
    from labrat.maze.trail import intent_slug
    assert intent_slug("Résumé çöğ analizi") == "resume-cog-analizi"  # was r-sum-...


def test_intent_slug_empty_after_strip_falls_back():
    from labrat.maze.trail import intent_slug
    assert intent_slug("???") == "untitled-trail"
```

(For the overwrite-warning + rules-hint + widget-id, add a TUI test asserting the review screen renders the overwrite warning when a same-slug project trail exists, following the trail TUI test pattern; and a unit test that `TrailReviewScreen` composes without crashing on a doc containing a non-canonical heading. Complete these following the existing trail test fixtures — assertions are the contract.)

- [ ] **Step 2: Run to verify failure** → the transliterate test fails (`r-sum-...`).

- [ ] **Step 3: Implement** per the Fixes block (`import unicodedata`, `import re` in trail.py as needed).

- [ ] **Step 4: Run tests** → PASS.

- [ ] **Step 5: Gates + commit** (`git add src/labrat/maze/trail.py src/labrat/screens/findings_viewer.py src/labrat/screens/trail_review.py docs/dab-integration.md tests/unit/test_trail.py tests/tui/...`; message `"fix(trail): non-ASCII slug + widget-id guard + rules-read hint + overwrite warning (review minors)"`).

---

## Verification gate (after Task 2, before merge)

No dedicated pty gate needed (all covered by tests + a whole-branch review) — but confirm: `intent_slug("Résumé")` → `"resume"` live; the full suite green. The whole-branch review covers the cross-cutting check.

## Execution notes

- Branch: `feat/ticket-sweep` off master; merge after whole-branch Fable review.
- Tasks are independent (Cheese vs Trail) — order doesn't matter, but do 1 then 2 for a clean ledger.
