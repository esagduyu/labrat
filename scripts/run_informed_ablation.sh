#!/bin/zsh
# ABLATION: which of the four benchmark-informed packs actually earn their place?
#
# Four arms, one per pack, each ON ALONE so its effect is isolated. Every arm also runs
# a shared PARITY set (bookreview, crmarenapro, yelp) to detect dilution — a pack that
# helps its targets while damaging unrelated tasks is a net loss, which is how the
# taxonomy pack died.
#
# Sonnet, not Opus: the packs are model-agnostic guidance and Sonnet costs ~1/5 of Opus
# on the Max plan, so all four arms fit in the budget one Opus arm would consume. Per
# the design doc, any pack showing a MIXED SIGNAL gets re-ablated on Opus before a
# ship/drop decision. Mixed signal = target gain within noise (Fisher p > 0.2) while
# parity moves at all; or target gain with parity regression; or a pack disagreeing
# across its own target datasets.
#
# BASELINE for comparison: the same tasks from runs/dab/levers-dilution-2026-07-29-shards
# (best Sonnet arm, 0.7435 stratified). All non-pack flags here are meant to match that
# run exactly — but flags matching is NOT sufficient: the CODE that ran them can have
# drifted since the baseline was captured (this is exactly how a prior run of this
# ablation went wrong — see src/labrat/eval/benchmarks/dab/provenance.py). The
# comparability guard below
# certifies that the checkout about to run this ablation is code-comparable to the
# stored baseline before any arm executes; it aborts loudly if not, and refuses (rather
# than passing) if the baseline predates provenance capture.
#
# n=3. Run this from whichever checkout you intend to measure — a frozen worktree is
# strongly recommended for a run this long (see feedback_branch_isolation): the guard
# below only certifies the code state at LAUNCH time, not that it stays put for the
# run's duration.
set -u
cd "${DAB_ABLATION_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

SHARDS=runs/informed-ablation
BASELINE=runs/dab/levers-dilution-2026-07-29-shards/bookreview

# GUARDS — each corresponds to a way this ablation could silently be worthless.
grep -q "informed_shape" scripts/eval_dab.py || { echo "ABORT: pack flags not wired."; exit 6; }
grep -q "_append_trial_row" scripts/eval_dab.py || { echo "ABORT: no durable trials.jsonl write."; exit 6; }
grep -q "__empty_audit__" src/labrat/eval/benchmarks/dab/taint.py || { echo "ABORT: taint gate not fail-closed."; exit 6; }
grep -q '"--effort", self._agent_reasoning' src/labrat/eval/benchmarks/dab/suite.py || { echo "ABORT: no --effort mapping; would run at medium."; exit 6; }
# Comparability guard: the whole point of this ablation is "the only variable is the
# single pack under test" vs. the stored baseline above. Refuses (not passes) if the
# baseline has no recorded provenance (predates this feature) or if this checkout's
# code differs from it in a way that could confound every arm.
uv run python scripts/check_dab_comparability.py "$BASELINE" --live \
  || { echo "ABORT: current checkout is not certified code-comparable to $BASELINE; see above."; exit 6; }
uv sync --extra semantic >/dev/null 2>&1 || { echo "uv sync --extra semantic FAILED"; exit 5; }

PARITY=(bookreview crmarenapro yelp)

run_arm () {
  local arm="$1"; local flag="$2"; shift 2
  local targets=("$@")
  local out="$SHARDS/$arm"
  echo "######## ARM $arm ($flag) targets=${targets[*]} + parity=${PARITY[*]} $(date)"
  for s in "${targets[@]}" "${PARITY[@]}"; do
    for attempt in 1 2 3 4 5 6; do
      echo "=== [$arm/$s] attempt $attempt/6 $(date)"
      env -u ANTHROPIC_API_KEY -u CLAUDECODE uv run python scripts/eval_dab.py \
        --driver claude-mcp \
        --agent-model claude-sonnet-5 \
        --agent-reasoning high \
        --agent-cartograph \
        --hints \
        --agent-levers \
        --llm-classify-backend local-embed \
        --agent-mcp-ledger \
        "$flag" \
        --n-trials 3 \
        --datasets "$s" \
        --output-dir "$out/$s"
      ec=$?
      if [ $ec -eq 0 ]; then echo "=== [$arm/$s] COMPLETE"; break; fi
      echo "=== [$arm/$s] attempt $attempt FAILED exit=$ec $(date)"
      if [ $ec -eq 4 ]; then echo "=== [$arm/$s] exit4 = Max-plan wall; backing off 1h"; fi
      [ $attempt -lt 6 ] && sleep 3600
    done
  done
  echo "=== [$arm] rows: $(cat $out/*/trials.jsonl 2>/dev/null | grep -c .) $(date)"
}

# Targets chosen per the design doc: the datasets whose measured failures each pack aims at.
run_arm A --informed-shape       pancancer_atlas patents deps_dev_v1
run_arm B --informed-validator   stockindex googlelocal
run_arm C --informed-conventions patents stockmarket deps_dev_v1
run_arm D --informed-datasets    github_repos pancancer_atlas agnews

echo "=== ABLATION DONE $(date)"
for a in A B C D; do
  echo "  arm $a: $(cat $SHARDS/$a/*/trials.jsonl 2>/dev/null | grep -c .) rows"
done
