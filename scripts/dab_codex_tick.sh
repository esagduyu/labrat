#!/usr/bin/env bash
# Self-healing tick for a DAB run on GPT-5.5 via the ChatGPT subscription
# (labrat-agent driver + codex provider). Mirrors dab_rerun_tick.sh but for the
# codex path: the watchdog the user asked for ("retry every ~5 min to check the
# process is still running"), so a $20-plan rate-limit window self-heals.
#
# Each tick:
#   1. concurrency guard — never two eval_dab against the same run dir;
#   2. probes GPT-5.5 availability (a trivial codex call) and skips the tick if
#      rate-limited, so we don't blast fast-fail infra trials into trials.jsonl
#      during a limit window (same lesson as the Max-plan rerun loop);
#   3. starts / resumes eval_dab.py --output-dir <RUN_DIR> (skips completed
#      (task,trial) pairs, auto-retries infra-marked ones);
#   4. prints a progress summary.
#
# Parameterized by env (with smoke defaults) so the same script serves the
# baseline smoke, the verifier-on run, and the eventual full run:
#   RUN_DIR   target run dir (default runs/dab/dab-codex-smoke)
#   DATASETS  comma-separated dataset filter (default the 5 DuckDB+SQLite set —
#             no mongod/postgres needed; includes the pernicious music_brainz /
#             deps_dev_v1 and matches the Phase 1b/4 subset for an apples-to-
#             apples GPT-5.5-vs-Sonnet read)
#   NTRIALS   trials per query (default 2)
#   VERIFY    "1" to enable --agent-verify (default off)
#   REASONING codex reasoning effort (default medium — the $20-plan budget)
set -uo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

REPO="/Users/ege/repos/labrat"
RUN_DIR="${RUN_DIR:-$REPO/runs/dab/dab-codex-smoke}"
DATASETS="${DATASETS:-deps_dev_v1,github_repos,music_brainz_20k,stockindex,stockmarket}"
NTRIALS="${NTRIALS:-2}"
VERIFY="${VERIFY:-0}"
REASONING="${REASONING:-medium}"
cd "$REPO" || { echo "[codex-tick] cannot cd to $REPO"; exit 1; }

echo "[codex-tick] $(date -u +%Y-%m-%dT%H:%M:%SZ) RUN_DIR=$RUN_DIR DATASETS=$DATASETS NTRIALS=$NTRIALS VERIFY=$VERIFY"

# ── Concurrency guard ────────────────────────────────────────────────────────
RUN_TAG="$(basename "$RUN_DIR")"
if pgrep -f "eval_dab.py.*$RUN_TAG" >/dev/null 2>&1; then
  echo "[codex-tick] an eval_dab for $RUN_TAG is already running — skipping."
  exit 0
fi

# ── 1. Probe GPT-5.5 availability ────────────────────────────────────────────
# A trivial one-turn codex call. If we're rate-limited the provider raises on the
# 429 and run_task prints a traceback (no JSON) — treat absence of {"final_text"
# as "limited" and skip, so we don't fast-fail the whole queue as infra.
PROBE=$(uv run python scripts/run_task.py \
          --provider codex --model gpt-5.5 \
          --prompt "Reply with the single word: ready. Do not call any tools." \
          --connections '{"main":{"db_type":"duckdb","db_path":"tests/fixtures/sample_dbs/ecommerce.duckdb"}}' \
          2>&1)
if ! echo "$PROBE" | grep -q '"final_text"'; then
  echo "[codex-tick] GPT-5.5 unavailable (rate-limited or auth issue) — skipping. Probe: ${PROBE:0:200}"
  exit 0
fi
echo "[codex-tick] GPT-5.5 probe OK."

# ── 2. Start / resume the run ────────────────────────────────────────────────
VERIFY_FLAG=()
[ "$VERIFY" = "1" ] && VERIFY_FLAG=(--agent-verify)
# Optional per-trial tool-call cap to clip the heavy tail (one 74-turn trial cost
# ~1.9M tokens). Only passed when MAX_TOOL_CALLS is set, because --max-tool-calls
# is resume-guarded — a fresh run sets it; an existing uncapped run must not.
CAP_FLAG=()
[ -n "${MAX_TOOL_CALLS:-}" ] && CAP_FLAG=(--max-tool-calls "$MAX_TOOL_CALLS")
uv run python scripts/eval_dab.py \
  --driver labrat-agent \
  --agent-provider codex \
  --agent-model gpt-5.5 \
  --agent-reasoning "$REASONING" \
  --n-trials "$NTRIALS" \
  --datasets "$DATASETS" \
  "${VERIFY_FLAG[@]}" \
  "${CAP_FLAG[@]}" \
  --output-dir "$RUN_DIR"
RC=$?
echo "[codex-tick] eval_dab exit=$RC"

# ── 3. Progress summary ──────────────────────────────────────────────────────
if [ -f "$RUN_DIR/trials.jsonl" ]; then
  DATASETS="$DATASETS" NTRIALS="$NTRIALS" uv run python3 - "$RUN_DIR/trials.jsonl" <<'PY'
import json, os, sys
from collections import defaultdict
wanted = set(os.environ["DATASETS"].split(","))
ntrials = int(os.environ["NTRIALS"])
rows = [json.loads(l) for l in open(sys.argv[1])]
rows = [r for r in rows if r["task_id"].split(":")[0] in wanted]
real = {}
for r in rows:  # latest non-infra attempt per (task,trial)
    k = (r["task_id"], r["trial_num"])
    reason = r.get("reason") or ""
    if k not in real or (reason and not reason.startswith("infra:")):
        real[k] = r
done = [r for r in real.values() if not (r.get("reason") or "").startswith("infra:")]
infra = [r for r in real.values() if (r.get("reason") or "").startswith("infra:")]
queries = {r["task_id"] for r in rows}
target = len(queries) * ntrials
passes = defaultdict(lambda: [0, 0])
for r in done:
    ds = r["task_id"].split(":")[0]
    passes[ds][1] += 1
    if r.get("passed"):
        passes[ds][0] += 1
print(f"[codex-tick] progress: {len(done)}/{target} real trials "
      f"({len(queries)} queries x{ntrials}); {len(infra)} infra-blocked (retry next tick).")
for ds in sorted(passes):
    p, n = passes[ds]
    print(f"[codex-tick]   {ds}: {p}/{n} pass")
if len(done) >= target:
    print("[codex-tick] DONE — every trial in this subset has a real result.")
PY
fi

# Optionally refresh the overnight score sheet after this tick (set by the
# overnight orchestrator) so a long phase keeps OVERNIGHT_SCORESHEET.md current.
if [ "${SCORESHEET_AFTER:-0}" = "1" ]; then
  uv run python "$REPO/scripts/dab_codex_scoresheet.py" >/dev/null 2>&1 || true
fi
