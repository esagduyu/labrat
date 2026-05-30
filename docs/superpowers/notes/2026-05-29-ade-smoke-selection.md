# ADE-bench Smoke Task Selection (2026-05-29)

This file is the audit trail for `ADE_SMOKE_TASK_IDS` in `src/labrat/eval/smoke.py`.
Once `tests/baselines/ade_smoke_baseline.json` is captured (Task 15), this set is **frozen**.
Changing the composition invalidates the baseline.

## Selection criteria

- 3 easy / 3 medium / 3 hard (from `task.yaml::difficulty`)
- Easy tier: one each from `analytics_engineering`, `asana`, `f1` family prefixes — exercises distinct `_FAMILY_HINTS` paths
- Medium tier: diverse table-shape complexity; exercises the agent's exploration tools
- Hard tier: at least one currently-passing, at least one currently-failing per `docs/ade_bench_failure_analysis.md`

## Selected tasks

### Easy
- `analytics_engineering001` — analytics_engineering family. Actual easy task (counts columns in project); trivially simple with no join complexity, ensuring near-zero flakiness risk in the smoke baseline.
- `asana001` — asana family. **Near-miss: no easy asana tasks exist in the benchmark** (see notes below); this is the simplest passing asana task (medium difficulty, 3 tests), exercises the `asana` `_FAMILY_HINTS` injection path.
- `f1004` — f1 family. **Near-miss: no easy f1 tasks exist in the benchmark** (see notes below); "Fix string matching issue" is the simplest passing f1 task (medium difficulty, 3 tests), exercises the `f1` `_FAMILY_HINTS` injection path.

### Medium
- `intercom001` — small schema (~1-2 tables, create aggregation model). Currently passing. No family-specific hint fires (intercom has no entry in `_FAMILY_HINTS`), so this varies on a different axis from the easy tier — exercises the generic agent path through `run_sql` + `describe_table`.
- `f1001` — medium schema (~5-6 staging models, add source model layer). Currently passing. 7 tests including structural checks (`src_models_are_correct`, `stg_models_use_src_models`) — exercises `list_tables` + `describe_table` in the exploration loop.
- `helixops_saas005` — denormalized output / FK lineage tracing (fix broken intermediate model by tracing a missing column back through staging → intermediate layers). Currently passing. Exercises the helixops `_FAMILY_HINTS` path and the `search_columns` tool for upstream propagation.

### Hard
- `helixops_saas016` — currently passing. Three-way join with a seed file, date-range validity logic, and enterprise SLA override — complex business logic that exercises the helixops hint path and requires the agent to create and join a new seed. A stable passing task ensures the smoke set has at least one reliable green signal at the hard tier.
- `analytics_engineering006` — currently failing (failure mode: three simultaneous errors across three new models — supplier FK join not discovered (G2), date format not parsed (G4), no dedup ROW_NUMBER (G1); fails 5/8 tests consistently). Selected because the failure mode is completely understood and stable — it will remain red until Tier 2 tools are shipped, giving the smoke baseline a reliable red signal that any regression (i.e. accidental improvement or breakage) will be visible.
- `airbnb013` — currently passing but stochastic. "Fix incremental model that silently drops reviews at the high-water mark boundary." No airbnb-specific family hints fire, so this exercises the generic debugging path. Boundary-condition tests introduce natural pass-rate variance — ideal for capturing the stochastic envelope in the baseline.

## Enumeration evidence

Full eligible task list (status=ready, duckdb+dbt variant present), sorted by difficulty:

```
easy    airbnb001
easy    airbnb003
easy    airbnb008
easy    analytics_engineering001   ← SELECTED
easy    analytics_engineering002
easy    analytics_engineering003
easy    analytics_engineering005
easy    analytics_engineering008
easy    helixops_saas001
easy    helixops_saas002
easy    helixops_saas004
easy    helixops_saas009
easy    helixops_saas010           (FAILING — excluded)
easy    helixops_saas017
easy    quickbooks001
hard    airbnb007
hard    airbnb010
hard    airbnb011
hard    airbnb012
hard    airbnb013                  ← SELECTED (passing, stochastic)
hard    analytics_engineering006   ← SELECTED (failing, stable)
hard    asana003
hard    f1002                      (FAILING — excluded)
hard    f1006
hard    helixops_saas008
hard    helixops_saas016           ← SELECTED (passing)
hard    helixops_saas018
hard    intercom002                (FAILING — excluded)
hard    intercom003
hard    quickbooks003              (FAILING — excluded)
medium  airbnb002
medium  airbnb004
medium  airbnb005
medium  airbnb006
medium  airbnb009
medium  analytics_engineering004   (FAILING — excluded)
medium  analytics_engineering007   (FAILING — excluded)
medium  asana001                   ← SELECTED (near-miss for easy/asana)
medium  asana002
medium  asana004                   (FAILING — excluded)
medium  asana005                   (FAILING — excluded)
medium  f1001                      ← SELECTED
medium  f1003
medium  f1004                      ← SELECTED (near-miss for easy/f1)
medium  f1005
medium  f1007
medium  f1009
medium  f1010
medium  f1011
medium  helixops_saas003
medium  helixops_saas005           ← SELECTED
medium  helixops_saas006
medium  helixops_saas007           (FAILING — excluded)
medium  helixops_saas011
medium  helixops_saas012
medium  helixops_saas013
medium  helixops_saas015           (FAILING — excluded)
medium  intercom001                ← SELECTED
medium  quickbooks002
medium  quickbooks004
```

## Notes

### Near-miss for easy/asana and easy/f1

The `asana` family has no easy-difficulty tasks in the benchmark (all 5 asana tasks are medium or hard). The `f1` family similarly has no easy-difficulty tasks (all 11 f1 tasks are medium or hard). The selection criteria requires one each from `analytics_engineering`, `asana`, `f1` to exercise three distinct `_FAMILY_HINTS` code paths — which is the real constraint, not difficulty level per se.

Resolution per the plan's guidance: `asana001` and `f1004` are the simplest currently-passing tasks in their respective families and are used as near-misses. The "easy" tier of the smoke set is therefore:
- 1 actual easy task (`analytics_engineering001`)
- 2 medium tasks selected for family-hint coverage (`asana001`, `f1004`)

This means the easy tier will run slightly longer and may show lower pass rates than a true easy baseline. The notes file records this so future maintainers understand the composition.

### Hard tier stochastic envelope

`airbnb013` (incremental model boundary condition) has no airbnb-specific `_FAMILY_HINTS` entry, exercises generic debugging, and the boundary-condition nature of the test means pass rate may be ~70-90% rather than 100%. This is intentional: Task 15's baseline capture (3 runs × 3 attempts) will measure that envelope so regressions are distinguishable from natural variance.

### Currently-failing hard task rationale

`analytics_engineering006` was chosen over other failing hard tasks (`f1002`, `intercom002`, `quickbooks003`) because its failure mode is completely characterised (three simultaneous G1/G2/G4 gaps described in `docs/ade_bench_failure_analysis.md`) and it fails at a consistent 5/8 tests. The other failing hard tasks either have unknown root causes (intercom002, quickbooks003) or have a partially-understood failure (f1002). A well-understood failure is a better regression sentinel — if the failure pattern changes unexpectedly, it's a signal, not noise.

### Task IDs copied into `src/labrat/eval/smoke.py::ADE_SMOKE_TASK_IDS` (Task 14)

```python
ADE_SMOKE_TASK_IDS = [
    # Easy tier (analytics_engineering family)
    "analytics_engineering001",
    # Near-miss easy: asana family (no easy asana tasks exist)
    "asana001",
    # Near-miss easy: f1 family (no easy f1 tasks exist)
    "f1004",
    # Medium tier
    "intercom001",   # small schema, generic agent path
    "f1001",         # medium schema, structural exploration
    "helixops_saas005",  # FK lineage tracing
    # Hard tier
    "helixops_saas016",       # currently passing
    "analytics_engineering006",  # currently failing (stable, understood)
    "airbnb013",              # currently passing, stochastic envelope
]
```

Baseline capture (Task 15): 3 runs × 3 attempts = 9 attempts per task, 81 total trials (~30–60 min wall time).
