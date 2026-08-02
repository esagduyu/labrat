#!/bin/zsh
# Full 12-dataset Sonnet-5 run at XHIGH effort. claude-mcp on Max plan (no API).
# 270 trials (54 queries x 5). NEW output dir so it does NOT resume-skip the
# high/medium runs' trials — this is a fresh A/B arm at --effort xhigh.
# TIMEOUT RAISED to 1800s: at HIGH, 2 trials already hit the 1200s wall and 5 ran
# >900s; xhigh trials run longer, so the default 1200s ceiling would turn real
# answers into infra:timeout rows and understate the score. Give them room.
# Session-resilient: Max-plan 5h session walls are expected — on exit 4 back off
# ~1h and retry. Resume-safe: completed trials skip on every re-entry.
# NEEDS the `semantic` extra (uv sync --extra semantic) for local-embed classify.
#
# "Everything we built" for the claude-mcp path is ON: cartograph + hints +
# levers + local-embed classify + the GAP 1/2 catalog fixes (automatic, ride the
# checkout) + the GAP 3 server-side Context Ledger (--agent-mcp-ledger). The
# ledger budget is raised to 64 KB in-suite so it never truncates grounding
# (search_reference_docs/describe_table run 8-22 KB) and only bounds genuinely
# oversized run_sql dumps — smoke-verified 2026-07-24 (grounding intact, PASS).
# Deliberately OFF: --agent-taxonomy (net-negative; its rules are in --agent-levers)
# and --cartograph-semantics (-3.7pp). Verifier/consensus flags don't apply to
# claude-mcp (labrat-agent path only).
set -u
cd /Users/ege/repos/labrat

# GUARD: this run is only meaningful if the checkout maps --agent-reasoning to the
# claude CLI --effort flag (commit e0aa265 on master). Without it, --agent-reasoning
# high is silently ignored and the run is medium effort again.
if ! grep -q '"--effort", self._agent_reasoning' src/labrat/eval/benchmarks/dab/suite.py; then
  echo "ABORT: this checkout lacks the --effort mapping (commit e0aa265). Pull/checkout"
  echo "       a master that has it, or the run is silently medium effort."
  exit 6
fi

# local-embed classify backend requires the semantic extra; ensure it's present.
uv sync --extra semantic >/dev/null 2>&1 || { echo "uv sync --extra semantic FAILED"; exit 5; }

SHARDS=runs/dab/full12-sonnet5-xhigh-fixes-shards
OUT=runs/dab/full12-sonnet5-xhigh-fixes

# GUARD: never run two evals on the same shards concurrently
if pgrep -f "eval_dab.py --output-dir $SHARDS" >/dev/null 2>&1; then
  echo "ABORT: a Sonnet-5-xhigh eval is already running for $SHARDS"; exit 3
fi

# GUARD: don't launch while the HIGH-effort run is still going — they'd fight for
# the same Max-plan session budget and both crawl. Wait for it to finish first.
if pgrep -f "eval_dab.py --output-dir runs/dab/full12-sonnet5-high-fixes-shards" >/dev/null 2>&1; then
  echo "ABORT: the HIGH-effort run is still active — let it finish before starting xhigh."; exit 7
fi

# cheap->expensive-ish so early coverage banks before session pressure
ORDER=(bookreview deps_dev_v1 stockindex stockmarket music_brainz_20k googlelocal patents pancancer_atlas github_repos crmarenapro yelp agnews)
for s in $ORDER; do
  for attempt in 1 2 3 4 5 6 7 8; do
    echo "=== [$s] attempt $attempt/8 $(date)"
    env -u ANTHROPIC_API_KEY -u CLAUDECODE uv run python scripts/eval_dab.py \
      --driver claude-mcp \
      --agent-model claude-sonnet-5 \
      --agent-reasoning xhigh \
      --agent-cartograph \
      --hints \
      --agent-levers \
      --llm-classify-backend local-embed \
      --agent-mcp-ledger \
      --agent-timeout 1800 \
      --n-trials 5 \
      --datasets "$s" \
      --output-dir "$SHARDS/$s"
    ec=$?
    if [ $ec -eq 0 ]; then echo "=== [$s] COMPLETE"; break; fi
    echo "=== [$s] attempt $attempt FAILED exit=$ec $(date)"
    if [ $ec -eq 4 ]; then echo "=== [$s] exit4 = Max-plan session/rate wall; long backoff before retry"; fi
    [ $attempt -lt 8 ] && sleep 3600
  done
done

echo "=== per-shard coverage (partial OK — score honestly) $(date)"
for s in $ORDER; do f="$SHARDS/$s/trials.jsonl"; [ -f "$f" ] && echo "  $s: $(grep -c . "$f") rows" || echo "  $s: 0"; done
echo "=== DONE $(date)"
