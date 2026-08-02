#!/bin/zsh
# FULL OPUS 5 RUN — 270 trials, with the benchmark-informed packs that SURVIVED ablation.
#
# DO NOT LAUNCH UNTIL THE ABLATION VERDICTS ARE IN. Set PACK_FLAGS below to exactly the
# packs that earned their place; leave a pack out and it simply does not ship. Every pack
# defaults OFF in code, so an empty PACK_FLAGS reproduces the plain configuration.
#
# Usage:
#   PACK_FLAGS="--informed-shape --informed-validator" ./run_opus_informed_270.sh
#
# Frozen worktree: this must run from a checkout whose HEAD does not move mid-run —
# eval_dab.py is re-invoked per dataset shard, so an edit under it silently changes the
# code beneath later shards.
set -u
cd "${DAB_ABLATION_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

: "${PACK_FLAGS:?Set PACK_FLAGS to the ablation-surviving packs, or \"\" for none. Refusing to guess.}"

SHARDS=runs/opus-informed-270-shards

# GUARDS — each maps to a way this expensive run could silently be worthless.
grep -q "_append_trial_row" scripts/eval_dab.py || { echo "ABORT: no durable trials.jsonl write."; exit 6; }
grep -q "__empty_audit__" src/labrat/eval/benchmarks/dab/taint.py || { echo "ABORT: taint gate not fail-closed."; exit 6; }
grep -q '"--effort", self._agent_reasoning' src/labrat/eval/benchmarks/dab/suite.py || { echo "ABORT: no --effort mapping; would run at medium."; exit 6; }
grep -q "informed_shape" scripts/eval_dab.py || { echo "ABORT: pack flags not wired."; exit 6; }
# The off-path golden hash must still hold: if it does not, the completed 270-trial
# baseline is no longer comparable and this run cannot be read against it.
uv run pytest tests/unit/test_claude_mcp_prompt.py -q -k baseline >/dev/null 2>&1 \
  || { echo "ABORT: off-path golden-hash test fails; baseline comparability is broken."; exit 6; }
uv sync --extra semantic >/dev/null 2>&1 || { echo "uv sync --extra semantic FAILED"; exit 5; }

echo "=== packs enabled: ${PACK_FLAGS:-（none）}"

# cheap -> expensive so partial coverage is still readable if a weekly limit stops the run
ORDER=(bookreview stockindex stockmarket deps_dev_v1 github_repos pancancer_atlas music_brainz_20k googlelocal patents crmarenapro yelp agnews)
for s in $ORDER; do
  for attempt in 1 2 3 4 5 6 7 8; do
    echo "=== [opus-informed/$s] attempt $attempt/8 $(date)"
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
      ${=PACK_FLAGS} \
      --agent-timeout 1800 \
      --n-trials 5 \
      --datasets "$s" \
      --output-dir "$SHARDS/$s"
    ec=$?
    if [ $ec -eq 0 ]; then echo "=== [opus-informed/$s] COMPLETE $(date)"; break; fi
    echo "=== [opus-informed/$s] attempt $attempt FAILED exit=$ec $(date)"
    if [ $ec -eq 3 ]; then echo "=== [opus-informed/$s] exit3 = TAINT GATE. Retrying will fail identically — investigate before wasting backoff."; break; fi
    if [ $ec -eq 4 ]; then echo "=== [opus-informed/$s] exit4 = Max-plan wall; backing off 1h"; fi
    [ $attempt -lt 8 ] && sleep 3600
  done
done

echo "=== OPUS INFORMED 270 coverage $(date)"
tot=0
for s in $ORDER; do
  f="$SHARDS/$s/trials.jsonl"; n=$([ -f "$f" ] && grep -c . "$f" || echo 0)
  tot=$((tot+n)); echo "  $s: $n rows"
done
echo "  TOTAL: $tot/270"
