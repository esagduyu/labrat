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
