# Terra vs Luna Answer-by-Answer Diff — Group B (yelp, stockmarket, googlelocal, agnews, pancancer_atlas, bookreview)

**Confound notice:** TERRA (gpt-5.6-terra @ high, `--agent-taxonomy` ON) and LUNA (gpt-5.6-luna-max, taxonomy OFF, our submitted #5 entry at 74.18%) differ in model checkpoint, reasoning effort, *and* the taxonomy lever simultaneously. Any Terra-vs-Luna delta below is attributed to "Terra config" as a bundle, never specifically to the taxonomy lever, unless the evidence (e.g. identical failure on both sides) says otherwise.

Deduping method: both trials.jsonl files contain resume-retry duplicates (same `trial_num` re-appended after an `infra:`/`terminal:` row). All tallies below use **last-write-wins per `(task_id, trial_num)`** — this changed several counts from a naive full-file scan (e.g. agnews:1 Terra went from a spurious 5/5-with-3-infra-extra to a clean 5/5 no-infra after dedup).

## Pass tally (deduped, infra/turn-budget-exhausted trials excluded from the denominator)

| Dataset | Terra | Luna |
|---|---|---|
| yelp | 28/35 (80.0%) | 31/35 (88.6%) |
| stockmarket | 16/25 (64.0%) | 20/25 (80.0%) |
| googlelocal | 16/20 (80.0%) | 18/20 (90.0%) |
| agnews | 6/10 (60.0%) | 5/10 (50.0%) |
| pancancer_atlas | 7/15 (46.7%) | 10/15 (66.7%) |
| bookreview | 11/15 (73.3%) | 15/15 (100.0%) |
| **Group B total** | **84/120 (70.0%)** | **99/120 (82.5%)** |

Luna beats Terra on 5/6 datasets; agnews is the lone exception (driven entirely by agnews:2, a coin-flip classification query — see below). Excluding agnews:3/4 (identical all-infra/all-timeout on both sides, 5+5=10 trials each, no answer produced on either side).

---

## yelp (7 queries)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| yelp:1 | 4/5 | 5/5 | wrong-computation (noise-leaning) |
| yelp:2 | 2/5 | 1/5 | column-disambiguation (systematic, both sides) |
| yelp:3 | 5/5 | 5/5 | — |
| yelp:4 | 5/5 | 5/5 | — |
| yelp:5 | 4/5 | 5/5 | wrong-computation (same pattern as yelp:1) |
| yelp:6 | 3/5 | 5/5 | enumeration-gap / delivery-format |
| yelp:7 | 5/5 | 5/5 | — |

**yelp:1** (avg rating of Indianapolis businesses, GT 3.547009): Terra's one failing trial computed the average of each business's own average rating — `"Using an equal-weight average of each of the 8 Indianapolis businesses' review averages: 3.4052083333333334"` — instead of the review-weighted pooled mean the other 4 trials (and all 5 Luna trials) use (`"the mean of all 117 review.rating records... 3.547008547008547"`). Aggregation-level ambiguity (business-average-of-averages vs review-weighted pooled mean).

**yelp:2** (state with most reviews + avg rating, GT `PA,3.699`): Both sides split on which "review count" to use. The correct path counts actual `review` table rows (`"PA — 662 reviews"`). The wrong path sums the `business.review_count` metadata field (`"Missouri — 2,243 reviews; average rating 3.91/5"`) — a denormalized column that apparently doesn't match live review-row counts in this synthetic DB. Terra hit the wrong column on 3/5 trials, Luna on 4/5 — **systematic on both sides**, Luna slightly worse. This is a genuine `business.review_count`-vs-`COUNT(review.*)` disambiguation trap.

**yelp:5** (state with most WiFi businesses + avg rating, GT `PA,3.48`): same aggregation-ambiguity pattern as yelp:1 — Terra's one failing trial: `"Average rating is the unweighted mean of each business's review average: PA — 8 WiFi businesses, 3.582654975033492"` vs the correct pooled `3.4839857651245554` every other trial (both sides) gets.

**yelp:6** (highest-rated business Jan–Jun 2016 + all its categories, GT name + 4 categories): Terra's 2 failing trials list only the first category and drop "Breakfast & Brunch": `"Using the first category listed in the business description as the category: ... Restaurants — average rating 4.375"`. Luna 5/5 always emits the full category list. Multi-value field truncated to first element.

---

## stockmarket (5 queries)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| stockmarket:1 | 5/5 | 5/5 | — |
| stockmarket:2 | 1/5 | 5/5 | enumeration-gap |
| stockmarket:3 | 5/5 | 5/5 | — |
| stockmarket:4 | 0/5 | 0/5 | wrong-computation (shared, identical both sides) |
| stockmarket:5 | 5/5 | 5/5 | — |

**stockmarket:2** (list all 31 NYSE-Arca ETFs with 2015 peak Adj Close > $200, GT = 31 named ETFs): Terra abandons full enumeration in 4/5 trials — trial4's answer is literally `"BOIL, BZQ, DUST, EDZ, ERX, FAZ, GUSH — total 7 ETFs"`, trial2/3 stop at 17. Luna enumerates the full 31-row table every single trial (all 5 trials dump the complete markdown table with all 31 tickers/prices). The task requires checking `Adj Close` across ~31 separate per-ticker price tables; Terra gives up partway through the fan-out, Luna doesn't.

**stockmarket:4** (top-5 non-ETF NYSE stocks with more up-days than down-days in 2017, GT: MFA Financial, Argo Group, HDFC Bank, Albany International, DTE Energy) — **both sides fail identically, all 10 trials**, always returning the same wrong 6-name tied list (HDFC Bank, Albany International, Getty Realty, Mettler-Toledo, Ameriprise, Pfizer). I queried the raw `stocktrade_query.db` price tables directly to diagnose: both agents ranked by **raw up-day count** (HDB=146, AIN=143, GTY=143, MTD=143, AMP=141, PFE=141). But GT's actual ranking metric is **net margin (up−down)**: MFO=139−67=+72, ARGD=133−82=+51, HDB=146−102=+44, AIN=143−101=+42, DTQ=139−98=+41 — this net-margin ranking reproduces the GT top-5 exactly, while the top-raw-up-day-count ranking both agents used misses MFA Financial (Argo Group and DTE Energy entirely, since their raw up-day counts (139, 133, 139) are lower than the tied 141-146 cluster despite having a *larger* winning margin). This is a query-ranking-metric ambiguity ("top 5... that had more up days than down days" doesn't literally say "ranked by up-day count") that both agents resolve the same (wrong) way — not a Terra/Luna difference, a shared interpretation bug.

---

## googlelocal (4 queries)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| googlelocal:1 | 5/5 | 5/5 | — |
| googlelocal:2 | 4/5 | 3/5 | delivery-format (Luna markdown-bold pushes value out of validator window) |
| googlelocal:3 | 4/5 | 5/5 | enumeration-gap (tie-handling, noise-leaning) |
| googlelocal:4 | 3/5 | 5/5 | wrong-computation (systematic within Terra) |

**googlelocal:2** (massage businesses ≥4.0 avg rating + scores): the validator only scans **10 chars after the business name** for a decimal score. Luna's 2 failing trials wrap the score in markdown bold: `"- **Angel-A Massage** — **4.33**"` — the `**` markers and em-dash eat into the 10-char budget before the digits appear, so `re.findall(r"(\d+\.\d+)", window)` finds nothing in-window even though the value is correct. Terra's answers are plain `"Angel-A Massage — 4.333333333333333"` with no markdown, so it doesn't hit this trap (4/5). This is a pure delivery-format/proximity issue, not a computation error — both sides compute the right number.

**googlelocal:3** (top-5 open-after-6pm businesses by rating, all tied at 5.0): Terra's one failing trial drops "Mariscos el poblano" from its list of 5 and substitutes a lower-rated "Paradise tattoo" instead — a tie-set enumeration miss. Single-trial, noise-leaning.

**googlelocal:4** (top-3 businesses by count of ≥4.5-rated 2019 reviews, GT: 19/17/14): Terra's 2 failing trials both give the exact same names but with drastically undercounted, wrong review counts — `"Encino Dermatology & Laser: Alex Khadavi MD — 7"` (should be 19), `"The Boochyard @ Local Roots — 7"` (should be 17), `"Widows Peak Salon — 6"` (wrong business entirely, in place of Aurora Massage). Both failing trials state the identical reasoning ("ratings are integer-valued, so 4.5+ means 5-star") that Luna's *correct* trials also state — so the interpretation is right but the counting SQL undercounts by roughly 3x, suggesting a join/filter bug (possibly missing the 2019 date filter's OR-boundary, or a fan-out/dedup difference) reproduced identically twice. Systematic within Terra, not present in Luna at all (5/5).

---

## agnews (4 queries — the classification-heavy dataset)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| agnews:1 | 5/5 | 5/5 | — |
| agnews:2 | 1/5 | 0/5 | free-text-grounding / classification-variance |
| agnews:3 | 0/0 (5× infra:timeout) | 0/0 (5× terminal:turn_budget) | classification-timeout |
| agnews:4 | 0/0 (5× infra:timeout) | 0/0 (5× terminal:turn_budget) | classification-timeout |

**agnews:2** (fraction of Amy Jones's 111 articles that are Science/Technology, GT = 16/111 = 0.14414414414414414): `category` is **not a stored column** — `db_description_withhint.txt` says explicitly *"Determining an article's category requires understanding the meaning of its title and description"*. I traced both sides' tool calls:
- **Terra trial0** (the one pass) never calls `llm_classify`/`run_program` at all — its last tool call just materializes a 111-row temp table of article text and the model answers directly with `16/111 = 0.14414414414414414`, an exact 17-significant-digit match with no tool call verifying it. This is very likely a lucky guess in the plausible 11–19 range, not a real derivation (Terra's other 4 trials guess 15, 17, 17, 18 with equal confidence and get it wrong).
- **Terra trials 1–4** and **all 5 Luna trials** *do* invoke `run_program` (wrapping `llm_classify`), fully classifying all 111 articles with zero nulls each time — and still land on different counts every time: Terra gets 18, 17, 17, 15; Luna gets 13, 12, 11, 14, 13. None hit 16.
- Both configs' `config.json` shows `llm_classify_row_budget: 200`, `llm_classify_reasoning: "max"`, and **no `llm_classify_backend` override** — meaning both runs default to `llm_classify_backend="llm"` (one full-reasoning-effort LLM call per row) rather than the local embedding-based classifier. I found `src/labrat/agent/tools/local_classify.py` (zero-LLM-token, embedding-argmax classification) already built and wired behind the `llm_classify_backend="local-embed"` seam on the leverpack branch — **it exists but neither run enabled it.** Given the per-row LLM classifier itself is noisy near this task's true boundary (11–18 spread around GT's 16), switching to the deterministic local-embed backend wouldn't obviously fix *accuracy* (embedding classification is typically weaker on nuanced category boundaries per the code's own docstring), but it removes classification as a source of run-to-run variance and — critically — removes the per-row LLM cost that's exhausting the budget on :3/:4 below.

**agnews:3 / agnews:4** (avg business articles/year in Europe 2010–2020; largest World-category region in 2015) — **both queries require classifying articles across a full multi-year/region scope**, almost certainly thousands of rows given `llm_classify`'s 200-row hard cap per call, vs the dataset's 127,600 total articles. Traced the actual exhaustion mechanism, and it's *different* per side:
  - **Terra**: reaches 18 tool calls including two `run_program` invocations before hitting `infra:timeout` — i.e., **wall-clock timeout**, consistent with `llm_classify_reasoning: "max"` making each of the many required 200-row-capped classification batches very slow at "high" model reasoning effort.
  - **Luna**: hits `terminal:turn_budget` after only **11 tool calls**, still in the grounding/setup phase (`search_reference_docs → profile_dataset → workflow → attach_database → load_mongo_collection → profile_dataset → link_schema → describe_table`) — it **never even reaches a single classification call**. Luna's `agent_max_turns: 10` is too tight for a query whose grounding alone burns 8-9 turns before the actual multi-thousand-row classify/aggregate work can start.

Both sides produce **zero answer text** on :3/:4 (Terra: `"TimeoutError:"`; Luna: `"[trial exhausted 10-turn budget without a final answer]"`) — so the answer *shape* was never even tested; this is a pure infra/budget failure, not a semantic miss.

---

## pancancer_atlas (3 queries)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| pancancer_atlas:1 | 0/5 | 0/5 | column-disambiguation (shared, 100% reproducible both sides) |
| pancancer_atlas:2 | 4/5 | 5/5 | wrong-computation (cohort-scoping, noise-leaning) |
| pancancer_atlas:3 | 3/5 | 5/5 | wrong-computation (cohort-scoping, systematic within Terra) |

**pancancer_atlas:1** (avg log10 IGF2 expression per histology, GT keyed by ICD-O codes `9382/3`, `9400/3`, etc.): **all 10 trials on both sides fail identically** — every trial reports human-readable histology *names* (`"Astrocytoma"`, `"Oligoastrocytoma"`, `"Oligodendroglioma"`) instead of the numeric ICD-O code strings the validator anchors on (`llm_output.find("9382/3")` etc.). The clinical table clearly has two candidate columns for "histology type" — a readable label and a coded value — and every trial (both configs, all reasoning efforts) picks the readable one. 100% reproducible, zero variance — this is the cleanest column-disambiguation finding in the group.

**pancancer_atlas:2 / :3** (CDH1 mutation prevalence by histology; chi-square test): Terra shows a recurring **cohort-denominator instability** across both queries — its failing trials restrict the population to only "mutation-profiled"/"reliable-mutation-call" patients (N=147/22/35 in :2, N=762 in :3) instead of the full alive-BRCA population (N=178/24/36, N=1,059) the validator expects. In :3 this flips the chi-square statistic from the correct 305.12 to a wrong 325.34 on 2/5 Terra trials. Luna is 5/5 on both, always using the full population. Same root ambiguity ("reliable mutation entries" in the :3 query text literally invites this narrower-cohort reading) recurring twice within Terra — a real pattern, though the query wording itself (`"consider only reliable mutation entries"`) partially licenses the narrower interpretation, so I'd call this half query-ambiguity / half Terra-specific instability.

---

## bookreview (3 queries)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| bookreview:1 | 4/5 | 5/5 | wrong-computation (single outlier) |
| bookreview:2 | 3/5 | 5/5 | enumeration-gap |
| bookreview:3 | 4/5 | 5/5 | free-text-grounding (category-membership edge case) |

**bookreview:1** (decade with highest avg rating, ≥10 books, GT "2020s"): one Terra trial answers `"2010s"` outright — no reasoning shown, single flipped value, noise.

**bookreview:2** (15 perfect-5.0 English "Literature & Fiction" books): Terra's 2 failing trials return incomplete lists (11 titles, then a different 16-title list with substitutions) — dropping titles like `"Something That Feels Like Truth (Switchgrass Books)"` and `"Exits, Desires, & Slow Fires"` on different trials. Same partial-enumeration pattern as yelp:6/googlelocal:3 — Terra intermittently truncates multi-row enumerations.

**bookreview:3** (Children's Books ≥4.5 avg rating from 2020+ reviews): Terra's one failing trial drops `"Cheer Up, Ben Franklin!"` and substitutes 2 non-GT books (`"Kirsten: An American Girl"`, `"The Very Hungry Caterpillar"`) — suggests a fuzzy/inconsistent match against the free-text "Children's Books" category field rather than a hard filter miss (both correct and incorrect books stay in the same rating range, so it's category-membership noise, not a rating-threshold bug).

---

## Group summary

### Systematic vs noise

**Systematic (reproducible pattern, not single-trial flips):**
1. **agnews:2/3/4 — classification variance + budget exhaustion.** Every trial that runs a full 111-row classification lands on a different count (11–18, true=16); agnews:3/4 never even get an answer on either side. Root cause: no per-row-deterministic classification path is enabled; `llm_classify_reasoning="max"` + 200-row cap makes full-corpus classification too slow/turn-hungry for `agent_max_turns=10`.
2. **yelp:2 — `business.review_count` vs live review-row count column trap.** Wrong on 3/5 Terra, 4/5 Luna — both sides, not a Terra/Luna difference.
3. **pancancer_atlas:1 — histology code vs histology name column pick.** 100% reproducible failure on *every* trial, both sides — the single cleanest finding in the group.
4. **stockmarket:4 — "top N" ranking-metric ambiguity (raw count vs net margin).** 0/5 both sides, identical wrong 6-name answer every single trial.
5. **Terra-only partial-enumeration tendency.** Recurs across yelp:6, googlelocal:3, stockmarket:2, bookreview:2 — Terra intermittently truncates multi-row list answers (first-category-only, drops one tied item, gives up mid-fan-out) in a way Luna essentially never does in this 6-dataset sample (Luna is 5/5 on every one of those four queries).
6. **Terra-only aggregation-method ambiguity (weighted vs unweighted mean).** Recurs in yelp:1 and yelp:5 — same "average of per-business averages" mistake, absent from all 25 Luna×5 trials sampled here.

**Noise (single-trial flips, no discernible pattern):** bookreview:1 (one wrong decade), googlelocal:3 (one dropped tied business), yelp:1/yelp:5 individual trial (same pattern as above so arguably systematic-but-rare), pancancer_atlas:2 (one cohort-scoping slip, same root cause as :3 so also borderline-systematic).

**Do not over-attribute to `--agent-taxonomy`.** Terra and Luna differ in model (gpt-5.6-terra vs gpt-5.6-luna-max), reasoning effort, and the taxonomy lever simultaneously — the two "shared, identical-both-sides" failures (stockmarket:4, pancancer_atlas:1, and the yelp:2 column trap) prove the underlying agent stack has real gaps independent of any of those three variables. The Terra-only patterns (partial enumeration, aggregation-method flips) *could* be the taxonomy addendum nudging toward terser/first-match answers, but could equally be `high` vs `max`-reasoning-effort model behavior or plain run-to-run variance — this group's data can't separate the three.

### Prioritized fix list (existing tools/prompts)

1. **`llm_classify` budget/backend for large-fan-out classification (rescues agnews:2, agnews:3, agnews:4 — 3 of 4 agnews queries).** The local embedding-based backend (`src/labrat/agent/tools/local_classify.py`, `llm_classify_backend="local-embed"`) is built and wired but never enabled on either run (`config.json` shows no override, so both default to `"llm"` + `reasoning:"max"` + 200-row cap). For classification-shaped DAB queries specifically: (a) route through the deterministic local-embed backend to kill both the per-trial variance (11–18 spread on agnews:2) and most of the latency driving agnews:3/4's timeouts; (b) separately, `agent_max_turns=10` (Luna's config) is too tight for any query whose grounding phase alone burns 8-9 turns before classification can start — either raise the turn budget for classification-shaped queries or compress the mandatory grounding sequence.

2. **Column selection guidance in `link_schema`/`describe_table` for denormalized/coded-vs-readable columns (rescues pancancer_atlas:1 outright — 100% failure, all 10 trials — plus mitigates yelp:2, 7/10 trials wrong).** Both `business.review_count` (yelp) and the ICD-O code vs histology-name pair (pancancer_atlas) are cases where the schema has two plausible columns for what the query is asking about, and the agent silently picks the "friendlier" one every time. A retrieval-time hint ("when a query names a coded/ID field like a histology code, prefer the literal code column over a semantically-equivalent label column"; "prefer computing counts from fact-table rows over pre-aggregated summary columns when both exist") in `link_schema`/`describe_table` output would directly address the two cleanest systematic failures in this group.

3. **`_dab_lever_lines`-style enumeration-completeness reminder for large fan-outs and tied results (rescues stockmarket:2, mitigates yelp:6, googlelocal:3, bookreview:2).** Terra repeatedly truncates multi-row answers (first-category-only, drops one of N tied items, abandons a 31-row per-ticker-table scan partway through). A lever reminding the agent to verify row-count-returned == row-count-expected before finalizing (especially for "list all X" / "top N with ties" queries) would catch this class; it's a prompt-side fix, no new tool needed.

4. **Ranking-metric ambiguity check for underspecified "top N" queries (rescues stockmarket:4, shared 0/5 both sides).** When a query states a qualifying condition ("more up days than down days") but "top" isn't tied to an explicit sort key, the agent should try/state the most natural alternate metric (net margin vs raw count) rather than silently picking one. Lower priority — this is a single query in the sample and the fix is more prompt-engineering than tooling.

5. **Aggregation-level guidance (business-weighted vs review-weighted mean) — minor, rescues 2 Terra single-trial flips (yelp:1, yelp:5).** Same class of fix as #2 (schema/column guidance), lower priority given it's noise-adjacent (occurs 2/25 Terra trials, 0/25 Luna trials sampled).

### Is agnews classification rescuable via the built-but-unused local-NLI/embedding backend?

Partially. It would remove the *timeout* mechanism on agnews:3/4 (fewer, cheaper classification calls instead of many max-reasoning-effort per-row LLM calls) and likely stabilize agnews:2's run-to-run variance, but the local-embed backend's own docstring flags it as "weaker than a dedicated zero-shot NLI model on nuanced labels" — so it isn't guaranteed to land exactly on GT's specific 16/111 boundary, only to stop the wild 11-18 swings and stop burning the entire turn/time budget on setup + partial classification before any answer is produced.

---

*Regenerated 2026-07-23 from transcript after accidental deletion.*
