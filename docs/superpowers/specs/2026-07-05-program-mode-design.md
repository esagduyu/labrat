# Program Mode — `run_program` (M4 sub-project 2.2) — Design

**Date:** 2026-07-05
**Status:** Design — awaiting user review before writing-plans
**Branch:** `feat/program-mode`
**Source:** Milestone M4 feature 2.2 of `docs/superpowers/plans/2026-07-03-competitive-build-milestones.md` (PromptQL + MinusX + Pi, convergent). Builds on the shipped **Context Ledger** + **llm_extract** (M4 2.1). Extends the ledger from *bounding* re-entry to *preventing* it.

## Motivation

Today the agent calls tools one at a time across many round-trips — each result flows into model context (the ledger bounds it, but each call is still a separate turn). **Program mode** lets the model emit **one script that composes existing tools**, executed deterministically, with intermediate results bound to **handles** (DuckDB temp tables + ledger artifacts) instead of round-tripping. Only a bounded execution summary returns to context. This is the differentiated "plan-then-execute" ground PromptQL/MinusX/Pi converge on.

**Restricted tool-pipeline DSL (locked in brainstorming):** the "script" is an ordered JSON pipeline of registered-tool steps with handle binding — **not** arbitrary code. Safe by construction (only registered tools, no `eval`), matching the roadmap's "sandbox by construction (reuse the claude-mcp gate)."

## Non-negotiables

- **Safe by construction:** the interpreter ONLY dispatches registered tools via the existing `ToolRegistry`, with the SAME `ToolContext` — so a program inherits every existing gate (M3 read-only `is_mutating`, taint/contamination, per-tool caps like `llm_extract`'s `max_rows`) and can do nothing a tool can't. No `eval`, no arbitrary code, no new sandbox.
- **Bounded:** a hard **max-steps cap** (default 20) per program; each step keeps its own caps. **Stop-on-error** — a failed step halts and returns the partial summary + the failing step index.
- **Context prevention:** only a bounded `_Output` summary returns to model history (per-step status + handles + small previews) — intermediate table payloads live in temp tables / the `ResultStore`, never round-tripped. `mutating=True` (creates temp tables → composes with the M3 read-only gate).
- **Additive / backward-compatible:** a new tool + new modules; no change to the loop, existing tools, or any existing path. Not a claude-mcp leaderboard lever (composes tools on the AgentLoop path).

## Current-state anchors (code-verified 2026-07-05)

- `src/labrat/agent/tools/base.py`: `ToolRegistry` (line 147); `register` (161); `async def dispatch(self, name, args: dict, ctx) -> DispatchResult` (line 178) — the interpreter dispatches each step through this. `DispatchResult{ok, value, error}`. `ToolContext` (carries connections/catalogs/primary/read_only/llm_fn). `Tool.is_mutating(args)` (the read-only gate lives in `dispatch`).
- `src/labrat/agent/tools/serialization.py`: `LedgerPayloadProvider` protocol (line 24) + `ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None` (line 34) — the interpreter detects a step's table result via `isinstance(out, LedgerPayloadProvider)` + `ledger_payload() == ("table", df)`, then materializes it as the handle's temp table.
- `src/labrat/db/duckdb_engine.py`: `materialize_table(table_name, arrow_table)` (validates identifier, CREATE OR REPLACE TEMP TABLE) — used to bind a table handle; `pl.DataFrame.to_arrow()`.
- `src/labrat/agent/data_tools.py::build_data_tools_registry` — registers `RunProgramTool`. **Note:** the program interpreter needs the registry to dispatch into; the tool receives it (constructed with the registry, or builds its own via `build_data_tools_registry()` minus itself to avoid recursion — resolve in the plan; prefer passing the same registry, excluding `run_program` so a program can't nest-recurse in this slice).

## The DSL

```json
{"steps": [
  {"tool": "run_sql",     "args": {"sql": "SELECT id, abstract FROM patents"},                              "bind": "docs"},
  {"tool": "llm_extract", "args": {"table": "$docs", "text_column": "abstract", "json_schema": {"drug": "string"}}, "bind": "facts"},
  {"tool": "run_sql",     "args": {"sql": "SELECT d.id, f.drug FROM $facts f JOIN $docs d USING (id)"},      "bind": "final"}
]}
```

- **`ProgramStep`** = `{tool: str, args: dict, bind: str}` (`bind` = a `_SAFE_IDENT` handle name, unique within the program).
- **Handle references** in `args` (strings, recursively):
  - `$handle` → the handle's **materialized temp-table name** (`program_<handle>`), for steps whose output was a table. Interpolated into SQL / a tool's `table` arg.
  - `$handle.field` → a scalar field of that step's `_Output` (via `model_dump()[field]`), for passing a value forward.
  - A `$ref` to an unknown handle, or `.field` to a missing field → a structured program error (stop-on-error), not a crash.
- Steps run **sequentially**; a `DispatchResult.ok == False` (or a raised interpreter error) halts the program.

## Components (five units)

### U1 — DSL models + handle resolution (`src/labrat/agent/program/dsl.py`)
- `ProgramStep`/`Program` Pydantic models. `resolve_refs(args: dict, handles: dict[str, ResolvedHandle]) -> dict` — recursively substitutes `$handle` / `$handle.field` in the args tree; unknown handle/field → raise a typed `ProgramError`. `bind` names validated `_SAFE_IDENT`; duplicate bind → `ProgramError`. Pure/deterministic, unit-testable with no DB.

### U2 — Interpreter (`src/labrat/agent/program/interpreter.py`)
- `async def run_program(program: Program, ctx, registry, *, max_steps=20) -> ProgramResult`. For each step (≤ max_steps): `resolve_refs(step.args, handles)` → `registry.dispatch(step.tool, resolved_args, ctx)` → if `not ok`, stop + record the failing step; else record the step; if the output is a `LedgerPayloadProvider` returning `("table", df)`, `materialize_table("program_"+bind, df.to_arrow())` on the primary DuckDB connection and register the handle's temp-table name; store the `_Output` for `$handle.field` lookups. Returns `ProgramResult{ok, steps: [StepSummary], final_bind, error}` — each `StepSummary` is bounded (tool, ok, bind, rows/rows_failed, handle_table, small error). Never dumps full intermediate data.
- The interpreter reuses the same `ctx` (so read-only/taint/`llm_fn` all apply per step) and the same registry (excluding `run_program` — no nested programs in this slice).

### U3 — `RunProgramTool` (`src/labrat/agent/tools/run_program.py`)
- Input `Program` (`{steps}`); output `_Output{ok, steps, final_handle, final_table, error}` (bounded). `execute`: builds the interpreter's registry (the standard data-tools registry minus `run_program`), calls `run_program(...)`, returns the bounded result. `mutating = True`. A program producing a final table exposes `final_table` (the `program_<final_bind>` temp table) the model can `run_sql` against next.
- Registered in `build_data_tools_registry()`.

### U4 — Safety wiring
- `max_steps` cap enforced in the interpreter (a program with > max_steps steps → structured error before running any). Per-step gates are automatic (dispatch applies `is_mutating`/read-only; each tool applies its own caps). Contamination: a program can't reach answer-key files any more than its constituent tools can. Document that `run_program` is `mutating=True` (blocked under read-only Analyst mode by the M3 gate) and excludes itself from the sub-registry (no recursion).

### U5 — Registration + regression + composition test + gates
- Register; full gate; decisions.md/CLAUDE.md entries.

## Data flow (the M4 payoff)
- **Normal mode:** model → `run_sql` (result → history) → `llm_extract` (→ history) → `run_sql` join (→ history): 3 round-trips, 3 payloads in context.
- **Program mode:** model → `run_program({3 steps})` (ONE call). Interpreter runs all three; `docs`/`facts` become temp tables; only a bounded 3-line summary + `final_handle` return. The model then `run_sql`s the final handle if it wants the answer. Intermediate round-trips *prevented*, not just bounded.

## Testing (fixture-based; stub `llm_fn` for any llm_extract step)
- **U1:** `resolve_refs` substitutes `$docs` → `program_docs`, `$facts.rows_failed` → the value; unknown handle → `ProgramError`; duplicate `bind` → error. No DB.
- **U2:** a 3-step program over a fixture DuckDB (run_sql → llm_extract[stub] → run_sql-join) → all steps ok, `docs`/`facts` temp tables exist + the join reads them, `ProgramResult` is bounded (no full data); a failing middle step → stop-on-error, partial summary, later steps not run; `> max_steps` → structured error, nothing run; a step whose tool self-errors (e.g. llm_extract with `ctx.llm_fn=None`) → program stops with that step's error.
- **U3:** `RunProgramTool` end-to-end via the registry → bounded `_Output`, `final_table` queryable by a follow-up `run_sql`; `run_program` is NOT in the sub-registry (no recursion — a step `{tool: "run_program"}` → unknown-tool error).
- **Ledger/read-only composition:** the tool's `_Output` is bounded (no full intermediate data in `model_dump`); `mutating=True` → blocked under a `read_only=True` ctx at dispatch.
- **Regression:** full suite green; additive; existing paths unchanged.

## Non-goals
- Arbitrary control flow (loops/conditionals) / model-authored Python (a bounded map-construct or a real sandbox is a later extension behind the same tool).
- Nested programs (a program calling `run_program`) — excluded this slice (recursion guard).
- Parallel step execution (sequential first).
- Live DAB validation run (deferred; program mode composes on the labrat-agent path).
- claude-mcp exposure of the *benefit* (the tool registers, but the round-trip-prevention win is an AgentLoop/product property).

## Decomposition into plan phases
- **Phase A — U1:** DSL models + `resolve_refs` (pure, fixture-free).
- **Phase B — U2:** the interpreter (sequential dispatch + materialize-by-handle + bounded summaries + stop-on-error + max-steps).
- **Phase C — U3:** `RunProgramTool` + sub-registry-minus-self + registration.
- **Phase D — U4/U5:** safety wiring (recursion guard test, read-only compose) + regression + the end-to-end composition test + decisions.md/CLAUDE.md + gates.
