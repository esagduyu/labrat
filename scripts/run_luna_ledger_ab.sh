#!/bin/zsh
# GPT-5.6-LUNA LEDGER-BUDGET A/B — packs ON both arms, problem datasets, n=5
# (labrat-agent driver, codex subscription provider, zero Anthropic credits).
#
# ============================ EXPERIMENT DESIGN (rule 7) ============================
# DECISION THIS INFORMS: does raising the IN-PROCESS Context Ledger budget from its
# 8000-byte default to 64000 (--agent-ledger-max-bytes) improve the labrat-agent/
# codex path — the configuration behind the accepted 74.18% leaderboard entry, which
# made 398 search_reference_docs calls into the 8 KB cap with no get_artifact escape?
# If yes -> the flag joins the next Luna-lineage submission config. Both arms carry
# all four informed packs (user's design 2026-08-02), so the read is "budget effect
# on top of packs", directly comparable trial-by-trial to BOTH the accepted Luna
# run (packs-off archive) and this campaign's Opus packs run.
#
# ARMS (interleaved per dataset — control shard then treatment shard — closing the
# temporal confound the packs ablation left open, its §6.7):
#   control:   packs ON, ledger budget DEFAULT (8000)
#   treatment: packs ON, --agent-ledger-max-bytes 64000
#
# PRIMARY (mechanism, detectable regardless of score noise): count of tool outputs
# truncated at the ledger cap in agent_tool_calls.jsonl — treatment must drop to ~0.
# If treatment still truncates, the flag is not wired end-to-end: ABORT and debug,
# do not read scores. SECONDARY (score): n=140/arm detects |delta| >= ~0.15 at 80%
# power; anything smaller is diagnostic only (per-task flip table).
# WHAT CHANGES WHAT: truncations ~0 AND score >= control -> adopt flag (mechanism
# fix, no measured cost); score < control by >= 10pp -> investigate before adopting;
# truncations unchanged -> wiring bug, result void.
# Est. spend: 280 trials x ~5 min ~ 23 h wall on the ChatGPT subscription, sharded,
# resume-safe, rate-limit fail-fast (exit 4 -> 1h backoff).
# ====================================================================================
set -u
cd /Users/ege/repos/labrat

ROOT=runs/dab/luna-ledger-ab-2026-08-02
mkdir -p "$ROOT"

if [ -n "$(git status --porcelain -- src scripts)" ]; then
  echo "ABORT: src/scripts dirty; launch only from a clean committed tree (rule 5)."; exit 6
fi
if ! grep -q "_append_trial_row" scripts/eval_dab.py; then
  echo "ABORT: no durable trials.jsonl write."; exit 6
fi
if ! grep -q "agent_ledger_max_bytes" scripts/eval_dab.py; then
  echo "ABORT: ledger budget flag not present in this checkout."; exit 6
fi
if ! test -f src/labrat/eval/benchmarks/dab/informed_packs.py; then
  echo "ABORT: informed packs module missing."; exit 6
fi
uv run pytest -q tests/unit/test_dab_informed_packs.py >/dev/null 2>&1 \
  || { echo "ABORT: pack contamination gate failing."; exit 6; }

run_shard() {
  local arm=$1 ds=$2; shift 2
  for attempt in 1 2 3 4 5 6; do
    echo "=== [luna-ab/$arm/$ds] attempt $attempt/6 $(date)"
    uv run python scripts/eval_dab.py \
      --driver labrat-agent \
      --agent-provider codex \
      --agent-model gpt-5.6-luna \
      --agent-reasoning max \
      --agent-cartograph \
      --hints \
      --agent-levers \
      --informed-shape \
      --informed-validator \
      --informed-conventions \
      --informed-datasets \
      "$@" \
      --agent-timeout 1800 \
      --n-trials 5 \
      --datasets "$ds" \
      --output-dir "$ROOT/$arm/$ds"
    local ec=$?
    if [ $ec -eq 0 ]; then echo "=== [luna-ab/$arm/$ds] COMPLETE $(date)"; return 0; fi
    echo "=== [luna-ab/$arm/$ds] attempt $attempt FAILED exit=$ec $(date)"
    [ $ec -eq 4 ] && echo "=== [luna-ab/$arm/$ds] exit4 = rate limit; backing off 1h"
    [ $attempt -lt 6 ] && sleep 3600
  done
  return 1
}

# Interleave arms within each dataset so endpoint drift hits both arms equally.
ORDER=(stockindex deps_dev_v1 stockmarket github_repos pancancer_atlas googlelocal patents agnews)
for s in $ORDER; do
  run_shard control "$s"
  run_shard treatment "$s" --agent-ledger-max-bytes 64000
done

echo "=== LUNA LEDGER A/B coverage $(date)"
for arm in control treatment; do
  tot=0
  for s in $ORDER; do
    f="$ROOT/$arm/$s/trials.jsonl"
    n=$([ -f "$f" ] && grep -c . "$f" || echo 0)
    tot=$((tot+n))
  done
  echo "  $arm TOTAL: $tot/140"
done
echo "=== DONE $(date)"
