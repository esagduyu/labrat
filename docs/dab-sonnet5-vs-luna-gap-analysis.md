# DAB gap analysis: Sonnet-5-high (claude-mcp) vs GPT-5.6-Luna-max (labrat-agent, accepted 74.18% entry)

**Date:** 2026-07-24 · **Author:** trace-level task-by-task comparison (READ-ONLY analysis)
**Revised 2026-07-25** after an independent re-derivation of every number from the raw `trials.jsonl` and all 540 tool-call traces. Section (e) records what reproduced, what was wrong, and the corrections applied. Sections (f)–(h) are new: they identify a **driver/prompt asymmetry** that the original analysis mis-attributed to the model, and map the gaps onto already-built capability.

Runs compared (270 trials each, 54 tasks × 5):

- **LUNA:** `runs/dab/submission-gpt56-luna-max-ledger-final-270/trials.jsonl` — labrat-agent driver, codex/GPT-5.6-luna @ max, Cartographer + hints + levers + Context Ledger. The accepted PR #72 leaderboard entry.
- **SONNET:** `runs/dab/full12-sonnet5-high-fixes-shards/<dataset>/trials.jsonl` — claude-mcp driver, claude-sonnet-5 @ effort high, Cartographer + hints + levers + local-embed llm_classify + 2026-07-23 catalog/disambiguation fixes. No Context Ledger (claude-mcp has the server-side 64KB ledger only), no dispatch_subagent.

## Actionable conclusions (lead)

**Headline: Luna 74.18% stratified (206/270 micro) vs Sonnet-high 70.31% stratified (197/268 micro; 2 infra:timeout rows excluded). The −3.87pp gap is MOSTLY concentrated — 7 LUNA-WIN tasks (−16 trials) partially offset by 4 SONNET-WIN tasks (+11 trials) — but not entirely: a further −4 trials leak diffusely across 6 tasks bucketed as parity. The full reconciliation is −16 +11 −4 = −9, the exact micro delta.**

The most visible behavioral difference is that **Sonnet issues ~3× fewer tool calls than Luna** (median 11 from traces / 12 by the `tool_calls` field, vs Luna's 36; 54 s vs 304 s per trial). The original draft of this report read that as a *model* deficiency — "Sonnet under-explores." **That reading is wrong, on two counts** (evidence in §(e) and §(f)):

1. **Depth does not cause success.** Controlling for task difficulty (restricting to tasks where the same run both passed and failed), passing trials use *fewer* tool calls than failing ones — 5 of 13 tasks for Sonnet (median −2.5 calls) and 4 of 10 for Luna (median −4.5). Deep trials are deep because the task is hard, not successful because they are deep. Call count is a correlate of the driver, not a lever.
2. **The composition difference is real and prompt-caused — and it is NOT the cause of the gap.** Sonnet spends 72% of its calls on raw `run_sql` and 15% on grounding tools, with **zero** calls to `profile_dataset` and **zero** to `workflow`; Luna spends 44%, 26% and 23%. The cause is instruction, not availability: all 22 tools are exposed on both paths, but **the claude-mcp driver passes no system prompt at all** (`suite.py:455`) and its opening message never names those tools, while the labrat-agent path names them with `profile_dataset` marked "call this FIRST" (`suite.py:189-190`). **This was then tested end-to-end and the causal claim failed** (§(i)): naming the tools in the opening user message lifts `profile_dataset` to 0.92 calls/trial and `workflow` to 3.62 — roughly 40% of Luna's rates, from a standing start of zero — and the score does not move (19/24 vs 20/24, p = 1.0). So the prompt asymmetry is real and worth fixing on its own merits, but **tool composition does not explain the 3.87pp**, any more than tool depth does.

Sonnet's wins, by contrast, are correctly characterised: they are **driver-feature wins** (local-embed `llm_classify`, the raw-aggregation lever), not model wins — verified at trace level.

Ranked gap themes and what closes them (trial-level cost across the run in parentheses):

| # | Theme | Trials lost | What would close it |
|---|-------|------------|---------------------|
| 1 | **Free-text semantic recall: keyword filters miss description-only entities; partial entity-resolution misses the winning cluster; single-format date parsing silently drops rows** (googlelocal:2, music_brainz_20k:3, yelp:7) | −7 | A completeness lever: when candidate selection relies on keyword/regex over a free-text column, either read every row of small tables (googlelocal's business table is 79 rows) or run `llm_classify` (now cheap via local-embed) over the column; before filtering a text-typed date column, enumerate its distinct formats. This is autopsy levers A + C, still unclosed on the claude-mcp path. |
| 2 | **Verbatim value delivery: Sonnet reformats stored tokens** ("5–11PM" → "5PM–11PM") and the exact-substring scorer misses (googlelocal:3) | −3 | Strengthen the adjacent-token/verbatim lever: "quote stored values byte-for-byte — never normalize times, dates, codes, or ranges." Autopsy lever B; the 07-23 adjacent-token lever did not prevent this. |
| 3 | **Methodology instability on multi-convention tasks: 5 trials → 5 different EMA methodologies, never the full tie set** (patents:2) | −3 | Pin conventions before computing: a lever to (a) state the chosen filing-year scope / zero-fill / EMA-init convention explicitly, then (b) enumerate the FULL tie band. Luna converged 3/5 with heavy verification (43–71 calls); Sonnet at 23–32 calls never stabilized. The missing Context Ledger on claude-mcp plausibly contributes (no cross-turn pinning of intermediate conventions). |
| 4 | **Free-text numeric extraction fragility + wall-clock burn on prose-embedded values** (deps_dev_v1:2: regex boundary collision dropped semantic-ui; one 1200 s timeout in O(n²) `LIKE '%name%'` joins) | −2 | Lever: extract prose-embedded numbers/names ONCE into a temp table with word-boundary regexes, then join on the temp table; never substring-join two large tables directly. No tool guards this today — `verify_join` probes equi-joins only. |
| 5 | Noise-band ambiguity (crmarenapro:7 −1; offset by crmarenapro:8 +1) — both models confuse the same pair of knowledge articles | ~0 net | Not worth a lever; shared ambiguity, 1-trial noise. |
| 6 | **Diffuse parity-tail dilution** — single-trial losses on agnews:1, crmarenapro:2, crmarenapro:13, github_repos:2, pancancer_atlas:3, stockindex:3 | −4 | No single cause; this is the residual the original draft omitted. Worth +2.20pp if fully recovered, so it is not negligible — but it is exactly the surface a badly-targeted new lever would damage. |

Keep (validated by this comparison): **local-embed llm_classify** (+5 trials, agnews:4 — Luna scored 0/5 there on turn-budget exhaustion), **the 07-23 catalog fixes** (crmarenapro:1 now 5/5 parity), and **the push-aggregation lever** (yelp:2: Sonnet aggregated raw review rows 4/5 while Luna trusted the stale `business.review_count` column 4/5 → Missouri instead of PA).

Closing themes 1–3 alone recovers exactly 13 trials, worth **+5.75pp stratified (70.31% → 76.06%)** by direct simulation — comfortably above the Luna entry, since Sonnet's feature wins (+11 trials) are already banked. (The original draft estimated "+4–5pp"; it understated, because its per-theme figures were computed against the micro rate rather than the stratified mean. Closing themes 1–3 *perfectly*, at 5/5 rather than Luna's rate, is worth +7.70pp → 78.01%.)

---

## (a) Ground-truth stability

Checked `~/repos/DataAgentBench` git history (READ-ONLY):

- Commits since 2026-07-16 touch only `docs/data/leaderboards.json` and `README.md` (leaderboard additions: Sentinel/Actioneer, etc.).
- Last commit touching any `*ground_truth*` file: `c4724d2b1` **2026-06-12** ("Regenerate PATENTS query1/query2 ground truths...") — before BOTH runs.

**Verdict: GT is identical for both scoring dates. The stored `passed` flags are directly comparable; no re-scoring needed.**

## (b) Scores, matrix, roll-up

### Aggregate

| | LUNA (labrat-agent/codex) | SONNET-5-high (claude-mcp) |
|---|---|---|
| Stratified Pass@1 (mean over 12 datasets of mean-over-task pass rate) | **0.7418** | **0.7031** |
| Micro pass rate | 206/270 = 0.7630 | 197/268 = 0.7351 |
| infra rows (excluded from denominators) | 0 | 2 (`deps_dev_v1:2` t1, `stockmarket:2` t4 — both `infra:timeout` @1200 s) |
| `terminal:turn_budget` rows (counted as fails) | 10 (agnews:3 ×5, agnews:4 ×5) | 0 |
| Median tool calls / trial | 36 | 12 |
| Median latency / trial | 304 s (total 27.7 h) | 54 s (total 8.6 h) |

### Bucket matrix (54 tasks; WIN = pass-rate gap > 1 trial i.e. >0.2)

**PARITY-PASS 34 · PARITY-FAIL 8 · LUNA-WIN 7 · SONNET-WIN 4 · PARITY-MID 1**

| Task | Luna | Sonnet | Bucket |
|---|---|---|---|
| agnews:1 | 5/5 | 4/5 | PARITY-PASS |
| agnews:2 | 0/5 | 0/5 | PARITY-FAIL |
| agnews:3 | 0/5 | 0/5 | PARITY-FAIL |
| agnews:4 | 0/5 | 5/5 | **SONNET-WIN** |
| bookreview:1–3 | 5/5 ×3 | 5/5 ×3 | PARITY-PASS |
| crmarenapro:1 | 5/5 | 5/5 | PARITY-PASS |
| crmarenapro:2 | 1/5 | 0/5 | PARITY-FAIL |
| crmarenapro:3–6, 9–11 | 5/5 ×7 | 5/5 ×7 | PARITY-PASS |
| crmarenapro:7 | 4/5 | 3/5 | **LUNA-WIN** (noise-band) |
| crmarenapro:8 | 3/5 | 4/5 | **SONNET-WIN** (noise-band) |
| crmarenapro:12 | 0/5 | 0/5 | PARITY-FAIL |
| crmarenapro:13 | 5/5 | 4/5 | PARITY-PASS |
| deps_dev_v1:1 | 0/5 | 0/5 | PARITY-FAIL |
| deps_dev_v1:2 | 5/5 | 3/4 (+1 infra) | **LUNA-WIN** |
| github_repos:1 | 0/5 | 0/5 | PARITY-FAIL |
| github_repos:2 | 1/5 | 0/5 | PARITY-FAIL |
| github_repos:3–4 | 5/5 ×2 | 5/5 ×2 | PARITY-PASS |
| googlelocal:1 | 5/5 | 5/5 | PARITY-PASS |
| googlelocal:2 | 3/5 | 0/5 | **LUNA-WIN** |
| googlelocal:3 | 5/5 | 2/5 | **LUNA-WIN** |
| googlelocal:4 | 5/5 | 5/5 | PARITY-PASS |
| music_brainz_20k:1 | 4/5 | 5/5 | PARITY-PASS |
| music_brainz_20k:2 | 5/5 | 5/5 | PARITY-PASS |
| music_brainz_20k:3 | 5/5 | 3/5 | **LUNA-WIN** |
| pancancer_atlas:1 | 0/5 | 0/5 | PARITY-FAIL (identical reason both runs: "Missing histology type: 9382/3") |
| pancancer_atlas:2 | 5/5 | 5/5 | PARITY-PASS |
| pancancer_atlas:3 | 5/5 | 4/5 | PARITY-PASS |
| patents:1 | 5/5 | 5/5 | PARITY-PASS |
| patents:2 | 3/5 | 0/5 | **LUNA-WIN** |
| patents:3 | 2/5 | 3/5 | PARITY-MID |
| stockindex:1 | 4/5 | 5/5 | PARITY-PASS |
| stockindex:2 | 5/5 | 5/5 | PARITY-PASS |
| stockindex:3 | 5/5 | 4/5 | PARITY-PASS |
| stockmarket:1, 3, 5 | 5/5 ×3 | 5/5 ×3 | PARITY-PASS |
| stockmarket:2 | 5/5 | 4/4 (+1 infra) | PARITY-PASS |
| stockmarket:4 | 0/5 | 2/5 | **SONNET-WIN** |
| yelp:1, 3–6 | 5/5 ×5 | 5/5 ×5 | PARITY-PASS |
| yelp:2 | 1/5 | 4/5 | **SONNET-WIN** |
| yelp:7 | 5/5 | 3/5 | **LUNA-WIN** |

### Per-dataset roll-up (task-mean pass rate)

| Dataset | Luna | Sonnet | Δ |
|---|---|---|---|
| agnews | 0.250 | 0.450 | **+0.200** |
| bookreview | 1.000 | 1.000 | 0 |
| crmarenapro | 0.815 | 0.785 | −0.031 |
| deps_dev_v1 | 0.500 | 0.375 | −0.125 |
| github_repos | 0.550 | 0.500 | −0.050 |
| googlelocal | 0.900 | 0.600 | **−0.300** |
| music_brainz_20k | 0.933 | 0.867 | −0.067 |
| pancancer_atlas | 0.667 | 0.600 | −0.067 |
| patents | 0.667 | 0.533 | −0.133 |
| stockindex | 0.933 | 0.933 | 0 |
| stockmarket | 0.800 | 0.880 | +0.080 |
| yelp | 0.886 | 0.914 | +0.029 |

**On the known questions:** deps_dev_v1 and github_repos are weak for BOTH runs — mostly shared-hard (deps_dev_v1:1 and github_repos:1–2 fail near-identically in both), with only deps_dev_v1:2 a genuine Luna win. The real gap concentration is **googlelocal (−0.30) + patents (−0.13)**, plus one task each in music_brainz and yelp.

## (c) Divergences: per-task root causes with trace evidence

### LUNA-WIN tasks

#### googlelocal:2 — Luna 3/5, Sonnet 0/5 · root cause: keyword-LIKE entity recall misses a description-only entity

All 5 Sonnet trials answered the same 3 businesses and failed on `Missing name in LLM output: J B Oriental Inc`. Trace (`runs/dab/full12-sonnet5-high-fixes-shards/googlelocal/scratch/googlelocal_2__trial0/mcp_tool_calls.jsonl`) shows candidate selection was literally:

```sql
WHERE lower(b.name) LIKE '%massage%' OR lower(b.description) LIKE '%massage%'
```

"J B Oriental Inc" contains no "massage" token — its description says "rejuvenating therapies and soothing body treatments". Luna read the free text of the (only 79-row!) business table and included it; its passing trial4 states explicitly: *"J B Oriental Inc is included because its description refers to rejuvenating therapies and soothing body treatments."* Sonnet spent 8–12 tool calls / 16–32 s per trial and never enumerated the descriptions. (Luna's own 2 fails here were a scorer-format issue — score not adjacent to the name — not a wrong answer.) **Model/effort gap (shallow free-text recall), amplified by driver: Luna had ledger+deeper budget.**

#### googlelocal:3 — Luna 5/5, Sonnet 2/5 · root cause: verbatim-token loss (reformatted stored hour strings)

Failing Sonnet trials DID list the right business but reformatted the stored hours: trial1 wrote `Fri 5PM–11PM` where the DB (and scorer) has `Friday, 5–11PM`; scorer verdict `Missing hours [Friday, 5–11PM] for business: TACOS LA CABANA`. Sonnet's own passing trial0 wrote `Fri/Sat/Sun/Mon 5–11PM` — preserving the verbatim `5–11PM` token — and passed, proving the delivery format is the entire difference. Luna preserved verbatim ranges in 5/5 (e.g. "Mon/Fri/Sat/Sun 5–11PM"). Evidence: shard `trials.jsonl` payloads for `googlelocal:3` trials 0–4 vs Luna trial0. **Pure delivery-formatting loss — autopsy lever B; the 07-23 adjacent-token lever did not cover value normalization.**

#### patents:2 — Luna 3/5, Sonnet 0/5 · root cause: methodology instability; never enumerated the full EMA tie set

Scorer wants (among others) `BAKING; EDIBLE DOUGHS` (A21) in the answer. Sonnet's 5 trials produced 5 mutually inconsistent methodologies: B60 with "each group's full filing history" (t0), 23 candidate groups (t1), A61+H04 tie at EMA 2.0 (t2), A61 with EMA≈797 over the whole corpus (t3), A61+H04 again (t4) — none contained A21. Luna converged on the intended convention (34 DE patents granted H2-2019, first=true CPC, zero-filled years, α=0.1, EMA init = first year count) in 3/5 and enumerated the full 1.0-EMA tie band including A21, at 43–71 tool calls vs Sonnet's 23–32. Luna's 2 fails were the same missing-A21 mode, so this task is convention-sensitive for everyone — but Luna's exhaustive verification converged and Sonnet's didn't. **Model/effort gap + likely ledger absence (no cross-turn convention pinning on claude-mcp).**

#### music_brainz_20k:3 — Luna 5/5, Sonnet 3/5 · root cause: incomplete entity-resolution clusters

Sonnet t0 (45 calls) built a real dedup pipeline but its edit-distance rules missed the winning cluster and crowned "Groovey" ($5,668.50); t3 stopped at 11 tool calls and answered "Groovey $4,128.59" from an even shallower dedup. GT answer "Zo gaat het leven aan je voor" ($9,013.69 across 5 dup track_ids / 22 sales) was found by Luna in 5/5 and by Sonnet's own passing trials (t1/t2/t4, which explicitly enumerated per-source formatting quirks: source-2 "Artist - Title", source-3 "- Album" suffix, source-4 track-number prefix, source-1 "(Album)" suffix). **Model consistency gap: Sonnet knows the recipe but only fully applies it 3/5 times.** Evidence: shard payloads + `music_brainz_20k/scratch/music_brainz_20k_3__trial{0,3}/mcp_tool_calls.jsonl`.

#### yelp:7 — Luna 5/5, Sonnet 3/5 · root cause: single-format string date filtering on multi-format text dates

Failing trials (t0, t4) filtered with `u.yelping_since LIKE '2016%' AND r.date >= '2016-01-01'` — plain string compares (trace: `yelp/scratch/yelp_7__trial0/mcp_tool_calls.jsonl`). The data stores dates in **three formats**, so most qualifying rows silently dropped: failing answers count Restaurants=15 vs the true 58, producing a wrong category top-5 missing "Breakfast & Brunch". Sonnet's passing trial1 says it outright: *"all three date formats in the data were parsed."* Luna parsed robustly 5/5. **Model consistency gap; same silent-undercount family as theme 1.**

#### deps_dev_v1:2 — Luna 5/5, Sonnet 3/4 (+1 infra:timeout) · root cause: prose-embedded-value extraction fragility + O(n²) substring joins

t0 dropped `semantic-org/semantic-ui` from the top-5 (scorer: `Missing project name: semantic-org/semantic-ui`) via a regex/boundary collision — Sonnet's own t4 narrates the fix: *"The boundary fix resolves the collision (semantic-ui now correctly shows 4955, not the wrong duplicate)."* t1 burned its 1200 s wall clock building regex fork-count extraction and `pi.Project_Information LIKE '%' || pv.ProjectName || '%'` cross joins (trace: `deps_dev_v1/scratch/deps_dev_v1_2__trial1/mcp_tool_calls.jsonl`) → `infra:timeout`. Luna did the same extraction reliably in 43–54 calls / <520 s, 5/5. **Model gap (extraction robustness) + a genuine perf hazard.**

#### crmarenapro:7 — Luna 4/5, Sonnet 3/5 · noise-band, shared ambiguity

Both runs' failures are the SAME wrong knowledge article (`ka0Wt000000EpSUIA0`, Volume-Based Installation Timeline Policy) vs expected `ka0Wt000000EoD3IAK` (Scalability package validity article) — Luna t2 made the identical mistake. 1-trial difference; not a real gap. Notable: Sonnet solved it in 13–19 calls where Luna needed 46–73.

### SONNET-WIN tasks

#### agnews:4 — Luna 0/5, Sonnet 5/5 · cause: local-embed llm_classify (driver-feature win) vs Luna turn-budget death

All 5 Luna trials died `terminal:turn_budget` (10 turns, no answer) — bulk region-classification of 6,696 articles doesn't fit the API-backed 200-row-capped llm_classify within budget. Sonnet's trace (`agnews/scratch/agnews_4__trial0/mcp_tool_calls.jsonl`) shows one `llm_classify` call (local-embed backend) + 9 run_sql → "Africa, 291" in 13–18 calls. This is the validated 2026-07-23 fix; also note Luna additionally lost agnews:3 ×5 to the same turn-budget mode (both runs 0/5 there — Sonnet answered but its classification counts diverge from GT labels, as they do for every model on agnews:2/3).

#### yelp:2 — Luna 1/5, Sonnet 4/5 · cause: aggregate-the-raw-rows vs trusting a precomputed column

Luna used `business.review_count` (a stale/global column) in 4/5 trials → "Missouri, 2,243 reviews, 3.91"; GT wants counting actual joined review rows → "PA, 662, 3.70". Sonnet aggregated the review table directly (the push-aggregation lever's exact prescription) and verified totals ("All 2000 reviews accounted for") — its 1 fail was a sloppy 7-call trial (617 reviews/3.76). **Lever/feature + model win.**

#### stockmarket:4 — Luna 0/5, Sonnet 2/5 · cause: question-interpretation of the ranking metric

GT ranks by (up days − down days) margin → MFA Financial, Argo Group, HDFC, Albany, DTE. Luna ranked by raw up-day count in 5/5 (HDFC first; Argo never appears → fail). Sonnet picked the margin interpretation in 2/5 (t0/t1, pass) and raw count in 3/5 (fail). Neither model is reliable here; Sonnet's interpretation sampling is just wider. Shared-ambiguous task more than a durable Sonnet strength.

#### crmarenapro:8 — Luna 3/5, Sonnet 4/5 · noise-band

Failures in both runs are alternate-interpretation picks of the "fewest transfers" window/tie-break (Luna twice `005Wt000003NGjuIAG`, Sonnet once `005Wt000003NBcAIAW`). 1-trial noise.

### PARITY-MID

**patents:3 — Luna 2/5, Sonnet 3/5:** both runs fail the same way — reporting the full-depth CPC code title (H01M4/9066 "Metals or alloys...") instead of the 4-char subclass title (H01M "PROCESSES OR MEANS, e.g. BATTERIES..."). Granularity-choice ambiguity, shared.

### PARITY-FAIL (shared-hard, verified — no hidden gaps)

- **pancancer_atlas:1** 0/5 both, identical reason `Missing histology type: 9382/3`.
- **crmarenapro:2** ~0 both — both runs pick the SAME wrong article `ka0Wt000000Ens5IAC` in 13/14 failing trials.
- **crmarenapro:12** 0/5 both — both runs answer the SAME wrong agent `005Wt000003NJgAIAW`.
- **deps_dev_v1:1** 0/5 both — both miss the exotic GT name `@dmrvos/infrajs>0.0.6>typescript` / version-adjacency requirements.
- **github_repos:1** 0/5 both — neither produces a value rounding to 0.33.
- **github_repos:2** Luna 1/5, Sonnet 0/5 — both hunt for `swiftandroid/swift` and mostly miss the fuzzy window.
- **agnews:2/3** 0/5 both — classification-count disagreement with GT labels (agnews:2: Luna counts 11–14, Sonnet consistently 23, GT = 16/111); Luna's agnews:3 additionally all turn-budget.

## (d) Ranked gap themes (recap with expected recovery)

Stratified impact is measured by simulation: set Sonnet's pass count on the theme's tasks to Luna's rate, recompute the stratified mean, and take the difference. The figures below **supersede the estimates in the original draft**, which were understated roughly 2× and did not sum to the draft's own headline.

1. **Shallow first-pass semantics on free text** (googlelocal:2, music_brainz:3, yelp:7 — −7 trials, **+2.84pp** if closed). Close with a completeness lever (enumerate small tables / llm_classify description columns / enumerate date formats before filtering). Highest-leverage single fix.
2. **Verbatim value delivery** (googlelocal:3 — −3 trials, **+1.25pp** if closed). Close with a "byte-verbatim stored values" lever line.
3. **Convention pinning + full tie enumeration on methodology-heavy tasks** (patents:2 — −3 trials, **+1.67pp** if closed). Close with a state-your-convention-then-enumerate-ties lever. Note this theme ranks *above* theme 2, inverting the original draft's ordering.
4. **Prose-embedded extraction robustness + substring-join perf** (deps_dev_v1:2 — −2 trials incl. one timeout, **+1.04pp** if closed). Close with extract-once-to-temp-table guidance.
5. **Keep the wins:** local-embed llm_classify (+5), push-aggregation (+3 on yelp:2), catalog fixes (crmarenapro:1 parity). These are why Sonnet is at 70.3 rather than ~66.

**Attribution note (REVISED 2026-07-25 — the original claim here was wrong).** The original draft attributed themes 1–4 "predominantly" to MODEL/consistency gaps, on the strength of the 12-vs-36 tool-call difference. The trace re-analysis in §(f) does not support that. Within-task, call count does not predict success in either run, so depth is not a lever; and the entire composition difference tracks which tools each driver's prompt names. The corrected attribution is that themes 1–4 are **substantially DRIVER/PROMPT gaps** — the claude-mcp path runs with no system prompt and never invokes the grounding and plan-tracking tools it has — with a residual model component that this comparison **cannot isolate**, because the two runs differ in model, driver, and prompt simultaneously. Sonnet's wins are predominantly DRIVER/FEATURE wins (that part of the original claim holds).

The corollary matters for what to build: the draft's proposed "prompted-depth lever" is aimed at a mechanism that does not exist. It was never implemented (no match for it anywhere in `src/labrat/`), and it should not be, in that form. The cheaper and better-evidenced intervention is §(h.1) — give the claude-mcp driver the analyst SOP prompt the labrat-agent driver already has.

---

## (e) Independent validation of this report (2026-07-25)

Every figure below was recomputed from the raw `trials.jsonl` files and all 540 tool-call traces, not read from the prose. Scripts were one-off; the method is stated inline so it can be reproduced.

### Reproduced exactly

- Both runs: 270 rows, 54 tasks, the 12 official datasets, **zero duplicate `(task_id, trial_num)` keys** — no resume-dedup contamination in either.
- Stratified 0.7418 / 0.7031; micro 206/270 and 197/268; the two `infra:timeout` rows are exactly `deps_dev_v1:2` t1 and `stockmarket:2` t4; the ten `terminal:turn_budget` rows are exactly agnews:3 ×5 and agnews:4 ×5.
- Median tool calls 36 vs 12, median latency 304 s vs 54 s, totals 27.7 h vs 8.6 h. All twelve per-dataset means and deltas match to three decimals. Bucket counts 7 / 4 / 1 / 8 / 34 with the same task membership.
- **Ground-truth stability confirmed independently.** Last commit touching any `*ground_truth*` file in `~/repos/DataAgentBench` is `c4724d2b1` (2026-06-12); everything since 2026-07-16 touches only `README.md` and `docs/data/leaderboards.json`. The two runs are directly comparable.
- **Every trace-level causal claim in §(c) that was checked held up verbatim.** `googlelocal:2`'s SQL is quoted correctly and all 5 trials fail with the identical `Missing name in LLM output: J B Oriental Inc`. `googlelocal:3`'s passing trials (t0, t3) emit `5–11PM` while all three failing trials emit `5PM–11PM`, with the rest of the answer correct — the delivery-format claim is exactly right. `yelp:7`'s two failing trials are the only ones containing `LIKE '2016`. `yelp:2`'s four failing Luna trials carry 11–15 `review_count` references and all answer Missouri/2,243, against 3 references in the passing trial. `patents:2`'s five Sonnet trials name five different CPC code sets, none containing A21. All eight PARITY-FAIL tasks share a failure reason string across runs except agnews:3, where the modes genuinely differ (Luna dies on turn budget, Sonnet answers and misclassifies).

### Corrected

1. **Luna-win trial total is −16, not −15**, and the lead's framing omitted the parity tail. Fixing only the 7 Luna-win tasks is worth +6.92pp — *more* than the entire 3.87pp gap, because Sonnet's own wins offset. Fixing only the 6 diffuse parity-tail tasks is worth +2.20pp. Both are now stated.
2. **Per-theme stratified figures were understated ~2×** and were internally inconsistent (the draft's own theme numbers summed to 3.5pp against its 4–5pp headline). Corrected in §(d); the correction also inverts themes 2 and 3 in rank order.
3. **Bucket threshold is stated as "gap > 0.2" but two tasks entered on exactly 0.2.** `crmarenapro:7` (4/5 vs 3/5) and `crmarenapro:8` (3/5 vs 4/5) are 1-trial differences admitted only through floating-point representation (`0.8 - 0.6` evaluates to `0.20000000000000007`). Both are already flagged as noise-band in §(c) and net to zero, so nothing downstream breaks, but the defensible count is **6 Luna-win and 3 Sonnet-win**.

All three errors run in the conservative direction, so the report's actionable conclusion not only survives but strengthens.

## (f) The driver/prompt asymmetry the original analysis missed

Aggregated over all 540 traces:

| | Sonnet (claude-mcp) | Luna (labrat-agent) |
|---|---|---|
| Total tool calls | 3,305 | 10,591 |
| `run_sql` share | **72%** | 44% |
| Grounding-tool share | **15%** | 26% |
| `workflow` (plan tracking) share | **0%** | 23% |

Per-tool counts, Sonnet vs Luna: `workflow` **0 vs 2,444**; `profile_dataset` **0 vs 599**; `list_tables` 22 vs 341; `link_schema` 17 vs 175; `check_sql` 0 vs 108; `search_trails` 0 vs 64; `column_stats` 0 vs 16; `explain_sql` 0 vs 9; `dispatch_subagent` 0 vs 40; `llm_classify` 15 vs 0 (Sonnet's local-embed win). On **every one of the 7 Luna-win tasks**, Luna used `profile_dataset` and `workflow` and Sonnet used neither.

This is not a capability difference. `build_data_tools_registry()` returns the same 22 tools on both paths, the MCP server exposes all of them, and `--allowedTools mcp__labrat` is a server-level prefix that permits all of them.

It is a prompting difference, and the traces show the dose-response cleanly. On claude-mcp, tool usage tracks how forcefully the opening message names a tool:

- `search_reference_docs` — instructed with "call FIRST" → 279 calls, almost exactly one per trial.
- `verify_join` — instructed conditionally ("before any multi-table JOIN") → 65 calls.
- `link_schema` — instructed conditionally ("on a wide/unfamiliar schema") → 17 calls.
- `profile_dataset`, `workflow`, `column_stats`, `check_sql`, `explain_sql`, `search_trails` — **never named** → 0 calls.

The root cause is explicit in the code: `_build_claude_mcp_prompt` is documented at `suite.py:455` as passing **no custom `--system-prompt`**, so Sonnet runs on the stock Claude Code CLI system prompt — a coding-agent prompt with no analyst SOP. The labrat-agent path instead builds `_build_labrat_agent_system_prompt` (`suite.py:181`), which enumerates the tools and carries a numbered Approach: profile first, narrow with `link_schema`, verify joins, re-read the question before answering.

One nuance worth recording: **neither** prompt names `workflow`, so Luna's 2,444 `workflow` calls are model propensity (GPT-5.6 adopting the exposed todo-tracker), not instruction. Sonnet additionally has its native planning tools removed by the sandbox gate (`_BLOCKED_NATIVE_TOOLS`, `suite.py:82`) without being pointed at `workflow` as the substitute, so the claude-mcp path currently runs **with no plan-tracking scaffold of any kind**.

### Direct test of this hypothesis — the mechanism fires, but far too weakly (2026-07-25)

The dose-response above is a correlation across tools, so it was tested directly. `--agent-mcp-system-prompt` (commit `9af37ed`) appends an analyst SOP naming `profile_dataset` "FIRST, before planning or querying," plus `workflow`, `column_stats` and `check_sql`. Measured over a 24-trial arm against the 24-trial levers-only smoke (identical config otherwise):

| per trial | levers only (SOP off) | + SOP | Luna |
|---|---|---|---|
| `profile_dataset` | 0.00 | **0.29** | 2.22 |
| `workflow` | 0.00 | **0.17** | 9.05 |
| `sample_rows` | 0.29 | 0.54 | — |
| `run_sql` | 12.00 | **12.08** | — |
| total calls | 15.3 | 16.0 | 39.2 |

So the instruction is not ignored — naming a tool does raise its call rate from exactly zero. But it lands at roughly **13% of Luna's `profile_dataset` rate and 2% of its `workflow` rate**, and it displaces nothing: `run_sql` volume is unchanged, so the agent still does the same direct-SQL-first work with a thin grounding veneer on top. Delivery is not the bottleneck — `--append-system-prompt` was verified end-to-end with a marker-token probe and the model obeyed it exactly.

(Method note, recorded because it nearly produced a wrong conclusion: two single-trial probes showed *zero* calls to both tools and were briefly written up as "the mechanism does not fire." At n=2 against rates of 0.29 and 0.17 per trial, seeing zero is the most likely outcome even when the effect is real. The n=24 arm is the evidence; the probes were underpowered.)

Two candidate explanations for the weakness, neither yet discriminated:

1. **Channel dominance.** The opening *user* message enumerates a tool list that omits `profile_dataset`, and the Cartographer line instructs `search_reference_docs` "FIRST for grounding … **before profiling** or writing SQL" — actively deprioritising the very call the system prompt asks for. A user message plausibly outweighs an appended system prompt for immediate task framing.
2. **Genuine redundancy.** The Scent doc returned by `search_reference_docs` already carries table grain, columns and row counts, so `profile_dataset` may add little when `--agent-cartograph` is on. Against this: Luna had the same Scent available and profiled anyway.

**§(f) therefore stands as an association with a demonstrated but insufficient causal component.** Naming tools moves usage; it does not move it far enough to matter, and §(i) shows it does not move the score.

## (g) What we have already built that bears on these gaps

### Capabilities that do not work on the claude-mcp path

Verified against the code, not the parity doc:

| Capability | claude-mcp | labrat-agent |
|---|---|---|
| `llm_extract` | **Always self-errors** (`ctx.llm_fn` never set; the MCP server is a pure tool server with no model handle) | Works |
| `llm_classify` | Self-errors **unless** `--llm-classify-backend local-embed`; the default `"llm"` backend is dead there | Works on any backend |
| `dispatch_subagent` | **Always self-errors** (`ctx.subagent_runner` never set) | Works |
| Sufficiency verifier (`--agent-verify`) | No-op by construction (loop-level) | Works |
| `--agent-taxonomy` | **Silently a no-op** — the flag is accepted but `self._agent_taxonomy` is never read in the claude-mcp path (only refs: `suite.py:693`, `1769`) | Works |
| T1a consensus / re-derive / argue / postverify | **Works** — driver-agnostic at `_run_trial_verified` | Works |
| `get_artifact` | Exists (ledger-gated) | Does not exist |

Two consequences deserve attention. First, `--agent-taxonomy` being a silent no-op on claude-mcp means **any taxonomy ablation run on that driver measured nothing**; the "taxonomy is net-negative" conclusion is only valid if it was measured on labrat-agent. Second, `llm_extract` has no MCP-compatible fallback at all, which is directly relevant to theme 4 (prose-embedded extraction) on the very driver where that theme costs us trials.

### A gap in the reverse direction, on the submission path

The 64 KB ledger budget shipped on 2026-07-24 was applied **only to the claude-mcp server-side ledger**. The in-process ledger that the labrat-agent path uses — the path that produced our accepted 74.18% entry — is constructed as `ContextLedger(ResultStore(root))` at `session.py:110` with no budget argument, so it falls back to `LedgerBudget.max_bytes = 8000` (`context_ledger.py:36`). Neither `run_agent_task` nor `build_agent_session` exposes a budget parameter, so there is no way to raise it.

That is the exact 8 KB truncation the 64 KB fix was written to eliminate, and Luna made 398 `search_reference_docs` calls into it (grounding payloads run 8–22 KB). Luna also has no `get_artifact` tool to recover a truncated payload. The ledger is already net-positive on this path, so this is an **untested upside on the submission path**, not a known defect — but it is the single cheapest unexplored lever we have, and it applies to the configuration we actually submit.

### Roadmap items relevant to these themes

- **T1a verification layer** (K-of-N consensus + independent re-derive) is merged and, importantly, **driver-agnostic — it does work on claude-mcp**, via `_run_trial_verified`. All flags default off (`--agent-consensus`, `--agent-reverify`, `--agent-argue-rounds`, `--agent-postverify`). Its ablation measured +7.4pp and +9.2pp individually but **±0 combined, within noise at n=24**, and the M1 diverse-consensus / argue / postverify units have **never been ablated at all**. This is the closest thing we have to a real answer for theme 3, and it is sitting switched off and unmeasured.
- **Unbuilt** and relevant: a `min_rows` answer-shape floor (theme 3 enumeration), debate-style consensus with data-view diversity (theme 3 + methodology stability), difflib nearest-name normalization on answer emission (theme 2), and any guard against `LIKE '%x%'` substring cross-joins (theme 4 — `verify_join` probes equi-joins only).
- **Trails** (`search_trails`) could in principle encode a convention for theme 3, but no DAB-relevant Trail exists and the tool is never called on claude-mcp.

## (h) Revised recommendations, in priority order

1. ~~**Give the claude-mcp driver a system prompt.**~~ **TESTED AND FALSIFIED — see §(i).** This was the top recommendation, on the strength of §(f)'s dose-response. Both channels were then built and ablated. The appended system prompt moved tool usage barely (`profile_dataset` 0.29/trial) and scored 17/24; the opening user message moved it a lot (0.92/trial `profile_dataset`, 3.62 `workflow`, ~40% of Luna's rates) and scored 19/24 — both statistically indistinguishable from the 20/24 arm with neither. Grounding-tool usage on this driver can be driven close to Luna's and **the score does not follow**. The prompt asymmetry is still worth repairing for its own sake (it is free and parity-restoring), but it is not a scoring lever and should not be sold as one.
2. **Do not ship the unbuilt "prompted-depth lever."** Depth does not predict success within a task in either run. The premise is disproven.
3. **Treat the three unmerged gap levers as unproven, and validate them for *dilution*, not for gain.** The smoke A/B was a clean comparison (identical driver, model, effort, cartograph, hints, ledger, classify backend), but its parity check was 3 tasks × 3 trials. A 10% per-trial regression on healthy tasks survives that check 39% of the time, and a 5% regression survives 63% of the time — while a 10% dilution across the ~170 parity-pass trials would cost ~17 trials, more than the 13 these levers target. That is precisely how the taxonomy pack went net-negative. Note also that **lever 3 (convention-pinning) failed its only target**: `patents:2` remained 0/3.
4. ~~**Measure the T1a verification layer before writing any new lever for theme 3.**~~ **MEASURED — see §(i).** T1a consensus K=3 scored 19/24 (p = 1.0 vs the 20/24 arm without it) at 3.3× wall-clock, and the vote telemetry shows why: it selected `subrun0`'s answer in 23 of 24 trials, changing the delivered answer exactly once. Consensus corrects stochastic error; themes 1–4 are systematic, so all three sub-runs share the blind spot. Larger n will not rescue this.
5. **Raise the in-process ledger budget on the labrat-agent path** and A/B it, because that is the submission path and the 64 KB fix never reached it.
6. **Keep in mind what this comparison cannot tell us.** The two runs differ in model, driver, and prompt at once. Nothing here isolates a Sonnet-vs-GPT-5.6 model gap, and this document should not be cited as evidence of one.

## (i) Smoke validation of the SOP system prompt + levers + T1a (2026-07-25)

Eight tasks (5 gap-derived, 3 parity), n=3, on `feat/dab-mcp-system-prompt`. Every flag matches the completed high run (cartograph + hints + levers + local-embed classify + mcp ledger, `claude-sonnet-5` @ high), so each arm differs from the one before it by exactly one thing. Arm A is directly comparable to `runs/dab/smoke-gap-levers-2026-07-24`, which isolates the system prompt.

| task | baseline high (n=5) | levers only (n=3) | A: + SOP (n=3) | B: + SOP + T1a K=3 |
|---|---|---|---|---|
| googlelocal:2 | 0/5 | 2/3 | **0/3** | 1/3 |
| googlelocal:3 | 2/5 | 3/3 | 3/3 | 3/3 |
| music_brainz_20k:3 | 3/5 | 3/3 | 3/3 | 3/3 |
| yelp:7 | 3/5 | 3/3 | 3/3 | 3/3 |
| patents:2 | 0/5 | 0/3 | 0/3 | 0/3 |
| bookreview:1 (parity) | 5/5 | 3/3 | 3/3 | 3/3 |
| stockindex:1 (parity) | 5/5 | 3/3 | **2/3** | 3/3 |
| crmarenapro:3 (parity) | 5/5 | 3/3 | 3/3 | 3/3 |
| **total** | — | **20/24** | **17/24** | **19/24** |
| median latency/trial | — | 102 s | 101 s | **411 s** |
| arm wall-clock | — | 0.9 h | 0.9 h | **3.0 h** |

### Arm C — naming the tools in the opening user message

Arm C moves the same content out of the appended system prompt and into the opening user message (`--agent-mcp-tool-prompt`), and reconciles the Cartographer line's "before profiling" wording. Everything else matches Arm A, so it is a one-variable swap of *channel*.

**The channel hypothesis is confirmed, with a large effect:**

| calls per trial | levers only | A: system prompt | **C: opening message** | Luna |
|---|---|---|---|---|
| `profile_dataset` | 0.00 | 0.29 | **0.92** | 2.22 |
| `workflow` | 0.00 | 0.17 | **3.62** | 9.05 |
| `column_stats` | 0.00 | 0.04 | **0.25** | 0.06 |
| `check_sql` | 0.00 | 0.00 | 0.04 | 0.40 |
| `run_sql` | 12.00 | 12.08 | 12.29 | 17.20 |
| total | 15.3 | 16.0 | **20.4** | 39.2 |

Against the system prompt, the opening message is **3.2× more effective for `profile_dataset` and 21× for `workflow`**, moving both from "essentially never" to roughly 40% of Luna's rate, and total calls from 15.3 to 20.4 against Luna's 39.2. §(f)'s reading of the channel was right, and Arm A simply used the wrong one.

**And the score does not move: 19/24, Fisher p = 1.0 against the levers-only 20/24**, with parity intact at 9/9 and only a modest cost increase (1.1 h vs 0.9 h). `patents:2` remains 0/3; `googlelocal:2` is 1/3.

**This is the most important result of the three arms, because it falsifies the §(f) thesis rather than merely failing to confirm it.** We can now demonstrably drive the claude-mcp tool mix a large fraction of the way toward Luna's — profiling, plan-tracking and column inspection all engaged — and the outcome is unchanged. Tool composition is therefore **not** the cause of the 3.87pp gap; it is a correlate of the two runs' different prompts, exactly as the depth statistic in §(e) was a correlate rather than a cause. Two candidate explanations for the gap have now been tested directly and both are dead: *explore deeper* and *ground more*. What remains unexplained is answer quality on the specific failure modes in themes 1–4, which is where the remaining effort belongs.

**Verdict on the SOP system prompt: no benefit; do not merge as implemented.** Arm A is 17/24 against the levers-only arm's 20/24 — a 3-trial decline that is not statistically distinguishable from noise (Fisher p = 0.49; gap tasks alone p = 0.70), so the honest reading is *no detectable effect in either direction*, with no hint of the upside the mechanism was supposed to deliver. It also produced this smoke's **first parity regression** (`stockindex:1` 3/3 → 2/3), which is the dilution surface that sank the taxonomy pack, though at one trial it is equally consistent with noise.

**Verdict on T1a consensus K=3 (Arm B): no benefit at 3.3× the cost, and the telemetry says why.** Arm B lands at 19/24 against the levers-only 20/24 — Fisher p = **1.0**, numerically indistinguishable — while median trial latency goes 101 s → 411 s and arm wall-clock 0.9 h → 3.0 h. It does tidy up Arm A's two damage points (`stockindex:1` back to 3/3, `googlelocal:2` 0/3 → 1/3), which is what a vote should do to variance, but it buys no ground against the arm without it.

The mechanism verifiably engaged — each trial dir carries `subrun0/1/2` and a `verification.json` with `consensus_k: 3` — so this is a real measurement, not a silent no-op like `--agent-taxonomy`. And the vote records show the problem directly: **in 23 of 24 trials the modal vote selected `subrun0`'s answer, so consensus changed the delivered answer exactly once**, with zero fallbacks. Three independent derivations are being paid for and then almost always agreeing.

That is the substantive lesson, and it generalises past this smoke: **consensus corrects stochastic error, but the gap themes are systematic.** A keyword `LIKE` filter that misses synonym matches misses them in all three sub-runs; a reformatted `5PM–11PM` is reformatted in all three; a convention chosen without pinning is re-chosen freshly in each. Voting across three agents that share a blind spot cannot see past it. This is the **first measurement of T1a on the claude-mcp driver** and it reproduces the earlier `labrat-agent` "within noise" result rather than contradicting it — but it also downgrades the §(h.4) hope that T1a was the untested answer to theme 3. It is not, for a reason that a larger n will not change.

One observability gap noted in passing: `TrialResult.meta` is empty for every claude-mcp trial, so consensus telemetry is only recoverable from the on-disk `verification.json`, never from `trials.jsonl`. Consistent with the §(g) finding that primary-agent usage is not captured on this driver.

`googlelocal:2` swinging 2/3 → 0/3 → 1/3 across the three arms, while a separate single-trial probe of the Arm A configuration *passed* it, is the calibration that matters most here: this task's trial-to-trial variance at n=3 is large enough to swamp every effect being measured. None of these smoke arms — including the levers-only one this branch is built on — carries enough power to justify a merge on its own.

Two conclusions follow. First, the SOP as written should not ship; if the idea is pursued, the next variant should move the tool naming into the **opening user message** (the channel the traces show actually steering tool choice) and reconcile the Cartographer line's "before profiling" wording, rather than strengthening the system prompt further. Second, the levers themselves remain unvalidated for dilution — Arm A does not change that, because it was never designed to.

## (j) Where this leaves the investigation

Three hypotheses for the 3.87pp gap have now been tested directly, and all three are dead:

| hypothesis | test | result |
|---|---|---|
| Sonnet **explores too shallowly** (12 vs 36 calls) | within-task depth-vs-outcome, both runs | Falsified — passing trials use *fewer* calls than failing ones |
| Sonnet **grounds too little** (0 profile_dataset / 0 workflow) | Arms A and C, two prompt channels | Falsified — usage driven to ~40% of Luna's, score unchanged (p = 1.0) |
| Sonnet **needs answer-level verification** | Arm B, T1a consensus K=3 | Falsified — vote changed 1 answer in 24, at 3.3× cost |

Each was a plausible reading of the trace data, and each turned out to be a correlate of the two runs' differing model-and-driver setup rather than a cause of the score difference. What survives is narrower and less convenient: the gap lives in **answer quality on specific failure modes** — synonym-blind free-text filtering, multi-format date parsing, entity-resolution completeness, verbatim value delivery, convention stability — not in how the agent orchestrates its tools.

That points future effort at the levers, which act on exactly those failure modes, and away from further orchestration work on the claude-mcp path. It also raises the priority of the one untested item in §(g) that is neither orchestration nor prompt: the **8 KB in-process ledger on the labrat-agent path**, which is the only identified defect sitting on the configuration we actually submit.

None of the three arms justifies a merge. `feat/dab-mcp-system-prompt` carries both prompt flags default-OFF and should stay unmerged unless the opening-message variant is wanted purely as driver-parity hygiene, in which case it is free and harmless (parity 9/9, +0.2 h) but must not be described as a scoring improvement.

## (k) Effort curve + lever dilution validation (2026-07-29/30)

Three full arms, n=3 over all 54 tasks, **identical feature set** (10 levers, cartograph, hints, local-embed classify, mcp ledger, catalog fixes) so effort is the only variable. Baseline for the lever question is the completed 7-lever high run (70.31%, n=5).

| arm | trials | micro | **stratified** | median trial |
|---|---|---|---|---|
| medium | 162 | 0.7593 | **0.7410** | 54 s |
| high | 160 | 0.7688 | **0.7435** | 115 s |
| xhigh | 162 | 0.7716 | **0.7322** | 109 s |

**Effort is exhausted as a lever.** All three tiers land within 1.1pp of each other on the leaderboard metric, and the ordering is medium ≈ high > xhigh. Note xhigh has the *highest* micro rate but the *lowest* stratified score — it does relatively better on task-rich datasets and worse on small ones (stockindex 0.78 vs 1.00 elsewhere), and stratified is the metric the board scores. The earlier "more effort hurt Sonnet" claim (medium 72.90% vs high 70.31%) was confounded by the catalog fixes; with features held constant the tiers are indistinguishable, and none approaches 80%.

**Lever dilution: PASSED.** Against the 7-lever baseline, at constant effort:

| task group | baseline | 10 levers | delta |
|---|---|---|---|
| derived-from (5 tasks) | 8/25 = 0.320 | 10/15 = 0.667 | +0.347 |
| **non-derived (49 tasks)** | 189/243 = 0.778 | 113/145 = 0.779 | **+0.002** (Fisher p = 1.000) |
| saturated, base-perfect (31 tasks) | 0 failures / 154 | 2 failures / 92 | — |

The gain is confined to the tasks the levers target and the other 49 are flat. On the purest dilution surface we can now **exclude per-trial dilution worse than 6.7%** at 95% confidence, versus ~30% from the earlier 9-trial smoke. This is the opposite of the taxonomy pack's signature.

**But lever 3 (convention-pinning) should be dropped.** It has never moved its only target (`patents:2`: 0/5 → 0/3, and 0/3 in the smoke), and both saturated-task regressions are convention-flavoured: `github_repos:3` pinned "Shell as *primary* language" and answered 0 where passing runs read "Shell in the language mix" (1077); `yelp:1` locked onto a wrong averaging basis (3.50 vs 3.55). A zero result is exactly the sanity signal that should trigger a rethink, and lever 3 instructs the model to hold its convention absent such a signal. Every measured point of gain traces to levers 1 and 2.

**Harness defect found:** `trials.jsonl` is intermittently orphaned — the file at the path is replaced after `_run_interim` opens its append handle, so writes land on an unlinked inode (mtime = shard start, perms 600 instead of 644). `report.md`, `submission.json` and all traces are unaffected because they derive from in-memory results. Incidence varied by arm: dilution lost 10/12 shards, medium 2/12, xhigh 12/12. Results were recovered from the console log via a parser **validated against the shards that wrote correctly** (11/11 tasks matching exactly, including infra exclusion). **This must be fixed before any submission packaging**, and it degrades resume safety in the meantime.
