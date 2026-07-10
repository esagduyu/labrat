# dbt-CI At-Source Pairing — Staleness Gate — Design

**Date:** 2026-07-10 · **Status:** approved (interactive brainstorm; all core forks user-ratified) · **Deliverable class:** SPEC-ONLY (design artifact; build deferred — a cheaper model can execute this plan later)
**Thesis:** close mechanism (a) of the north-star's two-part freshness thesis (§8a). LabRat already ships (b) correction-harvesting; this adds **at-source colocation + CI pairing** — a read-only check that fails a PR when a user's dbt models change but the paired Scent wasn't refreshed in the same commit. "When a user's dbt models change, the Scent for those tables should be flagged stale in the same PR." This is a **paid/team-scale** capability (commercial-model memo #24, Option B): the free wedge is git-versioned Team Scent; CI pairing is what makes it *operate* at team scale.

## 1. What already holds (verified via a subsystem map this session)

- **dbt→Scent exists end-to-end.** `catalog/dbt/loader.py::DbtLoader(project_path, catalog_json=None).load()` builds a `Catalog` from `manifest.json`/`schema.yml` (+ optional `catalog.json` enrichment), **offline, no warehouse**. `catalog/dbt/semantic.py::parse_semantic_manifest(manifest) -> SemanticArtifacts` parses the `semantic_models`/`metrics` subset. `maze/semantic_ingest.py::ingest_dbt_semantics(...)` writes `semantic_layer`-tagged Scent sections to the project layer.
- **Fingerprint machinery exists.** `maze/semantic_ingest.py::semantic_fingerprint(manifest)` — sha256 over ONLY `{semantic_models, metrics}` (model-body churn excluded). Stored in `.manifest_fingerprint` (in `project_scent_dir`), read via `read_manifest_fingerprint(scent_dir)`. `maze/staleness.py::fingerprint_from_catalog(catalog)` — sha256 over `{table: [cols]}`; `is_stale(section_hash, current_fp)`; each semantic `Section.schema_hash` carries the per-table hash at ingest time.
- **Read-only status report exists.** `maze/status.py::build_status(store, *, catalog=None, ...)` already computes per-domain fresh/stale/unknown by comparing `Section.schema_hash` to `fingerprint_from_catalog(live)`. The CI check is this logic re-sourced from the *committed dbt project* instead of a live warehouse, wrapped in an exit code.
- **Colocation is already shipped** (Team Scent 2.3): commit `labrat_maze/scent/*.md` beside the project; `~/.labrat/**` (user layer) stays uncommitted/regenerable. `docs/team-scent.md` is the workflow. **The one missing piece is the CI check** — no CI/hook machinery exists today (only `.github/workflows/ci.yml` tests LabRat itself; no pre-commit, no `.git/hooks`).
- `Profile.dbt_project_path: str | None` (`profile/model.py:43`) already exists (onboarding + settings), consumed by semantic ingest.

## 2. User-ratified decisions

- **D1 — Read-only staleness GATE**, not write-back. CI detects dbt-changed-without-Scent-refresh and reports (configurable warn-or-block). It NEVER writes to the user's repo or Scent. Auto-refresh write-back (regenerate + commit Scent into the PR) is explicitly out of scope — the highest-trust surface, deferred to a later increment with its own review/audit design.
- **D2 — CLI + optional scaffold.** A portable `labrat scent check` (exit 0/1 + report) is the core, usable in any CI; an optional `labrat scent init-ci` scaffolds a starter GitHub Actions workflow. LabRat owns the command; the user owns their CI config.
- **D3 — Fingerprint-consistency signal** (Approach B, per-domain). Recompute the semantic fingerprint + per-table schema hashes from the *committed* dbt project (offline) and compare to what the *committed* Scent records (`.manifest_fingerprint` + per-section `schema_hash`). Divergence = stale, regardless of how it happened; names exactly which domains are stale and why. The same-PR git-diff heuristic is rejected (gameable, needs PR context, false-positives on non-semantic edits).
- **D4 — Offline-first.** The check requires no live warehouse: `manifest.json` (from `dbt parse`) + optional `catalog.json` suffice. CI runs `dbt parse` then `labrat scent check`.

## 3. Components

### 3.1 `maze/ci.py` (new — pure read-only logic)
- `StaleDomain(BaseModel)`: `domain: str`, `reason: Literal["semantic_drift", "schema_drift"]`, `committed_hash: str | None`, `current_hash: str | None`.
- `CiCheckResult(BaseModel)`: `ok: bool`, `stale: list[StaleDomain]`, `checked: int`, `warnings: list[str]`, `fix_command: str`, `manifest_found: bool`.
- `check_scent_freshness(dbt_project_path: Path, scent_dir: Path) -> CiCheckResult` — the pure check:
  1. Resolve `manifest_path = dbt_project_path/"target"/"manifest.json"`; if absent → `CiCheckResult(ok=False, manifest_found=False, warnings=["manifest.json not found — run `dbt parse` first"], ...)` (caller decides exit per `--skip-if-no-manifest`).
  2. **Semantic drift:** recompute `semantic_fingerprint(manifest)`; compare to `read_manifest_fingerprint(scent_dir)`. Mismatch → the whole semantic subset is stale (the `.manifest_fingerprint` sidecar wasn't refreshed alongside the model change). Attribute to affected domains via `parse_semantic_manifest` → the tables whose semantic sections exist in the committed Scent.
  3. **Schema drift:** build an offline `Catalog` via `DbtLoader(dbt_project_path).load()`; recompute the whole-catalog `fingerprint_from_catalog(catalog)`. Note the existing machinery stamps every semantic `Section.schema_hash` with this **whole-catalog** fingerprint (not a per-table hash), so schema drift is a project-level signal: when the recomputed fingerprint differs from the (uniform) `schema_hash` on committed semantic sections, flag `schema_drift` on every domain that carries `semantic_layer` sections (its freshness stamp is invalid). This reuses the stamp exactly rather than forking a per-table hashing scheme; finer-grained per-table attribution is a future enhancement to the stamp itself, out of scope here.
  4. Return `CiCheckResult` with `ok = not stale`, `checked = <domains examined>`, `fix_command = "labrat scent ingest --dbt-project <path>"` (the headless re-ingest — see 3.3).
- **Reads only. No `write_doc`, no sidecar write, no repo mutation anywhere in this module.**

### 3.2 CLI: `labrat scent check` (`cli.py`)
- Flags: `--dbt-project PATH` (default: `Profile.dbt_project_path` of the active/`--profile` profile, else auto-detect nearest `dbt_project.yml`), `--scent-dir PATH` (default: `project_scent_dir()` = `./labrat_maze/scent`), `--warn-only`, `--skip-if-no-manifest`, `--format text|json`.
- Exit codes: `0` fresh (or `--warn-only`, or `--skip-if-no-manifest` with no manifest); `1` stale (or missing manifest without the skip flag). Text report: per stale domain, its reason + committed-vs-current hash + the one-line fix command. `--format json` emits `CiCheckResult.model_dump_json()` for machine parsing.

### 3.3 CLI: `labrat scent ingest` (headless re-ingest — the fix path)
- A non-TUI wrapper over `ingest_dbt_semantics(force=True)` so the fix the check names is runnable locally/in CI-fix jobs without opening the app. `--dbt-project`, `--profile`. (The TUI's F9 already does this interactively; this exposes it headlessly.) Writes go through the existing contamination audit + git_sha stamping — unchanged.

### 3.4 CLI: `labrat scent init-ci [--platform github]` (optional slice)
- Scaffolds `.github/workflows/labrat-scent.yml`: checkout → setup dbt → `dbt parse` → `pip install labrat` (or `uv`) → `labrat scent check`. Writes nothing if the file exists (no clobber); prints the path. GitHub only in v1; the CLI itself is platform-agnostic so other CIs wire it by hand.

### 3.5 Docs: `docs/dbt-ci-pairing.md`
- The workflow: colocate (commit `labrat_maze/` beside the dbt project — links to `team-scent.md`), wire the check (the two CI steps), what a stale-Scent PR failure looks like, and the fix (`labrat scent ingest`, review the diff, commit). Positions it as the paid/team-scale complement to free Team Scent.

## 4. Non-negotiables

1. **Read-only:** `maze/ci.py` and `labrat scent check` never write to the repo, the Scent, or any sidecar. The only write path is the *separate* explicit `labrat scent ingest`, which the user runs deliberately.
2. **Offline:** no live warehouse required; `manifest.json` (+ optional `catalog.json`) is sufficient. The check must not open a DB connection.
3. **Honest-unknown:** missing manifest / unparseable project / no committed Scent each produce a clear, distinct outcome — never a false pass or false fail. Missing manifest defaults to exit 1 (can't verify) unless `--skip-if-no-manifest`.
4. **Reuse, don't fork:** `DbtLoader`, `semantic_fingerprint`, `parse_semantic_manifest`, `fingerprint_from_catalog`, `read_manifest_fingerprint`, `Section.schema_hash`, and `build_status`'s freshness comparison are consumed as-is; no parallel hashing/parsing copies.
5. **Benchmark isolation:** nothing under `src/labrat/eval/` or `src/labrat/mcp/` imports `maze/ci.py`.
6. Pyright strict for `maze/` and `cli.py`; repo gates per commit; `test_app_renders` env flake non-signal.

## 5. Testing

- Fixture: a minimal dbt project (`dbt_project.yml`, one model with a `semantic_models` entry, a `target/manifest.json`) + a committed `labrat_maze/scent/` ingested from it.
- Fresh state → `check_scent_freshness` returns `ok=True`, CLI exit 0.
- Mutate a `semantic_models` measure without re-ingesting → the owning domain flagged `semantic_drift`, exit 1, fix command shown.
- Add a column to a model (schema change) without re-ingest → `schema_drift` flagged on every domain carrying semantic sections (whole-catalog stamp invalidated), exit 1.
- `--warn-only` → exit 0 with the annotation still printed.
- Missing `target/manifest.json` → exit 1 + "run `dbt parse`" guidance; `--skip-if-no-manifest` → exit 0.
- No committed Scent → `ok=True` with a "no Scent to check" note.
- `--format json` round-trips `CiCheckResult`.
- `labrat scent ingest` headless writes the same bytes as the TUI F9 path (audit + git_sha unchanged).
- Read-only invariant: after any `check` run, the repo working tree is byte-unchanged (assert no file writes).

## 6. Out of scope (this increment)

- **Auto-refresh write-back** (CI regenerates + commits Scent into the PR) — the highest-trust surface; deferred with its own review/audit/non-clobber design.
- Non-GitHub CI scaffolds (the CLI is platform-agnostic; only the `init-ci` convenience is GitHub-only in v1).
- Live-warehouse schema drift beyond what the dbt project encodes (the check is dbt-project-vs-Scent consistency, not warehouse-vs-Scent — that's what the TUI's live `build_status` already covers).
- PII/secret scanning of dbt content (the check reads but emits nothing; the write path's existing contamination audit + PII-at-load handling are unchanged and not extended here).
- Same-PR git-diff heuristics (D3 rejected).
