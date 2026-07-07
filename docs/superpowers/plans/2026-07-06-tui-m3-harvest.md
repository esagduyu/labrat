# TUI M3 — M5 Harvest Surface (capture → review → apply) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the shipped M5 harvest machinery its first production caller: capture correction candidates during a TUI session (zero LLM cost), harvest + review them on demand (manual action + thread-switch prompt), route drafted sections to per-table domains, and apply approved sections to the Scent store — audited, fail-loud.

**Architecture:** A pure `CorrectionBuffer` (`memory/correction_buffer.py`) accumulates chat-correction pairs and editor-edit events. Extractors learn to set `Memory.table_scope` (sqlglot table extraction vs the catalog); `draft_harvested_sections` becomes domain-keyed (`dict[str, list[Section]]`). A new `HarvestReviewScreen` renders drafts for per-row approval and calls `apply_approved_sections` per domain. `MainScreen` wires capture seams, `ctrl+shift+h`, and the thread-switch prompt; gating is `harvesting_enabled(is_interactive=True, profile_opt_in=profile.harvest_opt_in)`.

**Tech Stack:** Python 3.12, Textual, sqlglot, Pydantic, pytest (`asyncio_mode = "auto"`), ruff, pyright strict (applies to `src/labrat/memory/` and `src/labrat/maze/`; `screens/` exempt).

**Spec:** `docs/superpowers/specs/2026-07-06-tui-integration-design.md` §6. **Prerequisites: M1 merged** (`Profile.harvest_opt_in`, `MainScreen._profile_obj`/`._provider`, SettingsScreen); M2 is NOT required (no dependency).

## Global Constraints

- Branch: `feat/tui-m3-harvest` off master (after M1 merged).
- **Fail-closed harvesting:** `SessionHarvester` is only ever constructed with `enabled=harvesting_enabled(is_interactive=True, profile_opt_in=…)`. No benchmark/headless path may reach these seams (they are all inside `screens/`).
- **Fail-loud writes:** `apply_approved_sections` audits before writing and raises `ScentContaminationError`; the review screen must surface the error and write nothing. Never catch-and-continue around the audit.
- Capture is free: no LLM call may happen before the user triggers harvest. Extractor LLM calls happen only inside the harvest worker.
- Quit must never block on harvest (no quit-time modal).
- `cluster_corrections` keys by `m.table_scope or "__global__"` — the `__global__` cluster maps to the domain doc `general`.
- Repo gates before every commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.

---

## File Structure

- Create: `src/labrat/memory/correction_buffer.py`, `src/labrat/screens/harvest_review.py`.
- Modify: `src/labrat/memory/extractor.py` (table_scope), `src/labrat/memory/harvest.py` (known_tables passthrough), `src/labrat/maze/harvest.py` (domain-keyed drafts), `src/labrat/screens/harvest_controller.py` (return type + domain mapping), `src/labrat/widgets/chat_panel.py` (UserMessage), `src/labrat/screens/main.py` (capture + action + prompt), `src/labrat/screens/help.py`, `TESTING.md`, `decisions.md`.
- Tests: `tests/unit/test_correction_buffer.py`, `tests/unit/test_extractor_table_scope.py`, `tests/unit/test_maze_harvest.py` (update), `tests/unit/test_harvest_wiring.py` (update), `tests/tui/test_harvest_review_screen.py`, `tests/tui/test_main_screen_harvest.py`.

---

### Task 1: `CorrectionBuffer`

**Files:**
- Create: `src/labrat/memory/correction_buffer.py`
- Test: `tests/unit/test_correction_buffer.py`

**Interfaces:**
- Consumes: `QueryEvent` (`labrat.history.events` — fields: `timestamp, profile, thread_id, version_id, sql_final, sql_initial, edit_diff, …`).
- Produces (Task 5 uses these):
  - `ChatCorrection` (frozen dataclass): `user_message: str`, `context_sql: str`
  - `CorrectionBuffer` with `add_chat(user_message, context_sql)`, `add_edit(*, profile, thread_id, draft_sql, executed_sql) -> bool` (False when SQL identical → nothing recorded), `pending_count: int` property, `drain() -> tuple[list[ChatCorrection], list[QueryEvent]]` (empties the buffer).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_correction_buffer.py
"""Session-scoped, LLM-free capture of correction candidates."""

from labrat.memory.correction_buffer import ChatCorrection, CorrectionBuffer


def test_add_chat_and_drain() -> None:
    buf = CorrectionBuffer()
    buf.add_chat("no, exclude refunds", "SELECT sum(amount) FROM orders")
    assert buf.pending_count == 1
    chats, edits = buf.drain()
    assert chats == [ChatCorrection("no, exclude refunds", "SELECT sum(amount) FROM orders")]
    assert edits == []
    assert buf.pending_count == 0


def test_add_edit_builds_query_event_with_diff() -> None:
    buf = CorrectionBuffer()
    recorded = buf.add_edit(
        profile="p1",
        thread_id="t1",
        draft_sql="SELECT * FROM orders",
        executed_sql="SELECT * FROM orders WHERE status != 'test'",
    )
    assert recorded is True
    _, edits = buf.drain()
    assert len(edits) == 1
    ev = edits[0]
    assert ev.profile == "p1"
    assert ev.sql_initial == "SELECT * FROM orders"
    assert ev.sql_final == "SELECT * FROM orders WHERE status != 'test'"
    assert ev.edit_diff and "status != 'test'" in ev.edit_diff
    assert ev.executed is True


def test_identical_sql_records_nothing() -> None:
    buf = CorrectionBuffer()
    recorded = buf.add_edit(
        profile="p1", thread_id="t1",
        draft_sql="SELECT 1", executed_sql="SELECT 1",
    )
    assert recorded is False
    assert buf.pending_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_correction_buffer.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/labrat/memory/correction_buffer.py
"""Session-scoped correction capture for the TUI harvest flow (M5 T2b surface).

Pure bookkeeping — NO LLM calls, no I/O. The buffer accumulates cheap
candidates while the user works; SessionHarvester's extractors (which do call
an LLM) only run when the user explicitly triggers harvest-review.
"""

from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from labrat.history.events import QueryEvent


@dataclass(frozen=True)
class ChatCorrection:
    """A user chat message that followed agent-produced SQL."""

    user_message: str
    context_sql: str


@dataclass
class CorrectionBuffer:
    _chats: list[ChatCorrection] = field(default_factory=list)
    _edits: list[QueryEvent] = field(default_factory=list)

    def add_chat(self, user_message: str, context_sql: str) -> None:
        self._chats.append(ChatCorrection(user_message, context_sql))

    def add_edit(
        self, *, profile: str, thread_id: str, draft_sql: str, executed_sql: str
    ) -> bool:
        """Record an agent-draft → user-edit pair. Returns False when identical."""
        if draft_sql.strip() == executed_sql.strip():
            return False
        diff = "\n".join(
            difflib.unified_diff(
                draft_sql.splitlines(), executed_sql.splitlines(),
                fromfile="draft", tofile="executed", lineterm="",
            )
        )
        self._edits.append(
            QueryEvent(
                timestamp=datetime.now(tz=UTC),
                profile=profile,
                thread_id=thread_id,
                version_id=str(uuid.uuid4()),
                sql_final=executed_sql,
                sql_initial=draft_sql,
                edit_diff=diff,
                executed=True,
            )
        )
        return True

    @property
    def pending_count(self) -> int:
        return len(self._chats) + len(self._edits)

    def drain(self) -> tuple[list[ChatCorrection], list[QueryEvent]]:
        chats, edits = self._chats, self._edits
        self._chats, self._edits = [], []
        return chats, edits
```

- [ ] **Step 4: Run tests, gates, commit**

Run: `uv run pytest tests/unit/test_correction_buffer.py -v` — 3 PASS.

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/memory/correction_buffer.py tests/unit/test_correction_buffer.py
git commit -m "feat(memory): CorrectionBuffer — LLM-free session capture of correction candidates"
```

---

### Task 2: Extractors set `Memory.table_scope`

**Files:**
- Modify: `src/labrat/memory/extractor.py`, `src/labrat/memory/harvest.py`
- Test: `tests/unit/test_extractor_table_scope.py`

**Interfaces:**
- Consumes: `Memory.table_scope: str | None` (exists on the model, never set today); sqlglot (already a dependency — `run_sql.py` uses it).
- Produces:
  - `extractor.resolve_table_scope(sql: str, known_tables: Sequence[str]) -> str | None` — the single table referenced in *sql* that is present in *known_tables* (case-insensitive); `None` when zero or 2+ known tables match (conservative), or on parse failure.
  - `EditExtractor(profile, llm_fn, known_tables: Sequence[str] | None = None)`, `ChatCorrectionExtractor(profile, llm_fn, known_tables: Sequence[str] | None = None)` — both set `table_scope` on every produced `Memory` (from `event.sql_final` / `context_sql`).
  - `SessionHarvester(profile, llm_fn, store, enabled=False, known_tables: Sequence[str] | None = None)` — passes through to both extractors.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_extractor_table_scope.py
"""Domain routing groundwork: extractors resolve and stamp table_scope."""

from datetime import UTC, datetime

from labrat.history.events import QueryEvent
from labrat.memory.extractor import (
    ChatCorrectionExtractor,
    EditExtractor,
    resolve_table_scope,
)

_KNOWN = ["orders", "customers", "products"]


async def _fake_llm(_prompt: str) -> str:
    return "Filter out test orders with status != 'test'."


def test_resolve_single_known_table() -> None:
    assert resolve_table_scope("SELECT * FROM orders WHERE x=1", _KNOWN) == "orders"


def test_resolve_join_of_two_known_tables_is_none() -> None:
    sql = "SELECT * FROM orders o JOIN customers c ON o.cid = c.id"
    assert resolve_table_scope(sql, _KNOWN) is None  # ambiguous → conservative None


def test_resolve_unknown_table_is_none() -> None:
    assert resolve_table_scope("SELECT * FROM staging_tmp", _KNOWN) is None


def test_resolve_unparseable_sql_is_none() -> None:
    assert resolve_table_scope("not sql at all (((", _KNOWN) is None


async def test_chat_extractor_stamps_table_scope() -> None:
    ex = ChatCorrectionExtractor("p1", _fake_llm, known_tables=_KNOWN)
    memories = await ex.extract("no — exclude test orders", "SELECT count(*) FROM orders")
    assert memories and memories[0].table_scope == "orders"


async def test_edit_extractor_stamps_table_scope() -> None:
    ex = EditExtractor("p1", _fake_llm, known_tables=_KNOWN)
    event = QueryEvent(
        timestamp=datetime.now(tz=UTC), profile="p1", thread_id="t", version_id="v",
        sql_final="SELECT * FROM orders WHERE status != 'test'",
        sql_initial="SELECT * FROM orders",
        edit_diff="+ WHERE status != 'test'",
    )
    memories = await ex.extract(event)
    assert memories and memories[0].table_scope == "orders"


async def test_no_known_tables_leaves_scope_none() -> None:
    ex = ChatCorrectionExtractor("p1", _fake_llm)  # default: no catalog
    memories = await ex.extract("fix it", "SELECT * FROM orders")
    assert memories and memories[0].table_scope is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_extractor_table_scope.py -v`
Expected: FAIL — `ImportError: resolve_table_scope` / `TypeError: unexpected keyword 'known_tables'`.

- [ ] **Step 3: Implement**

In `src/labrat/memory/extractor.py` add (top-level, after the `LLMFn` alias):

```python
import sqlglot
from sqlglot import exp


def resolve_table_scope(sql: str, known_tables: Sequence[str]) -> str | None:
    """Best-effort single-table attribution for a correction's context SQL.

    Returns the one known table the SQL references, or None when zero or
    several match (a multi-table correction gets no table_scope rather than a
    wrong one — cluster_corrections then routes it to __global__).
    """
    known = {t.lower(): t for t in known_tables}
    try:
        root = sqlglot.parse_one(sql)
    except Exception:
        return None
    if root is None:
        return None
    referenced = {t.name.lower() for t in root.find_all(exp.Table)}
    hits = [known[name] for name in referenced if name in known]
    return hits[0] if len(hits) == 1 else None
```

(`from collections.abc import Sequence` import as needed. sqlglot's `parse_one` raises `sqlglot.errors.ParseError` on garbage — the broad `except Exception` is deliberate: attribution must never crash extraction.)

Both extractors: add the constructor param and stamp the field —

```python
class EditExtractor:
    def __init__(
        self, profile: str, llm_fn: LLMFn, known_tables: Sequence[str] | None = None
    ) -> None:
        ...existing assignments...
        self._known_tables = list(known_tables) if known_tables else []
```

and where each builds `Memory(...)`, add:

```python
    table_scope=(
        resolve_table_scope(event.sql_final, self._known_tables)
        if self._known_tables
        else None
    ),
```

(for `ChatCorrectionExtractor`, resolve from `context_sql` instead of `event.sql_final`).

In `src/labrat/memory/harvest.py::SessionHarvester.__init__`, add `known_tables: Sequence[str] | None = None` and pass it to both extractor constructors.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_extractor_table_scope.py tests/unit/test_memory_harvest.py tests/unit -k "extractor or harvest" -v`
Expected: new tests PASS; all existing extractor/harvest tests PASS unchanged (the param is optional).

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/memory/extractor.py src/labrat/memory/harvest.py tests/unit/test_extractor_table_scope.py
git commit -m "feat(memory): extractors stamp Memory.table_scope (sqlglot vs known tables)"
```

---

### Task 3: Domain-keyed drafts (`draft_harvested_sections` → dict) + controller update

**Files:**
- Modify: `src/labrat/maze/harvest.py`, `src/labrat/screens/harvest_controller.py`
- Test: update `tests/unit/test_maze_harvest.py` and `tests/unit/test_harvest_wiring.py`

**Interfaces:**
- Consumes: existing `cluster_corrections`, `Section`, `detect_contamination`, `ScentContaminationError`.
- Produces (Task 4/5 use these):
  - `draft_harvested_sections(clusters, *, generated_at, model_id=None) -> dict[str, list[Section]]` — **breaking signature change**, keys are cluster keys (`table_scope` or `"__global__"`), values the drafted sections for that cluster (one per cluster today; list-valued for future headroom).
  - `harvest_controller.review_corrections(...) -> dict[str, list[Section]]` (same drafts, same clustering).
  - `harvest_controller.domain_for_cluster(key: str) -> str` — `"general"` for `"__global__"`, else the key itself.

- [ ] **Step 1: Find and list ALL callers of `draft_harvested_sections`**

Run: `grep -rn "draft_harvested_sections" src/ tests/`
Expected callers: `maze/harvest.py` (def), `screens/harvest_controller.py`, `tests/unit/test_maze_harvest.py`, `tests/unit/test_harvest_wiring.py`. If any OTHER caller appears, stop and update it too in this task.

- [ ] **Step 2: Update the tests first (failing)**

In `tests/unit/test_maze_harvest.py`, update the draft tests to the dict shape. E.g. `test_draft_produces_harvested_gotchas_sections` becomes:

```python
def test_draft_produces_domain_keyed_harvested_sections() -> None:
    clusters = cluster_corrections(
        [_mem("filter test orders", "orders"), _mem("dates are UTC", None)]
    )
    drafts = draft_harvested_sections(clusters, generated_at="2026-07-06")
    assert set(drafts) == {"orders", "__global__"}
    for sections in drafts.values():
        for s in sections:
            assert s.heading == "Gotchas"
            assert s.source == "harvested"
            assert s.generated_at == "2026-07-06"
    assert "filter test orders" in drafts["orders"][0].body
```

Keep the contamination tests (`pytest.raises(ScentContaminationError)`) — only the return-shape assertions change. In `tests/unit/test_harvest_wiring.py`, update `review_corrections` assertions the same way and add:

```python
def test_domain_for_cluster_maps_global() -> None:
    from labrat.screens.harvest_controller import domain_for_cluster

    assert domain_for_cluster("__global__") == "general"
    assert domain_for_cluster("orders") == "orders"
```

Run: `uv run pytest tests/unit/test_maze_harvest.py tests/unit/test_harvest_wiring.py -v`
Expected: updated tests FAIL (list vs dict).

- [ ] **Step 3: Implement the dict return**

In `src/labrat/maze/harvest.py::draft_harvested_sections`, change the return annotation to `dict[str, list[Section]]` and the accumulator: instead of appending each built `Section` to a flat list, do `out.setdefault(key, []).append(section)` inside the existing `for key in sorted(clusters):` loop and `return out`. The body/dedup/contamination logic is unchanged. Update the docstring: keys are cluster keys; the caller maps `__global__` → a domain via `domain_for_cluster`.

In `src/labrat/screens/harvest_controller.py`: update `review_corrections`'s return annotation to `dict[str, list[Section]]` (body unchanged — it just forwards), and add:

```python
def domain_for_cluster(key: str) -> str:
    """Map a cluster key to a Scent domain doc name (``__global__`` → ``general``)."""
    return "general" if key == "__global__" else key
```

- [ ] **Step 4: Run tests, gates, commit**

Run: `uv run pytest tests/unit/test_maze_harvest.py tests/unit/test_harvest_wiring.py -v` — PASS. Then full gates:

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/harvest.py src/labrat/screens/harvest_controller.py tests/unit/test_maze_harvest.py tests/unit/test_harvest_wiring.py
git commit -m "feat(maze): domain-keyed harvested drafts + domain_for_cluster mapping"
```

---

### Task 4: `HarvestReviewScreen`

**Files:**
- Create: `src/labrat/screens/harvest_review.py`
- Test: `tests/tui/test_harvest_review_screen.py`

**Interfaces:**
- Consumes: `dict[str, list[Section]]` drafts (Task 3), `apply_approved_sections(store, domain, approved)` (raises `ScentContaminationError`), `domain_for_cluster`, `MazeStore`.
- Produces: `HarvestReviewScreen(ModalScreen[int])` — constructor `(drafts: dict[str, list[Section]], store: MazeStore)`; dismisses with the number of applied sections (0 on cancel). Rows toggle approval with `space` or click; `Apply approved` writes per-domain; audit failure renders in `#status` and writes nothing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/tui/test_harvest_review_screen.py
"""HarvestReviewScreen: approve → apply (audited); cancel → nothing written."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.maze.document import Section
from labrat.maze.store import MazeStore
from labrat.screens.harvest_review import HarvestReviewScreen


class _Host(App[None]):
    def __init__(self, screen: HarvestReviewScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: int | None = None

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        def _cb(result: int | None) -> None:
            self.result = result

        self.push_screen(self._screen, _cb)


def _drafts() -> dict[str, list[Section]]:
    return {
        "orders": [
            Section(heading="Gotchas", body="- filter test orders",
                    source="harvested", generated_at="2026-07-06")
        ],
        "__global__": [
            Section(heading="Gotchas", body="- dates are UTC",
                    source="harvested", generated_at="2026-07-06")
        ],
    }


def _store(tmp_path: Path) -> MazeStore:
    return MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")


async def test_apply_all_approved_writes_domains(tmp_path: Path) -> None:
    store = _store(tmp_path)
    host = _Host(HarvestReviewScreen(_drafts(), store))
    async with host.run_test() as pilot:
        await pilot.pause()
        # Rows default to approved; apply everything.
        await pilot.click("#apply-btn")
        await pilot.pause()
    assert host.result == 2
    orders = store.load_domain("orders")
    assert orders is not None and "filter test orders" in orders.sections[-1].body
    general = store.load_domain("general")  # __global__ routed via domain_for_cluster
    assert general is not None and "dates are UTC" in general.sections[-1].body


async def test_cancel_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    host = _Host(HarvestReviewScreen(_drafts(), store))
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert host.result == 0
    assert store.load_domain("orders") is None
    assert store.load_domain("general") is None


async def test_contaminated_draft_fails_loud_and_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bad = {
        "orders": [
            Section(heading="Gotchas", body="- see ground_truth.csv for the answer",
                    source="harvested", generated_at="2026-07-06")
        ]
    }
    host = _Host(HarvestReviewScreen(bad, store))
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#apply-btn")
        await pilot.pause()
        # Screen stays open showing the audit error; nothing written.
        assert "contamin" in str(
            pilot.app.screen.query_one("#status").render()
        ).lower() or "answer_key" in str(pilot.app.screen.query_one("#status").render())
    assert store.load_domain("orders") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_harvest_review_screen.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement the screen**

```python
# src/labrat/screens/harvest_review.py
"""HarvestReviewScreen: human approval gate for harvested Scent sections (M5).

Drafts arrive domain-keyed; every row starts APPROVED (the human deselects).
Apply routes each approved section to its domain doc via
apply_approved_sections — which audits fail-loud BEFORE writing. A
contamination hit renders in the status line and nothing is written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static

if TYPE_CHECKING:
    from labrat.maze.document import Section
    from labrat.maze.store import MazeStore

_APPROVED = "✓ apply"
_SKIPPED = "· skip"


class HarvestReviewScreen(ModalScreen[int]):
    """Review drafted Scent sections; dismisses with the applied count."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("space", "toggle_row", "Approve/skip", show=True),
    ]

    DEFAULT_CSS = """
    HarvestReviewScreen { align: center middle; }
    HarvestReviewScreen > Vertical {
        width: 100; height: 32;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    HarvestReviewScreen #drafts-table { height: 1fr; }
    HarvestReviewScreen #actions { height: auto; margin-top: 1; }
    HarvestReviewScreen Button { margin: 0 1; min-width: 16; }
    HarvestReviewScreen #status { color: $text-muted; }
    """

    def __init__(self, drafts: dict[str, list[Section]], store: MazeStore) -> None:
        super().__init__()
        self._drafts = drafts
        self._store = store
        # Flat row model: (cluster_key, section); row key = str(index).
        self._rows: list[tuple[str, Section]] = [
            (key, s) for key in sorted(drafts) for s in drafts[key]
        ]
        self._approved: dict[int, bool] = {i: True for i in range(len(self._rows))}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "[bold]─ Harvested Learnings · review before writing to Scent ─[/bold]",
                id="title", markup=True,
            )
            yield DataTable(id="drafts-table", cursor_type="row")
            with Horizontal(id="actions"):
                yield Button("Apply approved", id="apply-btn", variant="primary")
                yield Button("Cancel  [Esc]", id="close-btn")
            yield Label("", id="status")

    def on_mount(self) -> None:
        table = self.query_one("#drafts-table", DataTable)
        table.add_columns("Apply?", "Domain", "Section", "Preview")
        from labrat.screens.harvest_controller import domain_for_cluster

        for i, (key, section) in enumerate(self._rows):
            preview = section.body.replace("\n", " ")[:60]
            table.add_row(
                _APPROVED, domain_for_cluster(key), section.heading, preview, key=str(i)
            )

    def action_toggle_row(self) -> None:
        table = self.query_one("#drafts-table", DataTable)
        if table.row_count == 0:
            return
        row = table.cursor_row
        key = table.coordinate_to_cell_key(Coordinate(row, 0)).row_key
        idx = int(str(key.value))
        self._approved[idx] = not self._approved[idx]
        table.update_cell_at(
            Coordinate(row, 0), _APPROVED if self._approved[idx] else _SKIPPED
        )

    @on(Button.Pressed, "#apply-btn")
    def action_apply(self) -> None:
        from labrat.maze.harvest import apply_approved_sections
        from labrat.maze.scent_audit import ScentContaminationError
        from labrat.screens.harvest_controller import domain_for_cluster

        by_domain: dict[str, list[Section]] = {}
        for i, (key, section) in enumerate(self._rows):
            if self._approved[i]:
                by_domain.setdefault(domain_for_cluster(key), []).append(section)
        applied = 0
        try:
            for domain, sections in sorted(by_domain.items()):
                apply_approved_sections(self._store, domain, sections)
                applied += len(sections)
        except ScentContaminationError as exc:
            # Fail-loud: show the audit verdict, write nothing further, stay open.
            self.query_one("#status", Label).update(
                f"[red]Blocked by contamination audit: {exc}[/red]"
            )
            return
        self.dismiss(applied)

    @on(Button.Pressed, "#close-btn")
    def action_cancel(self) -> None:
        self.dismiss(0)
```

Caveat: `apply_approved_sections` audits **per domain doc**, so a contamination hit in a later domain can land after an earlier domain already wrote (the audit test above uses a single domain, so nothing is written). This matches the shipped per-doc audit contract; note it in the class docstring — approved-but-unwritten domains simply remain draftable on the next harvest (apply is idempotent via body dedup).

- [ ] **Step 4: Run tests, gates, commit**

Run: `uv run pytest tests/tui/test_harvest_review_screen.py -v` — 3 PASS.

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/screens/harvest_review.py tests/tui/test_harvest_review_screen.py
git commit -m "feat(tui): HarvestReviewScreen — approve/skip drafted sections, audited apply"
```

---

### Task 5: MainScreen capture seams + harvest action + thread-switch prompt

**Files:**
- Modify: `src/labrat/widgets/chat_panel.py`, `src/labrat/screens/main.py`, `src/labrat/screens/help.py`
- Test: `tests/tui/test_main_screen_harvest.py`

**Interfaces:**
- Consumes: `CorrectionBuffer` (Task 1), `SessionHarvester(..., known_tables=...)` (Task 2), `review_corrections`/`domain_for_cluster` (Task 3), `HarvestReviewScreen` (Task 4), `harvesting_enabled`, `provider_llm_fn`, `MemoryStore`, `MazeStore.from_env`, M1's `self._provider`/`self._profile_obj`, `ConfirmScreen`.
- Produces: `ChatPanel.UserMessage(text)` message; `MainScreen._correction_buffer`; `action_harvest_review` (`ctrl+shift+h`); thread-switch prompt in `action_manage_threads._on_result`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/tui/test_main_screen_harvest.py
"""Capture seams + harvest gating on MainScreen."""

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


def _screen(ecommerce_db: Path, *, opt_in: bool) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    return MainScreen(
        profile="hprof", dialect="duckdb",
        catalog=conn.introspect_catalog(), connection=conn,
        profile_obj=Profile(
            name="hprof", dialect="duckdb", path=str(ecommerce_db), harvest_opt_in=opt_in
        ),
    )


async def test_user_message_after_sql_lands_in_buffer(
    ecommerce_db: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    screen = _screen(ecommerce_db, opt_in=True)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        screen._last_sql = "SELECT count(*) FROM orders"     # simulate a prior agent answer
        panel = pilot.app.screen.query_one("#chat-content", ChatPanel)
        panel.post_message(ChatPanel.UserMessage("no — exclude test orders"))
        await pilot.pause()
        assert screen._correction_buffer.pending_count == 1


async def test_edit_divergence_lands_in_buffer(ecommerce_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    screen = _screen(ecommerce_db, opt_in=True)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        screen._last_draft_sql = "SELECT count(*) FROM orders"
        screen._record_edit_if_diverged("SELECT count(*) FROM orders WHERE status != 'x'")
        assert screen._correction_buffer.pending_count == 1
        assert screen._last_draft_sql is None  # recorded once, then cleared


async def test_harvest_action_without_opt_in_notifies(ecommerce_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    screen = _screen(ecommerce_db, opt_in=False)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        screen.action_harvest_review()
        await pilot.pause()
        # Gated off: no HarvestReviewScreen pushed.
        assert pilot.app.screen is screen
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/tui/test_main_screen_harvest.py -v`
Expected: FAIL — `AttributeError: UserMessage` / `_correction_buffer`.

- [ ] **Step 3: Implement the capture seams**

(a) `widgets/chat_panel.py` — new message class next to the others, posted on submit:

```python
    class UserMessage(Message):
        """Posted when the user submits a chat message (for capture seams)."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text
```

and in `_on_submit`, right after `self._append_history(...)`:

```python
        self.post_message(ChatPanel.UserMessage(message))
```

(b) `screens/main.py`:
- `on_mount` (top, with the managers): `from labrat.memory.correction_buffer import CorrectionBuffer` … `self._correction_buffer = CorrectionBuffer()`; also init `self._last_draft_sql: str | None = None`.
- In the M1 `on_draft` callback add: `self._last_draft_sql = sql`.
- New handler + helper:

```python
    def on_chat_panel_user_message(self, event: object) -> None:
        from labrat.widgets.chat_panel import ChatPanel

        if not isinstance(event, ChatPanel.UserMessage):
            return
        if self._last_sql:
            self._correction_buffer.add_chat(event.text, self._last_sql)

    def _record_edit_if_diverged(self, executed_sql: str) -> None:
        if self._last_draft_sql is None:
            return
        self._correction_buffer.add_edit(
            profile=self._profile,
            thread_id=self._current_thread_id or "unknown",
            draft_sql=self._last_draft_sql,
            executed_sql=executed_sql,
        )
        self._last_draft_sql = None
```

- In `_execute_sql`, after the successful `table.load(...)` line: `self._record_edit_if_diverged(sql)`.

- [ ] **Step 4: Implement the harvest action + prompt**

```python
    def action_harvest_review(self) -> None:
        from labrat.screens.harvest_controller import harvesting_enabled

        if self._profile_obj is None or self._provider is None:
            self.notify("Connect a profile first.", severity="warning")
            return
        if not harvesting_enabled(True, self._profile_obj.harvest_opt_in):
            self.notify(
                "Harvesting is off — enable it in Settings (Ctrl+,).", severity="warning"
            )
            return
        self._run_harvest_review()

    @work(exclusive=True, group="harvest")
    async def _run_harvest_review(self) -> None:
        from datetime import UTC, datetime

        from labrat.agent.verifier import provider_llm_fn
        from labrat.maze.store import MazeStore
        from labrat.memory.harvest import SessionHarvester
        from labrat.memory.store import MemoryStore
        from labrat.screens.harvest_controller import harvesting_enabled, review_corrections
        from labrat.screens.harvest_review import HarvestReviewScreen

        assert self._profile_obj is not None and self._provider is not None
        profile_name = self._profile_obj.name
        known_tables: list[str] = []
        if self._catalog is not None:
            known_tables = [t.name for s in self._catalog.schemas for t in s.tables]

        store = MemoryStore()
        harvester = SessionHarvester(
            profile_name,
            provider_llm_fn(self._provider),
            store,
            enabled=harvesting_enabled(True, self._profile_obj.harvest_opt_in),
            known_tables=known_tables,
        )
        chats, edits = self._correction_buffer.drain()
        self.notify(
            f"Harvesting {len(chats) + len(edits)} captured corrections…", timeout=3
        )
        try:
            for chat in chats:
                await harvester.harvest_correction(chat.user_message, chat.context_sql)
            await harvester.harvest_events(edits)
        except Exception as exc:
            self.notify(f"Harvest failed: {exc}", severity="warning", timeout=8)
            return

        # Draft from the FULL memory store, not just this session: cancelled
        # reviews stay recoverable, and apply dedups so re-approval is idempotent.
        memories = store.read_profile(profile_name)
        drafts = review_corrections(
            memories,
            generated_at=datetime.now(tz=UTC).date().isoformat(),
            model_id=getattr(self._provider, "_model", None),
        )
        if not drafts:
            self.notify("No correction learnings to review yet.", timeout=4)
            return

        def _done(applied: int | None) -> None:
            if applied:
                self.notify(f"Applied {applied} section(s) to Scent.", timeout=4)

        self.app.push_screen(
            HarvestReviewScreen(drafts, MazeStore.from_env(profile=profile_name)), _done
        )
```

Thread-switch prompt — extend `action_manage_threads._on_result` (before the switch bookkeeping):

```python
        def _on_result(thread_id: str | None) -> None:
            if (
                thread_id
                and self._profile_obj is not None
                and self._profile_obj.harvest_opt_in
                and self._correction_buffer.pending_count > 0
            ):
                from labrat.screens.confirm import ConfirmScreen

                n = self._correction_buffer.pending_count

                def _maybe_harvest(confirmed: bool | None) -> None:
                    if confirmed:
                        self._run_harvest_review()

                self.app.push_screen(
                    ConfirmScreen(
                        f"[bold]{n} correction(s) captured this session.[/bold]\n\n"
                        "Review learnings before switching threads?"
                    ),
                    _maybe_harvest,
                )
            # ...existing switch bookkeeping unchanged...
```

Bindings + help:
- `Binding("ctrl+shift+h", "harvest_review", "Harvest", show=False)` on `MainScreen` (same terminal caveat as M2 — fall back to `f7` if the target terminal swallows the chord).
- `screens/help.py` Session section: `("Ctrl+Shift+H", "Review harvested learnings → write approved to Scent"),`.

- [ ] **Step 5: Run tests, gates, commit**

Run: `uv run pytest tests/tui/test_main_screen_harvest.py tests/tui tests/widgets/test_chat_panel.py -v` — new PASS, existing PASS.

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/widgets/chat_panel.py src/labrat/screens/main.py src/labrat/screens/help.py tests/tui/test_main_screen_harvest.py
git commit -m "feat(tui): correction capture + harvest-review action + thread-switch prompt"
```

---

### Task 6: Docs + manual gate + finish

**Files:**
- Modify: `TESTING.md`, `decisions.md`

- [ ] **Step 1: TESTING.md section**

```markdown
## M3 — harvest surface (manual gate)

Setup: profile with `harvest_opt_in` ON (Ctrl+, → toggle → Save). Chat needs a working provider.

1. Ask the agent a question that yields SQL; then reply "no — exclude test orders". Ask another;
   edit the drafted SQL in the editor before running it. → two capture events (no LLM calls yet;
   verify no latency).
2. Ctrl+Shift+H → "Harvesting…" toast → review modal lists drafted Gotchas rows with domains
   (table name or `general`). Toggle one row to skip (space). Apply → success toast; verify
   `./labrat_maze/scent/<domain>.md` gained a `**Source:** harvested` section; skipped row absent.
3. Re-run Ctrl+Shift+H → re-drafts appear; Apply again → doc unchanged (body dedup, idempotent).
4. With harvest_opt_in OFF: Ctrl+Shift+H → "Harvesting is off" warning, no modal, no LLM call.
5. Switch threads (Ctrl+T) with captured corrections pending → confirm prompt appears; Cancel
   proceeds with the switch, nothing harvested.
6. Ask in chat about the harvested topic → `search_reference_docs` should retrieve the new section.
```

- [ ] **Step 2: decisions.md entry**

```markdown
## 2026-07-XX — TUI M3: M5 harvest surface (first production caller)

CorrectionBuffer captures chat-correction pairs + draft-vs-executed edits for free; harvest runs
only on explicit action (Ctrl+Shift+H) or a thread-switch confirm, gated by
harvesting_enabled(interactive, Profile.harvest_opt_in) — fail-closed. Extractors now stamp
Memory.table_scope (single-known-table sqlglot attribution, conservative None on ambiguity), and
draft_harvested_sections returns domain-keyed drafts, so approved sections land in per-table Scent
docs (project scope; `__global__` → `general`) instead of one global dump. Writes stay audited
fail-loud (ScentContaminationError surfaces in the review modal, nothing written).
```

- [ ] **Step 3: Full gates, manual gate, finish**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add TESTING.md decisions.md
git commit -m "docs: M3 manual gate + decisions entry"
```

**Manual TUI verification is a named exit gate for this phase** (spec §8) — run it or hand to the human. Then use superpowers:finishing-a-development-branch.
