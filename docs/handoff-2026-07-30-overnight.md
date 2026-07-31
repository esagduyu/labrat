# Overnight handoff — 2026-07-30

Written for review on waking. Everything below is committed; nothing is merged to
`master`. Three worktrees are in play (see **Worktrees** at the bottom).

---

## TL;DR

| item | status |
|---|---|
| `trials.jsonl` orphaning defect | **FIXED + verified live** (`3b1199d`) |
| Vacuous taint gate | **FIXED** — gate now fails closed (`3b1199d`) |
| Lever 3 (convention-pinning) | **DROPPED** — 9 levers now (`3b1199d`) |
| Opus 5 scout, n=2/high, 108 trials | **RUNNING** on a frozen worktree |
| Item 1 — deterministic answer gate | **BUILT + tested**, default OFF (`34160a5`) |
| Item 2 — deterministic helpers | **NOT built** — deliberate, see *Judgement calls* |
| Ablation of item 1 | **NOT run** — blocked on Max-plan contention, see below |
| Full Opus 5 run | **NOT launched** — see *Why I did not launch it* |

---

## 1. The defect, root cause, and what I actually did

**Symptom.** `trials.jsonl` ended 0 bytes on most shards of the 2026-07-29 campaign
(10/12 dilution, 12/12 xhigh, 2/12 medium) with mtime = shard start and perms `600`
instead of `644`, while `report.md` / `submission.json` / traces were all correct.

**Root cause: NOT fully established.** `_run_interim` held one append handle for a whole
shard and the file at that path was evidently replaced mid-run, orphaning the handle —
but I could not find the replacing writer. There is no `os.replace`, `os.rename`,
`mkstemp` or `NamedTemporaryFile` anywhere in our write path, which kills the
atomic-write hypothesis. **This is an open question worth a proper look when there is
time**; the incidence varying by arm (2/12 vs 12/12) suggests something environmental.

**What I did instead of chasing it further at 9pm:** made the write immune to that class
of failure regardless of cause. `_append_trial_row` re-opens the path per row and
fsyncs; `_verify_trials_file` rewrites from the in-memory list if the on-disk count still
falls short. Verified live: the Opus scout's first shard wrote 6 rows at `644` with real
taint verdicts.

**The serious half — the taint gate was passing vacuously.** `audit_run` derives verdicts
by iterating `trials.jsonl`, so an orphaned file produced `{}`, and `gate({})` returned
*pass, no offenders*. Every affected shard had its contamination gate screen **zero
trials and report clean**, and `submission.json` was written anyway. Given the 58.0%
answer-key retraction, that is the real bug here, not the data loss. `gate()` now fails
closed on an empty verdict set.

> **Gotcha:** the fail-closed check is applied at the call site *only when the run
> produced trials*. An empty `--dab-dir` legitimately runs zero trials and must still
> exit 0 — existing tests caught this, which is why the check is scoped rather than
> unconditional.

---

## 2. Lever 3 dropped

Removed the convention-pinning lever (9 levers now). Rationale, from the 162-trial
dilution run:

- It never moved its only target: `patents:2` was 0/5 at baseline, **0/3** with the
  lever, and 0/3 in the earlier smoke.
- Both saturated-task regressions were convention-flavoured. `github_repos:3` pinned
  "Shell as *primary* language" and answered **0** where passing runs read "Shell in the
  language mix" (1077); `yelp:1` locked a wrong averaging basis (3.50 vs 3.55). A zero
  result is exactly the sanity signal that should force a rethink, and the lever told the
  model to hold its convention absent such a signal.
- Every measured point of the levers' gain traces to the free-text and byte-verbatim
  lines, so dropping it costs nothing measurable.

---

## 3. Opus 5 scout — RUNNING

- Worktree: `/Users/ege/repos/labrat-wt-opusscout` (**detached HEAD at `3b1199d`** so it
  cannot drift while feature work continues elsewhere).
- Config: `claude-opus-5`, high effort, n=2, all 12 official datasets, 108 trials,
  cartograph + hints + 9 levers + local-embed classify + mcp ledger, `--agent-timeout 1800`.
- Log: `/Users/ege/repos/labrat-wt-opusscout/opus-scout.log`
- Progress: `grep -cE '^\[[a-z_0-9]+:[0-9]+ trial' <log>` → n/108. First shard took ~7 min.

**What it decides.** Three Sonnet arms at identical features all landed ~74% (medium
.7410 / high .7435 / xhigh .7322). If Opus lands ~74%, the gap is the **harness** and the
deterministic layer is the priority. If it lands ~80%, the gap was the **model**.

> **Gotcha:** n=2 not n=3 on purpose. Opus burns Max-plan budget ~5× Sonnet; 108 trials
> places the result in a 74/78/82 band, which is all this arm has to decide, while
> leaving weekly budget for the full run it informs.

---

## 4. Item 1 — deterministic answer gate (built, default OFF)

Branch `feat/dab-deterministic-gate`, commit `34160a5`, flag `--agent-answer-gate`.

`src/labrat/eval/benchmarks/dab/answer_gate.py` checks the final answer for delivery
failures we have *measured* costing otherwise-correct trials, and on a violation the
driver makes **one presentation-only corrective pass**. Checks: requested-count
shortfall, a name/value list delivered as a markdown table, and a fraction answered only
as a percentage.

**Why deterministic rather than another lever:** three prose levers aimed at delivery
shape have now been measured and prose keeps losing — one task passed and failed on
nothing but a reformatted time range, and the adjacent-token lever shipped and did not
prevent it. Code cannot forget on trial 3 of 5.

**Safety by construction.** The corrective call passes no `--mcp-config` and blocks every
native tool, so it cannot reach a database, the filesystem or the network — it can only
restate what the model already produced, opening no new evidence path. It is bounded to
one attempt and falls back to the original answer on any failure, so the gate can never
destroy a good answer.

**Untuned discipline is enforced by tests.** The module may not name a benchmark dataset,
and may not hard-code a grader window reverse-engineered from the scorer. (A competitor
derived `K_ADJ=10` from a DAB validator's `llm_lower[idx:idx+10]`; we keep adjacency as a
structural "don't bury the value in a table column" rule instead. That is a **decision
for you to revisit** — it is rubric-legal and it got them rank 3, but it is scorer-fitting
and I did not think it was mine to take unilaterally.)

---

## 5. Judgement calls I made

**Item 2 (deterministic helpers: EMA/windowing, join cardinality, word-boundary text
extraction) — deliberately NOT built.** Reasons: (a) it cannot be ablated tonight anyway
because the Opus scout owns the Max-plan session, so it would ship unmeasured; (b) the
scout result may reprioritise the whole deterministic track — if Opus alone reaches ~80%
this work matters much less; (c) a half-built tool surface at 1am is worse than a clear
spec. **Scope for the morning:** an `ema` helper is the highest-value single piece — it
targets `patents:2`, the one task no prompt lever has ever moved, by *computing* the
convention instead of asking the model to hold one.

**Ablation not run — this is the one place I could not follow the plan.** Item 1's
ablation needs Max-plan budget, and the Opus scout is using it. Running both concurrently
risks exit-4 walls corrupting both. Sequential is the only safe option.

**Why I did not launch the full Opus run.** You said to launch if confident, and I am
not, for a specific reason: the whole point of the scout is to choose the config for the
full run, and launching the full run before reading it would spend the expensive budget
on an unvalidated configuration. There is also an open question the scout answers — if
Opus lands ~74%, the right full run is *with* the deterministic layer, which does not
exist yet. Launching now would likely mean running it twice.

---

## 6. Suggested order when you wake

1. Read the scout: `uv run python` over the log with the parser in
   `scratchpad/from_log.py` (validated against known-good shards), or just
   `runs/opus-scout-shards/*/trials.jsonl` now that writes are fixed.
2. Decide the branch point:
   - **Opus ≈ 80%** → skip most of the deterministic work; run the full n=5 Opus arm.
   - **Opus ≈ 74%** → the gap is the harness; ablate item 1, build item 2, then run full.
3. Ablate `--agent-answer-gate` (8-task smoke, n=3, ~1h) before it goes near a submission.
4. Sync the DataAgentBench checkout — ours is from 07-16 and the board has moved (we are
   rank 8; 30 entries; two new Sarvam entries at 0.8208 / 0.7812). I did **not** touch it.
5. Open question worth reviving: the actual root cause of the `trials.jsonl` replacement.

---

## Worktrees

| path | branch | purpose |
|---|---|---|
| `/Users/ege/repos/labrat` | `feat/dab-harness-integrity` | defect fix + lever drop (`3b1199d`) |
| `/Users/ege/repos/labrat-wt-opusscout` | detached at `3b1199d` | **frozen** — Opus scout running here |
| `/Users/ege/repos/labrat-wt-detgate` | `feat/dab-deterministic-gate` | item 1 (`34160a5`) |
| `/Users/ege/repos/labrat-wt-ledger` | `feat/dab-inprocess-ledger-budget` | earlier ledger work, still unvalidated |

> **Gotcha:** do not edit the scout worktree while it runs — `eval_dab.py` is re-invoked
> per dataset shard, so a mid-run edit silently changes the code under later shards.
> That is why the scout is on a detached HEAD.
