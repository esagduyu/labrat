# Terra vs Luna — full 270-trial answer-by-answer synthesis (2026-07-20)

Combines the two per-dataset diff reports (`terra-luna-diff-groupA`, `terra-luna-diff-groupB`).
**Terra** = gpt-5.6-terra@high + full stack + **taxonomy lever ON** (69.78% stratified).
**Luna** = gpt-5.6-luna-max + full stack, **no taxonomy** — our submitted #5 entry (74.18%).
The comparison is **confounded** (model AND lever changed together); both agents attributed
to the taxonomy only where trace/payload evidence was direct, and separated noise + shared gaps.

## The −4.4pp is mostly NOT a taxonomy regression

Decomposing the aggregate gap:
- **Group A (crmarenapro, deps_dev_v1, github_repos, stockindex, patents, music_brainz): a WASH** — Terra 108/140 vs Luna 107/140. Big swings cancel (deps_dev_v1 +3 for Terra vs crmarenapro:8 −5).
- **Group B (yelp, stockmarket, googlelocal, agnews, pancancer, bookreview): Luna wins** — 99/120 vs 84/120.
- A large share of the losses are **shared failures** (both runs fail identically → not the taxonomy, not the model): pancancer:1, stockmarket:4, yelp:2, github_repos:1, patents:2, crmarenapro:2/:7, music_brainz:1/:3.
- Several more are **n=5 noise** (single-trial flips, no shared mechanism): bookreview:1, stockindex:1/2/3, crmarenapro:3, googlelocal:3.
- The genuine taxonomy-attributable regression is **narrow**: (a) tie-band over-enumeration (crmarenapro:8), (b) partial-enumeration truncation (a Terra-only pattern, but model/effort equally plausible). The taxonomy also produced one clean **win**: deps_dev_v1:1 (proximity validator).

**Read:** the taxonomy as a bundle is net-negative, but it packages a couple of genuinely good micro-rules with a couple of over-aggressive ones. The biggest opportunities are in the **shared failures — existing-tool gaps that neither config fixes.**

## Failure modes, unified across both halves

| Mode | Queries (both groups) | Shared or Δ? | Tag |
|---|---|---|---|
| **Column disambiguation** (name vs code, hierarchy level, denormalized vs fact) | patents:3, github_repos:2, pancancer:1, yelp:2 | mostly SHARED | autopsy lever D |
| **Enumeration completeness** (too few OR too many) | crmarenapro:8 (too many), yelp:6, googlelocal:3, stockmarket:2, bookreview:2 (too few) | mixed | — |
| **Classification timeout** (never reaches/finishes) | agnews:3/4 | SHARED (0/10 both) | infra |
| **Delivery-format / proximity** | deps_dev_v1:1 (taxonomy WIN), stockmarket:4 fuzzy-name | mixed | levers B |
| **Free-text grounding** (prose-field regex too narrow) | yelp:2, googlelocal fringe | SHARED | autopsy lever A |
| Noise / shared-GT-scope | stockindex, github_repos:1 (GT-scope mismatch, not agent-fixable) | — | — |

## PRIORITIZED existing-tool fixes (ranked by trials rescued × durability)

1. **Column-disambiguation grounding in `link_schema` / `describe_table`** — surface, at grounding
   time, when a table has a code-vs-name pair or a hierarchy/level column, and prompt the agent to
   confirm which the question wants. Rescues patents:3, github_repos:2, pancancer:1 (and helps yelp:2).
   **Highest value: it's mostly SHARED failures (helps every config, both models), it's autopsy
   lever D, and it's moat-aligned (real product improvement, not benchmark-fitting).**
2. **Wire the built-but-unused local-NLI classify backend + a classification-query turn/timeout
   budget.** `local_classify.py` (`local-embed`) but `llm_classify_backend` defaults to `"llm"` and is
   never overridden. agnews:3/4 (0/10 on BOTH submissions) die on turn-budget/timeout before finishing
   classification. Enabling it removes the timeout mechanism; partial rescue (weaker than a true NLI
   model on nuanced boundaries).
3. **Enumeration-completeness lever in `_dab_lever_lines()` (bidirectional).** (a) a "count returned
   rows vs distinct qualifying groups; emit ALL, don't truncate to first category" reminder
   (yelp:6, googlelocal:3, stockmarket:2, bookreview:2); (b) a **tie-band sanity guard** — a tie band
   covering >50% of the candidate set, or a singular question ("the agent"), means re-derive, not
   dump the whole roster (crmarenapro:8).
4. **Promote the one proven delivery micro-rule into the always-on base levers (untuned).**
   deps_dev_v1:1: Luna's markdown-table answer pushed the version >10 chars past the package name and
   failed the proximity validator 0/5; Terra's bare `name — value` passed 3/5. Extract just "keep each
   item and its value adjacent as plain tokens, not in a table" into `_dab_lever_lines()`.
5. **(Not agent-fixable) github_repos:1** — both 0/5 with a ~40× inflated README denominator; looks
   like a DAB sample-table / GT-scope mismatch. Flag upstream, don't chase.

## Harness bug found
Terra's per-shard `trials.jsonl` **appends** a resumed trial instead of overwriting the prior infra
row for the same `(task_id, trial_num)` (music_brainz_20k:3 has 6 rows for 5 trials). Any re-scoring
must dedupe by `(task_id, trial_num)`. Fix in the resume path so infra rows are replaced.

## Strategic conclusion (re: tuned vs untuned prompt)
The evidence points AWAY from needing a DAB-tuned prompt to match the top-3. The real gains are:
(1) fixing existing **tools** (column-disambiguation grounding — helps real customers, moat-aligned),
and (2) extracting a few **proven micro-levers** (adjacent-tokens, completeness, tie-sanity) into the
always-on, benchmark-safe `_dab_lever_lines` — which keeps us **untuned**. The full taxonomy addendum
should NOT ship as-is; cherry-pick its winning rules and drop the over-aggressive ones.

---
*Note: regenerated 2026-07-23 after the original untracked file was accidentally swept by a
`git stash -u` + drop during a merge cleanup. Content is faithful to the original synthesis; the
two per-dataset diff reports were recovered in parallel from their subagent transcripts.*
