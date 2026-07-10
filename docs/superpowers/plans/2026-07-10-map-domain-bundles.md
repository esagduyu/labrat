# Map (Domain Bundles) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** ship **Maps** — per-domain bundles of pointers to Scent + Trails, auto-sketched by the Cartographer from dbt structure and activated as an additive retrieval filter that scopes the agent's grounding to a domain, benchmark-safe by default.

**Architecture:** a `kind="map"` doc (pure pointers) in the existing `MazeStore`; a retrieval pre-filter on `search_reference_docs`/`search_trails` driven by `ToolContext.active_maps` (a mutable list the TUI owns; empty/None → today's behavior); a dbt-structure auto-seed drafter; a TUI author/activate surface.

**Tech Stack:** existing `maze/` (store, document, cartographer), `agent/tools/` (the two search tools, ToolContext), `catalog/dbt`, Textual. No new deps, no LLM.

## Global Constraints

- **No retrieval-scorer change.** Activation is a pre-filter on *which docs are eligible*, never how they rank. `active_maps` empty/None (the default + EVERY benchmark run) → retrieval byte-identical to today.
- **Reference, not copy.** Maps store only member IDs; resolved live; a missing referent is a soft-miss (dropped), never stale content, never a raise.
- **Benchmark-safe by construction.** The benchmark never activates a Map (the Cartographer sketches but does not activate). Nothing under `eval/`/`mcp/` sets `active_maps`.
- **Auto-seed is deterministic, no LLM, human-gated** (`source="draft"`, contamination-audited) — same posture as the structure-only Scent pre-pass.
- **Reuse:** `MazeStore` (`kind="map"`), `document.py`, `scent_audit`, team-Scent git-versioning, the existing search tools — consumed as-is.
- Pyright strict for `maze/`, `agent/tools/`; `screens/` exempt. Gates per commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. `test_app_renders` env flake non-signal; `git checkout -- snapshot_report.html` if regenerated.

---

### Task 1: `maze/map.py` — Map doc model + member resolution

**Files:** Create `src/labrat/maze/map.py`, `tests/unit/test_maze_map.py`

**Interfaces:**
- Consumes: `maze.document.{ScentDoc, Section}`, `maze.store.MazeStore`.
- Produces:
  - Helpers to read a `kind="map"` `ScentDoc`'s members: `scent_members(doc) -> list[str]`, `trail_members(doc) -> list[str]`, `map_prompts(doc) -> list[str]` (parse the `Scent` / `Trails` / `Suggested Prompts` sections' bullet lists).
  - `build_map_doc(slug, *, scent, trails, prompts, overview="", source="human") -> ScentDoc` — constructs a `kind="map"` doc with those sections.
  - `ResolvedMembers(BaseModel)`: `scent: list[str]`, `trails: list[str]`, `misses: list[str]`.
  - `resolve_members(map_docs: list[ScentDoc], store: MazeStore) -> ResolvedMembers` — union the maps' scent/trail members; a member whose target doc doesn't exist in the store (`store.load_domain(m, kind="scent"/"trail")` is None) is dropped and recorded in `misses`. Never raises.

- [ ] **Step 1: Write the failing tests**

```python
"""Map doc model + member resolution (maze/map.py)."""
from pathlib import Path
from labrat.maze.map import build_map_doc, resolve_members, scent_members, trail_members, map_prompts
from labrat.maze.store import MazeStore
from labrat.maze.document import ScentDoc, Section


def test_build_and_read_map_doc():
    doc = build_map_doc("revenue", scent=["subscriptions", "invoices"], trails=["compute-mrr"], prompts=["What's our ARR?"])
    assert doc.kind == "map" and doc.domain == "revenue"
    assert scent_members(doc) == ["subscriptions", "invoices"]
    assert trail_members(doc) == ["compute-mrr"]
    assert map_prompts(doc) == ["What's our ARR?"]


def test_resolve_members_soft_miss(tmp_path):
    store = MazeStore(project_root=tmp_path, home=tmp_path / "h", profile="default")
    # a real scent domain + a real trail exist; the map references one missing of each
    store.write_doc(ScentDoc(domain="subscriptions", kind="scent", sections=[Section(heading="Quick Reference", body="subs")]), kind="scent")
    store.write_doc(ScentDoc(domain="compute-mrr", kind="trail", sections=[Section(heading="When to use", body="mrr")]), kind="trail")
    m = build_map_doc("revenue", scent=["subscriptions", "gone_domain"], trails=["compute-mrr", "gone_trail"], prompts=[])
    resolved = resolve_members([m], store)
    assert set(resolved.scent) == {"subscriptions"}
    assert set(resolved.trails) == {"compute-mrr"}
    assert set(resolved.misses) == {"gone_domain", "gone_trail"}


def test_resolve_members_union_across_maps(tmp_path):
    store = MazeStore(project_root=tmp_path, home=tmp_path / "h", profile="default")
    for d in ("subscriptions", "events"):
        store.write_doc(ScentDoc(domain=d, kind="scent", sections=[Section(heading="Quick Reference", body=d)]), kind="scent")
    m1 = build_map_doc("revenue", scent=["subscriptions"], trails=[], prompts=[])
    m2 = build_map_doc("product", scent=["events"], trails=[], prompts=[])
    resolved = resolve_members([m1, m2], store)
    assert set(resolved.scent) == {"subscriptions", "events"}
```

- [ ] **Step 2: Run to verify failure** → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `src/labrat/maze/map.py` per the Interfaces block. Member sections are bullet lists (`- <slug>`), parsed the same way harvest bullets are. `build_map_doc` renders `Overview`/`Scent`/`Trails`/`Suggested Prompts` sections (a section with the bullet list). `resolve_members` uses `store.load_domain(m, kind="scent", scope=None)` / `kind="trail"` — None → miss.
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Gates + commit** (`git add src/labrat/maze/map.py tests/unit/test_maze_map.py`; `"feat(map): kind=map doc model + reference resolution with soft-miss"`).

---

### Task 2: Activation retrieval filter (the benchmark-safety linchpin)

**Files:** Modify `src/labrat/agent/tools/base.py` (ToolContext), `src/labrat/agent/tools/search_reference_docs.py`, `src/labrat/agent/tools/search_trails.py`; Test `tests/unit/test_map_activation_filter.py`

**Interfaces:**
- `ToolContext.__init__` gains `active_maps: list[str] | None = None` (kw-only, after `subagent_runner`), stored as `self.active_maps`. Mirrors the `llm_fn`/`subagent_runner` injection precedent. Default None → no filter.
- In `search_reference_docs.execute` and `search_trails.execute`: after loading `docs`, if `ctx.active_maps` is **non-empty**, load those Map docs from the store (`store.load_domain(slug, kind="map", scope=None)` for each; skip None), `resolve_members(...)`, and **filter the doc set** — Scent to `resolved.scent` domains, Trails to `resolved.trails` domains. If `active_maps` is None or empty → **no filter** (unchanged). The scorer/sort/output is otherwise untouched.

- [ ] **Step 1: Write the failing tests**

```python
"""Map activation filters retrieval; empty/None is byte-identical (benchmark guarantee)."""
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.maze.store import MazeStore
from labrat.maze.document import ScentDoc, Section
from labrat.maze.map import build_map_doc


def _seed(root):
    store = MazeStore(project_root=root, home=root / "h", profile="default")
    for d in ("subscriptions", "campaigns"):
        store.write_doc(ScentDoc(domain=d, kind="scent",
            sections=[Section(heading="Gotchas", body=f"{d} revenue churn note")]), kind="scent")
    store.write_doc(build_map_doc("revenue", scent=["subscriptions"], trails=[], prompts=[]), kind="map")


async def test_no_active_maps_is_unchanged(tmp_path, monkeypatch):
    _seed(tmp_path); monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    tool = SearchReferenceDocsTool()
    ctx = ToolContext(connections={}, catalogs={}, primary="main")  # active_maps None
    out = await tool.execute(ctx, tool.input_model(question="revenue churn"))
    domains = {r.domain for r in out.results}
    assert domains == {"subscriptions", "campaigns"}  # both, as today


async def test_active_map_filters_to_members(tmp_path, monkeypatch):
    _seed(tmp_path); monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    tool = SearchReferenceDocsTool()
    ctx = ToolContext(connections={}, catalogs={}, primary="main", active_maps=["revenue"])
    out = await tool.execute(ctx, tool.input_model(question="revenue churn"))
    domains = {r.domain for r in out.results}
    assert domains == {"subscriptions"}  # campaigns filtered out — not a revenue-Map member


async def test_empty_list_is_unchanged(tmp_path, monkeypatch):
    _seed(tmp_path); monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    tool = SearchReferenceDocsTool()
    ctx = ToolContext(connections={}, catalogs={}, primary="main", active_maps=[])
    out = await tool.execute(ctx, tool.input_model(question="revenue churn"))
    assert {r.domain for r in out.results} == {"subscriptions", "campaigns"}  # empty == no filter
```

(Add the mirror test for `search_trails` filtering to `trail_members`.)

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement.** ToolContext field + the filter in both tools. Keep the filter minimal: build the allowed-domain set once, `docs = [d for d in docs if d.domain in allowed]` before scoring. `search_reference_docs` reads `MazeStore.from_env(ctx.profile_name)` already — reuse that store for the Map lookups. Guard: `active = ctx.active_maps or []` then `if active:`.
- [ ] **Step 4: Run tests** → PASS. Also run existing `test_search_reference_docs`/`test_search_trails` — they construct ToolContext without `active_maps` → None → unchanged; confirm green.
- [ ] **Step 5: Gates + commit** (`"feat(map): activation retrieval filter (additive, default-off, benchmark-safe)"`).

---

### Task 3: dbt-structure auto-seed — `draft_maps_from_dbt`

**Files:** Modify `src/labrat/maze/map.py` (add the drafter); Test `tests/unit/test_map_autoseed.py`

**Interfaces:**
- `draft_maps_from_dbt(manifest_path: Path, *, existing_scent_domains: set[str], generated_at: str, model_id: str | None = None) -> dict[str, ScentDoc]` — reads the dbt manifest nodes, groups model **table names** by their `fqn` domain-folder (the fqn segment for the model's folder, excluding staging/intermediate-style folders by name convention: skip folders named `staging`/`stg`/`intermediate`/`int`/`base`), and drafts one `kind="map"` skeleton per group: `domain`=folder slug, Scent members = the group's model table names that ARE in `existing_scent_domains` (so a Map only points at Scent that exists), empty Trails/prompts, `source="draft"`. Contamination-audited (fail-loud). Returns `{slug: ScentDoc}`. Deterministic; no LLM.
- Table name per node: prefer `node["alias"]` else `node["name"]` (mirror `semantic.py::_table_for`'s resolution).

- [ ] **Step 1: Write the failing test**

```python
"""dbt-structure auto-seed drafts kind=map skeletons per domain folder."""
import json
from pathlib import Path
from labrat.maze.map import draft_maps_from_dbt, scent_members


def _manifest(tmp_path):
    nodes = {
        "model.acme.mrr":     {"resource_type": "model", "name": "mrr",     "alias": "mrr",     "fqn": ["acme","marts","finance","mrr"]},
        "model.acme.invoices":{"resource_type": "model", "name": "invoices","alias": "invoices","fqn": ["acme","marts","finance","invoices"]},
        "model.acme.events":  {"resource_type": "model", "name": "events",  "alias": "events",  "fqn": ["acme","marts","product","events"]},
        "model.acme.stg_x":   {"resource_type": "model", "name": "stg_x",   "alias": "stg_x",   "fqn": ["acme","staging","stg_x"]},
    }
    p = tmp_path / "manifest.json"; p.write_text(json.dumps({"nodes": nodes})); return p


def test_autoseed_groups_by_folder(tmp_path):
    mp = _manifest(tmp_path)
    maps = draft_maps_from_dbt(mp, existing_scent_domains={"mrr","invoices","events"},
                               generated_at="2026-07-10T00:00:00Z")
    assert set(maps) == {"finance", "product"}     # staging excluded
    assert set(scent_members(maps["finance"])) == {"mrr", "invoices"}
    assert scent_members(maps["product"]) == ["events"]
    assert all(m.kind == "map" for m in maps.values())
    assert all(s.source == "draft" for m in maps.values() for s in m.sections)


def test_autoseed_only_points_at_existing_scent(tmp_path):
    mp = _manifest(tmp_path)
    maps = draft_maps_from_dbt(mp, existing_scent_domains={"mrr"},  # invoices scent not generated yet
                               generated_at="2026-07-10T00:00:00Z")
    assert scent_members(maps["finance"]) == ["mrr"]  # invoices dropped (no scent)
```

- [ ] **Step 2: Run to verify failure** → FAIL.
- [ ] **Step 3: Implement** `draft_maps_from_dbt` (read manifest, group by `fqn[-2]` folder with the staging/intermediate exclusion, filter members to `existing_scent_domains`, `build_map_doc(..., source="draft")`, `audit_scent_doc` fail-loud). Skip a group that ends up with zero existing-scent members.
- [ ] **Step 4: Run tests** → PASS.
- [ ] **Step 5: Gates + commit** (`"feat(map): Cartographer dbt-structure auto-seed (draft_maps_from_dbt)"`).

---

### Task 4: TUI — author/curate + activate Maps

**Files:** Create `src/labrat/screens/maps.py` (author + activate screens); Modify `src/labrat/screens/main.py` (the mutable `_active_maps` list wired into ToolContext + bindings + auto-seed trigger); Test `tests/tui/test_maps_tui.py`; Docs `TESTING.md` + `decisions.md`

**Interfaces:**
- `MainScreen`: `self._active_maps: list[str] = []` created in `__init__`, passed as `active_maps=self._active_maps` into the `ToolContext(...)` at `main.py:361` (the SAME list object → the search tools see activations live; mutate in place, never reassign).
- **Auto-seed:** on first-connect / an action, when a dbt project is configured, call `draft_maps_from_dbt(manifest, existing_scent_domains=<current scent domains>, ...)` and write the drafted skeletons (`store.write_doc(doc, kind="map")`) — human curates after; notify "sketched N domain Maps".
- **Author/curate screen** (`MapEditScreen`): pick Scent + Trail members (from existing docs), edit prompts, save (audited `store.write_doc(kind="map")`, git-shareable). Mirror `TrailReviewScreen`.
- **Activate screen** (`MapActivateScreen`): list Maps with active/inactive toggles; toggling mutates `self._active_maps` in place (append/remove the slug); status-bar shows active Maps. Additive (multiple active).
- Bindings (verify free; plain-key fallback per the M2/M3 chord precedent): a Maps action (e.g. `ctrl+shift+p` for "maps"/regions — confirm free) opening the activate/author surface.

- [ ] **Step 1: Failing pilot tests** (`tests/tui/test_maps_tui.py`, follow the `_MainHost` pattern): activating a Map mutates `_active_maps` and the next `search_reference_docs` is scoped; deactivating restores full grounding; auto-seed from a fixture dbt project writes `map/` skeletons. Complete the stubs.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** the screens + wiring. Re-verify the `ToolContext(...)` construction site and how a dbt manifest path is resolved (reuse the semantic-ingest path resolution). The `_active_maps` list MUST be the same object handed to ToolContext (mutate, don't reassign) — a reassign breaks the live link.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Docs + gates + commit.** `TESTING.md` "Maps (v1)" section (auto-seed → curate → activate → scoped grounding → deactivate); `decisions.md` dated entry (Map = Warren renamed; reference-pointers; Cartographer dbt auto-seed; activation = additive retrieval filter, benchmark-safe; no scorer change). Commit `"feat(map): TUI author/curate + additive activation + dbt auto-seed trigger"`.

---

## Manual gate (after Task 4, before merge)

Scripted/pty end-to-end: with a fixture dbt project + generated Scent, auto-seed Maps → a Revenue + Product Map skeleton exist; curate the Revenue Map (add the compute-mrr Trail); activate Revenue → `search_reference_docs`/`search_trails` scoped to revenue members; activate Product too → union; deactivate all → full grounding restored (byte-identical to no-Map). Confirm: no Map active → retrieval identical to pre-Map master (the benchmark guarantee).

## Execution notes

- Branch: `feat/map-domain-bundles` off master; merge after whole-branch Fable review + the manual gate.
- Strict task order (2 consumes 1; 3 consumes 1; 4 consumes 1–3).
- Benchmark-safety is the review's #1 focus: `active_maps` empty/None must be byte-identical, and nothing on the benchmark path may set it.
