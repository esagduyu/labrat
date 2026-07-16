# GPT-5.6 DAB grounding and model-tier ablation

Status: **FINAL EVIDENCE — LEDGER PROMOTED; TRACE-COMPLETE 270-ROW LUNA-MAX PACKAGE ASSEMBLED**

Last updated: 2026-07-16

Live snapshot: all five Luna-Max grounding arms have completed all 45 semantic
keys. The sharded arms were canonically merged only after complete coverage and
clean trace audits. ContextLedger is the official grounding winner: it improved
the headline stratified score by 0.3968pp versus Hints while reducing absolute
noncached input by 750,581 tokens (-6.5503%). It lost one raw pass and used more
requests, tools, and latency, so the promotion is a stratified-quality plus
noncached-efficiency decision rather than a claim of dominance on every metric.
The exact winning flags are frozen below for the matched hard-tail tier study.
Luna Max completed the hard-tail tier cohort at 18/18 semantic keys and 9/18
passes. Terra High and Sol High reached their retry ceilings with only 14/18
and 9/18 semantic keys, respectively. Sol Ultra reached 6/18 before the
fail-fast `infra:rate_limit` path stopped all workers with exit 4. The promoted
Luna-Max campaign first completed 260/270 semantic keys with 206 passes. The ten
missing keys were `agnews:3` and `agnews:4` across trials 0-4: their row-wise
`llm_classify` path repeatedly reached the 2,400-second guard without a final
answer. The approved narrow recovery reran only those ten keys with Luna Max,
ten main turns, a 200-row cumulative classification budget, and the normal
finite timeout. All ten reached the main-turn cap before calling
`llm_classify`; each is now an honestly scored `terminal:turn_budget` failure
with a matching final trace event. The assembled package therefore has exactly
270 answer rows, 270 unique keys, and 270 nonempty traces, with 206 passes and a
74.1758% dataset-stratified score. The task-specific cap must be disclosed and
accepted by DAB maintainers; this report does not present it as an ordinary
unconstrained run. The final trace-integrity and exact PR #65 audits are complete
and linked below.

## Technical summary

The GPT-5.6 Luna Max bare baseline is complete: 35/45 semantic trials passed
(77.8% micro rate), for the benchmark's dataset-stratified score of **74.4%**.
The dataset cuts are `deps_dev_v1` 3/6 (50.0%), `music_brainz_20k` 6/9
(66.7%), `stockindex` 9/9 (100%), and Yelp 17/21 (81.0%). The persistent
0/3 misses are `deps_dev_v1:1`, `music_brainz_20k:3`, and `yelp:2`;
`yelp:3` passed 2/3. All other queries passed 3/3.

The baseline retains 15 retryable infrastructure attempts (12 `agent_error`,
three `rate_limit`) alongside the 45 selected semantic attempts. No latest
attempt is infrastructure-only. All 45 selected attempts are marked clean in
`taint.json`, and all 45 have a nonempty per-attempt tool trace. The completed
run contains `submission.json` and `report.md` and exited successfully.

Across the selected semantic attempts, the adapter recorded 31,578,005 input
tokens, 21,846,272 cached tokens (**69.18% cache-read ratio**), and 9,731,733
noncached input tokens. It made 2,333 completed model requests (51.8 per trial),
used 1,708 tools, and took 14,720.7 seconds. The high request count is partly
driven by tool-internal LLM fan-out on some Yelp queries; cache percentage is
therefore supporting context, while absolute noncached input remains the
efficiency comparison metric.

The matched Cartographer-only arm completed at 33/45 semantic passes and 65.7%
stratified, below the baseline's 35/45 and 74.4%. It raised the aggregate
cache-read ratio from 69.18% to 70.35% while increasing absolute noncached
input from 9,731,733 to 10,057,480 (+3.3%). Its gains on Yelp queries 2 and 3
did not offset regressions on MusicBrainz query 1 and Stockindex query 1.
Cartographer is therefore not promoted on its own.

The canonical Cartographer-plus-levers arm also completed at 33/45 semantic
passes and **65.6746% stratified**: `deps_dev_v1` 50.0%,
`music_brainz_20k` 44.4%, `stockindex` 77.8%, and Yelp 90.5%. Across semantic
attempts it recorded 34,949,221 input tokens, 24,438,528 cached tokens
(**69.9258%**), 10,510,693 noncached input tokens, 518,943 output tokens,
2,315 completed requests, and 17,002.1 seconds of latency. Versus matched
Cartographer, levers produced no accuracy gain while increasing noncached
input by 453,213 (+4.51%), requests by 124 (+5.66%), and latency by 2,728.8
seconds (+19.12%), with cache ratio down 0.43pp. Versus the bare baseline, it
was down 8.73pp stratified and two passes while increasing noncached input by
778,960 (+8.00%). Three retryable infrastructure rows are preserved and
excluded; all 45 selected semantic attempts have clean, nonempty traces.

The canonical Cartographer-plus-levers-plus-hints arm was the quality leader
before the matched Ledger comparison: 40/45 semantic trials passed (**88.8889%
micro**) for **83.5317%
stratified**. Its dataset cuts are `deps_dev_v1` 3/6 (50.0%),
`music_brainz_20k` 9/9 (100%), `stockindex` 8/9 (88.8889%), and Yelp 20/21
(95.2381%). Across semantic attempts it recorded 38,707,688 input tokens,
27,248,896 cached tokens (**70.3966%**), 11,458,792 noncached input tokens,
533,380 output tokens, 2,086 completed requests, 16,414.0 seconds of latency
(364.76 seconds mean), and 1,617 tool calls. Twenty retryable infrastructure
rows are preserved and excluded; all 45 selected semantic attempts have clean,
nonempty traces.

Versus levers, hints gained **17.8571pp stratified** and seven passes while
increasing noncached input by 948,099 (+9.0202%). Cache-read ratio rose
0.4708pp, requests fell by 229 (-9.892%), latency fell by 588.04 seconds
(-3.4586%), and tool calls fell by 61. Versus the bare baseline, hints gained
**9.1270pp stratified** and five passes while increasing noncached input by
1,727,059 (+17.747%); cache-read ratio rose 1.2147pp, requests fell by 247
(-10.588%), and latency increased by 1,693.37 seconds (+11.503%). Hints is the
pre-Ledger quality leader and the correct Ledger parent, but it is not an absolute
noncached-efficiency win.

The canonical ContextLedger arm completed 45/45 semantic trials with 39 passes
(**86.6667% micro**) and **83.9286% stratified**. Its dataset cuts are
`deps_dev_v1` 3/6 (50.0%), `music_brainz_20k` 9/9 (100%), `stockindex` 9/9
(100%), and Yelp 18/21 (85.7143%). Across semantic attempts it recorded
37,680,115 input tokens, 26,971,904 cached tokens (**71.5813%**), 10,708,211
noncached input tokens, 559,647 output tokens, 2,192 completed requests,
18,280.2 seconds of latency (406.227 seconds mean), and 1,730 tool calls. Nine
retryable infrastructure rows are preserved and excluded; all 45 selected
semantic attempts have clean, nonempty traces.

Versus Hints, Ledger gained **0.3968pp stratified** and improved StockIndex by
one pass, while losing one raw pass overall because Yelp lost two; dependencies
and MusicBrainz were unchanged. It reduced noncached input by 750,581
(-6.5503%) and raised cache-read ratio by 1.1847pp, but increased requests by
106 (+5.0815%), latency by 1,866.19 seconds (+11.3695%), and tool calls by 113
(+6.9883%). The official stratified-plus-noncached promotion rule therefore
selects Ledger for the tier-study grounding configuration, with the explicit
caveat that it is slower, makes more requests, and loses one raw pass at this
descriptive `n=3` denominator.

The hard-tail tier evidence supports only one complete model result. Luna Max
covered all 18 planned keys and passed 9/18; its DAB-style five-dataset
stratified score is **40.0%**, while the unweighted six-query/raw rate is
**50.0%**. It passed both MusicBrainz queries and Patents 3/3 each, and missed
CRM Arena Pro, dependencies, and PanCancer 0/3 each. Terra High's 7/14, Sol
High's 5/9, and Sol Ultra's 3/6 are survivor-only semantic rates, not matched
model-tier accuracy estimates. Their planned-key pass rates are 38.8889%,
27.7778%, and 16.6667%, respectively, versus Luna's complete 50.0%.

The final full-campaign integrity audit found no confirmed cheating or external
result access: 254/270 selected traces are clean, 16 are
suspicious-but-not-proven, and none are contaminated. All 206 passing rows have
persisted local-data evidence. The verdict remains conditional because raw model
message bodies are absent, 25 earlier infrastructure traces were overwritten,
and timed-out tools may omit their final in-flight event. On all 270 exact keys,
the current campaign scores 206/270 (76.2963%) versus 176/270 (65.1852%), a
gain of 30 passes and 11.1111pp. The full dataset-stratified scores are 74.1758%
versus 60.8822% (+13.2937pp), with 23 task gains, eight regressions, and 23
ties. This is descriptive historical evidence, not a model-only causal estimate.

The experiment is pre-registered as a cumulative five-arm Luna Max comparison over 15 DAB queries and three trials per query: bare baseline, then Cartographer, prompt levers, benchmark hints, and ContextLedger. Each arm is 45 semantic trials. The completed winner, ContextLedger, is now the fixed grounding configuration for a separate four-tier hard-tail comparison: Luna Max, Terra High, Sol High, and Sol Ultra.

All tables below distinguish live status from completed results. `PENDING` means no supported value exists; it must never be replaced with a zero. The experiment is descriptive at `n=3`, not powered for statistical significance. The decision target is whether GPT-5.6 preserves the known Sonnet grounding gains, whether the ledger lowers context cost without losing accuracy, and whether larger tiers clear failures that Luna does not.

## Caching worked; Hints wins raw accuracy and Ledger wins the registered promotion rule

Prompt caching is operationally successful but is not a quota-safety guarantee.
Every campaign arm reports cached tokens and 100% cache-write-presence coverage.
The five complete Luna feature arms achieved semantic cache-read ratios from
69.1819% to 71.5813%, and the complete Luna hard-tail arm reached 78.1201%.
Caching therefore removed substantial repeated input from billing-equivalent
accounting. It did not prevent infrastructure exhaustion: the prompt-caching
guide states that caching does not change rate limits, and Sol Ultra still
stopped on `infra:rate_limit` after substantial retry overhead.

The selected 270-row package recorded 229,857,456 input tokens, 163,544,576
cached tokens (**71.1504% cache-read ratio**), 66,312,880 noncached input
tokens, 3,240,606 output tokens, and 13,174 completed requests. Cache-write
presence is reported on 100% of request metadata. The original 260 semantic
rows account for 229,236,566 input, 163,194,624 cached, 66,041,942 noncached,
3,231,643 output, and 13,074 requests; the ten bounded failures added 620,890
input, 349,952 cached (**56.3630%**), 270,938 noncached, 8,963 output, and 100
requests. Including all 35 preserved historical infrastructure rows in the
source shards—not the final package—yields 249,709,805 input tokens,
176,283,648 cached tokens (**70.5954%**), 73,426,157 noncached input tokens,
17,870 requests, and a $113.9937 public-API equivalent rather than a
subscription invoice.

Caching is therefore real but does not approach a host-native 90-95% ratio on
this architecture. Among semantic requests, 10,448 exact-replay requests
achieved a 71.4476% aggregate cache ratio, while 2,626 initial-full requests
achieved 44.1991%; 76.37% of semantic requests had a positive cache read. The
remaining noncached input comes from the growing replayed transcript, novel
tool output, first requests, and tool-internal model calls. The AG News failure
makes the limitation concrete: the latest ten unresolved attempts contained
2,362 row-classification `initial_full` requests with only a 7.5637% cache
ratio. Prompt caching reduces repeated-prefix cost; it cannot make thousands of
novel row-level requests quota-safe.

Hints has the highest raw feature-arm accuracy at **40/45 (88.8889%)**. Ledger
has one fewer raw pass, but it has the highest preregistered stratified score at
**83.9286%** and reduces semantic noncached input from 11,458,792 to 10,708,211
(-750,581; -6.5503%). Its semantic public-API price equivalent is also lower
($16.7633 versus $17.3840), and its all-attempt equivalent is lower after
including retry usage ($18.9869 versus $22.2528). The registered
stratified-plus-noncached promotion rule therefore selects Ledger with the
already-recorded raw-pass, request, and latency caveats.

## Live status: all five grounding arms complete; Ledger promoted

| Arm | Run directory | Current state | Semantic progress | Supported conclusion |
|---|---|---|---:|---|
| B — bare baseline | `runs/dab/ablation-gpt56-luna-max-baseline` | **COMPLETE** | 45 / 45 | **74.4% stratified; 35/45 micro; 69.18% cached; 9.73M noncached input.** Fifteen infrastructure rows are preserved and excluded. |
| C — +Cartographer | `runs/dab/ablation-gpt56-luna-max-cartograph` | **COMPLETE** | 45 / 45 | **65.7% stratified; 33/45 micro; 70.35% cached; 10.06M noncached input.** Accuracy and absolute noncached input both regressed. |
| L — +levers | `runs/dab/ablation-gpt56-luna-max-levers` | **COMPLETE** | 45 / 45 | **65.6746% stratified; 33/45 micro; 69.9258% cached; 10.51M noncached input.** No accuracy gain over Cartographer, with higher noncached input, requests, and latency; three infrastructure rows are preserved and excluded. |
| H — +hints | `runs/dab/ablation-gpt56-luna-max-hints` | **COMPLETE** | 45 / 45 | **83.5317% stratified; 40/45 micro; 70.3966% cached; 11.46M noncached input.** Pre-Ledger quality leader and canonical Ledger parent; twenty infrastructure rows are preserved and excluded. |
| G — +ledger | `runs/dab/ablation-gpt56-luna-max-ledger` | **COMPLETE — PROMOTED** | 45 / 45 | **83.9286% stratified; 39/45 micro; 71.5813% cached; 10.71M noncached input.** Official tier-study grounding winner: +0.3968pp stratified and -6.5503% noncached input versus Hints, with one fewer raw pass and higher requests and latency. Nine infrastructure rows are preserved and excluded. |

## Full Luna-Max campaign: all 270 rows and traces are assembled

The promoted Luna-Max configuration now contains **206/270 passing semantic
rows (76.2963%)** across all 54 tasks. Its full DAB-style dataset-stratified
score is **74.1758%**. The ten added AG News rows are traced terminal failures,
not successful answers: all reached the declared ten-turn cap before the
200-row classifier budget was used. The final package has no infrastructure
rows, while the source shards retain all 35 historical infrastructure rows.
The assembled handoff directory is
`runs/dab/submission-gpt56-luna-max-ledger-final-270`.

| Dataset | Passes / semantic | Semantic rate | Cache-read ratio | Noncached input | Requests | Preserved infra rows |
|---|---:|---:|---:|---:|---:|---:|
| `agnews` | 5 / 20 | 25.0000% | 50.8421% | 2,092,535 | 1,817 | 18 |
| `bookreview` | 15 / 15 | 100.0000% | 59.717% | 3,209,058 | 534 | 0 |
| `crmarenapro` | 53 / 65 | 81.5385% | 75.009% | 17,869,755 | 2,997 | 4 |
| `deps_dev_v1` | 5 / 10 | 50.0000% | 75.569% | 3,848,204 | 589 | 1 |
| `github_repos` | 11 / 20 | 55.0000% | 68.871% | 5,689,564 | 826 | 1 |
| `googlelocal` | 18 / 20 | 90.0000% | 62.737% | 2,832,481 | 614 | 1 |
| `music_brainz_20k` | 14 / 15 | 93.3333% | 66.087% | 4,301,458 | 553 | 1 |
| `pancancer_atlas` | 10 / 15 | 66.6667% | 68.712% | 3,433,236 | 571 | 0 |
| `patents` | 10 / 15 | 66.6667% | 73.073% | 6,909,981 | 953 | 2 |
| `stockindex` | 14 / 15 | 93.3333% | 70.969% | 2,571,646 | 515 | 1 |
| `stockmarket` | 20 / 25 | 80.0000% | 73.646% | 5,728,654 | 953 | 2 |
| `yelp` | 31 / 35 | 88.5714% | 68.168% | 7,826,308 | 2,252 | 4 |
| **Total / full package** | **206 / 270** | **76.2963%** | **71.1504%** | **66,312,880** | **13,174** | **35 in source runs; 0 selected** |

The ten AG News keys did not fail because of transient 429s. Tasks 3 and 4
require category inference over 14,860 and 6,696 rows, respectively, and
`llm_classify` makes one sequential subscription request per row. At the
observed 4.45 seconds per classified row, exhaustive five-trial completion is
approximately 133 hours and 107,780 row-model requests. Seventeen recorded
timeouts already consumed 3,884 requests and 3,628,997 noncached input tokens
without one semantic result.

The timeout is inside one outer `llm_classify` tool dispatch, not a shortage of
main-agent turns. `max_turns` and the outer tool-call limit therefore do not cap
the thousands of nested row-model requests. Prompt caching also cannot remove
the dominant work because each request contains a different article. Batching
reduces dispatch overhead, but the Luna-Max smoke still spent its 2,400-second
budget producing long reasoning responses and did not terminalize a result.

DataAgentBench requires five runs per query (270 entries), a trace for every
run, and agreement between each submitted answer and its trace. Its published
rubric does not require every run to return a correct or complete answer. The
current LabRat runner is stricter: it tags `TimeoutError` as `infra:timeout`,
automatically retries it, and the strict shard merger excludes all `infra:`
rows from its required semantic coverage. That local policy—not an explicit
DAB submission rule—is why the ten bounded failures could not be merged.

The clean submission recovery is to rerun only these ten Luna-Max trials under
a declared evaluator-enforced nested-request/row budget and the same finite
wall-clock policy. Exhausting that budget should append a terminal failure
event to the trace, produce the identical failure answer in the submission,
and count as a normal scored failure rather than retryable infrastructure. The
existing timeout rows should not be relabeled after the fact because
cancellation can leave their traces without the in-flight outer tool event or
a terminal answer. This recovery does not require a Luna-Low classifier.

That narrow recovery is now complete. It reran only `agnews:3` and `agnews:4`
trials 0-4 with Luna Max for both the main agent and classifier, a 200-row
cumulative classifier budget, ten main turns, and the normal 1,200-second
timeout. The ten-turn cap bound first in every trial: none called
`llm_classify`, and none reached either the row or wall-clock cap. All ten
submitted the exact artifact `[trial exhausted 10-turn budget without a final
answer]`, failed with `terminal:turn_budget`, and ended with a matching
`runner_turn_budget` trace event. The original timeout attempts remain
untouched in the source shards and are not relabeled or selected.

### Baseline trial detail

| Field | `deps_dev_v1:1` trial 0 | `deps_dev_v1:1` trial 1 | `deps_dev_v1:1` trial 2 |
|---|---:|---:|---:|
| Result | **FAIL** | **FAIL** | **FAIL** |
| Validator reason | `Version '3.25.4' not found after name '@dylanvann/svelte'` | `Version '3.25.4' not found after name '@dylanvann/svelte'` | `Version '3.25.4' not found after name '@dylanvann/svelte'` |
| Tool calls | 70 | 64 | 75 |
| Wall time | 540.182s | 482.466s | 536.788s |
| Input tokens | 1,956,551 | 1,553,366 | 2,013,253 |
| Cached tokens | 1,326,592 | 1,211,648 | 1,539,328 |
| Cache-read ratio | 67.8% | 78.0% | 76.46% |
| Noncached input | 629,959 | 341,718 | 473,925 |
| Cache-write tokens | 0, reported on all 71 completed requests | 0, reported on all 65 completed requests | 0, reported on all 76 completed requests |
| Output tokens | 21,463 | 19,237 | 20,519 |
| Reasoning tokens | 13,191, already included within output-token accounting | 11,624, already included within output-token accounting | 13,351, already included within output-token accounting |
| Completed requests | 71 | 65 | 76 |
| HTTP attempts | 72 | 65 | 76 |
| Cache pacing wait | 32.040s total | 27.612s total | 40.222s total |
| Cache-breakpoint fallbacks | 1 | 0 | 0 |
| Reasoning-passback fallbacks | 0 | 0 | 0 |

Trial 0's one-attempt gap between 72 HTTP attempts and 71 completed requests is explained by the cache-breakpoint compatibility fallback. Trials 1 and 2 had no fallbacks: all 65 and 76 HTTP attempts completed. In every trial the first completed request used `initial_full`, and every later request used `exact_replay`. These uncapped, ledger-off observations demonstrate the workload's scale, but one 0/3 query does not establish a typical trial cost, cache rate, or arm failure rate. Cross-trial cache differences are descriptive only, not a causal warm-cache estimate.

### Completed `deps_dev_v1:1` query aggregate

| Query result | Input tokens | Cached tokens | Cache-read ratio | Noncached input | Tool calls | Wall time |
|---:|---:|---:|---:|---:|---:|---:|
| **0 / 3** | 5,523,170 | 4,077,568 | 73.83% | 1,445,602 | 209 | 1,559.436s |

The query aggregate is complete and can be compared with the same query in later arms. It must not be promoted to the four-dataset baseline score; the companion deps query is reported below and the other 13 queries remain unmeasured.

### Completed `deps_dev_v1:2` observations

| Field | `deps_dev_v1:2` trial 0 | `deps_dev_v1:2` trial 1 | `deps_dev_v1:2` trial 2 |
|---|---:|---:|---:|
| Result | **PASS** | **PASS** | **PASS** |
| Validator reason | `All project names found.` | `All project names found.` | `All project names found.` |
| Tool calls | 39 | 54 | 60 |
| Wall time | 303.475s | 370.439s | 428.413s |
| Input tokens | 784,432 | 1,131,840 | 1,342,688 |
| Cached tokens | 438,528 | 823,040 | 1,025,280 |
| Cache-read ratio | 55.90% | 72.72% | 76.36% |
| Noncached input | 345,904 | 308,800 | 317,408 |
| Cache-write tokens | 0 | 0 | 0 |
| Output tokens | 11,475 | 14,094 | 14,574 |
| Reasoning tokens | 7,163, already included within output-token accounting | 7,937, already included within output-token accounting | 8,999, already included within output-token accounting |
| Completed requests | 40 | 55 | 61 |
| HTTP attempts | 40 | 55 | 61 |
| Cache pacing wait | 27.917s total | 27.854s total | 26.337s total |
| Cache-breakpoint fallbacks | 0 | 0 | 0 |
| Reasoning-passback fallbacks | 0 | 0 | 0 |

`deps_dev_v1:2` is complete at 3/3. Combined with `deps_dev_v1:1` at 0/3, this closes the dataset cut at 3/6 (50.0%).

### Completed `deps_dev_v1` dataset aggregate

| Dataset result | Input tokens | Cached tokens | Cache-read ratio | Noncached input | Tool calls | Wall time |
|---:|---:|---:|---:|---:|---:|---:|
| **3 / 6 (50.0%)** | 8,782,130 | 6,364,416 | 72.47% | 2,417,714 | 362 | 2,661.763s |

This dataset cut can be compared with `deps_dev_v1` in later arms. It must not be promoted to the stratified four-dataset baseline score while the other three dataset cuts remain unmeasured.

### Completed `music_brainz_20k:1` observations

| Field | `music_brainz_20k:1` trial 0 | `music_brainz_20k:1` trial 1 | `music_brainz_20k:1` trial 2 |
|---|---:|---:|---:|
| Result | **PASS** | **PASS** | **PASS** |
| Validator reason | `Ground truth found in LLM output.` | `Ground truth found in LLM output.` | `Ground truth found in LLM output.` |
| Answer | `Apple Music made $1,059.46 USD in Canada from Beyoncé’s “Get Me Bodied.”` | `Apple Music made $1,059.46 USD from Beyoncé’s “Get Me Bodied” in Canada.` | `Apple Music made $1,059.46 USD.` |
| Tool calls | 23 | 24 | 21 |
| Wall time | 144.816s | 128.608s | 121.648s |
| Input tokens | 184,401 | 200,989 | 180,254 |
| Cached tokens | 108,032 | 123,136 | 97,280 |
| Cache-read ratio | 58.59% | 61.27% | 53.97% |
| Noncached input | 76,369 | 77,853 | 82,974 |
| Cache-write tokens | 0 | 0 | 0 |
| Output tokens | 4,278 | 4,027 | 4,016 |
| Reasoning tokens | 3,083, already included within output-token accounting | 2,656, already included within output-token accounting | 2,716, already included within output-token accounting |
| Completed requests | 24 | 25 | 22 |
| HTTP attempts | 24 | 25 | 22 |
| Cache pacing wait | 16.688s total | 22.507s total | 21.199s total |
| Cache-breakpoint fallbacks | 0 | 0 | 0 |
| Reasoning-passback fallbacks | 0 | 0 | 0 |

### Completed `music_brainz_20k:1` query aggregate

| Query result | Input tokens | Cached tokens | Cache-read ratio | Noncached input | Tool calls | Wall time |
|---:|---:|---:|---:|---:|---:|---:|
| **3 / 3** | 565,644 | 328,448 | 58.06% | 237,196 | 68 | 395.072s |

This query was a historical hard-tail case—`music_brainz_20k:1` was 0/5 in the clean Sonnet run—but the 3/3 Luna Max result still does not isolate a model or configuration effect. It is a strong descriptive reversal on this query; the other two music-brainz queries and the dataset score remain pending.

### Completed `music_brainz_20k:2` observations

| Field | `music_brainz_20k:2` trial 0 | `music_brainz_20k:2` trial 1 | `music_brainz_20k:2` trial 2 |
|---|---:|---:|---:|
| Result | **PASS** | **PASS** | **PASS** |
| Validator reason | `Ground truth found in LLM output.` | `Ground truth found in LLM output.` | `Ground truth found in LLM output.` |
| Answer | `Amazon Music earned the most revenue: $304.13 USD across all countries.` | `Amazon Music earned the most: $304.13 in total revenue.` | `Amazon Music earned the most revenue: $304.13.` |
| Tool calls | 19 | 22 | 21 |
| Wall time | 89.617s | 105.391s | 95.859s |
| Input tokens | 144,752 | 177,335 | 157,531 |
| Cached tokens | 87,296 | 98,048 | 98,048 |
| Cache-read ratio | 60.31% | 55.29% | 62.24% |
| Noncached input | 57,456 | 79,287 | 59,483 |
| Cache-write tokens | 0 | 0 | 0 |
| Output tokens | 2,426 | 2,900 | 2,220 |
| Reasoning tokens | 1,412, already included within output-token accounting | 1,709, already included within output-token accounting | 1,174, already included within output-token accounting |
| Completed requests | 20 | 23 | 22 |
| HTTP attempts | 20 | 23 | 22 |
| Cache pacing wait | 17.598s total | 20.770s total | 23.815s total |
| Cache-breakpoint fallbacks | 0 | 0 | 0 |
| Reasoning-passback fallbacks | 0 | 0 | 0 |

### Completed `music_brainz_20k:2` query aggregate

| Query result | Input tokens | Cached tokens | Cache-read ratio | Noncached input | Tool calls | Wall time |
|---:|---:|---:|---:|---:|---:|---:|
| **3 / 3** | 479,618 | 283,392 | 59.09% | 196,226 | 62 | 290.866s |

The first two music-brainz queries are a combined 6/6. Query 3 completes the dataset below.

### Completed `music_brainz_20k:3` observations

| Field | `music_brainz_20k:3` trial 0 | `music_brainz_20k:3` trial 1 | `music_brainz_20k:3` trial 2 |
|---|---:|---:|---:|
| Result | **FAIL** | **FAIL** | **FAIL** |
| Validator reason | Expected fuzzy match `Zo gaat het leven aan je voor` was absent; best score 0.13. | Expected fuzzy match `Zo gaat het leven aan je voor` was absent; best score 0.34. | Expected fuzzy match `Zo gaat het leven aan je voor` was absent; best score 0.33. |
| Answer | `Systemisch bled by Stüngö — $2,522.82 total revenue.` | `“Systemisch bled” by Stüngö generated the highest total revenue: $2,522.82.` | `“Systemisch bled” by Stüngö generated the highest total revenue: $2,522.82 across 5 sales, 3 countries, and 3 stores.` |
| Tool calls | 22 | 12 | 25 |
| Wall time | 138.000s | 80.693s | 133.964s |
| Input tokens | 194,171 | 81,260 | 232,622 |
| Cached tokens | 122,112 | 52,480 | 151,040 |
| Cache-read ratio | 62.89% | 64.58% | 64.93% |
| Noncached input | 72,059 | 28,780 | 81,582 |
| Cache-write tokens | 0 | 0 | 0 |
| Output tokens | 4,563 | 3,133 | 4,148 |
| Reasoning tokens | 3,120, already included within output-token accounting | 2,314, already included within output-token accounting | 2,641, already included within output-token accounting |
| Completed requests | 23 | 13 | 26 |
| HTTP attempts | 23 | 13 | 26 |
| Cache pacing wait | 17.919s total | 4.619s total | 14.709s total |
| Cache-breakpoint fallbacks | 0 | 0 | 0 |
| Reasoning-passback fallbacks | 0 | 0 | 0 |

The expected fuzzy match was `Zo gaat het leven aan je voor`; the model instead returned the same `Systemisch bled` answer all three times. Query 3 is complete at 0/3.

### Completed `music_brainz_20k` dataset aggregate

| Dataset result | Input tokens | Cached tokens | Cache-read ratio | Noncached input | Tool calls | Wall time |
|---:|---:|---:|---:|---:|---:|---:|
| **6 / 9 (66.7%)** | 1,553,315 | 937,472 | 60.35% | 615,843 | 189 | 1,038.595s |

Music-brainz shows a split result: two formerly difficult revenue queries were perfect, while the persistent title-ranking failure remained 0/3. The dataset cut is complete and can be compared with later arms; it is not the four-dataset baseline score.

### Earlier live snapshot: `stockindex:1` (superseded by the completed result)

| Field | `stockindex:1` trial 0 | `stockindex:1` trial 2 |
|---|---:|---:|
| Result | **PASS** | **PASS** |
| Validator reason | `Target '399001.SZ' present as primary answer.` | `Target '399001.SZ' present as primary answer.` |
| Primary answer | `399001.SZ` | `399001.SZ` |
| Tool calls | 33 | 35 |
| Wall time | 287.419s | 278.083s |
| Input tokens | 450,952 | 489,967 |
| Cached tokens | 269,568 | 310,272 |
| Cache-read ratio | 59.78% | 63.33% |
| Noncached input | 181,384 | 179,695 |
| Cache-write tokens | 0 | 0 |
| Output tokens | 10,905 | 10,625 |
| Reasoning tokens | 6,166, already included within output-token accounting | 6,692, already included within output-token accounting |
| Completed requests | 39 | 36 |
| HTTP attempts | 39 | 36 |
| Cache pacing wait | 28.323s total | 20.904s total |
| Cache-breakpoint fallbacks | 0 | 0 |
| Reasoning-passback fallbacks | 0 | 0 |

This snapshot captured a retryable `stockindex:1` trial-1 transport failure,
which remains preserved and excluded. The later semantic retry passed, closing
the query at 3/3.

### Completed `stockindex:2` observations

| Field | `stockindex:2` trial 0 | `stockindex:2` trial 1 | `stockindex:2` trial 2 |
|---|---:|---:|---:|
| Result | **PASS** | **PASS** | **PASS** |
| Validator reason | `Target 'IXIC' present as primary answer.` | `Target 'IXIC' present as primary answer.` | `Target 'IXIC' present as primary answer.` |
| Primary answer | `IXIC` | `IXIC` | `IXIC` |
| Tool calls | 30 | 38 | 33 |
| Wall time | 261.904s | 265.089s | 286.344s |
| Input tokens | 432,305 | 520,956 | 441,243 |
| Cached tokens | 271,360 | 342,528 | 253,952 |
| Cache-read ratio | 62.77% | 65.75% | 57.55% |
| Noncached input | 160,945 | 178,428 | 187,291 |
| Cache-write tokens | 0 | 0 | 0 |
| Output tokens | 10,255 | 9,182 | 10,715 |
| Reasoning tokens | 6,831, already included within output-token accounting | 5,538, already included within output-token accounting | 6,748, already included within output-token accounting |
| Completed requests | 37 | 39 | 42 |
| HTTP attempts | 37 | 39 | 42 |
| Cache pacing wait | 22.374s total | 28.521s total | 29.639s total |
| Cache-breakpoint fallbacks | 0 | 0 | 0 |
| Reasoning-passback fallbacks | 0 | 0 | 0 |

### Completed `stockindex:2` query aggregate

| Query result | Input tokens | Cached tokens | Cache-read ratio | Noncached input | Tool calls | Wall time |
|---:|---:|---:|---:|---:|---:|---:|
| **3 / 3** | 1,394,504 | 867,840 | 62.23% | 526,664 | 101 | 813.337s |

This was an intermediate snapshot. The completed selected-attempt set is 9/9
for stockindex; the final aggregate is authoritative.

### Earlier live snapshot: `stockindex:3` (superseded by the completed result)

| Field | `stockindex:3` trial 0 |
|---|---:|
| Result | **PASS** |
| Validator reason | `All name-country pairs matched.` |
| Ranked name-country pairs | `IXIC`—United States; `NSEI`—India; `399001.SZ`—China; `GDAXI`—Germany; `TWII`—Taiwan |
| Tool calls | 48 |
| Wall time | 552.258s |
| Input tokens | 851,007 |
| Cached tokens | 600,064 |
| Cache-read ratio | 70.51% |
| Noncached input | 250,943 |
| Cache-write tokens | 0 |
| Output tokens | 16,175 |
| Reasoning tokens | 9,230, already included within output-token accounting |
| Completed requests | 49 |
| HTTP attempts | 49 |
| Cache pacing wait | 18.192s total |
| Cache-breakpoint fallbacks | 0 |
| Reasoning-passback fallbacks | 0 |

This snapshot captured a retryable `stockindex:3` trial-1 rate-limit attempt,
which remains preserved and excluded. Its later retry and trial 2 both passed,
closing the query at 3/3.

### Earlier baseline partial aggregate (superseded)

| Semantic progress | Observed passes | Input tokens | Cached tokens | Cache-read ratio | Noncached input | Tool calls | Wall time | Retryable infrastructure rows |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **21 / 45** | **15 / 21** | 13,521,875 | 9,349,632 | 69.15% | 4,172,243 | 768 | 5,631.457s | 14 |

This aggregate covers only the 21 completed semantic attempts. It is a progress and resource snapshot, not a completed-arm score, and it cannot support a stratified overall rate while stockindex and Yelp remain incomplete.

The baseline's first 11 recorded rows have `reason="infra:agent_error"`, an artifact payload naming HTTP 429, `requests=0`, `http_attempts=1`, and zero terminal token usage. They were produced before the runner's dedicated `infra:rate_limit` fail-fast path was added. Controlled resumes have since recorded two sanitized `infra:rate_limit` rows, each stopping the queue immediately while preserving `n_trials=3`. This prevents a quota outage from filling the file with more zero-token attempts.

The first controlled row reported `resets_at=1783767751`, or 2026-07-11 04:02:31 PDT (11:02:31 UTC). The baseline resumed after that reset and produced the 21 semantic results above. The latest controlled row is the `stockindex:3` trial-1 attempt and reported a reset at 2026-07-11 09:53:34 PDT; the circuit is paused on that quota signal. It is not a failed answer. Resume the same directory after the reported reset.

On resume, every `infra:*` key is treated as incomplete and is attempted again. Preserve the rows and reuse the same output directory; do not restart or hand-edit `trials.jsonl`. Retained infrastructure rows are excluded from scoring and are allowed in the trace bundle as long as each key ultimately has exactly one non-infrastructure semantic attempt.

## The five-arm Luna Max design isolates one cumulative increment at a time

All arms use:

- driver/provider/model: `labrat-agent` / `codex` / `gpt-5.6-luna`;
- reasoning: `max`;
- datasets: `deps_dev_v1,music_brainz_20k,stockindex,yelp`;
- 15 queries × three trials = 45 semantic trials per arm;
- unbounded agent turns and tool calls; and
- separate immutable output directories.

| Arm | Cartographer | Levers | Hints | Ledger | Marginal comparison | Purpose |
|---|---:|---:|---:|---:|---|---|
| B — bare baseline | off | off | off | off | — | Measure Luna Max with only the LabRat tool surface and base prompt. |
| C — +Cartographer | on | off | off | off | C − B | Does deterministic Scent add grounding beyond GPT-5.6's own exploration? |
| L — +levers | on | on | off | off | L − C | Do force-query, SQL repair, SQL-side aggregation, and tie rules add value? |
| H — +hints | on | on | on | off | H − L | Do DAB's declared benchmark hints add value beyond LabRat grounding? |
| G — +ledger | on | on | on | on | G − H | Does bounded model-visible tool history preserve accuracy while reducing context cost? |

This is a cumulative design, so it does not estimate feature interactions or the standalone effect of levers, hints, or ledger without prior layers. The marginal comparisons above are the only isolated claims the design supports.

### Query cohort and denominator

| Dataset | Included queries | Queries | Trials | Historical reason for inclusion |
|---|---|---:|---:|---|
| `deps_dev_v1` | `:1`, `:2` | 2 | 6 | Cross-database dependency traversal; historically volatile and grounding-sensitive. |
| `music_brainz_20k` | `:1`, `:2`, `:3` | 3 | 9 | Persistent answer-from-memory failure; force-query is directly relevant. |
| `stockindex` | `:1`, `:2`, `:3` | 3 | 9 | Dirty-date and answer-format cases; historically high variance across prompt changes. |
| `yelp` | `:1`–`:7` | 7 | 21 | MongoDB + DuckDB coverage and the largest query count in the subset. |
| **Total** | 15 exact queries | **15** | **45** | Multi-database grounding and context behavior. |

The headline score is DAB's **stratified Pass@1**: compute each query's `passes / 3`, average queries within each dataset, then average the four dataset rates equally. Yelp therefore contributes one quarter of the headline score, not 21/45 of it. Raw passes out of 45 remain a secondary diagnostic.

## Historical references set expectations, not GPT-5.6 results

The prior evidence comes from [`dab-progress-report.md`](dab-progress-report.md) and uses different models, drivers, scopes, or ground-truth snapshots. It is context for hypotheses only; none is a substitute for the five Luna Max arms.

| Reference | Observed result | How to use it here |
|---|---|---|
| Sonnet tools-only → +Cartographer, tuning subset | 21% → 29%, **+8pp** | Cartographer should earn its place on Luna only if C improves on B. The old subset covered deps/music/stockindex, not Yelp. |
| Sonnet +Cartographer → +prompt levers | 29% → 38%, about **+8–9pp marginal** and +17pp stacked | The source headline rounds the marginal lift to +8pp; displayed scores imply +9pp. Compare L − C without forcing either expectation. |
| Sonnet per-dataset Cartographer signal | deps 0%→33%; music 0%→11%; stockindex 56%→44% | Cartographer helped the two weak datasets; the stockindex decline was treated as noise. Inspect dataset cuts, not just the aggregate. |
| GPT-5.5 Cartographer ablation | **neutral, +0pp**, `n=2` | GPT-5.5 already explored heavily (about 32 `run_sql` calls in the observed trials). Luna may resemble GPT-5.5 or the leaner Sonnet; this is the central C − B question. |
| GPT-5.5 verifier off/on | 49.3% vs 49.1%, **−0.2pp** | The verifier is intentionally omitted from these five arms; prior evidence showed no accuracy benefit for extra tokens. |
| Final Sonnet pass@5 stack, 2026-06-24 | deps 20%, music 27%, stockindex 93%, Yelp 46%; overall 60.88% on all 12 datasets | Useful difficulty context only. The run combined Cartographer, levers, hints, reruns, and a newer ground-truth checkout; it is not a clean hints delta. |
| ContextLedger | no prior DAB accuracy ablation | G − H is genuinely new. Judge both accuracy and context/token efficiency. |

There is no clean historical standalone hints estimate in the durable history. The accepted 60.88% submission declared Hints: Yes, but its improvement over the prior 51.38% entry also included grounding changes and ground-truth corrections. Do not attribute that +9.5pp difference to hints.

## Results table — update only from completed semantic attempts

### Arm-level decision table

| Arm | Status | Semantic trials | Stratified Pass@1 | Δ vs prior arm | Raw passes / 45 | Input tokens | Cached tokens | Cache-read ratio | Noncached input | Output tokens | Mean latency | Semantic API equivalent | All-attempt API equivalent |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| B — bare baseline | **COMPLETE** | 45 / 45 | **74.4%** | — | **35 / 45** | 31,578,005 | 21,846,272 | 69.18% | 9,731,733 | 489,785 | 327.1s | $14.8551 | $14.8551 |
| C — +Cartographer | **COMPLETE** | 45 / 45 | **65.7%** | **-8.7pp** | **33 / 45** | 33,922,824 | 23,865,344 | 70.35% | 10,057,480 | 492,760 | 317.2s | $15.4006 | $15.4006 |
| L — +levers | **COMPLETE** | 45 / 45 | **65.6746%** | **0.0pp** | **33 / 45** | 34,949,221 | 24,438,528 | 69.9258% | 10,510,693 | 518,943 | 377.8s | $16.0682 | $16.7028 |
| H — +hints | **COMPLETE** | 45 / 45 | **83.5317%** | **+17.8571pp** | **40 / 45** | 38,707,688 | 27,248,896 | 70.3966% | 11,458,792 | 533,380 | 364.76s | $17.3840 | $22.2528 |
| G — +ledger | **COMPLETE — PROMOTED** | 45 / 45 | **83.9286%** | **+0.3968pp** | **39 / 45** | 37,680,115 | 26,971,904 | 71.5813% | 10,708,211 | 559,647 | 406.227s | $16.7633 | $18.9869 |

### Dataset score table

| Arm | `deps_dev_v1` | `music_brainz_20k` | `stockindex` | `yelp` | Stratified overall |
|---|---:|---:|---:|---:|---:|
| B — bare baseline | **50.0%** | **66.7%** | **100.0%** | **81.0%** | **74.4%** |
| C — +Cartographer | **50.0%** | **44.4%** | **77.8%** | **90.5%** | **65.7%** |
| L — +levers | **50.0%** | **44.4%** | **77.8%** | **90.5%** | **65.6746%** |
| H — +hints | **50.0%** | **100.0%** | **88.8889%** | **95.2381%** | **83.5317%** |
| G — +ledger | **50.0%** | **100.0%** | **100.0%** | **85.7143%** | **83.9286%** |

All five arms now share the same 45-attempt denominator. The exact tables remain
the audit surface; any later summary chart must reproduce these completed values.

## Exact runbook for the five grounding arms

Before any scoring or resume operation:

```bash
git -C ~/repos/DataAgentBench fetch origin --prune
git -C ~/repos/DataAgentBench rev-list --left-right --count HEAD...origin/main
uv run python scripts/dab_setup.py --dab-dir ~/repos/DataAgentBench
```

Run `~/repos/DataAgentBench/download.sh` first if required large benchmark files
are missing, and ensure MongoDB is running for Yelp. Record the DAB checkout SHA
when the first semantic trial begins. Fetch and recheck the upstream-behind
count before every scoring session; preserve local benchmark data changes.

For new arms, `scripts/dab_shards.py` may split the fixed arm config into one
isolated directory per dataset. Run at most two dataset owners concurrently,
stagger starts by ten seconds, and never run the same task in two workers. Stop
all workers on the first exit 4 or `infra:rate_limit`. Merge only after every
shard is complete; the merger rejects config drift, duplicate semantic rows,
trace collisions, incomplete coverage, or a failed taint audit.

### B — bare baseline (resume this existing directory)

Omitting `--agent-cartograph` is intentional; the other three features are explicitly disabled.

```bash
uv run python scripts/eval_dab.py \
  --dab-dir ~/repos/DataAgentBench \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-luna --agent-reasoning max \
  --no-hints --no-agent-levers --no-agent-ledger \
  --datasets deps_dev_v1,music_brainz_20k,stockindex,yelp \
  --n-trials 3 \
  --output-dir runs/dab/ablation-gpt56-luna-max-baseline
```

### C — add Cartographer only

```bash
uv run python scripts/eval_dab.py \
  --dab-dir ~/repos/DataAgentBench \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-luna --agent-reasoning max \
  --agent-cartograph --no-hints --no-agent-levers --no-agent-ledger \
  --datasets deps_dev_v1,music_brainz_20k,stockindex,yelp \
  --n-trials 3 \
  --output-dir runs/dab/ablation-gpt56-luna-max-cartograph
```

### L — add benchmark-safe prompt levers

```bash
uv run python scripts/eval_dab.py \
  --dab-dir ~/repos/DataAgentBench \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-luna --agent-reasoning max \
  --agent-cartograph --no-hints --agent-levers --no-agent-ledger \
  --datasets deps_dev_v1,music_brainz_20k,stockindex,yelp \
  --n-trials 3 \
  --output-dir runs/dab/ablation-gpt56-luna-max-levers
```

### H — add DAB benchmark hints

```bash
uv run python scripts/eval_dab.py \
  --dab-dir ~/repos/DataAgentBench \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-luna --agent-reasoning max \
  --agent-cartograph --hints --agent-levers --no-agent-ledger \
  --datasets deps_dev_v1,music_brainz_20k,stockindex,yelp \
  --n-trials 3 \
  --output-dir runs/dab/ablation-gpt56-luna-max-hints
```

### G — add ContextLedger

```bash
uv run python scripts/eval_dab.py \
  --dab-dir ~/repos/DataAgentBench \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-luna --agent-reasoning max \
  --agent-cartograph --hints --agent-levers --agent-ledger \
  --datasets deps_dev_v1,music_brainz_20k,stockindex,yelp \
  --n-trials 3 \
  --output-dir runs/dab/ablation-gpt56-luna-max-ledger
```

The runner's resume guard rejects a feature/model mismatch against an existing `config.json`. If a command conflicts, fix the command or choose a genuinely new arm directory; never mutate an existing arm into another configuration.

## Winner rule before the full 270-trial submission

Select the arm with the highest completed stratified Pass@1. Because `n=3` is noisy, report all per-dataset directions and raw passes alongside the headline. If two arms tie on stratified score, prefer the one with lower mean public-API price equivalent per semantic trial, then lower noncached input, then lower latency. Do not retain a layer solely because it helped Sonnet historically.

For the ledger specifically, a score tie with H plus a meaningful reduction in noncached input or API-price equivalent is a positive result; an accuracy loss is a guardrail failure unless the magnitude is clearly attributable to one retryable infrastructure artifact, which must be rerun rather than scored.

G did not tie or lose the preregistered headline score: stratified Pass@1 rose
0.3968pp while noncached input fell 6.5503%. The one-pass raw decline is retained
as a limitation rather than silently overriding the stratified decision rule.

Once selected, write the winning arm and its exact flags here before launching the full run or tier study:

- Winning arm: **G — +ledger** (`runs/dab/ablation-gpt56-luna-max-ledger`)
- Frozen grounding flags: `--agent-cartograph --hints --agent-levers --agent-ledger`
- Decision date: **2026-07-13**
- Evidence: **83.9286% stratified and 10,708,211 noncached input**, versus
  Hints at 83.5317% and 11,458,792. This is +0.3968pp stratified and -750,581
  noncached input (-6.5503%), despite one fewer raw pass, +106 requests, and
  +1,866.19 seconds latency.

## Hard-tail tier study: six fixed queries, four model/effort arms

Run the tier comparison only after the winning grounding flags above are frozen. Every tier must use the same DAB checkout, exact task list, `n=3`, and grounding configuration. That yields six queries × three trials = 18 semantic trials per tier, 72 total.

The task list is fixed:

```text
crmarenapro:12,deps_dev_v1:1,music_brainz_20k:1,music_brainz_20k:3,pancancer_atlas:1,patents:2
```

These are historical hard-tail failures spanning exact-ID/precision, dependency traversal, answer-from-memory, cross-database joins, and patent taxonomy. Sync the benchmark checkout before this study: patents ground truth has changed historically.

**`agnews` is deliberately excluded.** Pretraining-exposed models can recall the public AG News ID→label mapping from parametric memory even inside a clean tool sandbox. This affected both Sonnet and GPT-5.5-era submissions and makes `agnews` an unreliable clean model-tier discriminator. Do not add it to the hard-tail study.

| Tier arm | Model | Requested effort | Wire/delegation behavior | Run directory | Status |
|---|---|---|---|---|---|
| Luna Max | `gpt-5.6-luna` | `max` | wire `max`; no Ultra delegation policy | `runs/dab/tier-gpt-5.6-luna-max` | **COMPLETE — ONLY RELIABLE TIER RESULT (18/18)** |
| Terra High | `gpt-5.6-terra` | `high` | wire `high` | `runs/dab/tier-gpt-5.6-terra-high-shards` | **INCOMPLETE — RETRY CEILINGS (14/18)** |
| Sol High | `gpt-5.6-sol` | `high` | wire `high` | `runs/dab/tier-gpt-5.6-sol-high-shards` | **INCOMPLETE — RETRY CEILINGS (9/18)** |
| Sol Ultra | `gpt-5.6-sol` | `ultra` | wire `max` + proactive multi-agent delegation | `runs/dab/tier-gpt-5.6-sol-ultra-shards` | **STOPPED — `infra:rate_limit` / EXIT 4 (6/18); RESET `2026-07-20T18:40:46Z`** |

### Tier command templates use the frozen Ledger grounding winner

All four commands use the exact promoted flags above. Keep this grounding
configuration identical across tiers so the model/effort comparison remains
matched.

```bash
uv run python scripts/eval_dab.py \
  --dab-dir ~/repos/DataAgentBench \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-luna --agent-reasoning max \
  --agent-cartograph --hints --agent-levers --agent-ledger \
  --tasks crmarenapro:12,deps_dev_v1:1,music_brainz_20k:1,music_brainz_20k:3,pancancer_atlas:1,patents:2 \
  --n-trials 3 --output-dir runs/dab/tier-gpt-5.6-luna-max

uv run python scripts/eval_dab.py \
  --dab-dir ~/repos/DataAgentBench \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-terra --agent-reasoning high \
  --agent-cartograph --hints --agent-levers --agent-ledger \
  --tasks crmarenapro:12,deps_dev_v1:1,music_brainz_20k:1,music_brainz_20k:3,pancancer_atlas:1,patents:2 \
  --n-trials 3 --output-dir runs/dab/tier-gpt-5.6-terra-high

uv run python scripts/eval_dab.py \
  --dab-dir ~/repos/DataAgentBench \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-sol --agent-reasoning high \
  --agent-cartograph --hints --agent-levers --agent-ledger \
  --tasks crmarenapro:12,deps_dev_v1:1,music_brainz_20k:1,music_brainz_20k:3,pancancer_atlas:1,patents:2 \
  --n-trials 3 --output-dir runs/dab/tier-gpt-5.6-sol-high

uv run python scripts/eval_dab.py \
  --dab-dir ~/repos/DataAgentBench \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-sol --agent-reasoning ultra \
  --agent-cartograph --hints --agent-levers --agent-ledger \
  --tasks crmarenapro:12,deps_dev_v1:1,music_brainz_20k:1,music_brainz_20k:3,pancancer_atlas:1,patents:2 \
  --n-trials 3 --output-dir runs/dab/tier-gpt-5.6-sol-ultra
```

### Tier result table

The headline is DAB-style stratified Pass@1 over the five included datasets;
`music_brainz_20k`'s two queries share one-fifth of that score. Only Luna has the
full 18-key denominator required for that metric. For incomplete arms,
`passes / semantic` describes only surviving semantic rows, while planned-key
rate is the conservative `passes / 18`; neither repairs the missing matched
trials.

| Tier | Reliability status | Semantic coverage | Passes / semantic | Planned-key pass rate | Stratified hard-tail Pass@1 | New task with a pass that Luna missed 0/3 |
|---|---|---:|---:|---:|---:|---|
| Luna Max | **Complete / reliable** | **18 / 18 (100%)** | **9 / 18 (50.0%)** | **9 / 18 (50.0%)** | **40.0%** | — |
| Terra High | **Incomplete — retry ceilings** | **14 / 18 (77.7778%)** | **7 / 14 (50.0%)** | **7 / 18 (38.8889%)** | Not comparable | `crmarenapro:12` (2/3) |
| Sol High | **Incomplete — retry ceilings** | **9 / 18 (50.0%)** | **5 / 9 (55.5556%)** | **5 / 18 (27.7778%)** | Not comparable | `crmarenapro:12` (2/2 observed) |
| Sol Ultra | **Incomplete — rate-limit stop** | **6 / 18 (33.3333%)** | **3 / 6 (50.0%)** | **3 / 18 (16.6667%)** | Not comparable | `crmarenapro:12` (1/1 observed) |

The incomplete arms' higher or equal survivor rates do not establish a tier
gain. Luna Max is the only complete and reliable tier result; no incremental
cost-per-pass or model-tier promotion is supportable from the partial rows.

### Tier efficiency and infrastructure accounting

| Tier | Semantic cache ratio | Semantic noncached input | All-attempt cache ratio | All-attempt noncached input | Semantic API equivalent | All-attempt API equivalent | Infra rows |
|---|---:|---:|---:|---:|---:|---:|---:|
| Luna Max | **78.1201%** | 5,165,580 | **78.1201%** | 5,165,580 | $8.6977 | $8.6977 | 0 |
| Terra High | 53.5671% | 2,756,511 | 54.2186% | 4,111,853 | $8.9323 | $13.4020 | 18 `agent_error` |
| Sol High | 29.9635% | 3,089,406 | 30.2919% | 5,331,452 | $17.6502 | $30.5596 | 31 `agent_error` |
| Sol Ultra | 28.4093% | 3,664,243 | 29.5828% | 11,854,053 | $21.0406 | $69.4165 | 27 `agent_error`; 11 `timeout`; 1 `rate_limit` |

All-attempt cost includes the persisted request usage on retryable
infrastructure rows. It is the relevant reliability cost, but still understates
wall-clock overhead because terminal infra rows report zero latency and tool
calls. Sol Ultra spent more than three times its semantic equivalent after
including failed attempts, then stopped under the preregistered global
rate-limit circuit. Its one `infra:rate_limit` row reports reset epoch
`1784572846`, exactly `2026-07-20T18:40:46Z`; the worker exited 4 and no sibling
work may resume before that reset.

The task table reports `passes / semantic rows / infra rows`. A zero semantic
denominator is shown explicitly rather than converted to a failure rate.

| Hard query | Luna Max | Terra High | Sol High | Sol Ultra |
|---|---:|---:|---:|---:|
| `crmarenapro:12` | 0 / 3 / 0 | 2 / 3 / 0 | 2 / 2 / 3 | 1 / 1 / 8 |
| `deps_dev_v1:1` | 0 / 3 / 0 | 0 / 3 / 1 | 0 / 1 / 6 | 0 / 1 / 6 |
| `music_brainz_20k:1` | 3 / 3 / 0 | 2 / 2 / 3 | 2 / 2 / 3 | 2 / 2 / 5 |
| `music_brainz_20k:3` | 3 / 3 / 0 | 2 / 2 / 6 | 1 / 1 / 7 | 0 / 0 / 8 |
| `pancancer_atlas:1` | 0 / 3 / 0 | 0 / 3 / 1 | 0 / 3 / 3 | 0 / 2 / 6 |
| `patents:2` | 3 / 3 / 0 | 1 / 1 / 7 | 0 / 0 / 9 | 0 / 0 / 6 |

The tier metrics and arithmetic above come from the generated
[`campaign-summary.json`](../runs/dab/audits/campaign-summary.json), with the
saved tier configs and reports as the run-level sources.

## Token and public-API price-equivalent methodology

The Codex provider is subscription-backed; LabRat does not observe how ChatGPT debits subscription quota. Dollar figures in this report are therefore **public-API price equivalents**, not invoices, subscription charges, or proof that caching avoids 429s. The current public prices, checked 2026-07-11, are:

| Model | Uncached input / 1M | Cached input / 1M | Output / 1M | Cache write / 1M |
|---|---:|---:|---:|---:|
| [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) | $1.00 | $0.10 | $6.00 | $1.25 |
| [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra) | $2.50 | $0.25 | $15.00 | $3.125 |
| [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) | $5.00 | $0.50 | $30.00 | $6.25 |

The [prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching) prices GPT-5.6 cache writes at 1.25× uncached input and confirms that caching does not change rate limits.

Calculate equivalent cost **per completed request**, then sum requests; do not price only the arm-level aggregate because a long-context multiplier can apply request by request.

For request `j`:

```text
I_j = input_tokens
R_j = cached_tokens
W_j = cache_write_tokens
O_j = output_tokens
U_j = max(0, I_j - R_j - W_j)

cost_j = input_multiplier_j × (U_j × input_price
                              + R_j × cached_price
                              + W_j × 1.25 × input_price)
       + output_multiplier_j × O_j × output_price
```

Divide prices by 1,000,000. For a request above 272K input tokens, use `input_multiplier=2` and `output_multiplier=1.5` for the full request; otherwise both are 1. `output_tokens` already includes reasoning tokens in its detail accounting, so do not add `reasoning_tokens` again.

Only treat `cache_write_tokens=0` as measured zero when `cache_write_tokens_reported=true`. If the presence bit is false or absent, report the computed equivalent as a lower bound and call out unmeasured writes. Compute arm cache-read ratio as `sum(cached_tokens) / sum(input_tokens)`, never as an unweighted mean of request ratios.

Primary efficiency fields per arm:

- total and mean input, cached, cache-write, noncached, output, and reasoning tokens;
- cache-read ratio and write-presence coverage;
- completed `requests` versus `http_attempts`;
- pacing wait, end-to-end latency, and tool calls;
- public-API price equivalent per semantic trial; and
- separate infrastructure overhead, including any partial completed-call usage on retryable attempts.

An HTTP 429 without a terminal usage event has no token count. Do not infer zero provider work from missing terminal telemetry; show the attempt count and quota status separately.

## Completion and update checklist

For each grounding or tier arm:

1. Confirm `config.json` matches the registered model, effort, flags, task filter, `n_trials`, and `trace_attempt_policy="reset_on_attempt"`.
2. Confirm the DAB checkout SHA matches every comparison arm and record it below.
3. Resume until every expected key has exactly one non-infrastructure semantic result: 45 for a grounding arm, 18 for a tier arm.
4. Preserve retryable `infra:*` rows, but exclude them from score denominators. Report their attempts and any measurable token overhead separately.
5. Confirm every selected semantic attempt has `agent_tool_calls.jsonl`; empty is valid only for a genuine zero-tool semantic attempt. Run the taint audit and reject missing or malformed traces.
6. Build the non-strict subset bundle with `uv run python scripts/build_dab_trace_bundle.py --run-dir <arm-dir>` after `submission.json` and `report.md` exist.
7. Populate exact result tables from the saved artifacts. Keep `PENDING` for any incomplete metric; never coerce missing telemetry to zero.
8. Add the completion timestamp, DAB SHA, semantic/infra counts, and one-paragraph interpretation beside each updated table.
9. Freeze the winning grounding flags before starting any tier arm; use the same flags and checkout for all four tiers.

Run metadata:

- DAB checkout SHA at the paused launch: `ca45478a102792c8acbe5d19c8bcb2fb58827557` (includes the LabRat submission branch; synced `origin/main` at `5dd866b7f403007a15a79060233a5d98562d1ca9`)
- Grounding arms completed: **5 / 5**
- Tier arms completed and reliable: **1 / 4 — Luna Max only**
- Higher-tier terminal state: **Terra High and Sol High incomplete at retry ceilings; Sol Ultra stopped on `infra:rate_limit` / exit 4**
- Full Luna Max campaign: **COMPLETE — 270/270 semantic keys; 206 passes; ten bounded AG News turn-budget failures**
- Mandatory trace-integrity audit: **COMPLETE — 254 clean / 16 suspicious-but-not-proven / 0 contaminated across 270 selected traces**
- Mandatory PR #65 exact-key comparison: **COMPLETE — 206/270 versus 176/270 (+30 passes; +11.1111pp)**
- Full immutable attempt-trace bundle: **NOT CLAIMED — 25 current-run infra-attempt traces and 110 traces in the broader ablation/tier history were overwritten by `reset_on_attempt`**

## Mandatory post-run integrity and official-submission gates

These are required deliverables before any upstream handoff. Both are complete
for the final 270-row selected package.

### Trace-integrity and cheating audit

**Verdict: no confirmed cheating or external-result access; share with explicit
trace-history caveats.** The final selected package contains 270 semantic rows,
270 nonempty traces, 10,591 parsed tool events, and 13,174 completed request
records. It finds **254 clean / 16 suspicious-but-not-proven / 0 contaminated**;
all 206 passing rows have distinctive persisted local-data evidence, including
ten cases confirmed manually.

It found no browser, web, HTTP, shell, arbitrary filesystem-read, validator,
ground-truth, answer-file, or benchmark-repository access. All 68
`search_trails` calls returned empty results. The 16 questionable rows are CRM
trials that use “expected answer” or benchmark-targeting wording; ten passed and
six failed. Every one remains enumerated with exact trace evidence in the final
audit, and none is silently excluded.

The caveats are concrete. Raw model message bodies are not persisted. Twenty-five
earlier current-run infrastructure traces were overwritten by
`reset_on_attempt`, although 2,154 of their request records remain. A cancelled
timeout can omit the last in-flight tool event, which is why the old AG News
attempts were neither relabeled nor selected. The ten bounded recovery traces
are all automated-taint clean and were also manually scanned. These limitations
prevent an immutable-every-attempt claim, but none supplies evidence of cheating
in the selected data.

Full evidence: [final 270-row trace-integrity report](../runs/dab/audits/full-luna-trace-integrity-final-270.md),
[machine-readable 270-row audit](../runs/dab/audits/full-luna-trace-integrity-final-270.json),
and the [base deep audit](../runs/dab/audits/full-luna-trace-integrity-final.md).

### Comparison with the last official traced submission

The final exact comparison covers **54 task IDs and identical trial numbers
0-4: 270 matched keys on each side**. Current Luna Max passes **206/270
(76.2963%)**, versus **176/270 (65.1852%)** for PR #65: **+30 passes and
+11.1111pp**. The full dataset-stratified scores are **74.1758% versus 60.8822%
(+13.2937pp)**. Task-level five-trial pass counts show **23 gains, eight
regressions, and 23 ties**.

Current newly clears seven tasks: `github_repos:2`, `googlelocal:3`,
`music_brainz_20k:1`, `music_brainz_20k:3`, `patents:2`, `yelp:2`, and
`yelp:4`. It loses four: `agnews:2`, `agnews:4`, `github_repos:1`, and
`stockmarket:4`.
The largest dataset gains are MusicBrainz (+10 passes), Yelp (+15), GoogleLocal
(+6), and dependencies (+3); StockMarket regresses by five passes. The full
per-task vectors and all 54 exact comparisons are preserved in the linked CSV
and report rather than duplicated here.

Both sides were regraded through the official validators at DAB SHA
`ca45478a102792c8acbe5d19c8bcb2fb58827557`. There were zero validator errors
and zero current stored-flag mismatches. The 54 validators plus 54
`ground_truth.csv` files are **108/108 byte-identical** to PR #65 base. PR #65's
full submission regrades to 176/270 and stratified 0.6088217338.

This is not a model-only causal comparison. PR #65 used Claude Sonnet 4.6 with
Cartographer and Hints; current uses GPT-5.6 Luna Max with Cartographer, Hints,
Levers, and ContextLedger on a different host/runtime. The ten new AG News rows
used a disclosed task-specific 10-turn/200-row cap; maintainers must decide
whether that evaluator policy is acceptable. Three selected input BSON files
are dirty locally—one AG News file and two Yelp files—even though the
validator/ground-truth manifest is clean; this is a model-interaction
reproducibility caveat.

Full evidence: [`ucbepic/DataAgentBench#65`](https://github.com/ucbepic/DataAgentBench/pull/65),
[final comparison report](../runs/dab/audits/full-luna-pr65-comparison-final.md),
[machine-readable comparison](../runs/dab/audits/full-luna-pr65-comparison-final.json),
[per-task CSV](../runs/dab/audits/full-luna-pr65-comparison-final.csv), and the
[reproducible generator](../runs/dab/audits/pr65-source/build_full_luna_pr65_comparison.py).

## Answers supported by the completed evidence

1. Luna resembles the GPT-5.5 result more than the older Sonnet result on
   Cartographer: Cartographer alone regressed from 74.4% to 65.7% stratified.
2. Prompt levers were not net-positive in this Luna cohort: they left accuracy
   unchanged from Cartographer while increasing noncached input, requests, and
   latency.
3. Hints produced the largest raw-accuracy gain, reaching 40/45 and 83.5317%
   stratified after the 33/45 Levers arm.
4. Ledger reduced semantic noncached input and public-API equivalent while
   slightly improving stratified score, but lost one raw pass and increased
   requests and latency. The integrity audit found no confirmed evidence that
   Ledger hid or replaced local data evidence on passing rows.
5. No larger tier has reliable matched evidence. Luna Max is the only complete
   tier arm. Terra High and Sol High stopped at retry ceilings; Sol Ultra hit
   the global rate-limit circuit. The observed CRM clears are survivor-only and
   do not justify an incremental cost-per-pass or tier promotion.
6. The promoted full Luna-Max campaign materially outperforms PR #65 on all
   270 exact keys: 206 passes versus 176, with a +13.2937pp full stratified
   gain. Seven tasks are newly cleared and four are lost.
7. Prompt caching is successful but not sufficient for subscription safety. The
   final selected package reads 71.1504% of input from cache. The historical
   AG News attempts show that novel row-level classification remains the main
   wall-clock and quota risk; the bounded recovery avoided it by terminalizing
   at ten main turns.
8. The package is trace-complete and ready for maintainer review, but this
   report does not claim unconditional leaderboard eligibility. The upstream
   handoff must prominently disclose the task-specific AG News cap and obtain
   DAB maintainer acceptance before presenting it as an official submission.
