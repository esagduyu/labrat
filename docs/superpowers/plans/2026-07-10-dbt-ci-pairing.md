# dbt-CI At-Source Pairing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** a read-only `labrat scent check` CLI that fails a PR when a committed dbt project's semantics/schema have drifted from the committed Scent — reusing the existing fingerprint machinery, offline, never writing to the repo.

**Architecture:** `maze/ci.py` holds pure read-only consistency logic (build an offline catalog via `DbtLoader`, recompute `semantic_fingerprint` + `fingerprint_from_catalog`, compare to the committed `.manifest_fingerprint` sidecar + section `schema_hash`es). A new `scent` Typer subcommand group exposes `check` (gate), `ingest` (headless fix), and `init-ci` (optional scaffold).

**Tech Stack:** Typer (existing CLI), the existing `catalog/dbt/`, `maze/semantic_ingest.py`, `maze/staleness.py`, `maze/store.py`. No new deps.

## Global Constraints (from the spec — every task inherits)

- **Read-only:** `maze/ci.py` and `labrat scent check` never write to the repo, Scent, or any sidecar. The only write path is `labrat scent ingest`, run deliberately.
- **Offline:** no live warehouse; `manifest.json` (from `dbt parse`) is sufficient. Never open a DB connection in the check.
- **Honest-unknown:** missing manifest / unparseable project / no committed Scent each produce a distinct, clear outcome — never a false pass or fail. Missing manifest → exit 1 unless `--skip-if-no-manifest`.
- **Reuse, don't fork:** `DbtLoader`, `semantic_fingerprint`, `parse_semantic_manifest`, `fingerprint_from_catalog`, `read_manifest_fingerprint`, `Section.schema_hash` consumed as-is; no parallel hashing/parsing.
- **Benchmark isolation:** nothing under `src/labrat/eval/` or `src/labrat/mcp/` imports `maze/ci.py`.
- **Schema-hash is WHOLE-CATALOG** (`fingerprint_from_catalog` returns one hash stamped on every semantic section): schema drift is a project-level signal flagged on all domains carrying semantic sections, not per-table.
- Pyright strict for `maze/` and `cli.py`. Gates per commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. `test_app_renders` env flake non-signal; `git checkout -- snapshot_report.html` if regenerated.

---

### Task 1: `maze/ci.py` — read-only consistency check

**Files:**
- Create: `src/labrat/maze/ci.py`, `tests/unit/test_maze_ci.py`, `tests/fixtures/dbt_ci/` (fixture builder helper in the test file)

**Interfaces:**
- Consumes: `catalog.dbt.loader.DbtLoader`; `maze.semantic_ingest.{semantic_fingerprint, read_manifest_fingerprint, ingest_dbt_semantics}`; `maze.staleness.fingerprint_from_catalog`; `catalog.dbt.semantic.parse_semantic_manifest`; `maze.store.MazeStore`; `db.catalog.Catalog` (DbtLoader returns `dict[str, CatalogEntry]` — wrap into a `Catalog` the same way the app does; read `screens/main.py::_run_semantic_ingest` for how a `Catalog` is built from a `DbtLoader` result, and mirror it).
- Produces:
  - `StaleDomain(BaseModel)`: `domain: str`, `reason: Literal["semantic_drift", "schema_drift"]`, `committed_hash: str | None`, `current_hash: str | None`.
  - `CiCheckResult(BaseModel)`: `ok: bool`, `stale: list[StaleDomain]`, `checked: int`, `warnings: list[str]`, `fix_command: str`, `manifest_found: bool`.
  - `check_scent_freshness(dbt_project_path: Path, scent_dir: Path, *, store: MazeStore) -> CiCheckResult`.

**Note on catalog construction (verified — no existing bridge to reuse):** the TUI's `_run_semantic_ingest` uses the *live warehouse* catalog (`self._catalog`), so there is NO DbtLoader→Catalog bridge to extract — the offline check must build one. `DbtLoader(path).load()` returns `dict[str, CatalogEntry]` where `CatalogEntry(name, schema_name, columns: dict[str, ColumnEntry(name, description, data_type, is_pii)], ...)` (catalog/base.py). Convert to `db.catalog.Catalog` (verified shapes: `Column(name, data_type, nullable, comment=None)`, `Table(name, schema_name, columns: list[Column])`, `Schema(name, tables)`, `Catalog(database_name, schemas)` — all `frozen`) like this:

```python
from collections import defaultdict
from labrat.catalog.dbt.loader import DbtLoader
from labrat.db.catalog import Catalog, Column, Schema, Table

def _catalog_from_dbt(dbt_project_path: Path) -> Catalog | None:
    try:
        entries = DbtLoader(dbt_project_path).load()
    except Exception:  # noqa: BLE001 — unloadable dbt project → honest-unknown None
        return None
    if not entries:
        return None
    by_schema: dict[str, list[Table]] = defaultdict(list)
    for e in entries.values():
        cols = [
            Column(name=c.name, data_type=c.data_type or "", nullable=True,
                   comment=c.description or None)
            for c in e.columns.values()
        ]
        by_schema[e.schema_name].append(Table(name=e.name, schema_name=e.schema_name, columns=cols))
    schemas = [Schema(name=s, tables=sorted(ts, key=lambda t: t.name)) for s, ts in sorted(by_schema.items())]
    return Catalog(database_name="dbt", schemas=schemas)
```

`fingerprint_from_catalog` only reads `table.name` + column names, so `data_type=""`/`nullable=True` placeholders are harmless. This helper is the one the Task-1 test imports as `_catalog_from_dbt`.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_maze_ci.py`:

```python
"""Read-only dbt↔Scent consistency check (maze/ci.py)."""

import json
from pathlib import Path

from labrat.maze.ci import CiCheckResult, check_scent_freshness
from labrat.maze.store import MazeStore


def _manifest(measure_expr: str = "revenue", extra_col: bool = False) -> dict:
    cols = {"amount": {"name": "amount", "meta": {}}}
    if extra_col:
        cols["region"] = {"name": "region", "meta": {}}
    return {
        "nodes": {
            "model.demo.orders": {
                "resource_type": "model", "name": "orders", "schema": "analytics",
                "columns": cols, "depends_on": {"nodes": []}, "compiled_code": "select 1",
            }
        },
        "semantic_models": [
            {"name": "orders_sm", "node_relation": {"alias": "orders"},
             "description": "orders", "entities": [{"name": "id", "type": "primary"}],
             "dimensions": [{"name": "day", "type": "time"}],
             "measures": [{"name": "rev", "agg": "sum", "expr": measure_expr}]}
        ],
        "metrics": [],
    }


def _write_manifest(project: Path, manifest: dict) -> None:
    target = project / "target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "manifest.json").write_text(json.dumps(manifest))
    (project / "dbt_project.yml").write_text("name: demo\nprofile: demo\n")


def _ingest(project: Path, scent_root: Path) -> MazeStore:
    """Run the real ingest so the committed Scent + sidecar are 'fresh'."""
    from labrat.maze.ci import _catalog_from_dbt  # the shared bridge helper
    from labrat.maze.semantic_ingest import ingest_dbt_semantics

    store = MazeStore(project_root=scent_root, home=scent_root / "home", profile="default")
    ingest_dbt_semantics(
        manifest_path=project / "target" / "manifest.json",
        catalog=_catalog_from_dbt(project),
        store=store,
        project_scent_dir=scent_root / "labrat_maze" / "scent",
        force=True,
    )
    return store


def test_fresh_state_ok(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = _ingest(project, scent)
    res = check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    assert isinstance(res, CiCheckResult)
    assert res.ok is True and res.stale == [] and res.manifest_found is True


def test_semantic_drift_flagged(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = _ingest(project, scent)
    # change the measure expr WITHOUT re-ingesting → sidecar now stale
    _write_manifest(project, _manifest(measure_expr="net_revenue"))
    res = check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    assert res.ok is False
    assert any(s.reason == "semantic_drift" for s in res.stale)
    assert "labrat scent ingest" in res.fix_command


def test_schema_drift_flagged(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = _ingest(project, scent)
    # add a column (schema change), keep semantics identical → schema_hash diverges
    _write_manifest(project, _manifest(extra_col=True))
    res = check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    assert res.ok is False
    assert any(s.reason == "schema_drift" for s in res.stale)


def test_missing_manifest(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    (project).mkdir()
    store = MazeStore(project_root=scent, home=scent / "home", profile="default")
    res = check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    assert res.manifest_found is False and res.ok is False


def test_no_committed_scent_passes(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = MazeStore(project_root=scent, home=scent / "home", profile="default")
    res = check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    # manifest present, but no ingested Scent/sidecar → nothing to be stale
    assert res.ok is True and res.stale == []


def test_read_only_no_writes(tmp_path):
    project, scent = tmp_path / "proj", tmp_path / "scent"
    _write_manifest(project, _manifest())
    store = _ingest(project, scent)
    before = {p: p.read_bytes() for p in (scent / "labrat_maze").rglob("*") if p.is_file()}
    check_scent_freshness(project, scent / "labrat_maze" / "scent", store=store)
    after = {p: p.read_bytes() for p in (scent / "labrat_maze").rglob("*") if p.is_file()}
    assert before == after  # the check writes nothing
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/unit/test_maze_ci.py -v`
Expected: FAIL (`ModuleNotFoundError: labrat.maze.ci`)

- [ ] **Step 3: Implement `src/labrat/maze/ci.py`**

Implement per the Interfaces block. Logic:
1. `_catalog_from_dbt(dbt_project_path) -> Catalog | None` — the shared DbtLoader→Catalog bridge (extracted from `screens/main.py` per the note; returns None if the project can't be loaded).
2. `check_scent_freshness`:
   - `manifest_path = dbt_project_path / "target" / "manifest.json"`; if missing → `CiCheckResult(ok=False, manifest_found=False, warnings=["manifest.json not found — run `dbt parse` first"], stale=[], checked=0, fix_command=_FIX)`.
   - `manifest = json.loads(...)` (guard OSError/ValueError → warning + ok=False, manifest_found=True).
   - `committed_manifest_fp = read_manifest_fingerprint(scent_dir)`. If `None` (no ingested Scent) → `ok=True, stale=[], warnings=["no committed Scent to check"]`, checked=0. (Non-negotiable honest-unknown: absence ≠ stale.)
   - `current_manifest_fp = semantic_fingerprint(manifest)`.
   - Load committed trail... no — load committed Scent docs: `store.docs(kind="scent")`; the domains carrying `semantic_layer` sections are the checkable set.
   - **Semantic drift:** if `current_manifest_fp != committed_manifest_fp` → append a `StaleDomain(domain, "semantic_drift", committed_manifest_fp, current_manifest_fp)` for each domain with semantic sections (attribute via `parse_semantic_manifest` tables ∩ committed semantic domains; if the intersection is empty but the fp differs, still flag a single project-level entry with `domain="(semantic layer)"`).
   - **Schema drift:** `catalog = _catalog_from_dbt(...)`; if catalog is not None, `current_schema_fp = fingerprint_from_catalog(catalog)`; for each committed semantic section, its `schema_hash` is the uniform committed value — if it differs from `current_schema_fp`, append `StaleDomain(domain, "schema_drift", section.schema_hash, current_schema_fp)` once per affected domain. (Dedup domains already flagged semantic_drift? No — a domain can be both; keep distinct reasons, one StaleDomain per (domain, reason).)
   - `ok = not stale`; `checked = <#semantic domains examined>`; `fix_command = _FIX` where `_FIX = "labrat scent ingest --dbt-project <path>"` (interpolate the path).
   - **No writes anywhere.**

- [ ] **Step 4: Run tests** → `uv run pytest tests/unit/test_maze_ci.py -v` → ALL PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/maze/ci.py tests/unit/test_maze_ci.py
git commit -m "feat(dbt-ci): read-only dbt<->Scent consistency check (maze/ci.py)"
```

---

### Task 2: `labrat scent check` + `labrat scent ingest`

**Files:**
- Modify: `src/labrat/cli.py`
- Test: `tests/unit/test_cli_scent.py`

**Interfaces:**
- Consumes: `check_scent_freshness`/`CiCheckResult` (Task 1); `ingest_dbt_semantics`; `MazeStore`; `ProfileManager` (for `--profile` → `dbt_project_path` default); `maze.store.project_scent_dir`.
- Produces: a `scent_app = typer.Typer(name="scent", ...)` group registered via `app.add_typer`, with `check` and `ingest` commands.

- [ ] **Step 1: Write the failing CLI tests**

`tests/unit/test_cli_scent.py` — use Typer's `CliRunner` (read an existing `conn`-command test for the pattern). Build the same fixture as Task 1 (import the helpers or duplicate minimally), then:

```python
def test_scent_check_fresh_exit_0(tmp_path): ...   # exit_code == 0
def test_scent_check_stale_exit_1(tmp_path): ...   # after a semantic change, exit_code == 1, "semantic_drift" in output
def test_scent_check_warn_only_exit_0(tmp_path): ... # stale + --warn-only → exit_code 0, warning still printed
def test_scent_check_json(tmp_path): ...           # --format json → parseable CiCheckResult
def test_scent_check_missing_manifest_exit_1(tmp_path): ...
def test_scent_check_skip_if_no_manifest(tmp_path): ... # --skip-if-no-manifest → exit 0
def test_scent_ingest_writes(tmp_path): ...        # labrat scent ingest --dbt-project ... writes trail/... no: scent/ docs + sidecar
```

(Read `tests/` for how CLI commands are invoked in this repo — `typer.testing.CliRunner().invoke(app, [...])`. The assertions above are the contract.)

- [ ] **Step 2: Run to verify failure** → FAIL (no `scent` command).

- [ ] **Step 3: Implement** in `cli.py`:
- `scent_app = typer.Typer(name="scent", help="Check and refresh dbt-paired Scent.")`; `app.add_typer(scent_app, name="scent")`.
- `@scent_app.command("check")` with options `--dbt-project` (default: resolve from `--profile`'s `dbt_project_path`, else error with guidance), `--scent-dir` (default `project_scent_dir()`), `--warn-only`, `--skip-if-no-manifest`, `--format` (`text`|`json`). Build `MazeStore.from_env(profile)`; call `check_scent_freshness`; render; `raise typer.Exit(code)` where code = 0 if `res.ok` or `--warn-only` or (not manifest_found and `--skip-if-no-manifest`), else 1. `--format json` → `typer.echo(res.model_dump_json())`.
- `@scent_app.command("ingest")` — headless `ingest_dbt_semantics(force=True)` (the fix path): resolve manifest = `<dbt_project>/target/manifest.json`, build catalog via the Task-1 bridge, `MazeStore.from_env(profile)`, `project_scent_dir()`, `git_root=Path.cwd()`. Print the `IngestOutcome` summary. Writes go through the unchanged audit + git_sha path.

- [ ] **Step 4: Run tests** → ALL PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/cli.py tests/unit/test_cli_scent.py
git commit -m "feat(dbt-ci): labrat scent check (gate) + scent ingest (headless fix)"
```

---

### Task 3: `labrat scent init-ci` scaffold + docs

**Files:**
- Modify: `src/labrat/cli.py`
- Create: `docs/dbt-ci-pairing.md`
- Test: extend `tests/unit/test_cli_scent.py`

**Interfaces:** `@scent_app.command("init-ci")` with `--platform github` (only value in v1), `--path` (default `.github/workflows/labrat-scent.yml`). No-clobber (refuse if the file exists; print the path either way).

- [ ] **Step 1: Failing test**

```python
def test_init_ci_writes_workflow(tmp_path): ...    # creates .github/workflows/labrat-scent.yml containing "dbt parse" and "labrat scent check"
def test_init_ci_no_clobber(tmp_path): ...         # existing file → exit non-zero or a clear "exists" message, file unchanged
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement** the command (writes a starter workflow: checkout → setup python/uv → `pip install`/`uv` → `dbt parse` → `labrat scent check`) + write `docs/dbt-ci-pairing.md` (colocate → wire check → failure looks like → fix with `labrat scent ingest`; cross-link `docs/team-scent.md`; note it's the paid/team-scale complement to free Team Scent). Add a `decisions.md` dated entry.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Gates + commit** (`git add cli.py docs/dbt-ci-pairing.md decisions.md tests/unit/test_cli_scent.py`; message `"feat(dbt-ci): scent init-ci scaffold + workflow docs"`).

---

## Manual/verification gate (after Task 3, before merge)

No TUI — a CLI/artifact gate: build the fixture dbt project, run `labrat scent check` (fresh → exit 0), mutate a semantic model, re-run (→ exit 1 + `semantic_drift` + fix command), run `labrat scent ingest`, re-check (→ exit 0). Run `labrat scent init-ci` and confirm the workflow file contains both `dbt parse` and `labrat scent check`. Confirm the working tree is byte-unchanged by any `check` run.

## Execution notes

- Branch: `feat/dbt-ci-pairing` off master; merge after whole-branch Fable review + the gate above.
- Strict task order (2 consumes 1; 3 extends 2).
- Task 1's `_catalog_from_dbt` bridge is net-new (verified: no existing DbtLoader→Catalog bridge — the TUI uses the live warehouse catalog). Use the exact conversion in the Task-1 note.
