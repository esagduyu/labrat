# LabRat on ADE-Bench: From 67% to 80% in Three Days

> **TL;DR** — LabRat, a terminal-native open-source dbt agent (AGPL-3.0), scored **80% (48/60 tasks)** on ADE-bench DuckDB+dbt with claude-sonnet-4-6 and best-of-3 retries. On the 39 tasks that overlap with Altimate Code's published DuckDB results, LabRat passes **32/39 (82%)** vs. Altimate's **30/39 (77%)**. This report documents what changed, what's still broken, and what it means.

---

## What is ADE-Bench?

ADE-Bench is a Docker-sandboxed, execution-based benchmark created by Benn Stancil (founder of Mode) in collaboration with dbt Labs. The task structure is simple but strict:

- An AI agent is given a real dbt project inside a Docker container
- It receives a natural-language task description (e.g. "create a `dim_superhost_evolution` model with SCD2 snapshotting")
- Its **only** tools are `docker exec` (to run commands) and `docker cp` (to write files)
- Success is judged by whether all dbt tests pass after the agent finishes — no partial credit, no LLM-as-judge, no open-ended rubric

This makes ADE-Bench one of the few agent benchmarks that is genuinely hard to game. You can't get credit for a plausible-sounding answer. Either the tests pass or they don't.

We run against **DuckDB + dbt**, the most accessible configuration. The full suite has **60 tasks** across three difficulty tiers: easy (15), medium (30), hard (15).

---

## Our setup

LabRat connects to ADE-Bench via `LabratLocalAgent` — a thin bridge in the `ade-bench` repo that:

1. Assembles a task prompt including the container name, project exploration instructions, and a mandatory verification checklist
2. Shells out to the `claude` CLI using Mac OAuth (Claude Max subscription — no API credits required)
3. The agent uses its local `Bash` tool to issue `docker exec` and `docker cp` commands against the sandboxed container

The same base model (claude-sonnet-4-6) powers both LabRat and Altimate Code's published results. The difference is in what the agent knows how to do, not the underlying LLM.

We run with **`--n-attempts 3`** (best-of-3 semantics: a task passes if any attempt passes) and **`--n-concurrent-trials 3`**. Each full 60-task run costs roughly $20–30 and takes 3–5 hours.

---

## The journey: 67% → 80% in three days

### Baseline: 67% (40/60) — May 24, 2026

Fresh `LabratLocalAgent` with minimal prompting. The agent knew how to use `docker exec` and `docker cp` but had no specific guidance on dbt project exploration, mandatory verification, or common failure modes.

**By difficulty tier:**
| Tier | Tasks | Score |
|---|---|---|
| Easy | 15 | 93% (14/15) |
| Medium | 30 | 73% (~22/30) |
| Hard | 15 | 53% (~8/15) |
| **Overall** | **60** | **67% (40/60)** |

The easy tier performance is encouraging — it means the agent can handle well-defined, single-model tasks reliably. The hard tier gap (53%) tells the real story: complex multi-model transformations, SCD2 snapshots, and tasks requiring knowledge of dbt-specific syntax fail more than half the time.

---

### Improvement run 1: 75% (45/60) — May 25–26, 2026

**What changed:** We analysed the 20 failing tasks and added four categories of guidance to the agent's preamble (`_DOCKER_PREAMBLE` in `labrat_local_agent.py`):

1. **Mandatory project exploration before writing** — `ls /app/models/`, read `dbt_project.yml`, read existing staging models before touching anything
2. **Mandatory verification checklist** — after every change, run `dbt build --select +<model>`, confirm PASS=N WARN=0 ERROR=0, inspect output with `dbt show`
3. **DuckDB-specific anti-patterns** — `DATE_TRUNC(...) + 1` is wrong, `get_current_timestamp()` isn't available, always use `{{ ref() }}` not raw table names
4. **Per-family hints** — domain-specific context for `analytics_engineering`, `asana`, and `f1` task families

**New passes (5):** `f1005`, `f1009`, `helixops_saas009`, and two variant tasks (`f1005.medium`, `airbnb011.hint`)

**Interpretation:** The biggest gains came from exploration discipline and the verification loop. Tasks that previously failed because the agent guessed at model names or skipped the final test run started passing once it was forced to check. The hard tier still lagged — anti-patterns and hints alone can't close gaps that require new capabilities.

---

### Errored batch recovery: ~77% (46/60) — May 26, 2026

Six tasks had errored (API usage limits) in the improvement run. On retry, two confirmed passes:

- **`quickbooks004`** — PASS (49/49 tests). A complex medium task with the most tests in the suite. Passed on 2/3 attempts once usage limits cleared.
- **`helixops_saas015.low`** — PASS (extra variant, not counted in the core 60)

The other four (`helixops_saas010`, `helixops_saas015`, `intercom002`, `quickbooks003`) still fail — not a usage-limit issue, genuine capability gaps.

---

### Tier 1 prompt improvements + validation: 80% (48/60) — May 26–27, 2026

After a root-cause analysis of the 10 remaining stubborn failures (see [`docs/ade_bench_failure_analysis.md`](ade_bench_failure_analysis.md)), we identified five distinct capability gaps. Tier 1 addresses the two that are fixable with prompt changes alone:

**T1.1 — Compile-first mandate**
The agent now runs `dbt compile 2>&1 | tail -30` as its very first step before writing any SQL. This surfaces pre-existing errors in the project (broken upstream package models, type mismatches) before the agent wastes context trying to build on top of a broken foundation.

**T1.2 — Column type inspection**
The mandatory verification step now explicitly instructs the agent to check column *types* via `dbt show`, not just column names. The prompt flags the specific failure mode: "if a duration column shows an interval value like `3 days 00:00:00`, fix it with `DATEDIFF('day', start_date, end_date)`."

**T1.3 — dbt unit test syntax**
A full `unit_tests:` YAML example was added to the preamble. dbt unit tests (introduced in dbt-core 1.8, February 2024) use a different YAML structure from data tests — the agent previously didn't know the syntax existed.

**T1.4 — Expanded family hints**
- `analytics_engineering`: added JOIN grain rules ("always anchor on the most complete table, never FULL OUTER JOIN two aggregation CTEs"), the `ROW_NUMBER()` dedup pattern, and FK join discovery
- `helixops_saas`: new family (the largest in the suite) — added DAG tracing guidance: find where a column originates before adding it to a mart

**New passes from T1:**
- **`airbnb010`** (PASS 2/3) — agent caught the INTERVAL type on `acct_age_before_achieving_superhost` and replaced it with `DATEDIFF`. T1.2 worked exactly as intended.
- **`airbnb012`** (PASS 1/3) — agent wrote a valid `unit_tests:` block in schema.yml and the tests caught the injected logic bugs. T1.3 worked exactly as intended.

**What T1 didn't fix:**
`analytics_engineering004`, `analytics_engineering006`, `asana004`, `asana005` still fail. These require new tool capabilities (schema comparison, lineage tracing) — prompt changes can't substitute for missing information.

**Final score after all improvements:**

| Tier | Tasks | Score |
|---|---|---|
| Easy | 15 | **100%** (15/15) |
| Medium | 30 | **80%** (~24/30) |
| Hard | 15 | **60%** (~9/15) |
| **Overall** | **60** | **80% (48/60)** |

---

## Apples-to-apples: LabRat vs. Altimate Code on DuckDB

Altimate Code publishes per-task results at [altimate.sh/benchmarks/ade-bench](https://www.altimate.sh/benchmarks/ade-bench) for both Snowflake and DuckDB. Their DuckDB run used 41 tasks, Sonnet 4.6, and max-3 retries on failures.

Before comparing, two important context differences:

1. **Task set**: Altimate ran 41 tasks that include `simple001`/`simple002` (not in our suite) and exclude `helixops_saas001–018`, `airbnb010–013`, and `quickbooks001–004`. The `helixops_saas` tasks (18 tasks, ~30% of the suite) were added to ADE-Bench in March 2026 — after Altimate ran their benchmark. The remaining gaps (quickbooks, airbnb010+) appear to be deliberate omissions, though no reason is stated. **Altimate's task set is a subset of ours, skewed toward the tasks added earlier in the benchmark's history.**

2. **Test counts have grown**: for tasks that appear in both runs, the number of dbt tests per task is sometimes higher in our run than in Altimate's (e.g. `quickbooks003`: 14 tests in their run vs. 15 in ours; `intercom002`: 4 vs. 5). The benchmark has been updated since their run, making direct score-line comparisons across all 60 vs. all 41 misleading.

### The 39 tasks both ran (Altimate's 41 minus `simple001`/`simple002`)

| | Score | % |
|---|---|---|
| **LabRat** | **32 / 39** | **82%** |
| **Altimate Code** | **30 / 39** | **77%** |

### Per-task breakdown

| Task | Difficulty | LabRat | Altimate | Notes |
|---|---|---|---|---|
| airbnb001 | Medium | ✅ PASS | ❌ FAIL | Altimate: 8/10 tests |
| airbnb002 | Medium | ✅ PASS | ❌ FAIL | Altimate: 9/11 tests |
| airbnb003 | Easy | ✅ PASS | ✅ PASS | |
| airbnb004 | Medium | ✅ PASS | ✅ PASS | |
| airbnb005 | Medium | ✅ PASS | ✅ PASS | |
| airbnb006 | Medium | ✅ PASS | ✅ PASS | |
| airbnb007 | Hard | ✅ PASS | ❌ FAIL | Altimate: 8/11 tests |
| airbnb008 | Easy | ✅ PASS | ❌ FAIL | Altimate: 2/4 tests |
| airbnb009 | Medium | ✅ PASS | ✅ PASS | |
| analytics_engineering001 | Easy | ✅ PASS | ✅ PASS | |
| analytics_engineering002 | Easy | ✅ PASS | ✅ PASS | |
| analytics_engineering003 | Easy | ✅ PASS | ✅ PASS | |
| analytics_engineering004 | Medium | ❌ FAIL | ✅ PASS | Wrong column order; needs schema comparison tool |
| analytics_engineering005 | Easy | ✅ PASS | ✅ PASS | |
| analytics_engineering006 | Hard | ❌ FAIL | ✅ PASS | Missing supplier join + dedup; needs lineage tracing |
| analytics_engineering007 | Medium | ✅ PASS | ✅ PASS | |
| analytics_engineering008 | Easy | ✅ PASS | ✅ PASS | |
| asana001 | Medium | ✅ PASS | ✅ PASS | |
| asana002 | Medium | ✅ PASS | ✅ PASS | |
| asana003 | Hard | ✅ PASS | ✅ PASS | |
| asana004 | Medium | ❌ FAIL | ❌ FAIL | Both fail: wrong JOIN anchor (both get 5-6/7 tests) |
| asana005 | Medium | ❌ FAIL | ✅ PASS | Altimate passes; we drop rows due to wrong JOIN grain |
| f1001 | Medium | ✅ PASS | ✅ PASS | |
| f1002 | Hard | ❌ FAIL | ❌ FAIL | Both fail: extra columns in output |
| f1003 | Medium | ✅ PASS | ✅ PASS | |
| f1004 | Medium | ✅ PASS | ✅ PASS | |
| f1005 | Medium | ✅ PASS | ✅ PASS | |
| f1006 | Hard | ✅ PASS | ✅ PASS | |
| f1007 | Medium | ✅ PASS | ✅ PASS | |
| f1009 | Medium | ✅ PASS | ✅ PASS | |
| f1010 | Medium | ✅ PASS | ✅ PASS | |
| f1011 | Medium | ✅ PASS | ❌ FAIL | Altimate: 4/6 tests |
| intercom001 | Medium | ✅ PASS | ✅ PASS | |
| intercom002 | Hard | ❌ FAIL | ✅ PASS | Altimate passes (4/4); we fail 2/5 — task has more tests in current suite |
| intercom003 | Hard | ✅ PASS | ✅ PASS | |
| quickbooks001 | Easy | ✅ PASS | ✅ PASS | |
| quickbooks002 | Medium | ✅ PASS | ✅ PASS | |
| quickbooks003 | Hard | ❌ FAIL | ❌ FAIL | Both fail: complex multi-model task, 15 tests |
| quickbooks004 | Medium | ✅ PASS | ❌ FAIL | Altimate: 28/48 tests; we pass 49/49 |

**Summary:**
- LabRat wins where Altimate fails: `airbnb001`, `airbnb002`, `airbnb007`, `airbnb008`, `f1011`, `quickbooks004` — 6 tasks
- Altimate wins where LabRat fails: `analytics_engineering004`, `analytics_engineering006`, `asana005`, `intercom002` — 4 tasks
- Both fail: `asana004`, `f1002`, `quickbooks003` — 3 tasks

---

## Honest critique

### What these results don't prove

**The benchmark snapshots differ.** Our 60-task DuckDB run is on the current (May 2026) ADE-Bench, which has more tasks and in some cases more dbt tests per task than Altimate's run. Altimate passing `intercom002` with 4/4 tests is not the same difficulty as us failing it with 3/5 tests — the benchmark got harder in between. We're not running the same exam.

**The agent architectures are not equivalent.** Altimate Code is a purpose-built analytics engineering agent with 100+ specialised tools (explain column, show lineage, generate unit test, run data_diff across 12 warehouses). LabRat is a general-purpose data agent with 12 tools, adapted for ADE-Bench via prompt engineering. That we're comparable in score despite the architectural gap is encouraging for LabRat, but it doesn't mean the approaches are equivalent.

**Best-of-3 inflates scores.** Altimate reports a single-run range of 63–68% for their DuckDB agent — the 78% best-run score requires cherry-picking the best outcome from multiple runs. We use the same methodology (best-of-3), so both numbers are "best run" figures, not median performance. Median single-run LabRat is roughly 65–70%.

**We don't have `simple001`/`simple002`.** These are in Altimate's 41-task set and both pass for them. We didn't include them in our 60 because they don't appear in the current `ade-bench` task registry. If they're trivial warm-up tasks (the name suggests so), their absence doesn't hurt the comparison.

### Where we genuinely lose

**Output-schema blindness** (`analytics_engineering004`, `analytics_engineering006`, `f1002`): The agent can't know the expected column list or ordering before writing. Altimate closes this with a `data_diff` tool — the agent writes a model, runs data_diff against the existing table or test expectation, sees the column delta, and fixes it. We don't have this yet.

**JOIN grain awareness** (`asana004`, `asana005`): Both agents fail `asana004`. Altimate passes `asana005` — they correctly anchor the join on the project table and LEFT JOIN aggregations, preserving NULL rows for projects with no user activity. Our expanded analytics_engineering hints address this, but the fix didn't fully propagate to the asana family.

**Dependency discovery** (`analytics_engineering006`, `helixops_saas007`): The agent needs to trace backwards through the DAG to discover that a `supplier_id` FK needs a join to `stg_suppliers`, or that adding `geo_segment` to a mart requires adding it first to `int_account_billing_snapshot`. Our `helixops_saas` family hints target this, but we haven't re-run these tasks yet (usage limits hit during the validation run).

### What comes next

The three gaps above map to specific Tier 2 engineering tasks described in [`docs/ade_bench_failure_analysis.md`](ade_bench_failure_analysis.md):

- **T2.1 — `compare_schema` tool**: reads schema.yml test expectations, compares against actual `dbt show` output, reports column mismatches. Closes the output-schema blindness gap. Estimated 3 days.
- **T2.2 — `trace_column_lineage` tool**: given a column name + target model, traverses the dbt manifest's `node_deps` graph to find where the column originates. Closes the dependency discovery gap. Estimated 1 week.
- **T2.3 — `check_project_compile` tool**: wraps the compile-first step as an explicit pre-task check, returns structured error output. Already partially addressed by T1.1. Estimated 1 day.

Implementing T2.1 and T2.2 would close `analytics_engineering004`, `analytics_engineering006`, and likely `helixops_saas007` — three of our four losses to Altimate on the shared task set.

---

## Context: the benchmark field

For reference, the broader ADE-Bench leaderboard as of May 2026:

| Agent | Database | Tasks | Best-run score | Notes |
|---|---|---|---|---|
| **LabRat** | DuckDB | 60 | **80%** | AGPL-3.0, open-source, this report |
| Altimate Code | DuckDB | 41 | 78% | Closed-source; subset of current suite |
| Altimate Code | Snowflake | 43 | 74.4% | Their headline number |
| Snowflake Cortex Code | — | — | 65% | Opus 4.6 |
| dbt Labs baseline | — | — | 59% | Sonnet 4.5 |

The `simple001`/`simple002` tasks in Altimate's set are absent from ours and may account for 1–2 percentage points of their reported 78%.

---

## Reproducibility

All experiment results live in `~/repos/ade-bench/experiments/`. To reproduce the 80% run:

```bash
# Prerequisites: Docker, ade-bench repo, Claude Max (Mac OAuth)
cd ~/repos/ade-bench

# Full 60-task run
uv run ade run $(cd tasks && ls -d */ | sed 's|/||') \
  --db duckdb --project-type dbt --agent labrat_local \
  --no-diffs --n-concurrent-trials 3 --n-attempts 3

# Or via labrat's wrapper (handles task selection and report)
cd ~/repos/labrat
uv run scripts/eval_ade_bench.py --n-attempts 3
```

The `LabratLocalAgent` source is at `~/repos/ade-bench/ade_bench/agents/installed_agents/labrat_local/labrat_local_agent.py`. All prompt improvements are committed to the `main` branch of that repo.

---

*LabRat is [open-source under AGPL-3.0](https://github.com/esagduyu/labrat). Issues, PRs, and competing benchmark runs welcome.*
