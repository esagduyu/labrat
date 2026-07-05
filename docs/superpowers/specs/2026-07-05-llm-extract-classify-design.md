# llm_extract + llm_classify — Per-Row LLM Primitives (M4 sub-project 2.1) — Design

**Date:** 2026-07-05
**Status:** Design — awaiting user review before writing-plans
**Branch:** `feat/llm-extract`
**Source:** Milestone M4 feature 2.1 of `docs/superpowers/plans/2026-07-03-competitive-build-milestones.md`; competitive analysis `docs/competitive-analysis-2026-07-03.md` (PromptQL's per-row primitives = genuine white-space nobody has shipped; attacks bulk unstructured extraction). Builds on the shipped **Context Ledger** foundation (M4 dependency).

## Motivation

Per-row LLM primitives — `llm_extract(table, text_column, json_schema)` and `llm_classify(table, text_column, labels)` — fan out per-row mini-LLM-calls from a **deterministic loop**, binding results **outside model context** (into a queryable table + a ledger artifact). This is the one differentiated capability nobody on the DAB leaderboard has, and it targets bulk unstructured extraction. **Caveat:** the roadmap's "moves patents off 0%" is partly stale — patents was globally GT-broken at 0% until upstream PR #59 fixed it, so patents already contributes to our score; the feature stands on its general merit (per-row extraction), and the live DAB/patents validation is a deferred follow-on run, not part of this build.

**This is the codebase's first LLM-calling tool.** Every existing tool is deterministic; these call an LLM per row. That is an intentional, bounded departure (see Non-negotiables).

## Non-negotiables

- **Functional only where an `llm_fn` is injected (labrat-agent / AgentLoop path).** The tools are registered in the shared builder (so no conditional-registration complexity), but they **self-error with a structured `ok=False` result whenever `ctx.llm_fn is None`** — which is the case on the claude-mcp path (bypasses AgentLoop), the MCP server, the TUI, and any deterministic context. So they are effectively inert everywhere except the labrat-agent path that injects the provider. Not a claude-mcp leaderboard lever; no per-row LLM calls happen on the leaderboard path.
- **Deterministic contexts unaffected / byte-identity preserved:** `ToolContext.llm_fn` defaults `None`; when `None`, the tools return a structured "no LLM available" error rather than raising. Adding the optional field must not change any existing `ToolContext` construction or tool behavior.
- **Bounded fan-out:** a hard `max_rows` cap (default 200) — per-row calls multiply cost; the tool must never fan out unboundedly.
- **Failure-tolerant:** a per-row parse/LLM failure yields a null row + increments `rows_failed`; it never aborts the whole batch.
- **Results bound outside context (reuse the ledger):** the extracted table declares `ledger_payload() -> ("table", df)` so the model sees a bounded summary while the full extraction lives in the `ResultStore` + a queryable DuckDB temp table.

## Current-state anchors (code-verified 2026-07-05)

- `LLMFn = Callable[[str], Awaitable[str]]` (established alias; e.g. `agent/verifier.py:21`). `provider_llm_fn(provider, *, system="") -> LLMFn` (`agent/verifier.py:81`) adapts a `ModelProvider` into a one-shot `LLMFn` via a tool-less `provider.stream(...)`.
- `src/labrat/agent/runner.py`: `run_agent_task(prompt, ..., provider: ModelProvider, ...)` (line 36; `provider` at line 41) — the injection point for `ctx.llm_fn`. It also (post-ledger) constructs the `ToolContext`/ledger.
- `src/labrat/agent/tools/base.py`: `ToolContext.__init__` (line 24) — add `llm_fn: LLMFn | None = None` (keyword-only, default None). `Tool[InputT]` + `ToolRegistry`; `DispatchResult{ok, value, error}`.
- `src/labrat/db/duckdb_engine.py`: `materialize_table(table_name, arrow_table)` (line 81) — creates/replaces a TEMP table from an Arrow table (validates the identifier); `pl.DataFrame.to_arrow()` supplies the Arrow table. This is how the result table becomes a queryable temp table (same machinery `load_mongo_collection` uses).
- `src/labrat/agent/data_tools.py::build_data_tools_registry` — registers all data tools; llm_extract/classify are registered here too and self-error when `ctx.llm_fn is None` (so they're inert on non-injected paths — see Non-negotiables).
- `LedgerPayloadProvider` protocol + `ledger_payload()` (`agent/tools/serialization.py`) — the extract/classify `_Output` implements it (`("table", df)`), same idiom as the run_sql/sample_rows retrofits.

## The five units

### U1 — `ToolContext.llm_fn` + runner injection
- `ToolContext` gains `llm_fn: LLMFn | None = None` (keyword-only). Existing constructions unaffected.
- `run_agent_task` sets `ctx.llm_fn = provider_llm_fn(provider, system=<extraction system prompt>)` (its provider doubles as the per-row caller — same model/billing as the loop). Other `ToolContext` builders (TUI, MCP, DAB claude-mcp) leave it `None`, so the tools error cleanly there.

### U2 — Per-row engine (`src/labrat/agent/tools/llm_primitives.py`)
- `async def extract_rows(ctx, *, table, text_column, key_columns, spec, where, limit, max_rows) -> ExtractResult` where `spec` is either a JSON-schema dict (extract) or a label list (classify).
- Steps: validate `table`/`text_column`/`key_columns` as SQL identifiers (reuse the `_SAFE_IDENT`-style guard); `SELECT {keys, text_column} FROM {table} [WHERE ...] LIMIT {min(limit, max_rows)}` via `ctx.connection`; per row, build a prompt (the spec + the row's text) instructing a JSON-only response; `await ctx.llm_fn(prompt)`; parse JSON → the requested fields (or the single `category` constrained to `labels`); on any parse/validation failure → a row with null fields + `rows_failed += 1`; assemble a `pl.DataFrame` (key columns + extracted fields).
- Returns `ExtractResult{df, rows_processed, rows_failed}`. Pure orchestration over `ctx.llm_fn` — no provider construction inside the engine (testable with a stub `llm_fn`).

### U3 — `LlmExtractTool`
- Input `{table: str, text_column: str, json_schema: dict, key_columns: list[str] = [], where: str | None = None, limit: int | None = None, result_table: str | None = None}`.
- `execute`: `ctx.llm_fn is None` → `_Output(ok=False, error="llm_extract requires an LLM-enabled context")`. Else run `extract_rows`, `materialize_table(result_table_or_default, df.to_arrow())` on the primary DuckDB connection, return `_Output{ok, result_table, rows_processed, rows_failed}`.
- `_Output` implements `ledger_payload() -> ("table", <df>)` (df on a Pydantic `PrivateAttr`, same idiom as the run_sql retrofit) so the ledger bounds the model-visible summary.

### U4 — `LlmClassifyTool`
- Input `{table, text_column, labels: list[str], key_columns=[], where=None, limit=None, result_table=None}`. Same engine with `spec = labels`; the result table has a `category` column constrained to `labels` (a value not in `labels` counts as a failed row). Same `_Output`/`ledger_payload` shape.

### U5 — Registration + regression + gates
- Register both tools in `build_data_tools_registry()` (available on the labrat-agent path; they self-error when `ctx.llm_fn is None`, so no crash on non-LLM contexts). Full gate; decisions.md entry.

## Data flow
1. Agent (labrat-agent path) has a table with an unstructured text column (a base table, or a temp table a prior `run_sql`/`load_file` materialized).
2. Agent calls `llm_extract(table, text_column, json_schema, key_columns=[...])`.
3. Engine SELECTs the rows (capped at `max_rows`), fans out per-row `ctx.llm_fn` calls, parses each into structured fields, assembles a result DataFrame.
4. The result is materialized as a DuckDB temp table (joinable by later `run_sql`) AND declared to the ledger (`ledger_payload → ("table", df)`), so the model sees a bounded `{summary, preview, artifact_ref}` and can query the new table by name.

## Testing (fixture-based; `llm_fn` STUBBED — no live LLM)
- **Engine:** a fake `llm_fn` returning canned JSON per row → `extract_rows` assembles the correct DataFrame (keys + fields), `rows_processed`/`rows_failed` correct; a row whose stub returns malformed JSON → null row + `rows_failed` incremented, batch continues; `max_rows` caps the SELECT.
- **`LlmExtractTool`:** end-to-end with a stub `ctx.llm_fn` → result table materialized (queryable via a follow-up `run_sql`/`describe_table`), `_Output.ledger_payload()` returns `("table", df)`, `isinstance(out, LedgerPayloadProvider)`; `ctx.llm_fn is None` → structured error (no raise).
- **`LlmClassifyTool`:** labels constrain the `category`; an out-of-label stub response counts as failed.
- **Ledger composition:** an over-budget extract result run through the ledger is bounded in history while the full table is retrievable + queryable.
- **Regression:** full suite green; existing `ToolContext` constructions unaffected by the new optional field; deterministic tools unchanged.

## Non-goals
- Live DAB/patents validation run (deferred follow-on; needs the labrat-agent DAB driver to inject `ctx.llm_fn`).
- A cheaper/separate per-row model (reuse the loop's provider; a bulk cheap-model option is a later optimization).
- claude-mcp exposure (bypasses AgentLoop).
- Program mode / handle-based results (M4 sub-project 2.2, next).
- Streaming/parallel per-row calls (sequential loop first; concurrency is a later optimization behind the same engine interface).

## Decomposition into plan phases
- **Phase A — U1:** `ToolContext.llm_fn` + `run_agent_task` injection (byte-identity when absent).
- **Phase B — U2:** the per-row engine (SELECT + fan-out + parse + assemble + max_rows + failure tolerance), stub-tested.
- **Phase C — U3:** `LlmExtractTool` + materialize + `ledger_payload` + registration.
- **Phase D — U4:** `LlmClassifyTool` (label-constrained) + registration.
- **Phase E — regression + ledger-composition test + gates + decisions.md.**
