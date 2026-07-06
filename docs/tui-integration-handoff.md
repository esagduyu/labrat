# TUI Integration Handoff (2026-07-06)

**Purpose:** a session-start map for the work of integrating the milestones shipped this week (M0–M5) into the LabRat TUI experience. All the machinery exists and is tested on the benchmark/agent paths; almost none of it is surfaced in the interactive TUI yet.

## The core finding

**The TUI chat path bypasses `run_agent_task` entirely.** `screens/main.py` (`on_mount`, ~lines 257–320) builds an `AgentLoop` directly with a **small hand-rolled `ToolRegistry`** — only `draft_sql`, `create_chart`, `run_validations`, `recall_memories`, `search_query_history` — and drives it via `ClaudeCodeProvider` (`claude --print`, tool schemas serialized into a text prompt; it does **not** speak MCP). `agent/runner.py`'s docstring still says the TUI path is "eventually."

Because of this, the TUI gets **none** of:
- `build_data_tools_registry()`'s 20 tools (profile_dataset, run_sql, link_schema, verify_join, search_reference_docs, explain_lineage, workflow, **llm_extract/llm_classify**, **run_program**, …)
- the **Context Ledger** (`AgentLoop(ledger=...)` is never passed one in the TUI → `None`)
- the **verifier** / consensus verification (`agent/verification/`)
- **`llm_fn` injection** (so the per-row LLM primitives self-error even if registered)
- the **Cartographer** first-connect Scent pre-pass (`cartograph_prepass` is called only from the DAB suite)

**Highest-leverage move:** route the TUI chat through `run_agent_task` (or replicate its wiring in `screens/main.py`): `build_data_tools_registry()` + a real provider-backed `llm_fn` (`provider_llm_fn`) + `enable_ledger=True` + optional verifier. That single change surfaces most of the week's machinery into the TUI at once. Do this first; the discrete surfaces below build on it.

## Per-capability TUI status (code-verified 2026-07-06)

| Capability | Shipped where | In the TUI today? |
|---|---|---|
| `run_agent_task` / full agent loop | `agent/runner.py` | ❌ TUI builds `AgentLoop` directly with a 5-tool registry (`main.py:257-320`) |
| MCP server / 20-tool registry | `mcp/server.py`, `data_tools.py` | ❌ TUI never imports `labrat.mcp`; uses `ClaudeCodeProvider` (`claude --print`) |
| Context Ledger | `runtime/context_ledger.py`, `results/store.py` | ❌ `ledger` never passed to the TUI's `AgentLoop` |
| llm_extract / llm_classify / run_program | `data_tools.py`, `agent/program/` | ❌ not in the TUI registry (need `build_data_tools_registry()` + `llm_fn`) |
| Cartographer / Scent (`search_reference_docs`) | `maze/cartographer.py` | ❌ `cartograph_prepass` only called from DAB; Scent not surfaced. **This is T2c (first-connect Cartographer).** |
| Verification (consensus + re-derive) | `agent/verification/consensus.py` | ❌ TUI passes no verifier |
| Memory recall (read) | `recall_memories` tool, `memories_viewer.py` | ✅ WIRED — `RecallMemoriesTool` in registry (`main.py:310`); `ctrl+g` → `MemoriesViewerScreen` (view/delete) |
| Memory write / **M5 harvest** | `memory/harvest.py`, `maze/harvest.py`, `screens/harvest_controller.py` | ❌ NO caller. Nothing in the TUI ever creates a memory or triggers harvest. **This is the #1 M5 follow-on.** |
| Column lineage / `explain_lineage` (M3) | `agent/tools/explain_lineage.py` | ❌ not in the TUI registry |

## TUI surface inventory (what exists to hook onto)

- **Panes** (`screens/main.py` compose, ~174–224): status bars, `chat-pane` (ChatPanel), center (`QueryEditor` + results/chart), `schema-pane` (SchemaBrowser); draggable dividers.
- **Bindings / actions:** `ctrl+1..4` focus panes; `ctrl+h` toggle schema; `ctrl+l` toggle chat; `?`/`F1` help; `ctrl+t` → `ThreadManagerScreen`; `ctrl+k` → `FindingsViewerScreen`; `ctrl+r` → `HistoryBrowserScreen`; `ctrl+g` → `MemoriesViewerScreen`; `ctrl+\` toggle tool-trace log. SQL run + mutation-confirm via `screens/confirm.py`; pin-a-finding via `FindingsManager`.
- **Modal screens:** `thread_manager`, `findings_viewer`, `history_browser`, `memories_viewer` — **none have direct Screen-class tests**; only their backing logic is unit-tested. `harvest_controller.py` is unit-tested but has zero production callers. `tests/tui/test_main_screen.py` covers mount + toggles + 3 snapshots (no modal/agent-path tests).
- **Manual TUI testing:** `TESTING.md`, against `tests/fixtures/sample_dbs/ecommerce.duckdb`.

## Two blockers the M5 harvest wiring needs (from the Fable review)

1. **No thread-close lifecycle.** `ThreadManager` (`thread/manager.py`) exposes only `create_thread`/`list_threads`/`get_thread`/`append_version`/`get_versions`/`branch_from_version` — **no close/switch/exit hook.** A harvest trigger needs a new lifecycle event. Candidate seams: the `_on_result` callback of `action_manage_threads` (`main.py:422-439`, the only place a thread switch happens today), or `App.on_unmount`/quit for "session end". Deciding what "harvest boundary" means is a **product decision**, not just wiring.
2. **No settings/opt-in field.** `profile/model.py::Profile` is a flat frozen model with no feature-flag field. `harvesting_enabled(is_interactive, profile_opt_in)` expects a `profile_opt_in` flag nobody supplies. Gating harvest requires adding a `Profile` field (or a settings store) + UI to set it (no settings screen exists).
3. **Domain-routing (also from the Fable review):** the extractors (`memory/extractor.py`) never set `Memory.table_scope`, and `draft_harvested_sections` returns a flat `list[Section]` with no cluster key — so real harvested memories all cluster under `__global__` and the approval flow can't route a drafted section to its domain doc. To make per-table Scent compounding real, teach the extractors to set `table_scope` and have `draft_harvested_sections` return `dict[str, list[Section]]` (or tag each `Section` with its domain). **Ship this together with the harvest-review UI.**

## Suggested integration sequence (for the next session to brainstorm/spec)

1. **Route the TUI chat through the real agent stack** — `run_agent_task`-equivalent wiring in `screens/main.py`: full tool registry + `llm_fn` + Context Ledger + (optional) verifier. Unlocks tools, ledger, llm_extract, run_program, explain_lineage, Scent lookup in one move. *(Biggest single win; also the riskiest — needs manual TUI verification. `ClaudeCodeProvider` fragility under tool round-trips is a known issue — see `docs/dab-integration.md`; may motivate a provider choice here.)*
2. **T2c — first-connect Cartographer** — run `cartograph_prepass` on connect (dual store, progress via `on_status`), so Scent exists in the TUI session. Prereq for surfacing Scent + the T3c provenance footer.
3. **M5 harvest surface** — add a `Profile.harvest_opt_in` field + a harvest boundary event (product decision) + `harvest_review.py` `ModalScreen` (model on `memories_viewer.py`'s DataTable+Button pattern) calling `review_corrections`→approve→`apply_approved_sections`; wire `table_scope`/domain-routing at the same time.
4. **Verification toggle** and **provenance footer (T3c)** — surface verification as an opt-in and stamp answers with `source_rank`/freshness once Scent + lineage populate the ladder.

Each is its own brainstorm → spec → plan → subagent-build cycle. See `docs/superpowers/plans/2026-07-03-competitive-build-milestones.md` for the milestone roadmap this feeds, and memory `project_tui_integration_next`.
