# dbt-CI Follow-ups Implementation Plan (Q6)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** close the two deferred correctness tickets from the dbt-CI whole-branch review — F3 (removing the semantic layer deadlocks the `scent check` gate forever) and the auto-detect-subdir store-rooting false pass — both in freshly-shipped code, both benchmark-safe.

**Architecture:** F3 makes `ingest_dbt_semantics` *clear* stale `semantic_layer` sections + update the sidecar when the manifest's semantic subset became empty after a prior ingest, so `scent ingest` actually unblocks a "removed the semantic layer" PR. The subdir fix roots `scent check`'s store + scent-dir at the *resolved* dbt project, not cwd.

**Tech Stack:** existing `maze/semantic_ingest.py`, `maze/store.py`, `cli.py`. No new deps.

## Global Constraints

- **Benchmark-safe:** `ingest_dbt_semantics` is called only by the TUI F9 path and the `scent ingest` CLI — NOT the Cartographer/`cartograph_prepass` benchmark path (verified). The change must not touch `cartographer.py` or any `eval/`/`mcp/` path.
- **Contamination-audit preserved:** any doc rewritten by the F3 clear-pass runs through `audit_scent_doc` fail-loud before write, exactly as the ingest write-loop already does.
- **Reuse:** `MazeStore.load_domain`/`write_doc`, `project_scent_dir`, `write_manifest_fingerprint`, `_resolve_dbt_project`/`_find_dbt_project_upward` consumed as-is.
- Pyright strict for `maze/`, `cli.py`. Gates per commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. `test_app_renders` env flake non-signal; `git checkout -- snapshot_report.html` if regenerated.

---

### Task 1: F3 (clear stale semantics on empty ingest) + subdir store-rooting

**Files:**
- Modify: `src/labrat/maze/semantic_ingest.py` (`ingest_dbt_semantics` empty-artifacts branch), `src/labrat/cli.py` (`scent check` store/scent-dir rooting)
- Test: `tests/unit/test_maze_ci.py` (F3 round-trip), `tests/unit/test_cli_scent.py` (subdir), and a `semantic_ingest` unit test file if one exists (grep)

**Interfaces:**
- Consumes: `MazeStore` (`load_domain(domain, scope="project")`, `write_doc`), `project_scent_dir`, `write_manifest_fingerprint`, `audit_scent_doc`/`ScentContaminationError`, `semantic_fingerprint`, `_resolve_dbt_project`.
- Produces: no new public API. `IngestOutcome` gains a `cleared: int = 0` field (sections removed) so the CLI/TUI can report "cleared N stale semantic sections".

**F3 mechanism:** in `ingest_dbt_semantics`, the empty-artifacts branch currently `return IngestOutcome(skipped=True, ...)` WITHOUT clearing. Replace with a clear-pass: enumerate existing PROJECT-layer scent docs (glob `project_scent_dir/*.md` → domain = stem, or reuse the store), and for each doc that has `source == "semantic_layer"` sections, strip them, `audit_scent_doc` (fail-loud), `write_doc`; count removed sections. Then `write_manifest_fingerprint(project_scent_dir, semantic_fingerprint(manifest))` (the new empty-subset fingerprint, so a subsequent `scent check` sees fresh). Return `IngestOutcome(domains=<cleared domains>, cleared=<count>, warnings=...)`. This runs only when the code is PAST the fingerprint gate (i.e. the semantic subset actually changed / `force`), so a genuinely-empty project with no prior ingest just writes the fingerprint and clears nothing.

**Subdir mechanism:** in `scent check` (`cli.py`), when `--scent-dir` is NOT given, derive both the store root and the scent-dir from the *resolved* `project_path` (`project_path / "labrat_maze" / "scent"` and a `MazeStore` rooted at `project_path`), not from cwd's `project_scent_dir()`. When `--scent-dir` IS given, keep the existing `scent_dir.parent.parent` rooting (the F2 fix). This closes the "bare `scent check` from a subdir reports OK (0 domains)" false pass.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_maze_ci.py` — F3 round-trip (reuse the fixture helpers already there):

```python
def test_removing_semantics_clears_scent_and_unblocks_check(tmp_path):
    from labrat.maze.ci import check_scent_freshness
    from labrat.maze.semantic_ingest import ingest_dbt_semantics
    from labrat.maze.ci import catalog_from_dbt
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = _ingest(project, scent)  # fresh, has a ## Semantic Model section
    scdir = scent / "labrat_maze" / "scent"
    # remove ALL semantic_models from the manifest, then re-ingest (the fix path)
    _write_manifest(project, {"nodes": _manifest()["nodes"], "semantic_models": {}, "metrics": {}})
    outcome = ingest_dbt_semantics(
        manifest_path=project / "target" / "manifest.json",
        catalog=catalog_from_dbt(project), store=store,
        project_scent_dir=scdir, force=True)
    assert outcome.cleared >= 1
    # check now passes (no stale semantic Scent left)
    res = check_scent_freshness(project, scdir, store=store)
    assert res.ok is True
```

`tests/unit/test_cli_scent.py` — subdir (chdir into a subdir of the project, bare `scent check`, no `--scent-dir`):

```python
def test_scent_check_from_subdir_uses_resolved_project(tmp_path, monkeypatch):
    # build a stale-schema fixture; from a subdir with no --scent-dir + no profile,
    # bare `scent check` must resolve upward AND root the store at the project
    # → reports the real verdict (exit 1 stale), not a false "no committed Scent" 0.
    ...
```

(Complete the subdir test from the existing fixture pattern; assertions are the contract.)

- [ ] **Step 2: Run to verify failure** → F3 test fails (`cleared` attr / still stale); subdir test fails (false 0).

- [ ] **Step 3: Implement** both mechanisms per the Interfaces block. Add `cleared: int = 0` to `IngestOutcome`. Update the `scent ingest` CLI + TUI F9 summary to mention `cleared` when >0 (small, optional print).

- [ ] **Step 4: Run tests** → PASS. Also re-run the full dbt-CI suites (`test_maze_ci.py`, `test_cli_scent.py`) to confirm no regression, and `test_main_screen_semantic.py` (the TUI F9 path).

- [ ] **Step 5: Gates + commit** (`git add src/labrat/maze/semantic_ingest.py src/labrat/cli.py tests/...`; message `"fix(dbt-ci): clear stale semantics on empty ingest (F3) + subdir store-rooting"`).

---

## Verification gate (after Task 1, before merge)

A scripted end-to-end: ingest a fixture with semantics (fresh), remove all `semantic_models`, `scent check` → exit 1 (stale), `scent ingest` → clears + updates sidecar, `scent check` → exit 0. And: from a subdir of a stale-schema project, bare `scent check` → exit 1 (not a false 0). Whole-branch review covers the rest.

## Execution notes

- Branch: `feat/dbt-ci-followups` off master; merge after whole-branch Fable review + the gate.
- Single task (two small, related, benchmark-safe fixes to freshly-shipped code).
- The F3 clear-pass MUST run `audit_scent_doc` before each rewrite (fail-loud) — a stripped doc is still an audited write.
