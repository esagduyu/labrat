# LabRat on DataAgentBench: Results, Contamination Audit, and Leaderboard Acceptance

> **TL;DR (2026-06-18)** — **LabRat is on the [DataAgentBench public leaderboard](https://ucbepic.github.io/DataAgentBench/) at a stratified Pass@1 of 51.4%** (rank #10 of 18; full 54-query / 12-dataset benchmark, pass@5, claude-sonnet-4-6, LabRat tools via the MCP driver). Getting there was a four-act story this doc records end to end: a **48.5%** raw-Claude+prompt baseline (Phase 1b) → a **54.0%** measured tool-layer delta on the shared subset (Phase 4) → a full-coverage run **submitted at 58.0% that we self-audited and the maintainers independently re-validated down to 51.4%** after a harness contamination leak (Phase 5) → a **sandboxed clean re-run that independently confirms ~50%** (the sandbox gate is now permanent). Strongest single-dataset signal: **crmarenapro 82%** on a 6-database hybrid query set. The honest read: ~51% is a real, defensible, maintainer-verified number; the journey to it is a case study in benchmark contamination and how to disclose and fix it.

---

## Final outcome: accepted onto the leaderboard at 51.4% (2026-06-18)

After we disclosed the Phase 5 contamination (below), DAB maintainer **@Ruiying-Ma re-validated all 270 answers independently**, confirmed the leakage, and counted every contaminated trial as a non-pass (0-penalty). Their trace audit found **3 additional subagent-delegated leaks beyond the 18 we flagged** (21 contaminated trials total). The resulting **stratified Pass@1 of 51.4%** was accepted, and **LabRat was added to the public leaderboard** (rank #10 of 18, just below Claude Opus 4.6 ReAct at 54.7%). Per the maintainers' process, PR #54 was closed and the entry merged on their end (they keep third-party commits out of the project repo).

Our own **sandboxed clean re-run** (`runs/dab/dab-rerun-clean`, sandbox gate on) independently lands at **~50% official-12 stratified mean**, cross-confirming the accepted figure. So three numbers, three meanings — never conflate them:

| Number | What it is |
|---|---|
| ~~58.0%~~ | The original submission. **Contaminated — never cite.** |
| 50.5% | Our interim recompute withdrawing the 18 trials we found. |
| **51.4%** | **The official, maintainer-re-validated leaderboard figure. Cite this.** |

A separate sandboxed re-submission with score improvements (force-query rule, schema-linking/verify-join grounding) is planned. Full detail on each act is below.

---

## What is DataAgentBench?

[DataAgentBench](https://ucbepic.github.io/DataAgentBench/) (UC Berkeley) is a multi-database, execution-based benchmark for data agents. Unlike ADE-Bench (which tests dbt/analytics engineering), DAB tests **natural language to SQL** across heterogeneous database stacks.

The benchmark structure:
- **12 datasets**, **54 queries** total (official; the local repo contains 5 extra unofficial datasets, see gotchas below)
- **4 database types**: DuckDB, SQLite, PostgreSQL, MongoDB
- Many datasets require **cross-database joins** — e.g., a question answered by joining a DuckDB table with a SQLite table
- Success is judged by an exact-match or near-match validator per query (`validate.py` per task) — no LLM judge, no partial credit
- Scoring is **stratified**: mean of per-dataset pass rates. Each dataset contributes equally regardless of query count. Per-query rate = `passes / n_trials` (not binary)

The leaderboard as of the 2026-06-12 recompute (the neighborhood LabRat now sits in):

| # | Agent | Pass@1 |
|---|-------|--------|
| 1 | Altimate Code + GPT-5.5 + Claude Sonnet 4.6 | 71.7% |
| 2 | Altimate Code + Claude Sonnet 4.6 | 68.2% |
| 3 | Spacedock (Recce) + Claude Opus 4.8 | 67.2% |
| 4 | MinusX + Sonnet 4.6 + GPT-5.5-mini + Haiku 4.5 | 65.2% |
| … | … | … |
| 9 | Claude Opus 4.6 ReAct | 54.7% |
| **10** | **LabRat (Claude Sonnet 4.6)** | **51.4%** |
| 11 | Oracle Forge (Team PaLM) + Gemini 3.1 Pro Preview | 47.2% |

The leaders pair a stronger/secondary model or a deeper agent harness with Sonnet; LabRat is the strongest single-Sonnet-4.6 entry below the Opus ReAct baseline. (Phase 1 runs below cover only 17/54 queries and predate full coverage — kept as historical record.)

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

## Phase 5: Full 54-query DAB run — submitted 58.0% → maintainer-re-validated to 51.4% (accepted) (2026-05-31 / 2026-06-18)

> ⚠️ **Resolution (2026-06-18).** This run was submitted to the DAB leaderboard at 58.0%, but a trace audit (prompted by the maintainers' review on PR #54) found that the `claude-mcp` harness left the agent unsandboxed, so on some queries it read the benchmark's answer-key files (`ground_truth.csv`/`validate.py`) off disk or pulled external labels (`load_dataset("ag_news")`). We disclosed it; the maintainers **independently re-validated all 270 answers, found 21 contaminated trials** (the 18 we flagged + 3 subagent-delegated leaks they caught), and **accepted LabRat onto the leaderboard at 51.4%.** Our interim recompute (withdrawing just our 18) was 50.5%. See [Contamination audit and correction](#contamination-audit-and-correction-2026-06-03) below. The per-dataset numbers below are the **original (contaminated)** values, kept as the historical record and corrected inline where they changed.

All 12 official datasets, all 54 queries, pass@5, claude-sonnet-4-6, claude-mcp driver (LabRat MCP server mounted in `claude --print`, Max-plan billing). Run dir: `runs/dab/dab-1780210698/`. 270 trials. Required four resume cycles across Max-plan session windows.

**Headline (final, accepted):**

| Agent | Score | Notes |
|---|---|---|
| **LabRat (this run, accepted)** | **51.4%** | submitted 58.0%; maintainers re-validated all 270, withdrew 21 contaminated trials, accepted onto the leaderboard |

The originally-reported 58.0% would have placed higher; the accepted 51.4% lands at rank #10. The substrate plumbing (Postgres + Mongo cross-DB) was built end-to-end over the prior 48 hours and works mechanically; what the contamination invalidated was the *score-level* claim, agnews above all — now corrected and independently verified.

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

**Correction → maintainer re-validation → acceptance.** Our recompute withdrawing the 18 contaminated passes we found gave **58.0% → 50.5%** (agnews 95%→15%, bookreview 93%→87%, yelp 63%→60%, the other nine unchanged); our local recompute reproduced the submitted 58.0% exactly, cross-checking the scoring. The maintainers then **re-validated all 270 answers independently** and found **3 more leaks we'd missed — all subagent-delegated** (the contaminating access happened inside a `Task` subagent and surfaced only in tool-result text, not parent Bash calls). Counting all **21** contaminated trials as non-passes, they settled on **51.4%** and **accepted LabRat onto the leaderboard.** The 51.4%-vs-50.5% gap is the difference between their full independent re-validation of every answer and our withdraw-only recompute — 51.4% is the figure to cite.

**Disclosure & remediation.** Disclosed on PR #54 with a scrubbed 270-trial trace bundle (`runs/dab/dab-1780210698/trace_bundle/` — full LLM history + every tool I/O, `manifest.json` per-trial contamination flags, `CONTAMINATION_AUDIT.md`). The clean replacement required re-running with the agent sandboxed: tool access restricted to the MCP server (block Bash/WebFetch/Task via `--allowedTools`), the benchmark repo off the agent's filesystem, and no network egress — so answer-key and external-label access are impossible by construction. **That sandbox gate is now permanent (see "Pre-run gate" below) and the clean re-run is complete** — it independently lands at ~50%, confirming the accepted figure.

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

### Pre-run gate — ✅ IMPLEMENTED + VALIDATED (2026-06-03)

The contamination root cause was an unsandboxed agent. Shipped, TDD'd (551 tests passing), and validated live on agnews (`runs/dab/dab-1780554221/`): agnews:1 PASSes via 31 MCP-only calls (`load_mongo_collection`/`attach_database`/`run_sql`, no Bash/file/HF — the `mcp_tool_calls.jsonl` log proves it), agnews:2 honestly times out on the hard 111-article classification; contamination detector clean on both. The smoke also caught and fixed two latent bugs (relative `--mcp-config` doubling under `cwd`; the `:memory:` federation server crash that Phase 5's Bash usage had masked). Parts:

1. ✅ **Tool allowlist.** `_run_trial_claude_mcp` now passes `--allowedTools "mcp__labrat"` + `--disallowedTools "Bash,WebFetch,WebSearch,Task,Read,Write,Edit,NotebookEdit,Glob,Grep"`. `--permission-mode bypassPermissions` alone is **not** a sandbox — it kept the full Claude Code toolset live.
2. ✅ **Filesystem isolation.** The subprocess runs with `cwd=<absolute trial scratch dir>`; the MCP server gets DB paths via `LABRAT_MCP_CONNECTIONS`, so the agent never needs the DAB checkout on its path. (`scratch_dir.resolve()` up front — a relative scratch dir + cwd change otherwise doubles the `--mcp-config` path; the live smoke caught this, now covered by a regression test.)
3. ⚙️ **No network egress** (the one item that stays an *environment* step). Run in a container / `unshare -n` so `load_dataset("ag_news")` can't reach HuggingFace even if a tool slips. Not portable from Python on macOS, and moot once Bash/WebFetch are blocked — do it at kickoff via the run environment.
4. ✅ **Standing contamination detector.** `_detect_contamination()` scans each trial's output for `{validate.py, ground_truth, load_dataset, huggingface, …}`; a hit withdraws the trial as `reason="contaminated:<tag>"`, which `aggregate()` excludes alongside `infra:`. Silent leakage can never inflate the score again.
5. ✅ **Server-side tool-call logging.** `_log_tool_call` in `src/labrat/mcp/server.py` writes one `{tool, input, output, ok, latency_ms}` line per dispatch to `<LABRAT_MCP_LOG_DIR>/mcp_tool_calls.jsonl`; the driver points it at the trial scratch dir. Traces are first-class, not reconstructed from `~/.claude`.

Items 1+2 close the hole; 4+5 are the rigor layer; 3 is the environment belt-and-suspenders. The clean full re-run still has to happen — this makes it safe to run.

### Clean sandboxed re-run — ✅ COMPLETE (`runs/dab/dab-rerun-clean`)

Full 54-query official benchmark, claude-mcp, n=5, `--agent-timeout 1200`, sandbox gate on. The self-healing local loop ran to completion ("all 270 trials have a real result"). **Result: ~50% official-12 stratified mean — independently confirming the accepted 51.4%** (the run's auto-generated `report.md` shows `0.48`, but that headline includes the unofficial `civic_unstructured` dataset; recompute over the official 12 only).

**Per-dataset (clean run):** bookreview 1.00, stockindex 0.87, stockmarket 0.84, crmarenapro 0.80, github_repos 0.50, pancancer_atlas 0.47, googlelocal 0.40, yelp 0.40, deps_dev_v1 0.30 (was 10% in Phase 5 — grounding tools + n noise), agnews 0.30, music_brainz_20k 0.13, patents 0.00.

**How it ran (self-healing local loop):** `scripts/dab_rerun_tick.sh` probes Max-plan (skips cleanly if the limit is active, so it never blasts fast-fail trials), then starts/resumes `eval_dab.py --output-dir runs/dab/dab-rerun-clean` (official-scoped, idempotent, concurrency-guarded). `dab_rerun_loop.sh` fired it every **30 min** — the cheap probe means it resumes within ~30 min of a limit reset instead of waiting out a fixed window (an earlier 6h loop wasted ~1h+ per cycle). It must run locally (Max-plan OAuth + mongod + DAB checkout); a Claude Code routine on the bridge env worked until a power outage destroyed the bridge, so the local `nohup` loop was the durable path. Two scope/robustness lessons baked in: always `--datasets <12 official>` (the suite enumerates 104 queries incl. 5 unofficial extras), and a concurrency guard so a manual resume can't race the loop.

**Findings:**
- **The sandbox holds at scale.** Every trial uses MCP tools only (`mcp_tool_calls.jsonl` confirms `load_mongo_collection`/`attach_database`/`run_sql`/…); zero Bash/file/web. No contamination outside agnews.
- **agnews leaks via model *parametric memory*, not tooling.** Even fully sandboxed, Sonnet recalled the public AG News id→label mapping ("article_ids 0–29,999 = Business, label=2") and applied it via SQL. `_detect_contamination` caught and withdrew it (via the "huggingface" mention), but only catches trials that *name* the dataset — silent memorized use would pass. **agnews is intrinsically unreliable for any pretraining-exposed model**; caveat it. Benchmark-side fix: shuffle/reassign `article_id`s so memorized positions don't map to labels — worth proposing upstream.
- **Precedent (DAB PR #53, Altimate):** identical agnews `load_dataset` leak, same maintainer, sandboxed re-run **accepted onto the leaderboard** — agnews 100%→35%, stratified 68.93%→63.18%. They ran GPT‑5.5, so the contamination is model-agnostic, and our disclose→sandbox→rerun path is the accepted one — which is exactly how LabRat landed at 51.4%.

### Pre-run score levers — cheap, high-ROI, land before kickoff

These are prompt-only or small and target the worst *clean* datasets, so they go in the same pre-run PRs:

1. **Force-query rule for music_brainz (7%):** *"Do not answer from memory. You MUST run a query and print the result before answering."* Signal it worked: trial times rise from ~7s to 30s+.
2. **Answer-format guidance:** *"State your final answer as a plain value on the last line."* (recovers prose-buried answers; cheap insurance against validator-format misses).
3. **Anti-pattern bullets** in the system prompt (dialect gotchas, grep-before-assuming-a-table).

Two heavier ADOPT builds (north-star §9) are the highest-impact *score* levers — **schema-linking (NL→relevant-tables-only)** and **mechanically-verified joins (probe before trusting)**. They directly attack deps_dev_v1 (10%), music_brainz (7%), and patents (0%), which are grounding/mental-model failures, not tooling gaps. **✅ SHIPPED before kickoff (2026-06-03):** `link_schema` + `verify_join` tools (`src/labrat/agent/tools/`, registered + surfaced in both driver prompts, TDD'd). They encode only schema/grain/join *structure*, never answer-shaped content (the leakage smell we just fixed).

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

## Phase 6 — GPT‑5.5 via ChatGPT subscription: an honest experiment (2026-06-20 → 21)

We built a native `CodexSubscriptionProvider` (Responses API at `chatgpt.com/backend-api/codex/responses`, reusing the Codex CLI's `~/.codex/auth.json`) so LabRat's *own* AgentLoop — verifier included — could run on **GPT‑5.5**, which is subscription-only (no metered API). The goal: measure GPT‑5.5 vs Sonnet, measure the verifier's value, and stress the tool layer on a second model. The experiment ran its course and answered its questions; the honest summary is below. (Provider design + shipped commits: memory `project_codex_subscription_provider`; the provider is the personal/dev/benchmark path, with the metered `openai` provider as the distributable one.)

### Accuracy: GPT‑5.5 ≈ Sonnet (slightly behind on the subset)
On the 5 DuckDB+SQLite datasets (n=5, `labrat-agent` driver), GPT‑5.5 stratified to **~49%** vs Sonnet's ~53% on the same queries — **not a free win**. Different error profile: GPT‑5.5 is markedly better on deps_dev_v1 (50% vs Sonnet's 10% — dependency-graph traversal) but worse on stockindex and github_repos. music_brainz stays low on both (~20%) — the answer-from-memory failure is model-independent (force-query prompt rule still needed). A full 54-query GPT‑5.5 leaderboard number was **not** pursued — see "rate limit" below.

### Verifier ablation: no benefit on GPT‑5.5
Verify-OFF vs verify-ON on the subset (n=5) came out **49.3% vs 49.1% — a −0.2pp dead heat.** The opt-in LLM-as-judge verifier buys zero accuracy here for extra tokens. It does **not** reproduce Anthropic's +6% adversarial-review result because ours is a "does this answer address the question?" *sufficiency* gate, not a full adversarial review — and it cannot catch a *wrong-but-plausible* answer (e.g. the stockindex dirty-date miss). **Decision: keep the verifier opt-in / default-off; do not enable it for GPT‑5.5.** External evidence (Anthropic's +6% at +32% tokens / +72% latency) already justified opt-in-default-off; our own ablation confirms it's not worth turning on for this workload.

### Token economics & prompt caching (the most reusable finding)
We added per-trial token capture (the Responses `response.completed.usage` block → `TrialResult.meta`). What it revealed:
- **One DAB trial is enormous: ~625K input tokens** (≈99% re-sent context, ~5K output), over ~18 turns — because the agent loop re-sends the system prompt + all 13 tool schemas + the whole growing history (incl. a 2.5–4K-token `profile_dataset` blob) on *every* turn. A ~414-trial run exhausts the subscription rate limit (`plan_type: prolite`; the 429 body carries `resets_in_seconds`).
- **Prompt caching is automatic on the Responses API** (caches the longest stable prefix). Our measured hit rate was **~40%** — far below Sonnet-via-claude-CLI's **93%**.
- We chased the gap: research named *dropping reasoning items* as "the top cause of cache misses" for reasoning models, so we shipped **reasoning-item passback** (capture each encrypted `reasoning` item, re-emit it before its function_call). **It's verified working (the codex endpoint accepts reasoning items — no 400) but it barely moved the rate (~40% → ~41%, confounded by trial length).**
- **The real bottleneck is cache TTL eviction, not prefix instability.** Our trials run ~18 turns × ~80s ≈ **20+ minutes wall-clock**, far past the codex cache's 5–10-min in-memory window — so mid-trial the cache evicts and a perfect prefix has nothing to hit. We **cannot extend retention**: `prompt_cache_retention: "24h"` is rejected by the codex endpoint with HTTP 400 ("Unsupported parameter"). So the lever for better caching is **shorter/faster trials** (the turn cap we built + history pruning + a leaner `profile_dataset`), not more prefix work.

### Rate limit: the subscription path is benchmark-constrained
GPT‑5.5 is subscription-only, so a full 270-trial run is bound by the ChatGPT rate limit. Even on the upgraded (~$100/mo `prolite`) tier, a single overnight ~414-trial attempt hit a hard `429` and didn't recover for hours. A self-healing watchdog (`scripts/dab_codex_{tick,loop,finish}.sh`) trickles through resets, but **full GPT‑5.5 benchmarking on the subscription is impractical** — keep Sonnet (Max-plan, claude-mcp) as the full-benchmark path.

### Net conclusion
GPT‑5.5 is **not a free win** (≈ Sonnet), the **verifier doesn't help**, and the **subscription path is rate- and cache-constrained**. The experiment's lasting value is model-agnostic: real per-trial token/usage capture, the **stockindex dirty-date grounding case** (the poster child for the Scent layer — see `FEATURE_ROADMAP.md` #26b), and a set of token-efficiency levers. If chasing caching further, the path is shorter trials; given GPT‑5.5 ≈ Sonnet, that energy is better spent on the grounding/Scent layer.

---

## Phase 6b — GPT‑5.5 re-run after the Scent + workflow builds (2026-06-21)

After shipping #26a/#26b (Scent) and #30 (the workflow skill + `run_sql` self-repair), re-ran GPT‑5.5 (`labrat-agent` driver, codex provider, reasoning medium) on the same 5 DuckDB/SQLite datasets, **unbounded turns, n=2**.

**Score: 61.8% (21/34)** — up from Phase 6's ~49%. Per-dataset: deps_dev_v1 50% · github_repos 50% · music_brainz_20k 33% · **stockindex 100%** (↑↑ — Phase 6 mostly failed this dirty-date set) · stockmarket 70%. Caveat: n=2 is noisy, a dev measurement — the official leaderboard number stays Sonnet/claude-mcp **51.38%**. Note this path uses the DAB driver's *own* prompt (`_build_labrat_agent_system_prompt`), so it does **not** exercise #30's SOP/workflow tool; #30's value lives in the TUI/`run_task` path.

**Turn-cap = a clean NEGATIVE result.** Trying `--max-turns 12` to chase prompt caching scored **0%** — the agent spends ~18–30 turns (median 26 tool calls, max 69) profiling/querying and gets starved of its final-answer turn → empty/rushed answers. Unbounded recovers to ~62% **and caches better** (44% vs the capped run's 18%): longer trials reuse the growing prefix more, so capping for cache is self-defeating. **Drop the turn-cap-for-cache lever.** (Confirmed against Phase 6: the codex cache is TTL/eviction-bound, not turn-cap-tunable.)

**Rate limit confirmed (Phase 6's wall).** One heavy unbounded subset (~23M input tokens) exhausted the ChatGPT subscription limit mid-run (instant 0.0s `agent_error` on every subsequent call); resumed cleanly after reset. Subscription path stays benchmark-constrained — Sonnet/Max-plan remains the full-run path.

**Cache fix shipped (commit `0bc1427`).** Per the [OpenAI prompt-caching 201 guide](https://developers.openai.com/cookbook/examples/prompt_caching_201): the provider already did `prompt_cache_key` + reasoning-item passback, but the key was a **fresh per-trial uuid** — so the n>1 trials of one task (byte-identical prompt prefix) routed to different machines and re-paid the cold prefix every trial. Now `build_provider` threads a `cache_key` and the DAB driver passes **`task.id`**, so a task's repeated trials reuse the same warm cache (sequential DAB trials run ~7 req/min, well under the guide's ~15-RPM/key scatter threshold). Routing-only; accuracy unchanged. **A/B result: NULL on the subscription path.** Old per-trial-uuid key deps_dev_v1 cache ≈ **36.3%**; new per-task key ≈ **28.8%** (flat across all 5 trials — trial 0 cold 30.3%, trials 1–4 = 33/28/30/23%, i.e. **no cross-trial reuse**). The codex/ChatGPT-subscription endpoint evidently does **not honor `prompt_cache_key`'s routing stickiness** (consistent with it rejecting `prompt_cache_retention`); the ~30% we see is purely *within-trial* caching, which the old key already captured. The change is kept anyway — it is strictly more-correct, harmless, tested, and **would** help on the metered `openai`/real-OpenAI-API path (the guide's 60→87%), where `prompt_cache_key` is honored — but it is **inert on the subscription endpoint**. Net: re-confirms Phase 6 — subscription-path caching is hard to move; the real remaining lever (avoid context-window truncation via history pruning / a leaner `profile_dataset`) is diminishing-returns given GPT‑5.5 ≈ Sonnet and the subscription's benchmark-impracticality. **Caching thread closed** unless we switch to the metered provider. (Also audited clean: no dynamic timestamp/id in the early prefix.)

---

## Phase 7 — Cartographer grounding + prompt levers + driver parity (2026-06-22)

Three independent ablations shipped to `master` this session. Leaderboard figure is unchanged at **51.38%** (not yet resubmitted); a fresh full run is in progress (pass@1 sweeps on Sonnet + GPT-5.5 running concurrently; pass@5 submission run to follow).

### The Cartographer pre-pass (FEATURE_ROADMAP #26b) — +8pp Sonnet, ablated

The Cartographer (`maze/cartographer.py::cartograph_prepass`) is a deterministic, GT-firewalled first-contact pass. Before the agent loop, it explores each dataset's databases and writes **Scent** docs (table grain, columns, `verify_join`-confirmed joins, observed dimension values) to a hermetic scratch HOME. The agent then calls `search_reference_docs` (#26a CONSUME) to retrieve relevant sections during reasoning. The Scent docs encode only structure — never answer-shaped content.

Wired into both `labrat-agent` and `claude-mcp` drivers via `--agent-cartograph` (off by default). GT-firewalled by construction: reads only DB metadata and sampled rows, never answer-key or validator files.

**Ablation result (Sonnet, claude-mcp, tuning subset):**

| Configuration | Stratified score |
|---|---|
| tools-only (baseline) | **21%** |
| + Cartographer | **29%** (+8pp) |
| + Cartographer + prompt levers | **38%** (+17pp stacked) |

Per-dataset signal: deps_dev_v1 0%→33%, music_brainz_20k 0%→11% (Cartographer alone); stockindex 56%→44% is noise not signal. Each layer was independently ablated.

**Precedent:** Altimate's AutoContext (PR #53) achieved a similar +8pp on DAB and was accepted on the leaderboard — the disclosed/ablated grounding pre-pass is the accepted playbook.

### Prompt levers (Pillar 1) — +8pp marginal on top of Cartographer

Benchmark-safe process rules added to both driver prompts (`_dab_lever_lines`):
- **Force-query rule:** "Do not answer from memory; you MUST run a query before answering." Addresses the music_brainz fast-fail pattern (7-10s trials → 30s+ = working).
- **Repair-via-diagnostics:** `run_sql` now returns `error_category`/`hint`/`executed_sql` on errors; the prompt instructs the agent to use these for SQL self-repair.
- **Push-aggregation-into-SQL:** aggregations stay in the query, not reconstructed in the agent's head.

These stack cleanly on top of Cartographer: tools-only 21% → +Cartographer 29% → +levers 38% (+17pp, tuning subset).

### Codex/GPT-5.5 ⇄ Sonnet driver parity (submission-equivalence)

The `labrat-agent` driver now matches the `claude-mcp` driver's audit guarantees:

- **Per-call traces** (`agent_tool_calls.jsonl`): shared `append_tool_trace` writer — schema-identical to `claude-mcp`'s `mcp_tool_calls.jsonl` (`{tool, input, ok, output, latency_ms}`). A submission is trace-valid on either driver.
- **Per-trial wall-clock timeout:** `asyncio.wait_for` → `reason="infra:timeout"`, excluded by `aggregate()` and auto-retried on resume (parallel to `claude-mcp`'s `--agent-timeout` behaviour).
- Feature-by-feature parity matrix: **`docs/dab-driver-parity.md`**.

### Provider-agnostic verdict

The Cartographer mechanism is provider-agnostic — GPT-5.5 does consult `search_reference_docs` (confirmed directly from `agent_tool_calls.jsonl` traces). The **effect is Sonnet-favoring**: **+8pp on Sonnet, +0pp (neutral) on GPT-5.5** (n=2). GPT-5.5 already self-grounds exhaustively (~32 `run_sql` calls + full schema exploration per trial), making structure-only Scent redundant for it; the leaner-exploring Sonnet benefits. The leaderboard path is Sonnet/claude-mcp + Cartographer + prompt levers.

### Pre-submission: full pass@1 sweeps + lever ablations (2026-06-22)

**Full-coverage pass@1 sweeps** (de-risk before the multi-day pass@5; Cartographer + levers on, all 12 official datasets, zero infra failures on either provider):

- **Sonnet (claude-mcp): 50.1% stratified** (29/48 raw pass@1).
- **GPT-5.5 (labrat-agent/codex): 53.0% stratified** (33/54 raw pass@1).

A dead heat within n=1 noise — big per-dataset deltas are single-trial flips, consistent with "GPT-5.5 ≈ Sonnet". Both sitting ~50% at pass@1 vs the 51.38% pass@5 leaderboard → pass@5-with-Cartographer has headroom. The GPT-5.5 sweep only completed via the self-healing loop fighting 429 rate-limit bursts — not viable for a clean leaderboard run. **Decision: leaderboard run = claude-mcp/Sonnet.**

**Plan-discipline lever (#12) ablation — NET-NEGATIVE, discarded.** Adding a brief-numbered-plan + verify-before-finish instruction to the claude-mcp prompt (parity with the labrat-agent prompt), ablated on top of Cartographer (n=3, tuning subset deps_dev_v1/music_brainz_20k/stockindex): **−12.5pp** (29%→17%), driven by stockindex 56%→11% (−44pp; over-planning wastes turns on a dataset already working). Branch deleted; do not retry on the claude-mcp path.

**`--hints` bug fix + lever.** DAB ships `db_description_withhint.txt` per dataset (benchmark-provided data-quirk guidance, e.g. music_brainz "tracks has duplicates → entity resolution"); this is a declared leaderboard "Hints" axis and top teams including Altimate declare Hints: Yes, stacking it with their own AutoContext doc. Our `--hints` flag was broken — it loaded the hints file *instead of* the base description (dropping the schema); fixed to append (base + "\n\n" + hints) per DAB's `run_agent.py`. All prior runs, including the accepted 51.38%, were hints-off. A hints-on vs hints-off ablation on top of Cartographer is in progress; if net-positive the submission runs `--agent-cartograph --hints` and declares Hints: Yes — a distinct declared category from the hints-off 51.38%.

### Final submission — LIVE on the leaderboard at #8 / 60.88% (2026-06-24)

**LabRat is #8 of 21 on the public DataAgentBench leaderboard at a stratified Pass@1 of 60.88%** (shown as 60.9% on the board), submitted as a new entry "LabRat (Claude Sonnet 4.6 + Cartographer)". The prior "LabRat (Claude Sonnet 4.6)" entry at 51.38% is still on the board at #13 — both rows exist, the new one is current. This is the **only top-10 entry running a single mid-tier model**: everyone in the top 7 runs GPT-5.5, Opus 4.8/4.6, or stacked ensembles. The differentiator is the grounding layer, not the model.

**Leaderboard neighbourhood (2026-06-24):** #1 Spacedock+GPT-5.5 74.33% · #2 Altimate+GPT-5.5+Sonnet 71.71% · #3 Altimate+Sonnet 68.22% · #4 Spacedock+Opus 4.8 67.21% · #5 MinusX 65.18% · #6 DataBridge+GLM-5.2 61.37% · #7 Pi Coding Agent+Opus 4.6 61.03% · **#8 LabRat 60.88%** · #9 PromptQL+Gemini 60.00%.

**Score trajectory:**

| Score | Event |
|---|---|
| 54.34% | Raw result as submitted |
| 55.88% | Fixed 14 Claude API outage trials miscounted as semantic fails; fixed `_INFRA_PATTERNS` to classify 5xx as infra |
| **60.88%** | Synced DAB checkout to current ground truth + re-ran patents and music_brainz q2 |

The big mover: **patents 0% → 60%** after syncing to PR #59, which fixed a globally-broken ground truth on that dataset (every team on the leaderboard scored 0% on patents until #59 corrected it; the board re-scores all rows when GTs are fixed, which is how Altimate moved 63.18% → 71.71% with no new run).

**Per-dataset (final):**

| Dataset | Pass@1 |
|---|---|
| stockmarket | 100% |
| stockindex | 93% |
| bookreview | 87% |
| crmarenapro | 82% |
| pancancer_atlas | 67% |
| googlelocal | 60% |
| patents | 60% |
| yelp | 46% |
| agnews | 45% |
| github_repos | 45% |
| music_brainz | 27% |
| deps_dev_v1 | 20% |

**Integrity:** clean pass@5, 270/270 trials, 0 contamination. Deep trace scan: 2884 tool calls, all MCP tools, zero answer-key or external-dataset access. Submitted as PR #65. Declared: **Hints: Yes**, Cartographer disclosed, agnews model-memory caveat noted, opening prompts provided for the leakage audit.

Screenshot: `docs/images/dab-leaderboard-2026-06-24.png`.

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

---

## 2026-07-16 — GPT-5.6 entry accepted: leaderboard #5 of 27 at 74.18%

The GPT-5.6 Luna Max campaign (labrat-agent driver, codex provider, Cartographer +
levers + hints + Context Ledger; full story in
[dab-solultra-ablation.md](dab-solultra-ablation.md)) was submitted as
[PR #72](https://github.com/ucbepic/DataAgentBench/pull/72) and **accepted as-is**
("Thanks for the submission and the detailed traces") — **#5 of 27 at stratified
Pass@1 0.7418**, +13.3pp over our Sonnet entry (#13, 0.6088, still on the board).
The board's new "Tuned prompt" column marks ranks 1–3 as benchmark-tuned; among
untuned entries LabRat is #2, 0.15pp behind Spacedock. Independently audited
pre-submission (zero P0/P1; byte-identical package rebuild;
`claude-fable-gpt56-dab-audit-report.md`). Screenshot:
`images/dab-leaderboard-2026-07-16.png`.
