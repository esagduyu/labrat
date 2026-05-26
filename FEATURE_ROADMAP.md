# LabRat — Feature Roadmap

> Companion to `CONTEXT/labrat-competitive-landscape-v2.md` (May 25, 2026, local-only) and `benchmark_plan.md`. Read after the landscape doc — every entry below cites a specific competitive or quality signal that motivates it.

This is a prioritization document, not an implementation spec. Each item has an **Impact** score (1–5, business/quality lift), a **Difficulty** score (1–5, engineering cost + risk), and a one-line **Source** linking back to the signal that justifies it. Use the quadrant view to pick the next 1–2 items; use the per-item notes to scope.

---

## How to read

**Impact (1–5):**
- 5 — Changes our competitive position (adds a benchmark axis, claims a defensible gap, makes a public moment)
- 4 — Significant quality lift or closes a verifiable competitor gap
- 3 — Solid product improvement, defensible against churn
- 2 — Nice to have
- 1 — Cosmetic

**Difficulty (1–5):**
- 5 — Multi-week, significant new architecture
- 4 — 1–2 weeks, new substantial code
- 3 — Several days, mostly wiring
- 2 — 1–2 days, narrow change
- 1 — Hours, config/docs only

**Priority groups:**
- 🟢 **Do now** — high impact, low difficulty (Impact ≥ 4 AND Difficulty ≤ 2)
- 🔵 **Strategic** — high impact, higher difficulty (Impact ≥ 4 AND Difficulty 3–5)
- 🟡 **Background** — moderate impact, low difficulty (Impact 2–3 AND Difficulty ≤ 3)
- 🔴 **Defer / skip** — explicit list of things we deliberately won't do, with reasoning

---

## 🟢 Do now (Impact ≥ 4, Difficulty ≤ 2)

These are the highest-leverage moves. Total estimated effort: under one week if done sequentially.

### 1. Normalize the ADE-bench comparison to Altimate
- **Impact: 5 · Difficulty: 1**
- **Source:** Landscape doc Section 1.3 — Altimate's 74.4% is on a **43-task subset** with **Snowflake**; LabRat's 67% is on **all 60** with **DuckDB**. The comparison is invalid until we restrict to the same subset.
- **What:** Identify the 43 tasks Altimate scored on (from their published `altimate.sh/benchmarks/ade-bench` page or by asking them directly), re-run LabRat on that subset, publish both numbers (full 60 + 43-subset comparison).
- **Why it's #1:** Every public claim we make about being "competitive with the leaderboard" is currently bogus until normalized. Cost of being caught: high. Cost to fix: half a day.

### 2. Submit official ADE-bench score
- **Impact: 5 · Difficulty: 1**
- **Source:** Landscape doc Section 6 Stage 1 — "Submit official ADE-bench and Spider 2.0-DBT runs."
- **What:** Engineering is already done. Submit our 67% run through the dbt-labs/ade-bench official submission process. Frame the post around "Docker-sandboxed, execution-based, no LLM judges" — leveraging the anti-gameable angle (item 4 below).
- **Why:** First public LabRat score = first public competitive moment. The engineering already paid for this; not submitting is leaving money on the table.

### 3. ADE-bench N=3 retries
- **Impact: 4 · Difficulty: 2**
- **Source:** `benchmark_plan.md` Part 1 Tier 1 #1. `helixops_saas010` passed on a single-task rerun but failed in the full run — single-trial scoring over-penalizes us.
- **What:** Add `--n-trials 3` to the runner, mark `is_resolved` true if any trial passes. This matches how published leaderboards score.
- **Why:** Likely +1–3 tasks for zero model/prompt changes. Expected +2–5% on overall score.

### 4. "Anti-gameable benchmark" public messaging
- **Impact: 4 · Difficulty: 1**
- **Source:** Landscape doc Section 4.1 — Berkeley RDI 2026 found 8 major agent benchmarks (SWE-bench Verified, GAIA, OSWorld, Terminal-Bench, etc.) gameable via leaked refs, unsanitized `eval()`, prompt-injectable LLM judges. ADE-bench's design avoids all of these.
- **What:** README section + a short blog post / launch tweet thread framing LabRat's benchmarks as "the kind that actually mean something." Concrete examples from the Berkeley study, contrast with ADE-bench's design.
- **Why:** Anti-gameable design is a marketing asset, not just engineering hygiene. Free positioning win — no engineering required.

### 5. Pre-submit verification prompt nudge
- **Impact: 4 · Difficulty: 1**
- **Source:** `benchmark_plan.md` Part 1 Tier 1 #2. `helixops_saas009` failed because the agent quit after 5 turns / $0.10 with wrong `dbt run` scope.
- **What:** Append to system prompt: *"Before finishing, run `dbt build --select +<your_changed_models>` and inspect the test results. If any test fails, fix it before declaring done."*
- **Why:** Direct fix for the early-quit failure mode. Cost is one paragraph.

### 6. Anti-pattern list in system prompt
- **Impact: 3 · Difficulty: 1**
- **Source:** Archived Spider2 findings (`docs/spider2_decisions_archive.md`). Already-collected patterns: `DATE_TRUNC(...) + N` must be `+ INTERVAL N DAY`; `get_current_timestamp()` is banned; grep before assuming a table exists.
- **What:** Add the list to the system prompt. ~5 bullet points.
- **Why:** Costless. Promotes to Do-now tier only because it sits next to items 5/6 in the same PR.

### 7. Fix v1 framing in `README.md` / `competitive_intelligence.md`
- **Impact: 3 · Difficulty: 1**
- **Source:** Landscape doc Section 6 Stage 1 — "Fix the v1 framing... the truthful version is *no AGPL-licensed, terminal-native agent appears in the top 7*, not *no open-source agent.*"
- **What:** Audit all docs/READMEs/posts that say "no open-source competitor on Spider 2.0-DBT." Replace with the AGPL-terminal-native framing.
- **Why:** Avoid getting publicly caught overclaiming on something easily verified.

---

## 🔵 Strategic (Impact ≥ 4, Difficulty 3–5)

Big-ticket items. Each needs its own scoping pass before commit.

### 8. Spider 2.0-DBT integration end-to-end
- **Impact: 5 · Difficulty: 4**
- **Source:** Landscape doc Section 6 Stage 1; `benchmark_plan.md` Part 2.
- **What:** Phases A–D from `benchmark_plan.md`: runner shim → eval wiring → reporting → fair-score allowlist. ~4–6 days.
- **Why:** Spider2-DBT is the most-cited dbt-agent benchmark. A credible score (target 50+, SignalPilot range) puts LabRat in the top-7 conversation. Databao's 60.29 is a stretch v0 target.
- **Risk:** Some tasks unsolvable (Fivetran `_tmp` known issue). Handle with a documented allowlist, not by hiding the raw score.

### 9. BIRD-CRITIC-Flash as third benchmark
- **Impact: 4 · Difficulty: 3**
- **Source:** Landscape doc Section 4.2 Tier 1; `benchmark_plan.md` Part 3.
- **What:** 200-task SQL-debug benchmark. Exercises `explain_sql` + `draft_sql` + `run_validations` (single-query path, different from the dbt-project path).
- **Why:** Third axis. Different agent code path = broader test surface. Anti-gameable execution-based design = same marketing angle.
- **Sequencing:** Land after items 8 + 11. Don't add a third benchmark while two are still wobbly.

### 10. Per-failure analysis: read all 20 ADE-bench failure casts
- **Impact: 4 · Difficulty: 3**
- **Source:** `benchmark_plan.md` Part 1 Tier 2 #4.
- **What:** Watch the `agent.cast` recordings for each of the 20 failing tasks. Categorize failure modes. Output: a "common mistakes" appendix for the system prompt.
- **Why:** Cheapest way to find high-leverage prompt fixes. The `analytics_engineering` family at 25% pass rate strongly suggests a single domain-prompt issue.

### 11. Per-family domain prompts
- **Impact: 4 · Difficulty: 3**
- **Source:** `benchmark_plan.md` Part 1 Tier 2 #5. `analytics_engineering` (3/4 fail), `asana` (3/6 fail), `quickbooks` (2/4 fail) likely share family-specific conventions.
- **What:** A `dict[task_family, str]` of short domain preambles injected when matched.
- **Why:** Closes a chunk of the medium-tier failures without changing models. Depends on #10 to know what to write.

### 12. Plan-then-execute pattern
- **Impact: 4 · Difficulty: 3**
- **Source:** Archived Spider2 work (the deleted `spider2_agent.py` had phases 1–4 with mandatory `---plan ... ---` block). Prior work lifted dbt success rate ~4.5×.
- **What:** Bring back the mandatory plan block. Agent must declare a plan before any tool use; can revise mid-run but must justify.
- **Why:** Targets the early-quit and under-scoping patterns. We've shipped this before; resurrection cost is moderate.

### 13. Verifier critic loop
- **Impact: 4 · Difficulty: 3**
- **Source:** `benchmark_plan.md` Part 1 Tier 3 #8.
- **What:** After agent says "done," a second prompt (cheaper model fine, e.g., Haiku) reviews the diff and either approves or sends back with notes.
- **Why:** Catches the under-scoping pattern (helixops_saas009) for cheap. Costs ~$0.02/task at Haiku rates.

### 14. dbt unit-test generation tool
- **Impact: 4 · Difficulty: 4**
- **Source:** Landscape doc Section 1.3 — Altimate Code ships dbt unit-test generation as a flagship capability.
- **What:** A new tool (`generate_dbt_tests`) that proposes `dbt test` definitions from model SQL + sample data. Integrates with `validations/`.
- **Why:** Direct gap-close vs. Altimate. Also useful inside the verifier critic (#13) — the critic can demand tests be added before approving.
- **Risk:** Adds a tool that has to be maintained across all 7 warehouse adapters. Scope tightly to dbt+DuckDB first.

### 15. Continuous benchmarking CI
- **Impact: 4 · Difficulty: 3**
- **Source:** `benchmark_plan.md` Part 1 Tier 4 #12.
- **What:** GitHub Action — 6-task ADE-bench smoke set per PR, full 60-task nightly. Per-task regression detection alerts. Slack/email integration.
- **Why:** Without this, benchmark scores drift silently. Every architectural change risks a regression we won't notice until next manual run.
- **Cost note:** Smoke set per PR = ~$2/PR. Nightly = ~$22/day. Budget ~$700/month.

### 16. `marimo-pair` notebook integration
- **Impact: 5 · Difficulty: 5**
- **Source:** Landscape doc Section 5 gap #6 ("Open-source agentic notebook with warehouse-native + dbt-aware data tooling") and Section 6 Stage 2 #5.
- **What:** A `labrat-notebook` package that plugs LabRat's agent into `marimo-team/marimo-pair` running marimo notebook sessions. Closes the only meaningful gap vs. Hex/Briefer (no notebook).
- **Why:** Strategically large. marimo has 20.8k stars and is where the OSS notebook stack is converging (ACP/MCP). Being the first/best vertical data agent on top is a positioning win comparable to the original ADE-bench integration.
- **Risk:** Significant new code. Scope to a minimum-viable proof-of-concept first (one tool call from a marimo cell), expand from there.
- **Trigger to escalate:** If marimo-team ships their own vertical data agent, this becomes P0 — we want to be the canonical backend before they pick someone else.

### 17. Recording playback / debugger UI
- **Impact: 3 · Difficulty: 3**
- **Source:** `benchmark_plan.md` cross-cutting infra section. We have `agent.cast` files; viewing them currently requires re-running.
- **What:** A textual viewer that scrubs an `agent.cast` file. Annotates each turn with tools called, tokens used, dbt output.
- **Why:** Pays for itself the first time we use it on item #10. Probably enables 2× faster failure-mode analysis on every future benchmark run.

---

## 🟡 Background (Impact 2–3, Difficulty ≤ 3)

Lower-priority but cheap. Pick up when blocked on bigger items.

### 18. Unified `BenchmarkRunner` abstraction
- **Impact: 3 · Difficulty: 3**
- **Source:** `benchmark_plan.md` cross-cutting infra. Both ADE-bench and Spider2-DBT fit the same shape.
- **What:** Pydantic-shaped `BenchmarkRun` with `benchmark_name`, per-task results, cost, timestamps, model. Both runners produce these.
- **Why:** Enables side-by-side dashboards. Refactor cost paid once, used by every benchmark forever.
- **Order:** After item 8 (Spider2 lands). Don't refactor for one benchmark.

### 19. `data_diff`-style cross-dialect comparison tool
- **Impact: 3 · Difficulty: 4**
- **Source:** Landscape doc Section 1.3 — Altimate has 12 warehouses × 5 algorithms.
- **What:** A `compare_tables` tool that takes two warehouse connections + table names and reports row-level diffs.
- **Why:** Useful for migration / parity workflows. Lower impact than #14 (dbt unit tests) — narrower use case. Demote unless we hear users asking for it.

### 20. Per-task hint database
- **Impact: 3 · Difficulty: 2**
- **Source:** `benchmark_plan.md` cross-cutting infra. After #10, we'll have task-specific learnings worth saving.
- **What:** YAML file keyed by task ID, injected when matched. Not for leaderboard submissions (would be a leak), only for dev iteration.
- **Why:** Lets us encode failure-mode fixes without re-prompting from scratch. Strict discipline needed on what counts as a "hint" vs. cheating.

### 21. Pydantic-AI Harness capability bundle
- **Impact: 3 · Difficulty: 4**
- **Source:** Landscape doc Section 5 gap #5 and Section 6 Stage 2 #6 — no canonical "Pydantic-AI for data" implementation exists.
- **What:** Extract LabRat's 7 warehouse adapters + dbt catalog + memory layer into a reusable Pydantic-AI tool bundle.
- **Why:** Claims a positioning slot. Lower impact than the marimo move (#16) because Pydantic-AI users are a smaller / more developer-niche audience.
- **Demote unless:** Pydantic-AI's user base grows notably or someone else moves to claim the slot.

---

## 🔴 Defer / skip — explicit list, with reasoning

Things we should explicitly decide *not* to do right now (with the reasoning preserved so the decision survives turnover):

### Benchmarks we should *not* add
- **Spider 1.0** — saturated since GPT-4. Doesn't differentiate.
- **BIRD-dev** — binning issues, replaced by BIRD-CRITIC + LiveSQLBench. Use BIRD-CRITIC-Flash (#9) instead.
- **InfiAgent-DABench** — saturating.
- **Spider 2.0-Snow** — top 96.7%, near-saturation. Pure compute spend with no headroom.
- **SWE-bench Verified, GAIA, OSWorld, Terminal-Bench** — Berkeley RDI 2026 study found these gameable. Quoting them undermines our anti-gameable positioning (item 4).
- **MLE-Bench** — different agent shape (ML modeling). Defer until we have a strategic reason to claim "data agent across SQL + ML." Not v0 work.

### Features we should *not* clone from competitors
- **Hex Notebook Agent / Threads / Chat-with-App / Generative Apps** — these are SaaS/notebook-shaped. LabRat is terminal-native by design. Cloning them would dilute the positioning. The one exception is the marimo-pair integration (#16) because marimo is OSS-aligned.
- **Hex "Automatic User Memory" (Apr 14, 2026)** — we already have edit-derived memory shipped. Don't re-implement; instead, publish the comparison (item 23 below).
- **Hex "Semantic Model References" / "Metric Views"** — strong fit only if we move toward a semantic-layer-shaped product. Currently out of scope.
- **Wren AI's open context layer pivot** — overlaps with our `context_engine/`. Cloning means becoming a context layer instead of an agent. Stay an agent; cite Wren in docs.

### Things to actively watch, not build
- **Fivetran-dbt licensing changes** — if dbt Core becomes more restrictive, our AGPL stance becomes more valuable. Be ready to migrate to SQLMesh-or-equivalent if needed.
- **Databao / SignalPilot terminal client** — if either ships one, our terminal-native differentiator narrows. Be ready to defend with AGPL + audit + personalized memory.
- **marimo vertical data agent** — if marimo-team builds one, #16 promotes from Strategic to P0.

---

## Items worth doing but explicitly *not* product features

These are positioning / content moves, not engineering. Listed here so they don't fall through the cracks.

### 22. "Context engineering" research note
- **Impact: 4 · Difficulty: 3**
- **Source:** Landscape doc Section 6 Stage 2 #7.
- **What:** A longitudinal study measuring edit-derived memory deltas on a held-out task set. Day-1 LabRat vs. day-30 LabRat scored side-by-side, with the memory layer ablated.
- **Why:** No competitor has published this. Altimate has anti-pattern benchmarks; Databao has the workflow-restriction blog; nobody has longitudinal memory-deltas measurement.
- **Dependency:** Needs 4+ weeks of real usage data, plus the held-out task set. Start collecting now; publish when the data is meaningful.

### 23. Side-by-side comparison post vs. Hex's "Automatic User Memory"
- **Impact: 3 · Difficulty: 2**
- **Source:** Landscape doc Section 5 gap #2 — Hex shipped auto user memory April 14, 2026, but it's closed.
- **What:** Blog post showing LabRat's edit-derived memory + audit log against Hex's closed-source equivalent, with examples of what becomes possible when the memory is inspectable.
- **Why:** Defends our positioning the next time someone says "Hex has this too."

### 24. Decide commercial model (LabRat Cloud or not)
- **Impact: 5 · Difficulty: 5**
- **Source:** Landscape doc Section 6 Stage 3 #9 — Briefer→Resend, Numbers Station→Alation, DataChat→Mews pattern shows OSS data-agent companies tend to get acquired by adjacent SaaS players. Commercial model decision shapes everything else.
- **What:** Decide: AGPL + Cloud SaaS (Hex/Deepnote shape) vs. pure OSS (acquisition target) vs. consulting/services. Each has different feature implications.
- **Why:** This is not an engineering item, but every roadmap decision downstream depends on it. Item #16 (notebook integration) is more valuable under "Cloud SaaS"; item #21 (Pydantic-AI bundle) is more valuable under "pure OSS."
- **Sequencing:** Should happen *before* committing to items #16 or #21 at full scope.

---

## Quadrant summary

```
                          ↑ Impact
                          5  │  #8 Spider2-DBT
                             │  #16 marimo-pair        ← strategic big bets
                             │  #24 Commercial decision
                             │
                  #1 Altimate│  #9 BIRD-CRITIC
                  #2 Submit  │  #10 Cast analysis     ← quick wins        ← bigger items
                  #4 Anti-gam│  #11 Per-family prompts
                  #5 Pre-sub │  #12 Plan-then-execute
                          4  │  #3 N=3 retries        #13 Verifier critic
                             │                        #14 dbt unit tests
                             │                        #15 Continuous CI
                             │                        #22 Context eng note
                             │
                          3  │  #6 Anti-pattern list  #17 Cast viewer
                             │  #7 Fix README framing #18 BenchmarkRunner abstraction
                             │  #20 Hint database     #19 data_diff tool
                             │  #23 Hex memory post   #21 Pydantic-AI bundle
                             │
                          2  │  (Nice-to-haves)
                             │
                          1  │  (Cosmetic)
                             ├──────────────────────────────────→ Difficulty
                                1    2    3    4    5
```

The frontier to push on is the upper-left (high impact, low difficulty): items **#1, #2, #3, #4, #5, #6, #7** are all in the "do this week" zone. Doing all seven sets up the bigger strategic moves (#8, #16) with cleaner footing.

---

## Suggested 30-day sequence

| Week | Items | Rationale |
|---|---|---|
| 1 | #1, #2, #3, #4, #5, #6, #7 | Land all the 🟢 quick wins. Submit first official benchmark. Public moment + cleaner story. |
| 2 | #10 (cast analysis) → #11 (per-family prompts) | Highest-leverage prompt fixes. Re-baseline ADE-bench. |
| 3 | #8 Phase A+B (Spider2 runner + eval) | First Spider2 number. Aim 35+ (beats academic baseline). |
| 4 | #8 Phase C+D (reporting + fair-score allowlist), #12 (plan-then-execute) | Publish Spider2 score. Decide if #13 (critic) needed based on Spider2 failures. |

By end of day 30: two published benchmark scores, anti-gameable narrative live, ADE-bench score improved beyond 67% baseline. Strategic items (#16 marimo, #24 commercial decision) start in days 30–90.
