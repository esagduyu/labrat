#!/usr/bin/env python3
"""Generate the overnight GPT-5.5 score sheet from the codex run dirs.

Reads up to three runs and emits a markdown comparison vs. the Sonnet reference:
  - runs/dab/dab-codex-smoke   (subset, verifier OFF)
  - runs/dab/dab-codex-verify  (subset, verifier ON)   -> the verifier ablation
  - runs/dab/dab-codex-full    (full 54-query, verifier OFF)

Robust to partial/in-progress runs: per-dataset rates are computed over real
(non-infra, non-contaminated) trials only, and infra/missing counts are surfaced
so an incomplete run is never silently presented as complete.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "runs" / "dab"

# Sonnet reference — the accepted leaderboard run (claude-sonnet-4-6, claude-mcp,
# pass@5, maintainer-re-validated; PR #54). Stratified mean 51.38%.
SONNET_OFFICIAL: dict[str, float] = {
    "deps_dev_v1": 0.10,
    "github_repos": 0.50,
    "pancancer_atlas": 0.6667,
    "patents": 0.0667,
    "agnews": 0.05,
    "bookreview": 0.80,
    "crmarenapro": 0.8154,
    "googlelocal": 0.50,
    "music_brainz_20k": 0.2667,
    "stockindex": 1.00,
    "stockmarket": 0.80,
    "yelp": 0.60,
}
SUBSET = ["deps_dev_v1", "github_repos", "music_brainz_20k", "stockindex", "stockmarket"]
OFFICIAL = sorted(SONNET_OFFICIAL)


def load_real(run_dir: Path) -> dict[tuple[str, int], dict]:
    """Latest non-infra attempt per (task, trial)."""
    f = run_dir / "trials.jsonl"
    if not f.exists():
        return {}
    real: dict[tuple[str, int], dict] = {}
    for line in f.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        k = (r["task_id"], r["trial_num"])
        reason = r.get("reason") or ""
        if k not in real or (reason and not reason.startswith("infra:")):
            real[k] = r
    return real


def dataset_rates(run_dir: Path) -> tuple[dict[str, float], dict[str, tuple[int, int]], int]:
    """Return per-dataset pass-rate, per-dataset (passes, real_trials), and infra count."""
    real = load_real(run_dir)
    stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    infra = 0
    for r in real.values():
        reason = r.get("reason") or ""
        if reason.startswith(("infra:", "contaminated:")):
            infra += 1
            continue
        ds = r["task_id"].split(":")[0]
        stats[ds][1] += 1
        if r.get("passed"):
            stats[ds][0] += 1
    rates = {ds: (p / t if t else 0.0) for ds, (p, t) in stats.items()}
    counts = {ds: (p, t) for ds, (p, t) in stats.items()}
    return rates, counts, infra


def config(run_dir: Path) -> dict:
    f = run_dir / "config.json"
    return json.loads(f.read_text()) if f.exists() else {}


def pct(x: float | None) -> str:
    return f"{x * 100:.0f}%" if x is not None else "—"


def strat(rates: dict[str, float], keys: list[str]) -> float | None:
    present = [rates[k] for k in keys if k in rates]
    return sum(present) / len(present) if present else None


def n_trials(run_dir: Path) -> int:
    return int(config(run_dir).get("n_trials", 0) or 0)


def main() -> None:
    smoke = RUNS / "dab-codex-smoke"
    verify = RUNS / "dab-codex-verify"
    full = RUNS / "dab-codex-full"

    out: list[str] = []
    out.append("# GPT-5.5 overnight score sheet — DataAgentBench")
    out.append("")
    out.append(
        "All GPT-5.5 runs use the `labrat-agent` driver + `codex` provider "
        "(GPT-5.5 via ChatGPT subscription), reasoning=medium. Sonnet reference is the "
        "accepted leaderboard run (claude-sonnet-4-6, claude-mcp, pass@5, maintainer-"
        "re-validated, **51.38%** stratified). Rates are over real (non-infra) trials only."
    )
    out.append("")

    # ---- verifier ablation on the subset ----
    out.append("## Verifier ablation (subset, n=5)")
    out.append("")
    off_rates, off_counts, off_infra = dataset_rates(smoke)
    on_rates, on_counts, on_infra = dataset_rates(verify)
    out.append("| Dataset | GPT-5.5 verify OFF | GPT-5.5 verify ON | Δ (verifier) | Sonnet |")
    out.append("|---|---|---|---|---|")
    for ds in SUBSET:
        o = off_rates.get(ds)
        v = on_rates.get(ds)
        delta = f"{(v - o) * 100:+.0f}pp" if (o is not None and v is not None) else "—"
        oc = f" ({off_counts[ds][0]}/{off_counts[ds][1]})" if ds in off_counts else ""
        vc = f" ({on_counts[ds][0]}/{on_counts[ds][1]})" if ds in on_counts else ""
        out.append(
            f"| {ds} | {pct(o)}{oc} | {pct(v)}{vc} | {delta} | {pct(SONNET_OFFICIAL.get(ds))} |"
        )
    so = strat(off_rates, SUBSET)
    sv = strat(on_rates, SUBSET)
    ss = strat(SONNET_OFFICIAL, SUBSET)
    vdelta = f"{(sv - so) * 100:+.1f}pp" if (so is not None and sv is not None) else "—"
    out.append(
        f"| **Stratified (subset)** | **{pct(so)}** | **{pct(sv)}** | **{vdelta}** | {pct(ss)} |"
    )
    out.append("")
    out.append(
        f"_Infra-skipped (excluded): verify-off {off_infra}, verify-on {on_infra}. "
        f"n_trials: off={n_trials(smoke)}, on={n_trials(verify)}._"
    )
    out.append("")

    # ---- full 54-query ----
    out.append("## Full 54-query benchmark (GPT-5.5, verifier OFF, n=5)")
    out.append("")
    full_rates, full_counts, full_infra = dataset_rates(full)
    out.append("| Dataset | GPT-5.5 | Sonnet (51.38% run) | Δ |")
    out.append("|---|---|---|---|")
    for ds in OFFICIAL:
        g = full_rates.get(ds)
        s = SONNET_OFFICIAL.get(ds)
        delta = f"{(g - s) * 100:+.0f}pp" if (g is not None and s is not None) else "—"
        gc = f" ({full_counts[ds][0]}/{full_counts[ds][1]})" if ds in full_counts else ""
        flag = "" if ds in full_rates else "  ⏳ not yet run"
        out.append(f"| {ds} | {pct(g)}{gc}{flag} | {pct(s)} | {delta} |")
    gfull = strat(full_rates, OFFICIAL)
    out.append(
        f"| **Stratified mean** | **{pct(gfull)}** | **51.4%** | "
        f"{(gfull - 0.5138) * 100:+.1f}pp" if gfull is not None else "| **Stratified mean** | **—** | **51.4%** | — "
    )
    out[-1] = out[-1] + " |"
    out.append("")
    done_ds = len([d for d in OFFICIAL if d in full_rates])
    out.append(
        f"_Datasets with results: {done_ds}/12. Infra-skipped (excluded): {full_infra}. "
        f"{'⚠️ INCOMPLETE — some datasets pending.' if done_ds < 12 else '✅ all 12 datasets have results.'}_"
    )
    out.append("")

    Path(RUNS / "OVERNIGHT_SCORESHEET.md").write_text("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
