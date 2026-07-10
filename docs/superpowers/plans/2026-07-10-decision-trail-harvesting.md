# Decision-Trail Harvesting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** let an analyst record a durable **decision** (a business rule/definition/choice) that promotes — human-gated — into a `## Decisions` Scent section future sessions retrieve, extending the shipped correction-harvest loop with zero LLM and zero retrieval-scorer change.

**Architecture:** reuse the entire harvest pipeline. Capture appends a `Memory(kind=explicit_user_rule)` immediately (durable + recallable). At `ctrl+shift+h`, unpromoted decisions are clustered + drafted into `## Decisions` sections (parallel to Gotchas), shown in the existing `HarvestReviewScreen`, and written via the existing `apply_approved_sections`.

**Tech Stack:** the existing `memory/`, `maze/harvest.py`, `screens/harvest_*`, Textual. No new deps, no LLM.

## Global Constraints

- **No LLM** in decision capture or drafting — the analyst's text is stored/drafted verbatim.
- **Human-gated + contamination-audited + fail-closed + never on benchmark:** promotion only via `HarvestReviewScreen` approval + `audit_scent_doc` (fail-loud); gated on `Profile.harvest_opt_in` (default False); never on `run_agent_task`/benchmark.
- **No retrieval-scorer change** — decisions surface via the existing `search_reference_docs` section retrieval; the benchmark/default path stays byte-identical.
- **Reuse, don't fork:** `MemoryKind.explicit_user_rule`, `MemoryStore`, `resolve_table_scope`, `HarvestReviewScreen`, `apply_approved_sections`, `domain_for_cluster`, `Profile.harvest_opt_in` as-is.
- Pyright strict for `maze/`, `memory/`; `screens/` exempt. Gates per commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. `test_app_renders` env flake non-signal; `git checkout -- snapshot_report.html` if regenerated.

---

### Task 1: Decision clustering + drafting + unpromoted filter (backend)

**Files:**
- Modify: `src/labrat/maze/harvest.py` (extract `_cluster_by_scope`, add `cluster_decisions`, `draft_decision_sections`), `src/labrat/screens/harvest_controller.py` (add `review_decisions`, `filter_unpromoted_decisions`, `merge_drafts`)
- Test: `tests/unit/test_harvest.py` (or wherever harvest unit tests live — grep), `tests/unit/test_harvest_controller.py`

**Interfaces:**
- Consumes: `memory.model.{Memory, MemoryKind}`; `maze.document.{ScentDoc, Section}`; `maze.scent_audit.{audit_scent_doc, detect_contamination, ScentContaminationError}`; `maze.store.MazeStore`; existing `cluster_corrections`/`draft_harvested_sections`/`domain_for_cluster`.
- Produces:
  - `maze.harvest._cluster_by_scope(memories: list[Memory]) -> dict[str, list[Memory]]` (group by `table_scope or "__global__"`; no kind filter).
  - `maze.harvest.cluster_decisions(memories: list[Memory]) -> dict[str, list[Memory]]` (filter `kind == explicit_user_rule`, then `_cluster_by_scope`).
  - `maze.harvest.draft_decision_sections(clusters, *, generated_at: str, model_id: str | None = None) -> dict[str, list[Section]]` (heading `"Decisions"`, `source="harvested"`, deduped bullets, `detect_contamination` fail-loud — structurally identical to `draft_harvested_sections`).
  - `harvest_controller.filter_unpromoted_decisions(memories: list[Memory], store: MazeStore) -> list[Memory]` — drop any decision whose `text.strip()` already appears as a bullet in its target domain's PROJECT-layer `## Decisions` section (`store.load_domain(domain_for_cluster(m.table_scope or "__global__"), scope="project")`).
  - `harvest_controller.review_decisions(memories, store, *, generated_at, model_id=None) -> dict[str, list[Section]]` = `draft_decision_sections(cluster_decisions(filter_unpromoted_decisions(memories, store)), ...)`.
  - `harvest_controller.merge_drafts(a: dict[str, list[Section]], b: dict[str, list[Section]]) -> dict[str, list[Section]]` — per-domain concat.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_harvest_controller.py` (extend; also add decision tests to the harvest unit test file):

```python
def _decision(text, table_scope=None):
    from labrat.memory.model import Memory, MemoryKind, MemoryScope
    return Memory(profile="p", scope=MemoryScope.global_, kind=MemoryKind.explicit_user_rule,
                  text=text, table_scope=table_scope)


def test_cluster_decisions_only_explicit_rules():
    from labrat.maze.harvest import cluster_decisions
    from labrat.memory.model import Memory, MemoryKind, MemoryScope
    corr = Memory(profile="p", scope=MemoryScope.global_, kind=MemoryKind.chat_correction, text="c")
    clusters = cluster_decisions([_decision("attribute revenue at order time", "orders"), corr])
    assert set(clusters) == {"orders"}  # the correction is excluded


def test_draft_decision_sections_heading_and_source():
    from labrat.maze.harvest import cluster_decisions, draft_decision_sections
    drafts = draft_decision_sections(
        cluster_decisions([_decision("exclude is_test from metrics", "events")]),
        generated_at="2026-07-10T00:00:00Z")
    sec = drafts["events"][0]
    assert sec.heading == "Decisions" and sec.source == "harvested"
    assert "exclude is_test from metrics" in sec.body


def test_draft_decision_contamination_fails_loud():
    import pytest
    from labrat.maze.harvest import cluster_decisions, draft_decision_sections
    from labrat.maze.scent_audit import ScentContaminationError
    with pytest.raises(ScentContaminationError):
        draft_decision_sections(cluster_decisions([_decision("see ground_truth.csv", "t")]),
                                generated_at="2026-07-10T00:00:00Z")


def test_filter_unpromoted_drops_already_promoted(tmp_path):
    from labrat.maze.harvest import apply_approved_sections, cluster_decisions, draft_decision_sections
    from labrat.maze.store import MazeStore
    from labrat.screens.harvest_controller import filter_unpromoted_decisions
    store = MazeStore(project_root=tmp_path, home=tmp_path/"h", profile="default")
    d = _decision("attribute revenue at order time", "orders")
    # promote it once
    drafts = draft_decision_sections(cluster_decisions([d]), generated_at="2026-07-10T00:00:00Z")
    apply_approved_sections(store, "orders", drafts["orders"])
    # now it's promoted → filtered out; a NEW decision survives
    d2 = _decision("new rule about refunds", "orders")
    survivors = filter_unpromoted_decisions([d, d2], store)
    assert [m.text for m in survivors] == ["new rule about refunds"]


def test_merge_drafts_concats_per_domain():
    from labrat.maze.document import Section
    from labrat.screens.harvest_controller import merge_drafts
    a = {"orders": [Section(heading="Gotchas", body="g")]}
    b = {"orders": [Section(heading="Decisions", body="d")], "events": [Section(heading="Decisions", body="e")]}
    merged = merge_drafts(a, b)
    assert [s.heading for s in merged["orders"]] == ["Gotchas", "Decisions"]
    assert "events" in merged
```

- [ ] **Step 2: Run to verify failure** → FAIL (functions don't exist).

- [ ] **Step 3: Implement.**
- In `maze/harvest.py`: extract the grouping body of `cluster_corrections` into `_cluster_by_scope(memories)`; rewrite `cluster_corrections` as `_cluster_by_scope([m for m in memories if m.kind in _CORRECTION_KINDS])`; add `cluster_decisions` = `_cluster_by_scope([m for m in memories if m.kind == MemoryKind.explicit_user_rule])`; add `draft_decision_sections` copying `draft_harvested_sections` with `heading="Decisions"`.
- In `harvest_controller.py`: `filter_unpromoted_decisions` (load each decision's target project doc, collect bodies of its `## Decisions` sections split into bullets, drop decisions whose text matches a bullet); `review_decisions`; `merge_drafts`.

- [ ] **Step 4: Run tests** → PASS.

- [ ] **Step 5: Gates + commit** (`git add src/labrat/maze/harvest.py src/labrat/screens/harvest_controller.py tests/...`; message `"feat(decisions): cluster + draft + unpromoted-filter for decision harvesting"`).

---

### Task 2: TUI capture + harvest wiring

**Files:**
- Create: `src/labrat/screens/record_decision.py`
- Modify: `src/labrat/screens/main.py` (binding + action + capture + wire decisions into `_run_harvest_review`)
- Test: `tests/tui/test_decision_capture.py`

**Interfaces:**
- Consumes: `Memory`/`MemoryKind`/`MemoryScope`, `MemoryStore`, `resolve_table_scope` (`memory/extractor.py`), `review_decisions`/`merge_drafts` (Task 1), `Profile.harvest_opt_in`, the existing `_run_harvest_review` flow.
- Produces:
  - `RecordDecisionScreen(ModalScreen)` — a `TextArea` + Save/Cancel; on Save returns/dispatches the typed text (mirror `TrailReviewScreen`/`HarvestReviewScreen` structure).
  - `MainScreen.action_record_decision` behind `Binding("ctrl+shift+d", "record_decision", "Record Decision", show=False)` (fallback plain key noted in help/TESTING per the M2/M3 chord precedent): gated on `harvest_opt_in` (notify "Enable harvesting in Settings to record decisions." + return when off); push `RecordDecisionScreen`; on save append `Memory(profile=self._profile, scope=global_, kind=explicit_user_rule, text=<typed>, table_scope=resolve_table_scope(self._last_sql, <catalog table names>))` via `MemoryStore().append(...)`; notify "🧭 Decision recorded".
  - `_run_harvest_review` (main.py:602) additionally: read this profile's `explicit_user_rule` memories from `MemoryStore`, `review_decisions(those, store, ...)`, `merge_drafts(correction_drafts, decision_drafts)`, hand the merged dict to `HarvestReviewScreen` (already takes `dict[str, list[Section]]`).

**Catalog table names:** derive `known_tables` from `self._catalog` (iterate `schema.tables` for names) — read how another call site enumerates tables; pass `[]` when `self._catalog is None` (→ `resolve_table_scope` returns None → global decision, fine).

- [ ] **Step 1: Write the failing pilot test**

`tests/tui/test_decision_capture.py` (follow `tests/tui/test_trail_review.py`'s `_MainHost`/`_screen(opt_in=...)` pattern):

```python
async def test_record_decision_gated_off(main_host, tmp_path, monkeypatch):
    # harvest_opt_in False → ctrl+shift+d notifies, records nothing
    ...

async def test_record_decision_persists_and_harvests(main_host, tmp_path, monkeypatch):
    # opt_in True: record a decision → a explicit_user_rule Memory lands in the store;
    # then harvest review shows a "Decisions" draft; approve → ## Decisions section on disk.
    ...
```

(Complete the `...` bodies from the real fixture — assertions are the contract; monkeypatch the memory dir + LABRAT_MAZE_DIR to tmp_path.)

- [ ] **Step 2: Run to verify failure** → FAIL.

- [ ] **Step 3: Implement** per the Interfaces block. `record_decision.py` mirrors `trail_review.py`'s modal structure.

- [ ] **Step 4: Run tests** → PASS.

- [ ] **Step 5: Docs + gates + commit.** `TESTING.md`: a "Decision-trail (v1)" section (enable harvesting, ctrl+shift+d, record, ctrl+shift+h, approve, confirm `## Decisions` on disk + retrieval next session). `decisions.md`: dated entry. `git add src/labrat/screens/record_decision.py src/labrat/screens/main.py tests/tui/test_decision_capture.py TESTING.md decisions.md`; message `"feat(decisions): ctrl+shift+d capture + harvest-review wiring"`.

---

## Manual gate (after Task 2, before merge)

Via the pty harness (or a scripted end-to-end): enable harvesting in Settings; `ctrl+shift+d`, type a decision, save (→ Memory persisted); `ctrl+shift+h` → the review shows a `Decisions` draft alongside any Gotchas; approve → `labrat_maze/scent/<domain>.md` gains a `## Decisions` section (`Source: harvested`); in a fresh session, `search_reference_docs` for a matching query returns it. Confirm the gate: with `harvest_opt_in` off, `ctrl+shift+d` records nothing.

## Execution notes

- Branch: `feat/decision-trail` off master; merge after whole-branch Fable review + the manual gate (D1 "what is a decision" is surfaced there for the user).
- Strict task order (2 consumes 1).
- Task 2's implementer re-verifies the `_run_harvest_review` body + MemoryStore access + catalog-table enumeration against live source before wiring.
