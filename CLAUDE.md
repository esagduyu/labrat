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
| `maze/` | Scent grounding layer: `cartographer.py` (Cartographer pre-pass, `generate_scent`, `cartograph_prepass`); `search_reference_docs` tool retrieves from the store (#26a/#26b SHIPPED). **M5 moat foundation + T2b:** `provenance.py` (`SOURCE_TIERS` trust ladder + `source_rank`/`best_source`); `document.py` `Section` optional freshness metadata (`generated_at`/`schema_hash`/`model_id`/`git_sha`, back-compat `**Meta:**` line); `harvest.py` (`cluster_corrections` + `draft_harvested_sections` → `harvested`-tagged Gotchas, contamination-audited **fail-loud, draft-only**; `apply_approved_sections` audits+merges before write); `store.py` **write path** (`write_doc`/`load_domain` — was read-only); `staleness.py` (`schema_fingerprint`/`is_stale`). **dbt-CI pairing (2026-07-10, `5b99444`/`3a637d7`):** `ci.py` (`check_scent_freshness`/`catalog_from_dbt`) backs the `labrat scent check|ingest|init-ci` CLI group — read-only dbt↔Scent staleness gate for CI (guide: `docs/dbt-ci-pairing.md`). **Map (2026-07-10, `1b64953` — renames the never-built "Warren"):** `map.py` — `kind="map"` per-domain bundles of *pointers* to Scent/Trail docs + suggested prompts (soft-miss `resolve_members`; `draft_maps_from_dbt` auto-seeds skeletons from dbt marts structure); activation via `ToolContext.active_maps` is an **additive retrieval filter** on `search_reference_docs`/`search_trails` (unset ⇒ behavior unchanged; no eval/MCP path sets it; adds no agent tool — count stays 27); TUI author/curate/activate in `screens/maps.py` (ctrl+shift+p). **Hybrid RRF retrieval (T2b v2, 2026-07-16, `feat/hybrid-rrf-retrieval`):** `embedding.py` (optional local model2vec embedder behind the `labrat[semantic]` extra, fail-open) + `hybrid.py` (RRF fusion; `hybrid_section_keys` orchestrator) upgrade `search_reference_docs`/`search_trails` to lexical+semantic behind `ToolContext.hybrid_retrieval`/`Profile.hybrid_retrieval` — default OFF, benchmark path byte-identical (tested), sidecar cache `~/.labrat/maze/<profile>/<kind>/.embeddings.jsonl`. Every Scent write runs through `scent_audit.py` |
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

[DataAgentBench](https://ucbepic.github.io/DataAgentBench/) — 12 datasets / 54 queries / 4 DBMSes (DuckDB, SQLite, Postgres, Mongo). **LabRat is on the leaderboard at stratified Pass@1 74.18%** (accepted entry "GPT-5.6-Luna-Max + Cartographer", PR #72, 2026-07-16, labrat-agent driver + codex provider + Cartographer/levers/hints/ledger). Rank drifts as the board grows (was #5 of 27 at acceptance; #6 after Sentinel/Actioneer's 84.50% displaced the top) — the **durable claim is #2 among UNTUNED-prompt entries** (0.15pp behind Spacedock; every entry above us carries the board's "Tuned prompt ✓" mark). Prior entries stay on the board: Sonnet 4.6 + Cartographer 60.88% (PR #65), first Sonnet 51.38%. **Citation discipline: 74.18% is the accepted number; 0.8102 is SUBMITTED-PENDING (PR #84) — do not claim its rank until accepted; never 58.0% (contaminated) or 50.5% (interim recompute).** DAB scores are versioned against **current upstream ground truth** (the board re-scores all rows when GTs change). Screenshot: `docs/images/dab-leaderboard-2026-07-16.png`.

**First Opus submission — PR #84 SUBMITTED 2026-08-04 (pending review): 0.810150 stratified, "Claude Opus 5 + Cartographer + informed packs".** Run 2026-08-03 at commit `7ca04ed` (this branch), claude-mcp + cartograph/hints/levers/local-embed/mcp-ledger/tool-prompt/answer-gate + **all four informed packs v2.2** — the first taint-CLEAN Opus run (270 verdicts clean; the unsubmitted packs-off 0.750748 archive run had 2 agnews withdrawals). 268/270 semantic (2 agnews:4 `infra:timeout` disclosed in the PR, scored both ways: 0.8102 excl / 0.8018 as-fails; re-runs of those trials hang the CLI — a subprocess-cleanup deadlock, timeout fires but cleanup blocks on the wedged child; kill -9 required; UNFIXED). Vs the packs-off archive: six datasets up (stockindex +0.40, github +0.10, pancancer +0.067, googlelocal +0.05, agnews +0.05, crmarenapro +0.046), zero regressions. **Packs v2.2 story:** v1 (one-pack-per-arm Sonnet ablation: underpowered null) was a wash on Opus (+1pp; stockindex +6 trials cancelled by googlelocal −6 delivery-format drift); three derived fixes made the difference — adjacency non-interposition, stored-structure completeness, and **first-mention value delivery** (derived by reading the validator: graders anchor to an item's FIRST mention + a bounded window; a later restatement can never repair a bare early mention). Every pack line passes the token contamination gate + a reverse GT-value audit. Submission package + trace bundle: `labrat-wt-packsv2/runs/dab/submission-opus5-packsv2-270/` (hand-merged: every canonical merge check enforced except completeness, waived for the 2 disclosed trials — see config `assembly_note`). Memory: `project_dab_opus_packs_submission`.

**Existing-tool fixes SHIPPED 2026-07-23** (from the 270-trial Terra-vs-Luna answer analysis, `docs/terra-luna-synthesis-2026-07-20.md`; Fable-reviewed + Sonnet-5-smoke-validated): (1) **column-disambiguation grounding** — deterministic code/name-pair + value-based hierarchy hints in `link_schema`/`describe_table` output (fires only on genuine ambiguity, zero hints on normal tables); (2) **local-NLI `llm_classify` backend wired through claude-mcp** (`--llm-classify-backend local-embed`; needs `uv sync --extra semantic`; `HF_HOME` pinned so the hermetic HOME doesn't re-download per trial); (3) **three new untuned `_dab_lever_lines`** (enumeration-completeness, tie-band sanity, adjacent-token delivery); (4) **harness resume-dedup** (a re-run's semantic row replaces the prior `infra:` row). The taxonomy lever (`--agent-taxonomy`) exists but is **default-off / net-negative** — never on a submission; its good micro-rules were extracted into the levers instead.

**Catalog + ledger fixes SHIPPED (2026-07-24, merged `feat/dab-claude-mcp-catalog-gaps`):** three claude-mcp gaps closed (all 12 DAB datasets are 2-DB federated, so these were systemic — `describe_table` was 6 ok/27 err before). (1) `attach_database` now introspects an ATTACHed secondary's schema into `ctx.catalogs[alias]` (`DuckDBConnection.introspect_attached_catalog`, system-schema-filtered) + registers `ctx.connections[alias]`; `describe_table` resolves the dotted `alias.table` form — so the four catalog tools + the disambiguation hint work on attached DBs. `column_stats`/`sample_rows` still need the dotted form (bare name + `database=alias` errors with a self-healing DuckDB "did you mean" hint). (2) A dataset's **second DuckDB** now attaches via `TYPE DUCKDB` instead of being dropped (`env.py` emits it as an `AttachSpec(db_type="duckdb")`; both drivers). (3) An **opt-in server-side Context Ledger** via `--agent-mcp-ledger` (`LABRAT_MCP_LEDGER=1` + `LABRAT_MCP_RESULT_STORE_DIR` + `LABRAT_MCP_LEDGER_MAX_BYTES`, which the DAB path sets to **64000** — 8 KB truncates `search_reference_docs` grounding, so the budget is raised to bound only monster `run_sql` dumps), plus a `get_artifact` retrieval tool. **Every OFF path byte-identical.** Memory `project_dab_claude_mcp_feature_gaps`.

**High-effort Sonnet-5 full run (2026-07-24, complete, `runs/dab/full12-sonnet5-high-fixes-shards`):** claude-sonnet-5 @ `--agent-reasoning high` + cartograph+hints+levers+local-embed+mcp-ledger + the catalog fixes scored **70.31% stratified (197/268, 2 infra excluded)** — BELOW Luna's 74.18% and the earlier medium-effort 72.90%; **more effort did not help** (Sonnet runs 12-call/54s trials vs Luna's 36-call/304s — it shortcuts). **Keep Luna as the leaderboard entry.** Task-by-task vs Luna: **`docs/dab-sonnet5-vs-luna-gap-analysis.md`** (7 Luna-win / 4 Sonnet-win / 34 both-pass / 8 both-fail; the whole gap is 7 tasks in 3 themes). GAP-2 win confirmed (crmarenapro:1 unreachable→5/5); GAP-1 restored grounding but pancancer:1 is still 0/5 for BOTH models (shared-hard, not a gap). GT was stable across both runs (last changed 2026-06-12), so the comparison is clean.

**Gap levers — MERGED to master (`235f52e`, 2026-08-02, via the `feat/dab-opus-full` merge):** 2 surviving `_dab_lever_lines` (free-text match/parse completeness, byte-verbatim value delivery); the convention-pinning lever was **dropped on-branch for cause** (never moved its only target patents:2; both saturated-task regressions in the dilution run were convention-flavoured — see the `_dab_lever_lines` docstring). Safety verified 2026-08-01: all 1069 DAB GT/validator values checked against the emitted lever text (planted-failure self-test first) — zero held-out tokens. Effect size never isolated by a full-270 A/B; they were live in the archived best-ever Opus 0.7507 run and showed no dilution (§(k) of `docs/dab-sonnet5-vs-luna-gap-analysis.md`: non-derived tasks +0.002, per-trial dilution >6.7% excluded at 95%). The same merge brought `--agent-answer-gate`, `--agent-mcp-tool-prompt`, and the per-row-fsync trials.jsonl durability fix (C1) onto master. **CONTAMINATION LESSON:** a lever-draft example (`5–11PM`) was a literal held-out GT token; **always grep any lever example against `~/repos/DataAgentBench` GT before shipping.** xhigh runner now tracked at `scripts/run_full12_sonnet5_xhigh.sh` (`--agent-timeout 1800`), unlaunched. Memory `project_dab_gap_levers`.

Three drivers via `--driver`: `raw-bash` (baseline), `labrat-agent` (`AgentLoop` + tools, any provider incl. `--agent-provider codex` for GPT‑5.5/5.6 — viable full-benchmark path via per-dataset sharding, `scripts/dab_shards.py`), `claude-mcp` (Max-plan OAuth full-benchmark path). **Sonnet on DAB ALWAYS runs claude-mcp, never the metered API** (model `claude-sonnet-5`; strip `ANTHROPIC_API_KEY`/`CLAUDECODE` or the CLI bills metered) — that path historically carried no Context Ledger, but the GAP-3 fix (2026-07-24, merged) adds an opt-in server-side ledger via `--agent-mcp-ledger`; it also carries cartograph+hints+levers+local-embed-classify + the catalog fixes; memory `reference_dab_sonnet_claude_mcp`. (The catalog+ledger fixes and the completed 70.31% high-effort run are detailed above.)

**GPT-5.6 campaign (2026-07-10→16, branch `feat/codex-caching-gpt56`):** a full 270-trial labrat-agent/codex run on `gpt-5.6-luna` @ max with Cartographer+levers+hints+ledger scored **206/270 = 74.18% stratified** (+13.29pp over the live PR #65 entry) — independently audited (zero P0/P1, byte-identical package rebuild, traces clean; report: `docs/claude-fable-gpt56-dab-audit-report.md`). Package: `runs/dab/submission-gpt56-luna-max-ledger-final-270` (**accepted onto the leaderboard as-is via PR #72, 2026-07-16 — no adjustments, no footnote**). Sharded runs assemble via `dab_shards.py merge|recover`; `scripts/build_dab_trace_bundle.py` produces the scrubbed upstream bundle. Note: on GPT-5.x, Cartographer-alone *regresses* and levers are neutral (Sonnet-favoring levers); hints + ledger carried the lift. **Two invariants that must not regress:** (1) **always `--datasets <12 official>`** — the suite enumerates 104 local queries incl. 5 unofficial extras; an unfiltered run pollutes the aggregate; (2) the **claude-mcp sandbox gate** (MCP-only `--allowedTools`, isolated `cwd`, `_detect_contamination` backstop) — it closes the Phase 5 answer-key-leak path by construction.

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

**Monitors over append-only logs must track a high-water mark** (offset/mtime) or they re-emit old failures as new alerts on every poll. And `pgrep -f <script>` self-matches — the watching shell's own command line contains the pattern, so a liveness check written that way can never fire; match the actual PID instead. Prove a monitor can fire before trusting its silence (§Build process rule 4).

**OPEN BUG — per-trial timeout cleanup can deadlock (both drivers, 2026-08-04).** After `asyncio.wait_for` fires the 1800s trial timeout, cleanup can block forever awaiting a wedged child (claude CLI subprocess on claude-mcp; HTTP close on labrat-agent/codex) — the eval sits at 0% CPU indefinitely, no infra row is appended, and only `kill -9` of the process tree recovers. Observed 3× on Opus agnews:4 re-runs and once on the Luna A/B agnews shard (provider-independent ⇒ the bug is in our cancellation path around `run_trial`, not the providers). Until fixed: put a watchdog on long shards (row-count high-water mark, NOT process liveness) and expect agnews to need manual kills. Fix = shield cleanup with its own short timeout and hard-kill the child process group.

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

## Build process (enforced — derived from the 2026-07-31 and 2026-08-01 post-mortems)

These are not preferences. One build under the full subagent process still produced ~14 fix
rounds, two invalidated ablation results, and a corrupted baseline cited for days. The
common mechanism: verification that is **local** (checks the symptom, not the invariant) and
**credulous** (accepts an assertion, a green check, or an agent's report as proof). Every
rule below is checkable from artifacts — if you cannot point to the artifact showing a rule
was followed, it was not followed.

**1. The whole-branch review runs on Fable — and its report must prove it.**
Per-task reviews run on Sonnet (deliberate — they are transcription checks). The
whole-branch review is dispatched with `model: "fable"` — never as a `fork` (forks silently
ignore model overrides) — and the review's report must state, in its first line, the model
it actually ran on. A report without that line is invalid: re-dispatch, don't act on it.
This rule lapsed silently once (Opus, 2026-07-31) precisely because nothing surfaced the
model; the self-report line is the surface.

**2. A brief never contains the deliverable.**
For content-shaped artifacts — rule text, corpora, regexes, thresholds, prompt lines — the
brief states the *derivation procedure* and a *failing acceptance test*; the implementer
derives the content and you check it against the test. A brief that carries the finished
content ships the orchestrator's errors by construction (a brief-embedded regex's 5-char
floor blinded the contamination gate to real 4-char GT values). Exact values belong in a
brief only when they are genuine inputs: a model id, a path, a flag name.

**3. A correction to a set-valued artifact re-verifies the whole set — and shows its work.**
Fix the invariant, not the instance; then re-check every member and enumerate what was
re-checked in the commit message or review note. A one-line fix to a rule list, corpus, or
guard pattern with no re-check enumeration is rejected at review. (5 of 14 fix rounds were
caused by the previous fix — each locally right, globally incomplete. A later class-level
audit demanded under this rule found 2 further live fail-open paths beyond the one reported.)

**4. No green without a red — a check is evidence only after you have watched it fail.**
Applies to every checker: contamination gates, audit gates, smoke asserts, background
monitors, reviews. Three parts, all required, before the check's pass means anything:
- **Minimum shape, every population.** Plant a failing case of the *smallest* shape the
  check claims to catch, in *each* population it claims to cover; watch it caught; remove
  it. (A 6-char plant blessed a gate with a 5-char floor; a coverage helper iterated the
  wrong dict and silently exempted an entire pack.)
- **The plant fails for the reason under test.** A probe the old gate already caught proves
  nothing about the new gate. A plant that still passes when its fix is reverted is not a
  plant — revert and confirm.
- **Empty input fails loud.** A gate given nothing must block (`gate({})` blessed an
  unaudited submission); a reader given an empty result file must error, never score zero;
  a monitor must be shown to fire once and its death must be observable — silence is not
  health (`pgrep -f <script>` matches the watcher's own shell and can never fire).
  Evidence gates fail closed; only liveness assists (e.g. the agent loop's sufficiency
  verifier) may fail open. A missing or falsy field is never a reason to *skip* a check.

**5. A run is citable only with recorded provenance and verified integrity.**
Checks on a run derive from the conclusion the run must license, not from the feature.
Concretely:
- Every run records its git commit and dirty-tree state in the run dir (`config.json` —
  `capture_git_provenance`). A run without them, or launched from a dirty tree, is not
  citable as a result or baseline.
- Everything that defines a run — runner scripts included — is committed before launch. An
  untracked runner is invisible to every diff-reading review by construction
  (`run_informed_ablation.sh` was `??` and carried the design error as a comment).
- Two runs are comparable only if their commits match, or the `git diff` between those
  commits over shared code paths has been read and attached to the comparison. A config
  diff plus GT-stability is not a comparability check — it certifies flags, not code ("the
  only variable is the pack" was an assertion; the baseline was 6 days of unconditional
  prompt changes older, and all four verdicts were unattributable). Use
  `scripts/check_dab_comparability.py`.
- Before reading any run's numbers — and always before citing a stored run as a baseline —
  check per-shard `trials.jsonl` row counts against the expected task count (7 of 12
  shards of a cited baseline were empty; a silent writer plus a credulous reader scores
  the survivors as the whole).

**6. Smoke tests are small, fast, and informative — and run at every phase boundary.**
Before any expensive run, run a targeted subset that exercises *every changed code path*
end to end on the real runner, not a unit test. A 3-trial shard that touches each new flag
beats a 60-trial shard that touches one. The test must distinguish infra from semantic
failure (see `scripts/run_smoke_regression.py`) and must assert on something that would
change if the code were wrong — a smoke test that passes identically with the feature off
is not a smoke test. Verify the runner's plumbing too: durable row writes, resume
resolution, the off-path golden hash.

**7. An experiment is designed before it is launched — and the design says what would change your mind.**
The brainstorming skill already covers purpose and success criteria for *features*; this rule
exists because *measurements* were never treated as things that get designed at all (the
ablation that judged a whole build was an untracked shell script). Its only non-redundant
content is quantitative. Before any run costing >1h or >50 trials, write into the runner
itself: the decision it informs, the smallest effect worth acting on, the n needed to detect
that effect at 80% power, and the result that would change the decision. **If the required n
exceeds the budget, the run cannot answer its question — redesign it or don't run it.**
- **Filter saturated units first.** Units already at 0% or 100% carry no information and
  consume budget. In the 2026-08-01 ablation, 38 of 51 tasks were saturated and identical
  across all five conditions: 75% of a ~20h Max-plan spend bought nothing, and the whole
  result turned on 8 trial-flips across 13 tasks.
- **Translate the effect into the decision's own units before spending.** That ablation's
  ceiling was computable in advance: with only 13 movable tasks, even a +0.20/trial effect
  (3× anything observed) was worth +4.8pp on the leaderboard — short of the target that
  motivated it. Sixty seconds of arithmetic would have cancelled the run.

## Key conventions

- Pyright strict applies to all of `src/labrat/` except `dspy_opt/` and `screens/`.
- `Connection` adapter names: use `duckdb_engine.py` (not `duckdb.py`) to avoid shadowing the library.
- Profile credentials live in the OS keyring via `keyring` — never logged or printed.
- `QueryEvent` never stores result rows (security decision).
- `asyncio_mode = "auto"` — no decorator needed on async tests.
- Tool `name`, `description`, and `input_model` must be `@property` methods, not class attributes.
- `json.loads()` results are `Unknown` under pyright strict — use `# type: ignore[arg-type]` on the specific access, or `cast(dict[str, Any], x)` after an `isinstance(x, dict)` narrowing (see `codex_subscription.py` / `suite.py`).
