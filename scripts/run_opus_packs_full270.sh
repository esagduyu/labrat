#!/bin/zsh
# FULL OPUS 5 RUN — informed packs v2, all 12 official datasets, n=5 (270 trials).
# claude-mcp, high effort, Max-plan OAuth. Candidate submission run.
#
# ============================ EXPERIMENT DESIGN (rule 7) ============================
# DECISION: does Opus 5 + packs v2 beat the archived packs-OFF 0.750748 by enough to
# become the submission candidate — and does it come back CLEAN (270/270 semantic,
# zero taint), which the archived run did not (2 tainted + 2 missing agnews trials
# make dab_shards merge refuse it)?
#
# PROJECTION BEING TESTED (from the 2026-08-02 problem-subset run + the targeted
# googlelocal v2 gate): packs v1 was +6 trials (stockindex) and -6 (googlelocal
# delivery drift) vs archive = wash. v2 repaired the drift mechanism (adjacency
# interposition + structure summarization). If the stockindex-class gains hold and
# googlelocal returns to archive levels, projected ~+2pp stratified vs 0.7507 —
# a new best-ever and rank ~7 on the current board as the #1 untuned entry (packs
# are untuned-eligible: form-only rules, contamination-gated, no dataset content).
#
# GATES BEFORE THIS LAUNCHES (both must have been green):
#   1. packs v2 full test suite incl. contamination gate + interposition guard.
#   2. targeted googlelocal n=3: tasks 2+3 recovered to >=2/3 each, tasks 1+4 held.
# WHAT CHANGES WHAT: score > 0.7507 AND clean -> build trace bundle, propose
# submission. Score <= 0.7507 -> keep archive as best; packs line closes (two
# strikes). Tainted agnews rows -> the clean-gate question returns to the user
# BEFORE any packaging (gate() rejects non-clean; policy decision, not mine).
# Est. spend: 270 trials x ~5.4 min ~ 24h Max wall, sharded, resume-safe.
# ====================================================================================
set -u
cd /Users/ege/repos/labrat-wt-packsv2

SHARDS=runs/dab/opus-packsv2-full270-shards
mkdir -p "$SHARDS"

if [ -n "$(git status --porcelain -- src scripts)" ]; then
  echo "ABORT: src/scripts dirty; launch only from a clean committed tree (rule 5)."; exit 6
fi
if ! grep -q "_append_trial_row" scripts/eval_dab.py; then
  echo "ABORT: no durable trials.jsonl write."; exit 6
fi
if ! grep -q "__empty_audit__" src/labrat/eval/benchmarks/dab/taint.py; then
  echo "ABORT: taint gate not fail-closed."; exit 6
fi
if ! grep -q '"--effort", self._agent_reasoning' src/labrat/eval/benchmarks/dab/suite.py; then
  echo "ABORT: no --effort mapping; would silently run at medium."; exit 6
fi
if ! grep -q "never between a label and its value" src/labrat/eval/benchmarks/dab/informed_packs.py; then
  echo "ABORT: packs v2 interposition fix not present in this checkout."; exit 6
fi
uv run pytest -q tests/unit/test_dab_informed_packs.py tests/unit/test_claude_mcp_prompt.py \
  >/dev/null 2>&1 || { echo "ABORT: pack gate / prompt tests failing."; exit 6; }
uv sync --extra semantic >/dev/null 2>&1 || { echo "uv sync --extra semantic FAILED"; exit 5; }

# cheap -> expensive: banks coverage early; agnews last (slow + taint-prone).
ORDER=(bookreview stockindex stockmarket deps_dev_v1 github_repos pancancer_atlas music_brainz_20k googlelocal patents crmarenapro yelp agnews)
for s in $ORDER; do
  for attempt in 1 2 3 4 5 6 7 8; do
    echo "=== [opus-packsv2/$s] attempt $attempt/8 $(date)"
    env -u ANTHROPIC_API_KEY -u CLAUDECODE uv run python scripts/eval_dab.py \
      --driver claude-mcp \
      --agent-model claude-opus-5 \
      --agent-reasoning high \
      --agent-cartograph \
      --hints \
      --agent-levers \
      --llm-classify-backend local-embed \
      --agent-mcp-ledger \
      --agent-mcp-tool-prompt \
      --agent-answer-gate \
      --informed-shape \
      --informed-validator \
      --informed-conventions \
      --informed-datasets \
      --agent-timeout 1800 \
      --n-trials 5 \
      --datasets "$s" \
      --output-dir "$SHARDS/$s"
    ec=$?
    if [ $ec -eq 0 ]; then echo "=== [opus-packsv2/$s] COMPLETE $(date)"; break; fi
    echo "=== [opus-packsv2/$s] attempt $attempt FAILED exit=$ec $(date)"
    [ $ec -eq 4 ] && echo "=== [opus-packsv2/$s] exit4 = Max-plan wall; backing off 1h"
    [ $attempt -lt 8 ] && sleep 3600
  done
done

echo "=== OPUS PACKS-V2 FULL 270 coverage $(date)"
tot=0
for s in $ORDER; do
  f="$SHARDS/$s/trials.jsonl"
  n=$([ -f "$f" ] && grep -c . "$f" || echo 0)
  tot=$((tot+n)); echo "  $s: $n rows"
done
echo "  TOTAL: $tot/270"
echo "=== DONE $(date)"
