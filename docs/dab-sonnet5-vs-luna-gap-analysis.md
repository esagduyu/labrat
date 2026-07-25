# DAB gap analysis: Sonnet-5-high (claude-mcp) vs GPT-5.6-Luna-max (labrat-agent, accepted 74.18% entry)

**Date:** 2026-07-24 · **Author:** trace-level task-by-task comparison (READ-ONLY analysis)

Runs compared (270 trials each, 54 tasks × 5):

- **LUNA:** `runs/dab/submission-gpt56-luna-max-ledger-final-270/trials.jsonl` — labrat-agent driver, codex/GPT-5.6-luna @ max, Cartographer + hints + levers + Context Ledger. The accepted PR #72 leaderboard entry.
- **SONNET:** `runs/dab/full12-sonnet5-high-fixes-shards/<dataset>/trials.jsonl` — claude-mcp driver, claude-sonnet-5 @ effort high, Cartographer + hints + levers + local-embed llm_classify + 2026-07-23 catalog/disambiguation fixes. No Context Ledger (claude-mcp has the server-side 64KB ledger only), no dispatch_subagent.

## Actionable conclusions (lead)

**Headline: Luna 74.18% stratified (206/270 micro) vs Sonnet-high 70.31% stratified (197/268 micro; 2 infra:timeout rows excluded). The −3.87pp gap is NOT diffuse — it is 7 LUNA-WIN tasks (−15 trials) partially offset by 4 SONNET-WIN tasks (+11 trials), against 34 parity-pass and 8 parity-fail (shared-hard) tasks.**

The single biggest behavioral difference: **Sonnet explores ~3× shallower than Luna** (median 12 tool calls / 54 s per trial vs Luna's 36 calls / 304 s) and commits to first-pass heuristics — keyword `LIKE` filters, single-format string date compares, partial dedup rules — without an exhaustive cross-check. Nearly every LUNA-WIN trial loss traces to a shortcut that a full enumeration would have caught. Sonnet's wins, conversely, are mostly **driver-feature wins** (local llm_classify, raw-aggregation lever), not model wins.

Ranked gap themes and what closes them (trial-level cost across the run in parentheses):

| # | Theme | Trials lost | What would close it |
|---|-------|------------|---------------------|
| 1 | **Free-text semantic recall: keyword filters miss description-only entities; partial entity-resolution misses the winning cluster; single-format date parsing silently drops rows** (googlelocal:2, music_brainz_20k:3, yelp:7) | −7 | A completeness lever: when candidate selection relies on keyword/regex over a free-text column, either read every row of small tables (googlelocal's business table is 79 rows) or run `llm_classify` (now cheap via local-embed) over the column; before filtering a text-typed date column, enumerate its distinct formats. This is autopsy levers A + C, still unclosed on the claude-mcp path. |
| 2 | **Verbatim value delivery: Sonnet reformats stored tokens** ("5–11PM" → "5PM–11PM") and the exact-substring scorer misses (googlelocal:3) | −3 | Strengthen the adjacent-token/verbatim lever: "quote stored values byte-for-byte — never normalize times, dates, codes, or ranges." Autopsy lever B; the 07-23 adjacent-token lever did not prevent this. |
| 3 | **Methodology instability on multi-convention tasks: 5 trials → 5 different EMA methodologies, never the full tie set** (patents:2) | −3 | Pin conventions before computing: a lever to (a) state the chosen filing-year scope / zero-fill / EMA-init convention explicitly, then (b) enumerate the FULL tie band. Luna converged 3/5 with heavy verification (43–71 calls); Sonnet at 23–32 calls never stabilized. The missing Context Ledger on claude-mcp plausibly contributes (no cross-turn pinning of intermediate conventions). |
| 4 | **Free-text numeric extraction fragility + wall-clock burn on prose-embedded values** (deps_dev_v1:2: regex boundary collision dropped semantic-ui; one 1200 s timeout in O(n²) `LIKE '%name%'` joins) | −1.75 | Lever: extract prose-embedded numbers/names ONCE into a temp table with word-boundary regexes, then join on the temp table; never substring-join two large tables directly. |
| 5 | Noise-band ambiguity (crmarenapro:7 −1; offset by crmarenapro:8 +1) — both models confuse the same pair of knowledge articles | ~0 net | Not worth a lever; shared ambiguity, 1-trial noise. |

Keep (validated by this comparison): **local-embed llm_classify** (+5 trials, agnews:4 — Luna scored 0/5 there on turn-budget exhaustion), **the 07-23 catalog fixes** (crmarenapro:1 now 5/5 parity), and **the push-aggregation lever** (yelp:2: Sonnet aggregated raw review rows 4/5 while Luna trusted the stale `business.review_count` column 4/5 → Missouri instead of PA).

Closing themes 1–3 alone recovers ~13 trials ≈ +4–5pp stratified — enough to put a Sonnet-5 claude-mcp run at or above the Luna entry, since Sonnet's feature wins (+11 trials) are already banked.

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

1. **Shallow first-pass semantics on free text** (googlelocal:2, music_brainz:3, yelp:7 — −7 trials, ~−2.2pp stratified). Close with a completeness lever (enumerate small tables / llm_classify description columns / enumerate date formats before filtering). Highest-leverage single fix.
2. **Verbatim value delivery** (googlelocal:3 — −3 trials, ~−1.5pp on that dataset's mean → ~−0.5pp stratified... at dataset level googlelocal alone is −30pp of which this is −15pp). Close with a "byte-verbatim stored values" lever line.
3. **Convention pinning + full tie enumeration on methodology-heavy tasks** (patents:2 — −3 trials, ~−0.8pp stratified). Close with a state-your-convention-then-enumerate-ties lever; consider bringing a Context-Ledger-equivalent to claude-mcp (GAP already tracked in `project_dab_claude_mcp_feature_gaps`).
4. **Prose-embedded extraction robustness + substring-join perf** (deps_dev_v1:2 — −1.75 trials incl. one timeout). Close with extract-once-to-temp-table guidance.
5. **Keep the wins:** local-embed llm_classify (+5), push-aggregation (+3 on yelp:2), catalog fixes (crmarenapro:1 parity). These are why Sonnet is at 70.3 rather than ~66.

**Attribution note:** themes 1–4 are predominantly MODEL/consistency gaps (Sonnet-high still runs 12-call/54 s trials vs Luna-max's 36-call/304 s — even at `--effort high` it under-explores), with theme 3 partly a DRIVER gap (no in-process ledger on claude-mcp). Sonnet's wins are predominantly DRIVER/FEATURE wins. A prompted-depth lever ("for aggregation/dedup tasks, verify by a second independent derivation before answering") is the cheapest way to buy back the exploration-depth difference without switching drivers.
