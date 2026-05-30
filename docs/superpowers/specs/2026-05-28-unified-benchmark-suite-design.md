# Unified Benchmark Suite Design

**Status:** Draft (2026-05-28)
**Branch:** `feat/dab-integration` (co-resident with the DAB spec; both ship together)
**Author:** Ege (via Claude brainstorm)
**Related:** [`2026-05-27-dab-bench-integration-design.md`](2026-05-27-dab-bench-integration-design.md)

## Goals

1. **One harness for three benchmarks.** DAB (in-process, text-answer scoring), Spider2-DBT (in-process, DuckDB table-match scoring), and ADE-bench (external CLI, container pytest scoring) all expose the same surface to LabRat's eval machinery.
2. **Pay-as-you-go generalization.** Build only the abstractions that have a second consumer. Single-use code stays inline. The orchestrator and shared driver are extracted when Spider2 lands, not before.
3. **No DAB delay.** DAB Phase 1 lands on the new file layout from day one, but does not block on shared infrastructure. The shared infrastructure arrives when Spider2 needs it.
4. **No ADE regression.** The ADE-bench port is a file move, not a logic change. Smoke regression baseline gets captured on the new shape so it tracks the actual code path going forward.
5. **Spider2 slots in as a thin addition.** When Spider2-DBT integration starts (post-DAB), the work is a new directory under `eval/benchmarks/spider2_dbt/` plus the orchestrator + driver extraction — not a re-architecture.

## Working Agreement

- Lives on the existing `feat/dab-integration` branch alongside the DAB spec. Both are co-dependent and ship together.
- This document is a **contract**: material design changes require updating it before code changes. Protocol shape changes during DAB Phase 1a are allowed (the spec is the contract until it isn't), but require a doc patch in the same commit as the code change.
- Spider2-DBT is a **design constraint**, not a near-term build. Its presence in this spec ensures the protocol doesn't paint into a corner; the actual implementation is deferred to a follow-on spec post-DAB.
- ADE-bench coupling is **hybrid**: adapter today (external `ade` CLI), with a documented option to deepen in a future phase. No commitment to ever do so.

## Background

LabRat today integrates with ADE-bench through `eval/suites/ade_bench.py` (task enumeration) and `eval/runners/ade_bench_runner.py` (shells out to the external `ade` CLI). The agent code itself lives at `~/repos/ade-bench/.../labrat_local_agent.py`. The DAB integration (spec'd 2026-05-27) introduces a second benchmark with materially different shape: multi-DB connections, text-answer scoring, per-query validators. Spider2-DBT (paused, dataset at `~/repos/Spider2/spider2-dbt/`) is a likely third benchmark with a third shape: local DuckDB + dbt project completion + table-match scoring.

The current `EvalCase` / `EvalRunner` shape is SQL-flavored: `EvalCase.expected_sql`, `EvalRunner._sql_matches`. ADE-bench, DAB, and Spider2 all stretch this shape rather than fit it. The DAB spec as originally written would add a third one-off (`DabSuite` + `DabRunner` + `DabValidator`) without consolidating any shared concerns.

This spec defines a small protocol that all three benchmarks can implement honestly, with shared concurrency / resumability / reporting machinery extracted only when there's a second consumer.

## Architecture

### Core abstractions

Five types and one protocol, all in `src/labrat/eval/types.py`:

```python
class BenchmarkTask(BaseModel):
    """A single benchmark task. Superset of the legacy EvalCase."""
    model_config = ConfigDict(frozen=True)

    id: str
    benchmark: str                   # "dab" | "ade_bench" | "spider2_dbt"
    prompt: str
    difficulty: str | None = None    # "easy" | "medium" | "hard" | None
    tags: list[str] = []
    config: dict[str, Any] = {}      # opaque per-benchmark blob

class TrialResult(BaseModel):
    task_id: str
    trial_num: int                   # 0..n-1 (for pass@k)
    passed: bool
    reason: str | None = None        # short failure tag if any
    latency_seconds: float
    tool_calls: int = 0
    cost_usd: float = 0.0
    artifact: dict[str, Any] = {}    # {"type": "text|files|container_state|duckdb_state", "payload": ...}
    meta: dict[str, Any] = {}

class AggregateScore(BaseModel):
    overall: float                   # primary score, 0..1
    per_task: dict[str, float]       # task_id → pass-rate across trials
    by_dimension: dict[str, dict[str, float]] = {}
    # e.g. {"difficulty": {...}} for ADE, {"dataset": {...}} for DAB stratified
    n_tasks: int
    n_trials: int
    n_passes: int

class BenchmarkReport(BaseModel):
    benchmark: str
    run_id: str
    score: AggregateScore
    trials: list[TrialResult]
    config: dict[str, Any]

    def to_markdown(self) -> str: ...

class BenchmarkSuite(Protocol):
    """The contract every benchmark integration implements."""
    name: str
    def tasks(self) -> Iterable[BenchmarkTask]: ...
    async def run_trial(self, task: BenchmarkTask, trial_num: int, scratch_dir: Path) -> TrialResult: ...
    def aggregate(self, results: list[TrialResult]) -> AggregateScore: ...
    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None: ...  # default no-op
```

`BenchmarkSuite.run_trial` is the only granularity decision worth flagging: setup + agent invocation + scoring all happen inside one async call. This keeps benchmark-specific state confined to one function (no awkward setup/teardown lifecycle on the protocol surface) and gives the orchestrator one thing to schedule. Per-benchmark code is free to split this into helpers internally.

`TrialResult.artifact` is a typed dict rather than a sealed union — DAB produces `{"type": "text", ...}`, ADE produces `{"type": "container_state", ...}`, Spider2 will produce `{"type": "duckdb_state", ...}`. New artifact types add without protocol changes.

### Shared in-process agent driver

```python
class AgentRunResult(BaseModel):
    final_text: str
    tool_calls: int
    usage: dict[str, int]
    cost_usd: float
    runtime_seconds: float
    files_produced: list[Path] = []
    tool_call_log: list[dict[str, Any]] = []

class LabRatAgentDriver:
    """Shared runtime: takes a configured ToolContext + prompt, returns the agent's output."""
    def __init__(self, provider: BaseProvider, tool_registry: ToolRegistry, max_turns: int = 100): ...
    async def run(self, ctx: ToolContext, prompt: str, system_prompt: str | None = None) -> AgentRunResult: ...
```

Used by DAB and Spider2 (both run the agent in-process). ADE bypasses the driver entirely — its agent lives in `~/repos/ade-bench` and gets invoked via subprocess.

The driver lives at `src/labrat/eval/driver.py` and is **not built during DAB Phase 1**. DAB Phase 1 writes its agent-loop invocation inline. Extraction happens during Phase 4 (Spider2 onboarding), when there's a second consumer to share with.

### Orchestrator

```python
class BenchmarkOrchestrator:
    """One implementation, used by every benchmark."""
    def __init__(
        self,
        suite: BenchmarkSuite,
        run_id: str | None = None,
        n_trials: int = 1,           # pass@k semantics
        n_concurrent: int = 3,
        output_dir: Path = Path("runs"),
        resume: bool = True,
    ): ...
    async def run(self) -> BenchmarkReport: ...
    # Handles: scratch dirs, jsonl streaming, resumability, asyncio scheduling, report rendering
```

Responsibilities:
- Create `runs/<benchmark>/<run_id>/` and write `config.json`
- If `resume=True`, read existing `trials.jsonl`, build `(task_id, trial_num)` skip set
- Enumerate `(task, trial_num)` pairs minus skip set
- Schedule with `asyncio.Semaphore(n_concurrent)`
- Per pair: allocate `scratch/<task_id>__trial<n>/`, call `suite.run_trial`, append `TrialResult` to `trials.jsonl`
- After all complete: `suite.aggregate(results)`, render `report.md`, call `suite.write_submission`

Like the driver, the orchestrator is **not built during DAB Phase 1**. DAB Phase 1 implements the orchestrator's behavior inline in its interim runner. The contract: that inline code must not contain DAB-specific logic — it's the orchestrator-in-waiting. Extraction happens during Phase 4.

### Standard run output structure

Same for every benchmark:

```
runs/<benchmark>/<run_id>/
├── config.json                         # suite config, model, seed, concurrency
├── trials.jsonl                        # streaming, append-only, resume-source
├── submission.json                     # if suite.write_submission is non-default
├── report.md                           # rendered AggregateScore + failure summary
└── scratch/<task_id>__trial<n>/        # per-trial working dir (preserved on failure)
```

This shape mirrors `~/repos/ade-bench/experiments/<run_id>/` deliberately, so existing failure-analysis tools (`scripts/analyze_ade_failures.py`) can be adapted with minimal rework.

## Module Layout

```
src/labrat/eval/
├── types.py                            # NEW (DAB Phase 1) — BenchmarkTask, TrialResult, AggregateScore, BenchmarkReport, BenchmarkSuite
├── orchestrator.py                     # NEW (Phase 4) — BenchmarkOrchestrator
├── driver.py                           # NEW (Phase 4) — LabRatAgentDriver, AgentRunResult
├── reporting.py                        # NEW (DAB Phase 1) — markdown rendering helpers
├── smoke.py                            # NEW (DAB Phase 1) — SubsetSuite + ade_smoke_suite()
│
├── benchmarks/
│   ├── __init__.py
│   ├── dab/                            # NEW (DAB Phase 1)
│   │   ├── suite.py                    # DabSuite
│   │   ├── env.py                      # multi-DB ToolContext factory
│   │   ├── scorer.py                   # wraps DAB per-query validate.py
│   │   └── reporter.py                 # submission.json writer
│   ├── ade_bench/                      # NEW (DAB Phase 1, port from legacy paths)
│   │   ├── suite.py                    # AdeBenchSuite
│   │   ├── external_runner.py          # shells to `ade` CLI
│   │   └── reporter.py                 # parses experiments/<run_id>/ into TrialResults
│   └── spider2_dbt/                    # stubbed only — implementation deferred
│       └── README.md                   # design notes referencing this spec
│
└── (legacy — kept until migrated)
    ├── models.py                       # EvalCase
    ├── runner.py                       # EvalRunner
    ├── report.py                       # EvalReport
    ├── baselines/
    ├── suites/
    │   ├── ade_bench.py                # DELETE after benchmarks/ade_bench/ lands
    │   ├── bird.py                     # stays — internal eval, not benchmark integration
    │   ├── custom_scenarios.py         # stays — same
    │   └── latency.py                  # stays — same
    └── runners/
        └── ade_bench_runner.py         # DELETE after benchmarks/ade_bench/ lands
```

**Why `bird.py` / `latency.py` / `custom_scenarios.py` stay on the legacy shape:** they're not benchmark integrations. They're internal LabRat evals for SQL-correctness and latency profiling, using local fixtures. They have no shared concerns with DAB/ADE/Spider2 (no concurrency, no resumability, no external scoring). Forcing them onto `BenchmarkSuite` would be ceremony with no payoff.

**Top-level scripts (unchanged shape):**
- `scripts/eval_dab.py` — entrypoint: build `DabSuite`, hand to interim runner (DAB Phase 1) or `BenchmarkOrchestrator` (Phase 4+)
- `scripts/eval_ade_bench.py` — rewired to use the new `AdeBenchSuite` during DAB Phase 1 port
- `scripts/eval_spider2_dbt.py` — Phase 4 (Spider2 spec)

## Per-Benchmark Implementations

### DAB (`benchmarks/dab/`)

Per the DAB spec (with file paths updated per Section "DAB Spec Adjustments" below).

- `DabSuite.tasks()` walks `<dab_dir>/query_*/query*/`, builds `BenchmarkTask` with `config={"db_config_path", "validator_path", "dataset", "db_description_path"}`.
- `DabSuite.run_trial()` builds a multi-DB `ToolContext` from `db_config_path`, invokes the agent inline (Phase 1) or through `LabRatAgentDriver` (Phase 4+), captures final text, scores via `benchmarks/dab/scorer.py` (imports per-query `validate.py`).
- `DabSuite.aggregate()` returns stratified score: each of 12 datasets weighted equally regardless of query count; `by_dimension["dataset"]` populated.
- `DabSuite.write_submission()` writes DAB-format `submission.json`.

### ADE-bench (`benchmarks/ade_bench/`)

Port of the existing `eval/suites/ade_bench.py` + `eval/runners/ade_bench_runner.py`. Same logic, new file paths, implements `BenchmarkSuite`.

- `AdeBenchSuite.tasks()` reads `~/repos/ade-bench/tasks/*/task.yaml`, filters to duckdb+dbt variants with `status: ready`, builds `BenchmarkTask` with `difficulty` from `task.yaml`.
- `AdeBenchSuite.run_trial()` invokes `external_runner.run_one(task_id, trial_num, scratch_dir)`, which shells `ade run <task_id> --agent labrat_local --n-attempts 1 ...` and parses the resulting `experiments/<exp_id>/results_metadata.jsonl` into a `TrialResult`. `artifact = {"type": "container_state", "payload": {"experiment_dir": "..."}}`.
- `AdeBenchSuite.aggregate()` returns flat mean per-task pass-rate; `by_dimension["difficulty"]` populated.
- `AdeBenchSuite.write_submission()` no-op (ADE has no submission format).

**Critical port acceptance test:** before deleting the legacy paths, run `scripts/eval_ade_bench.py --tasks <one_easy>` on both shapes and confirm identical `EvalReport` / `BenchmarkReport` content (task pass count, failure modes, runtimes within noise). Smoke baseline gets captured on the new shape only.

### Spider2-DBT (`benchmarks/spider2_dbt/`)

Stubbed only. The README captures design intent so the protocol decisions made here don't get forgotten:

- Tasks come from `~/repos/Spider2/spider2-dbt/examples/spider2-dbt.jsonl` (67 entries).
- `Spider2DbtSuite.run_trial()` will copy the dbt project to `scratch_dir`, build a `ToolContext` over the starter DuckDB, invoke the agent via `LabRatAgentDriver`, then table-match the resulting DuckDB state against `evaluation_suite/gold/<task_id>/<db>.duckdb` using `duckdb_match` / `tables_match` (ported from Spider2's `evaluate.py`).
- `artifact = {"type": "duckdb_state", "payload": {"db_path": "..."}}`.
- Dataset triage (Fivetran `_tmp` unsolvability, allowlist for "fair score") is a Spider2-spec concern, not architectural.

This entry exists so anyone reading the spec sees Spider2 as a designed-for case, not an afterthought.

## Smoke Regression

Smoke regression sheds the originally-proposed `eval/suites/ade_smoke.py` in favor of pure composition.

```python
# src/labrat/eval/smoke.py

class SubsetSuite:
    """Generic wrapper: expose only a fixed subset of a parent suite's tasks."""
    def __init__(self, parent: BenchmarkSuite, task_ids: list[str], name: str | None = None):
        self._parent = parent
        self._task_ids = set(task_ids)
        self.name = name or f"{parent.name}-subset"
    def tasks(self) -> Iterable[BenchmarkTask]:
        return [t for t in self._parent.tasks() if t.id in self._task_ids]
    async def run_trial(self, task, trial_num, scratch_dir):
        return await self._parent.run_trial(task, trial_num, scratch_dir)
    def aggregate(self, results):
        return self._parent.aggregate(results)
    def write_submission(self, report, output_dir):
        pass

ADE_SMOKE_TASK_IDS: list[str] = [
    # 3 easy / 3 medium / 3 hard — populated in DAB Phase 0 after baseline review
]

def ade_smoke_suite() -> BenchmarkSuite:
    return SubsetSuite(AdeBenchSuite(), ADE_SMOKE_TASK_IDS, name="ade-smoke")
```

- Baseline file: `tests/baselines/ade_smoke_baseline.json` (per-task pass counts captured on master across 3 runs).
- Regression check: `scripts/run_smoke_regression.py` runs `ade_smoke_suite()` through the interim runner (Phase 1) or `BenchmarkOrchestrator` (Phase 4+), compares `AggregateScore.per_task` against baseline, exits non-zero on hard fail.
- Run cadence: at every DAB phase exit (1a, 1b, 1c).
- `SubsetSuite` generalizes — Spider2 can define its own smoke set the same way when it lands.

Hard fail / soft signal thresholds are unchanged from the DAB spec.

## DAB Spec Adjustments

The DAB spec (`2026-05-27-dab-bench-integration-design.md`) gets these targeted edits, applied in the same commit as this spec:

**Architecture / Module layout section, file-path replacements:**

| DAB spec (current) | Replace with |
|---|---|
| `src/labrat/eval/suites/dab.py` | `src/labrat/eval/benchmarks/dab/suite.py` |
| `src/labrat/eval/suites/ade_smoke.py` | `src/labrat/eval/smoke.py` |
| `src/labrat/eval/runners/dab_runner.py` | DAB-interim runner code colocated with `scripts/eval_dab.py` (Phase 1); migrates to `eval/orchestrator.py` (Phase 4) |
| `src/labrat/eval/validators/dab.py` | `src/labrat/eval/benchmarks/dab/scorer.py` |

**New DAB Phase 1a responsibilities (added to the existing phase plan, not replacing it):**

1. Create `src/labrat/eval/types.py` with `BenchmarkTask`, `TrialResult`, `AggregateScore`, `BenchmarkReport`, `BenchmarkSuite` protocol.
2. Port existing `eval/suites/ade_bench.py` + `eval/runners/ade_bench_runner.py` to `eval/benchmarks/ade_bench/`. Run the port-acceptance test (above). Delete legacy paths after passing.
3. Create `src/labrat/eval/smoke.py` with `SubsetSuite` + `ade_smoke_suite()`. Populate `ADE_SMOKE_TASK_IDS` per DAB Phase 0 selection.
4. Capture `tests/baselines/ade_smoke_baseline.json` on the new shape (not the legacy one).
5. Create `src/labrat/eval/benchmarks/spider2_dbt/README.md` referencing this spec.

**Shared infra explicitly NOT built during DAB Phase 1:**

- `BenchmarkOrchestrator` — DAB's interim runner does its own concurrency + jsonl + resumability. Extracted in Phase 4 (Spider2 onboarding).
- `LabRatAgentDriver` — DAB's `run_trial` invokes the agent inline. Extracted in Phase 4.

The DAB spec's existing Phase 1a/1b/1c structure is otherwise unchanged.

## Phasing

| Phase | Trigger | Scope | Build |
|---|---|---|---|
| Phase 0 (this spec) | Now | Architectural commitment | Write this spec; patch DAB spec to point at new file layout. No code. |
| Phase 1 (= DAB Phase 1a) | DAB build starts | New file layout in place | `eval/types.py`. Port `AdeBenchSuite` to `eval/benchmarks/ade_bench/`. `eval/smoke.py` with `SubsetSuite` + `ade_smoke_suite()`. `tests/baselines/ade_smoke_baseline.json`. Spider2 stub README. |
| Phase 2 (= DAB Phase 1b) | DAB needs runner | DAB interim runner | DAB builds concurrency / jsonl / resumability inline. No DAB-specific logic in the runner layer. |
| Phase 3 (= DAB Phase 1c ships) | DAB merged to master | DAB at 55%+ score | Spec exit met. Branch merges. |
| Phase 4 (Spider2 starts) | Post-DAB | Extract shared infra | Build `BenchmarkOrchestrator` + `LabRatAgentDriver`. DAB's interim runner deletes; DAB calls orchestrator. Spider2 lands on shared infra. |
| Phase 5 (ADE deep-coupling) | TBD, post-Spider2 | Optional re-integration | Pull `LabratLocalAgent` from `~/repos/ade-bench` into LabRat; ADE runs through `LabRatAgentDriver`. Adapter retires. May never happen. |
| Phase 6 (legacy cleanup) | After Spider2 + (maybe) ADE migrations | Delete dead code | Remove `eval/models.py` / `runner.py` / `report.py` once nothing in benchmarks/ imports them. Internal evals stay on legacy shape. |

**Critical commitment:** Phase 4 is where the architecture earns its keep. If Spider2 never happens, the unified design has paid only its Phase 1 cost (~2 days of porting AdeBenchSuite + adding `types.py`) and shipped DAB. The protocol exists; the orchestrator and driver don't. Extraction can happen later or never.

**Risk if Phase 1 reveals the protocol is wrong-shaped:** the spec is the contract until it isn't. Protocol revisions during DAB Phase 1a are allowed but require a doc patch in the same commit as the code change. Revision after Phase 1a (once the interim runner has accumulated assumptions) is much costlier — that's the boundary to watch.

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `BenchmarkSuite.run_trial` granularity wrong-shaped once DAB hits real code | Medium | Protocol revision allowed during DAB Phase 1a before interim-runner accumulates assumptions. Revisions later require Phase 4 timing. |
| ADE adapter port during Phase 1 breaks the existing ADE-bench flow | Low | Port-acceptance test (run one easy task on both shapes; results match). Smoke baseline captured on new shape only. |
| Phase 4 extraction reveals DAB's interim runner picked up DAB-isms | Medium | Keep DAB's interim runner narrow — scheduling + jsonl + resumability only. Code review against this rule before DAB Phase 1c merge. |
| Spider2 never actually happens, so the orchestrator never gets extracted | Low | Acceptable outcome. The protocol + layout still pays for itself in ADE adapter cleanup. The orchestrator extraction is the deferrable part. |
| Two parallel "eval" shapes confuse readers | Low | Document the split in `eval/__init__.py` docstring: benchmark integrations live in `eval/benchmarks/`; internal LabRat evals stay on legacy `EvalCase`/`EvalRunner`. |
| Spider2 protocol fit reveals gaps (e.g., scorer needs DuckDB connection passed in) | Medium | Phase 4 extraction is the natural moment to add what's missing. `TrialResult.artifact` is flexible — `duckdb_state` type already anticipated here. |

## Decisions Made (and rationale)

- **Option A (light protocol + shared driver) over B (full re-architecture) and C (per-benchmark scripts).** B's blast radius mid-DAB-build conflicts with the no-regressions requirement. C duplicates concurrency / resumability machinery across three benchmarks. A captures the real reusable assets without forcing a risky migration.
- **Hybrid ADE coupling (adapter today, fold-in optional later).** Re-integrating `LabratLocalAgent` from `~/repos/ade-bench` into LabRat would risk the 80% ADE score during a 4-week DAB build. The adapter is the abandonability mechanism — Phase 5 is opt-in, never blocking.
- **Spider2-DBT as design constraint, not Phase 1 build.** Spider2 informs the protocol shape (artifact types, scoring flexibility, DuckDB-state output) but doesn't get built until DAB ships. Avoids over-engineering for a benchmark that hasn't earned commitment.
- **`BenchmarkOrchestrator` and `LabRatAgentDriver` extracted in Phase 4, not Phase 1.** DAB alone doesn't justify the shared infra. Spider2 is the second consumer that does. Inline implementation during DAB Phase 1 stays narrow by design contract.
- **`SubsetSuite` composition for smoke regression.** Cheaper than a hardcoded `ade_smoke.py` file and generalizes to Spider2 smoke later for free.
- **`bird.py` / `latency.py` / `custom_scenarios.py` stay legacy.** Internal evals have no concurrency / resumability / external scoring concerns. Migrating them is ceremony without payoff.
- **One run output structure shared across all benchmarks.** Mirrors ADE-bench's `experiments/<run_id>/` shape so existing analyst tools (`scripts/analyze_ade_failures.py`) port forward.

## Open Questions

- **Exact ADE smoke task IDs.** Picked in DAB Phase 0 after reviewing failure analysis. Codified in `ADE_SMOKE_TASK_IDS` before Phase 1a smoke baseline.
- **Spider2 dataset triage.** Whether known-unsolvable tasks (Fivetran `_tmp`, naming inconsistencies) get an allowlist for "fair score" reporting. Defer to Spider2 spec; not architectural.
- **Phase 5 ADE re-integration.** Whether `LabratLocalAgent` ever folds into LabRat. Defer until DAB + Spider2 both prove out the in-process path on the unified harness.
- **`BenchmarkOrchestrator` cost tracking.** Where per-trial `cost_usd` accounting hooks in for `ClaudeCodeProvider` (token usage without API cost, but capacity-planning-relevant). Defer to Phase 4.
- **Internal evals migration.** Whether `bird.py` / `latency.py` / `custom_scenarios.py` ever move to the new shape. Probably never — they're not benchmark integrations.
