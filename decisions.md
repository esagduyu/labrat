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

**Impact:** fixes all `deps_dev_v1` (cross-DB join) and improves `music_brainz_20k` (tracks in SQLite, sales in DuckDB — same issue). Phase 1b run will quantify.
