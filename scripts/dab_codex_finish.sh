#!/usr/bin/env bash
# Scoped GPT-5.5 follow-up (after the overnight full-run attempt hit the codex
# rate limit). Per the user's call: finish the subset, then the verifier ablation
# — no full 54-query run. Two phases, each driven by the self-healing watchdog
# (probe → resume → retry infra), so it sits idle on a 429 and resumes when the
# rate limit clears:
#
#   A  finish subset (5 DuckDB+SQLite) n=5 verify OFF -> runs/dab/dab-codex-smoke  (72/85 done; needs stockmarket's last 13)
#   B  subset n=5 verify ON            -> runs/dab/dab-codex-verify  (the verifier ablation: A vs B)
#
# Launch detached:
#   cd /Users/ege/repos/labrat
#   nohup bash scripts/dab_codex_finish.sh >> runs/dab/finish.log 2>&1 &
#   tail -f runs/dab/finish.log
# Score sheet: runs/dab/OVERNIGHT_SCORESHEET.md (refreshed each tick).
set -uo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
REPO="/Users/ege/repos/labrat"
cd "$REPO" || { echo "[finish] cannot cd to $REPO"; exit 1; }

SUBSET="deps_dev_v1,github_repos,music_brainz_20k,stockindex,stockmarket"
export SCORESHEET_AFTER=1   # keep OVERNIGHT_SCORESHEET.md fresh each tick

say() { echo "[finish] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

run_phase() {  # $1=run_dir $2=verify $3=label
  say "=== PHASE START: $3 (dir=$(basename "$1"), verify=$2) ==="
  RUN_DIR="$1" DATASETS="$SUBSET" NTRIALS=5 VERIFY="$2" REASONING=medium \
    bash "$REPO/scripts/dab_codex_loop.sh"
  say "=== PHASE DONE: $3 ==="
  uv run python "$REPO/scripts/dab_codex_scoresheet.py" >/dev/null 2>&1 || true
}

say "START — finish subset, then verifier ablation (no full run). Self-heals on rate-limit reset."
run_phase "$REPO/runs/dab/dab-codex-smoke"  0 "A: finish subset n=5 (verify off)"
run_phase "$REPO/runs/dab/dab-codex-verify" 1 "B: subset verifier-on n=5 (ablation)"
uv run python "$REPO/scripts/dab_codex_scoresheet.py" >/dev/null 2>&1 || true
say "ALL DONE — score sheet at runs/dab/OVERNIGHT_SCORESHEET.md"
