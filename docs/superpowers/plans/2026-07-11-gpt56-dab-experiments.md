# GPT-5.6 DAB Experiments and Submission Operations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-07-11-native-codex-mcp-dab-design.md`

**Branch:** Continue on `feat/codex-caching-gpt56`. Task 0 of the runtime plan first checkpoints the existing goal work; do not fork away from that authoritative state.

**Goal:** Build a preregistered, resumable experiment controller that completes the legacy Luna Max baseline, runs the host, grounding, ContextLedger, feature, and model-tier studies without configuration drift, makes every promotion decision through pure frozen gates, and launches the selected trace-complete 270-trial DAB run only after certification and a durable evidence report.

**Architecture:** A new `labrat.eval.benchmarks.dab.experiments` package owns immutable campaign/arm schemas, the frozen experiment registry, pure metrics and gates, append-only scheduling, legacy-run adoption, report generation, and final selection. `scripts/dab_experiments.py` is the only live-operations entrypoint. It materializes an arm from either a fixed configuration or a typed reference to an earlier decision, calls the normal DAB attempt runner one key at a time, and stops on quota or audit errors. Existing DAB scoring remains authoritative; the experiment layer decides only whether evidence is complete and which preregistered configuration advances.

**Tech stack:** Python 3.12, Pydantic v2, existing `DabSuite`/`TrialResult`, canonical JSON + SHA-256, JSONL append-only event logs, pytest with `asyncio_mode="auto"`, ruff, and pyright strict. Unit tests use fake trials and fake launchers; no unit or integration test in this plan makes a paid model call.

## Global constraints

- The approved design spec is authoritative. A controller convenience must never weaken a policy, isolation, trace, manifest, taint, or eligibility gate.
- Registered comparisons and the full run require a clean LabRat commit. Diagnostic runs may record a dirty-diff digest, but no dirty run can satisfy a comparison prerequisite.
- Codex CLI `0.144.1` is the only submission-eligible native version for this campaign. A different version requires a new certification record and campaign id.
- The controller never mutates an existing `registered_arm.json`, `experiment_manifest.json`, `decision.json`, `trials.jsonl`, trace, or report input. Operational progress is append-only in `controller_events.jsonl`.
- Certification refreshes are append-only records. A refresh is accepted only when
  `contract_sha256` matches the campaign; every attempt records the exact active
  certification-record SHA. An expired record pauses launches until a clean refresh.
- An `infra:*` attempt remains recorded and retryable. An HTTP 429 is flushed, returns exit code `4`, and stops the entire queue before another model call. An `audit-error` returns exit code `5`, scores nothing, and stops the entire campaign.
- A semantic attempt is eligible only when its required policy, isolation, host-event, canonical tool-trace, reconciliation, and taint checks are clean. Missing evidence is incomplete evidence, never a zero or a semantic failure.
- Schedule task-major and run trial numbers `0,1,2` contiguously within an arm. This keeps each arm's stable per-task cache namespace warm while preserving exactly matched trial keys for paired comparisons.
- Cache percentage is supporting context. Host and ledger efficiency gates use paired median **noncached input**. Every report also shows absolute input, cached input, noncached input, request-index curves, and incomplete cache-write coverage.
- Cost is a public-API price equivalent, not a subscription invoice and not evidence about quota debit. The campaign freezes its pricing snapshot before the first semantic attempt.
- No validator, ground truth, answer key, benchmark source path, authentication file, user Maze, or unrelated database may enter a prompt, manifest, report input, trace, or bundle.
- `agnews` remains in the official 270-trial run but is excluded from model-tier promotion because its public label mapping is vulnerable to parametric-memory leakage.
- ContextLedger, `run_program`, `dispatch_subagent`, `llm_extract`, and `llm_classify` are AgentLoop-only studies. They never appear to work on `codex-mcp`, and their results never cross host boundaries.
- Deterministic SQL checks and warnings are common substrate until a real null profile exists. Semantic Scent, TUI, harvest, dbt, Map, Trail, Cheese, team-Scent, and user memory are not rerun in this campaign.

Use focused tests and checks during Tasks 1–9. Run the repository-wide code gate once
after those implementation tasks, immediately before final isolation certification:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
git diff --check
```

Live experiment commands are permitted only after the runtime prerequisites below pass. The code/test tasks themselves must remain hermetic and free of paid calls.

## Runtime and isolation prerequisites

The campaign controller consumes, but does not manufacture, the runtime certification evidence implemented by the native-driver, policy, trace, and isolation plans.

The controller imports the exact frozen `CertificationRecord` defined by the isolation
plan. The operator passes its digest-bound path to `register`; the controller does not
hardcode or synthesize a path/schema. It requires `submission_eligible=true`, a valid
issuance/expiry window, no revocation, and exact clean checks.

Before `register`, the controller must also prove:

1. `git status --porcelain` is empty in LabRat.
2. `git -C ~/repos/DataAgentBench status --porcelain` is empty.
3. `git rev-parse HEAD` matches `CertificationRecord.labrat_commit`.
4. `git -C ~/repos/DataAgentBench rev-parse HEAD` matches `CertificationRecord.dab_commit`.
5. The certified image (not the host CLI) reports Codex `0.144.1` on the certified platform/architecture.
6. The live `dab-core-v1` names/schema hash, policy-builder hash, dual-container isolation/network digests, image digest, credential-plane schema, and gateway identities match the certification record.
7. Per-attempt preflight proves the worker has no data/DB-secret mount or route, the MCP sidecar has no Codex auth/inference route, and the attempt uses a fresh Codex home.

If any check fails, `register` exits `2` before creating a campaign directory. Existing campaigns additionally compare these values to their immutable campaign digest before every launch.

## Frozen campaign registry

The campaign id and root are fixed:

```text
campaign_id: gpt56-dab-20260711
campaign_root: runs/dab/gpt56-dab-20260711
```

Stage order is strict:

```text
certification -> legacy -> host-ab -> grounding -> ledger -> features -> tiers -> evidence -> freeze -> full -> strict-bundle
```

The controller cannot skip a stage. A stage that is not applicable writes a signed `not_applicable.json` carrying the campaign digest and then completes without synthesizing a score.

### Cohorts

`HOST_AB_TASKS`, in this exact order:

```text
deps_dev_v1:1
deps_dev_v1:2
music_brainz_20k:1
music_brainz_20k:3
stockindex:3
yelp:1
```

`GROUNDING_TASKS` is the existing 15-query/four-dataset cohort, in this exact order:

```text
deps_dev_v1:1
deps_dev_v1:2
music_brainz_20k:1
music_brainz_20k:2
music_brainz_20k:3
stockindex:1
stockindex:2
stockindex:3
yelp:1
yelp:2
yelp:3
yelp:4
yelp:5
yelp:6
yelp:7
```

`HARD_TAIL_TASKS`, in this exact order:

```text
crmarenapro:12
deps_dev_v1:1
music_brainz_20k:1
music_brainz_20k:3
pancancer_atlas:1
patents:2
```

`OFFICIAL_TASKS` is deterministically expanded in dataset order from:

```python
OFFICIAL_QUERY_COUNTS = {
    "agnews": 4,
    "bookreview": 3,
    "crmarenapro": 13,
    "deps_dev_v1": 2,
    "github_repos": 4,
    "googlelocal": 4,
    "music_brainz_20k": 3,
    "pancancer_atlas": 3,
    "patents": 3,
    "stockindex": 3,
    "stockmarket": 5,
    "yelp": 7,
}
```

The registry asserts 54 unique official tasks and 270 `(task_id, trial_num)` keys for `n_trials=5`.

### Arm matrix and exact run directories

| Stage | Arm id | Run directory | Host/model/effort | Tasks × trials | Exact treatment |
|---|---|---|---|---:|---|
| legacy | `legacy-full-20260710` | `runs/dab/ablation-gpt56-luna-max-baseline` | labrat-agent/Luna Max | 15 × 3 | frozen 22-tool compatibility surface and original flags |
| host-ab | `host-labrat-agent-luna-max` | `runs/dab/gpt56-dab-20260711/arms/host-labrat-agent-luna-max` | labrat-agent/Luna Max | 6 × 3 | `dab-core-v1`; Cartographer/levers/hints/ledger off |
| host-ab | `host-codex-mcp-luna-max` | `runs/dab/gpt56-dab-20260711/arms/host-codex-mcp-luna-max` | codex-mcp/Luna Max | 6 × 3 | identical safe core, prompt flags, schemas, task order, and timeout |
| grounding | `ground-core` | `runs/dab/gpt56-dab-20260711/arms/ground-core` | host-gate winner/Luna Max | 15 × 3 | safe core |
| grounding | `ground-cartographer` | `runs/dab/gpt56-dab-20260711/arms/ground-cartographer` | host-gate winner/Luna Max | 15 × 3 | core + hermetic Cartographer/search docs |
| grounding | `ground-levers` | `runs/dab/gpt56-dab-20260711/arms/ground-levers` | host-gate winner/Luna Max | 15 × 3 | previous + benchmark-safe prompt levers |
| grounding | `ground-hints` | `runs/dab/gpt56-dab-20260711/arms/ground-hints` | host-gate winner/Luna Max | 15 × 3 | previous + DAB-declared hints |
| ledger | `ledger-h` | `runs/dab/gpt56-dab-20260711/arms/ledger-h` | labrat-agent/Luna Max | 15 × 3 | Cartographer + levers + hints; ledger off |
| ledger | `ledger-g` | `runs/dab/gpt56-dab-20260711/arms/ledger-g` | labrat-agent/Luna Max | 15 × 3 | exact H config; ledger on |
| features | `feature-core` | `runs/dab/gpt56-dab-20260711/arms/feature-core` | labrat-agent/Luna Max | 6 × 3 | frozen grounding flags + safe core |
| features | `feature-program` | `runs/dab/gpt56-dab-20260711/arms/feature-program` | labrat-agent/Luna Max | 6 × 3 | previous + `run_program` |
| features | `feature-program-dispatch` | `runs/dab/gpt56-dab-20260711/arms/feature-program-dispatch` | labrat-agent/Luna Max | 6 × 3 | previous + controlled `dispatch_subagent` |
| features | `feature-llm-primitives-control` | `runs/dab/gpt56-dab-20260711/arms/feature-llm-primitives-control` | labrat-agent/Luna Max | audited cohort × 3 | identical eligible tasks/config without LLM primitives, or not-applicable artifact |
| features | `feature-llm-primitives` | `runs/dab/gpt56-dab-20260711/arms/feature-llm-primitives` | labrat-agent/Luna Max | audited cohort × 3 | exact eligible tasks + `llm_extract`/`llm_classify`, or not-applicable artifact |
| features | `feature-verification-v2` | `runs/dab/gpt56-dab-20260711/arms/feature-verification-v2` | labrat-agent/Luna Max | 6 × 3 | report-only feature-ladder control + bounded verification composite |
| tiers | `tier-luna-max` | `runs/dab/gpt56-dab-20260711/arms/tier-luna-max` | selected host/Luna Max | 6 × 3 | frozen host/profile/grounding |
| tiers | `tier-terra-high` | `runs/dab/gpt56-dab-20260711/arms/tier-terra-high` | selected host/Terra High | 6 × 3 | identical except model/effort |
| tiers | `tier-sol-high` | `runs/dab/gpt56-dab-20260711/arms/tier-sol-high` | selected host/Sol High | 6 × 3 | identical except model/effort |
| tiers | `tier-sol-ultra` | `runs/dab/gpt56-dab-20260711/arms/tier-sol-ultra` | selected host/Sol Ultra | 6 × 3 | pure native Ultra only when codex-mcp won; otherwise labeled AgentLoop composite and report-only |
| full | `full-selected-270` | `runs/dab/gpt56-dab-20260711/arms/full-selected-270` | freeze-decision selection | 54 × 5 | fresh frozen safe-core/portable-grounding configuration; no AgentLoop feature-study tools |

Every non-legacy arm uses a fresh directory. No result from the legacy run is reused as a safe-core control. AgentLoop feature arms are report-only and never feed the tier or full-run tool profile.

### Model ordering and verified price snapshot

The registry freezes these exact model/effort pairs:

```text
gpt-5.6-luna / max
gpt-5.6-terra / high
gpt-5.6-sol / high
gpt-5.6-sol / ultra
```

The promotion cost order is frozen independently of dollar pricing:

```text
Luna Max < Terra High < Sol High < Sol Ultra
```

Before registration, create a write-once `pricing_snapshot.json` from an official
OpenAI source. It records retrieval time, source URL, source-content SHA-256, exact
public model-id mapping, token categories, units, and any published long-context/cache
rules. A value may be used only when the official source exactly supports that model
and telemetry category. If Luna/Terra/Sol subscription tiers lack public API mapping,
the report marks dollar equivalent `unavailable`; it never invents prices,
cache-write multipliers, thresholds, or token-accounting semantics. Tier selection
still uses the frozen order above plus the preregistered accuracy rule.

## Exact schemas and interfaces

Create the following frozen Pydantic models in `models.py`. Every model uses `ConfigDict(frozen=True, extra="forbid")`.

```python
Stage = Literal[
    "legacy", "host-ab", "grounding", "ledger", "features", "tiers", "full"
]
DriverName = Literal["labrat-agent", "codex-mcp"]
ReasoningEffort = Literal["high", "max", "ultra"]
RunClass = Literal["legacy", "registered", "official"]

class DecisionRef(BaseModel):
    decision: Literal["host", "grounding", "tier", "freeze"]
    field: Literal["driver", "grounding_flags", "tool_profile", "model", "reasoning"]

class GroundingFlags(BaseModel):
    cartographer: bool
    prompt_levers: bool
    hints: bool
    ledger: bool

class VerificationFlags(BaseModel):
    consensus_k: int | None
    consensus_diversity: bool
    argue_rounds: int
    reverify: bool
    postverify: bool

class PublicPriceMapping(BaseModel):
    campaign_model: Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
    public_model_id: str | None
    status: Literal["verified", "unavailable"]
    unit: str | None
    input_price: Decimal | None
    cached_input_price: Decimal | None
    output_price: Decimal | None
    published_rules: dict[str, JsonValue]

class PricingSnapshot(BaseModel):
    schema_version: Literal["openai-public-pricing-v1"]
    retrieved_at: datetime
    source_url: str
    source_content_sha256: str
    mappings: tuple[PublicPriceMapping, ...]
    sha256: str

class ArmSpec(BaseModel):
    arm_id: str
    stage: Stage
    run_dir: Path
    run_class: RunClass
    driver: DriverName | DecisionRef
    provider: Literal["codex"]
    model: Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"] | DecisionRef
    reasoning: ReasoningEffort | DecisionRef
    tool_profile: str | DecisionRef
    task_ids: tuple[str, ...]
    n_trials: Literal[3, 5]
    grounding: GroundingFlags | DecisionRef
    verification: VerificationFlags
    timeout_seconds: Literal[1200]
    max_turns: None
    max_tool_calls: None
    cache_namespace: str
    requires_submission_eligibility: bool

class CampaignSpec(BaseModel):
    schema_version: Literal["dab-experiment-campaign-v1"]
    campaign_id: Literal["gpt56-dab-20260711"]
    design_spec_sha256: str
    certification_contract_sha256: str
    initial_certification_record_sha256: str
    labrat_commit: str
    dab_commit: str
    codex_cli_version: Literal["0.144.1"]
    tool_schema_sha256: str
    policy_builder_sha256: str
    isolation_config_sha256: str
    image_digest: str
    pricing: PricingSnapshot
    cohorts: CohortRegistry
    arms: tuple[ArmSpec, ...]
    stage_order: tuple[str, ...]
    created_at: datetime
    sha256: str

class RegisteredArm(BaseModel):
    schema_version: Literal["dab-registered-arm-v1"]
    campaign_sha256: str
    resolved_spec: ArmSpec
    runtime_manifest_sha256: str
    parent_decision_sha256: str | None
    expected_semantic_keys: tuple[str, ...]
    arm_sha256: str

class ControllerEvent(BaseModel):
    schema_version: Literal["dab-controller-event-v1"]
    sequence: int
    timestamp: datetime
    stage: str
    arm_id: str | None
    event: Literal[
        "registered", "attempt_started", "attempt_finished", "quota_paused",
        "audit_paused", "arm_completed", "stage_completed", "decision_written",
        "certification_refreshed"
    ]
    task_id: str | None
    trial_num: int | None
    attempt_id: str | None
    detail: dict[str, JsonValue]
```

Canonical hashing is `json.dumps(model_dump(mode="json", exclude={"sha256"}), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()` followed by SHA-256. Files are written with exclusive creation; a byte-identical existing file is accepted on resume, and any mismatch raises `ExperimentConflict`.

Required public functions:

```python
# registry.py
def build_campaign_spec(certification: CertificationRecord, repo: RepoSnapshot) -> CampaignSpec: ...
def expected_keys(arm: ArmSpec) -> tuple[tuple[str, int], ...]: ...
def materialize_arm(spec: ArmSpec, decisions: DecisionStore) -> RegisteredArm: ...

# storage.py
def write_immutable_json(path: Path, value: BaseModel) -> str: ...
def append_controller_event(path: Path, event: ControllerEvent) -> None: ...
def load_and_verify_campaign(root: Path) -> CampaignSpec: ...
def load_and_verify_arm(run_dir: Path) -> RegisteredArm: ...
def append_certification_refresh(root: Path, record: CertificationRecord) -> str: ...

# controller.py
def next_pending_key(arm: RegisteredArm, attempts: list[TrialAttempt]) -> tuple[str, int] | None: ...
def host_ab_schedule(window_index: int, attempts: AttemptIndex) -> tuple[ScheduledKey, ...]: ...
async def run_stage(root: Path, stage: str, launcher: AttemptLauncher) -> StageOutcome: ...
def assert_stage_complete(root: Path, stage: str) -> None: ...
def active_certification(root: Path, now: datetime) -> CertificationRecord: ...

# metrics.py
def select_semantic_attempts(records: Iterable[TrialAttempt]) -> dict[TrialKey, TrialAttempt]: ...
def summarize_arm(arm: RegisteredArm, records: Iterable[TrialAttempt]) -> ArmResult: ...
def pair_results(control: ArmResult, treatment: ArmResult) -> tuple[TrialPair, ...]: ...
def equivalent_request_cost(request: RequestUsage, pricing: PricingSnapshot) -> CostResult: ...
def request_index_curve(result: ArmResult) -> tuple[RequestIndexPoint, ...]: ...

# gates.py
def choose_host(labrat: ArmResult, native: ArmResult) -> HostDecision: ...
def choose_grounding(results: tuple[ArmResult, ...]) -> GroundingDecision: ...
def choose_ledger(h: ArmResult, g: ArmResult) -> LedgerDecision: ...
def choose_feature_profile(results: FeatureResults) -> FeatureDecision: ...
def choose_tier(results: TierResults) -> TierDecision: ...
def build_freeze_decision(inputs: DecisionInputs) -> FreezeDecision: ...

# reporting.py
def build_evidence(campaign: CampaignSpec, results: CampaignResults) -> EvidenceReport: ...
def render_markdown(evidence: EvidenceReport) -> str: ...
def publish_report(root: Path, evidence: EvidenceReport) -> tuple[Path, Path]: ...
```

## File map

| File | Operation |
|---|---|
| Create `src/labrat/eval/benchmarks/dab/experiments/__init__.py` | public experiment exports |
| Create `src/labrat/eval/benchmarks/dab/experiments/models.py` | frozen campaign, arm, attempt, result, decision schemas |
| Create `src/labrat/eval/benchmarks/dab/experiments/registry.py` | exact cohorts, arm matrix, verified pricing snapshot, and materialization |
| Create `src/labrat/eval/benchmarks/dab/experiments/storage.py` | canonical hashes, exclusive writes, JSONL events |
| Create `src/labrat/eval/benchmarks/dab/experiments/metrics.py` | semantic selection, stratified score, cache/cost pairs and curves |
| Create `src/labrat/eval/benchmarks/dab/experiments/gates.py` | pure host, grounding, ledger, feature, tier, and freeze gates |
| Create `src/labrat/eval/benchmarks/dab/experiments/legacy.py` | campaign adoption around the authoritative runtime legacy sidecar |
| Create `src/labrat/eval/benchmarks/dab/experiments/controller.py` | stage ordering, AB/BA scheduler, stop/resume behavior |
| Create `src/labrat/eval/benchmarks/dab/experiments/reporting.py` | machine evidence and durable Markdown report |
| Create `scripts/dab_experiments.py` | `register`, `adopt-legacy`, `run`, `status`, `report`, `freeze`, `bundle` CLI |
| Modify `scripts/eval_dab.py` | expose one-attempt launch seam; consume registered arm/cache namespace |
| Modify `src/labrat/eval/benchmarks/dab/bundle.py` | campaign/freeze eligibility layered over driver-specific runtime/isolation contracts |
| Modify `scripts/build_dab_trace_bundle.py` | tracked bundle CLI wrapper |
| Modify `docs/dab-integration.md` | controller, campaign, retry, and full-run operations |
| Modify `docs/dab-solultra-ablation.md` | publish completed preregistered results and selection |
| Modify `docs/codex-caching-investigation.md` | publish host/cache findings and request-index curves |
| Create `tests/unit/test_dab_experiment_models.py` | schemas and immutable storage |
| Create `tests/unit/test_dab_experiment_registry.py` | exact cohorts, arms, task counts, decision refs |
| Create `tests/unit/test_dab_experiment_metrics.py` | cache/cost/stratified/paired reporting |
| Create `tests/unit/test_dab_experiment_gates.py` | every boundary and tie-break |
| Create `tests/unit/test_dab_experiment_legacy.py` | sidecar and append-only resume |
| Create `tests/unit/test_dab_experiment_controller.py` | schedule, stop, resume, prerequisites |
| Create `tests/unit/test_dab_experiment_reporting.py` | complete report, no missing-value coercion |
| Extend `tests/unit/test_eval_dab_runner.py` | registered one-attempt seam and immutable conflict checks |
| Extend `tests/unit/test_dab_trace_bundle.py` | freeze and 270-trial native strict bundle |

---

# Phase A — Immutable registry, metrics, and gates

### Task 1: Frozen schemas, canonical storage, and campaign registry

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/experiments/__init__.py`
- Create: `src/labrat/eval/benchmarks/dab/experiments/models.py`
- Create: `src/labrat/eval/benchmarks/dab/experiments/storage.py`
- Create: `src/labrat/eval/benchmarks/dab/experiments/registry.py`
- Test: `tests/unit/test_dab_experiment_models.py`
- Test: `tests/unit/test_dab_experiment_registry.py`

- [ ] **RED — write schema and registry tests.** Assert extra fields are rejected; nested values are frozen; canonical hashes are stable across key order; exclusive write accepts byte-identical resume and rejects drift; all exact cohort tuples match this plan; the arm ids/run directories are unique; grounding has four portable arms; ledger has exactly H/G; tiers have exactly the four model/effort pairs; full has 54 tasks × five trials; every comparison arm requires a clean commit and the appropriate eligibility state.

- [ ] Run the red tests:

```bash
uv run pytest -q tests/unit/test_dab_experiment_models.py tests/unit/test_dab_experiment_registry.py
```

Expected: collection fails because the package does not exist.

- [ ] **GREEN — implement the exact models, hash contract, constants, and registry.** Validate the campaign id, CLI version, stage order, task uniqueness, denominator, and typed decision references at model construction. Implement `write_immutable_json` with `Path.open("x")`; on `FileExistsError`, compare canonical bytes and raise `ExperimentConflict` on any difference.

- [ ] Run the focused tests again and require all pass.

- [ ] **REFACTOR — keep registry data declarative.** Move repeated safe-core/verification values into frozen constructors without changing serialized output. Run ruff and pyright on the package.

- [ ] Run the focused task gate and commit only Task 1 files:

```bash
git add src/labrat/eval/benchmarks/dab/experiments/__init__.py \
  src/labrat/eval/benchmarks/dab/experiments/models.py \
  src/labrat/eval/benchmarks/dab/experiments/storage.py \
  src/labrat/eval/benchmarks/dab/experiments/registry.py \
  tests/unit/test_dab_experiment_models.py \
  tests/unit/test_dab_experiment_registry.py
git commit -m "feat(dab): register immutable GPT-5.6 experiment campaign"
```

### Task 2: Pure semantic, cache, request-curve, and cost metrics

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/experiments/metrics.py`
- Test: `tests/unit/test_dab_experiment_metrics.py`

The authoritative formulas are:

```text
noncached_input = input_tokens - cached_input_tokens
cache_read_ratio = sum(cached_input_tokens) / sum(input_tokens)
control_median = median(control_noncached_i over the exact paired key set)
treatment_median = median(treatment_noncached_i over the exact paired key set)
paired_median_reduction = 1 - treatment_median / control_median
```

`pair_results` requires the same complete semantic key set and pairs by `(task_id, trial_num)`. A missing usage record, negative noncached input, duplicate semantic attempt, zero control denominator, or mismatched key set makes the efficiency comparison incomplete and therefore unable to pass an efficiency gate.

For request `j`, preserve every observed telemetry category verbatim. Compute a
public-API equivalent only when the hashed official pricing snapshot defines the exact
model mapping, token categories, and applicable multipliers. The pricing evaluator is
schema-driven; no multiplier or assumption is compiled into `metrics.py`. If the
source does not state whether reasoning is included in output, or cache writes are
unobserved/unpriced, `CostResult.status` is `unavailable` or `incomplete` with named
missing categories rather than a fabricated lower bound.

Stratified Pass@1 is query pass rate averaged within dataset, then equally averaged across included datasets. Also retain raw passes and the unweighted query mean.

- [ ] **RED — write metric tests.** Cover one semantic plus multiple infra attempts; rejection of duplicate semantics; four-dataset stratification where Yelp still weighs one quarter; request-index aggregation; cached tokens included in input; exact ratio-of-paired-medians boundaries at 25% and 20%; mismatched/missing pairs; aggregate cache ratio as ratio-of-sums; a complete fixture-backed official pricing schema; missing model mapping; missing cache-write/reasoning semantics; and rejection of unverified multipliers.

- [ ] Run:

```bash
uv run pytest -q tests/unit/test_dab_experiment_metrics.py
```

Expected: import failure for `experiments.metrics`.

- [ ] **GREEN — implement pure functions.** Use `Decimal` for source-backed USD calculations and serialize rounded decimal strings with six fractional digits. Preserve raw integer token counts and explicit unavailable/incomplete states. Do not read files or environment variables in `metrics.py`.

- [ ] **REFACTOR — isolate selection, scoring, pairing, and pricing helpers.** Ensure `select_semantic_attempts` is the only attempt-selection implementation used by reporting and gates.

- [ ] Run the focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/experiments/metrics.py \
  tests/unit/test_dab_experiment_metrics.py
git commit -m "feat(dab): add paired cache and equivalent-cost evidence"
```

### Task 3: Pure preregistered decision gates

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/experiments/gates.py`
- Test: `tests/unit/test_dab_experiment_gates.py`

All gate functions are pure: frozen result objects in, frozen decision objects out. They do not read run directories, clocks, environment variables, or model output text.

The exact rules are:

1. **Host:** native wins only if every policy/isolation/trace eligibility flag is clean, native raw passes are at least `labrat_raw_passes - 1` out of 18, and paired median noncached-input reduction is at least `0.25`. Otherwise select `labrat-agent`. A missing efficiency pair or audit field cannot select native.
2. **Grounding:** choose highest completed four-dataset stratified Pass@1 across `ground-core`, `ground-cartographer`, `ground-levers`, and `ground-hints`. Exact score ties choose lower verified equivalent cost only when complete for every tied arm, otherwise lower mean noncached input, then lower mean latency, then earlier arm in registered cumulative order.
3. **Ledger:** promote G when G score exceeds H; also promote on an exact score tie only when raw passes do not fall and paired median noncached-input reduction is at least `0.20`. Any score loss rejects ledger. If the selected official host is `codex-mcp`, record the ledger decision as AgentLoop-only and do not apply it to native configuration.
4. **Program/dispatch ladder:** evaluate each increment against its immediate predecessor and report the same score/tie logic, but never promote these tools into the certified submission profile. `dispatch_subagent` is interpretable only after `run_program` because the arm is cumulative.
5. **LLM primitives:** compare treatment to the exact audited-cohort control. Report a positive delta only when treatment gains at least one raw pass with no per-task pass-rate loss. A `not_applicable` result has no score. Neither outcome changes the submission profile.
6. **Verification-v2:** report the composite as positive only if hard-tail stratified score and raw passes both exceed its exact control. A tie rejects it. It remains report-only because of multiplied model-call cost.
7. **Tier:** Luna is the default. A larger comparable tier qualifies only if its completed hard-tail stratified score exceeds Luna and it records at least one pass on a Luna `0/3` query. Choose the first qualifying arm in frozen cost order. A tie or only swapped stochastic passes retains Luna. When native won, Sol Ultra is pure native effort with native multi-agent disabled and additionally needs a clear beyond Sol High. When `labrat-agent` won, its Ultra composite is reported but cannot promote the official model; promotion is limited to Luna/Terra/Sol High.
8. **Freeze:** require complete host, grounding, ledger, feature-report, and tier evidence plus the prelaunch-report digest. Apply a passing Ledger result only when `labrat-agent` is selected; program/dispatch/LLM/verification studies remain report-only. Emit one exact host/safe-profile/portable-grounding/model/effort configuration with no unresolved reference.

- [ ] **RED — write table-driven boundary tests.** Include native at exactly one fewer pass and exactly 25%; native two fewer; 24.999%; a single dirty audit flag; grounding every tie-break; ledger 19.999%, 20%, score loss, and score gain; program and dispatch cumulative behavior; eligible and not-applicable LLM primitives; verification score tie; Terra/Sol clears versus stochastic swaps; Sol Ultra with and without an incremental clear; and freeze refusing unresolved or incomplete evidence.

- [ ] Run:

```bash
uv run pytest -q tests/unit/test_dab_experiment_gates.py
```

Expected: import failure for `experiments.gates`.

- [ ] **GREEN — implement the gates with named threshold constants.** Decision objects include selected value, every measured predicate, threshold, pass/fail, and source arm digest so the report can explain the result without recomputation.

- [ ] **REFACTOR — eliminate branch duplication with a shared completeness check.** Keep each public gate short and preserve the table-driven tests.

- [ ] Run the focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/experiments/gates.py \
  tests/unit/test_dab_experiment_gates.py
git commit -m "feat(dab): freeze preregistered experiment gates"
```

---

# Phase B — Attempt controller and legacy adoption

### Task 4: One-attempt launch seam and append-only stage controller

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/experiments/controller.py`
- Modify: `scripts/eval_dab.py`
- Test: `tests/unit/test_dab_experiment_controller.py`
- Extend: `tests/unit/test_eval_dab_runner.py`

Refactor the body that currently runs one `DabSuite.run_trial(...)` call into this reusable interface without changing ordinary `eval_dab.py` behavior:

```python
class AttemptLauncher(Protocol):
    async def __call__(
        self,
        arm: RegisteredArm,
        task_id: str,
        trial_num: int,
        attempt_id: str,
    ) -> TrialAttempt: ...

async def launch_registered_attempt(
    arm: RegisteredArm,
    task_id: str,
    trial_num: int,
    attempt_id: str,
) -> TrialAttempt: ...
```

Attempt scratch is isolated by ordinal:

```text
<run-dir>/scratch/<safe-task-id>__trial<trial-num>/attempt-<four-digit-ordinal>/
```

Before launch, call the runtime attempt API, which exclusively creates a new directory and required trace files; a collision fails rather than truncating. After launch, finalize the authoritative runtime `attempt_manifest.json`, then append the trial row. The experiment layer references the runtime run/attempt-manifest digests and never defines a second manifest schema or overwrites an earlier attempt.

For ordinary arms, schedule task-major in registered order, then trial `0,1,2`. For host A/B define A=`host-labrat-agent-luna-max`, B=`host-codex-mcp-luna-max`; one task × three trials is a block. In quota window `w`, block `b` runs AB when `(w + b) % 2 == 0` and BA otherwise. A 429 ends the window immediately; resume increments `w`, skips completed semantic keys, reverses the next block order, and preserves a partially attempted block.

The cache key for `labrat-agent` is the first 64 hexadecimal characters of SHA-256 over `campaign_id + "\0" + arm_id + "\0" + task_id`. It is stable across all retries/trials in that arm and different across treatment arms. The native driver retains its first-party cache behavior; both hosts remain task-major.

- [ ] **RED — write controller and compatibility tests.** Assert exact task-major order; AB/BA inversion by block and quota window; stable cache namespace; one model call per scheduled key; retryable infra remains pending; 429 appends once then stops before the next key with exit `4`; audit error stops with exit `5`; a semantic result without clean required traces becomes audit error; resume never reruns a selected semantic key; ordinary `eval_dab.py` still runs and resumes existing nonregistered directories.

- [ ] Run:

```bash
uv run pytest -q tests/unit/test_dab_experiment_controller.py tests/unit/test_eval_dab_runner.py
```

Expected: controller imports and registered attempt interface fail.

- [ ] **GREEN — implement `AttemptIndex`, schedule functions, `run_stage`, and the extracted launch seam.** Flush JSONL after every row/event. Permit at most one retryable non-429 infrastructure attempt per key in one invocation so transport errors cannot form a tight loop.

- [ ] **REFACTOR — make scheduling pure and filesystem orchestration thin.** Keep legacy `_run_interim` compatibility tests green until all callers intentionally migrate.

- [ ] Run the focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/experiments/controller.py \
  scripts/eval_dab.py \
  tests/unit/test_dab_experiment_controller.py \
  tests/unit/test_eval_dab_runner.py
git commit -m "feat(dab): add append-only registered experiment controller"
```

### Task 5: Legacy baseline sidecar and drift-safe resume

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/experiments/legacy.py`
- Test: `tests/unit/test_dab_experiment_legacy.py`

Adopt only `runs/dab/ablation-gpt56-luna-max-baseline`. The authoritative
`experiment_manifest.json` is created and verified by the runtime manifest API; this
module stores only its digest in the campaign record. It never defines a second schema
under the same filename or rewrites `config.json`, `trials.jsonl`, or scratch artifacts.

The runtime sidecar recovers any historical DAB identity from authoritative run
evidence, verifies the exact 22-tool `legacy-full-20260710` order and selected
prompts/configs/hints/validators, and records observed semantic/infra counts as ordinary
snapshot integers. It does not hardcode an unproven DAB SHA or mutable counts into
`Literal` types. If the historical fingerprint cannot be proven, adoption fails
honestly and makes no changes. Resume continues until all 45 keys have exactly one
non-infrastructure semantic attempt, retains every infrastructure row, and stops on
each new 429.

- [ ] **RED — write tests** using copied synthetic legacy runs: a provable fingerprint
  succeeds with whatever counts are observed; missing/unproven identity and changed
  prompt, validator, task filter, trial count, tool order, or schema fail; existing core
  files remain byte-identical; resume appends; 45 semantic keys completes the stage.

- [ ] Run:

```bash
uv run pytest -q tests/unit/test_dab_experiment_legacy.py
```

- [ ] **GREEN — implement** `adopt_legacy_run`, `verify_legacy_resume`, and
  `legacy_stage_complete` as wrappers around the runtime `create_legacy_sidecar` and
  manifest verifier.

- [ ] **REFACTOR — reference runtime manifest digests; never duplicate manifest normalization.**

- [ ] Run the focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/experiments/legacy.py \
  tests/unit/test_dab_experiment_legacy.py
git commit -m "feat(dab): adopt legacy Luna baseline without drift"
```

### Task 6: Static feature applicability and exact feature profiles

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/tool_profiles.py`
- Modify: `src/labrat/eval/benchmarks/dab/experiments/registry.py`
- Modify: `src/labrat/eval/benchmarks/dab/experiments/controller.py`
- Extend: `tests/unit/test_dab_experiment_registry.py`
- Extend: `tests/unit/test_dab_experiment_controller.py`

Add exact AgentLoop-only profiles to the shared resolver implemented by the runtime plan:

```text
dab-core-v1+program-v1
dab-core-v1+program-dispatch-v1
dab-core-v1+program-dispatch-llm-v1
```

The latter profiles add only the named tools to `dab-core-v1`; they do not add file, shell, Trail, Map, memory, dbt, or native host tools. `run_program` uses its already restricted subregistry. `dispatch_subagent` remains the controlled LabRat tool, not native host delegation.

These profiles exist only for isolated AgentLoop feature arms. Registered tier and
official arms reject them and retain the certified safe profile. When the LLM audit is
eligible, materialize both `feature-llm-primitives-control` and treatment on the exact
same task keys/config, differing only by the two primitive tools.

Implement:

```python
class FeatureApplicability(BaseModel):
    schema_version: Literal["dab-feature-applicability-v1"]
    status: Literal["eligible", "not_applicable"]
    task_ids: tuple[str, ...]
    reasons_by_task: dict[str, str]
    audited_prompt_manifest_sha256: str
    sha256: str

def audit_llm_primitive_applicability(tasks: tuple[TaskDescriptor, ...]) -> FeatureApplicability: ...
```

A task is eligible only when its prompt and data description require row-level extraction/classification from unstructured database text, the requested field/labels are defined by the task or task data rather than a public benchmark mapping, and the bounded operation fits the shipped 200-row primitive cap. The audit reads no validator or ground truth. Zero eligible tasks produces `not_applicable.json`, no model call, and no zero score.

The verification-v2 composite is exact: `consensus_k=3`, `consensus_diversity=True`, `argue_rounds=2`, `reverify=True`, `postverify=True`. Its multiplied requests, tokens, and cost are reported separately.

- [ ] **RED — add tests** for eligible unstructured text, public-label leakage rejection, over-200-row rejection, empty audit, exact same-cohort control/treatment, exact additive profiles, native-host/tier/full rejection, and exact verification composite flags.

- [ ] Run:

```bash
uv run pytest -q tests/unit/test_dab_experiment_registry.py tests/unit/test_dab_experiment_controller.py
```

- [ ] **GREEN — implement the audit, materialization branch, and exact profiles.**

- [ ] **REFACTOR — keep applicability deterministic and explain every rejection in the artifact.**

- [ ] Run the focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/tool_profiles.py \
  src/labrat/eval/benchmarks/dab/experiments/registry.py \
  src/labrat/eval/benchmarks/dab/experiments/controller.py \
  tests/unit/test_dab_experiment_registry.py \
  tests/unit/test_dab_experiment_controller.py
git commit -m "feat(dab): register AgentLoop feature studies"
```

---

# Phase C — Evidence, freeze, CLI, and strict bundle

### Task 7: Complete evidence report and write-once freeze decision

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/experiments/reporting.py`
- Test: `tests/unit/test_dab_experiment_reporting.py`

`EvidenceReport` contains campaign/certification digests; stage completeness; semantic and infrastructure attempt counts; stratified and raw scores; per-query pass counts; zero-to-pass clears; aggregate and per-trial input/cached/noncached/output/reasoning tokens; request-index cache curves; tool calls; completed requests; latency; verified equivalent cost or explicit unavailable/incomplete status; quota/reset incidence; policy/isolation/trace eligibility; every gate predicate; and the selected host/safe-profile/flags/model/effort.

Prelaunch outputs are write-once:

```text
runs/dab/gpt56-dab-20260711/prelaunch_evidence.json
runs/dab/gpt56-dab-20260711/prelaunch_report.md
runs/dab/gpt56-dab-20260711/decision.json
```

`report --phase prelaunch` refuses incomplete prerequisite stages and never prints missing numbers as zero. It explicitly labels host-portable, AgentLoop-only, common-substrate, not-applicable, and intentionally excluded features. `freeze` consumes the prelaunch evidence digest and all pure decisions; it writes one fully resolved `FreezeDecision`. Re-running accepts identical bytes and rejects drift. After the full run/bundle, `report --phase final` writes separate immutable `final_evidence.json` and `final_report.md`; it never rewrites prelaunch evidence.

- [ ] **RED — write report tests.** Assert every required table/metric is present; request curves are indexed; cost unavailable/incomplete states are labeled; infra is outside semantic denominators; host A/B names the irreducible host-stack difference; report-only feature results never alter the submission profile; an incomplete prerequisite blocks prelaunch; changed evidence blocks freeze; final evidence requires the strict bundle; and prelaunch bytes remain unchanged.

- [ ] Run:

```bash
uv run pytest -q tests/unit/test_dab_experiment_reporting.py
```

- [ ] **GREEN — implement evidence construction, Markdown rendering, and freeze.** Render from typed evidence only; do not scrape prose documents.

- [ ] **REFACTOR — split table renderers by study while keeping one machine evidence source.**

- [ ] Run the focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/experiments/reporting.py \
  tests/unit/test_dab_experiment_reporting.py
git commit -m "feat(dab): generate preregistered evidence and freeze decision"
```

### Task 8: Operations CLI and stage prerequisites

**Files:**
- Create: `scripts/dab_experiments.py`
- Extend: `tests/unit/test_dab_experiment_controller.py`
- Modify: `docs/dab-integration.md`

Subcommands and exit codes are exact:

```text
register       validate certification/repos and create campaign.json
refresh-certification  append a new unexpired record with identical contract digest
adopt-legacy   create and verify the legacy sidecar
run            run/resume one allowed stage
status         print keys/attempts/completeness without mutation
report         create immutable prelaunch or final evidence/report artifacts
freeze         create decision.json
bundle         build the strict full-run bundle

0 success or completed stage
2 invalid input, drift, or unmet prerequisite
3 taint failure
4 rate-limit pause after durable infra row
5 audit/policy/isolation/trace pause
```

`run --stage grounding` materializes its host from `host-decision.json`; `run --stage tiers` materializes host/safe-profile/grounding from prior decisions; `run --stage full` requires `decision.json` and creates the 270-key arm. Before every attempt it requires an unexpired, unrevoked active certification record. `refresh-certification` accepts only an identical contract digest. The CLI never accepts manual winner or eligibility flags.

- [ ] **RED — add CLI tests** for every subcommand, stage ordering, clean-worktree checks, expired/revoked/mismatched certification, identical-contract refresh, rejection of changed contract, per-attempt record SHA, exact exit codes, idempotent status, forbidden manual selection flags, and rate-limit resume instructions naming the same campaign/stage.

- [ ] Run:

```bash
uv run pytest -q tests/unit/test_dab_experiment_controller.py
```

- [ ] **GREEN — implement argparse plumbing and prerequisite checks.** Keep subcommands thin; all decisions stay in package functions.

- [ ] **REFACTOR — centralize error-to-exit-code translation and sanitize all printed paths/secrets.**

- [ ] Run the focused task gate and commit:

```bash
git add scripts/dab_experiments.py \
  tests/unit/test_dab_experiment_controller.py \
  docs/dab-integration.md
git commit -m "feat(dab): add registered experiment operations CLI"
```

### Task 9: Trace-complete full-run gate and strict native bundle

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/bundle.py`
- Modify: `scripts/build_dab_trace_bundle.py`
- Extend: `tests/unit/test_dab_trace_bundle.py`

Extend package `build_bundle(...)` with `campaign_root: Path | None` and require campaign/freeze evidence for `strict_official=True`. For every selected semantic attempt require the authoritative runtime attempt manifest, canonical tool trace, clean taint, and hashes. For `codex-mcp`, also require native events/usage/policy plus matching dual-container certification and reconciliation. For `labrat-agent`, require its canonical agent trace only; do not invent native policy/isolation artifacts. Copy all infrastructure attempt manifests/partial traces into an `attempts/` subtree, never just row summaries.

Strict success requires:

```text
270 selected semantic attempts
54 exact official tasks × 5 trials
all infrastructure attempts retained
270 canonical tool traces
270 valid native event/token traces when codex-mcp is selected
270 clean policy/isolation records only when codex-mcp is selected
driver-appropriate reconciliation and clean taint verdicts
prelaunch_evidence.json + prelaunch_report.md + decision.json digests
submission.json matching selected semantic keys
```

The bundle manifest hashes every copied artifact and records campaign, certification, CLI, policy, isolation, decision, and report digests. Strict bundling before `freeze` or before all 270 keys is impossible.

- [ ] **RED — extend bundle tests** for missing freeze, 269 semantics, missing infra attempt artifacts, missing each driver-specific artifact, expired/revoked certification, trace mismatch, dirty policy/taint, wrong selected host, complete labrat-agent bundle without fabricated native records, complete codex-mcp bundle, and exact hash inventory.

- [ ] Run:

```bash
uv run pytest -q tests/unit/test_dab_trace_bundle.py
```

- [ ] **GREEN — implement campaign-aware validation and atomic copying.** Preserve the existing non-strict subset bundle behavior.

- [ ] **REFACTOR — use driver-specific required-artifact tables and one hash copier.**

- [ ] Run the focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/bundle.py scripts/build_dab_trace_bundle.py tests/unit/test_dab_trace_bundle.py
git commit -m "feat(dab): require freeze evidence for strict trace bundles"
```

---

# Phase D — Exact live operations

These steps are operational, not unit tests. Run them only from the clean certified commits recorded in `certification.json`. On exit `4`, wait until the reported reset and rerun the identical command. On exit `5`, preserve all artifacts and repair/re-certify; do not retry the model call as a stochastic failure.

### Task 10: Final certification, campaign registration, and legacy adoption

- [ ] After Tasks 1–9 are committed, run the one repository-wide code gate, verify
  clean LabRat/DataAgentBench commits, then execute the isolation plan's `no-model`,
  Luna-low `live-no-tool`, synthetic `live-integrated`, and `finalize` commands. The
  resulting record must bind this exact LabRat commit; use its actual digest-bound
  path as `$CERTIFICATION`. Do not use the host `codex --version` as certification.

```bash
git status --short
git -C ~/repos/DataAgentBench status --short
git rev-parse HEAD
git -C ~/repos/DataAgentBench rev-parse HEAD
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
git diff --check
```

- [ ] Create `pricing_snapshot.json` from a current official OpenAI source and its
  content hash. If no exact public Luna/Terra/Sol mapping exists, record unavailable
  mappings rather than inferred prices.

- [ ] Register the campaign:

```bash
uv run python scripts/dab_experiments.py register \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --dab-dir ~/repos/DataAgentBench \
  --certification "$CERTIFICATION" \
  --pricing-snapshot runs/dab/gpt56-dab-20260711/pricing_snapshot.json
```

- [ ] Adopt the legacy baseline without changing its existing files:

```bash
uv run python scripts/dab_experiments.py adopt-legacy \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --run-dir runs/dab/ablation-gpt56-luna-max-baseline
```

- [ ] Resume until 45 semantic keys are complete:

```bash
uv run python scripts/dab_experiments.py run \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --stage legacy
```

- [ ] Confirm status shows exactly 45 semantic results and all infrastructure rows retained:

```bash
uv run python scripts/dab_experiments.py status \
  --campaign-root runs/dab/gpt56-dab-20260711
```

### Task 11: Run and gate the six-task host A/B

- [ ] Run the controller-managed AB/BA queue:

```bash
uv run python scripts/dab_experiments.py run \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --stage host-ab
```

- [ ] Require 18 semantic keys per host, exact paired keys, clean native safety evidence, raw-pass difference no worse than one for native, and at least 25% paired median noncached-input reduction before native is selected. The controller writes `host-decision.json`; it defaults to `labrat-agent` when any native predicate fails.

### Task 12: Run the portable grounding ladder and Ledger H/G

- [ ] Run all four fresh portable arms on the selected host:

```bash
uv run python scripts/dab_experiments.py run \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --stage grounding
```

- [ ] Require 45 semantic keys in each arm. Let the pure gate select highest stratified Pass@1 with cost, noncached input, latency, then registered-order tie-breaks.

- [ ] Run the separate labrat-agent Ledger comparison:

```bash
uv run python scripts/dab_experiments.py run \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --stage ledger
```

- [ ] Require 45 semantic keys for both H and G. Apply the exact no-accuracy-loss and 20% paired median noncached-input rule; keep the result AgentLoop-only when native won the host gate.

### Task 13: Run last-week feature studies

- [ ] Run the static applicability audit and hard-tail cumulative ladder:

```bash
uv run python scripts/dab_experiments.py run \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --stage features
```

- [ ] Require 18 semantic keys for core, program, program+dispatch, and verification-v2. When LLM primitives are applicable, require exact paired control/treatment keys; otherwise require the not-applicable artifact. Report every feature result as AgentLoop-only/report-only, deterministic SQL checks as common substrate, and verification's multiplied cost separately.

### Task 14: Run the fixed four-arm tier study

- [ ] Run the four exact model/effort arms on the same selected host, certified safe profile, and portable-grounding configuration:

```bash
uv run python scripts/dab_experiments.py run \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --stage tiers
```

- [ ] Require 18 semantic keys per comparable tier. Keep Luna unless a larger tier improves completed hard-tail stratified score and clears at least one Luna `0/3` query; choose the first qualifier in frozen tier order. Sol Ultra is promotion-eligible only on native Codex with native multi-agent disabled and an incremental clear beyond Sol High. On a labrat-agent winner, run/report its labeled Ultra composite but exclude it from promotion.

### Task 15: Publish evidence and freeze the selected configuration

- [ ] Create the complete machine and Markdown evidence:

```bash
uv run python scripts/dab_experiments.py report \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --phase prelaunch
```

- [ ] Freeze the one fully resolved selection:

```bash
uv run python scripts/dab_experiments.py freeze \
  --campaign-root runs/dab/gpt56-dab-20260711
```

- [ ] Preserve the clean certified commit after freeze. The write-once campaign `prelaunch_report.md`
  is the durable pre-launch report; do not publish repository docs yet, because changing
  `HEAD` would violate the full arm's frozen LabRat commit.

### Task 16: Run the selected 270-trial configuration and build the strict bundle

- [ ] Launch/resume the fresh full arm. The CLI reads `decision.json`; it accepts no model, host, profile, or grounding override:

```bash
uv run python scripts/dab_experiments.py run \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --stage full
```

- [ ] Repeat the identical command after each quota reset until status proves exactly 270 selected semantic attempts and retains every infrastructure attempt.

- [ ] Build the strict bundle:

```bash
uv run python scripts/dab_experiments.py bundle \
  --campaign-root runs/dab/gpt56-dab-20260711
```

- [ ] Independently run the underlying strict verifier:

```bash
uv run python scripts/build_dab_trace_bundle.py \
  --run-dir runs/dab/gpt56-dab-20260711/arms/full-selected-270 \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --output-dir runs/dab/gpt56-dab-20260711/strict-trace-bundle \
  --strict-official
```

- [ ] Verify final status and hashes:

```bash
uv run python scripts/dab_experiments.py status \
  --campaign-root runs/dab/gpt56-dab-20260711
git diff --check
```

- [ ] After the strict bundle succeeds, publish the completed study and full-run findings
  from `final_evidence.json`, then commit only the generated documentation changes:

```bash
uv run python scripts/dab_experiments.py report \
  --campaign-root runs/dab/gpt56-dab-20260711 \
  --phase final \
  --publish-docs
git add docs/dab-solultra-ablation.md docs/codex-caching-investigation.md
git commit -m "docs(dab): report GPT-5.6 experiments and full run"
```

Completion is true only when the controller reports every stage complete, the full arm has 270 selected semantic attempts, the selected-host trace contract is complete for all 270, policy/isolation/reconciliation/taint are clean, and `strict-trace-bundle/manifest.json` verifies every artifact hash. A high score without these artifacts is not submission-ready.

## Final implementation audit

- [ ] Confirm every cohort, denominator, model, effort, threshold, tie-break, output path, and command matches the approved design.
- [ ] Confirm all implementation tests use fakes/fixtures and make no paid call.
- [ ] Confirm no dynamic winner is supplied manually; downstream arms resolve only from hashed decisions.
- [ ] Confirm cache comparisons use paired noncached input and reports retain absolute/cache/request-index evidence.
- [ ] Confirm legacy files are byte-preserved and all infrastructure attempts remain append-only.
- [ ] Confirm native host selection requires all safety gates plus accuracy and 25% efficiency.
- [ ] Confirm Ledger requires no score loss and either a score gain or 20% paired efficiency at a tie.
- [ ] Confirm larger-tier promotion requires hard-tail improvement plus a Luna zero-to-pass clear.
- [ ] Confirm the full run cannot launch before report and freeze, and strict bundling cannot succeed before 270 trace-complete semantics.
- [ ] Run the complete code gate one final time:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
git diff --check
```
