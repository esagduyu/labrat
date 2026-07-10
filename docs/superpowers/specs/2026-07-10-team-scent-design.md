# Moat Extra 2.3 — Git-Versioned Team Scent (v1) — Design

**Date:** 2026-07-10 · **Status:** approved (overnight autonomous run; scoping in decision log D-09; user reviews in the morning)
**Thesis:** the project-layer Scent (`./labrat_maze/scent/*.md`) already lives in the analyst's repo as deterministic markdown — v1 makes it a first-class, reviewable, team-shared artifact with provenance and a status surface. No new storage, no LLM, no clock.

## 1. What already holds (verified this session)

Deterministic rendering; idempotent apply/ingest (re-runs byte-stable); RMv2 merge-at-read dedups by body (git-merge dupes self-heal at read); `Section.git_sha` meta field exists, rendered/parsed, never written; per-doc audit fail-loud on every write path.

## 2. Decisions

- **D1 — Status surface:** new `src/labrat/maze/status.py` — pure report over a `MazeStore` + optional live `Catalog`: per domain → scope(s) present, section count by `source` tier, `best_source`, freshness (sections' `schema_hash` vs `fingerprint_from_catalog` when a catalog is given; unknown otherwise), and sidecar drift states (`.schema_fingerprint`, `.manifest_fingerprint`). `build_status(store, catalog=None, project_scent_dir=None) -> MazeStatus` (typed rows) + `render_status(status) -> str` (aligned plain-text table). Module CLI `python -m labrat.maze.print_status [--profile P] [--db /path.duckdb]` — read-only, exit 0/2.
- **D2 — Provenance stamping:** `apply_approved_sections` and `ingest_dbt_semantics` stamp `git_sha` on the sections they write when the project root is a git repo (`git rev-parse --short HEAD`, one subprocess, cached per call; failure/no-repo → `None`, honest-unknown — mirrors the freshness posture). NOT stamped by the Cartographer (user-layer, regenerable, and it would break its determinism-across-checkouts property). Deterministic given repo state — this explicitly RELAXES T1b's "identical manifest+catalog → identical bytes" to "...+repo-state": a force re-ingest after a new commit updates the Meta git_sha (honest provenance, visible in git diff as Meta-line churn). Body-dedup means re-APPLY of identical harvested sections never re-stamps (existing section kept), so apply idempotence stays byte-stable within any repo state.
- **D3 — Workflow doc:** `docs/team-scent.md` — commit `labrat_maze/` to the project repo; PR-review harvested/semantic sections like code (the audit + human harvest gate + git review = three-layer trust); merge-conflict guidance (section-per-block, dedup-at-read absorbs double-applies); what NOT to commit (`~/.labrat` user layer — machine-local, regenerable).
- **D4 — Out of scope (v1):** team conflict tooling beyond git's own; CRDT-ish merges; remote sync; any write path changes beyond the two stamps.

## 3. Non-negotiables

1. Status surface is READ-ONLY — no store writes, no side effects beyond stdout.
2. Stamping is additive + None-safe: no git repo / git absent / any subprocess failure → `git_sha=None`, write proceeds identically (byte-identical to today when None — the Meta renderer already omits None fields).
3. Existing tests pass unmodified except explicit-stamp pins added; apply/ingest idempotence and audit ordering untouched (stamp happens at Section construction/append time, BEFORE audit — audited-bytes == written-bytes).
4. No LLM, no clock. `git_sha` is repo-state, not time.
5. Pyright strict (`maze/`); repo gates per commit; known env flake `test_app_renders` non-signal.

## 4. Testing

Status: fixture store with mixed-tier/mixed-freshness domains → typed rows + rendered table pinned (substring, not byte-golden); no-catalog → freshness unknown; CLI smoke via `python -m` (exit 0, table on stdout; bad args exit 2). Stamping: temp git repo → sections carry the short sha; non-repo tmpdir → `None` + write succeeds; audit still sees final bytes (stamp-before-audit pinned); idempotence retained (re-apply/re-ingest byte-stable — same sha same bytes). Docs reviewed by the whole-branch reviewer for claim accuracy.
