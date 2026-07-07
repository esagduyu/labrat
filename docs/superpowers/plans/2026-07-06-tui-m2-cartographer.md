# TUI M2 — First-Connect Cartographer (T2c) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the deterministic Cartographer pre-pass automatically on TUI connect (idempotent, profile-keyed user store), detect schema staleness, and offer — never force — a scent refresh.

**Architecture:** A pure controller `maze/first_connect.py::tui_first_connect_prepass(...)` wraps `cartograph_prepass` + a sidecar schema-fingerprint file (new helpers in `maze/staleness.py` / `maze/store.py`). `MainScreen` calls it from a background worker after the M1 agent wiring, notifies progress, and gains a confirm-gated `action_refresh_scent`. Scent becomes retrievable in chat immediately because `search_reference_docs` (already in the M1 registry) reads the same store.

**Tech Stack:** Python 3.12, Textual, DuckDB (tests), pytest (`asyncio_mode = "auto"`), ruff, pyright strict (applies to `src/labrat/maze/`; `screens/` exempt).

**Spec:** `docs/superpowers/specs/2026-07-06-tui-integration-design.md` §5. **Prerequisite: M1 merged** (`feat/tui-m1-agent-stack` — this plan assumes `MainScreen._profile_obj`, the factory wiring, and `search_reference_docs` in the chat registry).

## Global Constraints

- Branch: `feat/tui-m2-cartographer` off master (after M1 merged).
- **Deterministic-only, always:** every `cartograph_prepass` call in this plan passes `with_semantics=False` (the default) and never an `llm_fn`. LLM semantics ablated net-negative (T1c); do not add it.
- **Never auto-regenerate scent** (spec D8). Refresh is user-confirmed, and deletes only the profile's **user-scope** scent dir — the project layer (`./labrat_maze/scent`, where M3 harvest writes) must never be touched by refresh.
- Fail-open: a pre-pass failure must leave chat fully functional (warning notify only).
- The pre-pass store path MUST be `~/.labrat/maze/<profile>/scent` — exactly the user layer `MazeStore(project_root, home, profile)` reads (`home / ".labrat" / "maze" / profile` + kind `"scent"`), which is what `SearchReferenceDocsTool` resolves via `MazeStore.from_env(profile=ctx.profile_name)`. A path mismatch here silently produces zero retrievals.
- Repo gates before every commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.

---

## File Structure

- Create: `src/labrat/maze/first_connect.py` (pure controller, no Textual imports).
- Modify: `src/labrat/maze/staleness.py` (catalog fingerprint + sidecar I/O), `src/labrat/maze/store.py` (`user_scent_dir` helper), `src/labrat/screens/main.py` (worker, action, binding), `src/labrat/screens/help.py`, `TESTING.md`, `decisions.md`.
- Tests: `tests/unit/test_staleness_catalog.py`, `tests/unit/test_first_connect.py`, `tests/tui/test_main_screen_scent.py`.

---

### Task 1: Catalog fingerprint + sidecar helpers

**Files:**
- Modify: `src/labrat/maze/staleness.py`, `src/labrat/maze/store.py`
- Test: `tests/unit/test_staleness_catalog.py`

**Interfaces:**
- Consumes: `schema_fingerprint(tables: dict[str, list[str]]) -> str`, `is_stale(section_schema_hash: str | None, current_fingerprint: str) -> bool` (both exist in `staleness.py`); `Catalog` model (`catalog.schemas: list[Schema]`, `schema.tables: list[Table]`, `table.name`, `table.columns: list[Column]`, `column.name`).
- Produces (Tasks 2–3 use these):
  - `staleness.fingerprint_from_catalog(catalog: Catalog) -> str`
  - `staleness.read_scent_fingerprint(scent_dir: Path) -> str | None`
  - `staleness.write_scent_fingerprint(scent_dir: Path, fingerprint: str) -> None`
  - `store.user_scent_dir(profile: str, home: Path | None = None) -> Path`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_staleness_catalog.py
"""Catalog-level fingerprint + sidecar file for TUI first-connect staleness."""

from pathlib import Path

from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.maze.staleness import (
    fingerprint_from_catalog,
    read_scent_fingerprint,
    schema_fingerprint,
    write_scent_fingerprint,
)
from labrat.maze.store import user_scent_dir


def _catalog(cols: list[str]) -> Catalog:
    return Catalog(
        schemas=[
            Schema(
                name="main",
                tables=[
                    Table(name="orders", columns=[Column(name=c) for c in cols]),
                ],
            )
        ]
    )


def test_fingerprint_from_catalog_matches_dict_form() -> None:
    cat = _catalog(["id", "amount"])
    assert fingerprint_from_catalog(cat) == schema_fingerprint({"orders": ["id", "amount"]})


def test_fingerprint_changes_when_schema_changes() -> None:
    assert fingerprint_from_catalog(_catalog(["id"])) != fingerprint_from_catalog(
        _catalog(["id", "new_col"])
    )


def test_sidecar_round_trip(tmp_path: Path) -> None:
    assert read_scent_fingerprint(tmp_path) is None
    write_scent_fingerprint(tmp_path, "abc123")
    assert read_scent_fingerprint(tmp_path) == "abc123"


def test_user_scent_dir_matches_mazestore_user_layer(tmp_path: Path) -> None:
    # MUST equal MazeStore's user layer + "scent" kind dir — the retrieval seam.
    assert user_scent_dir("prof1", home=tmp_path) == (
        tmp_path / ".labrat" / "maze" / "prof1" / "scent"
    )
```

Note: `Column`/`Table`/`Schema`/`Catalog` may have required fields beyond `name`/`columns`/`tables`/`schemas` (e.g. `data_type` on `Column`). Read `src/labrat/db/catalog.py` first and fill required fields in `_catalog` accordingly — do not change the models.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_staleness_catalog.py -v`
Expected: FAIL — `ImportError` on the new names.

- [ ] **Step 3: Implement**

Append to `src/labrat/maze/staleness.py`:

```python
_FINGERPRINT_FILE = ".schema_fingerprint"


def fingerprint_from_catalog(catalog: "Catalog") -> str:
    """Fingerprint an introspected Catalog (all schemas' tables + column names)."""
    tables: dict[str, list[str]] = {}
    for schema in catalog.schemas:
        for table in schema.tables:
            tables[table.name] = [c.name for c in table.columns]
    return schema_fingerprint(tables)


def read_scent_fingerprint(scent_dir: Path) -> str | None:
    """Read the sidecar fingerprint written at pre-pass time (None if absent)."""
    path = scent_dir / _FINGERPRINT_FILE
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def write_scent_fingerprint(scent_dir: Path, fingerprint: str) -> None:
    """Persist the catalog fingerprint next to the generated scent docs."""
    scent_dir.mkdir(parents=True, exist_ok=True)
    (scent_dir / _FINGERPRINT_FILE).write_text(fingerprint + "\n", encoding="utf-8")
```

Use a `TYPE_CHECKING` import for `Catalog` (`from labrat.db.catalog import Catalog`) to keep the module light; add `from pathlib import Path` if missing.

Append to `src/labrat/maze/store.py`:

```python
def user_scent_dir(profile: str, home: Path | None = None) -> Path:
    """The user-scope scent directory for *profile* — MazeStore's user layer + 'scent'.

    Single source of truth for the TUI pre-pass target: cartograph_prepass writes
    here, and SearchReferenceDocsTool reads it back via MazeStore.from_env(profile).
    """
    return (home or Path.home()) / ".labrat" / "maze" / profile / "scent"
```

- [ ] **Step 4: Run tests, gates, commit**

Run: `uv run pytest tests/unit/test_staleness_catalog.py -v` — PASS.

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/staleness.py src/labrat/maze/store.py tests/unit/test_staleness_catalog.py
git commit -m "feat(maze): catalog fingerprint + scent sidecar + user_scent_dir helper"
```

---

### Task 2: Pure first-connect controller

**Files:**
- Create: `src/labrat/maze/first_connect.py`
- Test: `tests/unit/test_first_connect.py`

**Interfaces:**
- Consumes: `cartograph_prepass(connections, catalogs, primary, scent_dir, *, with_semantics=False, ...) -> list[Path]` (from `labrat.maze.cartographer`); Task 1 helpers.
- Produces (`MainScreen` worker uses this in Task 3):
  - `PrepassOutcome` (frozen dataclass): `doc_paths: tuple[Path, ...]`, `generated: bool`, `stale: bool`
  - `async def tui_first_connect_prepass(*, connections, catalogs, primary, catalog, scent_dir) -> PrepassOutcome`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_first_connect.py
"""tui_first_connect_prepass: generate-once, reuse, staleness detection."""

from pathlib import Path

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.first_connect import tui_first_connect_prepass
from labrat.maze.staleness import read_scent_fingerprint, write_scent_fingerprint


def _connect(db_path: Path) -> tuple[DuckDBConnection, object]:
    conn = DuckDBConnection(path=str(db_path), read_only=True)
    conn.connect()
    return conn, conn.introspect_catalog()


async def test_first_contact_generates_docs_and_sidecar(
    ecommerce_db: Path, tmp_path: Path
) -> None:
    conn, catalog = _connect(ecommerce_db)
    scent_dir = tmp_path / "scent"
    outcome = await tui_first_connect_prepass(
        connections={"main": conn},
        catalogs={"main": catalog},
        primary="main",
        catalog=catalog,
        scent_dir=scent_dir,
    )
    assert outcome.generated is True
    assert outcome.stale is False
    assert len(outcome.doc_paths) >= 1
    assert all(p.suffix == ".md" for p in outcome.doc_paths)
    assert read_scent_fingerprint(scent_dir) is not None


async def test_second_connect_reuses_and_is_fresh(ecommerce_db: Path, tmp_path: Path) -> None:
    conn, catalog = _connect(ecommerce_db)
    scent_dir = tmp_path / "scent"
    first = await tui_first_connect_prepass(
        connections={"main": conn}, catalogs={"main": catalog}, primary="main",
        catalog=catalog, scent_dir=scent_dir,
    )
    second = await tui_first_connect_prepass(
        connections={"main": conn}, catalogs={"main": catalog}, primary="main",
        catalog=catalog, scent_dir=scent_dir,
    )
    assert second.generated is False
    assert second.stale is False
    assert set(second.doc_paths) == set(first.doc_paths)


async def test_stale_when_fingerprint_mismatches(ecommerce_db: Path, tmp_path: Path) -> None:
    conn, catalog = _connect(ecommerce_db)
    scent_dir = tmp_path / "scent"
    await tui_first_connect_prepass(
        connections={"main": conn}, catalogs={"main": catalog}, primary="main",
        catalog=catalog, scent_dir=scent_dir,
    )
    write_scent_fingerprint(scent_dir, "stale-fingerprint")  # simulate schema drift
    outcome = await tui_first_connect_prepass(
        connections={"main": conn}, catalogs={"main": catalog}, primary="main",
        catalog=catalog, scent_dir=scent_dir,
    )
    assert outcome.generated is False
    assert outcome.stale is True


async def test_missing_sidecar_on_existing_docs_is_not_stale(
    ecommerce_db: Path, tmp_path: Path
) -> None:
    # Docs generated by older tooling without a sidecar: is_stale(None, ...) is False.
    conn, catalog = _connect(ecommerce_db)
    scent_dir = tmp_path / "scent"
    await tui_first_connect_prepass(
        connections={"main": conn}, catalogs={"main": catalog}, primary="main",
        catalog=catalog, scent_dir=scent_dir,
    )
    (scent_dir / ".schema_fingerprint").unlink()
    outcome = await tui_first_connect_prepass(
        connections={"main": conn}, catalogs={"main": catalog}, primary="main",
        catalog=catalog, scent_dir=scent_dir,
    )
    assert outcome.stale is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_first_connect.py -v`
Expected: FAIL — `ModuleNotFoundError: labrat.maze.first_connect`.

- [ ] **Step 3: Implement the controller**

```python
# src/labrat/maze/first_connect.py
"""TUI first-connect Cartographer controller (T2c).

Pure async glue — no Textual imports — so the whole connect-time policy is
unit-testable: run the (idempotent) deterministic pre-pass, stamp a sidecar
schema fingerprint on generation, and report staleness on reuse. The TUI
worker owns notifications; this module owns the decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from labrat.maze.cartographer import cartograph_prepass
from labrat.maze.staleness import (
    fingerprint_from_catalog,
    is_stale,
    read_scent_fingerprint,
    write_scent_fingerprint,
)

if TYPE_CHECKING:
    from labrat.db.catalog import Catalog


@dataclass(frozen=True)
class PrepassOutcome:
    doc_paths: tuple[Path, ...]
    generated: bool  # True when this call authored the docs (first contact)
    stale: bool      # True when existing docs' fingerprint mismatches the live catalog


async def tui_first_connect_prepass(
    *,
    connections: dict[str, object],
    catalogs: dict[str, object],
    primary: str,
    catalog: Catalog,
    scent_dir: Path,
) -> PrepassOutcome:
    """Deterministic-only pre-pass + staleness check. Never regenerates on its own.

    Semantics stays off by construction (T1c ablated net-negative); refresh is a
    separate, user-confirmed action that deletes ``scent_dir`` before calling
    this again.
    """
    current = fingerprint_from_catalog(catalog)
    had_docs = scent_dir.exists() and any(scent_dir.glob("*.md"))

    doc_paths = await cartograph_prepass(
        connections, catalogs, primary, scent_dir, with_semantics=False
    )

    if had_docs:
        stored = read_scent_fingerprint(scent_dir)
        return PrepassOutcome(
            doc_paths=tuple(doc_paths), generated=False, stale=is_stale(stored, current)
        )

    write_scent_fingerprint(scent_dir, current)
    return PrepassOutcome(doc_paths=tuple(doc_paths), generated=True, stale=False)
```

Note the import location: `cartograph_prepass` lives in `labrat.maze.cartographer` (verified 2026-07-06: `grep -ln "async def cartograph_prepass" src/labrat/maze/*.py` → `cartographer.py`); the harvest helpers (`cluster_corrections` etc.) live in `maze/harvest.py`.

- [ ] **Step 4: Run tests, gates, commit**

Run: `uv run pytest tests/unit/test_first_connect.py -v` — 4 PASS.

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/first_connect.py tests/unit/test_first_connect.py
git commit -m "feat(maze): tui_first_connect_prepass — idempotent prepass + staleness outcome"
```

---

### Task 3: MainScreen worker + refresh action + help

**Files:**
- Modify: `src/labrat/screens/main.py`, `src/labrat/screens/help.py`
- Test: `tests/tui/test_main_screen_scent.py`

**Interfaces:**
- Consumes: `tui_first_connect_prepass`, `PrepassOutcome` (Task 2); `user_scent_dir` (Task 1); `ConfirmScreen(question) -> ModalScreen[bool]`; M1's `MainScreen._profile_obj` / connected-wiring block.
- Produces: `MainScreen._scent_stale: bool` and `MainScreen._scent_dir: Path | None` (M4's provenance footer reads `_scent_stale`); `action_refresh_scent`; constructor param `scent_dir: Path | None = None` (test override).

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_main_screen_scent.py
"""First-connect Cartographer wiring on MainScreen."""

import shutil
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.profile.model import Profile
from labrat.screens.main import MainScreen


class _Host(App[None]):
    def __init__(self, screen: MainScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _screen(ecommerce_db: Path, scent_dir: Path) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    return MainScreen(
        profile="scentprof",
        dialect="duckdb",
        catalog=catalog,
        connection=conn,
        profile_obj=Profile(name="scentprof", dialect="duckdb", path=str(ecommerce_db)),
        scent_dir=scent_dir,
    )


async def test_mount_runs_prepass_into_scent_dir(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    scent_dir = tmp_path / "scent"
    async with _Host(_screen(ecommerce_db, scent_dir)).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        assert any(scent_dir.glob("*.md"))
        assert (scent_dir / ".schema_fingerprint").exists()
        screen = pilot.app.screen
        assert screen._scent_stale is False


async def test_refresh_scent_regenerates(ecommerce_db: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    scent_dir = tmp_path / "scent"
    async with _Host(_screen(ecommerce_db, scent_dir)).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        before = sorted(scent_dir.glob("*.md"))
        assert before
        # Simulate drift, then refresh via the action's confirmed path.
        (scent_dir / ".schema_fingerprint").write_text("stale\n")
        pilot.app.screen._do_refresh_scent()  # the post-confirm entry point
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        assert sorted(scent_dir.glob("*.md"))  # regenerated
        assert (scent_dir / ".schema_fingerprint").read_text().strip() != "stale"
```

(`workers.wait_for_complete` — verify the exact Textual API on this repo's pinned version: `pilot.app.workers.wait_for_complete()` is the current name; if absent, poll `while pilot.app.workers: await pilot.pause(0.05)`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_main_screen_scent.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'scent_dir'`.

- [ ] **Step 3: Implement on MainScreen**

(a) `__init__`: add param `scent_dir: Path | None = None` (add `from pathlib import Path` import at top-level of `main.py` — it has none today); store `self._scent_dir_override = scent_dir`; init `self._scent_stale = False` and `self._scent_dir: Path | None = None`.

(b) At the END of the connected branch of `on_mount` (after the M1 agent wiring), resolve the dir and start the worker:

```python
        from labrat.maze.store import user_scent_dir

        self._scent_dir = self._scent_dir_override or user_scent_dir(profile_obj.name)
        self._run_scent_prepass()
```

(c) The worker + refresh entry points (new methods on `MainScreen`):

```python
    @work(exclusive=True, group="scent")
    async def _run_scent_prepass(self) -> None:
        from labrat.maze.first_connect import tui_first_connect_prepass

        if self._connection is None or self._catalog is None or self._scent_dir is None:
            return
        self.notify("🗺 mapping schema (Cartographer)…", timeout=3)
        try:
            outcome = await tui_first_connect_prepass(
                connections={"main": self._connection},
                catalogs={"main": self._catalog},
                primary="main",
                catalog=self._catalog,
                scent_dir=self._scent_dir,
            )
        except Exception as exc:  # fail-open: chat works without scent
            self.notify(f"Cartographer pre-pass failed (chat unaffected): {exc}",
                        severity="warning", timeout=8)
            return
        self._scent_stale = outcome.stale
        if outcome.generated:
            self.notify(f"scent ready · {len(outcome.doc_paths)} docs", timeout=4)
        elif outcome.stale:
            self.notify(
                "schema changed since scent was mapped — refresh with Ctrl+Shift+M",
                severity="warning", timeout=8,
            )

    def action_refresh_scent(self) -> None:
        from labrat.screens.confirm import ConfirmScreen

        if self._scent_dir is None:
            self.notify("No scent store for this session.", severity="warning")
            return

        def _after(confirmed: bool | None) -> None:
            if confirmed:
                self._do_refresh_scent()

        self.app.push_screen(
            ConfirmScreen(
                "[bold]Regenerate scent?[/bold]\n\n"
                "Deletes and re-maps this profile's user-scope scent docs.\n"
                "[dim]Project-scope docs (incl. harvested sections) are untouched.[/dim]"
            ),
            _after,
        )

    def _do_refresh_scent(self) -> None:
        import shutil

        assert self._scent_dir is not None
        if self._scent_dir.exists():
            shutil.rmtree(self._scent_dir)  # user-scope Cartographer output only
        self._scent_stale = False
        self._run_scent_prepass()
```

(d) BINDINGS: add `Binding("ctrl+shift+m", "refresh_scent", "Refresh Scent", show=False)`.
(e) `screens/help.py` Session section: `("Ctrl+Shift+M", "Regenerate scent docs (after schema changes)"),`.
Terminal caveat: `ctrl+shift+…` chords aren't delivered by all terminals — the manual gate (Task 4) checks this; the action also remains reachable programmatically. If the target terminal swallows it, rebind to `f6` and update help + TESTING.md.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_main_screen_scent.py tests/tui -v`
Expected: new tests PASS; all existing TUI tests (incl. M1's wiring tests and snapshots) PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/screens/main.py src/labrat/screens/help.py tests/tui/test_main_screen_scent.py
git commit -m "feat(tui): first-connect Cartographer pre-pass + staleness notify + refresh action"
```

---

### Task 4: Docs + manual gate + finish

**Files:**
- Modify: `TESTING.md`, `decisions.md`

- [ ] **Step 1: TESTING.md section**

```markdown
## M2 — first-connect Cartographer (manual gate)

1. Delete `~/.labrat/maze/<profile>/scent` if present. Launch `uv run labrat` → expect
   "🗺 mapping schema…" then "scent ready · N docs" toasts; verify `~/.labrat/maze/<profile>/scent/*.md`
   plus `.schema_fingerprint` exist.
2. Relaunch → no "scent ready" toast (idempotent reuse, no stale warning).
3. Ask in chat: "any reference notes on the orders table?" → expect a `search_reference_docs`
   trace and content drawn from the generated docs.
4. Add a column to the DB (or edit `.schema_fingerprint` to garbage), relaunch → expect the
   "schema changed … Ctrl+Shift+M" warning. Press Ctrl+Shift+M → confirm → docs regenerate.
   If Ctrl+Shift+M does not register in your terminal, note it — rebind to F6 per the plan.
5. Create `./labrat_maze/scent/manual-note.md` (any heading/body), run refresh → the project-scope
   file must be untouched.
```

- [ ] **Step 2: decisions.md entry**

```markdown
## 2026-07-XX — TUI M2: first-connect Cartographer (T2c)

Connect-time deterministic pre-pass into the user store (`~/.labrat/maze/<profile>/scent`),
idempotent via cartograph_prepass's existing-docs cache; sidecar `.schema_fingerprint` enables
detect-and-offer staleness (never auto-regenerate — user-scope dir only, project layer preserved).
Controller is pure (`maze/first_connect.py`); the screen only notifies. Semantics stays off (T1c).
```

- [ ] **Step 3: Full gates, manual gate, finish**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add TESTING.md decisions.md
git commit -m "docs: M2 manual gate + decisions entry"
```

Run the TESTING.md M2 manual gate (or hand to the human if no TTY). Then use superpowers:finishing-a-development-branch.
