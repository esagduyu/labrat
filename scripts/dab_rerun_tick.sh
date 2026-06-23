#!/usr/bin/env bash
# Self-healing tick for the DAB clean (sandboxed) re-run.
#
# Designed to be fired every ~6h by a Claude Code routine on the
# Eges-MacBook-Air bridge environment (so it has local Max-plan OAuth, the local
# DataAgentBench checkout, and a running mongod). Each tick:
#   1. probes Max-plan availability (skip if the session limit is active, so we
#      don't blast ~170 fast-fail trials into trials.jsonl — see the memory note);
#   2. starts (first tick) or resumes (later ticks) the full 54-query run via
#      eval_dab.py --output-dir <fixed dir>, which skips completed (task,trial)
#      pairs and auto-retries infra-marked ones;
#   3. prints a progress summary the routine can relay.
#
# Idempotent: safe to run repeatedly. Once every (task,trial) has a real
# (non-infra) result, a tick runs zero trials and just reprints the summary.
set -uo pipefail

# cron and routine shells start with a minimal PATH — make claude / uv / brew /
# mongod reachable regardless of how this is fired.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO="/Users/ege/repos/labrat"
# RUN_DIR / EXTRA_EVAL_FLAGS are env-overridable (defaults preserve the original
# clean-rerun behaviour). The submission run sets RUN_DIR to a fresh dir and
# EXTRA_EVAL_FLAGS="--agent-cartograph --hints".
RUN_DIR="${RUN_DIR:-$REPO/runs/dab/dab-rerun-clean}"
EXTRA_EVAL_FLAGS="${EXTRA_EVAL_FLAGS:-}"
RUN_TAG="$(basename "$RUN_DIR")"
cd "$REPO" || { echo "[tick] cannot cd to $REPO"; exit 1; }

echo "[tick] $(date -u +%Y-%m-%dT%H:%M:%SZ) starting DAB tick (RUN_DIR=$RUN_TAG flags='$EXTRA_EVAL_FLAGS')"

# ── Concurrency guard ────────────────────────────────────────────────────────
# Never run two eval_dab against the same run dir (e.g. a manual resume overlapping
# the next 6h cron fire) — they'd race on trials.jsonl. If one is already running,
# this tick is a no-op.
if pgrep -f "eval_dab.py.*$RUN_TAG" >/dev/null 2>&1; then
  echo "[tick] an eval_dab for $RUN_TAG is already running — skipping this tick."
  exit 0
fi

# ── 0. mongod must be up (agnews/yelp use it via load_mongo_collection) ──────
if ! pgrep -x mongod >/dev/null 2>&1; then
  echo "[tick] WARNING: mongod is not running — agnews/yelp trials will fail."
  echo "[tick] Start it (e.g. 'brew services start mongodb-community') before the next tick."
fi

# ── 1. Probe Max-plan OAuth ──────────────────────────────────────────────────
PROBE=$(env -u ANTHROPIC_API_KEY -u CLAUDECODE claude --print --model claude-sonnet-4-6 \
          --max-turns 1 -p "Reply with the single word: ready" </dev/null 2>&1)
if echo "$PROBE" | grep -qiE "session limit|usage limit|credit balance|rate limit|quota"; then
  echo "[tick] Max-plan unavailable (limit active) — skipping this tick. Probe: ${PROBE:0:160}"
  exit 0
fi
echo "[tick] Max-plan probe OK."

# ── 2. Start / resume the full run ───────────────────────────────────────────
# Scope to the 12 OFFICIAL DAB datasets only — the local DAB checkout also contains
# 5 unofficial extras (civic_unstructured, cve, imdb, krama, usaspending = 50 queries)
# that are NOT part of the 54-query benchmark. Without this filter the run wastes
# budget on them and pollutes the leaderboard aggregate.
OFFICIAL="agnews,bookreview,crmarenapro,deps_dev_v1,github_repos,googlelocal,music_brainz_20k,pancancer_atlas,patents,stockindex,stockmarket,yelp"
env -u ANTHROPIC_API_KEY -u CLAUDECODE uv run python scripts/eval_dab.py \
  --driver claude-mcp \
  --n-trials 5 \
  --agent-model claude-sonnet-4-6 \
  --agent-timeout 1200 \
  --datasets "$OFFICIAL" \
  $EXTRA_EVAL_FLAGS \
  --output-dir "$RUN_DIR"
RC=$?

# ── 3. Progress summary ──────────────────────────────────────────────────────
echo "[tick] eval_dab exit=$RC"
if [ -f "$RUN_DIR/trials.jsonl" ]; then
  uv run python3 - "$RUN_DIR/trials.jsonl" <<'PY'
import json, sys
from collections import defaultdict
OFFICIAL = {"agnews","bookreview","crmarenapro","deps_dev_v1","github_repos","googlelocal",
            "music_brainz_20k","pancancer_atlas","patents","stockindex","stockmarket","yelp"}
rows = [json.loads(l) for l in open(sys.argv[1])
        if json.loads(l)["task_id"].split(":")[0] in OFFICIAL]  # official datasets only
real = {}
for r in rows:  # keep the latest non-infra attempt per (task,trial)
    k = (r["task_id"], r["trial_num"])
    reason = r.get("reason") or ""
    if k not in real or (reason and not reason.startswith("infra:")):
        real[k] = r
done = [r for r in real.values() if not (r.get("reason") or "").startswith("infra:")]
infra = [r for r in real.values() if (r.get("reason") or "").startswith("infra:")]
contam = [r for r in real.values() if (r.get("reason") or "").startswith("contaminated:")]
TARGET = 54 * 5
print(f"[tick] progress: {len(done)}/{TARGET} trials with a real result; "
      f"{len(infra)} still infra-blocked (will retry next tick); "
      f"{len(contam)} contaminated (should be 0).")
if len(done) >= TARGET:
    print("[tick] DONE — all 270 trials have a real result. Disable the routine.")
PY
fi
