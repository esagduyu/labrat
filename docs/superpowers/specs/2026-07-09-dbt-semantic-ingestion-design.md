# T1b — dbt Semantic-Layer Ingestion + Freshness Activation — Design

**Date:** 2026-07-09 · **Status:** approved (brainstormed with Fable, user-approved)
**Closes:** FEATURE_ROADMAP T1b's open remainder (lineage half shipped M3 `ad125e0`); activates the per-section freshness shipped dormant in read-model v2 (`03205ca`).
**Prerequisite (satisfied):** Scent read-model v2 — semantic sections write into Cartographer-owned domains; merge-at-read makes those collisions safe.

## 1. Problem

The trust ladder's top tier (`semantic_layer`) has no writer: dbt semantic models and metrics — the human-governed definitions the north-star calls "the mandatory first step" — are invisible to the agent. Separately, no Scent writer stamps `schema_hash`, so the read-model-v2 freshness machinery renders `None` everywhere.

Corrections to stale references: dbt ≥1.6 compiles semantic models/metrics into **`manifest.json` top-level `semantic_models` and `metrics` keys** (FEATURE_ROADMAP's `dbt_project.yml` pointer is wrong). The existing `DbtLoader` parses only model `nodes`; the whole `catalog/` package has zero production consumers; onboarding already collects a dbt path (`OnboardingResult.catalog_path`) but dead-ends it.

## 2. Decisions (user-approved)

- **D1 — Trigger:** first-connect ingestion (when the profile has a dbt path) + drift-detect-and-offer re-ingest, mirroring M2's Cartographer pattern. Auto-ingest is faithful because the dbt repo is already human-governed at source. (Rejected: explicit-action-only — top tier stays empty; CLI-first — leaves the onboarding dead-end.)
- **D2 — Config:** new `Profile.dbt_project_path: str | None` (legacy-safe default `None`); onboarding persists its collected path into it; SettingsScreen gains the field. (Rejected: cwd auto-detect — wrong for out-of-tree dbt repos.)
- **D3 — Stamping scope:** BOTH the new ingestion and the Cartographer stamp `schema_hash` (deterministic catalog fingerprint; `generated_at` deliberately NOT stamped — would break the no-clock invariant). Cartographer golden/byte tests updated once.
- **D4 — Audit posture (designer-decided):** all ingested sections pass `audit_scent_doc` fail-loud before write. dbt YAML is external text, not clean-by-construction — the Cartographer's audit exemption does not extend to it.
- **D5 — Replace, not append:** re-ingest REPLACES a doc's existing `semantic_layer` sections (definitions derive from a source of truth; stale versions must not accumulate). All other sections (harvested/human/verified) preserved.

## 3. Design

### 3.1 Parser — `catalog/dbt/semantic.py` (pure)

Typed models + one function:

- `EntityDef(name, type)`, `DimensionDef(name, type, description)`, `MeasureDef(name, agg, description, expr)`.
- `SemanticModelDef(name, table, description, entities, dimensions, measures)` — `table` from `node_relation.alias` (fallback: relation-name tail, then semantic-model name).
- `MetricDef(name, type, label, description, measure_refs)` — `type` normalized to `{"simple","ratio","derived","other"}`; `measure_refs` = referenced measure names from `type_params` (simple: `measure.name`; ratio: numerator+denominator; derived: `metrics` inputs listed by name).
- `SemanticArtifacts(models, metrics, warnings)`.
- `parse_semantic_manifest(manifest: dict) -> SemanticArtifacts` — reads top-level `semantic_models`/`metrics`; missing keys → empty artifacts; malformed entries skipped, each appending one human-readable string to `warnings`; NEVER raises on shape.

### 3.2 Section builder — `maze/semantic_ingest.py::build_semantic_sections` (pure)

`build_semantic_sections(artifacts: SemanticArtifacts, schema_hash: str | None) -> dict[str, list[Section]]`:

- Per semantic model: one section `heading="Semantic Model: <name>"` on domain `<table>`; body = description line + entity/dimension/measure bullets (name, type/agg, description; measure `expr` included when present).
- Per metric: section `heading="Metric: <label-or-name>"`; **simple** metrics route to the domain of the semantic model owning their referenced measure; **ratio/derived/other** (or unresolvable measure) route to domain `"metrics"`; body = description + type + referenced measures (annotated with the owning model's TABLE when resolvable — the table is the grounding token domains key on; amended 2026-07-09 during build, T2 review).
- Every section: `source="semantic_layer"`, `schema_hash=schema_hash` (may be `None` when no catalog — honest unknown).
- Deterministic ordering: models and metrics sorted by name; bullet order follows manifest order within a def.

### 3.3 Ingestion controller — `maze/semantic_ingest.py::ingest_dbt_semantics`

`ingest_dbt_semantics(*, manifest_path: Path, catalog: Catalog | None, store: MazeStore, project_scent_dir: Path, force: bool = False) -> IngestOutcome` where `IngestOutcome(domains: tuple[str, ...], sections_written: int, warnings: tuple[str, ...], skipped: bool, drifted: bool)` (signature as built; synced 2026-07-09, T3 review):

1. Missing/unreadable/invalid-JSON manifest → `skipped=True` with one warning (fail-open at the controller level; the TUI renders it as a toast).
2. Parse → build sections with `schema_hash = fingerprint_from_catalog(catalog)` when a catalog is given, else `None`.
3. No semantic artifacts → `skipped=True`, no writes, quiet.
4. Per domain: load `store.load_domain(domain, scope="project")` (or fresh doc), **drop existing `source=="semantic_layer"` sections**, append the new ones, `audit_scent_doc` → raise `ScentContaminationError` (fail-loud, nothing written for that domain or any later domain — per-doc audit contract, same as harvest apply), `store.write_doc` (project layer).
5. Write `.manifest_fingerprint` sidecar into the project scent dir: sha256 over the canonicalized (sorted-keys JSON) `semantic_models`+`metrics` subset — NOT the whole manifest (model-body churn must not signal semantic drift).

New staleness helpers (in `maze/semantic_ingest.py`, reusing the sidecar read/write idiom): `semantic_fingerprint(manifest: dict) -> str`, `read/write_manifest_fingerprint(scent_dir)` — separate filename `.manifest_fingerprint`, same conventions as `.schema_fingerprint`.

### 3.4 TUI wiring

- `Profile.dbt_project_path: str | None = None` (after the M1 agent fields; legacy profiles validate).
- Onboarding: when `catalog_type == "dbt"`, persist `catalog_path` into the new field on profile creation (closing the dead-end).
- SettingsScreen: one Input row (`#dbt-path-input`, empty → `None`), saved via the existing `ProfileManager.update` path.
- `MainScreen`: after the M2 scent worker completes, if `profile.dbt_project_path` is set, run `_run_semantic_ingest` (`@work(exclusive=True, group="semantic")`): resolve `<path>/target/manifest.json`; missing → one actionable warning toast ("run `dbt parse`"); drift check via the sidecar — first contact ingests immediately; drift → warning toast offering the confirm-gated `action_reingest_semantics` (binding `f9`; chord lessons from M2/M3 applied); unchanged → silent. Worker body fully inside try/except → warning toast (fail-open; the fail-loud audit error surfaces as its own explicit toast naming contamination). Quit never blocks.
- Constructor override `dbt_manifest_override: Path | None` for tests (mirrors `scent_dir`).

### 3.5 Cartographer stamping

`generate_scent`'s deterministic section builders stamp `schema_hash` = the per-domain catalog's `fingerprint_from_catalog` (threaded as one parameter; builders stay pure). `generated_at` stays unset (no clock). Byte-golden tests updated once; the unexecuted 2026-07-04 attached-db plan's byte-identity notes get a one-line refresh rider.

### 3.6 Footer

No code changes needed: `semantic_layer` already tops `SOURCE_TIERS`, so `best_source` labels appear as `scent: orders (semantic_layer·fresh)` automatically, and Cartographer stamping activates `·fresh`/`·stale` everywhere.

## 4. Non-negotiables

1. Every ingested section passes `audit_scent_doc` fail-loud BEFORE write; no catch-and-continue around the audit.
2. Re-ingest replaces only `semantic_layer` sections; harvested/human/verified sections in the same doc are preserved byte-for-byte (test-pinned).
3. Deterministic end to end: no LLM, no clock, in any path this spec touches; identical manifest+catalog → identical bytes.
4. Benchmark isolation: ingestion is reachable only via `Profile.dbt_project_path` (TUI) or an explicit controller call; nothing under `eval/` or the MCP server can trigger it (grep-pinned).
5. Fail-open UI: no ingestion failure may break chat or connect; fail-loud writes.
6. `Profile` change is legacy-safe (defaulted); `run_agent_task`/benchmark surfaces untouched.
7. Cartographer determinism invariant holds post-stamping (schema_hash is a pure function of the catalog).
8. Freshness honesty carried through: no catalog → `schema_hash=None` → footer renders no freshness word.
9. Pyright strict on `catalog/`, `maze/`; `screens/` exempt. Repo gates before every commit.

## 5. Consumers checked

- `search_reference_docs` — reads merged docs; semantic sections retrieved + tier/freshness surfaced with zero changes.
- Harvest apply — same project-layer docs; RMv2 isolation means mutual non-interference (replace touches only `semantic_layer` sections; apply appends only approved harvested ones).
- M2 refresh — user-scope rmtree; project-layer semantic docs untouched (structural, post-RMv2).
- DAB/MCP — no path sets `dbt_project_path`; Cartographer stamping changes doc bytes → DAB scent goldens updated in-branch (prepass output remains deterministic).
- `DbtLoader`/`CatalogManager` — untouched (parser is a sibling module, not a `CatalogAdapter`; nothing forces the unwired package into production here).

## 6. Testing

New fixtures: `tests/fixtures/sample_dbt_project/manifest_semantic.json` (2 semantic models on the ecommerce tables + 1 simple, 1 ratio metric; one deliberately malformed entry for the warnings path). Suites: parser tolerance matrix (missing keys, malformed entries, empty); section-builder routing (model→table domain, simple→owner domain, ratio/derived→metrics, unresolvable→metrics) + determinism (same input → identical bytes); controller (first-contact ingest, drift re-ingest replaces semantic sections only, harvested preserved, audit fail-loud writes nothing, missing manifest skips with warning, sidecar round-trip); Profile/onboarding/Settings wiring; MainScreen worker gating (no path → no worker; fail-open); Cartographer stamping (schema_hash present, generated_at absent, determinism, goldens updated); end-to-end footer: ingest → ask → `(semantic_layer·fresh)`, then fingerprint drift → `·stale` — the first live exercise of RMv2 freshness.
