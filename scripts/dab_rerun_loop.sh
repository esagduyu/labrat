#!/usr/bin/env bash
# Local fallback to the 6h Claude Code routine: run one tick, sleep 6h, repeat
# until every trial has a real result. Same unit of work as the routine
# (dab_rerun_tick.sh) — use ONE mechanism at a time, never both (they'd contend
# on the same run dir and Max-plan budget).
#
# Run it so it survives the terminal closing:
#   cd /Users/ege/repos/labrat
#   nohup bash scripts/dab_rerun_loop.sh >> runs/dab/rerun_loop.log 2>&1 &
#   tail -f runs/dab/rerun_loop.log      # watch progress
#
# Stop early: kill the background process (pkill -f dab_rerun_loop.sh).
set -uo pipefail

TICK="/Users/ege/repos/labrat/scripts/dab_rerun_tick.sh"
SLEEP_SECONDS=21600  # 6h — longer than the 5h Max-plan reset window

while :; do
  echo "===== loop tick $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
  OUT=$(bash "$TICK" 2>&1)
  echo "$OUT"
  if echo "$OUT" | grep -q "\[tick\] DONE"; then
    echo "[loop] all 270 trials have a real result — exiting."
    break
  fi
  echo "[loop] sleeping ${SLEEP_SECONDS}s until the next tick..."
  sleep "$SLEEP_SECONDS"
done
