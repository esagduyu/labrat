#!/usr/bin/env bash
# Sonnet-5 M2+M1 ablation — 6 arms, shared baseline, M2 arms first.
# Resume-safe: each eval_dab call points at a persistent --output-dir, so re-running
# this script skips completed trials and resumes partial ones. Safe to relaunch after
# a Max-plan session-limit interruption.
set -uo pipefail
cd /Users/ege/repos/labrat

SUBSET="deps_dev_v1,music_brainz_20k,stockindex,pancancer_atlas,yelp"
ROOT="runs/dab/s5-abl"
COMMON=(--driver claude-mcp --agent-model claude-sonnet-5 --hints --n-trials 3
        --datasets "$SUBSET" --agent-cartograph)

# Strip metered-key env so the claude CLI uses Max-plan OAuth (belt-and-suspenders;
# the claude-mcp path also strips these itself).
run_arm() {
  local name="$1"; shift
  echo "=== ARM $name START $(date -u +%H:%M:%S) ==="
  env -u ANTHROPIC_API_KEY -u CLAUDECODE \
    uv run python scripts/eval_dab.py "${COMMON[@]}" "$@" --output-dir "$ROOT/$name"
  echo "=== ARM $name EXIT rc=$? $(date -u +%H:%M:%S) ==="
}

# --- M2 first (2 arms: shared baseline + verified semantics) ---
run_arm baseline
run_arm m2-semantics --cartograph-semantics --cartograph-semantics-model claude-sonnet-5

# --- M1 verification-v2 (4 arms, reuse baseline as the 'off' arm) ---
# Reordered 2026-07-04: run the cheap/high-signal arms first (nodiv = diversity
# control, postverify), leave the slow low-value argue arm last. All resume-safe.
run_arm m1-consensus  --agent-consensus 3
run_arm m1-nodiv      --agent-consensus 3 --no-consensus-diversity
run_arm m1-postverify --agent-postverify
run_arm m1-argue      --agent-consensus 3 --agent-argue-rounds 2

echo "=== ALL ARMS DONE $(date -u +%H:%M:%S) ==="
