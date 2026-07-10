# T1d Phase 2 — `dispatch_subagent`: Scoped Sub-Agent Dispatch — Design

**Date:** 2026-07-09 · **Status:** approved (adopt+refresh of the stranded 2026-06-26 spec, user-approved)
**Baseline:** `docs/superpowers/specs/2026-06-26-context-ledger-subagent-dispatch-design.md` on branch `feat/context-ledger` (§ Decision 3, §4.6, §7-dispatch invariants, Plan B) — adopted verbatim except where refreshed below. Phase 1 (Context Ledger) shipped `ebf3bd0`.
**Validated-by-production since the baseline:** registry-exclusion recursion guard (`run_program`, `4a08a4c`); `{summary, artifact_ref}` ledger mechanics (M4); capability self-error on provider-less hosts (`llm_extract` precedent).

## 1. Problem

The orchestrating agent's history carries every exploration side-quest. Spacedock (#1 DAB) keeps per-stage contexts small; LabRat's loop cannot delegate. Phase 1 bounded *tool outputs*; Phase 2 lets the model bound *sub-tasks*: a model-callable `dispatch_subagent` runs a scoped fresh loop and returns by reference.

## 2. Refreshed decisions (deltas vs the 2026-06-26 baseline; all else adopted)

- **R1 — Provider seam:** `build_agent_session` injects `ctx.subagent_runner: SubagentRunner | None` — a closure over the parent's provider, registry, ledger/store, and settings (the `ctx.llm_fn` injection precedent). Hosts that never build a session (MCP server, claude-mcp) have `runner=None` → the tool returns a **structured self-error** (`ok=False`-style payload, never an exception), byte-for-byte the `llm_extract` pattern. Benchmark-path isolation therefore holds by construction.
- **R2 — Sub-registry derived from the HOSTING registry** (not a fresh `build_data_tools_registry()`): parent registry's tools minus `dispatch_subagent` by name. This also retires the M4 review's deferred I1 advisory (confused-deputy: a restricted host's programs/sub-agents must not gain tools the host never mounted).
- **R3 — Depth-1 via two independent structural guards:** the sub-registry excludes the tool AND the sub-ctx carries `subagent_runner=None`. Either alone suffices; both are asserted.
- **R4 — Shared execution substrate:** sub-ctx shares `connections`/`catalogs`/`primary`/`profile_name`/`read_only`/`llm_fn` (same DB session → temp tables and artifact refs stay resolvable; read-only gates flow through). Same `ResultStore` and same `ContextLedger` instance as the parent (one artifact namespace).
- **R5 — Uniform return path:** the tool's output model carries `ledger_payload() -> ("json", {...})`; the parent's ledger mechanically bounds it to `{summary, preview, artifact_ref}` like any other large output. Ledger-off parents receive the full text. No bespoke storage code in the tool.
- **R6 — Budgets in the input model:** `max_turns: int = 6` (ceiling 8), `max_tool_calls: int = 10` (ceiling 15), clamped by validators. The dispatch itself is one parent tool call — parent budget engages automatically at the existing dispatch site.
- **R7 — Registration:** unconditional in `build_data_tools_registry` (self-gating per R1); `mutating=False` (sub-tools carry their own gates via the shared ctx). The TUI exposes it for free through M1's superset registry.

## 3. Design

### 3.1 Input / output models (`src/labrat/agent/tools/dispatch_subagent.py`)

- `_Input`: `sub_task: str` (required); `artifact_refs: list[str] = []`; `context_hint: str | None = None`; `max_turns: int = 6`; `max_tool_calls: int = 10` — validators clamp to `1 ≤ max_turns ≤ 8`, `1 ≤ max_tool_calls ≤ 15`.
- `_Output`: `ok: bool`, `final_text: str`, `turns_used: int`, `tool_calls_used: int`, `error: str | None = None`; implements `ledger_payload() -> ("json", model_dump())`. Self-error shape: `ok=False, final_text="", error="dispatch_subagent unavailable: no subagent runner on this host (requires an in-process AgentLoop provider)"`.

### 3.2 Runner seam (`src/labrat/agent/session.py`)

- `SubagentRunner` protocol (in `tools/base.py` next to `LLMFn`): `async def __call__(self, *, seed_prompt: str, artifact_refs: list[str], max_turns: int, max_tool_calls: int) -> tuple[str, int, int]` returning `(final_text, turns_used, tool_calls_used)`. The runner (which holds the store) resolves `artifact_refs` to previews and splices them into the seed as the `## Provided artifacts` section — the tool never touches the store (R5/§3.3).
- `ToolContext` gains `subagent_runner: SubagentRunner | None = None` (defaulted — every existing construction site unaffected).
- In `build_agent_session`, after the loop is constructed: if `ctx.subagent_runner is None`, install a closure that (a) builds the sub-registry per R2 from the *registry argument*; (b) builds the sub-ctx per R4 with `subagent_runner=None`; (c) constructs a fresh `AgentLoop` directly (NOT via `build_agent_session` — avoids re-injection recursion and reuses the parent's ledger instance): same provider, sub-registry, sub-ctx, same system-prompt mechanism (`system=""` → dialect default), `ledger=<parent's ledger>`, caps from the call; (d) runs it, collecting `on_text` into the final text; returns the tuple. Caller injection wins (a caller-provided runner is never overwritten — mirrors `llm_fn`).

### 3.3 Seed construction (inside the tool's `execute`)

Seed prompt = fenced sections, deterministic order:
1. `## Sub-task` — `args.sub_task` (+ `context_hint` paragraph when present).
2. `## Provided artifacts` — for each ref in `artifact_refs`: `store.preview(ref)` via the parent ledger's store (`ctx` has no store; the preview resolution happens in the RUNNER closure, which holds the store — the tool passes refs through in the seed-request; concretely: the tool composes sections 1 and 3 and passes `artifact_refs` to the runner, which splices resolved previews in as section 2. Unresolvable ref → one inline `"[unresolvable ref: …]"` line, never an exception).
3. `## Relevant reference notes` — top-3 sections from `SearchReferenceDocsTool` logic run against `sub_task` (deterministic lexical retrieval, reusing the real tool's execute with the parent ctx); omitted when empty.
Parent history is NEVER included (test-pinned by capturing provider messages).

### 3.4 Tool behavior

`execute(ctx, args)`: `runner = ctx.subagent_runner`; `None` → structured self-error `_Output`. Else compose seed, `await runner(seed_prompt=…, max_turns=…, max_tool_calls=…)`, return `_Output(ok=True, final_text=…, turns_used=…, tool_calls_used=…)`. Any runner exception → `_Output(ok=False, error=str(exc))` (the sub-loop's failure must not kill the parent turn; fail-open at the tool boundary, consistent with tool conventions).

## 4. Non-negotiables

1. Depth-1: both guards (R3) test-asserted; a sub-agent calling `dispatch_subagent` gets unknown-tool.
2. Scoped seed: parent history provably absent from every sub-loop provider call (captured-messages test).
3. Benchmark isolation: no path under `eval/`/`mcp/` acquires a runner; the tool self-errors there (grep + test pinned). claude-mcp untouched.
4. Parent-budget accounting: one dispatch = one parent tool call (existing dispatch-site semantics; test pinned).
5. Registry derivation from the hosting registry (R2) — a parent mounting a subset yields a sub-registry ⊆ that subset minus the dispatch tool.
6. Existing paths byte-identical when the tool is unused: bare `AgentLoop`, `run_agent_task`, TUI histories unchanged (the ctx field defaults `None`; injection only adds an unused attribute).
7. Uniform ledger flow (R5): no direct ResultStore writes from the tool.
8. Deterministic seed for identical inputs+store state; no clock.
9. Pyright strict (`agent/`); repo gates per commit; known env flake `test_app_renders` non-signal.

## 5. Testing

Unit: input clamping; self-error on `runner=None`; seed composition (sections, unresolvable ref line, empty-Scent omission). Session: runner injected once, caller-wins, sub-registry excludes tool + respects restricted hosts, sub-ctx guards (`subagent_runner=None`, shared connections/read_only). Loop-level e2e with a scripted fake provider: parent dispatches → sub-loop sees only the seed (captured messages) → sub-result returns through the parent ledger as `{summary, artifact_ref}` when oversized; parent `tool_calls_used` counts the dispatch. Runner-exception → `ok=False`, parent turn survives. TUI: registry superset test already covers exposure (extend the M1 wiring test's expected-extras only if it pins an exact tool count). Manual pty spot-check: ask the TUI agent to "delegate a sub-task to count orders and report back" → `▸ dispatch_subagent(...)` trace, sensible answer, sub-agent activity NOT polluting the parent transcript.
