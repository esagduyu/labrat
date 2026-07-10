# M3 — Column-Level Lineage + Read-Only Analyst Mode — Design

**Date:** 2026-07-04
**Status:** ✅ SHIPPED — merged to master 2026-07-04 as M3 (merge `ad125e0`) *(was: Design — awaiting user review)*
**Branch:** `feat/column-lineage` (proposed)
**Source:** Milestone M3 of `docs/superpowers/plans/2026-07-03-competitive-build-milestones.md` (T1b, upgraded with the live-parse insight). Competitive basis: Altimate's #1 differentiator is live-parsed column-level lineage (closed Rust core) + engine-enforced read-only mode. This is **moat + parity, not a DAB-score lever** (DAB datasets are base tables with no views) — verification is unit tests + the exit checks, no re-ablation.

## Motivation

Two Altimate-parity gaps, both deepening grounding + trust:
1. **Column-level lineage** — "where does this column come from?" answered by **live-parsing SQL against the `Catalog` via `sqlglot.lineage`**, never a dbt manifest (manifests go stale; live parse is always current — the whole differentiation argument).
2. **Engine-enforced read-only "Analyst" mode** — "safe to point at prod" enforced in the dispatch layer, not the prompt, generalizing the shipped `run_sql` statement-stacking guard.

## Non-negotiables

- Lineage is **deterministic / no-LLM / no execution** (parse-only, like `check_sql`).
- Read-only enforcement lives **at the registry/dispatch layer**, never in the prompt.
- GT-firewall preserved: the Cartographer view-lineage builder reads view **SQL metadata** only (never data, never answer-key files); every frozen doc still passes `audit_scent_doc` (fail-loud).
- Additive + backward-compatible: `Table.view_definition` defaults `None`; adapters without view introspection surface no views; a DB with no views yields byte-identical deterministic Scent.

## Current-state anchors (code-verified 2026-07-04)

- `sqlglot>=25` is a dependency; used in `agent/tools/check_sql.py` (the sqlglot-against-`Catalog` pattern to mirror: `_catalog_index`, `exp` walking, `ParseError` handling), `agent/tools/run_sql.py`, `sql/validator.py`.
- `sqlglot.lineage.lineage(column: str, sql: str, schema: dict | Schema | None = None, sources=None, dialect=None, ...)` — traces **one** output column of `sql` back to source columns, returning a `Node` tree (`.name`, `.downstream`). Trace all output columns by iterating the query's projections.
- `db/catalog.py`: `Table{name, schema_name, columns, foreign_keys, row_count, comment}` (frozen) + `qualified_name`; `Column{name, data_type, nullable, ...}`; `Catalog{...schemas}`. **No view/definition field today.**
- `db/duckdb_engine.py::introspect_catalog` (~line 155-210) filters `WHERE table_type = 'BASE TABLE'` (line ~184) — **views are excluded.** `duckdb_views()` / `information_schema.views.view_definition` provide view SQL.
- `agent/tools/base.py`: `ToolContext` (line 12; fields `connections/catalogs/primary/profile_name`, **no `read_only`**); `Tool` ABC (line 65; `name/description/input_model/execute`, **no `is_mutating`**); `ToolRegistry.dispatch` (line 154) parses args then `await tool.execute(ctx, parsed)` — the central enforcement seam.
- `agent/data_tools.py::build_data_tools_registry` (line 29) registers the tools; structurally-mutating ones = `AttachDatabaseTool`, `LoadFileTool`, `LoadMongoCollectionTool`.
- `maze/document.py`: `Section{heading, body, source="human"}`; `_RECOGNIZED_SOURCES` = `verified | draft | human` (line ~56) — **add `lineage`.**
- `maze/cartographer.py::generate_scent` per-connection section list (the wiring point for the view-lineage builder, same place C2's `build_code_name_notes` was added).

## The four units

### Unit A — Read-only "Analyst" mode (feature 3.2)

Engine-enforced, dispatch-layer.

- `ToolContext`: add `read_only: bool = False` (keyword-only, default preserves all existing behavior).
- `Tool`: add `is_mutating(self, args: InputT) -> bool` — **default** returns a class-level `mutating: bool = False`. `AttachDatabaseTool`/`LoadFileTool`/`LoadMongoCollectionTool` set `mutating = True`. `RunSqlTool` **overrides** `is_mutating` to sqlglot-parse `args.sql` and return `True` iff any statement is a write (`INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE/REPLACE/MERGE/GRANT/…`) — a SELECT/WITH/EXPLAIN returns `False` (still runs). Parse failure under `read_only` → treat as mutating (fail-closed: block the unparseable rather than run it).
- `ToolRegistry.dispatch`: after `parsed = tool.input_model.model_validate(args)`, insert `if ctx.read_only and tool.is_mutating(parsed): return DispatchResult(ok=False, value=None, error="blocked: read-only Analyst mode")`. Central; no per-tool prompt text.
- Rationale: `run_sql` cannot be a static `mutating=True` (that would block legitimate SELECTs); the SQL-classification hook is what makes "read-only but still queryable" work.

### Unit B — Capture views in the Catalog

Prerequisite for Scent lineage.

- `db/catalog.py`: add `view_definition: str | None = None` to `Table` (None = base table; a view is a `Table` with columns + a definition). Backward-compatible.
- `db/duckdb_engine.py::introspect_catalog`: additionally enumerate views (`table_type = 'VIEW'`) and populate `view_definition` from `information_schema.views`/`duckdb_views()`; a view's columns come from the same `information_schema.columns` path already used for base tables. Base-table behavior unchanged.
- Scope: **DuckDB adapter only** this milestone (primary/most-used; DAB primary + TUI are DuckDB). Other adapters keep base-tables-only introspection (surface no views) — Postgres view capture is a noted fast-follow; `explain_lineage` (Unit C) and read-only (Unit A) already work on any adapter.

### Unit C — `explain_lineage` tool (feature 1.1)

- New `Tool` subclass in `agent/tools/`, registered in `build_data_tools_registry()`. Name `explain_lineage`.
- Input: `sql: str`, `database: str | None = None` (routes via `ctx.connections[database or ctx.primary]`), `column: str | None = None` (a specific output column; when omitted, trace every output column of the query).
- Build a sqlglot schema dict from the routed `Catalog` (reuse/adapt `check_sql`'s catalog indexing), call `sqlglot.lineage(col, sql, schema=...)` per target column, and flatten each `Node` tree into `sources: list[{table, column}]` + a short `derivation` string (the transform expression). Output model: `{columns: list[{output_column, sources, derivation}], parse_error: str | None}`.
- Deterministic, no execution, `mutating = False` (read-only-safe). Fail-soft: a `ParseError`/unresolved column returns a structured `parse_error`, never raises.

### Unit D — Cartographer view-lineage → Scent

- New `build_view_lineage(catalog, database, ...) -> Section | None` in `maze/cartographer.py`: for each `Table` with a `view_definition`, run `sqlglot.lineage` per view column against the base-table schema, emit a `lineage`-tagged bullet per resolved column (`view `V`.`x` ← `base`.`y``). Returns `None` when the DB has no views (→ byte-identity preserved).
- `maze/document.py`: add `"lineage"` to `_RECOGNIZED_SOURCES`.
- Wire into `generate_scent`'s per-connection section list (same spot as C2's `build_code_name_notes`); runs on the deterministic path; GT-firewalled (reads `view_definition` metadata, no data). The merged doc (now possibly carrying a `lineage` section) still goes through `audit_scent_doc` fail-loud.

## Data flow

- **Read-only:** agent/driver constructs `ToolContext(read_only=True)` → every `dispatch` checks `tool.is_mutating(parsed)` → mutating call returns an error DispatchResult; SELECT via `run_sql` passes.
- **Lineage tool:** agent calls `explain_lineage(sql, column?)` → sqlglot schema from Catalog → `sqlglot.lineage` → flattened sources.
- **Scent lineage:** `introspect_catalog` captures views → `generate_scent` → `build_view_lineage` traces each view → `lineage`-tagged Section → merge → `audit_scent_doc` → freeze.

## Testing (fixture-based, no LLM)

- **A:** `ToolContext(read_only=True)` → a `run_sql` with `INSERT ...` returns `ok=False, error="blocked: read-only Analyst mode"`; an `attach_database` call blocked the same way; a `run_sql` `SELECT ...` still returns `ok=True`. An unparseable SQL under read_only is blocked (fail-closed). Under `read_only=False` all pass (regression).
- **B:** introspect a DuckDB with a `CREATE VIEW v AS SELECT ...` → the `Catalog` `Table` for `v` has `view_definition` set + its columns; base tables have `view_definition is None`.
- **C:** `explain_lineage` on a 2-table join / a renamed+derived projection → correct `sources` (`table.column`) per output column; a specific `column=` narrows to that column; a syntactically bad SQL returns a `parse_error`, no raise.
- **D:** a DB with a view → `generate_scent` produces a `lineage`-tagged section naming `view.col ← base.col`; a DB with no views → byte-identical deterministic Scent (builder returns None); a `lineage`/leaky section still trips `audit_scent_doc` (fail-loud unchanged).
- **Regression:** full suite green; `ruff format`/`ruff check`/`pyright` clean; existing `check_sql`/`run_sql` tests unaffected by the new `is_mutating` hook (default False → no behavior change when `read_only=False`).

## Non-goals

- dbt-manifest lineage (deliberately rejected — staleness).
- Postgres/other-adapter view introspection (fast-follow; the tool + read-only already work adapter-agnostically).
- Cross-query / physical-table lineage without a query (lineage is query-scoped by nature; views are the standing-query case surfaced in Scent).
- Column-level lineage *persistence/product UI*, TUI wiring, metric ingestion (later milestones).
- Any DAB re-ablation (not a DAB lever).

## Decomposition into plan phases

- **Phase 1 — Unit A (read-only mode):** `ToolContext.read_only` + `Tool.is_mutating` + dispatch gate + tool overrides + `run_sql` SQL classification. Independent; highest "safe to point at prod" value.
- **Phase 2 — Unit B (view capture):** `Table.view_definition` + DuckDB introspection. Prerequisite for D.
- **Phase 3 — Unit C (`explain_lineage` tool):** independent of B (traces agent-provided SQL); can reuse `check_sql` cataloging.
- **Phase 4 — Unit D (Cartographer view-lineage → Scent):** depends on B; adds `lineage` source token; wires into `generate_scent`.
- **Phase 5 — regression + audit fail-loud over `lineage` sections + byte-identity (no-views) + gates.**
