#!/bin/zsh
# OPUS 5 + ALL FOUR INFORMED PACKS — problem datasets, n=5 (claude-mcp, high effort).
#
# ============================ EXPERIMENT DESIGN (rule 7) ============================
# DECISION THIS INFORMS: do the benchmark-informed packs lift Opus 5 on its problem
# datasets enough (>= ~+10pp on this subset) to justify building a disclosed
# benchmark-informed submission? Secondary: trial-by-trial diff vs the archived
# packs-OFF Opus run (task-level flip table), which is the user's stated deliverable.
#
# CONTROL: the archived packs-OFF Opus run (0.750748 full-board), problem subset:
#   /Users/ege/repos/labrat-run-archive-2026-08-01/wt-opusfull-runs/opus-full270-shards
#   Archived control rate on these 8 datasets: 93/140 trials (~0.664 trial-level).
#   The archive has NO provenance record, so scripts/check_dab_comparability.py will
#   REFUSE to certify — this comparison is OBSERVATIONAL by design, defended by the
#   frozen off-path golden hash (a59349f1..., tests/unit/test_claude_mcp_prompt.py),
#   which proves the packs-OFF opening prompt is byte-identical across the two code
#   states. User accepted this trade (2026-08-02) to halve Max spend.
#
# POWER (two-proportion, alpha=.05 two-sided, 80%): n=140/arm at p0~0.66 detects
# |delta| >= ~0.15. Smallest effect worth acting on (+2-3pp board-level) is NOT
# detectable at this n — by design this run answers "large effect or not", plus
# per-task diagnostics. 38/51-saturation lesson applied: these 8 datasets are the
# non-saturated + pack-target set; stockmarket (5/5 saturated at Opus) is included
# as the regression/dilution canary, mirroring the ablation's parity role.
# WHAT CHANGES WHAT: >= +10pp subset -> design a powered full-270 informed run;
# within +/-10pp -> packs stay unshipped (null stands); <= -10pp or canary
# regression (stockmarket task drops >1 trial) -> record harm, close the line.
# Est. spend: 140 trials x ~5.4 min ~ 12.6 h Max wall, sharded, resume-safe.
# ====================================================================================
#
# Feature set = the archived run's EXACTLY (crib: archive run_opus_full270.sh),
# plus the four --informed-* flags. Any other delta would confound the comparison.
set -u
cd /Users/ege/repos/labrat

SHARDS=runs/dab/opus-packs-problem-2026-08-02
mkdir -p "$SHARDS"

# GUARDS — each corresponds to a way this run could silently become worthless.
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
if ! test -f src/labrat/eval/benchmarks/dab/informed_packs.py; then
  echo "ABORT: informed packs module missing."; exit 6
fi
# The two tests this run's validity rests on: pack contamination gate + the frozen
# off-path golden hash that anchors comparability to the archived control.
uv run pytest -q tests/unit/test_dab_informed_packs.py tests/unit/test_claude_mcp_prompt.py \
  >/dev/null 2>&1 || { echo "ABORT: contamination gate / golden hash tests failing."; exit 6; }
uv sync --extra semantic >/dev/null 2>&1 || { echo "uv sync --extra semantic FAILED"; exit 5; }

# cheap -> expensive; agnews last (timeout-heavy + taint-prone).
ORDER=(stockindex deps_dev_v1 stockmarket github_repos pancancer_atlas googlelocal patents agnews)
for s in $ORDER; do
  for attempt in 1 2 3 4 5 6 7 8; do
    echo "=== [opus-packs/$s] attempt $attempt/8 $(date)"
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
    if [ $ec -eq 0 ]; then echo "=== [opus-packs/$s] COMPLETE $(date)"; break; fi
    echo "=== [opus-packs/$s] attempt $attempt FAILED exit=$ec $(date)"
    [ $ec -eq 4 ] && echo "=== [opus-packs/$s] exit4 = Max-plan wall; backing off 1h"
    [ $attempt -lt 8 ] && sleep 3600
  done
done

echo "=== OPUS PACKS coverage $(date)"
tot=0
for s in $ORDER; do
  f="$SHARDS/$s/trials.jsonl"
  n=$([ -f "$f" ] && grep -c . "$f" || echo 0)
  tot=$((tot+n)); echo "  $s: $n rows"
done
echo "  TOTAL: $tot/140"
echo "=== DONE $(date)"
