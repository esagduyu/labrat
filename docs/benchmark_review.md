# Benchmark & Agent Harness Review

> Research session: 2026-05-24  
> Covers: [dbt-labs/ade-bench](https://github.com/dbt-labs/ade-bench) · [AltimateAI/altimate-code](https://github.com/AltimateAI/altimate-code) · LabRat current state

---

## 1. ADE-Bench: What It Is

ADE-Bench (Analytics and Data Engineering Benchmark) is the most serious public benchmark for
data-engineering agents right now. It is made by dbt Labs, it is execution-based (no LLM-as-judge,
no string matching), and it uses dbt's own test infrastructure as the evaluation oracle.

### 1.1 Task Design

~60 "ready" tasks across 10+ datasets: Airbnb, Formula 1, HelixOps SaaS, QuickBooks, Asana,
Intercom, Shopify analytics, generic analytics-engineering patterns.

Every task lives in `tasks/<task_id>/` with:

```
tasks/f1006/
├── task.yaml          # metadata, prompts, scoring config
├── setup.sh           # breaks the project for the agent to fix
├── solution.sh        # the answer key (only the sage/oracle agent uses this)
├── tests/             # hand-written dbt singular tests
├── seeds/             # solution__<table>.csv answer-key tables
└── solutions/         # files used by solution.sh
```

`task.yaml` carries:
- `difficulty`: easy | medium | hard
- `tags`: dbt-hygiene, model-creation, debugging, model-refactor, jinja, dbt-macros, etc.
- `prompts`: multiple variants (e.g. `base` and `hard`) — the `hard` variant intentionally
  omits diagnostic hints
- `solution_seeds`: which tables to compare against CSV answer keys (with include/exclude
  column lists and alternate answer support)
- `test_setup`: shell commands run *after* the agent finishes but *before* evaluation
  (e.g. `dbt run --select some_model`)
- `variants`: which (db_type, project_type) combos apply — DuckDB+dbt, DuckDB+dbt-fusion,
  Snowflake+dbt, Snowflake+dbt-fusion

Task difficulties:
- **Easy** — add a column, fix a deprecated macro, fix a data type, backfill a seed
- **Medium** — add a source layer, refactor a model family, debug a join
- **Hard** — diagnose a semantic calculation error with no explicit hint, trace a multi-model
  regression

### 1.2 Agent Interface

The interface is deliberately simple: **the agent is a CLI tool invoked with a text prompt inside
a Docker container.** The harness does not impose any Python callbacks, SDKs, or APIs.

For Claude Code:
```bash
echo 'AGENT RESPONSE: ' && claude --output-format stream-json --verbose \
  -p '<task_prompt>' [--model <model>] [--allowedTools Bash Edit Write ...]
```

The agent has read/write access to the dbt project directory, a live DuckDB database, and can run
`dbt`, `dbt test`, and arbitrary shell commands. It finishes when it exits. That's it.

### 1.3 Evaluation Mechanism

Pure execution-based, binary pass/fail per trial:

1. Copy solution seed CSVs into the sandbox, run `dbt seed` to materialise them as tables
2. Drop the task's `tests/` directory into place
3. Auto-generate two extra dbt singular tests per solution seed:
   - **equality test**: symmetric difference of agent-produced table vs. seed table (pass if diff = 0)
   - **existence test**: both tables must exist (non-null relations)
4. Run `dbt test` (or dbt-fusion equivalent) on all tests
5. **Pass** if and only if every test passes

No partial credit. The equality test supports multiple alternate answer keys and column
include/exclude lists. The existence test is a lightweight sanity check.

### 1.4 Metrics Tracked

Per trial: `is_resolved`, `input_tokens`, `output_tokens`, `cache_tokens`, `num_turns`,
`runtime_ms`, `cost_usd`, `failure_mode`, `tools_used`.

Aggregate: simple accuracy + **pass@k** using the unbiased estimator
`1 - C(n-c,k)/C(n,k)` (same as HumanEval's pass@k).

### 1.5 Infrastructure

- Docker + Docker Compose — each trial is a fresh isolated container
- tmux/libtmux — the harness sends prompts and reads output via tmux inside the container
- 4 Docker image variants: duckdb-dbt, duckdb-dbtf, duckdb-fusion, snowflake-dbt, snowflake-dbtf
- DuckDB database files downloaded from GitHub Releases (shared across tasks of the same dataset)
- Snowflake support optional (requires admin credentials to clone databases per trial)
- Agent CLIs (claude, codex, gemini) installed inside the container per trial

### 1.6 Plugin Sets

Named sets of skills/MCPs that can be toggled with `--plugin-set`:

| Set | Skills | MCP | Extra tools |
|-----|--------|-----|-------------|
| `none` (default) | — | — | Bash, Edit, Write, Read, Glob, Grep |
| `all-dbt-skills` | dbt-agent-skills (all) | — | + Skill |
| `dbt-for-ae` | using-dbt-for-analytics-engineering only | — | + Skill |
| `dbt-mcp` | — | dbt MCP server (Snowflake only) | + mcp__dbt__* |
| `dbt-skills-mcp` | all dbt skills + dbt MCP | both | + Skill, mcp__dbt__* |

---

## 2. AltimateAI/altimate-code: What It Is

altimate-code is an open-source data-engineering agent harness — described as "a fork of OpenCode
rebuilt for data teams." It is *inspiration*, not something to integrate directly. It is written in
TypeScript on Effect.js; LabRat is Python. The interesting parts are architectural patterns.

### 2.1 Agent Loop

A classic tool-use while loop (`session/prompt.ts`):

1. Load message history; find `lastUser`, `lastAssistant`, `lastFinished`
2. Exit if the last assistant message is a final answer (not a tool call)
3. Check for context overflow → compact
4. Assemble system prompt: env context + skill injections + memory/training + instructions
5. Resolve tools: filter by agent mode + user permissions; generate stubs for tools in history
   but no longer active (prevents LLM API validation errors)
6. Call LLM via `streamText()`, process events: tool-call → run tool, tool-result → record
7. Permission blocks and errors can halt the loop
8. Continue until exit condition

Doom-loop protection: identical tool input ≥3× → permission prompt; same tool ≥30× → telemetry.
Compaction is limited to 3 attempts per session.

**Compaction summary template** is structured (not free-text): captures goal, instructions, data
context, discoveries, accomplishments, relevant open files, and next steps. This is worth copying.

### 2.2 Three-Agent Mode System

| Mode | What it can do |
|------|---------------|
| `builder` | Full read/write/execute |
| `analyst` | Read-only + SELECT-only SQL execution |
| `plan` | File-read only, outputs a plan file |

Permission composition: last-match-wins. Safety denials (DROP/TRUNCATE blocking) are immutable
regardless of any config. The `analyst` mode is interesting for LabRat's read-only query use case.

### 2.3 Tool Design Philosophy: Determinism First

The key differentiator: ~34 capabilities are implemented in a **Rust NAPI-RS native core**
(`@altimateai/altimate-core`). The LLM doesn't guess at SQL validity, anti-patterns, or column
lineage — a deterministic parser does it. The LLM then reasons about the *outputs* of those
parsers.

Relevant Rust-backed capabilities:
- SQL validation, linting, safety scanning (anti-pattern detection with F1=1.00 on 1,077 queries)
- SQL transpilation (dialect-to-dialect)
- Semantic equivalence checking
- Column-level lineage tracing (500-query benchmark, 100% edge match)
- dbt manifest parsing
- PII classification

The Python equivalent of this pattern for LabRat would be: sqlglot (already a dependency) for
structural SQL analysis, rather than asking the LLM to describe problems.

### 2.4 ~70 Data Engineering Tools

Organized into categories: SQL analysis/execution/diff, schema inspection, dbt integration,
FinOps (query cost, warehouse tuning, unused resources), data parity (cross-warehouse diffs),
column lineage, governance/PII, warehouse management, persistent memory.

Notable tools LabRat doesn't have:
- `data_diff` (5 algorithms: auto/joindiff/hashdiff/profile/cascade; cross-warehouse)
- `sql_analyze` (anti-pattern detection on arbitrary SQL without executing it)
- `finops_*` family (query cost analysis, warehouse advisor)
- `impact_analysis` (DAG blast-radius before a schema change)
- `dbt_unit_test_gen` (generates dbt 1.8+ YAML unit tests from compiled SQL)
- `training_save` / persistent per-project memory

### 2.5 Skills as SKILL.md Files

19 built-in skills: `/dbt-develop`, `/data-parity`, `/cost-report`, `/pii-audit`, etc. Each is a
markdown file with YAML frontmatter. A separate LLM call (`skill-selector.ts`) uses the project
fingerprint to choose ≤15 relevant skills per session to inject into the system prompt. The
selection has a 5s timeout and full fallback.

The `dbt-develop` skill alone has reference subdocs for: layer patterns, medallion architecture,
incremental strategies, YAML generation, common mistakes. This is the level of domain knowledge
a serious data engineering agent needs.

### 2.6 Pre-execution Protocol (enforced in system prompt)

Before any SQL execution:
1. `sql_analyze` — anti-pattern scan
2. `altimate_core_validate` — syntax + schema validation
3. Execute

The builder system prompt calls this "NOT optional." This is the safest approach for a
write-capable agent.

### 2.7 Project Fingerprinting

On session start, scans for `dbt_project.yml`, `profiles.yml` (to detect adapter type),
`.sqlfluff`, `airflow.cfg`, `databricks.yml`. The fingerprint drives tool and skill selection.
LabRat already does something similar when connecting via profiles, but it doesn't auto-discover.

### 2.8 Persistent Memory / Training System

Per-project or global key-value store (patterns, rules, glossary, standards, context, playbooks).
Limits on entry count and total size. Usage frequency tracking. Dedup detection. Budget monitoring.
Project-scoped entries are committed to git for team sharing.

LabRat already has `recall_memories` and `FindingsManager` — the gap is on the write/persist side
(no structured way for the agent to save learnings across sessions).

---

## 3. LabRat Current State

### 3.1 Agent Loop

`src/labrat/agent/loop.py` — a clean tool-use while loop, provider-agnostic via `ModelProvider`.
Currently supports:
- `ClaudeCodeProvider` — shells out to `claude --print --output-format json` (no API key needed,
  uses Max subscription)
- `AnthropicDirectProvider` — direct API via `anthropic` SDK
- `OpenAICompatibleProvider` — for OpenAI / any OpenAI-API endpoint

### 3.2 Tools

12 tools registered in `MainScreen.on_mount()`:
`run_sql`, `draft_sql`, `create_chart`, `list_tables`, `describe_table`, `sample_rows`,
`search_columns`, `column_stats`, `explain_sql`, `search_query_history`, `recall_memories`,
`run_validations`

Missing vs. altimate-code:
- No SQL anti-pattern analysis (static analysis, not execution)
- No cross-table data diff / data parity
- No dbt manifest understanding / DAG awareness
- No column-level lineage
- No agent-writable persistent memory
- No pre-execution validation pipeline

### 3.3 Spider2-DBT Benchmark (Current)

Located in `src/labrat/dspy_opt/`. Uses DSPy for optimization framing. Problems identified:

- **Dataset quality**: Spider2's `_tmp` Fivetran patterns and inconsistent source naming are
  unsolvable at the prompt level — they're test-design bugs, not agent failures
- **Turn limit hit**: 20-turn cap causes the agent to run dbt successfully but never call `submit`
- **ClaudeCodeProvider timeout**: was 120s (too short); fixed to 300s
- **Score**: 8.3% DSPy metric (1/12 tasks); 37.5% dbt success rate (3/8 tasks that ran)
- **DSPy overhead**: the DSPy framing (LM, metric, evaluate) adds complexity without benefit;
  the benchmark doesn't use DSPy's optimisation features meaningfully

---

## 4. Integrating ADE-Bench: What It Would Look Like

### 4.1 The Right Architecture: Benchmarks Outside the Repo

The cleanest design is:
- **LabRat** = the library + agent, importable or invokable as a CLI
- **Benchmarks** = external repos that call LabRat's agent interface, measure performance,
  and feed results back

This means benchmarks don't need to live in `src/labrat/` at all. They only need to know:
"given a text prompt in a working dbt/DuckDB directory, invoke this agent, wait for it to
finish, then run dbt test."

ADE-Bench is already designed this way. The agent interface is just:
```bash
labrat bench --prompt "<task_prompt>" --model claude-sonnet-4-6
```
or
```bash
claude --print -p "<task_prompt>"
```

### 4.2 LabRat as an ADE-Bench Agent

For LabRat to participate in ADE-Bench with zero changes to the benchmark itself, we need a
thin CLI wrapper that:
1. Accepts a `--prompt` argument (or reads from stdin)
2. Spins up `AgentLoop` with `ClaudeCodeProvider` + the relevant tools (dbt-aware: `run_sql`,
   `draft_sql`, `list_tables`, `describe_table`, `explain_sql`, `run_validations`)
3. Runs the loop until the model produces a final text response (no more tool calls)
4. Exits 0 on success

This would live in `src/labrat/cli_bench.py` or as a subcommand of the existing `labrat` CLI:
```
labrat agent --prompt "Fix the deprecated macro in the dbt project" \
             --dialect duckdb --model claude-sonnet-4-6
```

**What the agent would need to actually work on ADE-Bench tasks:**

The agent currently lacks filesystem tools (Read, Write, Edit, Bash). In the TUI context these
aren't needed, but for ADE-Bench tasks the agent needs to:
- Read and edit `.sql` and `.yml` files in the dbt project
- Run shell commands (`dbt run`, `dbt test`, `dbt compile`)
- Read `profiles.yml` and `dbt_project.yml`

This means ADE-Bench–compatible LabRat needs an additional tool layer:
- `read_file(path)` — read arbitrary project files
- `write_file(path, content)` — write back fixed SQL/YAML
- `run_shell(command)` — execute `dbt run`, `dbt test`, etc.

These are precisely what Claude Code CLI gives you for free when invoked with `--allowedTools`.
For a LabRat-native agent loop these would need to be explicit `Tool` implementations.

### 4.3 What ADE-Bench Tests That Spider2-DBT Doesn't

| Dimension | Spider2-DBT (current) | ADE-Bench |
|-----------|-----------------------|-----------|
| Task authorship | Academic/crowdsourced | dbt Labs engineers |
| Dataset quality | Poor (Fivetran `_tmp` patterns, bad naming) | High (purpose-built for agent eval) |
| Evaluation oracle | dbt test + gold DB comparison | dbt singular tests + CSV seed equality |
| Task variety | Mostly query writing | Hygiene, refactor, debug, macros, jinja |
| Difficulty tiers | Implicit | Explicit (easy/medium/hard) + prompt variants |
| Agent interface | DSPy module calling Python tool | CLI + tmux in Docker |
| Sandbox isolation | Single shared DuckDB | Fresh Docker container per trial |
| Pass@k support | No | Yes (unbiased estimator) |
| Infrastructure cost | Low (local DuckDB) | Medium (Docker required) |

### 4.4 Implementation Sketch (not a plan, just orientation)

If we were to integrate ADE-Bench, the rough steps would be:

**Step 1 — Headless agent CLI**
Add `labrat agent` subcommand (or `labrat bench`) that takes a `--prompt` and runs the agent
loop non-interactively. The CLI exits when the loop exits. This is the only thing ADE-Bench
actually needs.

**Step 2 — Filesystem tools**
Implement `ReadFileTool`, `WriteFileTool`, `RunShellTool` as `Tool` subclasses. These have a
wider blast radius than the current TUI tools (they write files, run arbitrary commands), so
they need an opt-in flag: `--allow-filesystem`, `--allow-shell`.

**Step 3 — dbt-aware tools**
Optionally wrap `dbt run/test/compile` as structured tools rather than raw shell calls —
returning structured output (model names, test counts, failure summaries) rather than raw stdout.
This is the "determinism first" lesson from altimate-code.

**Step 4 — ADE-Bench as a separate repo**
Fork or install ade-bench. Configure it to use LabRat via a custom agent install script
in `tasks/shared/config/CLAUDE.md` (ade-bench supports this natively for Claude Code).
Alternatively, add a new agent entry (`installed_agents/labrat/`) to ade-bench pointing to the
`labrat agent` CLI.

**Step 5 — Benchmark results in a findings file**
After each run: `ade view` → save the TSV + HTML into `benchmarks/results/ade-bench/<date>/`.
Over time this gives a trend line on LabRat's data-engineering agent capability.

### 4.5 Why Not DSPy for ADE-Bench

DSPy's value is in prompt *optimization* (few-shot example selection, prompt tuning). ADE-Bench
tasks are too varied and too few per dataset for DSPy's optimizers to fire meaningfully. The
right framing is:

1. Run ADE-Bench → identify failure modes
2. Improve LabRat's tools/prompts/loop based on those failure modes
3. Re-run ADE-Bench → measure improvement
4. Repeat

This is a human-in-the-loop improvement loop, not an automated optimizer. Drop DSPy from the
benchmark harness entirely. Keep it only if/when there's a specific prompt optimization problem
to solve (e.g., optimising a classifier).

---

## 5. Key Lessons from altimate-code for LabRat

These are the patterns worth borrowing, ranked by impact vs. effort:

### 5.1 High Impact, Low Effort

**Structured compaction summaries**
altimate-code's compaction captures: goal, instructions, data context, discoveries,
accomplishments, relevant open files, next steps. LabRat's `AgentLoop` compaction (if it has
one) should do the same. This directly improves long-task performance.

**Pre-execution validation pipeline**
Before `run_sql` executes anything, run a lightweight validation step using sqlglot (already a
dependency): parse the SQL, check for common anti-patterns (SELECT *, unbounded scan, no
WHERE on large tables). Return a warning in the tool output, not a hard block. This is
determinism-first in LabRat's terms.

**Binary agent modes**
Expose an `analyst` mode (read-only SQL only) vs. `builder` mode (read/write + mutations allowed)
as a CLI flag. The TUI already has mutation confirmation dialogs — formalize this as a mode.

### 5.2 High Impact, Medium Effort

**dbt-aware tool layer**
Replace raw `run_sql` with structured dbt-aware tools when operating in a dbt project:
- `dbt_run(models)` → structured result (pass/fail per model, compilation errors, row counts)
- `dbt_test(select)` → structured result (test name, status, failure message)
- `dbt_compile(model)` → returns compiled SQL for inspection
- `dbt_manifest()` → model/source graph, column definitions, materializations

This is what makes the difference between an agent that can *stumble through* a dbt project and
one that *understands* it.

**Column-level lineage (sqlglot)**
sqlglot has built-in column lineage tracing (`sqlglot.lineage`). Exposing this as a tool gives
the agent the ability to understand upstream/downstream impact of a column change — critical for
refactor and debugging tasks.

**Impact analysis before writes**
When the agent is about to modify a model, a `dbt_impact(model, change_type)` call that traverses
the dbt DAG and returns "this change affects N downstream models" — so the agent can decide
whether to also update those models.

### 5.3 Lower Priority (Later)

**Project fingerprinting** — auto-detect dbt_project.yml and pre-configure tools without manual
profile setup. Medium effort; reduces friction for new users.

**Agent-writable memory** — structured persistent memory (patterns, discovered facts, glossary)
that the agent can save via a `save_memory(key, value)` tool and recall later. Medium effort;
high payoff for multi-session workflows.

**Pass@k benchmark reporting** — implement the unbiased estimator for aggregate benchmark reports.
Low effort; important for measuring reliability, not just accuracy.

**data_diff tool** — cross-table symmetric difference query as a first-class tool. Low effort
using existing DuckDB + sqlglot. Useful for self-verification ("did my fix actually change the
output?").

---

## 6. Recommended Direction

The overall recommendation, in priority order:

1. **Drop Spider2-DBT as the primary benchmark** — the dataset quality is the bottleneck, not
   the agent. No amount of agent improvement will fix tasks that have bad gold references.

2. **Build the headless `labrat agent` CLI** — this is the prerequisite for any external
   benchmark integration. ~1 day of work.

3. **Add filesystem + shell tools behind a flag** — `ReadFileTool`, `WriteFileTool`,
   `RunShellTool`. This unlocks dbt task types. ~0.5 days of work.

4. **Run ADE-Bench DuckDB tasks against the new CLI** — start with the `easy` tier (10-15
   tasks). Use as a regression baseline. ADE-Bench handles all the Docker/evaluation
   infrastructure.

5. **Add structured dbt tools** — `dbt_run`, `dbt_test`, `dbt_compile`, `dbt_manifest` —
   as the benchmark surfaces failure modes. Implement these incrementally as fixes for specific
   observed failures.

6. **Archive the Spider2-DBT code** — move `src/labrat/dspy_opt/` to `archive/` or a separate
   branch. It's not actively useful and adds maintenance weight.

This gives a clean feedback loop: external benchmark → identify gaps → improve LabRat tools
→ re-benchmark → repeat. The benchmark repo stays outside LabRat, LabRat stays a focused agent
library.
