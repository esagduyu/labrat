# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Test
uv run pytest                                    # full suite (~670 tests)
uv run pytest tests/unit/test_agent_loop.py      # single file
uv run pytest -k "test_smoke"                    # by name
uv run pytest --co -q                            # list tests without running

# Lint / format / types — run all three before committing
uv run ruff format .          # auto-fixes formatting (run this first)
uv run ruff check .           # linting (must be clean)
uv run pyright                # type checking (must be clean)

# Run the app
uv run labrat

# dbt-CI Scent pairing (docs/dbt-ci-pairing.md)
uv run labrat scent check     # read-only dbt<->Scent fingerprint staleness gate (offline dbt parse; exit 0/1)
uv run labrat scent ingest    # headless fix: re-ingest dbt semantics into project Scent
uv run labrat scent init-ci   # scaffold the GitHub Actions workflow

# Evals
uv run python scripts/eval_duckdb.py             # no API key needed
uv run scripts/eval_ade_bench.py --tasks helixops_saas001   # wrapper; needs ADE_BENCH_DIR + Docker
uv run python scripts/eval_dab.py --datasets stockindex,stockmarket   # DAB (needs ~/repos/DataAgentBench)
uv run python scripts/eval_dab.py --output-dir runs/dab/dab-<id>      # resume a crashed run
# DAB driver/provider selection + sandbox/scoring details: docs/dab-integration.md

# Standalone LabRat agent on any query (any provider):
uv run python scripts/run_task.py --prompt "..." \
    --connections '{"main":{"db_type":"duckdb","db_path":"/path.duckdb"}}' \
    --provider anthropic --model claude-sonnet-4-6

# Run the LabRat MCP server (mount inside any MCP-supporting host):
LABRAT_MCP_CONNECTIONS='{"main":{"db_type":"duckdb","db_path":"/path.duckdb"}}' \
    uv run python -m labrat.mcp.server
```

`asyncio_mode = "auto"` is set globally — no `@pytest.mark.asyncio` needed.
LLM-gated tests are skipped unless `ANTHROPIC_API_KEY` or `LABRAT_RUN_LLM_TESTS=1` is set.

## Architecture

### Agent loop (`src/labrat/agent/`)

`AgentLoop` in `loop.py` drives tool-use round-trips. It accepts a `ToolRegistry` and an LLM provider, sends messages, receives `TextBlock | ToolUseBlock` responses, dispatches tools, and feeds `ToolResultBlock`s back until the model stops calling tools. Optional `max_turns` and `max_tool_calls` cap the loop (both default `None` = unbounded). After `run()`, `loop.turns_used` and `loop.tool_calls_used` report what actually fired. `on_tool_call` (optional callback `(name, input, ok, output, latency_ms) → None`) fires per dispatch — the DAB `labrat-agent` path uses it to write per-call traces to `agent_tool_calls.jsonl`.

**Verifier loop (opt-in):** at the would-be-final turn (no tool calls), an optional `verifier` judges whether the answer addresses the question; if "insufficient" the feedback is injected as a new user turn and the loop continues — bounded by `max_verify_rounds` (default 2) AND the remaining turn budget. **Fail-open** (an unparseable verdict counts as sufficient, so it can never trap the loop). Types live in `verifier.py` (`Verdict`, `Verifier`, `LLMVerifier`, `parse_verdict`, `provider_llm_fn`), mirroring `validations.ValidationChecker`. `provider_llm_fn` adapts the loop's own `ModelProvider` (same model + billing). Exposed via `run_agent_task(verify=False)` — default off (costs an extra LLM call per would-be-final answer). Status goes to `on_status`, separate from `on_text` so it never corrupts `final_text`. (Measured no-benefit on DAB GPT-5.5 — see `docs/dab-progress-report.md` §Phase 6.)

> **This sufficiency-judge verifier is NOT the verification we're building next.** It judges *plausibility* ("does the answer address the question"), which measured no benefit. The **next build** (FEATURE_ROADMAP **T1a**, the #1 competitor-proven lever — both top-2 DAB teams verify, we don't) is a separate **verification layer**: K-of-N **consensus** + an independent **re-derive** stage, integrated **driver-agnostically at `DabSuite.run_trial`** (so it hits the `claude-mcp` leaderboard path, not just `run_agent_task`). Spec'd + planned on branch `feat/verification-layer` — see `docs/superpowers/{specs,plans}/2026-06-24-verification-layer*.md`. Memory: `project_verification_layer_next`.

`run_agent_task` in `runner.py` is the in-process wrapper turning a one-shot prompt into `AgentTaskResult(final_text, tool_calls, latency_seconds)`. Used by the DAB `labrat-agent` driver, `scripts/run_task.py`, and (eventually) the TUI chat path. Standard data tools come from `data_tools.py::build_data_tools_registry()` — `profile_dataset`, `list_tables`, `describe_table`, `search_columns`, `link_schema`, `sample_rows`, `column_stats`, `run_sql`, `explain_sql`, `check_sql`, `explain_lineage`, `verify_join`, `attach_database`, `load_file`, `load_mongo_collection`, `search_reference_docs`, `workflow`, plus the per-row LLM primitives `llm_extract`/`llm_classify` (labrat-agent path only — they self-error with a structured result when `ctx.llm_fn` is `None`, i.e. on claude-mcp, the MCP server, and the TUI; hard 200-row fan-out cap; results land in a queryable temp table + the ledger), `run_program` (M4 2.2) — executes a JSON pipeline of registered-tool steps in one call (max 20, stop-on-error, `$handle` refs → `program_<bind>` temp tables); only a bounded summary returns to model context, and its sub-registry excludes `run_program` itself (no recursion) AND `dispatch_subagent` (`include_program=False, include_dispatch=False` — closes the confused-deputy path where a program step could otherwise launder dispatch access back in via the parent ctx); `mutating=True`; and `dispatch_subagent` (T1d Phase 2) — delegates a self-contained sub-task to a scoped, budget-capped fresh `AgentLoop` (seed = sub-task + context hint + Scent notes only, never the parent's history) and returns by ledger ref, keeping the orchestrating agent's own context lean. Self-gating like `llm_extract`/`llm_classify`: it self-errors with a structured result when `ctx.subagent_runner` is `None` — i.e. on MCP/claude-mcp — and is injected by `build_agent_session` on the labrat-agent + TUI paths.

Every tool subclasses `Tool[InputT]` (`tools/base.py`), declaring `name`/`description`/`input_model` (Pydantic). The registry validates inputs, calls `execute(ctx, input)`, wraps results in `DispatchResult`. `ToolContext` supports **multi-DB construction** — `connections: dict[str, Connection]` + `catalogs: dict[str, object]` + `primary: str` (single-DB `connection=`/`catalog=` kept as a back-compat shim). Tools with an optional `database: str | None` field route via `ctx.connections[args.database or ctx.primary]`.

Current tools (27): `build_data_tools_registry()` registers the 22 above; the TUI adds 5 — `draft_sql`/`create_chart` (callbacks) and `run_validations`/`recall_memories`/`search_query_history` (profile-keyed). `link_schema` (NL→relevant-tables-only) and `verify_join` (probe a join's match-rate + fan-out before trusting it) are the FEATURE_ROADMAP #25 grounding tools — pure/deterministic, no LLM call. `search_reference_docs` (Scent retrieval, #26a SHIPPED) does section-level lexical lookup over reference docs. `search_trails` (Trail v1 SHIPPED) does the same intent-keyed lookup over saved analysis SOPs (`kind="trail"` docs). `workflow` (#30 SHIPPED) is a 9-step data-analysis SOP tool.

`profile_dataset` (`tools/profile_dataset.py`) is a one-call profiler: per table, columns+types, row count, FKs, and a few sample rows. Size-budgeted via `max_tables`; reads structure from `ctx.catalogs[db]` and samples live rows from the connection (with a `COUNT(*)` fallback because DuckDB introspection leaves `Table.row_count` `None`). Requires the catalog populated. `load_file` loads CSV/TSV/JSON/Parquet into the DuckDB session as a TEMP table (works against a read-only primary; DuckDB-only, backed by `DuckDBConnection.load_file()`).

The system prompt (`agent/prompts/system_base.md`) is **prescriptive**: profile first → numbered plan → execute step by step, reading each result → verify the answer addresses the question before finishing.

**Cartographer / Scent (#26b SHIPPED):** `maze/cartographer.py` provides `generate_scent` (per-DB exploration) and `cartograph_prepass(...)` (idempotent first-contact pre-pass: structure-only, GT-firewalled, deterministic; optional LLM "semantics" pass left for a human to own). Dual store: project (`./labrat_maze/scent`) + user (`~/.labrat/maze/<profile>/scent`). DAB is the first consumer; the TUI first-connect path is planned as the second.

### Database layer (`src/labrat/db/`)

`Connection` ABC defines `connect`, `disconnect`, `introspect_catalog`, `execute`, `explain`. Seven adapters: `DuckDBConnection`, `PostgresConnection`, `SnowflakeConnection`, `BigQueryConnection`, `RedshiftConnection`, `TrinoConnection`, `MySQLConnection`. All return Polars DataFrames. `catalog.py` defines `Catalog / Schema / Table / Column / ForeignKey` — the in-memory schema passed in `ToolContext`.

### LLM providers (`src/labrat/agent/providers/`)

`ModelProvider` ABC. `AnthropicProvider` (Anthropic SDK). `ClaudeCodeProvider` shells the `claude` CLI (Mac OAuth, Max plan; **fragile under tool round-trips** — see `docs/dab-integration.md`). `OpenAICompatibleProvider` covers Azure, LiteLLM, Ollama, etc. `CodexSubscriptionProvider` runs **GPT‑5.5/5.6 via the ChatGPT subscription** (Responses API + `~/.codex/auth.json`, native, no proxy; personal/dev/benchmark path) — GPT‑5.6 tiers luna/terra/sol incl. `max` effort (luna rejects `ultra`), exact-replay prompt caching (replay state commits only after complete streams; fallbacks observable in per-request `request_mode` telemetry), per-request token usage on `provider.usage` folded into DAB `trials.jsonl` meta, and rate-limit fail-fast (429 → `infra:rate_limit` + exit 4 with `resets_at`). `build_provider(name, model, timeout=None, reasoning=None)` is the shared factory; `PROVIDER_NAMES = ("anthropic", "claude-code", "openai", "codex")`.

### MCP server (`src/labrat/mcp/`)

`labrat.mcp.server` mounts the data-tools registry over MCP stdio (low-level `mcp.server.Server`). Reads `LABRAT_MCP_CONNECTIONS` (JSON: `{name: {db_type: "duckdb", db_path: "..."}}`) + optional `LABRAT_MCP_PRIMARY` + optional `LABRAT_MCP_LOG_DIR` (per-dispatch tool-call audit log). Each tool is exposed via `anthropic_schema()`; results serialised via Pydantic `model_dump_json()` or `json.dumps`. The DAB `claude-mcp` driver mounts this server. Additively, `LABRAT_MCP_PROFILES` (comma-separated names, resolved via keyring-backed `ProfileManager`/`make_connection` from `~/.local/share/labrat/profiles.json`) mounts any of the seven adapters instead of duckdb-only JSON; generate a ready-to-paste host config with `uv run python -m labrat.mcp.print_config --host claude-code|codex|generic` — see `docs/labrat-tools.md` for the full env-var reference, read-only derivation rule, and which tools self-error over MCP. `labrat.mcp.toy` is a 2-tool spike server for MCP compatibility checks. **NOTE (2026-07-09): the TUI does NOT mount this server** — the TUI chat path builds its `AgentLoop` in-process (`screens/main.py`) via `agent/session.py::build_agent_session`, the **same factory `run_agent_task` uses**, so the two paths no longer drift. The TUI registry is the full `build_data_tools_registry()` (22 tools) plus its 5 UI-callback/profile-keyed tools (`draft_sql`, `create_chart`, `run_validations`, `recall_memories`, `search_query_history`) — 27 total. It gets the Context Ledger, an injected `llm_fn`, and the optional sufficiency verifier (gated on `Profile.verify_enabled`); a first-connect Cartographer pre-pass + staleness refresh (M2); correction-capture → harvest-review → audited Scent apply (M3, fail-closed on `Profile.harvest_opt_in`); and per-answer `⚑ grounded:` provenance footers (`widgets/turn_provenance.py`, M4). The TUI-integration roadmap (M1–M4) is fully shipped — see `docs/tui-integration-handoff.md` (now historical) for the build history.

### TUI (`src/labrat/screens/`, `src/labrat/widgets/`)

Built on Textual. `app.py` is the root `App`. Main screen is a 3-pane layout: chat, SQL editor (`QueryEditor` extending `TextArea` with tree-sitter-sql highlighting), schema browser. `styles.tcss` holds all Textual CSS. Pyright strict is **not** applied to `screens/` (incomplete Textual stubs).

### Supporting subsystems

| Package | Purpose |
|---------|---------|
| `maze/` | Scent grounding layer: `cartographer.py` (Cartographer pre-pass, `generate_scent`, `cartograph_prepass`); `search_reference_docs` tool retrieves from the store (#26a/#26b SHIPPED). **M5 moat foundation + T2b:** `provenance.py` (`SOURCE_TIERS` trust ladder + `source_rank`/`best_source`); `document.py` `Section` optional freshness metadata (`generated_at`/`schema_hash`/`model_id`/`git_sha`, back-compat `**Meta:**` line); `harvest.py` (`cluster_corrections` + `draft_harvested_sections` → `harvested`-tagged Gotchas, contamination-audited **fail-loud, draft-only**; `apply_approved_sections` audits+merges before write); `store.py` **write path** (`write_doc`/`load_domain` — was read-only); `staleness.py` (`schema_fingerprint`/`is_stale`). **dbt-CI pairing (2026-07-10, `5b99444`/`3a637d7`):** `ci.py` (`check_scent_freshness`/`catalog_from_dbt`) backs the `labrat scent check|ingest|init-ci` CLI group — read-only dbt↔Scent staleness gate for CI (guide: `docs/dbt-ci-pairing.md`). **Map (2026-07-10, `1b64953` — renames the never-built "Warren"):** `map.py` — `kind="map"` per-domain bundles of *pointers* to Scent/Trail docs + suggested prompts (soft-miss `resolve_members`; `draft_maps_from_dbt` auto-seeds skeletons from dbt marts structure); activation via `ToolContext.active_maps` is an **additive retrieval filter** on `search_reference_docs`/`search_trails` (unset ⇒ behavior unchanged; no eval/MCP path sets it; adds no agent tool — count stays 27); TUI author/curate/activate in `screens/maps.py` (ctrl+shift+p). Every Scent write runs through `scent_audit.py` |
| `catalog/` | External catalog adapters: `DbtLoader` (manifest.json/schema.yml) and `McpCatalogAdapter` |
| `context_engine/` | Personal domain: table relevance scoring (frequency × recency), `ContextBundle`, `ContextAnalyzer` |
| `history/` | Always-on `QueryHistoryLog` (JSONL, PII-redacted). Singleton in `run_sql.py`, monkeypatched in tests |
| `memory/` | Self-healing memories: global/table/thread scopes, JSONL store, LLM-driven extraction. **M5 T2b:** `harvest.py::SessionHarvester` wires the once-dormant `EditExtractor`/`ChatCorrectionExtractor` into a session-boundary loop (`enabled` defaults **False** = fail-closed; never harvests on benchmark paths). Gating helper in `screens/harvest_controller.py`; production caller = the TUI harvest loop (TUI-M3, 2026-07-09). **Decision-trail harvesting (2026-07-10, `1dc13bd`):** `MemoryKind.explicit_user_rule` gains its first producer — TUI `ctrl+shift+d` (RecordDecisionScreen) → immediate persist → human-gated promotion to `## Decisions` Scent sections (retrieved via `search_reference_docs`); no LLM, opt-in |
| `validations/` | Per-rule LLM checks returning `"pass"` / `"warn: ..."` / `"block: ..."` |
| `eval/` | Legacy `EvalCase`/`EvalRunner` for internal SQL evals (`bird.py`, `latency.py`); new unified `BenchmarkSuite` protocol (`types.py`) for DAB + ADE-bench under `benchmarks/<bench>/{suite,external_runner,scorer,reporter}.py`. `smoke.py` = `SubsetSuite` + `ade_smoke_suite()`. Contract: `docs/superpowers/specs/2026-05-28-unified-benchmark-suite-design.md` |
| `audit/` | JSONL event sourcing for every interaction |
| `dspy_opt/` | DSPy prompt-optimisation utils. Pyright strict excluded (no dspy stubs) |

### ADE-bench integration (`~/repos/ade-bench`)

`LabratLocalAgent` (in the ade-bench repo at `ade_bench/agents/installed_agents/labrat_local/`) extends `BaseAgent`, runs `claude` locally via `subprocess` (Mac OAuth), and bridges into Docker via `docker exec`/`docker cp`. It pins `model_name="claude-sonnet-4-6"` (passes `--model` explicitly so it doesn't fall through to Opus and burn Max budget ~5x faster). LabRat-side: `src/labrat/eval/benchmarks/ade_bench/` (`suite.py` / `external_runner.py` shells `uv run ade run` + parses `results.json` / `reporter.py`).

```bash
uv run python scripts/eval_ade_bench.py --tasks <task_id> --n-attempts 3
cd ~/repos/ade-bench && uv run ade run <task_ids> --db duckdb --project-type dbt --agent labrat_local --no-diffs --n-attempts 3
uv run scripts/analyze_ade_failures.py ~/repos/ade-bench/experiments/<run_id>/   # analyse failures
```

Current score (claude-sonnet-4-6): **80% overall** (48/60) — 100% easy · 80% medium · 60% hard. Roadmap + 12 remaining failures + root causes: `docs/ade_bench_failure_analysis.md`.

### DAB integration (`src/labrat/eval/benchmarks/dab/`)

[DataAgentBench](https://ucbepic.github.io/DataAgentBench/) — 12 datasets / 54 queries / 4 DBMSes (DuckDB, SQLite, Postgres, Mongo). **LabRat is #8 of 21 on the leaderboard at stratified Pass@1 60.88%** (current entry "Claude Sonnet 4.6 + Cartographer", PR #65, claude-mcp + `--agent-cartograph` + `--hints`, pass@5, 2026-06-24) — the only top-10 entry on a single mid-tier model. The **prior** entry (Sonnet, no grounding) is still on the board at 51.38% / #13. **Cite 60.88% as current; never 58.0% (contaminated) or 50.5% (interim recompute).** DAB scores are versioned against the **current upstream ground truth** — the leaderboard re-scores all rows when GTs change (patents was globally-broken / 0% for every team until upstream PR #59 fixed it; syncing the checkout + re-running patents lifted us 54.34%→60.88%).

Three drivers via `--driver`: `raw-bash` (baseline), `labrat-agent` (`AgentLoop` + tools, any provider incl. `--agent-provider codex` for GPT‑5.5/5.6 — now a viable full-benchmark path via per-dataset sharding, `scripts/dab_shards.py`), `claude-mcp` (Max-plan full-benchmark path).

**GPT-5.6 campaign (2026-07-10→16, branch `feat/codex-caching-gpt56`):** a full 270-trial labrat-agent/codex run on `gpt-5.6-luna` @ max with Cartographer+levers+hints+ledger scored **206/270 = 74.18% stratified** (+13.29pp over the live PR #65 entry) — independently audited (zero P0/P1, byte-identical package rebuild, traces clean; report: `docs/claude-fable-gpt56-dab-audit-report.md`). Package: `runs/dab/submission-gpt56-luna-max-ledger-final-270` (submission-ready; PR disclosures listed in the audit report §10). Sharded runs assemble via `dab_shards.py merge|recover`; `scripts/build_dab_trace_bundle.py` produces the scrubbed upstream bundle. Note: on GPT-5.x, Cartographer-alone *regresses* and levers are neutral (Sonnet-favoring levers); hints + ledger carried the lift. **Two invariants that must not regress:** (1) **always `--datasets <12 official>`** — the suite enumerates 104 local queries incl. 5 unofficial extras; an unfiltered run pollutes the aggregate; (2) the **claude-mcp sandbox gate** (MCP-only `--allowedTools`, isolated `cwd`, `_detect_contamination` backstop) — it closes the Phase 5 answer-key-leak path by construction.

`--agent-cartograph` (off by default): runs the deterministic Cartographer pre-pass before each trial (both `labrat-agent` and `claude-mcp` drivers); hermetic HOME; GT-firewalled by construction; **deterministic-only on DAB** (`with_semantics=False` — no LLM authoring; structure-only Scent so nothing answer-shaped). The `labrat-agent` path writes per-call tool traces to `agent_tool_calls.jsonl` (schema-identical to `claude-mcp`'s `mcp_tool_calls.jsonl`, shared `append_tool_trace` writer); a submission is trace-valid on either provider. Feature-by-feature parity matrix: **`docs/dab-driver-parity.md`**. Per-trial wall-clock timeout via `asyncio.wait_for` → `infra:timeout` on expiry.

`--hints` (declared `Hints: Yes`): **appends** the benchmark's `db_description_withhint.txt` to the base description (`base + "\n\n" + hints`, matching DAB's `run_agent.py`) — it is hints-only data-quirk guidance, so loading it *instead of* the base would drop the schema. Benchmark-provided + a declared leaderboard axis (not contamination). **Score levers, all ablated net-positive ~+8pp each and shipped:** the Cartographer pre-pass, `_dab_lever_lines` (force-query / repair-via-error_category / push-aggregation), and `--hints`. `_build_claude_mcp_prompt` is the extracted claude-mcp opening-prompt builder (emit via `scripts/dump_dab_prompts.py` for prompt-leakage audits). `_INFRA_PATTERNS` classifies API 5xx/429/overloaded as `infra:` (auto-retry outages, don't miscount as semantic fails).

Full reference (drivers, env.py/suite.py internals, sandbox-gate detail, scoring math, resume safety, codex/GPT‑5.x, and all DAB run gotchas): **`docs/dab-integration.md`**. Results/history/conclusions: **`docs/dab-progress-report.md`** + **`docs/dab-solultra-ablation.md`** (GPT-5.6 campaign). Memory: `project_gpt56_dab_campaign`, `project_dab_phase5_submission`, `project_dab_contamination`.

### Smoke regression (`scripts/run_smoke_regression.py`)

Fixed 9-task ADE subset (`src/labrat/eval/smoke.py::ADE_SMOKE_TASK_IDS`, frozen). Run at every DAB phase boundary:

```bash
uv run python scripts/run_smoke_regression.py capture --n-runs 3 --n-attempts 3   # one-time baseline
uv run python scripts/run_smoke_regression.py check --n-attempts 3                # check vs baseline (exit 1 on hard fail)
```

Baseline at `tests/baselines/ade_smoke_baseline.json`. Capture aborts with `InfraFailureError` if any trial returns `reason.startswith("infra:")` — prevents budget-exhaustion runs from corrupting the baseline with zero-time fake failures.

## Gotchas

**Plain `python3` doesn't see project deps** — use `uv run python3 -c '...'` even for one-off inline inspection. The system `python3` has no duckdb / polars / mcp / etc.

**`DuckDBConnection.execute()` is SELECT-only** — it goes through `pl.read_database`, which expects a result set. For DDL/DML on DuckDB (ATTACH, CREATE, INSERT, …) call `self._connection.execute(sql)` directly, as `DuckDBConnection.attach()` does in `src/labrat/db/duckdb_engine.py`.

**Long-running `uv run` piped to `tail`/`grep` block-buffers stdout** — output won't appear until the process exits. For live progress, drop the pipe, wrap with `stdbuf -oL`, or run via `run_in_background` and read the output file. (Background watchdog launches need `run_in_background`, not a bare `&` — a bare `&` dies when the tool's shell exits.)

**One-off `claude --print` needs `env -u ANTHROPIC_API_KEY -u CLAUDECODE`** — if `ANTHROPIC_API_KEY` is in the shell, the CLI uses it (metered API) instead of Max-plan OAuth, and a credit-less account returns "Credit balance is too low". The `_invoke_agent` / `_run_trial_claude_mcp` paths strip this automatically; interactive spikes need to do it themselves.

**MCP server: use low-level `mcp.server.Server`, not FastMCP** — FastMCP's `@mcp.tool()` decorator infers schemas from function signatures, which doesn't fit a runtime `ToolRegistry` of arbitrary tools. Register via `@server.list_tools()` + `@server.call_tool()` and feed `tool.anthropic_schema()` — see `src/labrat/mcp/server.py`.

**HTML tour files** — `docs/index.html` and `labrat_tour.html` are 2.2MB and exceed the Read tool's token limit. Use `grep`/`sed` for inspection; spawn a subagent for edits. The two files are always byte-identical — every edit must be applied to both.

**ADE-bench `task.yaml`** — difficulty field is `difficulty` (not `tier`); variant db field is `db_type` (not `db`). ADE experiment results live in `results.json` (top-level `{"results": [...]}`), not `results_metadata.jsonl`.

**`_DOCKER_PREAMBLE` (ADE agent) is a Python format string** — called with `.format(...)`, so any literal `{` must be `{{`; dbt Jinja `{{ ref('x') }}` becomes `{{{{ ref('x') }}}}` in source. Same for `_FAMILY_HINTS` values, which inject by `task_name.startswith(prefix)` (rules on `analytics_engineering` never fire for `asana` — verify the family prefix).

**decisions.md** is the living design log — add a dated entry for every significant architectural decision. **TESTING.md** is the manual TUI testing guide (uses `tests/fixtures/sample_dbs/ecommerce.duckdb`) — consult it before manual UI testing.

## Before every commit

Run in this order — CI enforces all three:

```bash
uv run ruff format .   # must run first; fixes formatting in-place
uv run ruff check .    # must be clean
uv run pyright         # must be clean
uv run pytest -q       # must pass
```

`ruff format` must come before `ruff check` — format violations are check failures too.

## Key conventions

- Pyright strict applies to all of `src/labrat/` except `dspy_opt/` and `screens/`.
- `Connection` adapter names: use `duckdb_engine.py` (not `duckdb.py`) to avoid shadowing the library.
- Profile credentials live in the OS keyring via `keyring` — never logged or printed.
- `QueryEvent` never stores result rows (security decision).
- `asyncio_mode = "auto"` — no decorator needed on async tests.
- Tool `name`, `description`, and `input_model` must be `@property` methods, not class attributes.
- `json.loads()` results are `Unknown` under pyright strict — use `# type: ignore[arg-type]` on the specific access, or `cast(dict[str, Any], x)` after an `isinstance(x, dict)` narrowing (see `codex_subscription.py` / `suite.py`).
