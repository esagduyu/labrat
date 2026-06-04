# LabRat on DataAgentBench: Phase 1 Results and Roadmap

> **TL;DR** — LabRat scored **48.5%** on the 5 DuckDB+SQLite datasets of DataAgentBench (17 queries, pass@5, claude-sonnet-4-6). This is an explicitly a **raw Claude + prompt engineering baseline** — no LabRat tools, no LabRat agent loop. Phase 4 will route DAB through LabRat's actual tool stack; the gap between Phase 1b and Phase 4 is the quantified value of the tool layer.

---

## What is DataAgentBench?

[DataAgentBench](https://ucbepic.github.io/DataAgentBench/) (UC Berkeley) is a multi-database, execution-based benchmark for data agents. Unlike ADE-Bench (which tests dbt/analytics engineering), DAB tests **natural language to SQL** across heterogeneous database stacks.

The benchmark structure:
- **12 datasets**, **54 queries** total (official; the local repo contains 5 extra unofficial datasets, see gotchas below)
- **4 database types**: DuckDB, SQLite, PostgreSQL, MongoDB
- Many datasets require **cross-database joins** — e.g., a question answered by joining a DuckDB table with a SQLite table
- Success is judged by an exact-match or near-match validator per query (`validate.py` per task) — no LLM judge, no partial credit
- Scoring is **stratified**: mean of per-dataset pass rates. Each dataset contributes equally regardless of query count. Per-query rate = `passes / n_trials` (not binary)

The top-of-leaderboard scores as of May 2026:

| Agent | Score | Notes |
|-------|-------|-------|
| MinusX | 63.1% | Full 54-query / 12-dataset run |
| Altimate Code | 60.4% | Full 54-query / 12-dataset run |
| Spacedock | 57.7% | Full 54-query / 12-dataset run |

Our Phase 1 runs cover only 17/54 official queries (5 DuckDB+SQLite-only datasets) and are not directly comparable to leaderboard scores until we complete Phase 2 (PostgreSQL) and Phase 3 (MongoDB).

---

## Our setup

DAB requires a different harness architecture than ADE-Bench. The key constraint: DAB validators are Python scripts that need the answer as a plain string, not a test suite to be run inside Docker. There's no sandboxed execution environment — the agent needs to actually connect to local databases.

**Agent:** `claude --print --disable-slash-commands --dangerously-skip-permissions --max-turns 15` with the native Bash tool.

The agent runs Python+DuckDB queries directly via shell:
```bash
python3 -c "
import duckdb
conn = duckdb.connect('/path/to/db.duckdb')
print(conn.execute('SELECT ...').fetchall())
"
```

**No LabRat tools.** This is intentional. The DAB harness (`src/labrat/eval/benchmarks/dab/suite.py`) uses raw Bash, bypassing `AgentLoop` and `ClaudeCodeProvider` entirely. The text-protocol conflicts with the `claude` CLI's native tool handling (see `decisions.md`). More importantly, this is the design: Phase 1 establishes what raw Claude + good prompt engineering can achieve. Phase 4 will route DAB through the actual LabRat tool loop, and that delta is the measured value of the tool layer.

**Prompt preamble (`run_trial`):** each trial injects:
1. System role ("You are a data analyst. Query the databases using Python+DuckDB/SQLite via Bash.")
2. Per-DB connection snippets for every database in the task's `db_config.yaml`
3. When the task has both DuckDB and SQLite databases: the ATTACH idiom for cross-DB JOINs
4. The task's `db_description.txt` + the specific query question

**Scoring:** `scripts/eval_dab.py` runs trials, appends results to `runs/dab/<id>/trials.jsonl`, then calls `DabSuite.aggregate()` which computes the stratified mean. The run is resumable — restart with `--output-dir runs/dab/dab-<id>` to skip already-completed `(task_id, trial_num)` pairs.

---

## The original plan and what actually changed

This section documents the gap between the design spec (`docs/superpowers/specs/2026-05-27-dab-bench-integration-design.md`) and what was built. Preserving this is useful for future sessions so we don't re-litigate closed decisions.

### What the spec planned

The original spec (written May 27, 2026 via an Opus-level Claude brainstorm) designed a **full LabRat-tool-loop** approach:

1. **Inference via `ClaudeCodeProvider`** — shell out to the `claude` CLI, but route tool calls through LabRat's own `AgentLoop` and tool registry. Zero API cost on Max plan.
2. **Five new first-class LabRat tools:**
   - `list_databases` — discover available connections from `ToolContext.connections`
   - `attach_database` — DuckDB ATTACH for SQLite and PostgreSQL (LabRat's "secret weapon" per the spec)
   - `load_mongo_collection` — materialize MongoDB → DuckDB temp table
   - `execute_python` — subprocess sandbox for fuzzy entity resolution and unstructured text
   - Extended `run_sql`, `list_tables`, `describe_table` with optional `database` param
3. **Multi-DB `ToolContext`** — `connections: dict[str, Connection]` replacing the single `connection: Connection`, with backwards-compat shims for existing TUI and ADE-bench paths.
4. Phase 0 included an explicit go/no-go gate: **`ClaudeCodeProvider` compatibility spike** — wire a 2-tool toy registry, confirm tool calls round-trip. The spec said: *"If this assumption is wrong, the entire design pivots to a Bash-tool-prompting approach (the LabratLocalAgent pattern, adapted for query-answering)."*

The expected benefit: the agent would call structured tools (`list_databases → attach_database → run_sql`) rather than writing raw Python subprocesses, and those same tools would ship in the TUI product. DAB score would directly measure LabRat's tool quality.

### What the Phase 0 spike found

The spike was run and committed (commit `24501be`). Two failure modes surfaced:

**Failure mode 1 — `--tools ""`  hangs.** When `ClaudeCodeProvider` passes `--tools ""` to suppress the claude CLI's native tools so LabRat's custom protocol takes over, the CLI intercepts any `{"type":"tool_use",...}` response from the model and waits for a permission dialog. That dialog never arrives in a non-interactive subprocess. The process hangs indefinitely.

**Failure mode 2 — Without `--tools ""`, the model uses the CLI's native tools.** Without the suppression flag, claude CLI exposes its own Bash/Read/Edit tools. The model uses those instead of LabRat's custom text-protocol tool calls. `run_sql` and `list_databases` are never invoked.

The two failure modes are a pincer: suppress native tools → hang; don't suppress → wrong tools used. There's no simple flag combination that threads the needle. The spec's own pivot clause was triggered.

The root cause is architectural: `ClaudeCodeProvider` implements a text-protocol convention (`{"call": "<tool>", "input": {...}}`) that was designed for LabRat's TUI where the model's context has no awareness of the claude CLI's native tool infrastructure. When the same approach is used inside the claude CLI process, the CLI's tool handling layer sits between the model and the response stream, and the two protocols conflict. This is a deferred problem, not a dead end — three candidate resolutions are recorded in `decisions.md`:
- MCP server: expose LabRat's tool registry as MCP; `claude --print` connects natively, no text-protocol needed
- Headless `labrat run-task` CLI: wraps AgentLoop, returns JSON; DAB harness calls it as subprocess
- `--tools` JSON schema injection: newer claude CLI versions may allow custom tool schemas directly

### The pivot: raw Bash

For the DAB harness, `AgentLoop` and `ClaudeCodeProvider` were bypassed entirely. The harness calls:

```
claude --print --disable-slash-commands --dangerously-skip-permissions --max-turns 15
```

with the model's native Bash tool. The model runs Python+DuckDB queries as shell subprocesses. No LabRat tools are involved. This is structurally identical to `LabratLocalAgent` (the ADE-bench harness) — the same `claude --print` + Bash pattern that already worked there.

The five planned new tools (`list_databases`, `attach_database`, etc.) were **not built** for the DAB harness. They remain on the roadmap as first-class LabRat TUI tools, which is actually the right place for them — DAB using raw Bash doesn't mean the tools have no value, it means their value will be measured in Phase 4.

### What was preserved from the original plan

Not everything changed. These spec items shipped as designed:

- **Unified `BenchmarkSuite` protocol** — `src/labrat/eval/types.py` with `BenchmarkTask`, `TrialResult`, `AggregateScore`, `BenchmarkReport`. All benchmarks implement the same protocol.
- **`DabSuite` + `scorer.py` + `reporter.py`** — enumeration, per-trial orchestration, validator wrapping, `submission.json` generation.
- **Multi-DB `ToolContext`** with backwards-compat shims — shipped as designed. `ctx.connections: dict[str, Connection]` + `ctx.connection` shim. Required for the DAB harness's `env.py` to build multi-DB contexts.
- **pass@5 + JSONL resumability** — `eval_dab.py` appends per-trial records, skips completed pairs on restart.
- **ADE smoke regression gate** — 9-task fixed set, baseline at `tests/baselines/ade_smoke_baseline.json`, `run_smoke_regression.py check` at every phase boundary.
- **Branch isolation** — `feat/dab-integration` stayed separate from master until Phase 1b exit gates passed. Merged 2026-05-30.

### Cross-DB ATTACH: tool vs. prompt

The spec designed `attach_database` as a proper LabRat tool that would call `conn.execute("ATTACH ...")` under the hood. After the pivot, this became a **prompt engineering solution** instead:

`DabSuite.run_trial` detects DuckDB+SQLite mixes in `db_config.yaml` and injects the ATTACH idiom directly into the preamble text, showing the model exactly how to write the Python code itself:

```python
conn.execute("ATTACH '/path/to/other.db' AS alias (TYPE SQLITE)")
# then: SELECT ... FROM duck_table JOIN alias.sqlite_table ON ...
```

This is prompting the model to do what the tool would have done. The limitation is that the model can ignore or misuse the preamble (as music_brainz_20k demonstrates). A proper `attach_database` tool would be called explicitly by the agent and return confirmation — no ambiguity. This is one of the clearest cases where Phase 4's tool integration should improve the score.

### Why the baseline is still valuable

The pivot turned a potential architectural problem into a feature: Phase 1b's 48.5% score is now a clean **"what raw claude-sonnet-4-6 + good prompt engineering achieves on multi-DB queries"** number. No LabRat infrastructure noise, no tool-layer effects, no benchmark-specific scaffolding. If someone wants to know the model's ceiling without a tool stack, 48.5% on 17 DuckDB+SQLite DAB queries is the number.

Phase 4's `LabRatAgentDriver` score, subtracted from this baseline, will be the measured, defensible value of LabRat's tool layer — a number no one else has published because most teams don't cleanly separate the two.

---

## Phase 1a: 43% (2026-05-29)

First baseline. Five DuckDB+SQLite datasets, n_trials=1, no ATTACH preamble.

| Dataset | Queries | Score |
|---------|---------|-------|
| stockmarket | 5 | **100%** |
| github_repos | 4 | **50%** |
| music_brainz_20k | 3 | **33%** |
| stockindex | 3 | **33%** |
| deps_dev_v1 | 2 | **0%** |
| **Overall** | **17** | **43.3%** |

**Notable issue:** the `common_scaffold` validator import was broken in `scorer.py` — six tasks silently counted as failures instead of raising errors. After fixing `sys.path` injection, stockmarket went from 20% to 100% (the errors were masking real passes). This was the single biggest jump in Phase 1a — not agent quality, infrastructure fix.

**Variance problem with n_trials=1:** stockindex scored 33% (1/3 pass) and stockmarket 100%. With a single trial, one wrong run looks like a persistent failure. Phase 1b's pass@5 methodology was designed to measure real accuracy, not single-run luck.

**Phase 1a failure analysis:**

*deps_dev_v1 (0/2):* Both queries require joining the SQLite `package_database` with the DuckDB `project_database`. The agent saw separate connection snippets for each but had no mechanism to combine them in one query. DuckDB ATTACH was the planned fix.

*github_repos (2/4):* `:1` → numeric precision ("0.33" not found); `:2` → agent found "swiftlang/swift" where the expected answer was "swiftandroid/swift" — plausible but wrong.

*music_brainz_20k (1/3):* `:1` → agent computed $601.44, expected $1059.46 (wrong join or filter); `:3` → wrong aggregation. `:2` passed. Consistent with a SQLite accuracy problem.

*stockindex (1/3):* `:2` → answer buried in prose, not stated first (validator checks first 200 chars); `:3` → missed NSEI (India NSE index).

---

## Phase 1b: 48.5% (2026-05-30)

Two changes from Phase 1a:
1. **DuckDB ATTACH preamble** — when a task has both DuckDB and SQLite connections, `run_trial` auto-injects the ATTACH idiom:
   ```python
   conn.execute("ATTACH '/path/to/other.db' AS alias (TYPE SQLITE)")
   # query: SELECT ... FROM duck_table JOIN alias.sqlite_table ON ...
   ```
2. **pass@5 scoring** — 5 trials per query instead of 1. Per-query rate = `passes / 5`.

**Full per-query results:**

| Query | 0 | 1 | 2 | 3 | 4 | Pass rate |
|-------|---|---|---|---|---|-----------|
| deps_dev_v1:1 | ❌ | ❌ | ❌ | ❌ | ❌ | **0%** |
| deps_dev_v1:2 | ✅ | ❌ | ❌ | ❌ | ❌ | **20%** |
| github_repos:1 | ✅ | ❌ | ❌ | ❌ | ❌ | **20%** |
| github_repos:2 | ❌ | ❌ | ❌ | ❌ | ❌ | **0%** |
| github_repos:3 | ✅ | ✅ | ✅ | ✅ | ✅ | **100%** |
| github_repos:4 | ❌ | ✅ | ✅ | ✅ | ✅ | **80%** |
| music_brainz_20k:1 | ❌ | ❌ | ❌ | ❌ | ❌ | **0%** |
| music_brainz_20k:2 | ❌ | ❌ | ❌ | ✅ | ❌ | **20%** |
| music_brainz_20k:3 | ❌ | ❌ | ❌ | ❌ | ❌ | **0%** |
| stockindex:1 | ✅ | ✅ | ✅ | ✅ | ✅ | **100%** |
| stockindex:2 | ✅ | ✅ | ✅ | ✅ | ✅ | **100%** |
| stockindex:3 | ✅ | ✅ | ✅ | ✅ | ✅ | **100%** |
| stockmarket:1 | ✅ | ✅ | ✅ | ✅ | ✅ | **100%** |
| stockmarket:2 | ✅ | ✅ | ✅ | ✅ | ✅ | **100%** |
| stockmarket:3 | ❌ | ✅ | ❌ | ✅ | ❌ | **40%** |
| stockmarket:4 | ✅ | ✅ | ✅ | ✅ | ✅ | **100%** |
| stockmarket:5 | ✅ | ❌ | ❌ | ✅ | ❌ | **40%** |

**Dataset scores:**

| Dataset | Phase 1a | Phase 1b | Δ | Notes |
|---------|----------|----------|---|-------|
| deps_dev_v1 | 0% | **10%** | +10pp | ATTACH helped :2 (1/5 pass); :1 still 0/5 |
| github_repos | 50% | **50%** | 0 | :3 and :4 stable; :1 and :2 still failing |
| music_brainz_20k | 33% | **7%** | -26pp | n=1 fluke in 1a; structural prompt issue revealed by pass@5 |
| stockindex | 33% | **100%** | +67pp | n=1 noise in 1a; now perfectly stable |
| stockmarket | 100% | **76%** | -24pp | n=1 overestimate in 1a; :3 and :5 are genuinely hard |
| **Overall** | **43.3%** | **48.5%** | **+5.2pp** | |

**The headline +5.2pp improvement is mostly noise stabilization.** stockindex's jump from 33% to 100% accounts for ~13pp of raw improvement, but it was never really 33% — that was a single bad trial. The "true" Phase 1a underlying score was probably already close to 48%, masked by single-trial variance. The Phase 1b methodology gives us a stable floor.

**What actually improved:** ATTACH preamble marginally helped deps_dev_v1:2 (0→1/5). The rest of the delta is methodology, not agent quality.

**What Phase 1b revealed:**
- music_brainz_20k is structurally broken — consistently 7-10s response times with wrong answers on 2/3 queries. The model is answering from context (it has data descriptions in the prompt) rather than actually querying the database. ATTACH didn't help because the issue is not federation — it's that the model doesn't run the query at all.
- deps_dev_v1:1 is a harder problem than ATTACH alone can solve. 0/5 with 60-170s run times means the agent tries (and fails) — it's not a prompt-read failure like music_brainz.
- github_repos:2 is a consistent 0/5. Needs failure analysis.

---

## Phase 4: 54.0% (2026-05-30)

The full LabRat agent stack — `AgentLoop` + multi-DB `ToolContext` + the data tools registry (`list_tables`, `describe_table`, `search_columns`, `sample_rows`, `column_stats`, `run_sql`, `explain_sql`, `attach_database`) — mounted as an MCP server inside `claude --print --strict-mcp-config`. Driver: `claude-mcp` (Max-plan billing). Same model (claude-sonnet-4-6), same 17-query suite, same pass@5 methodology, same stratified scoring.

**Headline:** **54.0% overall · +5.5pp over the 48.5% Phase 1b baseline.** This is the measured value of LabRat's tool layer on this benchmark.

**Per-dataset breakdown:**

| Dataset | Phase 1b raw-bash | Phase 4 claude-mcp | Δ |
|---------|------------------|--------------------|---|
| **deps_dev_v1** | 10% | **40%** | **+30pp** |
| github_repos | 50% | 40% | −10pp |
| music_brainz_20k | 7% | 13% | +6pp |
| stockindex | 100% | 87% | −13pp |
| **stockmarket** | 76% | **88%** | **+12pp** |
| **Overall** | **48.5%** | **54.0%** | **+5.5pp** |

**Per-query pass rates (Phase 4):**

| Query | Pass rate | Notes |
|-------|-----------|-------|
| deps_dev_v1:1 | 0% (0/5) | All 5 trials produce specific but wrong package names. Closer than Phase 1b but still no exact match. |
| deps_dev_v1:2 | **80%** (4/5) | **+60pp from Phase 1b** (was 20%). The biggest single-query lift in the run. |
| github_repos:1 | 0% (0/5) | Precision: agent's values don't round to 0.33 exactly. |
| github_repos:2 | 0% (0/5) | Same `swiftandroid/swift` confusion as Phase 1b. |
| github_repos:3 | 100% (5/5) | Stable. |
| github_repos:4 | 60% (3/5) | Two trials drifted to `tensorflow/tensorflow`. |
| music_brainz_20k:1 | 0% (0/5) | All return `$601.44` (same wrong answer as Phase 1b). |
| music_brainz_20k:2 | 40% (2/5) | Three return `Amazon Music` vs. truth `iTunes`. |
| music_brainz_20k:3 | 0% (0/5) | All return `Systemisch bled` vs. truth `Zo gaat het leven aan je voor`. |
| stockindex:1 | 80% (4/5) | One formatting fail — right answer not in first 200 chars. |
| stockindex:2 | 80% (4/5) | Same formatting fail. |
| stockindex:3 | 100% (5/5) | Stable. |
| stockmarket:1 | 100% (5/5) | Clean. |
| stockmarket:2 | 100% (5/5) | One trial originally hit the 600s timeout; reran cleanly. |
| stockmarket:3 | 40% (2/5) | Genuinely hard — semantic disagreement on `Synthesis Energy Systems`. |
| stockmarket:4 | 100% (5/5) | One trial originally hit the Max-plan session limit; reran cleanly. |
| stockmarket:5 | 100% (5/5) | Originally 0/5 from the session-limit reset; reran cleanly. |

### What the tool counts say

The most revealing column. Per-trial averages over passing and failing trials combined:

| Dataset | Avg tool calls | Avg latency | Behaviour |
|---------|----------------|-------------|-----------|
| deps_dev_v1 | **16.2** | 130s | Deep cross-DB exploration: list_tables → attach_database → multi-step run_sql with iterative refinement. The tool layer is doing real work. |
| stockmarket | 11.1 | 136s | Schema discovery + targeted SQL. |
| github_repos | 10.1 | 62s | Lighter exploration; queries finish fast on the single-DB DuckDB. |
| stockindex | 9.7 | 89s | Moderate. |
| music_brainz_20k | **3.1** | 13.5s | **The "answer-from-context" pattern from Phase 1b is still here.** Sonnet has the LabRat tools and chooses not to use them — same wrong answers as raw-bash. |

### Infrastructure caveats (and the importance of reruns)

The first pass of the 85-trial run produced 7 infrastructure failures that polluted the raw aggregate (giving 48% instead of 54%):

- **5 trials of `stockmarket:5`** all returned `"You've hit your session limit · resets 7:30pm (America/Vancouver)"` as their "answer" text — the trial completed in ~3 seconds with the Max-plan session-limit error landing in the validator. These are not measurement failures; they are budget failures.
- **1 trial of `stockmarket:4`** (trial 4) hit the same session limit mid-trial (276s in).
- **1 trial of `stockmarket:2`** (trial 3) hit the 600s wall-clock timeout — borderline. The agent might have completed given more time.

All 7 were trimmed from `trials.jsonl` and re-run after the session limit reset. **All 7 reruns passed.** That means the original infrastructure failures were 100% recoverable — they reflected our compute budget, not the model's ability. The corrected aggregate is the honest number.

This is a real operational lesson: **a Max-plan run of this size is large enough to bump the per-session usage cap**, and any benchmark harness needs to detect "session limit" error text in trial output and not count it toward pass rates. See `docs/dab-progress-report.md` → `decisions.md` for the fix that should land in the harness.

### Where the tool layer wins

- **deps_dev_v1 (+30pp)** — the hardest cross-DB query type in the suite. Phase 1b raw-bash got 1/10 trials right (10%). Phase 4 with LabRat tools gets 4/10 (40%). The single biggest lift comes from `deps_dev_v1:2`: 20% → 80%. The agent calls `attach_database` to bring SQLite into the primary DuckDB, then does an iterative JOIN+filter exploration with `run_sql`. This is exactly the value proposition of a tool registry.
- **stockmarket (+12pp)** — once the session-limit casualties are out, the agent's schema-introspection phase pays off on the genuinely-hard queries (`stockmarket:3`, `stockmarket:4`). On the easier queries (`:1`, `:2`, `:5`) raw-bash already does well, and the tool overhead is roughly neutral.

### Where the tool layer doesn't help

- **music_brainz_20k (+6pp, still 13%)** — the answer-from-context failure mode is *unchanged* from Phase 1b. The agent has the tools, the prompt explicitly surfaces the SQLite attachable, and Sonnet still chooses to hallucinate (`$601.44`, `Systemisch bled`) instead of querying. This is an **instruction-following problem, not a tool problem**. A targeted prompt change ("you MUST run a query before answering; do not answer from prior knowledge") would likely recover several queries.
- **github_repos (−10pp)** — minor regression dominated by `github_repos:1` (precision issue: 5 trials all produce values that don't round to 0.33 exactly) and `github_repos:2` (5/5 trials reproduce the Phase 1b `swiftandroid/swift` mistake — model prior pulls toward the more famous repo).
- **stockindex (−13pp)** — pure formatting: two failed trials had the right answer but buried after the 200-char prefix the validator inspects. Adding "state the final answer on the first line" to the system prompt would likely recover both and push stockindex to 100%.

### Honest read

**+5.5pp is a real, measurable contribution from the tool layer.** It is concentrated on hard cross-DB queries (deps_dev_v1) and harder single-DB queries (stockmarket). It is essentially zero or negative on easy single-DB queries (stockindex), where the tool exploration overhead can't pay back.

**Most of the remaining 46% failure is semantic, not infrastructural.** Both raw-bash and the LabRat agent produce the same wrong answers on the same queries (`$601.44` on music_brainz:1; `swiftandroid/swift` on github_repos:2; `Systemisch bled` on music_brainz:3). The tool layer doesn't change the model's mental model — it just gives it a path to compute answers when it chooses to use one. On music_brainz the model often chooses not to.

The natural Phase 5 work:
1. **Prompt instruction-following on music_brainz** — force-query rule, expected to recover several queries.
2. **Output formatting** — "state final answer on first line" to recover the 2 stockindex trials.
3. **Session-limit detection** — harness should treat `"You've hit your session limit"` as an infra failure (re-runnable), not a semantic failure (counted).

---

## Phase 5: Full 54-query DAB run — submitted 58.0%, corrected to 50.5% (2026-05-31 / 2026-06-01)

> ⚠️ **Correction (2026-06-03).** This run was submitted to the DAB leaderboard at 58.0%, but a trace audit (prompted by the maintainers' review on PR #54) found that the `claude-mcp` harness left the agent unsandboxed, so on some queries it read the benchmark's answer-key files (`ground_truth.csv`/`validate.py`) off disk or pulled external labels (`load_dataset("ag_news")`). **18 of 270 trials are contaminated and withdrawn; the corrected score is 50.5%.** See [Contamination audit and correction](#contamination-audit-and-correction-2026-06-03) below before relying on any figure in this section. The per-dataset numbers below are the **original (contaminated)** values, kept as the historical record and corrected inline where they changed.

All 12 official datasets, all 54 queries, pass@5, claude-sonnet-4-6, claude-mcp driver (LabRat MCP server mounted in `claude --print`, Max-plan billing). Run dir: `runs/dab/dab-1780210698/`. 270 trials. Required four resume cycles across Max-plan session windows.

**Headline (corrected):**

| Agent | Score | Notes |
|---|---|---|
| MinusX | 63.1% | Official leaderboard top |
| Altimate Code | 60.4% | |
| Spacedock | 57.7% | |
| **LabRat (this run, corrected)** | **50.5%** | submitted 58.0%; −7.5pp after withdrawing 18 contaminated trials |

The originally-reported 58.0% would have edged Spacedock; the corrected 50.5% does not, and we are not claiming a leaderboard placement until the clean sandboxed re-run lands. The substrate plumbing (Postgres + Mongo cross-DB) was built end-to-end over the prior 48 hours and does work mechanically; what the contamination invalidates is the *score-level* claim, agnews above all.

### What shipped between Phase 4 and Phase 5

Three pieces, all in commits since `6748cc4`:

1. **Item 1 — harness-side infra-failure detection.** `run_trial` recognises `"You've hit your session limit"`, `"Credit balance is too low"`, and the `_DAB_TIMEOUT` sentinel; emits `reason="infra:<tag>"` on the matching trials; `aggregate()` excludes them so Max-plan budget artefacts no longer pollute the score. Resume auto-retries infra-marked trials.
2. **Phase 2 — PostgreSQL support.** `env.py` emits postgres `db_clients` entries as `AttachSpec(path="host=localhost dbname={db_name}", db_type="postgres")`. The existing `attach_database` tool calls into DuckDB's `postgres` extension, no new tool needed. Unlocks bookreview, crmarenapro, googlelocal, pancancer_atlas, patents — 26 of the 37 net-new queries.
3. **Phase 3 — MongoDB support.** New `load_mongo_collection` tool (materialize a Mongo collection into a DuckDB TEMP table; nested fields become STRUCTs queryable via dot notation). `MongoSpec` field on `DabTaskEnv`. `env.py` handles `db_type=mongo` → `MongoSpec(database=db_name)`. Unlocks agnews and yelp — the remaining 11 queries. New dep: `pymongo>=4.17.0`.

### Per-dataset scores

| Dataset | Pass rate | DB stack | Comment |
|---------|-----------|----------|---------|
| agnews | ~~95%~~ → **15%** | Mongo + SQLite | **Contaminated — 16/20 trials read answer key / loaded HF labels; withdrawn** |
| bookreview | ~~93%~~ → **87%** | Postgres + SQLite | Phase 2 at scale; 1 contaminated trial withdrawn |
| crmarenapro | **82%** | SQLite × 3 + DuckDB × 2 + Postgres (6 DBs!) | Clean. The hardest dataset in the benchmark, and one of LabRat's strongest results |
| stockindex | **100%** | DuckDB + SQLite | Clean. Up from 87% in Phase 4 — formatting issue resolved itself |
| stockmarket | 80% | DuckDB + SQLite | Clean. Within noise of Phase 4's 88% |
| pancancer_atlas | 67% | Postgres + DuckDB | Clean. Postgres + DuckDB cross-DB, solid |
| github_repos | 50% | DuckDB + SQLite | Clean. Matches Phase 1b — same swiftandroid/swift miss persists |
| googlelocal | 50% | Postgres + SQLite | Clean. 2 queries clean, 2 stubbornly fail |
| yelp | ~~63%~~ → **60%** | Mongo + DuckDB | Phase 3 at scale, mixed; 1 contaminated trial withdrawn |
| deps_dev_v1 | 10% | DuckDB + SQLite | Clean. Regressed from Phase 4's 40% — likely stochastic on n=10 |
| music_brainz_20k | 7% | DuckDB + SQLite | Clean. Answer-from-context failure mode persists — same `$601.44`, `Systemisch bled` answers |
| patents | **0%** | Postgres + SQLite | Clean. Sonnet ceiling: CPC-code lookup the model just doesn't crack |
| **Overall** | ~~58.0%~~ → **50.5%** | | Stratified mean of per-dataset means (corrected) |

### Per-query highlights

**25 of 54 queries scored a perfect 5/5 as submitted** (fewer after the agnews withdrawal). Notably, 9 of 13 crmarenapro queries are 100% (the dataset has 6 databases involved); all 3 stockindex queries are 100%; pancancer_atlas:2/3 100%. (agnews:1/2/3 also showed 100% but are contaminated — see the audit below.)

**11 of 54 queries scored 0/5.** patents:1/2/3 (Sonnet ceiling on CPC-code lookup), music_brainz_20k:1/3 (answer-from-context), github_repos:1/2 (precision + wrong-fork), deps_dev_v1:1, pancancer_atlas:1, crmarenapro:12, googlelocal:2/3.

The 0% queries cluster around two failure modes:
- **Precision validators** rejecting close-but-not-exact answers (github_repos:1 wants 0.33; deps_dev_v1:1 wants exact dependency-graph package names).
- **Sonnet's mental model** — answers persistently wrong even with full tool access (music_brainz semantics; patents CPC codes; github_repos:2's swiftandroid/swift).

### What this validates

1. **The MCP-driver path scales.** 270 trials across 54 queries, 4 different DB types, 12 datasets — all driven by `claude --print --mcp-config` against `labrat.mcp.server`. No protocol surprises.
2. **Phase 2 (Postgres) works at scale.** crmarenapro 82%, pancancer_atlas 67%, bookreview 87% (corrected) — the existing `attach_database` tool routing into DuckDB's `postgres` extension is a clean reuse of infrastructure. These datasets are uncontaminated.
3. **Phase 3 (Mongo) plumbing works; its score-level validation is on hold.** The materialize-into-DuckDB pattern lets the agent treat Mongo collections like any other table, including JOINs against attached primaries — and it did so in clean trials. But the headline evidence used to be "agnews 95%," and agnews is exactly where the contamination lives (corrected to 15%). Mongo support cannot be claimed validated-by-score until the sandboxed re-run; yelp (60% corrected) is the remaining Mongo signal.
4. **crmarenapro is the strongest substrate evidence in the run.** 6 databases (SQLite × 3, DuckDB × 2, Postgres × 1) requiring multi-DB orchestration. 82% pass rate. Raw-bash with prompt-engineered preambles wouldn't have built up the right ATTACH topology reliably.

### What this exposes

1. **Sonnet ceiling on hard semantic queries.** music_brainz_20k stays at 7% (same `$601.44` answer Phase 1b had). patents stays at 0% across all 3 queries. The tool stack doesn't fix the model's mental model — only force-querying prompt changes might recover music_brainz.
2. **Stochasticity on n=5.** deps_dev_v1 was 40% in Phase 4, 10% here. github_repos:4 was 60% in Phase 4, 100% here. Pass@5 is a noisy estimator — larger n would tighten the dataset scores by several percentage points.
3. **Max-plan session limits make a 270-trial run non-trivially operational.** This run required 4 resume cycles across a ~30-hour wall-clock window. The auto-retry-on-resume helps, but a smarter harness (wait-for-reset + sleep) would let one `eval_dab.py` invocation finish unattended.

### Contamination audit and correction (2026-06-03)

When the DAB maintainers reviewed PR #54 they asked for the full per-trial traces, noting that an unusually high agnews score is a classic data-leakage tell. Auditing our own saved `claude --print` transcripts confirmed the leakage — and the cause is our harness, not the benchmark.

**Root cause: the agent was never sandboxed.** The `claude-mcp` driver ran:

```
claude --print --strict-mcp-config --mcp-config <f> \
       --model claude-sonnet-4-6 --permission-mode bypassPermissions ...
```

`--strict-mcp-config` only constrains MCP configuration. With `bypassPermissions` and **no `--allowedTools`/`--disallowedTools`**, the agent retained the full Claude Code native toolset — Bash, WebFetch, `Task`/subagents, Read/Write — alongside the LabRat MCP server. Our DataAgentBench checkout, including every `validate.py` and `ground_truth.csv`, was on the same filesystem and readable. We intended the MCP server to be the sole data interface; it was one tool among many.

**What the traces show** (verbatim):
- Reading the answer key: `cat .../query_agnews/query3/validate.py`, with a subagent reporting *"The benchmark ground truth from `validate.py` is `GROUND_TRUTH = 336.6363636363636`."*
- Loading external labels: `load_dataset("fancyzhx/ag_news")`, mapping `article_id → label` — one trial states *"I solved this by mapping article_ids to categories using the HuggingFace AG News labeled dataset."*

**Scope — audited all 270 trials.** 18 accessed answer-key/validator files or external labels:

| Dataset | Contaminated / matched trials |
|---|---|
| agnews | **16 / 20** |
| bookreview | 1 / 15 (`bookreview:3` t4 — subagent read `validate.py`) |
| yelp | 1 / 35 (`yelp:1` t0 — enumerated `query1/ground_truth.csv`) |
| all 9 others | 0 |

The audit scans the full transcript text (Bash inputs, tool results, **and** `Task`-subagent returns) — a Bash-only scan undercounts because delegated work surfaces in tool-result text, not parent Bash calls.

**Correction.** Withdrawing the 18 contaminated passes (counting them as non-passes, leaving every other trial untouched) recomputes the stratified mean **58.0% → 50.5%**: agnews 95%→15%, bookreview 93%→87%, yelp 63%→60%, the other nine datasets unchanged. Our local recompute reproduces the submitted 58.0% exactly, which cross-checks the scoring.

**Disclosure & remediation.** Disclosed on PR #54 with a scrubbed 270-trial trace bundle (`runs/dab/dab-1780210698/trace_bundle/` — full LLM history + every tool I/O, `manifest.json` per-trial contamination flags, `CONTAMINATION_AUDIT.md`). We withdrew the 18 contaminated trials and asked maintainers to verify. **The clean replacement requires re-running with the agent sandboxed: tool access restricted to the MCP server (block Bash/WebFetch/Task via `--allowedTools`), the benchmark repo off the agent's filesystem, and no network egress — so answer-key and external-label access are impossible by construction.** This is the top DAB priority, ahead of any Phase 6 score-chasing.

### Reproducibility

```bash
uv run python scripts/eval_dab.py \
  --driver claude-mcp \
  --n-trials 5 \
  --datasets agnews,bookreview,crmarenapro,deps_dev_v1,github_repos,googlelocal,music_brainz_20k,pancancer_atlas,patents,stockindex,stockmarket,yelp
# When the run hits a session limit, simply:
uv run python scripts/eval_dab.py --output-dir runs/dab/dab-<id>
# (auto-retries infra trials; safe to fire as many times as needed)
#
# NOTE: this command reproduces the *contaminated* run. The clean re-run must
# additionally sandbox the agent (MCP-only --allowedTools, benchmark repo off
# the agent's filesystem, no network). See "Contamination audit" above.
```

---

## Honest critique

### What these numbers mean

**54.0% on 17/54 queries is not a leaderboard number.** The DAB leaderboard scores (MinusX 63.1%, Altimate 60.4%) are over all 54 queries across 4 database types. We've only run the DuckDB+SQLite subset. PostgreSQL and MongoDB datasets are not yet supported.

**The 48.5% number was the raw-Claude floor; the 54.0% number is the LabRat tool layer.** The +5.5pp gap quantifies the contribution of the tool registry. Phases 2 (PostgreSQL) and 3 (MongoDB) extend coverage to the full 54-query official suite.

**pass@5 is more lenient than pass@1.** If we reported binary pass/fail per query (any 1 of 5 passes = query passes), 13 of 17 queries would count as passing (76%). The leaderboard uses pass@1 as its primary metric. Our 48.5% is the stricter mean-pass-rate measure. The actual binary-pass@5 figure would be our ceiling for a single-run submission.

**The scoring formula matters.** Stratified scoring (mean of dataset means) treats deps_dev_v1 (2 queries, 10%) the same weight as stockmarket (5 queries, 76%). A query-weighted mean would give a different number. The official DAB leaderboard uses stratified scoring, so that's what we report.

### Where we genuinely struggle

**music_brainz_20k (7%):** The fast-fail pattern (7-10s per trial) is the clearest signal. When the model runs the query via Bash, trials take 30-170s depending on complexity. When it answers from context, it's done in under 10s. Two of three music_brainz queries are answered from context — the model reads the schema description, the sample data hint, and constructs a plausible answer without running the actual JOIN. The answer is wrong.

Root cause candidates:
- The db_description file for music_brainz includes enough schema detail that the model thinks it can answer directly
- The ATTACH preamble is injected (music_brainz has DuckDB+SQLite) but the model doesn't use it
- Possible prompt structure issue: the preamble shows how to connect but doesn't force the model to actually run a query

**deps_dev_v1:1 (0%):** The query asks for a specific version constraint in a package dependency graph. The agent runs queries (60-170s = real execution) but returns wrong answers. The cross-DB JOIN requirement is there (SQLite packages + DuckDB projects), and ATTACH partially helps (:2 gets 1/5), but query 1 appears to need a more complex traversal than a simple JOIN provides.

**github_repos:2 (0%):** Consistently fails. No trials pass across 5 attempts. From Phase 1a analysis, this asked for a specific repository name where the agent returned a similar but wrong answer ("swiftlang/swift" vs "swiftandroid/swift"). With 5 trials all failing, this is a systematic gap — the target repo may be rare enough that the model's prior knowledge pulls it toward the more famous fork.

---

## What comes next

**Sequencing decision (2026-06-03):** the clean re-run realistically takes *many days* across Max-plan session-limit failures, so we land the highest-impact DAB changes **first**, kick the run off, and only *then* go deep on broader agent/product work (the Anthropic-article-derived Scent/Trail layer below, tracked in `FEATURE_ROADMAP.md` and north-star §8). Everything in "Pre-run gate" and "Pre-run score levers" should be in `master` before kickoff; everything under "Post-run" happens while the run grinds.

### Pre-run gate — MUST land before kickoff (else the run is invalid again)

The contamination root cause was an unsandboxed agent. Close it, in priority order:

1. **Tool allowlist (the actual fix).** In `_run_trial_claude_mcp`, add `--allowedTools "mcp__labrat"` and `--disallowedTools "Bash,WebFetch,WebSearch,Task,Read,Write,Edit,NotebookEdit"`. `--permission-mode bypassPermissions` alone is **not** a sandbox — it kept the full Claude Code toolset live.
2. **Filesystem isolation.** Run the subprocess from an empty scratch `cwd`; the MCP server already gets DB paths via `LABRAT_MCP_CONNECTIONS`, so the agent never needs the DAB checkout on its path. Keep `validate.py`/`ground_truth.csv` outside `cwd` — ideally copy only the DB files into the scratch dir.
3. **No network egress.** Run in a container / `unshare -n` so `load_dataset("ag_news")` can't reach HuggingFace even if a tool slips. Belt-and-suspenders once Bash/WebFetch are gone.
4. **Standing contamination detector.** Promote the `flags()` scan from `scripts/_build_dab_trace_bundle.py` into a post-trial check: scan each trace for `{ground_truth, validate.py, load_dataset, huggingface}`, auto-mark `reason="contaminated"` (excluded from `aggregate()`), and warn loudly. Makes silent leakage impossible to ship ever again.
5. **Server-side tool-call logging** in `src/labrat/mcp/server.py` (gated on `LABRAT_MCP_LOG_DIR`) — one `{tool, input, output, ok, latency_ms}` line per dispatch — so traces are first-class, not reconstructed from `~/.claude`.

Items 1+2 close the hole; 3–5 are the rigor layer. ~1–2 hours total.

### Pre-run score levers — cheap, high-ROI, land before kickoff

These are prompt-only or small and target the worst *clean* datasets, so they go in the same pre-run PRs:

1. **Force-query rule for music_brainz (7%):** *"Do not answer from memory. You MUST run a query and print the result before answering."* Signal it worked: trial times rise from ~7s to 30s+.
2. **Answer-format guidance:** *"State your final answer as a plain value on the last line."* (recovers prose-buried answers; cheap insurance against validator-format misses).
3. **Anti-pattern bullets** in the system prompt (dialect gotchas, grep-before-assuming-a-table).

Two heavier ADOPT builds (north-star §9) are the highest-impact *score* levers but are real work — **schema-linking (NL→relevant-tables-only)** and **mechanically-verified joins (probe before trusting)**. They directly attack deps_dev_v1 (10%), music_brainz (7%), and patents (0%), which are grounding/mental-model failures, not tooling gaps. **Decision: land them before kickoff if you can afford ~a day; otherwise they become the first post-kickoff agent work and ship in the *next* re-run.** Do **not** encode answer-shaped per-dataset "gotchas" docs for DAB — that's the leakage smell we just fixed; keep DAB grounding to schema/grain/join *structure* only.

### Post-run — agent & product depth (while the run grinds; from the Anthropic self-service-analytics article)

Anthropic's data team reports **<21% accuracy without a skills/reference-doc layer, >95% with it** ([article](https://claude.com/blog/how-anthropic-enables-self-service-data-analytics-with-claude)). That layer is exactly our under-built **Rat Maze (Scent + Trail)**. These are product-level, not DAB-overfit, and are detailed in north-star §8:

- **Reference docs "written for retrieval by an LLM"** — Quick Reference / Dimensions / Key Tables (grain + joins) / **Gotchas** / Best Practices / Cross-refs; routing triggers, not recipes. The concrete shape of **Scent**.
- **Curation > raw retrieval** — their ablation: raw grep over thousands of prior queries moved accuracy <1pp. Re-ranks our `search_query_history` (low-leverage as raw access); the value is distilling `history/` into **Trails**.
- **Correction-harvesting loop** — skills decay 95%→65% in weeks unscented; a scheduled agent that turns corrections into doc PRs keeps the Maze fresh. Maps onto our self-healing `memory/` + `/schedule`.
- **Provenance footer** (source tier → freshness → ownership) — cheap trust UI for Pillar 2 ("spread the cheese").
- **Eval-as-telemetry + ablation discipline** — store every eval result with skill-version/git-SHA/model-ID; ablate each change against the fixed smoke set (they hit *three net-negative doc iterations* — measure, don't stack).

### Phase 1c (prompt iteration)

Before adding database types, there are prompt improvements that could recover points on the existing 5 datasets:

1. **Force-query instruction for music_brainz** — add an explicit rule: "Do not answer from memory. You MUST run a query and print the result before answering." Check if trial times increase from 7s to 30s+ (that's the signal it worked).

2. **deps_dev_v1:1 failure analysis** — run a single trial with verbose output (`--max-turns 30`) and inspect what query the model runs and why it fails. Is the ATTACH working? Is the traversal logic wrong?

3. **github_repos:2 ground truth check** — manually query the DB to find the expected answer and trace backward to understand why the agent consistently picks the wrong fork.

4. **Answer-format guidance** — stockindex:2 buried the answer in prose in Phase 1a. This may still be an issue for borderline queries. Add: "State your final answer as a plain value on the last line of your response."

### Phase 2 (PostgreSQL datasets)

Add PostgreSQL connection examples to `run_trial` for: `agnews` (4 queries), `bookreview` (3), `crmarenapro` (13), `googlelocal` (4), `yelp` (7). That's 31 more queries, covering the 5 PG-only datasets.

The preamble snippet would follow the same pattern as DuckDB/SQLite:
```python
import psycopg
conn = psycopg.connect("host=localhost dbname=<name> user=<user> password=<pass>")
```

This brings the total to 48/54 official queries (89%) and makes the overall score comparable to leaderboard submissions.

### Phase 3 (MongoDB datasets)

`pancancer_atlas` (3 queries) and `patents` (3 queries) use MongoDB. These require a different preamble pattern — `pymongo` instead of DuckDB. The scoring is the same. Once Phase 2 is complete, Phase 3 adds the final 6 queries for a full 54-query run.

### Phase 4 (LabRat tools)

Route DAB through `LabRatAgentDriver` — the actual LabRat agent loop with `list_tables`, `describe_table`, `run_sql`, `attach_database`, and `load_mongo_collection` (Mongo → DuckDB materializer). The Phase 1b→Phase 4 delta is the quantified value of LabRat's tool stack.

Expected gains:
- `attach_database` tool makes cross-DB joins first-class and discoverable (vs. injecting a preamble snippet the model may ignore)
- `describe_table` gives the model live schema introspection rather than static descriptions in the prompt
- `list_tables` lets the model discover what's available rather than relying on db_description.txt

The three candidate architectures for Phase 4 (documented in `decisions.md`): MCP server exposing the LabRat tool registry, headless `labrat run-task` CLI, or `--tools` JSON schema injection into the `claude` CLI.

---

## Gotchas and operational notes

**Local repo has 5 unofficial extras.** `~/repos/DataAgentBench` has 17 directories, not 12. The extras (`civic_unstructured`, `cve`, `imdb`, `krama`, `usaspending`) are not in the official benchmark and must not be included in official runs.

**Scoring is mean pass rate, not binary.** A query with 2/5 passes scores 0.4, not 1.0. This is the official leaderboard methodology.

**Resume crashed runs.** `eval_dab.py` appends to `trials.jsonl` and skips completed `(task_id, trial_num)` pairs on restart:
```bash
uv run python scripts/eval_dab.py --output-dir runs/dab/dab-<id>
```

**music_brainz fast-fail is a prompt issue, not an infra issue.** If you see 7-10s times on music_brainz trials in a future run, the model is not querying — check the prompt force-query instruction landed correctly.

**deps_dev_v1:1 response times distinguish it from music_brainz.** It runs 60-170s (real queries), it just returns wrong answers. Don't conflate the two failure modes.

**Phase 1b smoke check passed.** The 9-task ADE smoke suite was run against the baseline before merging — no ADE regressions from the DAB additions.

---

## Reproducibility

```bash
# Phase 1b run (5 datasets, n_trials=5)
uv run python scripts/eval_dab.py \
  --datasets deps_dev_v1,github_repos,music_brainz_20k,stockindex,stockmarket \
  --n-trials 5

# Quick single-trial check
uv run python scripts/eval_dab.py --n-trials 1

# Resume a crashed run
uv run python scripts/eval_dab.py --output-dir runs/dab/dab-<id>
```

The Phase 1b run output lives at `runs/dab/dab-1780121141/`. Trial-level results in `trials.jsonl`, final score in the run summary.

---

*LabRat is [open-source under AGPL-3.0](https://github.com/esagduyu/labrat). DAB integration code: `src/labrat/eval/benchmarks/dab/`. Setup: `scripts/dab_setup.py`.*
