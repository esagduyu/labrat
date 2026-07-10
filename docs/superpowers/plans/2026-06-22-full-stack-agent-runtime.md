# Full Stack Agent Runtime Implementation Plan

> **Status (2026-07-10): DEFERRED WHOLESALE 2026-06-22** (see `decisions.md`) — the 12-milestone refactor was never executed as a unit. Two slices were since cherry-picked and shipped separately: **Milestone 4's Context Ledger** (merge `ebf3bd0`, 2026-07-05) and **Milestone 5's MCP multi-warehouse slice as T2a** (merge `c6597f6`, 2026-07-09 — `LABRAT_MCP_PROFILES` + host config generators). The remainder (three runtime modes, hosted-Codex backend, GTM packaging) stays deferred.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn LabRat into a platform-agnostic full-stack data agent with three first-class paths: LabRat Tools, LabRat Agent API Mode, and LabRat Agent Hosted Mode.

**Architecture:** Keep one shared LabRat data core, then expose it through three runtime contracts. Tools Mode exposes the core through MCP and embeddable Python APIs; API Mode lets LabRat own the agent loop through provider APIs; Hosted Mode lets LabRat own the data workflow envelope while Claude Code, Codex, or another host owns subscription auth and its native agent loop.

**Tech Stack:** Python 3.12, Pydantic v2, Textual, MCP stdio server, Anthropic SDK, OpenAI SDK, provider CLIs for hosted mode, SQLGlot, Polars, DuckDB, JSONL audit/history stores, pytest, ruff, pyright.

---

## Product Contract

LabRat should support three paths without pretending they are the same product.

| Path | User Has | Runtime Owner | LabRat Promise | Primary Buyer Reason |
|---|---|---|---|---|
| LabRat Tools | Any MCP-capable agent or custom harness | External host | Data-agent tools, schema grounding, SQL safety, validation, artifacts, traces | "I like my current agent; make it better at data." |
| LabRat Agent API Mode | OpenAI, Anthropic, or compatible API credentials | LabRat | Full loop control, context ledger, provider telemetry, prompt/cache strategy, reproducible evals | "I want a serious data-agent runtime." |
| LabRat Agent Hosted Mode | Claude/Codex subscription login | Provider host plus LabRat envelope | Subscription-friendly full LabRat workflow, normalized traces, Cartographer, artifacts, post-run validation | "I want LabRat without setting up API billing." |

This distinction matters because API Mode can be benchmark-grade and deterministic, while Hosted Mode is entitlement-friendly but partially constrained by the provider host.

## Strategic Justification

The three-path architecture is not just a packaging choice. It is the product answer to how people actually buy and use frontier models today.

### Why LabRat Tools Exists

Many early users already live inside Claude Code, Codex, Cursor, OpenCode, Pi, or an internal agent harness. For them, forcing a new end-to-end app is adoption friction. LabRat Tools should be the easiest wedge:

- install LabRat;
- connect a warehouse;
- expose the LabRat tool registry over MCP or Python;
- let the user's existing agent call `profile_dataset`, `run_sql`, `verify_join`, `search_reference_docs`, and the rest of the data-agent toolbelt.

The tradeoff is control. In Tools Mode, LabRat does not decide when to profile, when to validate, how much context the host retains, or whether the host agent follows the ideal analysis workflow. The host agent owns the loop. LabRat can make tools safe, bounded, traceable, and well-documented, but it cannot guarantee full agent behavior.

This mode is still strategically important because it proves value immediately: "Keep your agent. Give it a data analyst's toolbelt."

### Why API Mode Exists

API Mode is where LabRat becomes a serious runtime rather than a tool bundle. With provider API credentials, LabRat can own:

- the agent loop;
- prompt construction;
- context ledger policy;
- tool result summarization;
- provider usage telemetry;
- prompt-cache and reasoning-state strategy;
- eval repeatability;
- audit and artifact creation;
- final validation and answer packaging.

This is the best path for production, teams, benchmark submissions, and users who care about reproducibility. It is also the path where OpenAI and Anthropic can be made genuinely comparable because LabRat controls the same runtime contract above each provider.

The tradeoff is billing and setup. A Claude Pro/Max subscription does not automatically equal Anthropic API access; a ChatGPT subscription does not automatically equal OpenAI API usage. API Mode should be positioned as the high-control path for users willing to bring API credentials.

### Why Hosted Mode Exists

Hosted Mode is the bridge between adoption and control. Some users have a Claude or Codex subscription and want LabRat's full workflow without separately setting up API billing. Hosted Mode should let those users run LabRat through provider-hosted agent surfaces:

- Claude Code / Claude Agent SDK-style surfaces for Claude subscription users;
- Codex CLI/app-style surfaces for ChatGPT/Codex subscription users;
- future hosted agent surfaces as they emerge.

Hosted Mode should not pretend to be API Mode. The provider host owns the hidden model loop, auth, quotas, subscription limits, and some context behavior. LabRat owns the envelope around that loop:

- generate a host-specific MCP config;
- run Cartographer before the task;
- expose bounded LabRat tools;
- collect MCP traces;
- store result artifacts;
- run postflight validation where possible;
- normalize the final answer into a Cheese artifact;
- show the same TUI workflow around the run.

The benefit over Tools Mode is that LabRat can provide a more opinionated workflow shell, not just passive tools. The benefit over API Mode is that users can start with the subscription they already have. The cost is weaker reproducibility and less direct control over caching/context internals.

Hosted Mode's honest promise is: "Use LabRat's workflow with the subscription you already have."

### Why The Context Ledger Comes Before Provider Chasing

The Codex/GPT-5.5 experiment showed that the core context problem is not simply "prompt caching is missing." The current `AgentLoop` resends the full system prompt, all tool schemas, and the whole growing history including large tool outputs on every turn. Prompt caching can reduce cost or latency for a stable prefix, but it does not make oversized context free and it does not solve tool-result bloat.

The right runtime fix is a LabRat-owned context ledger:

- large `profile_dataset` outputs become artifacts plus compact summaries;
- large `run_sql` results become result refs plus bounded previews;
- repeated schemas and tool outputs are summarized rather than replayed raw;
- the final answer still carries provenance through artifact refs;
- providers get a smaller, more stable prompt surface.

This makes all providers better. It helps OpenAI API Mode, Anthropic API Mode, hosted Codex, hosted Claude, and tools-only hosts. It is therefore a core LabRat runtime feature, not a provider-specific optimization.

### Why Full LabRat Is Differentiated

LabRat should not claim to beat generic agents by having a smarter chat loop. Its defensible product value is that data work has a lifecycle generic agents do not own:

- first-contact schema mapping;
- metric and reference-doc grounding;
- verified joins;
- SQL safety;
- query repair;
- result artifact storage;
- validation rules;
- audit trails;
- shareable answer packages;
- memory from prior corrections;
- eval-backed workflow discipline.

Tools Mode exposes pieces of this. Full LabRat Agent turns it into a coherent workflow. That is the reason to use LabRat end-to-end instead of staying fully inside an existing coding agent.

## Current Implementation Findings

These are the repo facts this plan builds on.

1. **The tool layer is real and valuable.** `src/labrat/agent/data_tools.py` builds the standard registry with `search_reference_docs`, `workflow`, `profile_dataset`, `link_schema`, `run_sql`, `verify_join`, `attach_database`, file loading, and Mongo materialization.

2. **The MCP path exists but is narrower than the product promise.** `src/labrat/mcp/server.py` exposes the tool registry over stdio, but `_build_context_from_env()` only accepts `db_type=duckdb`. The README says LabRat can mount into Claude Code, Codex, Cursor, OpenCode, or any MCP host; the server needs profile and multi-adapter support to make that true outside benchmark-shaped DuckDB usage.

3. **The API-mode loop exists but conflates provider transport with runtime policy.** `AgentLoop` sends `messages`, `tools`, and `system` to a `ModelProvider`, then appends full tool results to `history`. This is simple and testable, but it causes context growth and makes provider caching a band-aid rather than a runtime strategy.

4. **The OpenAI/Codex implementation is split between distributable and personal paths.** `OpenAICompatibleProvider` uses Chat Completions and lacks Responses-state, prompt-cache metadata, reasoning passback, and usage capture. `CodexSubscriptionProvider` has usage capture, `prompt_cache_key`, encrypted reasoning passback, and `store=False`, but it talks to an unversioned ChatGPT/Codex backend and is not a clean distributable API path.

5. **The robust Claude subscription path is currently benchmark-only.** `claude-mcp` in `src/labrat/eval/benchmarks/dab/suite.py` mounts LabRat MCP into Claude Code and is the recommended DAB path. The product TUI does not use this path.

6. **The TUI is not provider-agnostic yet.** `src/labrat/screens/main.py` constructs `ClaudeCodeProvider()` directly and assembles its own smaller registry instead of using the standard data-tool registry or profile provider settings.

7. **Profile provider settings are missing.** `src/labrat/profile/model.py` stores connection fields, not provider mode, model, hosted/API choice, reasoning effort, cache policy, or default runtime path.

8. **Some agent-adjacent features still hardcode Claude.** `src/labrat/agent/tools/run_validations.py` creates `ClaudeCodeProvider()` internally instead of using the active session provider or a provider-supplied `LLMFn`.

9. **Cartographer and Scent are shipped but not product-default.** `maze/cartographer.py` and `search_reference_docs` are real. DAB can run `--agent-cartograph`, but first-connect TUI/product flow does not yet invoke Cartographer or persist the generated Scent as a normal onboarding artifact.

10. **Tool outputs are prompt-visible payloads, not ledgered artifacts.** `profile_dataset` can emit a large schema/sample blob, and `run_sql` can return up to 1000 rows directly into model history. This explains the Codex context burn better than a simple "prompt caching not enabled" diagnosis.

11. **Existing DAB parity docs are correct but narrow.** `docs/dab-driver-parity.md` proves submission-equivalence between `claude-mcp` and `labrat-agent`; it does not prove product-runtime parity across Tools, API, and Hosted modes.

## Target Architecture

### Shared Packages And Boundaries

Keep the repo as one package initially, but organize it as if these install surfaces exist:

- `labrat-core`: tools, connection adapters, SQL safety, result artifacts, audit/history, Cartographer, Scent retrieval.
- `labrat-tools`: MCP server, host config generators, embeddable Python tool registry.
- `labrat-agent`: runtime session, context ledger, API backends, hosted backends, TUI, eval harness.

Avoid physically splitting packages until the runtime boundaries are stable.

### Runtime Objects

Introduce these core runtime concepts:

- `RuntimeMode`: `tools`, `api`, `hosted`.
- `ProviderKind`: `anthropic_api`, `openai_responses`, `openai_compatible`, `claude_code_hosted`, `codex_hosted`, `codex_subscription_dev`.
- `AgentBackend`: runs a whole LabRat task and returns normalized output.
- `ModelProvider`: stays as the low-level API-mode model-turn interface.
- `LabRatSession`: owns profile, connections, catalog, registry, prompt context, provider/backend, context ledger, result store, audit log, and thread state.
- `ContextLedger`: decides which tool outputs stay in model context, which become artifacts, and which are summarized.
- `CheeseArtifact`: the durable answer unit: final answer, SQL, result refs, charts, assumptions, validation status, trace refs, and source tables.

### Backend Rules

API Mode:

- LabRat owns `AgentLoop`.
- Uses `ModelProvider` implementations.
- Gets the strongest caching, telemetry, and reproducibility contract.

Hosted Mode:

- LabRat owns the run envelope, prompt contract, MCP server config, Cartographer pre-pass, traces, artifacts, validation, and TUI display.
- Claude Code, Codex, or another host owns subscription auth and native model loop.
- Hosted mode must be honest about lower control over hidden host behavior.

Tools Mode:

- LabRat does not own the loop.
- LabRat ships excellent tools, safe outputs, trace hooks, and host setup docs.

---

## Milestone 0: Product Contract And Decision Record

**Goal:** Lock the three-path strategy so future work does not blur tools, API runtime, and hosted runtime.

**Files:**
- Create: `docs/runtime_modes.md`
- Modify: `decisions.md`
- Modify: `README.md`

- [ ] **Step 1: Write `docs/runtime_modes.md`**

  Include:
  - the three paths table from this plan;
  - what LabRat owns in each path;
  - what the provider host owns;
  - which paths are benchmark-grade;
  - which paths are subscription-friendly;
  - which paths are suitable for production automation.

- [ ] **Step 2: Add a dated decision to `decisions.md`**

  Decision: LabRat will support Tools Mode, API Mode, and Hosted Mode as separate runtime contracts over a shared core.

- [ ] **Step 3: Update README wording**

  Replace any claim that implies all providers are equivalent with language that says provider surfaces differ by runtime mode.

- [ ] **Step 4: Run doc-safe checks**

  Run: `uv run pytest tests/unit/test_build_provider.py tests/unit/test_mcp_server.py -q`

  Expected: existing tests pass; no product code has changed.

- [ ] **Step 5: Commit**

  Commit: `docs: define LabRat runtime modes`

## Milestone 1: Runtime Mode And Provider Configuration

**Goal:** Let profiles and CLI/TUI startup express which runtime mode and provider should be used.

**Files:**
- Modify: `src/labrat/profile/model.py`
- Modify: `src/labrat/profile/manager.py`
- Modify: `src/labrat/profile/storage.py`
- Modify: `src/labrat/cli.py`
- Modify: `src/labrat/app.py`
- Create: `src/labrat/runtime/config.py`
- Create: `tests/unit/test_runtime_config.py`
- Modify: `tests/unit/test_profile.py`
- Modify: `tests/unit/test_cli_conn.py`

- [ ] **Step 1: Add failing tests for profile runtime defaults**

  Test that a new profile defaults to:
  - `runtime_mode="hosted"` on local interactive installs;
  - `provider_kind="claude_code_hosted"` only when explicitly selected, not silently hardcoded;
  - `model="claude-sonnet-4-6"`;
  - no API key stored in profile JSON.

- [ ] **Step 2: Add `src/labrat/runtime/config.py`**

  Define Pydantic models:
  - `RuntimeMode`
  - `ProviderKind`
  - `RuntimeConfig`
  - `ProviderConfig`

  Keep secrets out of these models. Store only references like `secret_name`, auth mode, or env-var key names.

- [ ] **Step 3: Extend `Profile`**

  Add runtime/provider fields with migration-safe defaults. Existing profile JSON must still load.

- [ ] **Step 4: Extend `labrat conn add`**

  Add flags:
  - `--runtime tools|api|hosted`
  - `--provider anthropic-api|openai-responses|openai-compatible|claude-code-hosted|codex-hosted|codex-subscription-dev`
  - `--model`
  - `--base-url`

- [ ] **Step 5: Make startup use default profile intentionally**

  `LabRatApp.on_mount()` currently chooses `profiles[0]`. Add a default-profile setting and make `labrat conn set-default` persist it.

- [ ] **Step 6: Run targeted tests**

  Run: `uv run pytest tests/unit/test_runtime_config.py tests/unit/test_profile.py tests/unit/test_cli_conn.py -q`

  Expected: all pass.

- [ ] **Step 7: Commit**

  Commit: `feat(runtime): persist runtime mode and provider configuration`

## Milestone 2: Session Spine For The TUI And CLI

**Goal:** Stop constructing providers, registries, and context ad hoc in screens and scripts.

**Files:**
- Create: `src/labrat/runtime/session.py`
- Create: `src/labrat/runtime/factory.py`
- Modify: `src/labrat/screens/main.py`
- Modify: `scripts/run_task.py`
- Modify: `src/labrat/agent/data_tools.py`
- Create: `tests/unit/test_runtime_session.py`
- Modify: `tests/tui/test_main_screen.py`

- [ ] **Step 1: Add failing test proving TUI does not hardcode Claude**

  Patch the runtime factory to return a fake session and assert `MainScreen` uses it instead of importing `ClaudeCodeProvider`.

- [ ] **Step 2: Create `LabRatSession`**

  It owns:
  - active profile;
  - primary connection and catalog;
  - multi-connection `ToolContext`;
  - standard registry plus TUI callback tools;
  - backend/provider config;
  - prompt context;
  - audit/history/thread/result stores.

- [ ] **Step 3: Create `build_session_for_profile(profile)`**

  This should be the only app-level function that wires connections, catalogs, registry, provider/backend, prompt, and stores together.

- [ ] **Step 4: Move TUI tool registration into session construction**

  `MainScreen` may pass callbacks for editor/results/chart display, but it should not decide the provider or rebuild a smaller registry by hand.

- [ ] **Step 5: Update `scripts/run_task.py`**

  Use the same session/runtime factory for API mode. Keep explicit `--connections` supported for benchmark/dev usage.

- [ ] **Step 6: Run targeted tests**

  Run: `uv run pytest tests/unit/test_runtime_session.py tests/tui/test_main_screen.py tests/unit/test_agent_runner.py -q`

  Expected: all pass.

- [ ] **Step 7: Commit**

  Commit: `feat(runtime): route TUI and task CLI through LabRatSession`

## Milestone 3: Backend Interface Split

**Goal:** Separate "one model turn" from "one LabRat task run" so hosted backends do not have to pretend to be `ModelProvider`s.

**Files:**
- Create: `src/labrat/agent/backends/base.py`
- Create: `src/labrat/agent/backends/api.py`
- Create: `src/labrat/agent/backends/hosted.py`
- Create: `src/labrat/agent/backends/factory.py`
- Modify: `src/labrat/agent/runner.py`
- Modify: `src/labrat/eval/benchmarks/dab/suite.py`
- Create: `tests/unit/test_agent_backend.py`
- Create: `tests/unit/test_backend_factory.py`

- [ ] **Step 1: Define backend request/result models**

  `AgentRunRequest` should include:
  - user prompt;
  - system prompt;
  - tool registry;
  - tool context;
  - runtime mode;
  - max turns/tool calls/time;
  - trace sink;
  - context ledger policy.

  `AgentRunResult` should include:
  - final text;
  - tool calls;
  - latency;
  - usage;
  - trace refs;
  - artifact refs;
  - infra status.

- [ ] **Step 2: Implement `ApiAgentBackend`**

  It wraps the existing `AgentLoop` and `ModelProvider`. This should preserve current `run_agent_task()` behavior.

- [ ] **Step 3: Keep `run_agent_task()` as a compatibility shim**

  Make it build an `ApiAgentBackend` internally or delegate to one. Existing DAB and unit tests should not break.

- [ ] **Step 4: Add backend factory**

  It maps `RuntimeConfig` to:
  - `ApiAgentBackend` for API providers;
  - hosted backend stubs for Claude/Codex;
  - a clear error for Tools Mode, because Tools Mode has no LabRat-owned run loop.

- [ ] **Step 5: Run targeted tests**

  Run: `uv run pytest tests/unit/test_agent_backend.py tests/unit/test_backend_factory.py tests/unit/test_agent_runner.py -q`

  Expected: all pass.

- [ ] **Step 6: Commit**

  Commit: `feat(agent): introduce task-level backend abstraction`

## Milestone 4: Context Ledger And Result Artifacts

**Goal:** Fix context burn by changing what enters model history, not by relying on provider prompt caching to save an oversized transcript.

**Files:**
- Create: `src/labrat/runtime/context_ledger.py`
- Create: `src/labrat/results/store.py`
- Create: `src/labrat/agent/tools/serialization.py`
- Modify: `src/labrat/agent/loop.py`
- Modify: `src/labrat/agent/tools/run_sql.py`
- Modify: `src/labrat/agent/tools/profile_dataset.py`
- Modify: `src/labrat/agent/tools/sample_rows.py`
- Modify: `src/labrat/agent/tools/column_stats.py`
- Create: `tests/unit/test_context_ledger.py`
- Create: `tests/unit/test_result_store.py`
- Modify: `tests/unit/test_agent_loop.py`

- [ ] **Step 1: Add failing tests for tool output budgets**

  Test that a tool result larger than a fixed budget is summarized and stored as an artifact ref.

- [ ] **Step 2: Implement `ResultStore`**

  Store:
  - tabular results as Parquet plus JSON metadata;
  - profile snapshots as JSON;
  - trace payloads as JSONL;
  - previews capped by rows and bytes.

- [ ] **Step 3: Implement `ContextLedger`**

  It should return a `ModelVisibleToolResult` with:
  - `summary`;
  - `preview`;
  - `artifact_ref`;
  - `full_row_count`;
  - `truncated`.

- [ ] **Step 4: Wire `AgentLoop` through the ledger**

  Add an optional ledger parameter. When absent, preserve old behavior for compatibility. When present, every tool result passes through the ledger before being appended to `history`.

- [ ] **Step 5: Tighten high-volume tools**

  `run_sql`, `profile_dataset`, `sample_rows`, and `column_stats` should expose model-visible summaries that are useful but bounded.

- [ ] **Step 6: Add token/context regression tests**

  Build a fake provider that records serialized message size across turns. Assert bounded mode grows slowly even after repeated large `run_sql` outputs.

- [ ] **Step 7: Run targeted tests**

  Run: `uv run pytest tests/unit/test_context_ledger.py tests/unit/test_result_store.py tests/unit/test_agent_loop.py tests/unit/test_sql_execution_tools.py tests/unit/test_profile_dataset_tool.py -q`

  Expected: all pass.

- [ ] **Step 8: Commit**

  Commit: `feat(runtime): add context ledger and bounded tool result artifacts`

## Milestone 5: LabRat Tools Product Hardening

**Goal:** Make the tools-only path a polished product, not just an internal MCP server.

**Files:**
- Modify: `src/labrat/mcp/server.py`
- Create: `src/labrat/mcp/config.py`
- Create: `src/labrat/mcp/host_configs.py`
- Modify: `src/labrat/cli.py`
- Create: `docs/labrat-tools.md`
- Modify: `tests/unit/test_mcp_server.py`
- Create: `tests/unit/test_mcp_host_configs.py`

- [ ] **Step 1: Extend MCP environment parsing**

  Replace DuckDB-only env parsing with the same profile/connection factory used by `LabRatSession`.

- [ ] **Step 2: Keep benchmark DuckDB env compatibility**

  Existing `LABRAT_MCP_CONNECTIONS` must still work for DAB and tests.

- [ ] **Step 3: Add host config generator commands**

  Add CLI commands:
  - `labrat mcp print-config --host claude-code`
  - `labrat mcp print-config --host codex`
  - `labrat mcp print-config --host generic`

- [ ] **Step 4: Add trace and artifact options**

  Support:
  - `LABRAT_MCP_LOG_DIR`;
  - `LABRAT_RESULT_DIR`;
  - `LABRAT_MAZE_DIR`;
  - `LABRAT_PROFILE`.

- [ ] **Step 5: Document Tools Mode**

  `docs/labrat-tools.md` should include install, config snippets, environment variables, host-specific caveats, and the honest limitation: host agent owns the loop.

- [ ] **Step 6: Run targeted tests**

  Run: `uv run pytest tests/unit/test_mcp_server.py tests/unit/test_mcp_host_configs.py -q`

  Expected: all pass.

- [ ] **Step 7: Commit**

  Commit: `feat(mcp): harden LabRat Tools mode`

## Milestone 6: API Mode Provider Parity

**Goal:** Make API Mode the highest-control runtime for OpenAI and Anthropic.

**Files:**
- Create: `src/labrat/agent/providers/openai_responses.py`
- Modify: `src/labrat/agent/providers/anthropic_direct.py`
- Modify: `src/labrat/agent/providers/openai_compatible.py`
- Modify: `src/labrat/agent/providers/__init__.py`
- Create: `src/labrat/agent/usage.py`
- Create: `tests/unit/test_openai_responses_provider.py`
- Modify: `tests/unit/test_providers.py`
- Modify: `tests/unit/test_build_provider.py`

- [ ] **Step 1: Define normalized provider usage**

  Add `ProviderUsage` with input, output, cached, reasoning, request count, and provider-specific raw metadata.

- [ ] **Step 2: Add official OpenAI Responses provider**

  This is the distributable OpenAI API path. It should support tools, usage capture, prompt-cache key, `store=False`, and reasoning settings where the selected model supports them.

- [ ] **Step 3: Keep `OpenAICompatibleProvider` for compatibility**

  Document it as the broad compatibility path, not the best OpenAI API path.

- [ ] **Step 4: Add Anthropic usage and cache controls**

  Add a provider-level way to mark stable prompt/tool blocks for caching where supported. Capture input/output/cache usage when the SDK response exposes it.

- [ ] **Step 5: Keep `CodexSubscriptionProvider` as dev-only**

  Rename product docs around it to `codex_subscription_dev` so users understand it is not the official API path.

- [ ] **Step 6: Run targeted tests**

  Run: `uv run pytest tests/unit/test_openai_responses_provider.py tests/unit/test_providers.py tests/unit/test_codex_subscription_provider.py tests/unit/test_build_provider.py -q`

  Expected: all pass without network.

- [ ] **Step 7: Commit**

  Commit: `feat(providers): add API-mode provider parity and usage telemetry`

## Milestone 7: Hosted Backend Mode

**Goal:** Give subscription users a full LabRat workflow without pretending subscriptions are raw APIs.

**Files:**
- Create: `src/labrat/agent/backends/claude_code_hosted.py`
- Create: `src/labrat/agent/backends/codex_hosted.py`
- Create: `src/labrat/agent/backends/hosted_protocol.py`
- Modify: `src/labrat/agent/backends/factory.py`
- Modify: `src/labrat/runtime/session.py`
- Create: `tests/unit/test_claude_code_hosted_backend.py`
- Create: `tests/unit/test_codex_hosted_backend.py`
- Create: `docs/hosted-mode.md`

- [ ] **Step 1: Define hosted backend protocol**

  Inputs:
  - prompt;
  - system prompt;
  - generated MCP config;
  - scratch cwd;
  - environment;
  - timeout;
  - trace directory.

  Outputs:
  - final text;
  - latency;
  - normalized trace refs;
  - infra status;
  - host raw output path.

- [ ] **Step 2: Implement Claude Code hosted backend**

  Use the DAB `claude-mcp` pattern as the production starting point:
  - run in scratch cwd;
  - mount LabRat MCP;
  - restrict native tools where possible;
  - strip API-key env vars when subscription auth is intended;
  - parse JSON output when available;
  - collect MCP traces.

- [ ] **Step 3: Implement Codex hosted backend**

  Start with a subprocess adapter around Codex CLI or the supported local Codex invocation available in the user's environment. It should mount LabRat MCP, use scratch cwd, collect host output, and classify auth/rate-limit errors as infra.

- [ ] **Step 4: Add hosted-mode session execution**

  `LabRatSession.run_user_task()` should call `AgentBackend.run()` regardless of API or Hosted mode.

- [ ] **Step 5: Document hosted limitations**

  Be explicit:
  - provider owns hidden loop behavior;
  - LabRat owns preflight/postflight/tooling;
  - eval reproducibility is weaker than API Mode;
  - subscription limits can interrupt long runs.

- [ ] **Step 6: Run targeted tests**

  Run: `uv run pytest tests/unit/test_claude_code_hosted_backend.py tests/unit/test_codex_hosted_backend.py tests/unit/test_backend_factory.py -q`

  Expected: all pass with subprocess calls mocked.

- [ ] **Step 7: Commit**

  Commit: `feat(agent): add hosted backends for subscription workflows`

## Milestone 8: First-Connect Cartographer And Prompt Context

**Goal:** Make the Rat Maze a normal first-contact product flow, not just a DAB flag.

**Files:**
- Create: `src/labrat/agent/prompt_context.py`
- Modify: `src/labrat/screens/onboarding.py`
- Modify: `src/labrat/app.py`
- Modify: `src/labrat/runtime/session.py`
- Modify: `src/labrat/maze/cartographer.py`
- Modify: `src/labrat/agent/prompts/__init__.py`
- Create: `tests/unit/test_prompt_context.py`
- Create: `tests/tui/test_onboarding_cartographer.py`

- [ ] **Step 1: Add prompt context builder**

  It should combine:
  - base prompt;
  - dialect prompt;
  - runtime-mode caveats;
  - Scent quick references;
  - context bundle;
  - active memories;
  - active validations;
  - profile/catalog metadata.

- [ ] **Step 2: Persist onboarding catalog choices**

  `OnboardingResult.catalog_type` and `catalog_path` are currently collected but not saved into profile/runtime state. Persist them.

- [ ] **Step 3: Run Cartographer on first connect**

  Add a session-level `ensure_scent()` that runs `cartograph_prepass()` when no Scent exists for the profile, with deterministic mode by default and optional semantic pass later.

- [ ] **Step 4: Make `search_reference_docs` available everywhere**

  Ensure the same Scent store is visible in Tools, API, and Hosted mode through `LABRAT_MAZE_DIR` or profile-derived defaults.

- [ ] **Step 5: Run targeted tests**

  Run: `uv run pytest tests/unit/test_prompt_context.py tests/unit/test_search_reference_docs.py tests/tui/test_onboarding_cartographer.py -q`

  Expected: all pass.

- [ ] **Step 6: Commit**

  Commit: `feat(maze): run Cartographer and inject prompt context on first connect`

## Milestone 9: Validation, Audit, Threads, And Cheese Artifacts

**Goal:** Make LabRat's full-agent value visible in the deliverable, not only in the run trace.

**Files:**
- Create: `src/labrat/cheese/artifact.py`
- Create: `src/labrat/cheese/store.py`
- Modify: `src/labrat/agent/tools/run_validations.py`
- Modify: `src/labrat/validations/checker.py`
- Modify: `src/labrat/audit/log.py`
- Modify: `src/labrat/thread/manager.py`
- Modify: `src/labrat/thread/findings.py`
- Modify: `src/labrat/screens/main.py`
- Create: `tests/unit/test_cheese_artifact.py`
- Modify: `tests/unit/test_validations/test_validation_checker.py`
- Modify: `tests/unit/test_audit.py`
- Modify: `tests/unit/test_findings.py`

- [ ] **Step 1: Define `CheeseArtifact`**

  Include:
  - question;
  - final answer;
  - SQL statements;
  - result refs;
  - chart refs;
  - assumptions;
  - source tables;
  - validation report;
  - trace refs;
  - provider/runtime metadata.

- [ ] **Step 2: Remove hardcoded provider from `run_validations`**

  Inject an `LLMFn` from the active session or provider. Tools Mode can omit LLM validation or expose it as host-driven.

- [ ] **Step 3: Write audit events for full runs**

  Log user prompt, tool call refs, SQL execution refs, validation outcomes, and artifact creation. Do not log raw result rows.

- [ ] **Step 4: Fix findings to pin artifact/version refs**

  Findings should point to Cheese artifacts and result refs, not ambiguous thread ids.

- [ ] **Step 5: Surface Cheese artifacts in the TUI**

  After a run, users should be able to pin/export the answer package with provenance.

- [ ] **Step 6: Run targeted tests**

  Run: `uv run pytest tests/unit/test_cheese_artifact.py tests/unit/test_audit.py tests/unit/test_findings.py tests/unit/test_validations -q`

  Expected: all pass.

- [ ] **Step 7: Commit**

  Commit: `feat(cheese): persist answer artifacts with validation and provenance`

## Milestone 10: Cross-Mode Eval And Regression Matrix

**Goal:** Prove that Tools, API, and Hosted modes work and make their tradeoffs measurable.

**Files:**
- Create: `src/labrat/eval/suites/runtime_matrix.py`
- Create: `tests/unit/test_runtime_matrix_suite.py`
- Modify: `scripts/eval_dab.py`
- Modify: `docs/dab-driver-parity.md`
- Create: `docs/runtime-parity-matrix.md`

- [ ] **Step 1: Define runtime matrix dimensions**

  Dimensions:
  - mode: tools, api, hosted;
  - provider/backend;
  - registry equality;
  - trace support;
  - artifact support;
  - context ledger support;
  - Cartographer support;
  - usage telemetry support;
  - benchmark suitability.

- [ ] **Step 2: Add a fixture-based runtime smoke suite**

  Use DuckDB fixture data and fake providers/host subprocesses. Assert every mode can answer a simple row-count task and produce trace/artifact refs where promised.

- [ ] **Step 3: Update DAB docs**

  Keep `claude-mcp` as the current best full-benchmark path, but explain how API Mode and Hosted Mode map to DAB drivers.

- [ ] **Step 4: Add CI-friendly regression command**

  Example: `uv run python scripts/run_smoke_regression.py runtime-matrix`

- [ ] **Step 5: Run targeted tests**

  Run: `uv run pytest tests/unit/test_runtime_matrix_suite.py tests/unit/test_dab_suite_run_trial.py -q`

  Expected: all pass.

- [ ] **Step 6: Commit**

  Commit: `test(eval): add cross-mode runtime regression matrix`

## Milestone 11: Packaging And User-Facing Docs

**Goal:** Make the three paths easy to adopt.

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Create: `docs/labrat-agent-api-mode.md`
- Create: `docs/labrat-agent-hosted-mode.md`
- Create: `docs/labrat-tools.md`
- Create: `docs/provider-setup.md`
- Modify: `TESTING.md`

- [ ] **Step 1: Add optional dependency groups**

  Keep a single package, but expose extras:
  - `labrat[tools]`;
  - `labrat[agent]`;
  - `labrat[warehouse-all]`;
  - `labrat[dev]`.

- [ ] **Step 2: Add setup guides**

  Write separate guides for:
  - Claude subscription user;
  - ChatGPT/Codex subscription user;
  - Anthropic API user;
  - OpenAI API user;
  - BYO MCP host user.

- [ ] **Step 3: Add comparison docs**

  Explain when to choose Tools Mode, Hosted Mode, or API Mode.

- [ ] **Step 4: Update testing guide**

  Add manual QA steps for:
  - TUI hosted run;
  - TUI API run;
  - MCP tools run;
  - first-connect Cartographer;
  - artifact export.

- [ ] **Step 5: Run final gate**

  Run:
  - `uv run ruff format .`
  - `uv run ruff check .`
  - `uv run pyright`
  - `uv run pytest -q`

  Expected: all pass.

- [ ] **Step 6: Commit**

  Commit: `docs: document LabRat tools, API, and hosted adoption paths`

---

## Execution Order

Recommended order:

1. Milestone 0 locks language and avoids product drift.
2. Milestones 1-3 create runtime boundaries without changing data behavior.
3. Milestone 4 fixes the context-burning root cause.
4. Milestone 5 makes Tools Mode a real adoption wedge.
5. Milestones 6-7 make API and Hosted modes real.
6. Milestones 8-9 make LabRat differentiated rather than just provider-switchable.
7. Milestones 10-11 make the result measurable and shippable.

## Acceptance Gates

LabRat Tools is ready when:

- MCP server supports profile-backed connections, not only DuckDB env specs.
- Host config generation exists for Claude Code, Codex, and generic MCP.
- Tool outputs are bounded and traceable.
- Docs make clear that the external host owns the loop.

LabRat Agent API Mode is ready when:

- TUI and `run_task` can run through OpenAI and Anthropic API credentials without hardcoded Claude paths.
- OpenAI has a Responses-based provider with usage and caching telemetry.
- Anthropic API provider has normalized usage telemetry.
- Context ledger prevents runaway transcript growth.
- DAB/runtime smoke can report usage, trace, and artifact metadata.

LabRat Agent Hosted Mode is ready when:

- Claude Code hosted backend uses the MCP path rather than the fragile text pseudo-tool protocol.
- Codex hosted backend can run a LabRat task through subscription auth with MCP tools.
- Hosted runs produce normalized trace/artifact outputs.
- The docs are honest that hosted mode is less reproducible than API mode.

LabRat Full-Stack Agent is ready when:

- first-connect Cartographer is product-default;
- prompt context includes Scent, memory, validation, catalog, and dialect;
- every final answer can become a Cheese artifact;
- all three runtime modes are represented in the runtime parity matrix.

## Product Positioning

Use this language consistently:

- **LabRat Tools:** "Keep your agent. Give it a data analyst's toolbelt."
- **LabRat Agent Hosted Mode:** "Use LabRat's workflow with the subscription you already have."
- **LabRat Agent API Mode:** "Run the full LabRat data-agent runtime with maximum control, telemetry, and reproducibility."
- **Overall:** "LabRat meets you where you are, but gets better when it owns the workflow."

### Positioning Ladder

LabRat should create a natural upgrade path instead of forcing a binary choice.

1. **Start with LabRat Tools.** The user keeps Claude Code, Codex, Pi, Cursor, OpenCode, or an internal harness. LabRat earns trust by making the existing agent better at data.
2. **Move to Hosted Mode.** The user wants the LabRat workflow, TUI, Cartographer, traces, and Cheese artifacts, but still wants to use a Claude/Codex subscription login rather than API billing.
3. **Move to API Mode.** The user wants reproducibility, telemetry, stronger provider parity, better context/cache control, team automation, or benchmark-grade runs.
4. **Adopt the full Rat Maze.** The user or team wants accumulated Scent docs, memories, corrections, validations, and reusable analysis recipes to compound over time.

This ladder is important because it lets LabRat serve two audiences without weakening either one:

- BYO-agent users who only want the tool package;
- data teams who want an end-to-end data-agent runtime.

### Messaging By Persona

For a Claude subscription-only user:

- Do not promise API-native LabRat unless they also set up Anthropic API/Console billing.
- Offer LabRat Tools through Claude Code MCP as the lowest-friction path.
- Offer Hosted Mode as the fuller LabRat workflow through Claude's hosted agent surface.
- Explain that API Mode is available later if they want stronger reproducibility and team automation.

For a ChatGPT/Codex subscription user:

- Offer LabRat Tools through Codex MCP as the lowest-friction path.
- Offer Hosted Mode through Codex CLI/app-style subscription login.
- Offer OpenAI API Mode through official API credentials for production-grade control.

For a data platform or analytics-engineering team:

- Lead with API Mode plus the Rat Maze.
- Emphasize audit, result artifacts, safety, validation, provider routing, and evals.
- Treat Hosted Mode as useful for individual adoption, not the final production architecture.

For an agent-framework power user:

- Lead with LabRat Tools and embeddable `labrat-core`.
- Make it easy to compare their own harness against LabRat Agent using the runtime matrix.
- Do not force them into the TUI.

### Why Choose Full LabRat Instead Of Tools Only

The answer should be concrete:

- Tools only gives the host agent capabilities.
- Full LabRat gives the user a governed data-analysis workflow.

Full LabRat should be better when the user needs:

- consistent profile-first analysis;
- context-efficient handling of large results;
- automatic Cartographer/Scent grounding;
- verified joins before trusting relationships;
- validation rules and postflight checks;
- durable answer artifacts;
- traceability for review;
- memory from prior corrections;
- repeatable eval behavior across providers.

If those needs do not matter, Tools Mode is enough. That honesty is part of the product trust.

### What Not To Claim

Avoid these claims in docs, README copy, or release notes:

- "All providers are equivalent." They are not; their auth, context, caching, and host-loop controls differ.
- "Hosted Mode is benchmark-grade." It is useful and subscription-friendly, but API Mode is the stronger reproducibility path.
- "Prompt caching fixes context burn." It helps when stable prefixes are reused, but the LabRat runtime must still bound tool outputs and manage history.
- "LabRat is only an MCP server." MCP is one product surface; the full agent runtime is the differentiated product.
- "LabRat is only a TUI." The TUI is the opinionated workflow surface over the reusable core.

### One-Sentence Narrative

LabRat is a data-agent runtime that starts as a toolbelt for the agent you already use, then becomes the full workflow layer when data correctness, provenance, context efficiency, and team memory matter.
