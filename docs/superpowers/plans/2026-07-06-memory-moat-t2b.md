# M5 Memory Moat (Foundation + T2b Correction-Harvesting v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Scent section-provenance model once (shared foundation), then wire LabRat's dormant correction extractors into a human-gated loop that promotes recurring corrections into Scent, with a schema-staleness flag.

**Architecture:** Foundation first — a `source_rank` trust ladder + widened source tokens + optional freshness metadata on `Section`. Then T2b v1: a `SessionHarvester` runs the existing extractors at a session boundary and persists memories; a promotion pass clusters correction memories by table scope and drafts `harvested`-tagged Scent bullets (contamination-audited, draft-only); a new `MazeStore` write path + an apply helper persist human-approved sections; a staleness check flags Scent docs whose schema drifted; a thin TUI shell gates approval.

**Tech Stack:** Python 3.12, Pydantic v2, pytest (`asyncio_mode=auto` — write async tests as `async def`, `await` directly; no `@pytest.mark.asyncio`), ruff, pyright strict (applies to `src/labrat/` except `dspy_opt/` and `screens/`).

## Global Constraints

- All Scent writes run through `labrat.maze.scent_audit` (`detect_contamination` / `audit_scent_doc`, fail-loud) — including harvested content.
- Harvested Scent content is **drafted then human-approved**, never auto-written/frozen.
- Harvesting is **disabled on benchmark paths** (DAB/ADE) — only the TUI/product path harvests. `SessionHarvester` must not be reachable from `src/labrat/eval/`.
- No `Date.now()`-style impurity inside pure functions: `generated_at` is passed in by callers, never generated inside promotion/serialization code.
- Back-compat: every new `Section`/source field is optional; existing serialized Scent docs must parse unchanged (round-trip test required).
- `Memory.embedding` stays unused in v1 (clustering is by `table_scope`).
- Verified anchors (use verbatim): document fns are `parse_document(text, *, domain, scope="")` / `render_document(doc)`; `MemoryStore(memory_dir=...)`; `MazeStore(project_root, home, profile)` is **read-only today** (only `docs()`/`from_env()`); `QueryEvent` requires `profile, thread_id, version_id, sql_final`; `_RECOGNIZED_SOURCES` already contains `lineage`; scent_audit exports `detect_contamination(text) -> str | None` + `ScentContaminationError`.
- Before every commit, run in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. All clean.

---

## Plan 0 — Shared foundation

### Task 1: Provenance tokens + trust-ladder ranking

**Files:**
- Create: `src/labrat/maze/provenance.py`
- Modify: `src/labrat/maze/document.py:17` (`_RECOGNIZED_SOURCES`)
- Test: `tests/unit/test_maze_provenance.py`

**Interfaces:**
- Produces: `SOURCE_TIERS: list[str]` (best→worst); `source_rank(source: str) -> int` (0 = highest; unknown → `len(SOURCE_TIERS)`); `best_source(sources: list[str]) -> str` (highest tier, `"human"` if empty).
- Modifies `document._RECOGNIZED_SOURCES` to `{"verified", "draft", "human", "harvested", "lineage", "semantic_layer"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_maze_provenance.py
from __future__ import annotations

from labrat.maze.provenance import SOURCE_TIERS, best_source, source_rank


def test_ladder_order() -> None:
    assert SOURCE_TIERS[0] == "semantic_layer"
    assert SOURCE_TIERS[-1] == "human"
    assert source_rank("lineage") < source_rank("verified") < source_rank("harvested")
    assert source_rank("harvested") < source_rank("human")


def test_unknown_source_is_lowest() -> None:
    assert source_rank("bogus") == len(SOURCE_TIERS)


def test_best_source_picks_highest_tier() -> None:
    assert best_source(["human", "harvested", "lineage"]) == "lineage"
    assert best_source([]) == "human"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_maze_provenance.py -v`
Expected: FAIL — `No module named 'labrat.maze.provenance'`.

- [ ] **Step 3: Implement**

```python
# src/labrat/maze/provenance.py
"""Scent source-tier trust ladder (Anthropic 'provenance footer' ordering).

semantic_layer > lineage > verified > harvested > draft > human. Consumed by the
future T3c provenance footer and any code that must pick the most-trustworthy
source among a doc's sections.
"""

from __future__ import annotations

SOURCE_TIERS: list[str] = [
    "semantic_layer",
    "lineage",
    "verified",
    "harvested",
    "draft",
    "human",
]


def source_rank(source: str) -> int:
    """0 = highest trust; unknown tokens rank lowest."""
    try:
        return SOURCE_TIERS.index(source)
    except ValueError:
        return len(SOURCE_TIERS)


def best_source(sources: list[str]) -> str:
    """The highest-tier source in the list; 'human' if empty."""
    if not sources:
        return "human"
    return min(sources, key=source_rank)
```

Then update `document.py:17` (note `lineage` is already present — add `harvested` + `semantic_layer`):

```python
_RECOGNIZED_SOURCES = {"verified", "draft", "human", "harvested", "lineage", "semantic_layer"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_maze_provenance.py tests/unit/test_maze_document.py -v`
Expected: PASS (new provenance tests + existing document tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/provenance.py src/labrat/maze/document.py tests/unit/test_maze_provenance.py
git commit -m "feat(maze): source-tier trust ladder + harvested/semantic_layer tokens"
```

---

### Task 2: Optional freshness/version metadata on `Section`

**Files:**
- Modify: `src/labrat/maze/document.py` (`Section` model ~line 21; `render_document` ~line 128; add `_extract_meta`, wire into `_split_sections` ~line 64)
- Test: `tests/unit/test_maze_document.py` (extend existing)

**Interfaces:**
- Produces: `Section` gains optional `generated_at: str | None = None`, `schema_hash: str | None = None`, `model_id: str | None = None`, `git_sha: str | None = None`. Serialized as a `**Meta:** key=value; …` line after the `**Source:**` line when any are set; parsed back on load. Absent metadata → all None; existing docs round-trip byte-stable.

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_maze_document.py`)

```python
def test_section_metadata_round_trips() -> None:
    from labrat.maze.document import ScentDoc, Section, parse_document, render_document

    doc = ScentDoc(
        domain="sales",
        tables=["orders"],
        sections=[
            Section(
                heading="Gotchas",
                body="- Orders can be soft-deleted.",
                source="harvested",
                generated_at="2026-07-06T00:00:00Z",
                schema_hash="abc123",
                model_id="claude-sonnet-4-6",
            )
        ],
    )
    text = render_document(doc)
    reparsed = parse_document(text, domain="sales")
    s = next(s for s in reparsed.sections if s.heading == "Gotchas")
    assert s.source == "harvested"
    assert s.generated_at == "2026-07-06T00:00:00Z"
    assert s.schema_hash == "abc123"
    assert s.model_id == "claude-sonnet-4-6"


def test_legacy_doc_without_meta_still_parses() -> None:
    from labrat.maze.document import parse_document

    legacy = "---\ndomain: x\nkind: scent\n---\n\n## Gotchas\n**Source:** verified\n\n- Note.\n"
    doc = parse_document(legacy, domain="x")
    s = next(s for s in doc.sections if s.heading == "Gotchas")
    assert s.source == "verified"
    assert s.generated_at is None and s.schema_hash is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_maze_document.py -v`
Expected: FAIL — `Section` has no `generated_at`, etc.

- [ ] **Step 3: Implement**

3a. Extend the `Section` model (`document.py:21`):

```python
class Section(BaseModel):
    heading: str  # "" for the preamble before the first H2
    body: str
    source: str = "human"
    generated_at: str | None = None
    schema_hash: str | None = None
    model_id: str | None = None
    git_sha: str | None = None
```

3b. In `render_document`, after the `**Source:**` line (currently `parts.append(f"**Source:** {s.source}")` at ~line 128), emit a `**Meta:**` line when any metadata is set:

```python
            parts.append(f"**Source:** {s.source}")
            meta_pairs = [
                (k, v)
                for k, v in (
                    ("generated_at", s.generated_at),
                    ("schema_hash", s.schema_hash),
                    ("model_id", s.model_id),
                    ("git_sha", s.git_sha),
                )
                if v is not None
            ]
            if meta_pairs:
                parts.append("**Meta:** " + "; ".join(f"{k}={v}" for k, v in meta_pairs))
```

3c. Add a `**Meta:**` parser mirroring `_extract_source`. Near `_SOURCE_LINE_RE` (line 18) add:

```python
_META_LINE_RE = re.compile(r"^\*\*Meta:\*\*\s*(.*)$")
```

Then a helper (after `_extract_source`):

```python
_META_KEYS = ("generated_at", "schema_hash", "model_id", "git_sha")


def _extract_meta(body: str) -> tuple[dict[str, str | None], str]:
    """Lift a leading ``**Meta:** k=v; …`` line into a dict of the recognized keys.

    Mirrors _extract_source: if the first non-empty line is a Meta marker, parse it
    and return (metadata, body-without-that-line); otherwise (all-None, body unchanged).
    """
    meta: dict[str, str | None] = {k: None for k in _META_KEYS}
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == "":
            continue
        m = _META_LINE_RE.match(line.strip())
        if m is None:
            return meta, body
        for pair in m.group(1).split(";"):
            if "=" in pair:
                k, _, v = pair.partition("=")
                k = k.strip()
                if k in meta:
                    meta[k] = v.strip()
        rest = "\n".join(lines[:i] + lines[i + 1 :]).strip()
        return meta, rest
    return meta, body
```

3d. Wire it into `_split_sections` (line 64): after each `src, clean = _extract_source(...)`, call `meta, clean = _extract_meta(clean)` and pass the fields into `Section(...)`:

```python
        src, clean = _extract_source(body[start:end].strip())
        meta, clean = _extract_meta(clean)
        sections.append(
            Section(
                heading=m.group(1).strip(),
                body=clean,
                source=src,
                generated_at=meta["generated_at"],
                schema_hash=meta["schema_hash"],
                model_id=meta["model_id"],
                git_sha=meta["git_sha"],
            )
        )
```

Do the same for the preamble branch (heading="") if you choose, but the preamble has no `**Source:**`/`**Meta:**` marker in practice — apply `_extract_meta` there too for symmetry (it degrades to all-None).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_maze_document.py -v`
Expected: PASS (round-trip + legacy + all existing document tests).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/document.py tests/unit/test_maze_document.py
git commit -m "feat(maze): optional freshness/version metadata on Scent sections (back-compat)"
```

---

### Task 3: Correct the stale "does not verify" claims

**Files:**
- Modify: `docs/superpowers/specs/2026-06-01-labrat-north-star-design.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Locate the stale claims**

Run: `grep -n "does not verify\|do not verify\|does not yet verify\|no verif\|LabRat does not" docs/superpowers/specs/2026-06-01-labrat-north-star-design.md`
(Known hit: line ~212 "**LabRat does not verify.**")

- [ ] **Step 2: Edit each hit** so it reflects that K-of-N consensus + adversarial re-derive verification **shipped** (T1a, `src/labrat/agent/verification/`, merged 2026-06-25 as M1, default-off pending a larger-n ablation). Keep the competitive framing but make the status accurate — e.g. replace "LabRat does not verify. This is our #1 gap" with a sentence noting verification is now built (default-off; ablation within-noise at n=24) and the gap is now *enabling it with confidence*, not *building it*.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-01-labrat-north-star-design.md
git commit -m "docs(north-star): correct stale 'does not verify' claim (T1a shipped as M1)"
```

Note (not a plan task — out of repo): the `project_competitive_position` memory carries the same stale claim; the controller updates it via memory tooling.

---

## Plan 1 — T2b correction-harvesting v1

### Task 4: Harvest orchestrator (wire the dormant extractors)

**Files:**
- Create: `src/labrat/memory/harvest.py`
- Test: `tests/unit/test_memory_harvest.py`

**Interfaces:**
- Consumes: `EditExtractor`, `ChatCorrectionExtractor`, `LLMFn` (`memory/extractor.py`); `MemoryStore` (`memory/store.py`); `QueryEvent` (`history/events.py`); `Memory` (`memory/model.py`).
- Produces: `class SessionHarvester` with `__init__(self, profile: str, llm_fn: LLMFn, store: MemoryStore, enabled: bool = True)`; `async def harvest_events(self, events: list[QueryEvent]) -> list[Memory]`; `async def harvest_correction(self, user_message: str, context_sql: str) -> list[Memory]`. Both return `[]` when `enabled` is False.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_memory_harvest.py
from __future__ import annotations

from pathlib import Path

from labrat.history.events import QueryEvent
from labrat.memory.harvest import SessionHarvester
from labrat.memory.store import MemoryStore


async def _fake_llm(_prompt: str) -> str:
    return "Filter soft-deleted rows with deleted_at IS NULL."


def _event() -> QueryEvent:
    # QueryEvent requires profile, thread_id, version_id, sql_final.
    return QueryEvent(
        profile="p1",
        thread_id="t1",
        version_id="v1",
        sql_final="SELECT 1 WHERE deleted_at IS NULL",
        edit_diff="- SELECT 1\n+ SELECT 1 WHERE deleted_at IS NULL",
    )


async def test_harvest_events_appends_edit_memories(tmp_path: Path) -> None:
    store = MemoryStore(memory_dir=tmp_path)
    h = SessionHarvester(profile="p1", llm_fn=_fake_llm, store=store)
    mems = await h.harvest_events([_event()])
    assert len(mems) == 1
    assert "soft-deleted" in mems[0].text
    assert store.read_profile("p1")  # persisted


async def test_disabled_harvester_is_noop(tmp_path: Path) -> None:
    store = MemoryStore(memory_dir=tmp_path)
    h = SessionHarvester(profile="p1", llm_fn=_fake_llm, store=store, enabled=False)
    assert await h.harvest_events([_event()]) == []
    assert store.read_profile("p1") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_memory_harvest.py -v`
Expected: FAIL — `No module named 'labrat.memory.harvest'`.

- [ ] **Step 3: Implement**

```python
# src/labrat/memory/harvest.py
"""Wire the correction extractors into a session-boundary harvest loop (T2b v1).

The extractors already exist (memory/extractor.py) but had no callers. This runs
them on a session's events/corrections and persists the derived memories. Gated by
`enabled` so benchmark paths never harvest.
"""

from __future__ import annotations

from labrat.history.events import QueryEvent
from labrat.memory.extractor import ChatCorrectionExtractor, EditExtractor, LLMFn
from labrat.memory.model import Memory
from labrat.memory.store import MemoryStore


class SessionHarvester:
    def __init__(
        self, profile: str, llm_fn: LLMFn, store: MemoryStore, enabled: bool = True
    ) -> None:
        self._profile = profile
        self._store = store
        self._enabled = enabled
        self._edit = EditExtractor(profile, llm_fn)
        self._chat = ChatCorrectionExtractor(profile, llm_fn)

    async def harvest_events(self, events: list[QueryEvent]) -> list[Memory]:
        if not self._enabled:
            return []
        out: list[Memory] = []
        for ev in events:
            if not ev.edit_diff:
                continue
            for mem in await self._edit.extract(ev):
                self._store.append(mem)
                out.append(mem)
        return out

    async def harvest_correction(self, user_message: str, context_sql: str) -> list[Memory]:
        if not self._enabled:
            return []
        out: list[Memory] = []
        for mem in await self._chat.extract(user_message, context_sql):
            self._store.append(mem)
            out.append(mem)
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_memory_harvest.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/labrat/memory/harvest.py tests/unit/test_memory_harvest.py
git commit -m "feat(memory): SessionHarvester wires dormant extractors into a gated harvest loop"
```

---

### Task 5: Promotion pass (corrections → drafted harvested Scent bullets)

**Files:**
- Create: `src/labrat/maze/harvest.py`
- Test: `tests/unit/test_maze_harvest.py`

**Interfaces:**
- Consumes: `Memory`/`MemoryKind` (`memory/model.py`); `Section` (`maze/document.py`); `detect_contamination`/`ScentContaminationError` (`maze/scent_audit.py`).
- Produces: `cluster_corrections(memories: list[Memory]) -> dict[str, list[Memory]]` (correction kinds only, `table_scope or "__global__"`); `draft_harvested_sections(clusters, *, generated_at: str, model_id: str | None = None) -> list[Section]` (one `## Gotchas` per cluster, `source="harvested"` + metadata, deduped bullets, **audited fail-loud**, draft-only; `[]` for empty).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_maze_harvest.py
from __future__ import annotations

import pytest

from labrat.maze.harvest import cluster_corrections, draft_harvested_sections
from labrat.maze.scent_audit import ScentContaminationError
from labrat.memory.model import Memory, MemoryKind, MemoryScope


def _mem(text: str, table: str | None, kind: MemoryKind = MemoryKind.edit_derived) -> Memory:
    return Memory(profile="p", scope=MemoryScope.global_, kind=kind, text=text, table_scope=table)


def test_cluster_groups_by_table_scope() -> None:
    mems = [_mem("a", "orders"), _mem("b", "orders"), _mem("c", None)]
    clusters = cluster_corrections(mems)
    assert {m.text for m in clusters["orders"]} == {"a", "b"}
    assert clusters["__global__"][0].text == "c"


def test_cluster_ignores_non_correction_kinds() -> None:
    mems = [_mem("keep", "orders"), _mem("skip", "orders", kind=MemoryKind.explicit_user_rule)]
    clusters = cluster_corrections(mems)
    assert {m.text for m in clusters["orders"]} == {"keep"}


def test_draft_produces_harvested_gotchas_sections() -> None:
    clusters = cluster_corrections([_mem("Filter deleted_at IS NULL.", "orders")])
    sections = draft_harvested_sections(
        clusters, generated_at="2026-07-06T00:00:00Z", model_id="claude-sonnet-4-6"
    )
    assert len(sections) == 1
    s = sections[0]
    assert s.heading == "Gotchas"
    assert s.source == "harvested"
    assert s.generated_at == "2026-07-06T00:00:00Z"
    assert "- Filter deleted_at IS NULL." in s.body


def test_draft_fails_loud_on_contamination() -> None:
    # NOTE for implementer: read scent_audit.py's contamination patterns and craft a
    # text that trips detect_contamination (e.g. a reference to a ground-truth/answer-key
    # file). Confirm the exact smell token before finalizing this test.
    clusters = cluster_corrections([_mem("see ground_truth.csv for the answer", "orders")])
    with pytest.raises(ScentContaminationError):
        draft_harvested_sections(clusters, generated_at="2026-07-06T00:00:00Z")
```

Before implementing, **read `src/labrat/maze/scent_audit.py`** to confirm `detect_contamination`'s exact match tokens and adjust the last test's text so it genuinely trips the guard (do not assume `ground_truth.csv` matches — verify).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_maze_harvest.py -v`
Expected: FAIL — `No module named 'labrat.maze.harvest'`.

- [ ] **Step 3: Implement**

```python
# src/labrat/maze/harvest.py
"""Promote clustered correction memories into drafted, audited Scent sections (T2b v1).

Draft-only: the returned Sections are shown to a human for approval before any
MazeStore write. Every drafted body is contamination-audited (fail-loud).
"""

from __future__ import annotations

from labrat.maze.document import Section
from labrat.maze.scent_audit import ScentContaminationError, detect_contamination
from labrat.memory.model import Memory, MemoryKind

_CORRECTION_KINDS = {MemoryKind.edit_derived, MemoryKind.chat_correction}
_GLOBAL_KEY = "__global__"


def cluster_corrections(memories: list[Memory]) -> dict[str, list[Memory]]:
    clusters: dict[str, list[Memory]] = {}
    for m in memories:
        if m.kind not in _CORRECTION_KINDS:
            continue
        key = m.table_scope or _GLOBAL_KEY
        clusters.setdefault(key, []).append(m)
    return clusters


def draft_harvested_sections(
    clusters: dict[str, list[Memory]],
    *,
    generated_at: str,
    model_id: str | None = None,
) -> list[Section]:
    sections: list[Section] = []
    for key in sorted(clusters):
        seen: set[str] = set()
        bullets: list[str] = []
        for m in clusters[key]:
            t = m.text.strip()
            if t and t not in seen:
                seen.add(t)
                bullets.append(f"- {t}")
        if not bullets:
            continue
        body = "\n".join(bullets)
        hit = detect_contamination(body)
        if hit:
            raise ScentContaminationError(
                f"harvested draft for {key!r} tripped contamination guard: {hit}"
            )
        sections.append(
            Section(
                heading="Gotchas",
                body=body,
                source="harvested",
                generated_at=generated_at,
                model_id=model_id,
            )
        )
    return sections
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_maze_harvest.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/harvest.py tests/unit/test_maze_harvest.py
git commit -m "feat(maze): draft harvested Scent Gotchas from corrections (audited, draft-only)"
```

---

### Task 6: MazeStore write path + apply-approved helper

**Files:**
- Modify: `src/labrat/maze/store.py` (add `load_domain` + `write_doc`)
- Modify: `src/labrat/maze/harvest.py` (add `apply_approved_sections`)
- Test: `tests/unit/test_maze_store_write.py` (new); extend `tests/unit/test_maze_harvest.py`

**Interfaces:**
- Produces:
  - `MazeStore.load_domain(self, domain: str, kind: str = "scent") -> ScentDoc | None` — the merged doc for `domain` (from `docs()`), or None.
  - `MazeStore.write_doc(self, doc: ScentDoc, *, scope: str = "project", kind: str = "scent") -> Path` — render via `render_document`, write to the chosen layer's `<kind>/<domain>.md` (`mkdir -p`), return the path. Default `scope="project"` → `<project_root>/labrat_maze/<kind>/<domain>.md`.
  - `apply_approved_sections(store: MazeStore, domain: str, approved: list[Section]) -> None` (in `maze/harvest.py`) — load-or-create the domain doc, append approved sections deduped against existing bodies, persist via `write_doc`.

Read `maze/store.py` first: the layers are `_Layer("user", home/.labrat/maze/<profile>)` and `_Layer("project", project_root/labrat_maze)`; write to the layer whose `scope` matches (default project). Expose the layer roots you need (the ctor stores `self._layers`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_maze_store_write.py
from __future__ import annotations

from pathlib import Path

from labrat.maze.document import ScentDoc, Section
from labrat.maze.store import MazeStore


def _store(tmp_path: Path) -> MazeStore:
    return MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="default")


def test_write_doc_round_trips_through_docs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    doc = ScentDoc(
        domain="sales",
        tables=["orders"],
        sections=[Section(heading="Gotchas", body="- Soft deletes.", source="harvested")],
    )
    path = store.write_doc(doc)
    assert path.exists()
    loaded = store.load_domain("sales")
    assert loaded is not None
    assert any("Soft deletes." in s.body and s.source == "harvested" for s in loaded.sections)


def test_load_domain_missing_returns_none(tmp_path: Path) -> None:
    assert _store(tmp_path).load_domain("nope") is None
```

```python
# append to tests/unit/test_maze_harvest.py
def test_apply_approved_sections_writes_only_approved(tmp_path) -> None:
    from labrat.maze.document import Section
    from labrat.maze.harvest import apply_approved_sections
    from labrat.maze.store import MazeStore

    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="default")
    approved = [Section(heading="Gotchas", body="- Keep this.", source="harvested")]
    apply_approved_sections(store, domain="sales", approved=approved)
    doc = store.load_domain("sales")
    assert doc is not None
    assert any("Keep this." in s.body and s.source == "harvested" for s in doc.sections)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_maze_store_write.py tests/unit/test_maze_harvest.py -v`
Expected: FAIL — `MazeStore` has no `write_doc`/`load_domain`; `apply_approved_sections` undefined.

- [ ] **Step 3: Implement**

3a. In `maze/store.py`, add (using the existing `_layers` + `render_document`):

```python
    def load_domain(self, domain: str, kind: str = "scent") -> ScentDoc | None:
        for doc in self.docs(kind):
            if doc.domain == domain:
                return doc
        return None

    def write_doc(self, doc: ScentDoc, *, scope: str = "project", kind: str = "scent") -> Path:
        layer = next((layer for layer in self._layers if layer.scope == scope), None)
        if layer is None:
            raise ValueError(f"unknown scope: {scope!r}")
        directory = layer.root / kind
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{doc.domain}.md"
        path.write_text(render_document(doc), encoding="utf-8")
        return path
```

Add the import at the top: `from labrat.maze.document import ScentDoc, parse_document, render_document`.

3b. In `maze/harvest.py`, add (import `MazeStore` + `ScentDoc` lazily or at top — watch for import cycles; `maze/store.py` imports `document`, not `harvest`, so `harvest` importing `store` is fine):

```python
from labrat.maze.document import ScentDoc  # add to existing imports
from labrat.maze.store import MazeStore


def apply_approved_sections(store: MazeStore, domain: str, approved: list[Section]) -> None:
    """Merge human-approved harvested sections into the domain's Scent doc and persist.

    Dedups against existing section bodies so re-approving the same bullet is idempotent.
    """
    if not approved:
        return
    doc = store.load_domain(domain) or ScentDoc(domain=domain)
    existing_bodies = {s.body.strip() for s in doc.sections}
    for s in approved:
        if s.body.strip() not in existing_bodies:
            doc.sections.append(s)
            existing_bodies.add(s.body.strip())
    store.write_doc(doc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_maze_store_write.py tests/unit/test_maze_harvest.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/store.py src/labrat/maze/harvest.py tests/unit/test_maze_store_write.py tests/unit/test_maze_harvest.py
git commit -m "feat(maze): MazeStore write path + apply-approved-sections merge helper"
```

---

### Task 7: Schema-staleness flag

**Files:**
- Create: `src/labrat/maze/staleness.py`
- Test: `tests/unit/test_maze_staleness.py`

**Interfaces:**
- Produces: `schema_fingerprint(tables: dict[str, list[str]]) -> str` (sha256 hexdigest of a canonical `{table: sorted(cols)}` JSON with sorted keys); `is_stale(section_schema_hash: str | None, current_fingerprint: str) -> bool` (True when a stored hash is present and differs; None → False).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_maze_staleness.py
from __future__ import annotations

from labrat.maze.staleness import is_stale, schema_fingerprint


def test_fingerprint_is_order_independent() -> None:
    a = schema_fingerprint({"orders": ["id", "total"], "users": ["id"]})
    b = schema_fingerprint({"users": ["id"], "orders": ["total", "id"]})
    assert a == b


def test_staleness_detection() -> None:
    fp = schema_fingerprint({"orders": ["id", "total"]})
    assert is_stale(fp, fp) is False
    assert is_stale("oldhash", fp) is True
    assert is_stale(None, fp) is False  # no baseline → not flagged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_maze_staleness.py -v`
Expected: FAIL — `No module named 'labrat.maze.staleness'`.

- [ ] **Step 3: Implement**

```python
# src/labrat/maze/staleness.py
"""Detect when a Scent doc's derived skeleton drifted from the live schema (T2b v1)."""

from __future__ import annotations

import hashlib
import json


def schema_fingerprint(tables: dict[str, list[str]]) -> str:
    canonical = {t: sorted(cols) for t, cols in tables.items()}
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def is_stale(section_schema_hash: str | None, current_fingerprint: str) -> bool:
    if section_schema_hash is None:
        return False
    return section_schema_hash != current_fingerprint
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_maze_staleness.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/staleness.py tests/unit/test_maze_staleness.py
git commit -m "feat(maze): schema-fingerprint staleness detection for Scent docs"
```

---

### Task 8: TUI harvest+approval wiring (thin shell over tested helpers)

**Files:**
- Modify: `src/labrat/screens/main.py` (or the `src/labrat/thread/` close path) — construct a `SessionHarvester` (enabled from a profile setting; **disabled in any non-TUI/benchmark context**), call `harvest_events`/`harvest_correction` at thread-close, and add a "harvest corrections" action that runs `cluster_corrections` → `draft_harvested_sections` and shows the drafted diff for approval.
- Create: `src/labrat/screens/harvest_review.py` — a Textual screen listing drafted harvested bullets with per-bullet approve/reject; approved bullets flow to `apply_approved_sections`.
- Test: `tests/unit/test_harvest_wiring.py` — headless test of the non-Textual glue. `screens/` is pyright-strict-exempt, so keep logic in the already-tested helpers (Tasks 4–6), not in widget callbacks.

**Interfaces:**
- Consumes: `SessionHarvester` (Task 4), `cluster_corrections`/`draft_harvested_sections`/`apply_approved_sections` (Tasks 5–6), `is_stale`/`schema_fingerprint` (Task 7).

Because the tested surface already exists (Tasks 4–6), this task's unit test targets a small orchestration helper, and the Textual screen is a shell over it. If you find no clean seam in `screens/main.py`, add a `src/labrat/screens/harvest_controller.py` with a pure helper:

```python
def review_corrections(memories, *, generated_at, model_id=None):
    """clusters -> drafted sections, ready for the approval UI."""
    from labrat.maze.harvest import cluster_corrections, draft_harvested_sections
    return draft_harvested_sections(
        cluster_corrections(memories), generated_at=generated_at, model_id=model_id
    )
```

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_harvest_wiring.py
from __future__ import annotations

from pathlib import Path

from labrat.maze.document import Section
from labrat.maze.harvest import apply_approved_sections
from labrat.maze.store import MazeStore
from labrat.memory.model import Memory, MemoryKind, MemoryScope


def test_review_then_apply_writes_only_approved(tmp_path: Path) -> None:
    from labrat.screens.harvest_controller import review_corrections

    mems = [
        Memory(
            profile="p",
            scope=MemoryScope.global_,
            kind=MemoryKind.edit_derived,
            text="Filter deleted_at IS NULL.",
            table_scope="sales",
        )
    ]
    drafted = review_corrections(mems, generated_at="2026-07-06T00:00:00Z")
    assert drafted and drafted[0].source == "harvested"

    # Simulate a human approving the first bullet only.
    approved = [drafted[0]]
    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="default")
    apply_approved_sections(store, domain="sales", approved=approved)
    doc = store.load_domain("sales")
    assert doc is not None
    assert any("deleted_at IS NULL" in s.body for s in doc.sections)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_harvest_wiring.py -v`
Expected: FAIL — `harvest_controller` / `review_corrections` undefined.

- [ ] **Step 3: Implement** `review_corrections` in `src/labrat/screens/harvest_controller.py` (as above). Then wire the Textual screen `harvest_review.py` and the thread-close trigger in `main.py` as thin shells calling `SessionHarvester` and these helpers. **Gate the harvester `enabled` flag off whenever there is no interactive TUI session** (benchmark/headless). Keep all non-trivial logic in the tested helpers.

- [ ] **Step 4: Run tests + manual TUI check**

Run: `uv run pytest tests/unit/test_harvest_wiring.py -v`
Expected: PASS.
Manual (optional, per `TESTING.md`, against `tests/fixtures/sample_dbs/ecommerce.duckdb`): open the TUI, make a correction, trigger "harvest corrections", confirm the drafted diff appears and approving writes the bullet into the Scent doc. (Controller runs autonomously — note in the report if the manual check was skipped.)

- [ ] **Step 5: Commit**

```bash
git add src/labrat/screens/main.py src/labrat/screens/harvest_review.py src/labrat/screens/harvest_controller.py tests/unit/test_harvest_wiring.py
git commit -m "feat(tui): human-gated harvest-review wiring corrections into Scent"
```

---

### Task 9: Full regression + gates + decisions.md

**Files:** Modify `decisions.md` (dated entry).

- [ ] **Step 1:** `uv run ruff format . && uv run ruff check . && uv run pyright` — all clean.
- [ ] **Step 2:** `uv run pytest -q` — all pass, no regressions (baseline ~1061 + new tests).
- [ ] **Step 3:** Confirm benchmark paths do not harvest — `grep -rn "SessionHarvester" src/labrat/eval/` returns nothing.
- [ ] **Step 4:** Add a `decisions.md` entry dated 2026-07-06 documenting: the Scent-provenance foundation (`source_rank` ladder + `harvested`/`semantic_layer` tokens + optional `Section` metadata, back-compat), T2b v1 (SessionHarvester wires the previously-dormant extractors; promotion pass drafts audited `harvested` Gotchas; new MazeStore write path; human-gated approval; staleness fingerprint), and the invariants (draft-then-approve, benchmark-path exclusion, `embedding` unused). Note the deferred v2 items.
- [ ] **Step 5: Commit**

```bash
git add decisions.md
git commit -m "docs(decisions): M5 memory moat — provenance foundation + T2b harvesting v1"
```

---

## Self-Review

**Spec coverage (`2026-07-06-memory-moat-t2b-design.md`):**
- U1 provenance ladder (Task 1), U2 section metadata (Task 2), U3 doc-correction (Task 3). ✓
- U4 SessionHarvester (Task 4), U5 promotion pass draft+audit (Task 5), U6 MazeStore write + apply (Task 6), U7 staleness (Task 7), U8 TUI shell (Task 8), U9 regression + benchmark-exclusion + decisions (Task 9). ✓
- Draft-don't-auto-write: Task 5 returns drafts; Task 6 `apply_approved_sections` only writes approved; Task 8 gates behind approval. ✓
- Back-compat round-trip: Task 2 legacy-parse test. ✓
- `embedding` unused, clustering by `table_scope`: Task 5. ✓
- Benchmark exclusion: Tasks 4 (`enabled`), 8 (gate off headless), 9 (grep gate). ✓

**Placeholder scan:** Concrete code in every core task. Two intentional real-codebase lookups, each naming exactly what to confirm: Task 5's contamination-smell token (read `scent_audit.py`), Task 8's TUI seam (add `harvest_controller.py` if `main.py` has none). The Task 8 Textual UI is deliberately a thin shell over the unit-tested `review_corrections`/`apply_approved_sections` helpers (screens/ is pyright-exempt and hard to unit-test).

**Type consistency:** `SOURCE_TIERS`/`source_rank`/`best_source` (Task 1); `Section` optional metadata (Task 2) reused in Tasks 5, 6, 8; `SessionHarvester.harvest_events/harvest_correction` (Task 4); `cluster_corrections`/`draft_harvested_sections`/`apply_approved_sections` (Tasks 5, 6, 8); `MazeStore.load_domain`/`write_doc` (Task 6); `schema_fingerprint`/`is_stale` (Task 7). Names consistent across tasks.

---

## Follow-on plans (not in this plan)

- **T1b v2** — dbt semantic-layer / metric ingestion + `semantic_layer`-tagged Scent (column-lineage + `explain_lineage` already shipped in M3).
- **T3c + T2c** — first-connect Cartographer in the TUI + provenance footer (consumes `source_rank` + section freshness metadata from this foundation).
- **2.3 / 2.4 / 2.5** — git-versioned team memory, customer-facing evals, decision-trail harvesting.
