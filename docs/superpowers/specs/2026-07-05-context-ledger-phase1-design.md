# Context Ledger — Phase 1 Foundation (M4 dependency, T1d) — Design

**Date:** 2026-07-05
**Status:** Design — awaiting user review before writing-plans
**Branch:** `feat/context-ledger-phase1`
**Source:** Phase 1 of the T1d spec `docs/superpowers/specs/2026-06-26-context-ledger-subagent-dispatch-design.md` (on branch `feat/context-ledger`), adopted + refreshed against the post-M0–M3 codebase. **Scoped to Phase 1 (the Context Ledger foundation); Phase 2 (`dispatch_subagent`) is deferred** — not needed for M4's `llm_extract`/program-mode, kept separate to keep this build focused. This is the foundation the M4 milestone (per-row `llm_extract` primitives + program mode) builds on: "results bound outside model context" = this ledger.

## 1. Problem & goal

`AgentLoop` stringifies the **full** tool output into model history every turn — `output_str = str(dispatch.value)` at `src/labrat/agent/loop.py:164` (seam verified current 2026-07-05). `run_sql` can dump up to 1000 rows; `profile_dataset` emits large schema/sample blobs. The loop resends the system prompt, all tool schemas, and the whole growing history (including oversized tool outputs) every turn — the real context-burn cause measured on the Codex/GPT-5.5 runs.

**Goal:** a LabRat-owned **Context Ledger** that controls *what enters model history* — bounded mechanical summaries + addressable artifacts instead of raw payloads. A product / API-mode / `labrat-agent` win (cost, scale, provenance) and the M4 foundation. **NOT a claude-mcp leaderboard lever** (that path bypasses `AgentLoop`).

## 2. Design decisions (locked)

1. **Scope: Phase 1 only** — the Context Ledger foundation. Phase 2 (`dispatch_subagent`) deferred to a separate follow-on.
2. **Artifact model: persistent addressable store.** Over-budget tool outputs are written to a `ResultStore` and replaced in history by `{summary, preview, artifact_ref, full_row_count, truncated}`. Enables provenance (the "Cheese" artifact) and by-reference passing (which M4's program mode will reuse as handle-tables).
3. **Ledger is opt-in on bare `AgentLoop`** — absent → **byte-identical to today** (non-negotiable safety; protects existing paths + tests). Product paths (`run_agent_task`) default the ledger **ON** with an `enable_ledger=False` toggle.
4. **Mechanical summaries, no LLM** — summaries are row counts / column names / truncation notes, not model-generated. Cheap, reproducible, benchmark-safe.

## 3. Architecture

```
tool DispatchResult ─▶ ContextLedger.record() ─▶ ModelVisibleToolResult ─▶ AgentLoop.history
                            │ (payload over row/byte budget)
                            ▼
                        ResultStore  (tables→Parquet+JSON meta, profiles→JSON, traces→JSONL; addressable by artifact_ref)
```

When no ledger is attached, `AgentLoop` behaves exactly as today (full `str(dispatch.value)` into history).

## 4. Current-state anchors (code-verified 2026-07-05)

- `src/labrat/agent/loop.py`: `AgentLoop.__init__` (line 57; add `ledger` param), `on_tool_call` param (line 96), the tool-result seam `output_str = str(dispatch.value) if dispatch.ok else f"Error: {dispatch.error}"` (**line 164**), which is placed into the `ToolResultBlock` `content` (line 169) and passed to `on_tool_call` (line 173).
- `src/labrat/results/` and `src/labrat/runtime/` **do not exist** — clean slate for the new modules.
- `src/labrat/agent/tools/base.py`: `DispatchResult{ok, value, error}` (the ledger records these).
- High-volume tools: `run_sql.py`, `profile_dataset.py`, `sample_rows.py`, `column_stats.py` — return Pydantic `_Output`/structured payloads today; retrofit is additive (declare the large payload so the ledger can store it; off-ledger path returns today's string).
- `src/labrat/agent/runner.py`: `run_agent_task(...)` — gains `enable_ledger: bool = True`.
- Polars is the DataFrame type across the DB layer (Parquet round-trip via Polars).

## 5. Components & changes

### 5.1 `ResultStore` (`src/labrat/results/store.py`) — NEW
- Per-session directory (caller-provided root; the run/scratch dir for benchmarks, a temp dir for the TUI).
- Stores: tabular → **Parquet** + a small JSON metadata sidecar (columns, dtypes, row count); profile snapshots → **JSON**; trace payloads → **JSONL**.
- Produces an opaque `artifact_ref` (e.g. `result://<session>/<n>`) that resolves back to the stored file.
- Interface: `put_table(df, *, meta) -> ArtifactRef`, `put_json(obj, kind) -> ArtifactRef`, `get(ref) -> stored payload/path`, `preview(ref, *, max_rows, max_bytes) -> str` (capped by **both** rows and bytes).

### 5.2 `ModelVisibleToolResult` + serialization (`src/labrat/agent/tools/serialization.py`) — NEW
- Pydantic: `{summary: str, preview: str, artifact_ref: str | None, full_row_count: int | None, truncated: bool}`.
- A `render(mvtr) -> str` producing the compact string that enters history, and a small contract by which a tool declares its large payload for the ledger to store.

### 5.3 `ContextLedger` (`src/labrat/runtime/context_ledger.py`) — NEW
- `record(tool_name, dispatch_result) -> ModelVisibleToolResult`: under budget → pass through (preview == full payload, no artifact); over budget (rows OR bytes) → `ResultStore.put_*` + a mechanical `summary` + bounded `preview` + `artifact_ref` + `full_row_count` + `truncated=True`.
- Budgets are config with conservative defaults (concrete numbers chosen in the plan, e.g. ~N rows / ~K bytes). One ledger per session; holds the `ResultStore`.
- Pure/deterministic (no LLM).

### 5.4 `AgentLoop` opt-in wiring (`src/labrat/agent/loop.py`) — MODIFY
- `__init__` gains `ledger: ContextLedger | None = None`.
- At the seam (line 164): when `ledger is not None`, `output_str = render(ledger.record(tu.name, dispatch))`; when `None`, unchanged.
- `on_tool_call` still receives the **full** payload (traces/audit stay complete) — model-visible bounding affects history only.

### 5.5 High-volume tool retrofit — MODIFY
- `run_sql`, `profile_dataset`, `sample_rows`, `column_stats` declare their large payload in a small explicit contract the ledger stores (prefer an explicit typed hook over the ledger sniffing `DispatchResult.value` shape). Off-ledger path keeps returning today's string.

### 5.6 `run_agent_task` default-on toggle (`src/labrat/agent/runner.py`) — MODIFY
- `enable_ledger: bool = True`: constructs a `ResultStore` + `ContextLedger` and passes it to the loop by default; `enable_ledger=False` restores bare-loop behavior.

## 6. Data flow & provenance
1. A tool runs; its `DispatchResult` → `ContextLedger.record`.
2. Under budget → model sees the full result (today's behavior). Over budget → model sees `summary + bounded preview + artifact_ref`; the full payload lives in the `ResultStore`.
3. The final answer can cite `artifact_ref`s; provenance resolves through the `ResultStore`. `on_tool_call`/trace writers still capture the full payload for audit.

## 7. Scope / non-goals
- **In scope:** `ResultStore`, `ContextLedger`, `ModelVisibleToolResult` + serialization, `AgentLoop` opt-in wiring, high-volume tool retrofit, `run_agent_task` default-on toggle.
- **Deferred:** Phase 2 `dispatch_subagent` (scoped sub-agent tool); LLM-generated summaries (stay mechanical); the rest of the Codex full-stack plan (3 runtime modes, hosted backends, MCP multi-warehouse); changing the claude-mcp path (bypasses `AgentLoop`).
- **Explicitly NOT a claude-mcp DAB-score lever** — its DAB relevance is reducing `labrat-agent`/Codex token burn only.

## 8. Testing
- `ResultStore`: round-trip a table (Parquet + meta), a JSON profile, a JSONL trace; `preview` respects row AND byte caps; `get(ref)` resolves.
- `ContextLedger`: under-budget passes through unchanged; over-budget stores + returns summary/preview/ref/row_count/truncated; budgets honored.
- `AgentLoop`: **no ledger → byte-identical history** (existing `test_agent_loop.py` stays green); with a ledger → an oversized tool result becomes a bounded model-visible string while the full payload is retrievable from the store; `on_tool_call` still gets the full payload.
- Tool retrofit: each high-volume tool's model-visible output is bounded yet informative; off-ledger unchanged.
- `run_agent_task`: `enable_ledger=True` (default) bounds a large result; `enable_ledger=False` restores bare behavior.

## 9. Open questions (resolve during planning)
- **Budget defaults** (rows/bytes) — pick concrete conservative numbers in the plan.
- **Tool payload contract:** explicit typed hook (preferred) vs ledger sniffing `DispatchResult.value` — the plan pins the exact contract.
- **ResultStore root:** per-session temp dir (TUI) vs the run dir (benchmark) — caller-provided.

## 10. Success criteria
- Ledger on → an oversized `run_sql`/`profile_dataset` result enters history as a bounded summary while the full data is retrievable by `artifact_ref`.
- Bare `AgentLoop` (no ledger) byte-identical to today; `run_agent_task` defaults the ledger on with a working `enable_ledger=False` toggle.
- Full gate clean (ruff/pyright/pytest); existing loop/runner tests green.

## 11. Build decomposition (plan phases)
- **Phase A — ResultStore** (store + preview caps; pure, fixture-tested).
- **Phase B — ModelVisibleToolResult + serialization + ContextLedger** (record/budget logic; pure).
- **Phase C — AgentLoop opt-in wiring** (byte-identity when absent; bounded when present).
- **Phase D — high-volume tool retrofit** (the 4 tools declare their payload).
- **Phase E — `run_agent_task` default-on toggle** + regression + gates.
