#!/usr/bin/env bash
# Watchdog loop for a DAB run on GPT-5.5 (codex provider). Runs one
# dab_codex_tick.sh, sleeps ~5 min, repeats until the subset is complete.
# The tick is idempotent + concurrency-guarded + probes before resuming, so a
# $20-plan rate-limit window self-heals: ticks during a limit are cheap no-ops,
# and the run resumes within ~5 min of reset.
#
# Run it detached (survives the terminal closing):
#   cd /Users/ege/repos/labrat
#   RUN_DIR=runs/dab/dab-codex-smoke nohup bash scripts/dab_codex_loop.sh \
#     >> runs/dab/codex_loop.log 2>&1 &
#   tail -f runs/dab/codex_loop.log
#
# Env passed through to the tick: RUN_DIR, DATASETS, NTRIALS, VERIFY, REASONING.
# Stop early: pkill -f dab_codex_loop.sh
set -uo pipefail

TICK="/Users/ege/repos/labrat/scripts/dab_codex_tick.sh"
# 5-min poll, per the user's ask. The probe is cheap and skips cleanly while
# rate-limited, so frequent ticks just mean we resume promptly after a reset.
SLEEP_SECONDS="${SLEEP_SECONDS:-300}"

while :; do
  echo "===== codex loop tick $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
  OUT=$(bash "$TICK" 2>&1)
  echo "$OUT"
  if echo "$OUT" | grep -q "\[codex-tick\] DONE"; then
    echo "[codex-loop] subset complete — exiting."
    break
  fi
  echo "[codex-loop] sleeping ${SLEEP_SECONDS}s until the next tick..."
  sleep "$SLEEP_SECONDS"
done
