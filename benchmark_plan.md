# LabRat Benchmark Plan

> **Status (2026-07-10):** written at ADE 67%. ADE-bench has since reached **80%** (48/60) via the Tier-1+2 work (see `docs/ade_bench_failure_analysis.md`); DAB was integrated and is on the leaderboard at **60.88%** ("Claude Sonnet 4.6 + Cartographer" — see `docs/dab-integration.md`); Spider2-DBT Part 2 is **still not integrated**. The whole benchmark-validation track is **parked until after 2026-07-13** per explicit user call.

> Roadmap for pushing benchmark coverage from "one good number" to "comprehensive, well-tested product." Three tracks: (1) raise ADE-bench score from 67%, (2) integrate Spider2-DBT as a second eval suite, (3) add a tier-2 benchmark for breadth.

**Status when this plan was written (2026-05-25):**
- ADE-bench: **67% overall** (40/60), via `LabratLocalAgent`, claude-sonnet-4-6, ~$21.70/run
- Spider2-DBT: setup verified locally at `~/repos/Spider2/spider2-dbt`, **not yet integrated**
- Third benchmark: not selected yet — candidate analysis in Part 3

---

## Competitive context (sourced from `CONTEXT/labrat-competitive-landscape-v2.md`, May 2026)

These are the actual numbers to beat, not abstract goals:

| Benchmark | Top score | Vendor / setup | LabRat current |
|---|---|---|---|
| **ADE-bench** | **74.4%** | `altimate-code` (MIT, 528 stars), Sonnet 4.6, Snowflake, **43-task subset** | 67% on full 60 (DuckDB+Sonnet 4.6) |
| **Spider 2.0-DBT** | **60.29** | Databao Agent (JetBrains, Apache-2.0), Sonnet 4.6 — retook #1 from SignalPilot's 51.56 (Apr 21, 2026) | not yet submitted |
| Spider 2.0-DBT #2 | 51.56 | SignalPilot (Apache-2.0) | — |
| Spider 2.0-DBT #6 | 35.29 | `xlang-ai/spider-agent-dbt` (Apache-2.0 academic baseline), GPT-5.4 | — |

**Note on the ADE-bench comparison:** Altimate's 74.4% is on a **43-task subset** with Snowflake; we score 67% on the **full 60** with DuckDB. Not directly comparable — different sample, different warehouse. Before claiming "we beat / are behind X by Y points," normalize: either restrict to the 43-task subset Altimate published, or get them to publish full 60. **First action:** identify which 43 tasks Altimate used and compute our score on that subset.

**Strategic positioning levers from the landscape doc:**

1. **No AGPL-licensed, terminal-native agent appears in the Spider 2.0-DBT top 7.** Three of the top 7 are open source (Databao Apache-2.0, SignalPilot Apache-2.0, xlang-ai baseline Apache-2.0), but all are SDK- or web-UI-shaped, not terminal-native. This is defensible white space.
2. **Anti-gameable benchmark design is a marketing asset, not just engineering hygiene.** Berkeley RDI (2026) found 8 major agent benchmarks (SWE-bench Verified, GAIA, OSWorld, etc.) gameable via leaked refs, unsanitized `eval()`, prompt-injectable LLM judges. ADE-bench's "Docker-sandboxed, execution-based, no LLM judges" design is genuinely defensible by contrast. Use this framing in any public results post.
3. **Leaderboards are volatile — cite snapshot dates.** Databao retook #1 with 60.29 after SignalPilot held it at 51.56 a few weeks earlier. Always pin scores to a date.

---

## Part 1 — ADE-bench: 67% → higher

### Where we actually lost points

Full 60-task run is `~/repos/ade-bench/experiments/2026-05-24__23-15-04__none/`. 20 failures (one trial each, no retries). Grouped:

| Family | Failed / Total | Failed tasks |
|---|---|---|
| `analytics_engineering` | 3/4 | 004, 006, 007.medium |
| `quickbooks` | 2/4 | 003, 004 |
| `asana` | 3/6 | 004, 005, 005.hard |
| `airbnb` | 3/11 | 010, 011.hint, 012 |
| `helixops_saas` | 5/15 | 007.no_location_hint, 009, 010, 015 (+1 missing from this digest) |
| `f1` | 4/13 | 002, 005, 005.medium, 009 |
| `intercom` | 1/3 | 002 |

Observations from the data:

- **`analytics_engineering` is the worst-performing family (25% pass rate).** Only 1 of 4 trials passed. Likely a domain/prompt mismatch — disproportionately worth one targeted prompt fix.
- **Hint/variant tasks cluster among failures.** 5 of 20 failures are `.hint`, `.medium`, `.hard`, or `.no_location_hint` variants of base tasks that *did* pass. The model relies on the stripped hint more than it should.
- **Early-quit signal.** `helixops_saas009` used only 5 turns and $0.10 before submitting — the agent thinks it's done. Per `decisions.md` it ran `dbt run` with wrong scope, leaving 3 test models unbuilt.
- **Flakiness, not difficulty.** `helixops_saas010` failed on the full run but passed on a single-task rerun (see `~/repos/ade-bench/experiments/2026-05-25__00-12-11__none/`). Single trials over-penalize.
- **Cost ceiling not the issue.** No clear "ran out of budget" pattern — most failures used 10–26 turns and finished under $0.55.

### Improvement plan, tiered by effort

**Tier 1 — Free wins (no code, just runner config / prompt nudges)**

1. **Run N=3 trials per task; pass if any trial passes.** This is what real harnesses do (the published leaderboard scoring is best-of-N). It directly handles `helixops_saas010`-style flakiness and would have lifted our score on at least that one task without any model changes. Expected lift: +1–3 tasks.
2. **Mandatory pre-submit verification step.** Add to the system prompt: *"Before you finish, run `dbt build --select +<your_changed_models>` and inspect the test results. If any test fails, fix it before finishing."* This targets the early-quit pattern (`helixops_saas009`). Expected lift: +1–2 tasks.
3. **Whitelist of dbt anti-patterns from prior runs.** From the archived Spider2 findings: `DATE_TRUNC(...) + N` must be `+ INTERVAL N DAY`; `get_current_timestamp()` is banned; never assume a table exists without grepping. Drop these directly into the system prompt.

**Tier 2 — Failure-driven prompt engineering (1 day)**

4. **Read all 20 `agent.cast` recordings and extract failure modes.** Don't guess — categorize. Expected output: a 1-page "common mistakes" appendix to the system prompt covering the top 5 failure modes. Run after this is added.
5. **Per-family domain hints.** `analytics_engineering` (3/4 fail) and `asana` (3/6 fail) likely share family-specific dbt conventions. A short per-family preamble injected when the task ID matches could close most of the gap. Cost: a `dict[str, str]` and a prompt template tweak.
6. **Hint-variant probe.** Run the 5 failing variant tasks (`.hint`, `.medium`, etc.) with the base task's hint manually re-added. If they pass, the gap is *only* hint-discovery — invest in a "search for context before writing" tool/prompt loop rather than more model capacity.

**Tier 3 — Loop & tool improvements (1–2 weeks)**

7. **Plan-then-execute pattern.** We already built this for Spider2 (the deleted `spider2_agent.py` had explicit phases 1–4). Bring back the `---plan ... ---` mandatory block: the agent must write a plan first, then execute, then verify. Phases 1–4 lifted dbt success rate ~4.5× in the prior Spider2 work — likely transferable.
8. **Verifier/critic pass.** After the agent says "done," run a second prompt (cheaper model OK) that reviews the diff and either approves or sends back. Catches the `helixops_saas009`-style under-scoping for cheap.
9. **Auto-rerun on test failure.** If `dbt test` fails after `dbt build`, automatically re-invoke the agent with the failure output appended. Currently the agent only sees test results if it chooses to run them.

**Tier 4 — Architecture / bigger swings (multi-week)**

10. **Model A/B on hard tasks.** Run the 15 hard tasks with Opus 4.7 instead of Sonnet 4.6. Cost grows ~5×; if pass rate jumps from 53% to >70%, route hard tasks to Opus selectively. Need cost model first.
11. **Self-consistency scoring.** Run N=3 with different temperatures, pick the answer that the most trials agree on (by output-table hash). More principled than majority pass/fail.
12. **Continuous benchmarking on commits.** GitHub Action that runs a 6-task smoke subset on every PR, full 60-task suite nightly. Per-task regression detection alerts if a previously-passing task starts failing.
13. **Per-task baseline DB + diff dashboard.** Store the resolved-status for every task at every commit, render a heatmap. Makes regressions and flakiness visible at a glance.

### Suggested execution order

1. Do **Tier 1 #1 (N=3)** first — single config change, easily +1–3 tasks.
2. Do **Tier 1 #2 + #3** (prompt nudges) in the same PR.
3. Re-run the 60-task suite. Get new baseline.
4. Then **Tier 2 #4** (read 20 cast files). This is the highest-leverage manual investment.
5. Decide on Tier 3 vs Tier 4 based on what failure modes Tier 2 surfaces.

**Target after Tier 1+2:** 75–80% (12–15 fewer failed tasks). Above 80% requires Tier 3+.

---

## Part 2 — Spider2-DBT integration

### Prior context (important — please read before committing)

LabRat **explicitly retired Spider2-DBT on 2026-05-24** in favor of ADE-bench. Reasons documented in memory and `decisions.md`:

- Some tasks depend on Fivetran `_tmp` ephemeral models with source tables that aren't in the seed — **unsolvable at any prompt quality**.
- Source-table naming inconsistencies between dbt packages and what's seeded.
- Wide complexity variance (1 model vs 82 models) makes the 12-task dev set noisy for prompt optimization.

**These reasons have not changed.** Reasons to revisit anyway, with concrete competitive context:

- Spider2-DBT is the **most-cited benchmark** in the dbt-agent space (XLang/Yale, paper at arxiv:2411.07763, public leaderboard at [spider2-sql.github.io](https://spider2-sql.github.io/)). All serious competitors submit here.
- **Real numbers to target** (May 2026 snapshot):
  - #1 Databao Agent (Apache-2.0, JetBrains) — **60.29**
  - #2 SignalPilot (Apache-2.0) — 51.56
  - #3 Shadowfax-DBT-Agent + GPT-5 (proprietary) — 41.18
  - #6 xlang-ai Spider-Agent-DBT + GPT-5.4 (Apache-2.0 baseline) — 35.29
- **The white-space claim is verifiable.** No AGPL-licensed, terminal-native agent is in the top 7. We can land there with a credible score and own that slot for narrative purposes.
- The dbt-agent infrastructure built for ADE-bench is highly reusable — incremental cost is low.
- A documented "fair score" allowlist for the unsolvable tasks is more honest than refusing to benchmark.

**Realistic first-submission target:** beat the xlang-ai academic baseline (35.29), aim for the SignalPilot range (50+). Cracking Databao's 60.29 likely requires substantial loop work and shouldn't be promised for v0.

### Setup status (verified 2026-05-25)

| Step | Status |
|---|---|
| Repo cloned at `~/repos/Spider2` | ✅ |
| `spider2-dbt/dbt_gold.zip` (684 MB) | ✅ already downloaded |
| `spider2-dbt/DBT_start_db.zip` (378 MB) | ✅ already downloaded |
| `python3 spider2-dbt/setup.py` (unzip step) | ✅ ran successfully |
| `examples/<task_id>/airbnb.duckdb` loads | ✅ 3 raw source tables |
| `evaluation_suite/gold/<task_id>/airbnb.duckdb` loads | ✅ 15 gold tables |
| Task count via `examples/spider2-dbt.jsonl` | ✅ 67 tasks |
| Docker installed (needed only for reference agent) | already have it for ADE-bench |
| Reference `spider-agent-dbt` deps installed | ⛔ not done — and we don't need them; we'll write our own runner |

The setup script is pure stdlib unzip; safe to re-run. No heavy deps required for *our* integration — only the reference Spider-Agent needs `chromadb`/`transformers`/`flaml`/etc.

### What Spider2-DBT looks like operationally

- Each task is a dbt project directory (`examples/<task_id>/`) containing `dbt_project.yml`, `profiles.yml`, `models/`, `packages.yml`, and a starter DuckDB (mostly empty / raw sources only).
- The task instruction comes from `examples/spider2-dbt.jsonl` (one line per task, fields `instance_id`, `instruction`, `type`).
- Gold answer is the fully-populated DuckDB at `evaluation_suite/gold/<task_id>/`.
- Evaluation (`evaluation_suite/evaluate.py`) is execution-based: runs the agent's dbt project, compares output tables to gold via `duckdb_match` / `tables_match`. **No LLM judge.** Same philosophy as ADE-bench.
- Unlike ADE-bench, **no Docker required for the task itself** — dbt+DuckDB run natively. Simpler harness.

### Integration plan — phased

**Phase A — Runner shim (1–2 days)**

- New file: `scripts/eval_spider2_dbt.py` (mirroring `scripts/eval_ade_bench.py`).
- Load `spider2-dbt.jsonl`, iterate tasks.
- For each task: copy `examples/<task_id>/` to a temp workdir, invoke `ClaudeCodeProvider` with the instruction + project path, let the agent edit dbt models, then run `dbt build` in the workdir.
- No Docker bridging needed — runs natively. Simpler than `LabratLocalAgent`.
- Configurable subset selection (`--tasks airbnb001,asana001` and `--tier easy|medium|hard`).
- Concurrency via existing `EvalRunner` pattern (`asyncio.gather` with semaphore).

**Phase B — Evaluation wiring (1 day)**

- Two options:
  - **(a)** Write results in the format `evaluation_suite/evaluate.py` expects (`results_metadata.jsonl` + per-instance output dirs), then shell out to it.
  - **(b)** Reimplement the comparison in `src/labrat/eval/spider2_scorer.py` — use `duckdb_match` logic but native to our pipeline.
- **Recommend (a) for v0** — reuses upstream scoring, avoids drift. Switch to (b) if (a) becomes a bottleneck or we want richer per-table diagnostics.

**Phase C — Reporting (0.5 day)**

- Extend `EvalReport` to produce a Spider2-specific section: per-task pass/fail, per-family aggregate (airbnb, asana, f1, helixops, etc. — the families largely overlap with ADE-bench, which makes cross-benchmark comparison cheap), cost-per-task, total runtime.
- Render alongside ADE-bench results in `eval_output/`.

**Phase D — Honest scoring with known-bad allowlist (0.5 day)**

- Codify the unsolvable-task list in `eval/spider2_blocklist.yaml` with one-line reasons (Fivetran `_tmp` missing sources, etc.).
- Report two numbers: **raw score** (over all 67) and **fair score** (over the subset).
- Surface both publicly — leaderboard expects raw, but fair score is the meaningful product-quality signal.

**Phase E — Continuous integration (1 day, optional)**

- Once Phase A–D are stable: GitHub Action that runs a 10-task Spider2 smoke set on every PR. Full 67-task nightly. Same regression-detection pattern proposed for ADE-bench in Part 1 Tier 4 #12.

**Total estimate:** ~4–6 days of focused work to ship Phase A–D end-to-end. Phase E is incremental.

### Risks / open questions

- **Cost.** 67 tasks × ~$0.30/task ≈ $20 per full run. Same magnitude as ADE-bench. Affordable but not free; budget for ~3 full runs/week if iterating.
- **The unsolvable tasks problem may bias optimization.** If we train prompts on the dev set, we might overfit to working around the dataset bugs rather than improving the agent. Mitigation: weight the prompt-optimization runs by the fair subset only.
- **Upstream churn.** Spider2 has been actively updated (most recent: 2025-11-06 Snowflake credential changes). Pin to a specific commit when we integrate; document the SHA in `decisions.md`.

---

## Part 3 — Third benchmark for breadth

The competitive landscape doc recommends three Tier-1 anchors: ADE-bench, Spider 2.0-DBT, and one more. After both Part 1 and Part 2 are stable, add a third axis. Candidate selection matters because each benchmark is a maintenance commitment.

### Tier 1 candidate (recommended)

**BIRD-CRITIC-Flash** (`bird-bench/BIRD-CRITIC-1`) — 200-task SQL-debug benchmark from NeurIPS 2025. Execution-based. Human-with-AI baseline ~90%.

Why this one first:
- **Different task shape from ADE-bench / Spider2-DBT.** Both of those are dbt-model-completion tasks. BIRD-CRITIC is SQL debugging — the agent receives a broken query + error and must repair it. Covers the *other* half of an analytics workflow.
- **Execution-based, no LLM judges** — same anti-gameable posture as ADE-bench. Aligns with the marketing angle.
- **Smaller scope to integrate (one query in/out)** vs. dbt-project-shaped benchmarks. Likely 1–2 days to wire up.
- **Direct showcase for LabRat's `explain_sql` + `draft_sql` + `run_validations` tool stack** — these are exactly the tools a SQL-debug task needs.

Risk: if our agent loop is heavily optimized for the dbt-project flow, single-query debugging may exercise different code paths and surface latent bugs. That's actually a feature — broadens our test surface.

### Tier 2 candidates (later, in priority order)

| # | Benchmark | Notes | Cost to integrate |
|---|---|---|---|
| 1 | **LiveSQLBench-Base-Lite** | 270 tasks across 18 end-user databases, includes DML. Exercises write paths LabRat currently restricts. May 2025 release. | Medium — need to allow scoped DML for the eval, contradicts our read-only-by-default stance. Frame carefully. |
| 2 | **Spider 2.0-Lite** | 547 tasks across BigQuery/Snowflake/SQLite, schemas with >1k columns. Top score ~73%. Stresses the personal context engine. | Medium — needs BigQuery/Snowflake credentials; the BigQuery subset costs real money to run. |
| 3 | **MLE-Bench** | 75 Kaggle competitions. AIDE/Weco is SOTA. Tests an entirely different agent shape (ML modeling, not SQL). | High — would need a new agent profile and probably the `dspy_opt` infrastructure rebuilt. Defer unless we want to make a "data agent across SQL + ML" claim. |
| 4 | **DSBench** / **DA-Code** | Analysis + EDA tasks. Best agents ~30–34%. | Medium-high. Less leaderboard visibility than the others. |

### Skip (per landscape doc)

- **Spider 1.0** — saturated since GPT-4
- **BIRD-dev** — binning issues, replaced by BIRD-CRITIC + LiveSQLBench
- **InfiAgent-DABench** — saturating
- **Spider 2.0-Snow** — best 96.7%, near-saturation
- **SWE-bench Verified, GAIA, OSWorld, Terminal-Bench** — Berkeley RDI 2026 found these gameable; useful only as "we evaluate beyond data" context, not as primary signal

### Suggested third-benchmark sequence

1. **Ship BIRD-CRITIC-Flash integration** after Part 1 Tier 2 and Part 2 Phase D land — total estimate ~3 days.
2. Run baseline, publish score alongside ADE-bench and Spider2-DBT.
3. Decide on Tier 2 additions based on which competitor moves matter most. If Altimate publishes BIRD-CRITIC numbers, that becomes a direct A/B. If Hex ships a CLI agent that benchmarks LiveSQLBench, prioritize that. If marimo-pair adopters benchmark MLE-Bench, prioritize that.

---

## Cross-cutting infrastructure (applies to both benchmarks)

These are worth building once and using twice:

- **Shared `BenchmarkRunner` abstraction** in `src/labrat/eval/` — both ADE-bench and Spider2-DBT loops fit the same shape (load tasks → run agent → score outputs → aggregate).
- **Unified results schema** — one `BenchmarkRun` Pydantic model with `benchmark_name`, per-task results, cost, timestamps, model. Enables side-by-side dashboards.
- **Recording playback / debugger** — already have `agent.cast` files from ADE-bench. Build a small viewer that lets us scrub a failed trial without rerunning it. Pays for itself the first time we use it on Tier 2 #4.
- **Per-task hint database.** Once we read 20 failure casts, we'll have specific patterns ("this task needs the `ref` to dim_X"). A YAML file keyed by task ID, injected when matched, lets us encode learnings without re-prompting from scratch. Apply carefully — these are leaks vs. base benchmark fairness, so only use for our own dev not for leaderboard submissions.

---

## What I'd actually do next, in order

1. **Identify Altimate's 43-task ADE-bench subset** and re-score LabRat on it. Without this, the "67% vs 74.4%" comparison is bogus and we should stop quoting it. ~half a day.
2. **Part 1 Tier 1 #1** — N=3 retries config. One PR. Re-baseline.
3. **Part 1 Tier 1 #2 + #3** — pre-submit verification + anti-pattern list. Same PR. Re-baseline.
4. **Part 1 Tier 2 #4** — read 20 cast files, build the common-mistakes appendix.
5. **Part 2 Phase A + B + C** — ship Spider2-DBT integration end-to-end. We'll get a Spider2 baseline number — target the SignalPilot 51.56 range, not Databao's 60.29.
6. **Part 2 Phase D** — fair-score allowlist.
7. **Submit official scores** to both leaderboards once stable. Frame publicly as "ADE-bench 67% / Spider2-DBT [X]" with snapshot dates and full disclosure (warehouse, retry budget, fair-vs-raw if applicable).
8. **Part 3** — wire up BIRD-CRITIC-Flash as the third axis. Exercises a different code path (SQL debug vs. dbt model completion).
9. Then decide architectural moves (plan-then-execute, verifier critic, Opus on hard tasks) based on which failure modes dominate across all three benchmarks.

The three tracks are largely independent — Part 2 doesn't block Part 1, Part 3 doesn't block Part 2. Run in parallel if there's capacity. **Submitting official scores (step 7) is what converts engineering work into competitive positioning** — don't let it slip behind the engineering.
