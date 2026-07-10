# Team Scent v1 (Moat 2.3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make project-layer Scent a first-class team artifact: a read-only status report (inventory/tiers/freshness/drift) + `git_sha` provenance stamping on the two derived write paths + the team workflow doc.

**Architecture:** Pure `maze/status.py` (`build_status` over MazeStore + optional Catalog → typed rows; `render_status` → plain table) + `maze/print_status.py` module CLI; a tiny `maze/gitmeta.py::current_git_sha(root) -> str | None` helper (subprocess, None-safe) consumed by `apply_approved_sections` and `ingest_dbt_semantics` at Section-append/build time (before audit); `docs/team-scent.md`.

**Tech Stack:** Python 3.12, pytest (`asyncio_mode="auto"`), ruff, pyright strict (`maze/` strict).

**Spec:** `docs/superpowers/specs/2026-07-10-team-scent-design.md` (D1–D4).

## Global Constraints

- Branch: `feat/team-scent` off master.
- Status surface READ-ONLY (no store writes; stdout only).
- Stamping None-safe: no repo / no git / subprocess failure → `git_sha=None`, writes byte-identical to today (Meta renderer omits None). Stamp BEFORE audit (audited == written).
- T1b determinism relaxation is EXACTLY as specced (repo-state-conditioned); apply idempotence stays byte-stable within a repo state (dedup keeps existing sections' stamps).
- No LLM, no clock. Existing tests unmodified except added stamp pins.
- Pyright strict on `maze/`. Repo gates per commit, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- Known env flake `tests/tui/test_app_renders.py` — non-signal; restore `snapshot_report.html` if regenerated.

---

## File Structure

- Create: `src/labrat/maze/status.py`, `src/labrat/maze/print_status.py`, `src/labrat/maze/gitmeta.py`, `docs/team-scent.md`.
- Modify: `src/labrat/maze/harvest.py` (apply stamp), `src/labrat/maze/semantic_ingest.py` (ingest stamp), `decisions.md`.
- Tests: `tests/unit/test_maze_status.py`, `tests/unit/test_gitmeta_stamping.py`.

---

### Task 1: `maze/status.py` + CLI

**Files:**
- Create: `src/labrat/maze/status.py`, `src/labrat/maze/print_status.py`
- Test: `tests/unit/test_maze_status.py`

**Interfaces:**
- Consumes: `MazeStore.docs()` (merged view; doc.scope ∈ user/project/merged), `Section.source/schema_hash`, `best_source` (`maze/provenance.py`), `fingerprint_from_catalog` + `read_scent_fingerprint` (`maze/staleness.py`), `read_manifest_fingerprint` (`maze/semantic_ingest.py`), `Catalog` (`labrat.db.catalog`).
- Produces:
  - `DomainStatus` (Pydantic): `domain: str`, `scope: str`, `sections: int`, `tier_counts: dict[str, int]`, `best: str`, `fresh: int`, `stale: int`, `unknown: int` (per-section schema_hash vs the given fingerprint; all-unknown when no catalog).
  - `MazeStatus` (Pydantic): `rows: list[DomainStatus]` (domain-sorted), `current_fingerprint: str | None`, `scent_sidecar_stale: bool | None` (user-layer `.schema_fingerprint` vs current; None when either side missing), `manifest_sidecar_present: bool`.
  - `build_status(store: MazeStore, *, catalog: Catalog | None = None, user_scent_dir: Path | None = None, project_scent_dir: Path | None = None) -> MazeStatus`.
  - `render_status(status: MazeStatus) -> str` — header line (`fingerprint: <8-char>… | scent sidecar: fresh|stale|n/a | manifest sidecar: yes|no`) + one aligned row per domain: `domain  scope  sections  best  fresh/stale/unknown  tiers`.
  - CLI `python -m labrat.maze.print_status [--profile P] [--db /path.duckdb] [--project-root DIR]` — builds `MazeStore(project_root or cwd-env-rule, home, profile)` via the same env-or-cwd rule as `project_scent_dir()`; `--db` connects read-only DuckDB for freshness (optional); prints `render_status`; exit 2 on connection/arg errors, 0 otherwise. READ-ONLY.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_maze_status.py
"""Team-scent status surface: inventory, tiers, freshness, sidecars. READ-ONLY."""

from pathlib import Path

from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.maze.document import ScentDoc, Section, render_document
from labrat.maze.staleness import fingerprint_from_catalog, write_scent_fingerprint
from labrat.maze.status import build_status, render_status
from labrat.maze.store import MazeStore


def _catalog() -> Catalog:
    return Catalog(
        database_name="db",
        schemas=[Schema(name="main", tables=[
            Table(name="orders", schema_name="main",
                  columns=[Column(name="id", data_type="INTEGER", nullable=False)])
        ])],
    )


def _seed(tmp_path: Path, fp: str) -> MazeStore:
    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")
    user = tmp_path / "home" / ".labrat" / "maze" / "p1" / "scent"
    proj = tmp_path / "proj" / "labrat_maze" / "scent"
    user.mkdir(parents=True); proj.mkdir(parents=True)
    (user / "orders.md").write_text(render_document(ScentDoc(domain="orders", sections=[
        Section(heading="Key Tables", body="- orders", source="verified", schema_hash=fp),
    ])), encoding="utf-8")
    (proj / "orders.md").write_text(render_document(ScentDoc(domain="orders", sections=[
        Section(heading="Gotchas", body="- exclude test", source="harvested"),
    ])), encoding="utf-8")
    (proj / "metrics.md").write_text(render_document(ScentDoc(domain="metrics", sections=[
        Section(heading="Metric: Revenue", body="- type: simple",
                source="semantic_layer", schema_hash="stalehash"),
    ])), encoding="utf-8")
    return store


def test_build_status_rows(tmp_path: Path) -> None:
    cat = _catalog()
    fp = fingerprint_from_catalog(cat)
    store = _seed(tmp_path, fp)
    status = build_status(
        store, catalog=cat,
        user_scent_dir=tmp_path / "home" / ".labrat" / "maze" / "p1" / "scent",
        project_scent_dir=tmp_path / "proj" / "labrat_maze" / "scent",
    )
    rows = {r.domain: r for r in status.rows}
    assert list(rows) == sorted(rows)                      # domain-sorted
    orders = rows["orders"]
    assert orders.scope == "merged" and orders.sections == 2
    assert orders.best == "semantic_layer" or orders.best == "verified"
    # ^ orders has verified+harvested → best == "verified"; fix the assertion:
    assert rows["orders"].best == "verified"
    assert orders.fresh == 1 and orders.unknown == 1       # stamped-fresh + unstamped
    metrics = rows["metrics"]
    assert metrics.best == "semantic_layer" and metrics.stale == 1
    assert status.manifest_sidecar_present is False


def test_no_catalog_all_unknown(tmp_path: Path) -> None:
    store = _seed(tmp_path, "whatever")
    status = build_status(store)
    for r in status.rows:
        assert r.fresh == 0 and r.stale == 0 and r.unknown == r.sections
    assert status.current_fingerprint is None
    assert status.scent_sidecar_stale is None


def test_scent_sidecar_states(tmp_path: Path) -> None:
    cat = _catalog()
    fp = fingerprint_from_catalog(cat)
    store = _seed(tmp_path, fp)
    user = tmp_path / "home" / ".labrat" / "maze" / "p1" / "scent"
    write_scent_fingerprint(user, fp)
    fresh = build_status(store, catalog=cat, user_scent_dir=user)
    assert fresh.scent_sidecar_stale is False
    write_scent_fingerprint(user, "drifted")
    stale = build_status(store, catalog=cat, user_scent_dir=user)
    assert stale.scent_sidecar_stale is True


def test_render_is_plain_table(tmp_path: Path) -> None:
    store = _seed(tmp_path, "x")
    text = render_status(build_status(store))
    assert "orders" in text and "metrics" in text
    assert "harvested" in text and "semantic_layer" in text
    assert "\x1b" not in text                              # no ANSI


def test_read_only(tmp_path: Path) -> None:
    store = _seed(tmp_path, "x")
    proj = tmp_path / "proj" / "labrat_maze" / "scent"
    before = {p.name: p.read_bytes() for p in proj.glob("*")}
    build_status(store, project_scent_dir=proj)
    after = {p.name: p.read_bytes() for p in proj.glob("*")}
    assert before == after
```

(Fix the sloppy double-assert on `orders.best` when writing the real file — keep only the correct `== "verified"` one. Catalog fixture fields per `tests/unit/test_staleness_catalog.py` conventions.)

- [ ] **Step 2: FAIL** (`ModuleNotFoundError`), **Step 3: implement** per the Interfaces block (pure; `tier_counts` via `collections.Counter` over `s.source`; freshness trio per section: `schema_hash is None` → unknown, `== fp` → fresh, else stale; when `catalog is None` everything unknown and `current_fingerprint=None`; `scent_sidecar_stale` = `is_stale(read_scent_fingerprint(user_dir), fp)` only when both catalog and user_dir given AND a sidecar exists, else None; `manifest_sidecar_present` = `read_manifest_fingerprint(project_dir) is not None` when the dir is given). CLI in `print_status.py`: argparse; store construction `MazeStore(project_root=Path(args.project_root or os.environ.get("LABRAT_MAZE_DIR") or os.getcwd()), home=Path.home(), profile=args.profile or "default")`; `--db` → `DuckDBConnection(path, read_only=True)` + connect + introspect (errors → stderr + exit 2, disconnect in finally); print table; also pass `user_scent_dir=user_scent_dir(profile)` and `project_scent_dir=project_scent_dir(...)` (import both helpers from `maze/store.py`).

- [ ] **Step 4: gates + commit** — `feat(maze): team-scent status surface (build_status + print_status CLI)`; git add the 3 files.

---

### Task 2: `git_sha` provenance stamping

**Files:**
- Create: `src/labrat/maze/gitmeta.py`
- Modify: `src/labrat/maze/harvest.py` (`apply_approved_sections`), `src/labrat/maze/semantic_ingest.py` (`ingest_dbt_semantics`)
- Test: `tests/unit/test_gitmeta_stamping.py`

**Interfaces:**
- Produces: `gitmeta.current_git_sha(root: Path) -> str | None` — `git -C <root> rev-parse --short HEAD` via `subprocess.run(capture_output=True, timeout=5)`; ANY failure (no repo, no git, timeout, nonzero) → `None`, never raises.
- `apply_approved_sections(store, domain, approved, *, git_root: Path | None = None)` — when `git_root` given and sha resolves, appended sections get `git_sha=<sha>` via `model_copy(update=...)` at append time (BEFORE audit); dedup-skipped sections untouched. Default `None` = today's bytes (all callers unchanged; the TUI's HarvestReviewScreen call gains `git_root=Path.cwd()`-equivalent via the project root it already knows — pass through from `harvest_review.py` `action_apply` using the store's project root: since MazeStore doesn't expose it, thread `git_root: Path | None` from the screen's constructor... SIMPLER: `apply_approved_sections` derives it itself when `git_root is None`? NO — keep explicit: the screen passes `git_root=Path(os.environ.get("LABRAT_MAZE_DIR") or os.getcwd())` mirroring `project_scent_dir()`'s rule via a small import of that helper's parent logic; concretely `project_scent_dir().parent.parent` is the root — just compute `Path(os.environ.get("LABRAT_MAZE_DIR") or os.getcwd())` inline in the screen with a comment).
- `ingest_dbt_semantics(..., git_root: Path | None = None)` — same: stamps the built sections before audit; MainScreen's worker passes the same root it computes for `project_scent_dir()` (override branch: the override root).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_gitmeta_stamping.py
"""git_sha provenance on derived Scent writes; None-safe everywhere."""

import subprocess
from pathlib import Path

from labrat.maze.document import ScentDoc, Section
from labrat.maze.gitmeta import current_git_sha
from labrat.maze.harvest import apply_approved_sections
from labrat.maze.store import MazeStore


def _git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "f").write_text("x")
    subprocess.run(["git", "add", "f"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root, check=True,
    )
    return root


def test_current_git_sha_in_repo_and_out(tmp_path: Path) -> None:
    root = _git_repo(tmp_path)
    sha = current_git_sha(root)
    assert sha is not None and 6 <= len(sha) <= 12
    assert current_git_sha(tmp_path / "not-a-repo") is None


def test_apply_stamps_when_root_given(tmp_path: Path) -> None:
    root = _git_repo(tmp_path)
    store = MazeStore(project_root=root, home=tmp_path / "home", profile="p1")
    apply_approved_sections(
        store, "orders",
        [Section(heading="Gotchas", body="- keep", source="harvested")],
        git_root=root,
    )
    doc = store.load_domain("orders", scope="project")
    assert doc is not None and doc.sections[0].git_sha == current_git_sha(root)


def test_apply_without_root_byte_identical(tmp_path: Path) -> None:
    store = MazeStore(project_root=tmp_path / "p", home=tmp_path / "h", profile="p1")
    apply_approved_sections(
        store, "orders", [Section(heading="Gotchas", body="- b", source="harvested")]
    )
    doc = store.load_domain("orders", scope="project")
    assert doc is not None and doc.sections[0].git_sha is None


def test_reapply_keeps_existing_stamp(tmp_path: Path) -> None:
    root = _git_repo(tmp_path)
    store = MazeStore(project_root=root, home=tmp_path / "home", profile="p1")
    sec = [Section(heading="Gotchas", body="- same", source="harvested")]
    apply_approved_sections(store, "orders", sec, git_root=root)
    first = store.load_domain("orders", scope="project").sections[0].git_sha
    # new commit, re-apply same body → dedup keeps the ORIGINAL stamp
    (root / "g").write_text("y")
    subprocess.run(["git", "add", "g"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "two"],
        cwd=root, check=True,
    )
    apply_approved_sections(store, "orders", sec, git_root=root)
    doc = store.load_domain("orders", scope="project")
    assert len(doc.sections) == 1 and doc.sections[0].git_sha == first
```

Plus an ingest-stamp test: reuse `tests/unit/test_semantic_ingest.py`'s fixture path pattern — ingest with `git_root=<repo>` → every `semantic_layer` section carries the sha; without → `None`.

- [ ] **Step 2: FAIL**, **Step 3: implement** (`gitmeta.py` ~15 lines; `apply_approved_sections`: resolve `sha = current_git_sha(git_root) if git_root else None` once, and where new sections append, `doc.sections.append(s.model_copy(update={"git_sha": sha}) if sha else s)`; `ingest_dbt_semantics`: same resolve-once, stamp the drafts dict's sections before the domain loop [stamp-then-audit order preserved: audit runs per-domain on the final doc]); thread `git_root` from `harvest_review.py::action_apply` and `main.py::_run_semantic_ingest` per the Interfaces note (env-or-cwd rule; override branch uses the override root). `screens/` exempt from strict.

- [ ] **Step 4: run stamped + all harvest/ingest suites** (`test_gitmeta_stamping.py test_maze_harvest.py test_semantic_ingest.py test_harvest_wiring.py tests/tui/test_harvest_review_screen.py tests/tui/test_main_screen_semantic.py`), gates, commit — `feat(maze): git_sha provenance on harvested + semantic Scent writes`.

---

### Task 3: `docs/team-scent.md` + decisions + finish

- [ ] **Step 1:** write `docs/team-scent.md` per spec D3: why commit `labrat_maze/` (team memory, PR review = third trust layer after audit + harvest gate); the workflow (harvest → review modal → apply → `git diff` shows the new section with `**Source:**`/`**Meta:**` provenance incl. `git_sha` → commit/PR); merge guidance (section-per-block; concurrent applies of the same learning dedup at read; true conflicts resolve like any doc); what NOT to commit (`~/.labrat/**` user layer — regenerable per machine); the status CLI (`python -m labrat.maze.print_status --profile <p> --db <path>`) with a sample output block generated by RUNNING it against a seeded fixture (note the command used).
- [ ] **Step 2:** decisions.md entry:

```markdown
## 2026-07-10 — team Scent v1 (moat extra 2.3, overnight run)

Project-layer Scent is now a first-class team artifact: maze/status.py + print_status CLI
(read-only inventory/tier/freshness/drift report), git_sha provenance stamped on harvested
and semantic-ingested sections (None-safe; relaxes T1b byte-determinism to repo-state-
conditioned, apply-dedup keeps original stamps), and docs/team-scent.md (commit labrat_maze/,
PR-review learnings — audit + harvest gate + git review = three trust layers). Scoping of all
six moat-extra candidates: docs/superpowers/overnight-2026-07-10-decisions.md D-09.
```

- [ ] **Step 3:** full gates; commit `docs: team-scent workflow guide + decisions entry`. Controller: whole-branch Fable review, CLI spot-check, merge.
