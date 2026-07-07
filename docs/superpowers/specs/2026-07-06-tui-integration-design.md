# TUI Integration — Umbrella Design (M0–M5 machinery into the LabRat TUI)

**Date:** 2026-07-06
**Status:** Approved (brainstormed with Fable; to be executed by Sonnet/Opus via the four phase plans)
**Companion docs:** `docs/tui-integration-handoff.md` (session-start map, code-verified 2026-07-06), `docs/superpowers/plans/2026-07-06-tui-m1-agent-stack.md` … `…-tui-m4-verify-provenance.md` (implementation plans, one per phase)

---

## 1. Problem

Everything shipped in milestones M0–M5 lives on the benchmark/agent paths and is invisible in the interactive TUI. The TUI chat path **bypasses `run_agent_task` entirely**: `screens/main.py::on_mount` (lines ~294–320) hand-builds an `AgentLoop` with a 12-tool registry (`run_sql`, `draft_sql`, `create_chart`, `list_tables`, `describe_table`, `sample_rows`, `search_columns`, `column_stats`, `explain_sql`, `search_query_history`, `recall_memories`, `run_validations`) around `ClaudeCodeProvider`, with:

- none of `build_data_tools_registry()`'s grounding/power tools (`profile_dataset`, `link_schema`, `verify_join`, `search_reference_docs`, `explain_lineage`, `workflow`, `llm_extract`/`llm_classify`, `run_program`, `check_sql`, `attach_database`, `load_file`, `load_mongo_collection`),
- no Context Ledger (`ledger=None` → oversized tool outputs flood model history),
- no `llm_fn` injection (per-row primitives would self-error),
- no verifier,
- single-DB `ToolContext` (no `attach_database` routing), no `read_only` flag,
- no Cartographer first-connect pre-pass (Scent never exists for TUI profiles),
- no caller for the M5 harvest machinery (`SessionHarvester`, `harvest_controller.py`, `apply_approved_sections` all have zero production callers).

This design routes the TUI through the real agent stack and surfaces the week's machinery in four sequential phases.

## 2. Decisions locked during brainstorming (with rationale)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Scope | All four phases, one umbrella spec + four plans | Maximize planning leverage now; each phase independently shippable |
| D2 | Chat wiring architecture | **Approach A: shared session factory** (`agent/session.py::build_agent_session`), used by both `run_agent_task` and the TUI | Parity by construction; prevents the drift that caused this gap. One-shot `run_agent_task`-per-message was rejected: it builds a fresh `AgentLoop` per call, which would reset multi-turn chat history |
| D3 | Provider | Configurable via `Profile.agent_provider`, default `"auto"`: `AnthropicProvider` when `ANTHROPIC_API_KEY` is set, else `ClaudeCodeProvider` + one-time degraded-tool-support warning | ClaudeCodeProvider is known-fragile under tool round-trips (see `docs/dab-integration.md`); with ~25 tools it gets worse. The DAB MCP-mount fix doesn't transfer: `draft_sql`/`create_chart` callbacks must run in-process to update the UI |
| D4 | Harvest boundary | **Manual action + thread-switch prompt.** Never blocks quit | No thread-close lifecycle exists; quit-time modal is hostile UX; capture is persistent so nothing is lost |
| D5 | Settings home | Extend `Profile` with 4 optional defaulted fields + new `SettingsScreen` modal (`ctrl+comma`) | All three knobs are naturally per-connection-profile (harvest is already profile-keyed); one persistence store, one new screen |
| D6 | Cartographer semantics | Deterministic-only (`with_semantics=False`), always | T1c ablation was net-negative (−3.7pp); a human owns semantics |
| D7 | Which verifier | The **loop sufficiency verifier** (`LLMVerifier`, fail-open, `max_verify_rounds=2`), opt-in via `verify_enabled`. K-of-N consensus (`agent/verification/consensus.py`) stays benchmark-only | Consensus ablated within-noise and is one-shot-shaped, wrong for interactive chat. Executors: do not wire consensus into the TUI |
| D8 | Staleness handling | Detect + offer refresh; **never auto-regenerate** | Harvested/human-authored sections must never be silently clobbered |

## 3. Phase 0 — shared foundations (lands inside the Phase 1 plan)

### 3.1 Profile fields (`src/labrat/profile/model.py`)

Add to `Profile` (frozen Pydantic model; all optional with defaults → back-compat with every serialized profile, Pydantic fills missing keys):

```python
agent_provider: Literal["auto", "anthropic", "claude-code", "openai", "codex"] = "auto"
agent_model: str | None = None      # explicit model override
harvest_opt_in: bool = False        # fail-closed, matches SessionHarvester default
verify_enabled: bool = False
```

Updates go through `profile.model_copy(update={...})` + `ProfileManager` persistence (model is frozen).

### 3.2 SettingsScreen (`src/labrat/screens/settings.py`, new)

- `ModalScreen` bound to `ctrl+comma` on `MainScreen`; registered in `HelpScreen`.
- Shows the active profile's four settings: a `Select` for `agent_provider`, an `Input` for `agent_model`, switches for `harvest_opt_in` / `verify_enabled`.
- Save persists via `ProfileManager`; changed provider/verify settings take effect on next app start (v1 — no live loop rebuild; the screen says so). `harvest_opt_in` takes effect immediately (it is read at harvest time).
- Modeled on the existing modal pattern (`memories_viewer.py` / `thread_manager.py`: DataTable/Buttons, `dismiss(result)`).

### 3.3 Provider resolution (`src/labrat/agent/session.py::resolve_provider`)

```python
def resolve_provider(profile: Profile) -> ModelProvider
```

- `"auto"`: if `ANTHROPIC_API_KEY` present → `AnthropicProvider(model=profile.agent_model or "claude-sonnet-4-6")` (pin explicitly — never let a default fall through, per the pin-model-explicitly rule); else `ClaudeCodeProvider` and the caller shows a **one-time** `notify(severity="warning")`: tool round-trips are degraded on this path.
- Explicit names route through the existing `build_provider(name, model)` factory (`agent/providers/__init__.py`).

## 4. Phase 1 — TUI chat through the real agent stack

### 4.1 Shared factory (`src/labrat/agent/session.py`, new)

```python
def build_agent_session(
    *,
    ctx: ToolContext,
    registry: ToolRegistry,
    provider: ModelProvider,
    system_prompt: str = "",
    dialect: str = "duckdb",
    verify: bool = False,
    max_verify_rounds: int = 2,
    enable_ledger: bool = True,
    ledger_dir: Path | None = None,
    max_turns: int | None = None,
    max_tool_calls: int | None = None,
) -> AgentLoop
```

Extracts exactly what `run_agent_task` does today between its signature and `loop.run()`:
1. `ctx.llm_fn = provider_llm_fn(provider, system=_LLM_FN_SYSTEM)` when `ctx.llm_fn is None` (move `_LLM_FN_SYSTEM` into `session.py`; `runner.py` imports it back).
2. `ledger = ContextLedger(ResultStore(ledger_dir or mkdtemp("labrat-ledger-")))` when `enable_ledger`.
3. `verifier = LLMVerifier(provider_llm_fn(provider))` when `verify`.
4. Return the configured `AgentLoop(provider=…, registry=…, ctx=…, system=system_prompt, dialect=…, verifier=…, max_verify_rounds=…, ledger=…, max_turns=…, max_tool_calls=…)`.

**`run_agent_task` is refactored to call `build_agent_session`** — public signature and behavior unchanged; the existing runner/DAB tests are the regression net. The loop is persistent: the TUI keeps it across chat turns so `loop.history` accumulates (multi-turn memory), whereas `run_agent_task` still uses it one-shot.

### 4.2 Registry composition

- `build_data_tools_registry()` (`agent/data_tools.py`) gains one optional parameter: `run_sql_tool: RunSqlTool | None = None` — when given, it registers that instance instead of the bare `RunSqlTool()`. All other consumers untouched.
- `main.py` builds: `registry = build_data_tools_registry(run_sql_tool=RunSqlTool(on_result=…, on_draft=…))`, then registers the 5 TUI extras: `DraftSqlTool(on_draft)`, `CreateChartTool(on_chart)`, `RunValidationsTool()`, `RecallMemoriesTool()`, `SearchQueryHistoryTool()`. **Total ≈ 25 tools**, a strict superset of the benchmark registry.

### 4.3 ToolContext (multi-DB form)

```python
ToolContext(
    connections={"main": self._connection},
    catalogs={"main": self._catalog},
    primary="main",
    profile_name=self._profile,
    read_only=profile.is_read_only,   # new: closes the chat-path mutation gap
)
```

`read_only` requires threading the `Profile` (not just its name/dialect strings) into `MainScreen` — `app.py::_connect_and_launch` passes `profile: Profile` through (keep the string params as display fallbacks for the disconnected case).

### 4.4 System prompt

`build_system_prompt(dialect)` (the prescriptive `system_base.md`) **plus a TUI addendum constant** (new, in `agent/prompts/`): when to use `draft_sql` (propose SQL into the editor for the user) vs `run_sql` (execute now); `create_chart` renders in the results pane; results render in the UI so don't re-print large tables in prose.

### 4.5 Ledger

On by default. Durable dir: `~/.labrat/ledger/<profile>/<session-ts>/` (session timestamp captured once at mount). Provenance survives the session; pruning is a later concern.

### 4.6 ChatPanel changes (`widgets/chat_panel.py`)

- **Delete the `registry.dispatch` monkey-patch** in `_start_agent` (lines ~143–158). Replace with the loop's first-class hook: `loop.run(message, on_text=…, on_tool_call=…, on_status=…)`.
- `on_tool_call(name, args, ok, output, latency_ms)` renders a trace line on completion: `▸ name(args) ✓ 320ms` (or `✗`). Deliberate UX change: lines appear when a call *finishes*, with status + latency. Trace toggle (`ctrl+\`) semantics unchanged.
- `on_status(text)` renders dim italic status lines (verifier feedback now; Phase-2 reuses `notify` instead — see §5).
- Callbacks are invoked from a worker; UI mutations must be posted to the UI thread (use `post_message` / `call_from_thread` consistent with existing worker patterns).

### 4.7 Risks / accepted trade-offs (Phase 1)

- ClaudeCodeProvider + 25 text-protocol tool schemas is expected to be flaky. Accepted and warned, not fixed. "auto" steers to AnthropicProvider when possible.
- Chat answers get slower/costlier per turn (more tools, bigger prompt, possible verifier). Ledger bounds context burn.
- Manual TUI verification is a **named gate** for this phase (see §8).

## 5. Phase 2 — first-connect Cartographer (T2c)

- **Trigger:** in `MainScreen.on_mount`, after the agent session is built (connection + catalog present), spawn a background Textual worker calling:
  ```python
  cartograph_prepass(
      connections={"main": conn}, catalogs={"main": catalog}, primary="main",
      scent_dir=Path.home() / ".labrat" / "maze" / profile_name / "scent",
      with_semantics=False,
  )
  ```
  Idempotent by construction (existing docs → instant return), so it runs on **every** connect and only does work on first contact.
- **Store:** user store keyed by profile (above). `search_reference_docs` (in the Phase-1 registry) already reads the dual store (project + user), so Scent is retrievable in chat as soon as the pre-pass lands. The tool/store must resolve the user store from the same `profile_name` the pre-pass wrote under — verify this seam in the plan (it is the one place a path mismatch would silently produce zero retrievals).
- **Status UX:** `notify()` on start/finish + transient status-bar suffix ("🗺 mapping schema…" → "scent ready · N docs"). **Fail-open:** on exception, warning notify; chat works without Scent.
- **Staleness (D8 — detect + offer only):** when docs already exist, compare `staleness.schema_fingerprint(catalog)` to the stored `schema_hash` meta; if stale → notify "schema changed since scent was mapped — refresh with ctrl+shift+m" and add `action_refresh_scent` (`ctrl+shift+m`): regenerate **Cartographer-authored docs only**, preserving `harvested`-tagged and human-authored sections per the M5 write-path rules (`maze/store.py` write path + `scent_audit`). Never auto-regenerate.
- Keybinding caveat: `ctrl+shift+…` chords are terminal-dependent; verify delivery during manual testing and fall back to a free plain binding (e.g. `f6`) if the target terminal swallows them.

## 6. Phase 3 — M5 harvest surface

### 6.1 Capture (cheap, zero LLM calls)

New session-scoped `CorrectionBuffer` (plain class in `src/labrat/memory/correction_buffer.py` — memory domain, no Textual deps, unit-testable under pyright strict):
- **Chat corrections:** each user message that follows an agent turn which produced SQL → buffer `(user_message, context_sql)` candidate pairs.
- **Edit corrections:** `QueryEvent`s with `edit_diff` from the always-on `QueryHistoryLog` for this session window.
Capture is bookkeeping only; extractor LLM calls happen at harvest time.

### 6.2 Trigger (D4)

- `action_harvest_review` (`ctrl+shift+h`, same terminal caveat as §5): construct `SessionHarvester(profile, llm_fn=provider_llm_fn(provider), store=MemoryStore(...), enabled=harvesting_enabled(is_interactive=True, profile_opt_in=profile.harvest_opt_in))`; run `harvest_correction`/`harvest_events` over the buffer in a worker; then `harvest_controller.review_corrections(memories, generated_at=…, model_id=…)` → push `HarvestReviewScreen`. If opt-in is off, the action opens Settings with a hint instead.
- **Thread-switch prompt:** in `action_manage_threads._on_result`, if opt-in AND buffer non-empty → `ConfirmScreen("N corrections captured this session — review before switching?")` → same flow. Quit never blocks; un-reviewed events persist in the history log.

### 6.3 HarvestReviewScreen (`src/labrat/screens/harvest_review.py`, new)

Modeled on `memories_viewer.py` (DataTable + Buttons):
- Rows = drafted sections: **domain**, title, body preview, source-memory count; per-row approve/skip toggle (space/enter).
- "Apply approved" → `apply_approved_sections` (audited, **fail-loud**): audit rejection renders in the modal, nothing is written on failure. Success → notify with written doc paths.
- "Cancel" discards drafts (memories remain in the MemoryStore; re-harvest can re-draft).

### 6.4 Domain routing (shipped together — from the Fable M5 review)

- `memory/extractor.py`: extractors set `Memory.table_scope`, resolved from tables referenced in `context_sql` / `edit_diff` against the catalog (best-effort; unresolved → `None`).
- `maze/harvest.py::draft_harvested_sections`: return domain-keyed drafts — each `Section` tagged with its target domain; `__global__` only as genuine fallback.
- `harvest_controller.review_corrections` + `apply_approved_sections`: route each approved section to its per-table/domain Scent doc.
This is what makes per-table compounding real instead of a `__global__` dumping ground.

## 7. Phase 4 — verification toggle + provenance footer (T3c)

- **Verification:** `profile.verify_enabled=True` → `build_agent_session(verify=True)` (D7: the loop `LLMVerifier`, fail-open, bounded; **not** consensus). Verifier feedback renders via `on_status` dim lines.
- **Provenance footer:** pure UI aggregation, zero core changes. `ChatPanel` accumulates a `TurnProvenance` per turn from the `on_tool_call` stream: `search_reference_docs` hits (doc names + freshness from Section meta vs current schema fingerprint), `verify_join` / `explain_lineage` usage, `run_sql` count, verifier outcome from `on_status`. On turn end, append one dim footer line to the agent history entry, e.g. `⚑ grounded: scent ×2 (fresh) · join verified · 3 queries · verifier ✓`. Tier labels via `maze/provenance.py::source_rank`/`best_source`. **No footer when there's nothing to say.**
- Parsing tool outputs for the footer must be tolerant (outputs may be ledger-summarized): prefer structured fields when present; degrade to counts-only.

## 8. Cross-cutting: error handling, testing, gates

**Error posture:**
- Provider errors → red chat line (existing behavior preserved).
- Cartographer pre-pass, footer aggregation → **fail-open** (warning notify / silently skip footer).
- Harvest write path → **fail-loud** (audit rejection displayed; no partial writes).
- ClaudeCodeProvider degraded warning → once per session.
- All LLM-bearing work in Textual workers; UI thread never blocks.

**Testing per phase:**
- Unit tests with fake providers / fake `llm_fn` (no LLM-gated tests in the default suite).
- **Parity test:** `run_agent_task` and the TUI wiring produce identically-configured loops (same registry names superset check, ledger present, llm_fn injected, verifier wiring).
- **Screen-class pilot tests** (Textual `run_test()` / pilot) for every new/changed screen: SettingsScreen, HarvestReviewScreen, chat round-trip through the full registry with a scripted provider — closing the "no modal has direct Screen tests" gap for new surface. Snapshot additions where layout changes.
- TESTING.md gains a manual-verification section per phase; **manual TUI verification is a named exit gate for Phases 1 and 3.**
- Repo gates close every task: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. `screens/` remains pyright-strict-exempt.

**Branching:** per-phase branches — `feat/tui-m1-agent-stack`, `feat/tui-m2-cartographer`, `feat/tui-m3-harvest`, `feat/tui-m4-verify-provenance` — merged to master after gates pass, sequentially (each builds on the previous merge).

## 9. Out of scope (explicit)

- Mounting `labrat.mcp.server` in the TUI (in-process callbacks preclude it; MCP stays the benchmark/host-embedding path).
- Hardening ClaudeCodeProvider's text protocol.
- K-of-N consensus verification in the TUI (D7).
- LLM-semantic Scent authoring anywhere in the TUI (D6).
- Live loop rebuild on settings change (restart required, v1).
- Ledger pruning/GC.
- Onboarding-flow changes; multi-connection UI (only `attach_database` via chat).
- DAB/ADE benchmark behavior changes of any kind — Phase 1's `runner.py` refactor must be behavior-preserving on the benchmark paths.

## 10. Milestone summary

| Phase | Branch | Delivers | New files | Key changed files |
|-------|--------|----------|-----------|-------------------|
| 1 | `feat/tui-m1-agent-stack` | Real agent stack in chat + settings foundation | `agent/session.py`, `screens/settings.py`, TUI prompt addendum | `profile/model.py`, `agent/runner.py`, `agent/data_tools.py`, `screens/main.py`, `widgets/chat_panel.py`, `app.py` |
| 2 | `feat/tui-m2-cartographer` | First-connect Scent + staleness detect/refresh | — | `screens/main.py` (worker + action), TESTING.md |
| 3 | `feat/tui-m3-harvest` | Harvest capture→review→apply + domain routing | `screens/harvest_review.py`, `memory/correction_buffer.py` | `memory/extractor.py`, `maze/harvest.py`, `screens/harvest_controller.py`, `screens/main.py` |
| 4 | `feat/tui-m4-verify-provenance` | Verify toggle live + provenance footer | — | `widgets/chat_panel.py`, `screens/main.py`, `screens/settings.py` |
