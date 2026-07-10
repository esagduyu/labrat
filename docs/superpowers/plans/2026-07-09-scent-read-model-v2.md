# Scent Read-Model v2 + Provenance-Rich Footer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-section merge-at-read in `MazeStore`, isolation of harvest-apply writes to the project layer (kills the I2 shadow class before T1b), additive provenance/freshness fields on `search_reference_docs` output, and a per-domain tier+freshness footer.

**Architecture:** Four seams change, one per task: `maze/store.py::docs()` unions sections per domain across layers (dedup by `body.strip()`); `maze/harvest.py::apply_approved_sections` loads/writes the project layer only; `agent/tools/search_reference_docs.py` computes `fresh`/`best_source`/`stale` inside the tool from `ctx.catalogs` + Section meta (additive Pydantic fields); `widgets/turn_provenance.py` renders `scent: <domain> (<tier>·<freshness>) +N` when structured data is present and degrades to today's count format otherwise.

**Tech Stack:** Python 3.12, Pydantic v2, pytest (`asyncio_mode="auto"`), ruff, pyright strict (`maze/`, `agent/tools/`, `widgets/` strict; `screens/` exempt).

**Spec:** `docs/superpowers/specs/2026-07-09-scent-read-model-v2-design.md` — read it before starting.

## Global Constraints

- Branch: `feat/scent-read-model-v2` off master.
- Every Scent write still passes `audit_scent_doc` (fail-loud) BEFORE hitting disk.
- Apply can never write user-layer content to the project layer (test-pinned).
- Single-layer domains: `docs()` output model-equal to today's (golden regression).
- Tool output changes are ADDITIVE ONLY — existing field names/shapes untouched.
- Footer honesty: unknown freshness never rendered as "fresh"; empty retrievals contribute nothing (ebecc9c invariant); parsing degrades, never raises.
- Deterministic-only: no LLM in any path this plan touches.
- Repo gates before every commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- Known pre-existing local env flake: `tests/tui/test_app_renders.py::test_app_renders` (fails on unmodified master, CI-skipped) — never a regression signal; restore `snapshot_report.html` via `git checkout` if a run regenerates it.

---

## File Structure

- Modify: `src/labrat/maze/store.py` (merge-at-read + scoped `load_domain`), `src/labrat/maze/harvest.py` (apply isolation), `src/labrat/agent/tools/search_reference_docs.py` (enrichment), `src/labrat/widgets/turn_provenance.py` (footer), `TESTING.md`, `decisions.md`.
- Tests: `tests/unit/test_maze_store_merge.py` (new), `tests/unit/test_maze_harvest.py` (extend), `tests/unit/test_search_reference_docs_provenance.py` (new), `tests/widgets/test_turn_provenance.py` (extend), plus existing suites unchanged.

---

### Task 1: `MazeStore` per-section merge-at-read + scoped `load_domain`

**Files:**
- Modify: `src/labrat/maze/store.py`
- Test: `tests/unit/test_maze_store_merge.py` (create)

**Interfaces:**
- Consumes: `ScentDoc(domain, kind, tables, confidence, scope, sections)`, `Section(heading, body, source, generated_at, schema_hash, model_id, git_sha)`, `parse_document`, `render_document` (all existing, `labrat.maze.document`).
- Produces (Tasks 2–3 rely on these):
  - `MazeStore.docs(kind="scent") -> list[ScentDoc]` — per-domain section UNION across layers (user first, project second; dedup by `body.strip()`; doc `scope` becomes `"merged"` when both layers contributed, else the contributing layer's scope; `tables` = sorted union; `confidence` = project value when non-None else user value).
  - `MazeStore.load_domain(domain, kind="scent", *, scope: str | None = None) -> ScentDoc | None` — `None` scope = merged view (today's callers unchanged); `"project"`/`"user"` = that single layer's parsed doc or `None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_maze_store_merge.py
"""MazeStore v2: per-section merge-at-read + scoped load_domain (I2 fix foundation)."""

from pathlib import Path

from labrat.maze.document import ScentDoc, Section, render_document
from labrat.maze.store import MazeStore


def _store(tmp_path: Path) -> MazeStore:
    return MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")


def _write(tmp_path: Path, layer: str, doc: ScentDoc) -> None:
    base = (
        tmp_path / "proj" / "labrat_maze" / "scent"
        if layer == "project"
        else tmp_path / "home" / ".labrat" / "maze" / "p1" / "scent"
    )
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{doc.domain}.md").write_text(render_document(doc), encoding="utf-8")


def _cart_doc() -> ScentDoc:
    return ScentDoc(
        domain="orders",
        tables=["orders"],
        sections=[
            Section(heading="Key Tables", body="- orders: 8 rows", source="verified"),
        ],
    )


def _harvest_doc() -> ScentDoc:
    return ScentDoc(
        domain="orders",
        tables=["orders"],
        sections=[
            Section(heading="Gotchas", body="- exclude test orders", source="harvested"),
        ],
    )


def test_single_layer_domain_reads_identically(tmp_path: Path) -> None:
    # Golden regression: a domain present in ONE layer must round-trip exactly as today.
    _write(tmp_path, "user", _cart_doc())
    docs = _store(tmp_path).docs()
    assert len(docs) == 1
    doc = docs[0]
    assert doc.scope == "user"
    assert [s.heading for s in doc.sections] == ["Key Tables"]
    assert doc.sections[0].source == "verified"


def test_colliding_domain_unions_sections_user_first(tmp_path: Path) -> None:
    _write(tmp_path, "user", _cart_doc())
    _write(tmp_path, "project", _harvest_doc())
    docs = _store(tmp_path).docs()
    assert len(docs) == 1
    doc = docs[0]
    assert doc.scope == "merged"
    assert [s.heading for s in doc.sections] == ["Key Tables", "Gotchas"]
    assert [s.source for s in doc.sections] == ["verified", "harvested"]
    assert doc.tables == ["orders"]


def test_duplicate_bodies_dedup_project_copy_absorbed(tmp_path: Path) -> None:
    # Legacy pre-v2 apply copied user sections into the project doc; union must absorb them.
    legacy = _harvest_doc()
    legacy.sections.insert(0, _cart_doc().sections[0].model_copy())
    _write(tmp_path, "user", _cart_doc())
    _write(tmp_path, "project", legacy)
    doc = _store(tmp_path).docs()[0]
    assert [s.heading for s in doc.sections] == ["Key Tables", "Gotchas"]  # no double Key Tables


def test_load_domain_scope_filters(tmp_path: Path) -> None:
    _write(tmp_path, "user", _cart_doc())
    _write(tmp_path, "project", _harvest_doc())
    store = _store(tmp_path)
    assert store.load_domain("orders", scope="user") is not None
    assert store.load_domain("orders", scope="user").sections[0].heading == "Key Tables"
    assert store.load_domain("orders", scope="project").sections[0].heading == "Gotchas"
    assert store.load_domain("orders").scope == "merged"  # default = merged view
    assert store.load_domain("nope", scope="project") is None


def test_i2_scenario_refresh_regeneration_visible_through_merge(tmp_path: Path) -> None:
    # The I2 cross-seam regression: harvest exists project-side; the user-layer doc is
    # regenerated (schema changed) — the merged read must reflect the NEW user content.
    _write(tmp_path, "user", _cart_doc())
    _write(tmp_path, "project", _harvest_doc())
    regenerated = _cart_doc()
    regenerated.sections[0] = Section(
        heading="Key Tables", body="- orders: 9 rows (new col added)", source="verified"
    )
    _write(tmp_path, "user", regenerated)  # simulates M2 refresh rewrite
    doc = _store(tmp_path).docs()[0]
    bodies = [s.body for s in doc.sections]
    assert "- orders: 9 rows (new col added)" in bodies  # fresh content visible
    assert "- orders: 8 rows" not in bodies              # stale copy NOT shadowing
    assert "- exclude test orders" in bodies             # harvested content preserved
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_maze_store_merge.py -v`
Expected: FAIL — `test_colliding_domain_unions_sections_user_first` gets whole-doc override (project wins, 1 section), `load_domain` rejects the `scope` kwarg with `TypeError`.

- [ ] **Step 3: Implement**

Replace `docs()` and `load_domain()` in `src/labrat/maze/store.py` (keep `_Layer`, `__init__`, `from_env`, `write_doc`, `user_scent_dir` unchanged):

```python
    def docs(self, kind: str = "scent") -> list[ScentDoc]:
        by_domain: dict[str, list[ScentDoc]] = {}
        for layer in self._layers:  # user first, project second
            directory = layer.root / kind
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                doc = parse_document(
                    path.read_text(encoding="utf-8"), domain=path.stem, scope=layer.scope
                )
                if doc.kind != kind:
                    continue
                by_domain.setdefault(doc.domain, []).append(doc)
        return [_merge_domain(parts) for parts in by_domain.values()]

    def load_domain(
        self, domain: str, kind: str = "scent", *, scope: str | None = None
    ) -> ScentDoc | None:
        if scope is not None:
            layer = next((la for la in self._layers if la.scope == scope), None)
            if layer is None:
                raise ValueError(f"unknown scope: {scope!r}")
            path = layer.root / kind / f"{domain}.md"
            if not path.is_file():
                return None
            doc = parse_document(
                path.read_text(encoding="utf-8"), domain=domain, scope=layer.scope
            )
            return doc if doc.kind == kind else None
        for doc in self.docs(kind):
            if doc.domain == domain:
                return doc
        return None
```

Module-level helper (below the class, above `user_scent_dir`):

```python
def _merge_domain(parts: list[ScentDoc]) -> ScentDoc:
    """Union a domain's layer docs (user first, project second) into one view.

    Sections dedup by body (strip): a project-layer copy of a user section —
    the legacy pre-v2 apply behavior — collapses into the union, which is why
    no on-disk migration is needed.
    """
    if len(parts) == 1:
        return parts[0]
    sections: list[Section] = []
    seen_bodies: set[str] = set()
    for doc in parts:
        for s in doc.sections:
            key = s.body.strip()
            if key in seen_bodies:
                continue
            seen_bodies.add(key)
            sections.append(s)
    tables = sorted({t for doc in parts for t in doc.tables})
    confidence = next(
        (doc.confidence for doc in reversed(parts) if doc.confidence is not None), None
    )
    return ScentDoc(
        domain=parts[0].domain,
        kind=parts[0].kind,
        tables=tables,
        confidence=confidence,
        scope="merged",
        sections=sections,
    )
```

Add `Section` to the existing `labrat.maze.document` import line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_maze_store_merge.py -v` — 5 PASS.
Also: `uv run pytest tests/unit -k "maze or scent or harvest or first_connect" -v` — existing store consumers must pass unchanged (they exercise single-layer domains → golden path).

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/store.py tests/unit/test_maze_store_merge.py
git commit -m "feat(maze): per-section merge-at-read + scoped load_domain (I2 foundation)"
```

---

### Task 2: `apply_approved_sections` writes only what it owns

**Files:**
- Modify: `src/labrat/maze/harvest.py` (the `apply_approved_sections` function only)
- Test: `tests/unit/test_maze_harvest.py` (extend)

**Interfaces:**
- Consumes: `MazeStore.load_domain(domain, scope="project")` (Task 1), `audit_scent_doc`, `ScentContaminationError`, `store.write_doc` (existing).
- Produces: same public signature `apply_approved_sections(store, domain, approved) -> None`; write path now provably project-layer-only.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_maze_harvest.py`; reuse its existing store/tmp fixtures — read the file first and match its helper conventions)

```python
def test_apply_never_copies_user_layer_content(tmp_path: Path) -> None:
    # Non-negotiable #2: user-layer (Cartographer) sections must not be written project-side.
    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")
    user_dir = tmp_path / "home" / ".labrat" / "maze" / "p1" / "scent"
    user_dir.mkdir(parents=True)
    cart = ScentDoc(
        domain="orders",
        sections=[Section(heading="Key Tables", body="- orders: 8 rows", source="verified")],
    )
    (user_dir / "orders.md").write_text(render_document(cart), encoding="utf-8")

    apply_approved_sections(
        store,
        "orders",
        [Section(heading="Gotchas", body="- exclude test orders", source="harvested")],
    )

    project_doc = store.load_domain("orders", scope="project")
    assert project_doc is not None
    assert [s.heading for s in project_doc.sections] == ["Gotchas"]  # NO Key Tables copy
    merged = store.load_domain("orders")
    assert {s.heading for s in merged.sections} == {"Key Tables", "Gotchas"}


def test_apply_idempotent_against_project_layer(tmp_path: Path) -> None:
    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")
    approved = [Section(heading="Gotchas", body="- dates are UTC", source="harvested")]
    apply_approved_sections(store, "general", approved)
    apply_approved_sections(store, "general", approved)  # re-approve
    doc = store.load_domain("general", scope="project")
    assert doc is not None and len(doc.sections) == 1
```

(Add whatever of `MazeStore/ScentDoc/Section/render_document/apply_approved_sections` the file does not already import.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_maze_harvest.py -v`
Expected: `test_apply_never_copies_user_layer_content` FAILS (project doc contains the copied "Key Tables" section under today's merged-load behavior).

- [ ] **Step 3: Implement**

In `src/labrat/maze/harvest.py::apply_approved_sections`, change the load line and docstring:

```python
def apply_approved_sections(store: MazeStore, domain: str, approved: list[Section]) -> None:
    """Merge human-approved harvested sections into the domain's PROJECT-layer doc.

    Loads only the project layer (never the merged view), so user-layer
    Cartographer content is never copied — M2's user-scope refresh can never be
    shadowed by a frozen project copy (spec 2026-07-09 non-negotiable #2).
    Dedups against existing project-layer section bodies so re-approving the
    same bullet is idempotent. Audits the doc fail-loud BEFORE writing.
    """
    if not approved:
        return
    doc = store.load_domain(domain, scope="project") or ScentDoc(domain=domain)
    ...  # rest of the function body unchanged (dedup, audit, raise, write_doc)
```

Only the `store.load_domain(domain)` call changes (gains `scope="project"`) plus the docstring; the dedup/audit/write lines stay byte-identical.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_maze_harvest.py tests/unit/test_harvest_wiring.py tests/tui/test_harvest_review_screen.py -v`
Expected: new tests PASS; all existing harvest tests PASS unchanged (they never relied on the copy).

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/harvest.py tests/unit/test_maze_harvest.py
git commit -m "fix(maze): harvest-apply writes only its own sections to the project layer (closes I2)"
```

---

### Task 3: `search_reference_docs` provenance/freshness enrichment (additive)

**Files:**
- Modify: `src/labrat/agent/tools/search_reference_docs.py`
- Test: `tests/unit/test_search_reference_docs_provenance.py` (create)

**Interfaces:**
- Consumes: `Section.source`/`Section.schema_hash` (existing), `fingerprint_from_catalog(catalog) -> str` (`labrat.maze.staleness`), `best_source(sources) -> str` (`labrat.maze.provenance`), `ctx.catalogs: dict[str, object]` + `ctx.primary` (existing `ToolContext`).
- Produces (Task 4 relies on the repr/JSON shape):
  - `SectionMatch` += `source: str = "human"`, `fresh: bool | None = None`
  - `DocResult` += `best_source: str = "human"`, `stale: bool | None = None`
  - Existing fields and their order of declaration UNCHANGED (additive only; new fields declared after existing ones so reprs keep their current prefix).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_search_reference_docs_provenance.py
"""Additive provenance/freshness fields on search_reference_docs output (spec 3.3)."""

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.maze.document import ScentDoc, Section, render_document
from labrat.maze.staleness import fingerprint_from_catalog


def _catalog() -> Catalog:
    return Catalog(
        database_name="db",
        schemas=[
            Schema(
                name="main",
                tables=[
                    Table(
                        name="orders",
                        schema_name="main",
                        columns=[
                            Column(name="id", data_type="INTEGER", nullable=False),
                        ],
                    )
                ],
            )
        ],
    )


def _write_doc(maze_dir: Path, *, schema_hash: str | None, source: str = "verified") -> None:
    doc = ScentDoc(
        domain="orders",
        sections=[
            Section(
                heading="Key Tables",
                body="- orders join key id",
                source=source,
                schema_hash=schema_hash,
            )
        ],
    )
    scent = maze_dir / "scent"
    scent.mkdir(parents=True, exist_ok=True)
    (scent / "orders.md").write_text(render_document(doc), encoding="utf-8")


@pytest.fixture
def env_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    maze_dir = tmp_path / "labrat_maze"
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    return maze_dir


async def _run(ctx: ToolContext):
    tool = SearchReferenceDocsTool()
    args = tool.input_model.model_validate({"question": "orders join key"})
    return await tool.execute(ctx, args)


async def test_fresh_section_labelled(env_store: Path) -> None:
    cat = _catalog()
    _write_doc(env_store, schema_hash=fingerprint_from_catalog(cat))
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": cat}, primary="main")
    out = await _run(ctx)
    sec = out.results[0].sections[0]
    assert sec.source == "verified" and sec.fresh is True
    assert out.results[0].best_source == "verified"
    assert out.results[0].stale is False


async def test_hash_mismatch_is_stale(env_store: Path) -> None:
    _write_doc(env_store, schema_hash="deadbeef")
    ctx = ToolContext(
        connections={"main": object()}, catalogs={"main": _catalog()}, primary="main"
    )
    out = await _run(ctx)
    assert out.results[0].sections[0].fresh is False
    assert out.results[0].stale is True


async def test_missing_meta_is_unknown_not_fresh(env_store: Path) -> None:
    _write_doc(env_store, schema_hash=None)
    ctx = ToolContext(
        connections={"main": object()}, catalogs={"main": _catalog()}, primary="main"
    )
    out = await _run(ctx)
    assert out.results[0].sections[0].fresh is None
    assert out.results[0].stale is None


async def test_no_catalog_degrades_to_unknown(env_store: Path) -> None:
    _write_doc(env_store, schema_hash="anything")
    out = await _run(ToolContext())  # default ctx: no catalogs
    assert out.results[0].sections[0].fresh is None
    assert out.results[0].stale is None
    assert out.results[0].best_source == "verified"  # tier still reported
```

Note: `Catalog`/`Table`/`Column` required fields above follow `tests/unit/test_staleness_catalog.py` — read that file first and copy its `_catalog` field conventions exactly (they were verified against the real models in M2 Task 1).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_search_reference_docs_provenance.py -v`
Expected: FAIL — `AttributeError`/`ValidationError`: `SectionMatch` has no `fresh`, `DocResult` has no `best_source`.

- [ ] **Step 3: Implement**

In `src/labrat/agent/tools/search_reference_docs.py`:

(a) Extend the output models (new fields AFTER existing ones):

```python
class SectionMatch(BaseModel):
    heading: str
    body: str
    score: float
    matched_terms: list[str]
    source: str = "human"
    fresh: bool | None = None  # None = no schema_hash meta / no catalog → unknown


class DocResult(BaseModel):
    domain: str
    quick_reference: str | None
    sections: list[SectionMatch]
    best_source: str = "human"
    stale: bool | None = None  # any section fresh=False → True; all None → None
```

(b) In `execute`, compute the current fingerprint once (top of the method, after the `docs = ...` line):

```python
        from labrat.db.catalog import Catalog
        from labrat.maze.provenance import best_source
        from labrat.maze.staleness import fingerprint_from_catalog

        catalog = ctx.catalogs.get(ctx.primary) if ctx.catalogs else None
        current_fp = (
            fingerprint_from_catalog(catalog) if isinstance(catalog, Catalog) else None
        )
```

(c) Where each `SectionMatch` is built, add the two fields:

```python
                SectionMatch(
                    heading=h.section.heading,
                    body=h.section.body,
                    score=h.score,
                    matched_terms=h.matched,
                    source=h.section.source,
                    fresh=(
                        None
                        if current_fp is None or h.section.schema_hash is None
                        else h.section.schema_hash == current_fp
                    ),
                )
```

(d) After the quick-reference loop, stamp the doc-level aggregates:

```python
        for dr in results:
            dr.best_source = best_source([s.source for s in dr.sections])
            freshes = [s.fresh for s in dr.sections]
            if any(f is False for f in freshes):
                dr.stale = True
            elif any(f is True for f in freshes):
                dr.stale = False
            # else: all None → stale stays None
```

(Move the three imports to module top-level if pyright/ruff prefer — no cycle risk: `agent.tools` already imports from `labrat.maze`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_search_reference_docs_provenance.py tests/unit -k "search_reference" -v`
Expected: new 4 PASS; all existing search_reference_docs tests PASS unchanged (additive fields have defaults).

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/search_reference_docs.py tests/unit/test_search_reference_docs_provenance.py
git commit -m "feat(tools): search_reference_docs reports per-section source/freshness + doc best_source/stale"
```

---

### Task 4: `TurnProvenance` per-domain tier+freshness footer

**Files:**
- Modify: `src/labrat/widgets/turn_provenance.py`
- Test: `tests/widgets/test_turn_provenance.py` (extend)

**Interfaces:**
- Consumes: the enriched output shapes from Task 3, arriving as `str(output)` — either JSON (MCP path) or the Pydantic repr (in-process path, e.g. `question='q' results=[DocResult(domain='orders', quick_reference=None, sections=[...], best_source='verified', stale=False)]`).
- Produces: `footer()` renders, for structured scent data: `scent: <first-domain> (<tier>[·fresh|·stale])[ +N]`; degrades to today's `scent ×N (fresh|stale)` count format when tier data is absent. All other public methods/behavior unchanged (`record_tool`, `set_verifier`, empty→None, never-raise).

**Footer format contract (exact):**
- First matched doc's `domain` + its `best_source` tier; freshness suffix `·fresh` when `stale is False`, `·stale` when `stale is True`, omitted when `None`.
- ` +N` appended when N additional docs matched (N = doc count − 1, only when > 0).
- Fallback (no tier data anywhere in the turn): `scent ×<hits> (<fresh|stale>)` exactly as today, using the global `scent_stale` flag.
- Empty results still contribute nothing (existing tests must keep passing).

- [ ] **Step 1: Write the failing tests** (append; keep ALL existing tests untouched — their fixtures lack tier fields, so they pin the fallback format)

```python
def test_structured_json_renders_domain_tier_freshness() -> None:
    prov = TurnProvenance()
    prov.record_tool(
        "search_reference_docs",
        True,
        json.dumps(
            {
                "question": "q",
                "results": [
                    {
                        "domain": "orders",
                        "quick_reference": None,
                        "sections": [],
                        "best_source": "verified",
                        "stale": False,
                    }
                ],
            }
        ),
    )
    assert (prov.footer() or "").startswith("⚑ grounded: scent: orders (verified·fresh)")


def test_structured_json_stale_and_plus_n() -> None:
    prov = TurnProvenance()
    payload = {
        "question": "q",
        "results": [
            {"domain": "orders", "quick_reference": None, "sections": [],
             "best_source": "harvested", "stale": True},
            {"domain": "general", "quick_reference": None, "sections": [],
             "best_source": "human", "stale": None},
        ],
    }
    prov.record_tool("search_reference_docs", True, json.dumps(payload))
    footer = prov.footer() or ""
    assert "scent: orders (harvested·stale) +1" in footer


def test_repr_shape_with_tier_fields() -> None:
    # Production in-process shape after Task 3 (build the string from the REAL model —
    # see the construction pattern in test_chat_panel.py's _GroundedFakeLoop fixture).
    from labrat.agent.tools.search_reference_docs import DocResult, SectionMatch, _Output

    out = _Output(
        question="q",
        results=[
            DocResult(
                domain="orders",
                quick_reference=None,
                # Non-empty sections matter: the nested SectionMatch(...) parens are
                # exactly what breaks naive per-DocResult regex segmentation.
                sections=[
                    SectionMatch(heading="Key Tables", body="- orders", score=1.0,
                                 matched_terms=["orders"], source="verified", fresh=True)
                ],
                best_source="verified",
                stale=False,
            )
        ],
    )
    prov = TurnProvenance()
    prov.record_tool("search_reference_docs", True, str(out))
    assert "scent: orders (verified·fresh)" in (prov.footer() or "")


def test_unknown_freshness_never_rendered_fresh() -> None:
    prov = TurnProvenance()
    prov.record_tool(
        "search_reference_docs",
        True,
        json.dumps({"question": "q", "results": [
            {"domain": "orders", "quick_reference": None, "sections": [],
             "best_source": "verified", "stale": None}]}),
    )
    footer = prov.footer() or ""
    assert "scent: orders (verified)" in footer
    assert "fresh" not in footer and "stale" not in footer


def test_tierless_payload_keeps_count_fallback() -> None:
    # Old-shape JSON (no best_source key) → today's count format, global flag.
    prov = TurnProvenance(scent_stale=True)
    prov.record_tool(
        "search_reference_docs",
        True,
        json.dumps({"question": "q", "results": [
            {"domain": "orders", "quick_reference": None, "sections": []}]}),
    )
    assert "scent ×1 (stale)" in (prov.footer() or "")  # noqa: RUF001
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/widgets/test_turn_provenance.py -v`
Expected: new 5 FAIL (footer renders count format / repr recognizer has no tier extraction); existing tests PASS.

- [ ] **Step 3: Implement**

Rework the scent bookkeeping in `src/labrat/widgets/turn_provenance.py` (verifier/join/lineage/sql handling and the never-raise contract unchanged):

```python
from __future__ import annotations

import json
import re
from typing import Any, cast

# NOTE: never try to segment per-DocResult with a non-greedy paren regex — the nested
# SectionMatch(...) reprs make it truncate before best_source/stale (which are declared
# AFTER sections). Instead: global findall per field and zip. This is collision-safe
# because SectionMatch has no domain=/best_source=/stale= fields (its names are
# source=/fresh=), so each pattern matches exactly once per DocResult.
_DOMAIN_RE = re.compile(r"domain='([^']*)'")
_BEST_RE = re.compile(r"best_source='([^']*)'")
_STALE_RE = re.compile(r"stale=(True|False|None)")


class TurnProvenance:
    def __init__(self, scent_stale: bool = False) -> None:
        self._scent_stale = scent_stale
        self._scent_hits = 0
        # (domain, best_source, stale) per matched doc, in arrival order; empty
        # when only count data was recoverable (fallback rendering).
        self._scent_docs: list[tuple[str, str, bool | None]] = []
        self._join_verified = False
        self._lineage_used = False
        self._sql_runs = 0
        self._verifier_rounds: int | None = None

    def _record_scent_doc(self, domain: str, best: str | None, stale: bool | None) -> None:
        self._scent_hits += 1
        if best is not None:
            self._scent_docs.append((domain, best, stale))

    def record_tool(self, name: str, ok: bool, output: str) -> None:
        if not ok:
            return
        if name == "search_reference_docs":
            try:
                parsed: Any = json.loads(output)
                results: Any = (
                    cast(dict[str, Any], parsed).get("results", [])
                    if isinstance(parsed, dict)
                    else []
                )
                if isinstance(results, list):
                    for doc in cast("list[Any]", results):
                        if not isinstance(doc, dict):
                            continue
                        d = cast(dict[str, Any], doc)
                        domain = d.get("domain")
                        if not isinstance(domain, str):
                            continue
                        best = d.get("best_source")
                        stale = d.get("stale")
                        self._record_scent_doc(
                            domain,
                            best if isinstance(best, str) else None,
                            stale if isinstance(stale, bool) else None,
                        )
            except (ValueError, TypeError):
                if "results=[]" in output:
                    pass  # zero hits: not grounding evidence
                elif "DocResult(" in output:
                    n_docs = output.count("DocResult(")
                    domains = _DOMAIN_RE.findall(output)
                    bests = _BEST_RE.findall(output)
                    stales = _STALE_RE.findall(output)
                    if len(domains) == n_docs and len(bests) == n_docs:
                        for i in range(n_docs):
                            stale: bool | None = None
                            if i < len(stales) and stales[i] != "None":
                                stale = stales[i] == "True"
                            self._record_scent_doc(domains[i], bests[i], stale)
                    else:  # pre-enrichment repr or field-count mismatch → count fallback
                        self._scent_hits += n_docs
                else:
                    self._scent_hits += 1  # truly opaque output
        elif name == "verify_join":
            self._join_verified = True
        elif name == "explain_lineage":
            self._lineage_used = True
        elif name == "run_sql":
            self._sql_runs += 1
```

And the scent branch of `footer()` becomes:

```python
        if self._scent_hits:
            if self._scent_docs:
                domain, best, stale = self._scent_docs[0]
                label = best
                if stale is True:
                    label += "·stale"
                elif stale is False:
                    label += "·fresh"
                seg = f"scent: {domain} ({label})"
                extra = len(self._scent_docs) - 1
                if extra > 0:
                    seg += f" +{extra}"
                parts.append(seg)
            else:
                freshness = "stale" if self._scent_stale else "fresh"
                parts.append(f"scent ×{self._scent_hits} ({freshness})")  # noqa: RUF001
```

Note the JSON-path counting change: hits now count per-doc via `_record_scent_doc` — this matches the existing per-result counting (`test_scent_hits_counted_with_freshness` counts 2 domains from one call and still passes because each result dict has a `domain` and, lacking `best_source`, lands in the fallback count). Verify that existing test still passes BEFORE moving on; if any old JSON fixture lacks `domain` keys, count those results with `self._scent_hits += 1` per result dict (preserving old totals) — the essential contract is: old fixtures → old footer strings, byte-identical.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/widgets/test_turn_provenance.py tests/widgets/test_chat_panel.py -v`
Expected: ALL PASS — old tests byte-identical footers, new tests the tier format. `test_chat_panel.py`'s `_GroundedFakeLoop` emits the pre-Task-3 repr (no tier fields) → its expected footer string (`scent ×1 (fresh) · 1 query`) must still hold via the fallback; if Task 3's new default fields change that fixture's repr expectations, update the FIXTURE to the real post-Task-3 `str(_Output(...))` (construct from the real model, as that file already does) and update its expected footer to the tier format — never weaken assertions.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/widgets/turn_provenance.py tests/widgets/test_turn_provenance.py tests/widgets/test_chat_panel.py
git commit -m "feat(widgets): footer renders per-domain provenance tier + freshness"
```

---

### Task 5: Docs + manual spot-check + finish

**Files:**
- Modify: `TESTING.md`, `decisions.md`

- [ ] **Step 1: TESTING.md** — in the M4 manual-gate section, update step 2's expected footer to the new format and append one step:

```markdown
2. Ask "any reference notes on orders? then count the orders" → footer like
   `⚑ grounded: scent: orders (verified·fresh) · 1 query`.
8. Harvest a correction into `orders` (M3 gate steps 1–2), then re-ask about orders →
   the same domain's footer tier stays the doc's best tier (verified, from the
   Cartographer sections) and the merged answer includes the harvested gotcha even
   immediately after a Ctrl+Shift+M scent refresh (the I2 shadow is gone).
```

(Adjust numbering to the file's actual list; keep the existing steps otherwise.)

- [ ] **Step 2: decisions.md entry**

```markdown
## 2026-07-09 — Scent read-model v2 + provenance-rich footer

MazeStore.docs() now unions sections per domain across layers (user→project, body-dedup)
instead of whole-doc project-wins, and apply_approved_sections loads/writes the project
layer only — the I2 shadow class (harvest-apply freezing Cartographer content) is
impossible by construction, clearing the path for T1b semantic-layer sections to share
domains. search_reference_docs gains additive per-section source/fresh and per-doc
best_source/stale (computed in-tool from ctx catalogs vs Section schema_hash meta); the
M4 footer renders `scent: <domain> (<tier>·<freshness>) +N` when that data is present and
degrades to the old count format otherwise. Spec:
docs/superpowers/specs/2026-07-09-scent-read-model-v2-design.md.
```

- [ ] **Step 3: Full gates**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add TESTING.md decisions.md
git commit -m "docs: scent read-model v2 — TESTING.md footer format + decisions entry"
```

- [ ] **Step 4: Manual spot-check** (controller, pty harness): one TUI session on the ecommerce profile — ask the M4-gate step-2 question and confirm the footer renders `scent: <domain> (verified·fresh)`-style; then TESTING.md's new step 8 flow (harvest → refresh → re-ask) confirming merged content post-refresh. Then superpowers:finishing-a-development-branch.
