# Scent Read-Model v2 + Provenance-Rich Footer — Design

**Date:** 2026-07-09 · **Status:** approved (brainstormed with Fable, user-approved)
**Follow-on of:** TUI-M3 final-review ticket I2 (harvest-apply/refresh shadowing) + TUI-integration spec §7 deltas (per-doc freshness, provenance tiers).
**Prerequisite for:** T1b dbt semantic-layer ingestion (writes sections into domains the Cartographer owns — collisions become the norm; this build makes them safe).

## 1. Problem

Two related defects in how Scent docs are read and written across store layers:

**P1 — whole-doc precedence + copy-on-apply (I2).** `MazeStore.docs()` resolves each domain with whole-doc precedence (project layer wins). `apply_approved_sections` loads the *merged* view and writes the whole doc to the *project* layer. On any domain collision between layers, apply copies user-layer (Cartographer) content into the project layer, where it permanently shadows future user-layer regenerations — M2's refresh becomes a silent no-op for that domain.

*Corrected severity (traced 2026-07-09):* today Cartographer domains are DB aliases (`main`) and harvest domains are table names / `general` — disjoint, so the shadow cannot fire in the current single-DB TUI. It becomes guaranteed once T1b writes `semantic_layer` sections into Cartographer-owned domains. This is a pre-T1b architectural fix, not an active-bug fix.

**P2 — footer fidelity (§7 deltas).** The M4 `⚑ grounded:` footer reports counts + a single global stale flag. Spec §7 wanted per-doc names, per-section freshness (Section meta vs current schema fingerprint), and provenance-tier labels via `maze/provenance.py::source_rank`/`best_source`. The tool output (`search_reference_docs`) carries none of that today, so the footer *cannot* be honest about tiers/freshness without a data path.

## 2. Decisions (user-approved)

- **D1 — Full read-model:** per-section merge-at-read in `MazeStore.docs()`; apply writes only its own sections to the project layer. (Alternatives rejected: minimal collision guard = double work at T1b; defer to T1b = hollow footer tiers.)
- **D2 — Data path = enriched tool output:** `search_reference_docs` output gains additive provenance/freshness fields; the footer stays a pure consumer of the `on_tool_call` stream (the architecture M4 proved). (Rejected: UI re-querying the store — duplicates retrieval logic, races the store.)

## 3. Design

### 3.1 Store: per-section merge-at-read

`MazeStore.docs(kind)` changes from per-domain whole-doc override to **per-domain section union**:

- For each domain, collect sections from all layers in order **user → project**.
- Dedup by `body.strip()` (the existing apply convention). Consequence: legacy project-layer copies of user content collapse into the union — **no on-disk migration needed**.
- Section order in the merged doc: user-layer sections first (structural Cartographer content), then project-layer additions, preserving in-layer order.
- Doc-level metadata: `kind` must match (existing filter); `tables` frontmatter unions (sorted, deduped); `scope` on a merged doc is `"merged"` when both layers contributed, else the contributing layer's scope.
- Single-layer domains produce **byte-equivalent** results to today (regression-pinned).

`load_domain(domain, kind="scent", scope: str | None = None)` gains an optional `scope` filter: `None` = merged view (today's callers), `"project"`/`"user"` = that layer's doc only (returns `None` if absent). Writers use the scoped form.

### 3.2 Apply: write only what you own

`apply_approved_sections(store, domain, approved)`:

- Loads `store.load_domain(domain, scope="project")` (or creates a fresh `ScentDoc(domain=domain)`).
- Appends approved sections with the same body-dedup.
- Audits the resulting **project-layer** doc (`audit_scent_doc`, fail-loud, unchanged semantics — everything written is still audited).
- Writes to the project layer.

User-layer content is never read into the write path, so refresh-shadowing is impossible **by construction**. The docstring note about copy-into-project is deleted; a test pins "apply never writes user-layer content".

### 3.3 Tool output enrichment (additive only)

`search_reference_docs` output model:

- Per section (`SectionMatch`): `source: str` (existing provenance token) and `fresh: bool | None` — `None` when the section has no `schema_hash` meta (unknown is never claimed fresh).
- Per doc (`DocResult`): `best_source: str` (via `maze/provenance.py::best_source` over its matched sections) and `stale: bool | None` (any section with `fresh is False` → `True`; all `None` → `None`).
- Freshness computed **inside the tool**: `fingerprint_from_catalog(ctx.catalogs[ctx.primary])` compared to each section's `schema_hash`. No catalog in ctx → all freshness fields `None`.
- Strictly additive fields: MCP/DAB consumers see extra JSON keys; retrieval ranking, section selection, and existing fields unchanged.

### 3.4 Footer (§7 fidelity)

`TurnProvenance` upgrades the scent segment from `scent ×2 (fresh)` to per-domain tier + freshness, e.g.:

```
⚑ grounded: scent: orders (verified·fresh) +1 · 2 queries · verifier ✓
```

- Tier label = the doc's `best_source`; freshness label from per-section data (`fresh`/`stale`; omitted when unknown).
- `+N` aggregates additional matched docs beyond the first (full list would flood the line; first-doc-plus-count keeps it one line).
- The M2 global `_scent_stale` provider remains **only** as the fallback when per-section data is absent.
- Degradation ladder unchanged in spirit: structured fields when present → repr-shape recognition (M4 fix) → opaque call count. Parse-tolerant, never raises; empty results are never grounding evidence (ebecc9c invariant).
- Joins/lineage/queries/verifier segments unchanged.

## 4. Non-negotiables

1. Every Scent write still passes `audit_scent_doc` (fail-loud) before hitting disk.
2. Apply can never write user-layer content to the project layer (test-pinned).
3. M2 refresh (user-scope rmtree) can never affect project-layer content, and post-refresh reads reflect regenerated user content immediately (no shadow) — test-pinned at the store seam.
4. Single-layer domains: merged read byte-equivalent to today's read (golden regression).
5. Tool output changes are additive; existing field names/shapes untouched; benchmark paths behavior-identical.
6. Footer honesty: unknown freshness is never rendered as fresh; empty retrievals contribute nothing.
7. Deterministic-only throughout; no LLM in any of these paths.
8. Pyright strict on `maze/`, `agent/tools/`, `widgets/`; `screens/` exempt. Repo gates before every commit.

## 5. Consumers checked

- `SearchReferenceDocsTool` — reads `docs()`; gains enrichment (3.3).
- `apply_approved_sections` — rewritten write path (3.2).
- `cartograph_prepass` / `generate_scent` — write user-layer docs directly (not via MazeStore); unaffected.
- `HarvestReviewScreen` — calls apply; behavior-compatible (counts unchanged).
- DAB claude-mcp / MCP server — read via the tool; additive JSON only.
- `first_connect` staleness sidecar — orthogonal (file-level, not store-level); unchanged.

## 6. Testing

TDD per task. Key suites: store merge semantics (union, dedup, ordering, scope filter, single-layer golden equivalence); apply isolation (never-copies-user, idempotent re-apply, audit ordering); tool enrichment (fresh/stale/None matrix, no-catalog degrade, additive-shape guard); footer (tier+freshness rendering, fallback ladder, ebecc9c invariant preserved); cross-seam regression reproducing the I2 scenario (colliding domain: harvest-apply then refresh-regenerate → merged read shows BOTH fresh Cartographer content AND harvested sections).
