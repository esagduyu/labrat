#!/usr/bin/env bash
# Overnight GPT-5.5 orchestration. Runs three phases sequentially, each driven by
# the self-healing codex watchdog (probes GPT-5.5, resumes, retries infra), and
# regenerates the score sheet after every phase so a partial result is always
# available:
#
#   A  subset (5 DuckDB+SQLite)  n=5  verify OFF  -> runs/dab/dab-codex-smoke   (resumes the in-progress run)
#   B  subset (5 DuckDB+SQLite)  n=5  verify ON   -> runs/dab/dab-codex-verify  (the verifier ablation: A vs B)
#   C  full 54-query (12 official) n=5 verify OFF -> runs/dab/dab-codex-full    (the leaderboard-comparable number)
#
# Phases run in priority order: A and B (small, highest-value) finish first; C
# (270 trials) runs as long as the night allows and is fully resumable.
#
# Launch detached:
#   cd /Users/ege/repos/labrat
#   nohup bash scripts/dab_codex_overnight.sh >> runs/dab/overnight.log 2>&1 &
#   tail -f runs/dab/overnight.log
# Score sheet: runs/dab/OVERNIGHT_SCORESHEET.md (refreshed after each phase).
set -uo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
REPO="/Users/ege/repos/labrat"
cd "$REPO" || { echo "[overnight] cannot cd to $REPO"; exit 1; }

SUBSET="deps_dev_v1,github_repos,music_brainz_20k,stockindex,stockmarket"
OFFICIAL="agnews,bookreview,crmarenapro,deps_dev_v1,github_repos,googlelocal,music_brainz_20k,pancancer_atlas,patents,stockindex,stockmarket,yelp"

# Keep OVERNIGHT_SCORESHEET.md fresh after every tick (read by dab_codex_tick.sh).
export SCORESHEET_AFTER=1

say() { echo "[overnight] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

# mongod is required for agnews/yelp in phase C.
if ! pgrep -x mongod >/dev/null 2>&1; then
  say "WARNING: mongod is DOWN — agnews/yelp (phase C) will fail. Start it before C if possible."
fi

run_phase() {  # $1=run_dir  $2=datasets  $3=verify(0/1)  $4=label
  local run_dir="$1" datasets="$2" verify="$3" label="$4"
  say "=== PHASE START: $label (dir=$(basename "$run_dir"), verify=$verify) ==="
  RUN_DIR="$run_dir" DATASETS="$datasets" NTRIALS=5 VERIFY="$verify" REASONING=medium \
    bash "$REPO/scripts/dab_codex_loop.sh"
  say "=== PHASE DONE: $label ==="
  # Refresh the score sheet after each phase (best-effort).
  uv run python "$REPO/scripts/dab_codex_scoresheet.py" >/dev/null 2>&1 \
    && say "score sheet refreshed -> runs/dab/OVERNIGHT_SCORESHEET.md" \
    || say "score sheet refresh failed (non-fatal)"
}

say "START — three phases queued (A subset baseline, B verifier ablation, C full 54-query)."
run_phase "$REPO/runs/dab/dab-codex-smoke"  "$SUBSET"   0 "A: subset baseline n=5 (verify off)"
run_phase "$REPO/runs/dab/dab-codex-verify" "$SUBSET"   1 "B: subset verifier-on n=5"
run_phase "$REPO/runs/dab/dab-codex-full"   "$OFFICIAL" 0 "C: full 54-query n=5"

uv run python "$REPO/scripts/dab_codex_scoresheet.py" >/dev/null 2>&1 || true
say "ALL PHASES COMPLETE — score sheet at runs/dab/OVERNIGHT_SCORESHEET.md"
