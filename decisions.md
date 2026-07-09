# LabRat — Decisions Log

> Living design log. Add a dated entry for every significant architectural decision.
> Spider2-DBT history and M1–M32 build notes archived in `docs/spider2_decisions_archive.md`.

## Conventions

- `typing.Self` and `pathlib.Path` throughout.
- Pydantic `model_config = ConfigDict(frozen=True)` for value objects.
- `pyright` strict mode scoped to `src/labrat/` (except `dspy_opt/` and `screens/`).
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` decorators needed.
- Tool `name`, `description`, and `input_model` must be `@property` methods, not class attributes.
- `Connection` adapter files: use `duckdb_engine.py` not `duckdb.py` (avoids shadowing the library).
- `QueryEvent` never stores result rows (security decision).
- PII redaction order: SSN → email → phone (SSN first to avoid false positives).

## Trade-offs

- **2026-05-23 — Banner in Textual**: `render_banner(console)` renders to a rich Console. Textual app uses `get_banner_renderable()` (returns a Rich renderable) for Static widget — avoids ANSI-escape re-parsing.
- **2026-05-23 — Audit log format**: JSONL over SQLite — human-readable, grep-able, no schema migrations.
- **2026-05-23 — Chart rendering**: two strategies: plotext (unicode, always works) + matplotlib+kitty/sixel (rich, terminal-dependent). Image protocol detected at startup.
- **2026-05-23 — Postgres adapter**: psycopg v3 (not v2) — async-first, better types, actively maintained.
- **2026-05-23 — Warehouse adapter stubs**: all 5 non-DuckDB drivers have no type stubs. Strategy: `# type: ignore[import-untyped]` on imports, `# pyright: ignore` on call sites.

## ADE-bench integration (2026-05-24)

### LabratLocalAgent: run Claude Code locally, bridge via docker exec/cp

**Problem:** ADE-bench harness runs agents inside Docker. Claude Code authenticates via macOS Keychain OAuth (Max subscription); the API key has no credits. Keychain tokens aren't portable to Linux containers — mounting `~/.claude/` gives "Not logged in · Please run /login."

**Solution:**
- `LabratLocalAgent` extends `BaseAgent` directly (not `AbstractInstalledAgent` — no in-container install)
- `perform_task()` runs `claude --output-format stream-json --verbose -p <prompt> --allowedTools Bash` locally via `subprocess.run`
- Prompt preamble teaches Claude to use `docker exec <name> cat/bash` to read/run and `docker cp` to write files
- `session.container.name` gives the container name; harness spins Docker up before calling the agent

**Why not alternatives:**
- API key: no credits on Max subscription
- Keychain mount: macOS Keychain is process-local; tokens don't serialize to files
- CI mode (`CLAUDE_CODE_USE_BEDROCK`): requires additional IAM config, out of scope

**Tradeoff:** Ties evaluation to developer's Mac. Acceptable for baselines; future option is a headless LabRat CLI installed inside Docker with an API key.

### Baseline (2026-05-24/25, claude-sonnet-4-6, DuckDB+dbt)

| Tier | Tasks | Score | dbt tests |
|------|-------|-------|-----------|
| Easy | 15 | **93%** (14/15) | 95% |
| Medium | 30 | **~73%** | — |
| Hard | 15 | **~53%** | — |
| **Overall** | **60** | **67%** (40/60) | 83% |

Cost: ~$21.70 total. Altimate leaderboard best (altimate-code, Sonnet 4.6, Snowflake): 74.4% on 43 tasks.

**Known failures:**
- `helixops_saas009`: persistent — agent uses wrong `dbt run` model scope, 3 test tables never built
- `helixops_saas010`: flaky — 9/11 first run, 11/11 rerun
- `quickbooks003/004`, `asana005`: large/complex multi-model tasks, partial completion

Improved score (2026-05-27, after prompt iteration): **80% overall** (48/60). Roadmap: `docs/ade_bench_failure_analysis.md`.

## Unified BenchmarkSuite protocol (2026-05-28)

Added `src/labrat/eval/types.py` with `BenchmarkSuite` protocol, `BenchmarkTask`, `TrialResult`, `AggregateScore`, `BenchmarkReport`. All external benchmark integrations (DAB, ADE-bench) implement this protocol at `eval/benchmarks/<bench>/{suite,scorer,reporter}.py`. Internal evals (`bird.py`, `latency.py`, `custom_scenarios.py`) stay on legacy `EvalCase`/`EvalRunner` shape — not worth porting.

**Why:** coalesces DAB + ADE-bench + future benchmarks into one runner; enables smoke regression across all benchmarks via `SubsetSuite`.

## DAB integration: `claude --print` with Bash instead of AgentLoop (2026-05-29)

**Problem:** original plan used `AgentLoop` + `ClaudeCodeProvider` text protocol for DAB. Two failure modes discovered:
1. `claude --tools ""` hangs when model outputs `{"type":"tool_use",...}` — CLI intercepts the response and waits for a permission dialog that never arrives
2. Without `--tools ""`, model uses its own built-in Bash/Read/Edit instead of LabRat's custom protocol

**Decision:** for the DAB harness specifically, bypass `AgentLoop`/`ClaudeCodeProvider` entirely. Use `claude --print --disable-slash-commands --dangerously-skip-permissions --max-turns 15` with native Bash tool. The model runs Python+DuckDB/SQLite queries directly via subprocess. `_invoke_agent` in `suite.py` is the shim.

**Why not fix ClaudeCodeProvider:** the text protocol design is sound for TUI use (where LabRat's own tools are the value), but fighting the claude CLI's native tool handling for a benchmark harness adds no product value. The LabRat tools (`attach_database`, `list_databases`, etc.) will be built for TUI; DAB gets raw Bash.

**Tradeoff:** DAB score doesn't reflect LabRat's tool quality; it measures the model's raw query ability. Acceptable for Phase 1a/1b baselines. Phase 4 extracts a `LabRatAgentDriver` that routes DAB through actual LabRat tools.

## DAB cross-DB ATTACH preamble (2026-05-29)

**Problem:** 9 of 17 Phase 1a tasks have datasets with both DuckDB and SQLite connections. The model saw them as separate and tried to query each independently — it can't JOIN across them without ATTACH.

**Decision:** `DabSuite.run_trial` detects DuckDB+SQLite mixes in `db_config.yaml` and injects an explicit ATTACH idiom into the prompt showing exactly how to do `conn.execute("ATTACH '/path' AS name (TYPE SQLITE)")` and then join `duck_table JOIN name.sqlite_table`. This is prompt engineering, not a new tool.

**Why not a tool:** the DAB harness uses raw Bash, not LabRat tools. An `attach_database` LabRat tool will be built for the TUI product independently.

**Phase 1b result:** marginal help — deps_dev_v1:2 improved from 0% to 20% (1/5 passes); deps_dev_v1:1 still 0/5 (more complex traversal required). music_brainz_20k unchanged at 7% — root cause is the model answering from context without querying (sub-10s response times), not a federation gap. Overall Phase 1b score: 48.5% on 5 DuckDB+SQLite datasets.

## LabRat agent substrate for DAB (2026-05-30)

**Goal:** route DAB through LabRat's own `AgentLoop` + tool registry so the
Phase 4 measurement reflects LabRat's tool quality, not raw Claude + Bash.
Also make the substrate harness-agnostic (MCP) and provider-agnostic (any
`ModelProvider`) per current product direction.

**Shipped on `feat/labrat-agent-substrate`:**
- Multi-DB routing on 8 tools (`run_sql`, `explain_sql`, `sample_rows`,
  `column_stats`, `create_chart`, `list_tables`, `describe_table`,
  `search_columns`) — optional `database` param resolves
  `ctx.connections[name]` / `ctx.catalogs[name]`.
- New `attach_database` tool + `DuckDBConnection.attach()` — first-class
  cross-DB JOIN via DuckDB ATTACH (replaces the prompt-engineering preamble
  for the labrat-agent driver).
- `eval/benchmarks/dab/env.py` returns `DabTaskEnv(ctx, attachable)`. SQLite
  is no longer a phantom `:memory:` DuckDB stub; it's exposed as an
  `AttachSpec` the agent uses via `attach_database`.
- `src/labrat/agent/runner.py::run_agent_task` — in-process `AgentLoop`
  wrapper that returns `(final_text, tool_calls, latency_seconds)`. Used by
  the DAB labrat-agent driver and the `scripts/run_task.py` CLI shim.
- `DabSuite(driver=, agent_model=, agent_provider=)`. `_run_trial_raw_bash`
  preserves the Phase 1b baseline byte-for-byte; `_run_trial_labrat_agent`
  builds `DabTaskEnv`, connects DBs, registers data tools, calls
  `run_agent_task`.
- `scripts/eval_dab.py` flags: `--driver`, `--agent-model`,
  `--agent-provider`. `config.json` records all three; resume restores them
  and refuses mismatched overrides.
- `src/labrat/agent/providers/__init__.py::build_provider` factory shared by
  `run_task.py` and the DAB suite.

**ClaudeCodeProvider fragility (2026-05-30 smoke confirmation):**
- stockmarket:1 with `--agent-provider claude-code`: PASS in 11.9s, 1 tool
  call. The model used the text-protocol `{"call":...}` format correctly.
- music_brainz_20k:1 with `--agent-provider claude-code`: FAIL with
  `error_max_turns` / `stop_reason: tool_use`. The model emitted the native
  `{"type":"tool_use",...}` format instead of the text protocol; CLI couldn't
  progress.
- Conclusion: the original Phase 0 conflict isn't a hard block, it's a
  *behavioral fragility*. Simple single-step queries work; harder
  multi-step / cross-DB queries push the model toward native tool_use and
  the path breaks. ClaudeCodeProvider is not a reliable Phase 4 driver.

## LabRat MCP server + `claude-mcp` DAB driver (2026-05-30)

**Goal:** ship the proper Max-plan path for the Phase 4 measurement, plus
make LabRat's tools portable to any MCP-supporting host harness (Claude
Code, Codex, Cursor, OpenCode, …) — addressing the two follow-on
constraints surfaced by the user this session: harness-agnostic and
model-agnostic.

**Compatibility spike (Layer 2a):** built a 2-tool toy FastMCP server
(`labrat.mcp.toy` with `echo` and `now`) and ran
`claude --print --strict-mcp-config --mcp-config toy.json -p "use echo"`.
The model called the tool natively, the toy server returned the result,
the model relayed it. Round-trip confirmed under `claude --print`
non-interactive mode with Max-plan OAuth (`ANTHROPIC_API_KEY` stripped).

**Layer 2 (MCP server):** `src/labrat/mcp/server.py` mounts the data-tools
registry over MCP stdio using `mcp.server.Server` (low-level API).
`ToolContext` is constructed from `LABRAT_MCP_CONNECTIONS` (JSON env var)
+ optional `LABRAT_MCP_PRIMARY`. Each LabRat tool is exposed via the
`anthropic_schema()` it already publishes; results are serialised via
Pydantic `model_dump_json()` or `json.dumps` fallback. `DuckDBConnection`
gained a public `path` property (was private `_path`) so callers can
build mcp-configs without re-parsing `db_config.yaml`.

**Layer 2b (`claude-mcp` DAB driver):** `DabSuite` gained a third driver.
`Driver = Literal["raw-bash", "labrat-agent", "claude-mcp"]`.
`_run_trial_claude_mcp` generates a per-trial `mcp-config.json` in the
scratch dir, then shells
`claude --print --strict-mcp-config --mcp-config <file> --model <agent_model>
--permission-mode bypassPermissions --output-format json` with
`ANTHROPIC_API_KEY` / `CLAUDECODE` stripped so the CLI falls through to
Max-plan OAuth. SQLite secondaries from `DabTaskEnv.attachable` are
surfaced in the prompt; the model uses the `attach_database` MCP tool to
bring them in. `num_turns` from the CLI JSON output → `tool_calls`.

**Driver matrix and billing reality (final):**

| Driver | Billing | Reliability | Phase 4 fitness |
|---|---|---|---|
| `raw-bash` | Max | high | Phase 1b baseline (48.5%) — no LabRat tools |
| `labrat-agent` + `anthropic` | metered API | high | works; needs API credits (~$50–300 for full run) |
| `labrat-agent` + `claude-code` | Max | low (fragile) | breaks on cross-DB; do not use |
| **`claude-mcp`** | **Max** | **high** | **recommended Phase 4 path** |

**Smoke validations:**
- `stockmarket:1` via `--driver=claude-mcp` → PASS, 34.8s, **7 tool calls**
  (real multi-step LabRat tool flow), Sonnet, `cost_usd=0.0` (Max plan).
- `music_brainz_20k:1` via the same path → returned `601.44` (same wrong
  answer as Phase 1b raw-bash). The tool path is fully exercised; the
  failure is semantic, not infrastructural.

## Configurable agent caps (2026-05-30)

Added optional `max_turns` and `max_tool_calls` parameters end-to-end so
runs can be bounded per scenario. Both default `None` (unbounded).

- `AgentLoop` enforces the caps directly. When `max_tool_calls` would be
  exceeded mid-round, it dispatches up to the budget, appends the partial
  `tool_result` to history, and exits — no inconsistent state left for a
  future call.
- `run_agent_task` (the in-process wrapper used by the `labrat-agent` DAB
  driver and `scripts/run_task.py`) passes both through.
- `DabSuite` exposes `agent_max_turns` / `agent_max_tool_calls`. Behaviour
  per driver:
  - `labrat-agent`: hard cap via `AgentLoop`.
  - `claude-mcp`: `max_turns` → `claude --max-turns` (default 200 when
    `None` rather than relying on the CLI's short default).
    `max_tool_calls` is **advisory** in the prompt — the claude CLI has no
    native tool-call cap.
  - `raw-bash`: keeps the Phase 1b `max_turns=15` default for baseline
    reproducibility, but honours an explicit override.
- `scripts/eval_dab.py` and `scripts/run_task.py` both expose
  `--max-turns` / `--max-tool-calls`. DAB persists them to `config.json`
  and restores them on resume; conflicting overrides are rejected.

## Original spec vs. shipped reality

Cross-check against
`docs/superpowers/specs/2026-05-28-unified-benchmark-suite-design.md`:

| Spec'd in 2026-05-28 | Where it actually landed |
|---|---|
| Phase 4 `LabRatAgentDriver` at `src/labrat/eval/driver.py` | Shipped as `run_agent_task` at `src/labrat/agent/runner.py`. Functional equivalent; located under `agent/` because it generalises beyond eval (the TUI and the `run_task.py` shim use it too). |
| `AgentRunResult` with `usage`, `cost_usd`, `files_produced`, `tool_call_log` | Shipped as `AgentTaskResult` with `final_text`, `tool_calls`, `latency_seconds`. Usage/cost accounting deferred (the spec flagged this as an open question). |
| `BenchmarkOrchestrator` at `src/labrat/eval/orchestrator.py` | **Not extracted.** DAB's interim runner in `scripts/eval_dab.py::_run_interim` still owns concurrency / JSONL / resumability. Spec said this extraction triggers when Spider2 lands. Deferred. |
| `LabRatAgentDriver` accessible via `AgentRunResult.tool_call_log` | Not surfaced. AgentLoop exposes `history` and `tool_calls_used`; callers can introspect there. |
| One driver per benchmark | **Three** DAB drivers now exist (`raw-bash` / `labrat-agent` / `claude-mcp`). The spec assumed one; the substrate work introduced a driver axis that the harness now persists in `config.json`. |
| MCP server | Not in spec. New work, motivated by the harness-agnostic / model-agnostic constraints; lives at `src/labrat/mcp/`. |
| `--max-turns` / `--max-tool-calls` configurable | Not in spec. New work, motivated by Max-plan budget control. |

Spider2-DBT remains stubbed only, as the spec intended.

## DAB Phase 4 measurement: 54.0% (+5.5pp over Phase 1b) (2026-05-30)

Ran the full 17-query Phase 1b suite through `--driver=claude-mcp --n-trials 5` against master. Same model (claude-sonnet-4-6), same scoring (stratified mean of per-dataset means), same pass@5 methodology. Run dir: `runs/dab/dab-1780171421/`.

**Result: 54.0% overall.** vs. Phase 1b raw-Claude floor of 48.5%, a **+5.5pp lift attributable to LabRat's tool layer** (`AgentLoop` + multi-DB `ToolContext` + the 8 data tools registered for DAB, mounted via the MCP server inside `claude --print`).

**Per-dataset:**

| Dataset | Phase 1b | Phase 4 | Δ |
|---|---|---|---|
| deps_dev_v1 | 10% | 40% | **+30pp** |
| github_repos | 50% | 40% | −10pp |
| music_brainz_20k | 7% | 13% | +6pp |
| stockindex | 100% | 87% | −13pp |
| stockmarket | 76% | 88% | **+12pp** |

**Tool-call counts per trial** (the most diagnostic column):

- deps_dev_v1: **16.2** avg — agent does deep schema discovery + `attach_database` + iterative `run_sql`. Real cross-DB work.
- music_brainz_20k: **3.1** avg — the agent has the tools, the prompt surfaces the SQLite attachable, and Sonnet still hallucinates `$601.44`, `Systemisch bled`, etc. **The answer-from-context failure mode from Phase 1b is unchanged.** This is an instruction-following problem (model chooses not to query), not a tool problem.
- stockmarket / github_repos / stockindex: 9–11 tool calls — moderate exploration. On easy queries the overhead is roughly neutral; on harder queries (e.g., stockmarket:3) it pays off.

**Operational lesson — Max-plan session limit pollutes naïve aggregate.** The first pass produced 7 infra-fail trials with `"You've hit your session limit · resets 7:30pm (America/Vancouver)"` captured as the "answer" text. Validator scored them as semantic failures (False) when in fact the model never got a chance to run. Trimmed those 7 from `trials.jsonl`, waited for the limit reset, resumed — **all 7 passed on rerun**, confirming the failures were 100% budget-related, not capability-related. The 48% raw aggregate is misleading; 54% is the honest number.

**Open: harness-side detection of session-limit error text** — `_run_trial_claude_mcp` should grep the captured `final_text` for `"You've hit your session limit"` and re-raise as an infra failure that `aggregate()` skips with a warning, rather than scoring it as a regular trial failure. Until that ships, the manual trim + resume is the workaround.

**Where this leaves Phase 4 as a result:**
- The tool layer adds real, measurable value on hard cross-DB queries (deps_dev_v1 +30pp; stockmarket +12pp).
- The tool layer is roughly neutral on easy single-DB queries.
- The tool layer does *not* fix the model's mental-model failures on music_brainz_20k — the same wrong answers (`$601.44`, `Systemisch bled`) persist whether the agent has tools or not.
- 54.0% is below the DAB leaderboard top-3 (MinusX 63.1%, Altimate 60.4%, Spacedock 57.7%) but only covers 17/54 official queries. Phases 2 (PostgreSQL) and 3 (MongoDB) are required for a directly-comparable submission.

See `docs/dab-progress-report.md` for the full per-query breakdown, failure taxonomy, and Phase 5 prompt-iteration roadmap.

## DAB Phase 5: full 54-query run, submitted 58.0% → corrected 50.5% (2026-06-01)

> ⚠️ **Superseded — read the [DAB Phase 5 correction entry](#dab-phase-5-correction-contamination-found-corrected-to-505-2026-06-03) below.** The 58.0% reported here was contaminated by a harness flaw (the agent could read the benchmark's answer-key files off disk). The defensible number is **50.5%**. The per-dataset table and "validated at scale" claims in this section are left intact as the original record but must be read alongside the correction.

First directly leaderboard-comparable LabRat number on DataAgentBench. Ran all 12 official datasets (54 queries × 5 trials = 270 trials) through `--driver=claude-mcp` against master. Same model (claude-sonnet-4-6), stratified scoring, claude --print + LabRat MCP server on Max-plan OAuth. Run dir: `runs/dab/dab-1780210698/`.

**Result as submitted: 58.0%** (corrected to 50.5% — see below). The substrate shipped over the prior 48 hours (Phase 4 + Phase 2 PG + Phase 3 Mongo + item 1 infra detection) is what made this number possible — Phase 1b raw-bash couldn't have scored this because it didn't support Postgres or MongoDB and would have had its 270-trial run polluted by session-limit infra.

**Per-dataset:**

| Dataset | DB stack | Score |
|---|---|---|
| agnews | Mongo + SQLite | ~~95%~~ → **15%** (16/20 trials contaminated, withdrawn) |
| bookreview | Postgres + SQLite | ~~93%~~ → **87%** (1 trial withdrawn) |
| crmarenapro | SQLite × 3 + DuckDB × 2 + Postgres | **82%** |
| stockindex | DuckDB + SQLite | **100%** |
| stockmarket | DuckDB + SQLite | 80% |
| pancancer_atlas | Postgres + DuckDB | 67% |
| yelp | Mongo + DuckDB | ~~63%~~ → **60%** (1 trial withdrawn) |
| github_repos | DuckDB + SQLite | 50% |
| googlelocal | Postgres + SQLite | 50% |
| deps_dev_v1 | DuckDB + SQLite | 10% |
| music_brainz_20k | DuckDB + SQLite | 7% |
| patents | Postgres + SQLite | 0% |

**The headline single-dataset signal is crmarenapro at 82%** on the hardest dataset in the benchmark — 13 queries, 6 databases (SQLite × 3, DuckDB × 2, Postgres × 1). This is the cleanest evidence that the substrate work paid off; raw-bash with prompt-engineered preambles wouldn't have built up the right ATTACH topology reliably across 6 databases.

**Phase 2 (Postgres) validated at scale:** bookreview 87% (corrected), crmarenapro 82%, pancancer_atlas 67%, googlelocal 50%. The existing `attach_database` tool dispatched into DuckDB's `postgres` extension works cleanly with libpq-default OS-user auth (`host=localhost dbname=…`); no per-task auth wiring needed. (crmarenapro/pancancer_atlas/googlelocal are uncontaminated; bookreview lost one trial.)

**Phase 3 (Mongo) — partially invalidated by contamination.** The original read was "agnews 95%, yelp 63%," but agnews is now known to be contaminated (corrected to 15%) and yelp lost one trial (→60%). The Mongo *plumbing* — `load_mongo_collection` materializing a Mongo find() into a DuckDB TEMP table, nested documents as STRUCTs queried via dot notation, joined through `run_sql` — does work mechanically (the agent reached and queried the Mongo data in clean trials too). But Phase 3's *score-level* validation must wait for the sandboxed re-run; agnews cannot be cited as evidence the substrate solves semantic-classification queries.

**Item 1 (session-limit detection) was load-bearing for this run.** A 270-trial Max-plan run spans multiple session windows. The run required 4 `--output-dir` resume cycles to clear infra trials; the auto-retry-on-resume logic shipped in commit `9c46c1c` meant each resume picked up exactly the trials that had hit the limit, without manual `trials.jsonl` trimming. The reported aggregate (58.0% as submitted, since corrected to 50.5% — see the correction entry below) is computed over real-attempt trials only — infra trials are persisted but excluded from scoring.

**What this exposes:**

- **Sonnet ceiling, not substrate ceiling:** music_brainz_20k stays at 7% (same wrong answers as Phase 1b), patents stays at 0%, deps_dev_v1 stays at 10%. The tool stack doesn't fix the model's mental model on these. The natural next work is a force-query prompt rule for music_brainz, a precision-relaxation strategy for the github_repos:1 rounding-validator mismatch, and a CPC-code lookup heuristic for patents.
- **Stochasticity matters on n=5:** deps_dev_v1 was 40% in Phase 4, 10% here on the same queries with the same driver and model. github_repos:4 was 60% in Phase 4, 100% here. Pass@5 estimates have wide CIs; pass@10 would tighten dataset means meaningfully.
- **Harness ergonomics gap:** the auto-retry helps, but the harness still fast-fails the rest of the queue once a session limit hits (each subsequent trial returns in ~1.5s with the error text as final_text). A future enhancement is detecting that pattern in real time and sleeping until the documented reset time instead of blasting through and exiting.

## DAB Phase 5 correction: contamination found, corrected to 50.5% (2026-06-03)

The DAB maintainers (PR #54) asked for full per-trial traces, noting that high agnews scores are usually a data-leakage tell (e.g. the agent retrieving labels from `load_dataset("ag_news")` instead of classifying). Auditing our own saved `claude --print` transcripts confirmed leakage — and the root cause is our harness, not the benchmark.

**Root cause — the agent was never sandboxed.** The `claude-mcp` driver invoked `claude --print --strict-mcp-config --mcp-config <f> --permission-mode bypassPermissions` with **no `--allowedTools`/`--disallowedTools`**. `--strict-mcp-config` only constrains MCP configuration; the agent therefore kept the full Claude Code native toolset (Bash, WebFetch, `Task`/subagents, Read/Write) alongside the LabRat MCP server. Our DataAgentBench checkout — including every `validate.py` and `ground_truth.csv` — sat on the same filesystem and was readable. The MCP server was meant to be the sole data interface; it was one tool among many.

**What the traces show.** Two forms of contamination, both verbatim in the transcripts:
- Reading the answer key: `cat .../query_agnews/query3/validate.py`, with a subagent reporting *"The benchmark ground truth from `validate.py` is `GROUND_TRUTH = 336.6363636363636`."*
- Loading external labels: `load_dataset("fancyzhx/ag_news")` and mapping `article_id → label` — one trial states *"I solved this by mapping article_ids to categories using the HuggingFace AG News labeled dataset."*

**Scope (audited all 270 trials).** 18 accessed answer-key/validator files or external labels: **16 of 20 agnews**, one bookreview (`bookreview:3` t4), one yelp (`yelp:1` t0). The other nine datasets show none. (A Bash-only scan undercounts because `Task`-subagent work lands in tool-result text, not parent Bash calls — the audit scans full transcript text.)

**Corrected score.** Withdrawing the 18 contaminated *passes* (counting them as non-passes, leaving every other trial untouched) recomputes the stratified mean **58.0% → 50.5%**: agnews 95%→15%, bookreview 93%→87%, yelp 63%→60%, all others unchanged. Our local recompute reproduces the submitted 58.0% exactly, cross-checking the scoring.

**Disclosure & remediation.** Disclosed on PR #54 with a scrubbed 270-trial trace bundle (`runs/dab/dab-1780210698/trace_bundle/` — `manifest.json` per-trial contamination flags + `CONTAMINATION_AUDIT.md`); withdrew the 18 contaminated trials and asked maintainers to verify. **The clean replacement requires re-running with the agent sandboxed: `--allowedTools` restricted to the MCP tools only (block Bash/WebFetch/Task), the benchmark repo off the agent's filesystem, and no network egress — so answer-key and external-label access are impossible by construction.** This is now the top DAB priority, ahead of Phase 6 score-chasing.

**Process lesson.** Benchmark harnesses that shell a general-purpose coding agent must explicitly restrict the tool surface; "we only mounted the MCP server" is not a sandbox when the underlying CLI ships Bash + web by default. Also: never publish raw dev-session transcripts — a secret-scan of the build sessions turned up a live OAuth token and a gitignored private spec, so chat history must be scanned before sharing.

The next reasonable target on this benchmark is closing the gap to MinusX 63.1% — most of the remaining 5pp lives in music_brainz, patents, and the deps_dev_v1 / github_repos / googlelocal "0% on a specific validator pattern" cluster.

## DAB pre-run sandbox gate (2026-06-03)

Implemented the gate that makes the clean re-run safe (commit `6b4d3bf`), so the contamination in the previous entry is impossible by construction rather than by hope. Four parts in the `claude-mcp` driver + MCP server, all TDD'd:

1. **Tool allowlist.** The `claude --print` invocation now passes `--allowedTools mcp__labrat` + `--disallowedTools Bash,WebFetch,WebSearch,Task,Read,Write,Edit,NotebookEdit,Glob,Grep`. `bypassPermissions` alone left the entire Claude Code toolset live; `--disallowedTools` is the hard block, `--allowedTools` scopes the rest to the LabRat MCP server.
2. **Filesystem isolation.** The subprocess runs with `cwd=<trial scratch dir>`; DB paths reach the MCP server via env, so the DataAgentBench checkout (validate.py / ground_truth.csv) is never under the agent's cwd.
3. **Contamination backstop.** `_detect_contamination()` scans each trial's output for answer-key (`validate.py`, `ground_truth`) / external-dataset (`load_dataset`, `huggingface`) markers; a hit withdraws the trial as `reason="contaminated:<tag>"`, which `aggregate()` now excludes alongside `infra:`. Under the sandbox this should never fire — if it does, it flags a sandbox regression loudly instead of silently inflating the score.
4. **Audit-grade traces.** The MCP server's `_log_tool_call` writes one `{tool,input,output,ok,latency_ms}` line per dispatch to `<LABRAT_MCP_LOG_DIR>/mcp_tool_calls.jsonl`; the driver points it at the trial scratch dir. Per-tool-call traces are now first-class, not reconstructed from `~/.claude` after the fact.

**Two latent bugs the live agnews smoke caught** (neither would have surfaced without running it — the unit tests used absolute `tmp_path` and an in-process context):
1. **Relative `--mcp-config` path doubling.** Setting `cwd` for isolation made the CLI re-resolve a *relative* config path against the new cwd (`Invalid MCP configuration: … not found`); the first real agnews trial failed instantly with `infra:agent_error`. Fix: `scratch_dir = scratch_dir.resolve()` up front + a regression test with a *relative* scratch dir.
2. **`:memory:` federation server crash.** `_build_context_from_env` defaulted `read_only=True`, but DuckDB can't open `:memory:` read-only — so for federation datasets (agnews, yelp; no file-backed DuckDB primary) the MCP server **crashed on startup**. This was *masked in Phase 5* because the agent used Bash and never needed the MCP server; the sandbox (Bash blocked) exposed it. Symptom: agnews:1 "passed" with a dead server (memorized answer), agnews:2 timed out flailing. Fix: force `read_only=False` for `:memory:` (it's the agent's writable workspace — it ATTACHes / loads Mongo into it) + a regression test.

**Validation (2026-06-03, agnews:1 + agnews:2, sandboxed):** gate holds. agnews:1 PASSes via genuine classification — 31 MCP calls (`load_mongo_collection`, `attach_database`, `run_sql`), zero Bash/file/HF access; the `mcp_tool_calls.jsonl` audit log confirms MCP-only. agnews:2 honestly times out on the genuinely hard 111-article classification (17 legit MCP calls) — the real difficulty, not a leakage-inflated pass. Contamination detector clean on both. The leakage-era agnews 95% vs. this is the honest signal. Lesson: harness code that changes `cwd` or re-creates connections from a spec must be tested with the inputs production actually passes (relative paths, `:memory:`).

Network egress isolation (container / `unshare -n`) stays an environment step — not portable from Python on macOS, and moot once Bash/WebFetch are blocked. Open tuning note: the 600s per-trial timeout may be tight for legitimately hard classification queries (agnews:2); consider raising `--agent-timeout` for the full re-run and accepting some honest timeouts.

## Pillar 1: profiling, file ingest, verifier loop, prescriptive prompt (2026-06-01)

Work on `feat/rat-maze-pillar1`. Six technical changes to the agent substrate:

1. **`profile_dataset` tool** (`src/labrat/agent/tools/profile_dataset.py`) — one-call dataset profiler returning, per table, columns+types, row count, declared foreign keys, and a few sample rows. Size-budgeted via `max_tables` with explicit truncation flagging. Reads structure from the introspected catalog (`ctx.catalogs[db]`), samples live rows from the connection, and has a `COUNT(*)` fallback because DuckDB introspection leaves `Table.row_count` `None`. Registered first in `build_data_tools_registry()`.

2. **`load_file` tool** (`src/labrat/agent/tools/load_file.py`) — loads a CSV/TSV/JSON/Parquet file into the DuckDB session as a TEMP table; works even against a read-only primary (like `load_mongo_collection`). DuckDB-only, guarded by `isinstance(conn, DuckDBConnection)` like `attach_database`. Backed by a new `DuckDBConnection.load_file()` (`src/labrat/db/duckdb_engine.py`) that runs the `CREATE OR REPLACE TEMP TABLE ... AS SELECT * FROM read_csv_auto/read_json_auto/read_parquet(...)` DDL directly, because `DuckDBConnection.execute()` is SELECT-only. Registered in `build_data_tools_registry()`.

3. **Tool count** — `build_data_tools_registry()` now registers 11 (added `profile_dataset`, `load_file`); the TUI registers 5 more, for 16 total.

4. **Verifier loop (opt-in)** (`src/labrat/agent/verifier.py`) — `Verdict`, the `Verifier` protocol, `LLMVerifier`, `parse_verdict`, `provider_llm_fn`. `AgentLoop` gained an opt-in `verifier` + `max_verify_rounds` (default 2) + a `verify_rounds_used` counter + an `on_status` callback on `run()`. At the would-be-final turn (no tool calls) the verifier judges sufficiency; if insufficient, the feedback is injected as a new user turn and the loop continues, bounded by the round cap AND the remaining turn budget. **Fail-open** — an unparseable verdict counts as sufficient, so the verifier can never trap the loop. Status goes to `on_status`, deliberately separate from `on_text` so it never corrupts `final_text`. `run_agent_task` gained `verify=False` (default off) + `max_verify_rounds`. Mirrors the `validations.ValidationChecker` LLM-judge pattern (one constrained call → `"sufficient"` / `"insufficient: <feedback>"`); `provider_llm_fn` reuses the loop's own provider as the judge.

5. **DAB `labrat-agent` empty-catalog fix** (`src/labrat/eval/benchmarks/dab/env.py`) — `build_dab_task_env` builds `Catalog(schemas=[])` because connections aren't `connect()`-ed at build time, so the catalog-backed tools (`list_tables` / `describe_table` / `column_stats` / `search_columns` / `profile_dataset`) were dead under the `labrat-agent` driver. New `introspect_env_catalogs(ctx)` populates the catalogs post-connect; `_run_trial_labrat_agent` (`suite.py`) calls it after connecting. The `claude-mcp` driver was unaffected (it introspects via the MCP server).

6. **Prescriptive system prompt** (`src/labrat/agent/prompts/system_base.md`) — rewritten from exploratory to prescriptive: profile first (`profile_dataset`) → numbered plan → step-by-step execution → verify the answer addresses the question before finishing. Tool-usage section now lists `profile_dataset` and `load_file`.

## DAB resilience, verifier wiring, SQL governance, prompt refresh (2026-06-01)

Follow-ups to the Pillar 1 sprint (on `master` after merges `3ff4d10`, `6a78914`):

1. **`run_sql` single-statement guard** (`src/labrat/agent/tools/run_sql.py`) — `run_sql` already did AST-based DDL/DML refusal + auto-limit via sqlglot, but `_is_mutation`/`_has_limit` use `parse_one`, which only sees the *first* statement, so `SELECT 1; DROP TABLE t` slipped through (and got a `LIMIT` appended after the `DROP`). New `_statement_count` (`sqlglot.parse`, fail-open on ParseError) refuses any input with >1 top-level statement. **Not** force-bypassable — a single-statement contract distinct from the mutation force-override.

2. **`--agent-verify` wired into the DAB labrat-agent driver** — `DabSuite(agent_verify=…)` → `run_agent_task(verify=…)`, plus a `--agent-verify` CLI flag with the same resume-safety as the other `agent_*` options. Default off (an extra LLM call per would-be-final answer — latency + usage cost). No effect under raw-bash / claude-mcp (their loops live outside `AgentLoop`).

3. **Per-trial exception isolation** (`DabSuite.run_trial`) — the driver dispatch is wrapped in try/except; a provider/agent exception is recorded as `reason="infra:timeout"` (TimeoutError) or `"infra:agent_error"` instead of crashing the whole run. Reuses the existing `aggregate()`-skip + resume auto-retry. Surfaced by a claude-code partial read whose first-query `claude --print` 120 s timeout took down the entire eval.

4. **Configurable claude-code timeout** — `build_provider` gains an optional `timeout` (claude-code only; the others manage their own HTTP timeouts), threaded via `DabSuite(agent_timeout=…)` and a `--agent-timeout` flag (resume-safe). Absorbs slow turns when the verifier adds round-trips.

5. **DAB labrat-agent prompt refresh** (`_build_labrat_agent_system_prompt`) — now surfaces `profile_dataset` (call first) + `load_file` and the profile→plan→verify discipline, keeping the DAB `single plain answer on the last line` scoring contract, so a labrat-agent run actually exercises the new Pillar 1 grounding/tools.

`config.json` now records **seven** agent fields (added `agent_verify`, `agent_timeout`); all restored on `--output-dir` resume with conflicting overrides rejected. 542 tests pass, pyright/ruff clean.

**Measurement note:** the verifier only runs on the labrat-agent driver, which needs a metered `ANTHROPIC_API_KEY` (not set) for a clean run; claude-mcp (Max-plan) bypasses it. A claude-code partial read is the fallback but is fragile under the verifier's extra round-trips — per-trial isolation + a larger `--agent-timeout` make it *survivable* but not reliable.

## DAB grounding tools + clean sandboxed re-run (2026-06-04, in progress)

Pre-kickoff work for the clean re-run, then the run itself.

**Grounding tools (FEATURE_ROADMAP #25), shipped + TDD'd:**
- `link_schema` (`tools/link_schema.py`) — NL question → ranked relevant tables via lexical stem-overlap over the catalog (columns + matched terms). Narrows wide schemas before SQL. Pure/deterministic.
- `verify_join` (`tools/verify_join.py`) — COUNT-probe a join before trusting it: match rate (wrong-key detection) + max right-rows-per-key (fan-out / double-count detection) + a plain verdict.
- Both registered in `build_data_tools_registry()` (reach labrat-agent + the claude-mcp MCP path) and surfaced in both driver prompts.
- claude-mcp per-trial timeout raised 600s → **1200s** (default; `--agent-timeout` now overrides it for claude-mcp too) — hard classification queries (agnews) need the headroom.

**Self-healing local runner (`scripts/dab_rerun_tick.sh` + `dab_rerun_loop.sh`):**
- The run must execute *locally* (Max-plan OAuth + mongod + local DAB checkout). First tried a Claude Code **routine on the bridge environment** (runs on the Mac); it worked initially but a **power outage destroyed the bridge env** (`environment_not_found` on the next fire) — bridges don't survive a reboot. Switched to the local loop, which is reboot-fragile too but matches the "small bash script" ask; launchd (reboot-durable) was declined as a persistence escalation.
- Loop tick: probe Max-plan (skip cleanly if the limit is active — avoids blasting fast-fail trials), else start/resume `eval_dab.py --output-dir runs/dab/dab-rerun-clean`. Idempotent (skips completed (task,trial), retries infra). Concurrency guard prevents overlapping evals.
- **Poll every 30 min, not 6h.** The original 6h buffer wasted ~1h+ per session-limit cycle (the limit reset well before the next tick). The cheap probe lets a 30-min loop resume within ~30 min of reset. Confirmed live: caught a "resets 11pm" limit and resumed ~15 min after.
- **Scope to the 12 OFFICIAL datasets** (`--datasets …`). The local DAB checkout enumerates **104 queries / 520 trials** — 5 unofficial extras (civic_unstructured, cve, imdb, krama, usaspending) on top of the official 54/270. The first ticks ran unfiltered and wasted ~7 civic trials before the filter was added (task_filter isn't resume-guarded, so it applies cleanly).

**Interim findings (run ongoing; do NOT cite a final score yet):**
- **The sandbox holds.** Every trial uses MCP tools only (`mcp_tool_calls.jsonl` confirms); zero file/web access. No contamination outside agnews.
- **agnews leaks via model *memory*, not just tools.** Even fully sandboxed, Sonnet recalled the public AG News id→label mapping ("article_ids 0–29,999 = Business, label=2") and applied it via SQL — `_detect_contamination` caught it (via the "huggingface" mention) and withdrew it, but only catches trials that *name* the dataset; silent memorized use would pass. So **agnews is intrinsically unreliable for a pretraining-exposed model regardless of sandbox** — caveat it, don't treat its number as capability. Benchmark-side fix = shuffle `article_id`s; worth raising upstream.
- **Precedent — DAB PR #53 (Altimate):** same agnews `load_dataset` leak, same maintainer, sandboxed re-run **accepted onto the leaderboard**; agnews 100%→35%, stratified 68.93%→63.18%. They ran GPT‑5.5, so the contamination is model-agnostic. Our disclose→sandbox→rerun path is the accepted playbook.
- **Interim clean-vs-old per-dataset:** deps_dev_v1 10%→~30% (grounding tools may help, but n=5 noisy), github_repos 50% flat, patents 0% flat (Sonnet ceiling), bookreview tracking high, agnews honest ~29% (matches Altimate's clean 35%). The strong datasets (crmarenapro, stockindex, stockmarket, googlelocal, music_brainz) were still running at the time of writing.

## 2026-06-21 — Scent reference-doc layer (#26a, the consume half)

The first concrete slice of Pillar 3 (the Rat Maze knowledge moat): a `search_reference_docs` tool + dual on-disk reference-doc store. Built TDD on `feat/scent-reference-docs` (spec/plan in `docs/superpowers/specs|plans/2026-06-21-scent-reference-docs*`).

- **New `src/labrat/maze/` package** (code mirror of the on-disk `labrat_maze/` namespace, kind-agnostic for forward-compat): `_lexical.py` (tokenizer/stemmer/stopwords **extracted from `link_schema.py`** and shared — link_schema now imports them, behavior unchanged), `document.py` (`ScentDoc`/`Section` + `parse_document` — YAML frontmatter + H2-section split, tolerant of missing/malformed frontmatter), `store.py` (`MazeStore` — ordered source layers).
- **Dual store + precedence:** user-global `~/.labrat/maze/<profile>/scent/` < project `<root>/labrat_maze/scent/` (project wins on `domain` conflict). Project root = `$LABRAT_MAZE_DIR` else cwd. Deduped by `domain`.
- **Retrieval:** section-level lexical scoring `2·heading + body` (same mechanics as `link_schema`), grouped by doc, with each hit doc's Quick Reference prepended for context. `top_k` caps total matched sections across docs.
- **Benchmark-safety by construction:** empty/absent store → `results: []`, **no fallback-to-all** (the deliberate divergence from `link_schema`, which falls back). The store is empty on DAB/ADE → the tool is a no-op → zero leakage. Mechanism ships; content is user-authored. Shipped a `docs/scent/TEMPLATE.md` + a fixture-honest `examples/ecommerce_sales.md` (never auto-loaded).
- **Registered once** in `build_data_tools_registry()` (first) → reaches labrat-agent + MCP/claude-mcp + TUI. System-prompt Workflow gains a "Consult reference docs" step 1.
- **Deviation from the plan (intentional):** Quick Reference is excluded from *scoring* (it's context, not a hit) rather than scored-then-deduped as the plan sketched. Behavior-correct and tested; makes the plan's de-dup guard defensive-only.
- **Forward-compat seams (not built):** the `labrat_maze/<kind>/` namespace, the ordered precedence list (room for a team layer), and the `kind:` frontmatter discriminator let Trail/Warren + team scope land additively. `confidence`/`provenance` frontmatter fields are reserved for #26b/#29.

## 2026-06-21 — Scent auto-cartographer, GENERATE half (#26b cycle A)

The cold-start generator that writes the curated Scent docs #26a consumes. Built TDD on `feat/scent-cartographer` (spec/plan in `docs/superpowers/specs|plans/2026-06-21-scent-cartographer-generate*`). Subagent-driven; final whole-branch review (opus) = ready to merge.

- **New `src/labrat/maze/cartographer.py`** orchestrates existing deterministic blocks (no reimplementation): `ProfileDatasetTool` + `VerifyJoinTool` → a `Source: verified` skeleton (Quick Reference grain, Key Tables columns + mechanically-verified joins, Dimensions via bounded `SELECT DISTINCT` probes). One doc per connection, born `confidence: draft`.
- **Two-layer generation:** deterministic structure (always) + an **opt-in single LLM deep pass** (`--with-semantics`) that drafts Gotchas/Best-Practices/business-context as `Source: draft`, treating the verified skeleton as immutable ground truth. GT-firewall is structural — the LLM only ever sees `render_document(skeleton)`, never the filesystem/tools; `with_semantics=False` makes **zero** LLM calls (asserted).
- **Provenance marker** (`document.py` extension): human-visible `**Source:** verified|draft|human` per section, lifted into `Section.source` on parse (out of #26a retrieval scoring) and re-emitted by the new `render_document`. Unmarked sections → `human` (safe default; cycle-B MAINTAIN keys on this to flag-not-overwrite). The `document.py` change is purely additive — #26a parses unmarked docs byte-identically.
- **Join discovery is adapter-agnostic:** declared-FK candidates + an `<base>_id` name heuristic, self-joins excluded (case-insensitive), each mechanically confirmed via `verify_join` (`likely_valid` only). **Note: DuckDB introspection does not surface declared FKs** (verified empirically), so the FK path is dormant on the DuckDB CLI but active for FK-bearing adapters; it's covered by pure unit tests over synthetic profiles.
- **CLI** `scripts/cartograph.py` mirrors `run_task.py` (DuckDB-only; LLM pass opt-in; connections disconnected in a `finally` that also covers catalog introspection).
- **Single-schema assumption:** the distinct-probe + join SQL use bare unquoted identifiers (same convention as `verify_join`); inputs come from catalog introspection, not user free-text. Multi-schema qualification is a later refinement.
- **Deferred to cycle B (MAINTAIN spec or a follow-up):** (1) wire `context_engine.score_table_relevance` through the CLI for the table budget (the `relevance` param exists but is unpopulated, so budgeting falls back to row-count; the budget branch is currently untested since the fixture has <40 tables) + add a budget-path unit test; (2) resolve the preamble source-marker render asymmetry; (3) document the single-schema bare-identifier assumption inline. None block cycle A.
- **Benchmark safety unchanged from #26a:** never author + commit Scent docs for a held-out benchmark; the store stays empty there → `search_reference_docs` is a no-op → zero leakage.

## 2026-06-21 — Workflow skill + SQL self-repair (#30)

The procedural half of the article's two-layer skill pattern (knowledge half = #26a/#26b). Built TDD on `feat/workflow-skill` (spec/plan in `docs/superpowers/specs|plans/2026-06-21-workflow-skill*`); subagent-driven; final whole-branch review (opus) = ready to merge.

- **`src/labrat/agent/workflow.py`** — a canonical 9-step data-analysis SOP (`DATA_ANALYSIS_WORKFLOW`: clarify → consult_scent → ground → plan → query → repair → verify_joins → verify_answer → review) + `WorkflowState` (per-step status, notes, repair counter, checklist render).
- **`workflow` tool** (`agent/tools/workflow.py`) — fail-open **record-and-inspect**: the agent marks each step `doing`/`done`; the tool returns the rendered checklist (inspectable) and **never blocks**. State keyed by `profile_name`. Registered in `build_data_tools_registry()` → agent + MCP + TUI.
- **Deterministic SQL self-repair** (`run_sql.py`) — on an execution error, return structured repair-oriented diagnostics: `error_category` (missing_column / unknown_table / syntax / type_mismatch / other via deterministic message matching), `executed_sql` (post auto-limit), and a remediation `hint`. The AgentLoop already feeds errors back, so this makes the *existing* retry effective (research: +4.6pp BIRD comes from rich feedback, not a parallel loop). Scoped to the error path; fields default None (back-compat). Anti-thrash: a `repair_attempts` counter (agent-marked on `repair`/`doing`) flags after 3; `max_tool_calls` is the hard backstop.
- **System prompt** — `system_base.md` Workflow promoted to the 9-step SOP, naming the `workflow` tool and the `error_category`/`hint` repair guidance.
- **SOTA-validated (June-2026 research review, big-company/evaluated sources):** flow is SOTA-aligned; review added question decomposition (clarify), column-value grounding (ground), and the explicit repair step. **Fail-open enforcement confirmed** (hard gates cost throughput w/o accuracy gains; DS-STAR uses an advisory loop). Verifier stays opt-in: pure sufficiency judges show ~no benefit (matches our DAB finding); Anthropic's +6% needs a **tool-enabled** reviewer.
- **Benchmark-safe:** mechanism + generic diagnostics only (no content, no GT). Inert if unused.
- **Deferred follow-ups:** upgrade the #13 verifier to a tool-enabled adversarial reviewer (where the +6% lives); wire the anti-thrash counter to actual `run_sql` failures (currently self-reported); reference-doc drift detection (#26b cycle-B MAINTAIN); add `verify_join` to the Tool Usage list; minor test tidies.

## 2026-06-22 — Cartographer DAB pre-pass (GT-firewalled Cartographer pre-pass)

Wired #26b's deterministic Scent cartographer into DAB as a first-contact pre-pass — the honest, precedent-validated form of grounding (Altimate AutoContext PR #53: GT-firewalled, accepted on the leaderboard, ~+8pp). Corrects an earlier over-correction: we'd kept the DAB Scent store *empty* (belt-and-suspenders after the Phase 5 contamination), but the roadmap already permits "schema/grain/join **structure** only" grounding — the gap was integration, not capability. Built TDD on `feat/cartographer-dab-prepass`; subagent-driven; final whole-branch review (opus) = ready to merge.

- **Reusable seam:** `cartograph_prepass(connections, catalogs, primary, scent_dir, *, with_semantics=False, ...)` in `maze/cartographer.py` — lazy first-contact cache over `generate_scent` (if `scent_dir` has docs, reuse; else generate+write). DAB calls it **deterministic-only**; the agent's first-connect path later calls the SAME function (with semantics + the #26a dual store) — generalizing = a second caller, no rework.
- **DAB wiring** (`dab/suite.py`): `_run_cartographer(env_spec, dataset, cache_root)` connects the primary, introspects, runs the pre-pass into a **per-dataset** store (`<cache_root>/<safe dataset>/labrat_maze/scent`, so connection-key collisions like `main` can't happen), disconnects, returns the maze root. Wired into **both** drivers (claude-mcp = full-run path; labrat-agent = ablation), **gated by `--agent-cartograph`** (off by default; flag-off path is byte-identical — no store, no env, no prompt line). The agent reads it via `LABRAT_MAZE_DIR` + `search_reference_docs`; a **hermetic empty `HOME`** neutralizes the dev machine's `~/.labrat` user Scent layer. labrat-agent runs the pre-pass on a FRESH `build_dab_task_env` so its `finally: disconnect` doesn't close the driver's live connections.
- **Guardrails (verified):** deterministic-only (no LLM → no model-authored prose); GT-firewalled **by construction** (the pre-pass only receives DB connections + a store dir; `validate.py`/`ground_truth.csv` have no path into it — confirmed by data-flow trace); existing sandbox (MCP-only `--allowedTools`, cwd isolation) + `_detect_contamination` backstop unchanged. **Must be disclosed in the submission** (Altimate playbook).
- **Next (non-negotiable):** run the §6 **ablation** — tuning subset with vs without `--agent-cartograph` + the 9-task ADE smoke — and keep the Cartographer pre-pass for the full run **only if net-positive** (Altimate ~+8pp; confirm on ours).
- **Deferred (minor):** clean up `self._scent_cache_root` temp dir (an `atexit`/`shutil.rmtree(ignore_errors=True)`); orphan `mkdtemp` trees accumulate across cartographer runs (benchmark-only, no answer content).

**Ablation result (2026-06-22, claude-mcp/Sonnet, n=3, tuning subset):** OFF 5/24 (21%) → ON 7/24 (29%) = **+8.3pp** (deps_dev_v1 0→33%, music_brainz_20k 0→11%, stockindex 56→44% [−1 trial, noise; deterministic Scent has structure but not the dirty-date Gotcha, which needs the off-for-DAB LLM pass]). **Decision: KEEP `--agent-cartograph` for the full run** (matches Altimate's ~+8pp). Disclose in the submission. Next: layer the cheap prompt levers (ablated) → fresh full run.

**Prompt levers ablation (2026-06-22, on top of the Cartographer pre-pass, claude-mcp/Sonnet, n=3):** Cartographer-only 7/24 (29%) → +levers 9/24 (38%) = **+8.3pp** marginal (deps_dev_v1 33→50, stockindex 44→67, music_brainz_20k 11→0 [−1 trial, noise; force-query didn't move music_brainz — its failures look like query-logic, not memory]). Cumulative on the subset: tools-only 21% → +Cartographer 29% → +levers 38% (**+17pp**, each layer independently ablated). **Decision: KEEP the levers for the full run.** Full run uses `--agent-cartograph`.

## 2026-06-22 — Provider-agnostic check (Codex/GPT-5.5) + the observability that proved it

Verified the Cartographer + levers on the **labrat-agent / codex (GPT-5.5)** path, and built the trace observability (see Codex⇄Claude parity) that made the verdict provable.

- **Mechanism is provider-agnostic + PROVEN.** Scent generated; GPT-5.5 **consults** `search_reference_docs` (read directly from the new `agent_tool_calls.jsonl` traces: 2 calls = 1/trial, called FIRST per the prompt); per-call traces written on codex too (71 calls captured across 2 trials). So a codex submission is trace-valid.
- **Effect is provider-DEPENDENT.** Codex Cartographer ablation = **+0.0pp** (8/16 both, n=2) vs **+8pp** on Sonnet/claude-mcp. Not because GPT-5.5 ignores the Scent — it consults it — but because GPT-5.5 **already grounds exhaustively itself** (~32 `run_sql` + `profile_dataset`/`describe_table`/`list_tables`/`sample_rows`/`verify_join`/`column_stats` per trial), so structure-only Scent is largely redundant for it. The leaner-exploring Sonnet benefits more.
- **Decisions:** the leaderboard full run is **claude-mcp/Sonnet → keep Cartographer + levers** (validated +17pp stacked). For a codex run the Cartographer is neutral (harmless); leave it off or on by preference. The **P1 traces make either provider's submission valid.** No larger codex ablation warranted (rate-limited, effect explained, codex isn't the leaderboard path).

## 2026-06-22 — Full-coverage pass@1 sweeps (Sonnet vs GPT-5.5) + pre-submission ablations

Before committing the multi-day pass@5 submission run, ran de-risking pass@1 sweeps across all 12 official datasets on BOTH providers (Cartographer + levers on), then ablated two candidate levers.

- **Pass@1 full sweep, zero infra on either provider** (de-risk passed — Cartographer + levers + traces ran clean on all 12, incl. the un-ablated datasets): **Sonnet 50.1% stratified** (29/48 raw), **GPT-5.5 53.0%** (33/54 raw). A dead heat within n=1 noise (the big per-dataset deltas are single-trial flips) — consistent with "GPT-5.5 ≈ Sonnet." Both ~50% at pass@1 vs the 51.38% pass@5 leaderboard → pass@5-with-Cartographer has real headroom. **Decision: leaderboard run = claude-mcp/Sonnet** (rate-limit-free Max-plan; GPT-5.5's 53% required the self-healing loop fighting 429 bursts, not viable for a clean run).
- **Plan-discipline lever (#12) — ABLATED NET-NEGATIVE, discarded.** Added a brief-numbered-plan + verify-before-finish instruction to the claude-mcp prompt (parity with the labrat-agent prompt). Ablation on top of Cartographer (n=3, tuning subset): **−12.5pp** (overall 29%→17%), driven by **stockindex 56%→11% (−44pp)** — over-planning wastes turns / distracts on a dataset already working. Branch deleted. **Do not retry plan-discipline on the claude-mcp path.** (The labrat-agent prompt keeps its plan step — untouched; this was specifically about adding it to the leaner claude-mcp prompt.)
- **`--hints` BUG FIXED + lever identified.** DAB ships `db_description_withhint.txt` per dataset (a few lines of data-quirk guidance — e.g. music_brainz: "tracks has duplicates, do entity resolution"). It is **benchmark-provided, not self-generated** (read by DAB's `run_agent.py`; a declared leaderboard "Hints" axis; top teams incl. Altimate declare `Hints: Yes`). Altimate stacks BOTH the hint file AND their own AutoContext doc. **Our `--hints` was broken** — it loaded the hints file *instead of* the base description (dropping the schema); now appends (`base + "\n\n" + hints`), matching `run_agent.py`. Never bit us (all prior runs were hints-off, incl. the accepted 51.38%). Ablation (hints-on vs hints-off, on top of Cartographer+levers, n=3 tuning subset): **+8.3pp** (29%→38%) — **music_brainz 0%→22%** (its hint = the entity-resolution note) and stockindex 56%→78%; deps_dev_v1 −33pp (2-trial variance, our hardest dataset). **KEEP.** Submission runs `--agent-cartograph --hints`, declares `Hints: Yes` (a distinct declared category from the hints-off 51.38% — transparent, apples-to-apples with Altimate). Clean stacked story: each grounding layer ~+8pp (Cartographer · levers · hints).

## 2026-06-22 — Evaluated the "Full-Stack Agent Runtime" plan (Codex-authored) — DEFER, extract one milestone

A Codex agent authored `docs/superpowers/plans/2026-06-22-full-stack-agent-runtime.md`: a 12-milestone refactor into three runtime modes (Tools / API / Hosted) with new abstractions (RuntimeMode, AgentBackend, LabRatSession, ContextLedger, CheeseArtifact, ResultStore). **Assessment: do not adopt wholesale.**
- **Best idea (worth extracting): Milestone 4 — the Context Ledger.** Correct diagnosis of the Codex usage-burn: `AgentLoop` appends full tool results (up to 1000-row `run_sql`, big `profile_dataset` blobs) into history and resends the whole growing transcript every turn → caching is a band-aid; bounding what enters history (artifact + summary) is the real fix. Helps every provider; north-star-aligned (efficient governed runtime). **Caveat: it's a product/cost win, NOT a DAB-score lever** (Sonnet/Max-plan is flat-rate; DAB answers are small). Do it as a standalone scoped build AFTER the submission run.
- **Why defer the rest:** ~10 of 12 milestones don't improve answer quality (runtime modes, provider parity, packaging, positioning); it's a multi-month horizontal refactor that would stall validated benchmark momentum; it invests heavily in evidence-contradicted paths (OpenAI/Codex parity + a hosted-Codex subprocess backend — we measured GPT-5.5 ≈ Sonnet and the subscription path rate-limited/fragile); and it plans GTM packaging for a product without users yet (YAGNI). Keep the three-mode framing as a mental model, not a build. Repo findings in the doc are accurate (TUI hardcodes ClaudeCodeProvider, run_validations hardcodes Claude, MCP server DuckDB-only) — real tech debt to pay down later, not urgent.

## 2026-06-24 — Final corrected pass@5 submission score: 60.88%

Clean, sandboxed, fully-traced submission run (`runs/dab/dab-submission-cartograph-hints`): claude-mcp/Sonnet + Cartographer (deterministic-only) + prompt levers + `--hints` (declared Hints: Yes). **270/270 real, 0 infra, 0 contaminated** (deep trace scan: 2884 tool calls, all MCP tools, zero answer-key/external-dataset hits).

**Stratified Pass@1 = 60.88%** (excl. agnews: 62.33%). Trajectory: 54.34% (raw) → 55.88% (fixed 14 Claude-outage trials miscounted as fails) → **60.88%** (synced DAB checkout to current HEAD + re-ran patents & music_brainz q2 against corrected ground truths). **+9.5pp over the accepted 51.38%.**
Per-dataset: stockmarket 100 · stockindex 93 · bookreview 87 · crmarenapro 82 · pancancer 67 · googlelocal 60 · patents 60 · agnews 45 · github_repos 45 · music_brainz 27 · deps_dev_v1 20 · yelp 46. Big mover: **patents 0%→60%** (PR #59 fixed the globally-broken GT + documented CPC conventions in the hints; see [[reference_dab_ground_truth_versioning]]). agnews carries the model-memory caveat (disclose, as Altimate did).

## 2026-06-24 — LIVE on the DataAgentBench leaderboard at #8 / 60.88%

PR #65 accepted: **LabRat (Claude Sonnet 4.6 + Cartographer) is #8 of 21** on the public DAB leaderboard at **stratified Pass@1 60.88%** — the **only top-10 entry on a single mid-tier model** (every entry above runs GPT-5.5, Opus 4.8/4.6, or a stacked ensemble: #1 Spacedock+GPT-5.5 74.33, #2 Altimate+GPT-5.5+Sonnet 71.71, #4 Spacedock+Opus 4.8 67.21, #5 MinusX 65.18, #7 Pi+Opus 4.6 61.03, **#8 LabRat 60.88**). Our prior entry (Sonnet, no grounding) stays at #13 / 51.38% — **+9.5pp from the grounding layer alone**, not a model change. Submission disclosed Hints: Yes + the Cartographer + the opening prompts for the maintainers' leakage audit (clean: 270/270, 0 contamination, MCP-only sandbox). The grounding-is-the-moat thesis ([[reference_anthropic_self_serve_analytics]], north-star §5) is now externally validated against frontier-model and ensemble competitors. Leaderboard screenshot: `docs/images/dab-leaderboard-2026-06-24.png`. Next deepening candidate: the Context Ledger ([[project_full_stack_runtime_eval]]).

## 2026-07-04 — Column-level lineage + read-only Analyst mode (M3 / T1b)

- Read-only "Analyst" mode is enforced at ToolRegistry.dispatch (ToolContext.read_only
  + Tool.is_mutating), never in the prompt. run_sql classifies its SQL via a
  fail-closed sqlglot safelist (unparseable SQL is blocked under read_only, and
  force=True cannot bypass the gate).
- Column lineage is live-parsed via sqlglot.lineage against the introspected Catalog —
  deliberately NOT dbt-manifest-based (manifests go stale). explain_lineage is
  parse-only/fail-soft, mirroring check_sql.
- DuckDB introspection now captures views (Table.view_definition; duckdb_views(),
  NOT information_schema.views which leaks ~50 internal temp views). The Cartographer
  emits a `lineage`-tagged View Lineage section from view metadata only (GT-firewalled
  by construction: build_view_lineage takes a Catalog, no Connection); no-views DBs
  yield byte-identical Scent.

## 2026-07-05 — Context Ledger Phase 1 (T1d foundation)

Tool outputs no longer necessarily enter AgentLoop history verbatim: an opt-in
ContextLedger (`src/labrat/runtime/context_ledger.py`) bounds the model-visible
string (budget: 50 rows / 8000 bytes) and stores over-budget payloads in a
ResultStore (`src/labrat/results/store.py`; tables→Parquet+meta, profiles→JSON,
traces→JSONL) addressable by `result://<session>/<n>` refs. Tools declare large
payloads via an explicit `ledger_payload()` hook (run_sql/sample_rows → table,
profile_dataset/column_stats → json); hookless tools get a byte-bounded string
fallback. Summaries are mechanical (no LLM). Bare AgentLoop without a ledger is
byte-identical to before; `run_agent_task` defaults the ledger ON
(`enable_ledger=False` restores bare behavior; `ledger_dir=` for durable
provenance, else a per-call temp dir). `on_tool_call` still receives full
payloads, so DAB trace validity is unaffected. NOT a claude-mcp lever (that
path bypasses AgentLoop) — this is the M4 program-mode/`llm_extract` foundation.

## llm_extract / llm_classify — first LLM-calling tools (per-row primitives) (2026-07-05)

Shipped per-row LLM primitives (`llm_extract`, `llm_classify`) as registered data
tools backed by a shared engine (`agent/tools/llm_primitives.py::extract_rows`)
that fans out one `ctx.llm_fn` call per row from a deterministic loop. PromptQL-
style per-row primitives are white space on the DAB leaderboard (competitive
analysis 2026-07-03) and attack bulk unstructured extraction. Builds on the
Context Ledger: results bind outside model context (`ledger_payload() ->
("table", df)`) AND materialize as a queryable DuckDB temp table
(`llm_extract_result` / `llm_classify_result` by default).

Boundaries (non-negotiable): functional only where `run_agent_task` injects
`ctx.llm_fn` (labrat-agent/AgentLoop path; the runner adapts its own provider via
`provider_llm_fn` — same model + billing) — structured `ok=False` self-error
everywhere else (claude-mcp, MCP server, TUI); hard `max_rows` cap of 200;
per-row failures (NULL text, LLM error, bad JSON, missing field, out-of-label)
yield null rows + `rows_failed`, never aborting the batch; extracted columns are
always VARCHAR. NOT a claude-mcp leaderboard lever (that path bypasses
AgentLoop). Live DAB/patents validation is a deferred follow-on run. Sequential
fan-out for now; concurrency is a later optimization behind the same engine
interface.

## 2026-07-05 — Program mode: `run_program` tool-pipeline DSL (M4 2.2)

**Decision:** Ship program mode as a restricted tool-pipeline DSL — `run_program`
takes `{"steps": [{tool, args, bind}, ...]}` and the interpreter
(`src/labrat/agent/program/`) dispatches each step through the standard
`ToolRegistry` with the same `ToolContext`. NOT arbitrary code: no eval, no new
sandbox; a program inherits every existing gate per step (read-only
`is_mutating`, per-tool caps, input validation).

**Key mechanics:**
- Handle refs in step args: `$handle` → that step's materialized temp table
  (`program_<handle>`, via `LedgerPayloadProvider` → `materialize_table` on the
  DuckDB primary); `$handle.field` → a scalar from the step output's
  `model_dump()`. Token regex `\$([A-Za-z_]\w*)(?:\.(\w+))?` — `$100`-style SQL
  literals never match. Bad refs raise a typed `ProgramError` → failed step.
- Bounded by construction: max 20 steps (`DEFAULT_MAX_STEPS`); stop-on-error
  with partial summaries + failing step index; only `ProgramResult`
  (per-step `StepSummary`, no row payloads) returns to model context —
  intermediate tables never round-trip. The model reads `final_table`
  (`program_<final_bind>`) with a follow-up `run_sql`.
- A step also fails when its output reports `ok=False` (run_sql refusal /
  llm_extract self-error) even though dispatch succeeded — otherwise later
  `$refs` would read a poisoned handle.
- Recursion guard: `RunProgramTool.execute` builds its sub-registry via
  `build_data_tools_registry(include_program=False)` (deferred import breaks
  the data_tools↔run_program cycle) — a step `{tool: "run_program"}` is an
  unknown-tool error. `mutating=True` → blocked under read-only Analyst mode.
- `RunProgramTool` overrides `anthropic_schema`/`openai_schema` to pass the
  nested `ProgramStep` `$defs` through (the base helpers drop them).

**Why:** extends the Context Ledger from *bounding* tool-result re-entry to
*preventing* it (PromptQL/MinusX/Pi convergent "plan-then-execute" ground).
AgentLoop/product lever, NOT a claude-mcp leaderboard lever. Additive:
new modules + one registry flag; no change to the loop or existing tools.

## 2026-07-06 — M5 memory moat: Scent-provenance foundation + T2b correction-harvesting v1

**Foundation (Tasks 1-2):** `src/labrat/maze/provenance.py` adds `SOURCE_TIERS`
(`semantic_layer > lineage > verified > harvested > draft > human`), `source_rank`
(0 = highest trust, unknown tokens rank lowest) and `best_source` — the ordering the
future T3c provenance footer will render. `maze/document.py`'s `Section` gained two
new recognized source tokens (`harvested`, `semantic_layer`) plus optional freshness
metadata (`generated_at`/`schema_hash`/`model_id`/`git_sha`), serialized as a single
`**Meta:** k=v; ...` line under `**Source:**` and parsed back by `_extract_meta`
(mirrors the existing `_extract_source`). Back-compat: docs with no `**Meta:**` line
parse with all four fields `None` — the existing round-trip test still passes
unchanged.

**T2b v1 (Tasks 4-8):** correction memories were already extractable
(`memory/extractor.py::EditExtractor` / `ChatCorrectionExtractor`) but had no caller.
`memory/harvest.py::SessionHarvester` wires them into a session-boundary harvest
loop (`harvest_events` / `harvest_correction`), gated by an `enabled` flag so
benchmark/headless paths never harvest — confirmed by `grep -rn SessionHarvester
src/labrat/eval/` returning nothing. `maze/harvest.py` is the promotion pass:
`cluster_corrections` groups correction memories by `table_scope` (never by
`embedding` — that field stays unused, per the north-star design); `draft_harvested_sections`
renders each cluster into a `harvested`-tagged "Gotchas" `Section`, deduping bullets
by stripped body, and runs every drafted body through `scent_audit.detect_contamination`
— any hit raises `ScentContaminationError` and drops the *whole* cluster's section
(one contaminated bullet taints the batch; nothing partial is drafted). `MazeStore`
gained a write path (`write_doc`, `load_domain`) — it was read-only through M3/M4.
`apply_approved_sections` merges a human-approved section list into a domain's
`ScentDoc` and persists it: it loads-or-creates the doc, dedups against existing
section bodies (idempotent re-approval), and — critically — *appends* rather than
replaces, so re-approving a new correction never drops a prior harvested section.
`maze/staleness.py::schema_fingerprint`/`is_stale` hash a table→sorted-columns map
and compare against a section's stored `schema_hash`, flagging harvested Gotchas that
drifted from the live schema. `screens/harvest_controller.py` is a thin, pyright-exempt
orchestration layer (`review_corrections`, `harvesting_enabled`) sequencing the
already-unit-tested helpers for the TUI review flow — no new logic, just gating
(`is_interactive AND profile_opt_in`) and lazy imports to keep the screens/ import
graph light.

**Invariants:** (1) draft-then-human-approve, never auto-write — `draft_harvested_sections`
only returns `Section`s; `apply_approved_sections` is the only path that touches disk,
and it's only ever called with a human-approved subset; (2) benchmark-path exclusion —
`SessionHarvester` has zero callers under `src/labrat/eval/`; (3) `Memory.embedding`
remains unused (clustering is `table_scope`-keyed, not vector-similarity — deferred to
v2). Test coverage: `tests/unit/test_maze_harvest.py` now also covers the merge-preserves-
prior-sections and merge-is-idempotent cases (Task 6 review gap) and the mixed-clean-
plus-contaminated-cluster fail-loud case (Task 5 optional hardening).

**Deferred:** the `harvest_review.py` Textual review screen and a `main.py`
thread-close trigger to invoke it (no thread-close lifecycle exists in the TUI yet —
wiring one is a product decision plus needs manual TUI verification, not a unit-testable
build step); T2b v2 (autonomous scheduled harvest, embedding-based clustering instead
of `table_scope`); moat Increments 2/3 (`project_moat_roadmap`: T1b lineage integration,
T3c provenance footer).

## 2026-07-09 — TUI M1: chat through the real agent stack

The TUI chat path now builds its AgentLoop via `agent/session.py::build_agent_session` — the
same factory `run_agent_task` uses — with `build_data_tools_registry()` + 5 TUI extras (~25 tools),
multi-DB ToolContext (`primary="main"`, `read_only` from the profile), Context Ledger (durable under
`~/.labrat/ledger/<profile>/`), injected `llm_fn`, and an optional sufficiency verifier
(`Profile.verify_enabled`). Provider is per-profile (`agent_provider`, default "auto": Anthropic
with API key, else claude CLI + degraded warning). Spec:
docs/superpowers/specs/2026-07-06-tui-integration-design.md. Consensus verification stays
benchmark-only by design. Because default profiles have `is_read_only=True` and the tool-dispatch
gate blocks every `mutating=True` tool, mutating tools (`run_program`, `llm_extract`/`llm_classify`,
`load_file`, `attach_database`) self-report "blocked: read-only Analyst mode" on a fresh profile
and require a read-write profile to exercise.

## 2026-07-09 — TUI M2: first-connect Cartographer (T2c)

Connect-time deterministic pre-pass into the user store (`~/.labrat/maze/<profile>/scent`),
idempotent via cartograph_prepass's existing-docs cache; sidecar `.schema_fingerprint` enables
detect-and-offer staleness (never auto-regenerate — user-scope dir only, project layer preserved).
Controller is pure (`maze/first_connect.py`); the screen only notifies. Semantics stays off (T1c).
