# GPT-5.6 DAB Campaign Runbook

**Status:** active. This is the concise execution plan for the original GPT-5.6
DataAgentBench goal. It supersedes the abandoned native-Codex runtime, isolation,
and experiment-controller plans.

## Objective and fixed sequence

1. Keep the shipped Responses-adapter cache, model-tier, retry, and trace work green.
2. Finish the matched Luna-Max grounding-feature ablation on the representative subset.
3. Run the matched hard-task tier study: Terra High, Sol High, and Sol Ultra versus Luna Max.
4. Freeze the cheapest configuration that is performant enough.
5. Resume and complete the official 54-query x five-trial (270-trial) run.
6. Build and verify the strict trace-complete submission bundle.

The official run remains on `--driver labrat-agent --agent-provider codex`. The
2026-07-12 native-host diagnostic is a completed negative result: despite an
83.8% cache-read rate, it used 2.14x the Responses adapter's absolute noncached
input on the identical valid workload. Do not reintroduce a native DAB driver.

## Scoring-run preflight

Before each scoring session:

```bash
git -C /Users/ege/repos/DataAgentBench fetch origin --prune
git -C /Users/ege/repos/DataAgentBench rev-list --left-right --count HEAD...origin/main
uv run python scripts/dab_setup.py --dab-dir /Users/ege/repos/DataAgentBench
```

- Preserve local DataAgentBench data-file changes; never use a destructive checkout.
- Require upstream-behind count `0` before scoring.
- Preserve every `infra:*` row. Resume the same output directory; do not restart it.
- Exit `4` on a rate limit before launching another task. Exit `5` on an audit failure.
- Never change model, effort, feature flags, trial count, or task filter when resuming a run.

## Stage D: grounding-feature ablation

The matched subset is:

```text
deps_dev_v1,music_brainz_20k,stockindex,yelp
```

Use Luna Max, three trials per query, identical task keys, and separate output
directories. Complete each arm before promoting a feature:

1. Bare baseline (already resumable at
   `runs/dab/ablation-gpt56-luna-max-baseline`).
2. Cartographer only.
3. Cartographer plus benchmark-safe levers.
4. Cartographer plus levers plus DAB hints.
5. One matched ContextLedger on/off pair using the best preceding configuration.
6. Run verifier only if the preceding evidence leaves a real accuracy question.

Example new-arm shape:

```bash
uv run python scripts/eval_dab.py \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-luna --agent-reasoning max \
  --datasets deps_dev_v1,music_brainz_20k,stockindex,yelp \
  --n-trials 3 \
  --output-dir runs/dab/ARM-NAME
# Add only that arm's explicit feature flags.
```

Compare matched semantic trials, per-dataset Pass@1, aggregate and per-request
noncached input, tool calls, and latency. Cache percentage is supporting context,
not the promotion metric. Record the table and selected flags in
`docs/dab-solultra-ablation.md`.

## Stage F: hard-task tier study

Select the lowest-performing matched task IDs from the completed Luna-Max subset
and historical failure analysis. Freeze one task list and the winning grounding
flags, then run these four matched arms with three trials per task:

| Arm | Model | Effort |
|---|---|---|
| control | `gpt-5.6-luna` | `max` |
| tier | `gpt-5.6-terra` | `high` |
| tier | `gpt-5.6-sol` | `high` |
| tier | `gpt-5.6-sol` | `ultra` |

Use `runs/dab/tier-<model>-<effort>` directories. Compare matched Pass@1 and
public-API price-equivalent cost, clearly labeled as an analytical normalization
rather than ChatGPT-subscription billing. Promote a larger tier only when it
clears Luna misses often enough to justify its additional cost.

## Stage E: official 270-trial run

After the feature and tier decisions are frozen, resume or launch exactly:

```bash
uv run python scripts/eval_dab.py \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-luna --agent-reasoning max \
  --datasets agnews,bookreview,crmarenapro,deps_dev_v1,github_repos,googlelocal,music_brainz_20k,pancancer_atlas,patents,stockindex,stockmarket,yelp \
  --n-trials 5 \
  --output-dir runs/dab/solultra-luna-max
# Add the frozen winning grounding flags before launch.
```

Luna Max remains the default unless the matched tier study proves that a larger
tier earns its cost on a clearly defined hard-task route. Keep `agnews` in the
official run but flag its parametric-memory leakage risk in reporting.

## Trace and completion gates

Every semantic trial must retain its driver trace and answer/scoring artifacts.
After all 270 semantic keys are complete:

```bash
uv run python scripts/build_dab_trace_bundle.py \
  --run-dir runs/dab/solultra-luna-max \
  --strict-official
```

Completion requires all of the following, not merely a finished process:

- exact 12-dataset, 54-query, five-trial matrix;
- exactly one selected semantic attempt per `(task_id, trial_num)`;
- retryable infrastructure attempts preserved but excluded from scoring;
- clean taint audit and complete per-call traces;
- `submission.json`, `report.md`, strict bundle, and manifest all present;
- feature-ablation and tier-study tables updated with the final recommendation.
