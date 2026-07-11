# Native Codex MCP DAB Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-07-11-native-codex-mcp-dab-design.md` — authoritative; read it in full before Task 0.

**Branch:** `feat/codex-caching-gpt56` at design commit `2307b03`.

**Goal:** Add a policy-certified, trace-complete `codex-mcp` DataAgentBench driver in which native Codex CLI owns the model/tool loop while LabRat owns the task, restricted data surface, immutable run contract, retries, telemetry, scoring, taint gate, and bundle.

**Architecture:** A shared versioned tool-profile resolver feeds both the in-process registry and MCP policy. A generic fail-closed MCP policy authorizes every call before dispatch. DAB creates one append-only attempt directory and immutable experiment manifest before invoking an isolated, pinned Codex CLI. The native JSONL trace and private-rollout usage are reconciled with the canonical MCP trace before any validator runs. Strict bundling is possible only for a submission-eligible host.

**Tech stack:** Python 3.12, Pydantic v2, MCP, SQLGlot, DuckDB, Polars, Codex CLI `0.144.1`, JSONL, pytest, ruff, pyright strict.

## Non-Negotiable Execution Rules

- Preserve the approved spec exactly. Do not silently reduce `dab-core-v1`, weaken a denial, omit an artifact, or call a diagnostic run submission-ready.
- Preserve all existing dirty GPT-5.6/cache/usage/retry/trace work. Task 0 checkpoints it before native-runtime edits begin.
- Use TDD for every behavior: add the focused failing test, run it and observe the expected failure, implement the smallest passing change, refactor only while green, then run the task gate.
- Do not invoke Codex, Claude, OpenAI, Anthropic, Postgres, or Mongo during unit tests. Subprocesses, clocks, network clients, filesystems, and CLI output are injected or fixture-backed.
- This runtime plan makes no paid/subscription model probe. The isolation plan owns live
  canaries, and it runs them only after runtime, isolation, and experiment-controller
  implementation commits are complete and a clean commit is ready to certify.
- A policy denial, malformed/unknown native event, trace disagreement, isolation-canary hit, or missing semantic artifact is `audit-error`, not `infra:*` and not a semantic fail.
- Infrastructure attempts and their artifacts are append-only. Never overwrite an earlier attempt directory or remove an earlier `trials.jsonl` row.
- Keep intermediate verification task-scoped: run the focused tests named by the task,
  format/check only edited Python paths, run Pyright on the edited source package, and
  run `git diff --check`. Run the repository-wide regression gate once at the final
  implementation/certification boundary, not after every small task:

```bash
cd /Users/ege/repos/labrat
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
git diff --check
```

## Locked File Map

| File | Responsibility |
|---|---|
| `src/labrat/eval/benchmarks/dab/tool_profiles.py` | Versioned tool lists, registry filtering, canonical schema hash |
| `src/labrat/mcp/policy.py` | Generic immutable policy schema, digest, load, discovery and call authorization |
| `src/labrat/mcp/sql_policy.py` | SQL AST, relation, database, identifier, row-limit authorization |
| `src/labrat/mcp/mongo_policy.py` | Exact Mongo target and non-JavaScript filter authorization |
| `src/labrat/eval/benchmarks/dab/policy.py` | Trusted `DabTaskEnv` to scrubbed MCP policy |
| `src/labrat/eval/benchmarks/dab/attempts.py` | Append-only attempt identities, directories, artifact contracts |
| `src/labrat/eval/benchmarks/dab/manifest.py` | Immutable experiment manifest, fingerprints, resume diff, legacy sidecar |
| `src/labrat/eval/benchmarks/dab/codex_events.py` | Pinned native JSONL parsing, event audit, MCP reconciliation |
| `src/labrat/eval/benchmarks/dab/codex_usage.py` | Matching-rollout lookup and deduplicated request usage |
| `src/labrat/eval/benchmarks/dab/codex_host.py` | Command/env/version/model validation and subprocess lifecycle |
| `src/labrat/eval/benchmarks/dab/bundle.py` | Tracked strict trace-bundle implementation |
| `src/labrat/mcp/server.py` | Optional policy load, filtered discovery, pre-dispatch denial |
| `src/labrat/eval/benchmarks/dab/env.py` | Trusted source resolution and server-side pre-attachment |
| `src/labrat/eval/benchmarks/dab/suite.py` | `codex-mcp` orchestration and pre-score audit gate |
| `src/labrat/eval/benchmarks/dab/taint.py` | Attempt-aware four-artifact audit contract |
| `scripts/eval_dab.py` | CLI, manifest-before-call guard, append-only runner stop codes |
| `scripts/build_dab_trace_bundle.py` | Public tracked bundle CLI |
| `scripts/dab_manifest.py` | Manifest inspection and legacy sidecar CLI |

---

### Task 0: Checkpoint the existing GPT-5.6/cache work and promote the bundler

**Files:** Existing dirty files only; rename `scripts/_build_dab_trace_bundle.py` to `scripts/build_dab_trace_bundle.py`; modify `.gitignore`, `tests/unit/test_dab_trace_bundle.py`, and the dirty DAB docs that name the old script.

- [x] **Step 1: Capture and verify the exact starting state without changing it.**

```bash
cd /Users/ege/repos/labrat
git status --short --branch
git diff --check
uv run pytest -q tests/unit/test_codex_subscription_provider.py tests/unit/test_dab_suite_run_trial.py tests/unit/test_eval_dab_runner.py tests/unit/test_dab_taint.py tests/unit/test_dab_trace_bundle.py
```

Expected: the branch is `feat/codex-caching-gpt56`; the focused foundation tests pass. Fix only failures caused by the already-present GPT-5.6/cache work.

- [x] **Step 2: Promote the ignored trace bundler without rewriting it.**

```bash
mv scripts/_build_dab_trace_bundle.py scripts/build_dab_trace_bundle.py
```

Update imports to `from scripts.build_dab_trace_bundle import ...`, update `docs/dab-integration.md`, and remove only the `scripts/_build_dab_trace_bundle.py` ignore entry and its obsolete comment from `.gitignore`.

- [x] **Step 3: Run the promoted entrypoint tests.**

```bash
uv run pytest -q tests/unit/test_dab_trace_bundle.py tests/unit/test_dab_taint.py tests/unit/test_eval_dab_runner.py
uv run python scripts/build_dab_trace_bundle.py --help
```

Expected: all tests pass and the CLI exits zero without building a bundle.

- [x] **Step 4: Run the focused checkpoint gate and commit the current foundation once.** Stage the existing modified provider/loop/verifier, DAB runner/suite/taint, their tests, `docs/codex-caching-investigation.md`, `docs/dab-solultra-ablation.md`, the promoted script, and `.gitignore`; do not stage unrelated files.

```bash
git add .gitignore docs/dab-integration.md docs/codex-caching-investigation.md docs/dab-solultra-ablation.md scripts/build_dab_trace_bundle.py scripts/eval_dab.py src/labrat/agent/loop.py src/labrat/agent/providers/__init__.py src/labrat/agent/providers/base.py src/labrat/agent/providers/codex_subscription.py src/labrat/agent/verifier.py src/labrat/eval/benchmarks/dab/suite.py src/labrat/eval/benchmarks/dab/taint.py tests/unit/test_claude_mcp_prompt.py tests/unit/test_codex_subscription_provider.py tests/unit/test_dab_prompt_levers.py tests/unit/test_dab_suite_run_trial.py tests/unit/test_dab_taint.py tests/unit/test_dab_trace_bundle.py tests/unit/test_eval_dab_runner.py
git commit -m "feat(codex): checkpoint GPT-5.6 cache and DAB trace foundations"
```

Commit boundary: the worktree is clean after this commit; subsequent tasks never rewrite or revert this foundation.

---

### Task 1: Shared versioned DAB tool profiles

**Files:** Create `src/labrat/eval/benchmarks/dab/tool_profiles.py`; create `tests/unit/test_dab_tool_profiles.py`.

**Interfaces:**

```python
ToolProfileName = Literal["dab-core-v1", "legacy-full-20260710"]

@dataclass(frozen=True)
class ResolvedToolProfile:
    name: ToolProfileName
    tools: tuple[str, ...]
    canonical_schemas: tuple[dict[str, Any], ...]
    schema_sha256: str

@dataclass(frozen=True)
class TaskToolContract:
    task_id: str
    profile_name: str
    tools: tuple[str, ...]
    schema_sha256: str

def resolve_tool_profile(
    name: ToolProfileName,
    registry: ToolRegistry,
    *,
    cartographer: bool = False,
    mongo: bool = False,
) -> ResolvedToolProfile: ...

def filter_registry(registry: ToolRegistry, profile: ResolvedToolProfile) -> ToolRegistry: ...
def resolve_task_tool_contract(
    task_id: str,
    profile: ResolvedToolProfile,
    *,
    cartographer: bool,
    mongo: bool,
) -> TaskToolContract: ...
```

`dab-core-v1` is exactly the 13 tools in spec section 7.1. `cartographer=True` appends only `search_reference_docs`; `mongo=True` appends only `load_mongo_collection`. `legacy-full-20260710` is exactly the frozen 22-tool list in section 11 and rejects conditional additions. The run manifest stores one `TaskToolContract` per selected task; a single run-level tool tuple is insufficient because Mongo and Cartographer are task-scoped.

- [ ] **Step 1 RED:** Test exact names/order, missing-global-tool failure, no hidden/self-erroring tools, deterministic canonical JSON hash, conditional additions, the exact legacy list, and identical filtered in-process/MCP schemas.
- [ ] **Step 2:** Run `uv run pytest -q tests/unit/test_dab_tool_profiles.py`; expected collection failure because the module does not exist.
- [ ] **Step 3 GREEN:** Implement the constants and functions. Canonical JSON uses `json.dumps(value, sort_keys=True, separators=(",", ":"))`; fail if a requested tool is absent or duplicated.
- [ ] **Step 4 REFACTOR:** Keep this module independent of `DabSuite`, policy, and CLI; rerun `uv run pytest -q tests/unit/test_dab_tool_profiles.py`.
- [ ] **Step 5:** Run the focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/tool_profiles.py tests/unit/test_dab_tool_profiles.py
git commit -m "feat(dab): add versioned shared tool profiles"
```

---

### Task 2: Generic fail-closed MCP policy and server seam

**Files:** Create `src/labrat/mcp/policy.py`; modify `src/labrat/mcp/server.py`; create `tests/unit/test_mcp_policy.py`; extend `tests/unit/test_mcp_server.py`.

**Interfaces:**

```python
class PolicyLoadError(RuntimeError): ...
class PolicyDenied(RuntimeError): ...

class McpPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1]
    run_manifest_sha256: str
    task_id: str
    trial_num: int
    attempt_num: int
    primary_database: str
    allowed_tools: tuple[str, ...]
    source_grants: tuple[SourceGrant, ...]
    mongo_grants: tuple[MongoGrant, ...]
    limits: PolicyLimits
    cartographer_enabled: bool
    builder_sha256: str
    digest: str

def canonical_policy_bytes(policy: McpPolicy, *, include_digest: bool = False) -> bytes: ...
def policy_digest(policy: McpPolicy) -> str: ...
def load_policy_from_env(env: Mapping[str, str]) -> McpPolicy | None: ...

class PolicySession:
    def visible_tools(self, registry: ToolRegistry) -> list[Tool[Any]]: ...
    def authorize(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> None: ...
    def record_success(self, name: str, arguments: dict[str, Any]) -> None: ...
```

Add module-level server helpers so tests do not reach into MCP internals:

```python
def _listed_tools(registry: ToolRegistry, policy: PolicySession | None) -> list[mcp.types.Tool]: ...
async def _dispatch_tool_call(
    name: str,
    arguments: dict[str, Any],
    *,
    ctx: ToolContext,
    registry: ToolRegistry,
    policy: PolicySession | None,
    log_dir: str | None,
) -> list[TextContent]: ...
```

- [ ] **Step 1 RED:** Cover absent policy returning `None`; set-but-missing path; malformed JSON; unknown field; unsupported schema; digest mismatch; exact discovery; hidden-tool direct denial; and denial logged before registry dispatch.
- [ ] **Step 2:** Run `uv run pytest -q tests/unit/test_mcp_policy.py tests/unit/test_mcp_server.py`; expected import/test failures.
- [ ] **Step 3 GREEN:** Load `LABRAT_MCP_POLICY_PATH` only when set. A set path fails startup on every error. `_serve()` loads policy before opening stdio. Under policy, denial logs `ok=false` and raises an MCP error; without policy, existing discovery/dispatch behavior is unchanged.
- [ ] **Step 4 REFACTOR:** Keep DAB names and paths out of the generic policy module. Run `uv run pytest -q tests/unit/test_mcp_policy.py tests/unit/test_mcp_server.py tests/unit/test_mcp_config.py`.
- [ ] **Step 5:** Focused task gate and commit:

```bash
git add src/labrat/mcp/policy.py src/labrat/mcp/server.py tests/unit/test_mcp_policy.py tests/unit/test_mcp_server.py
git commit -m "feat(mcp): enforce optional fail-closed tool policy"
```

---

### Task 3: SQL, relation, database, identifier, and helper authorization

**Files:** Create `src/labrat/mcp/sql_policy.py`; create `tests/unit/test_mcp_sql_policy.py`; extend `src/labrat/mcp/policy.py` and `tests/unit/test_mcp_policy.py`.

**Interfaces:**

```python
@dataclass(frozen=True)
class RelationRef:
    database: str | None
    schema: str | None
    table: str

def validate_identifier(value: str, *, label: str) -> str: ...
def authorize_relation(value: str, grant: SourceGrant) -> RelationRef: ...
def authorize_column(table: RelationRef, column: str, grant: SourceGrant) -> None: ...
def authorize_sql(
    query: str,
    *,
    database: str,
    grants: tuple[SourceGrant, ...],
    force: bool,
    requested_limit: int,
    maximum_limit: int,
) -> None: ...
```

Use `sqlglot.parse(query, read="duckdb")`; require exactly one non-null statement with a `Select`, `Union`, `Intersect`, or `Except` root. Every `exp.Table.this` must be an `exp.Identifier`. Reject command/DDL/DML nodes, embedded writes, `PRAGMA`, `COPY`, `ATTACH`, `INSTALL`, `LOAD`, `force=True`, dynamic/excessive limits, unknown qualifiers/relations, and all external readers/scanners including `read_*`, `glob`, `sqlite_scan`, `postgres_scan`, `query`, secrets, and settings access.

`PolicySession.authorize` must explicitly cover all 13 core tools. It validates database selection for every tool, relation/column identifiers for structured helpers, SQL for `run_sql`, `explain_sql`, `check_sql`, and `explain_lineage`, and policy ceilings for rows/tables/output/top-k. An uncovered allowed tool is a denial, never a pass-through.

- [ ] **Step 1 RED:** Parameterize safe SELECT/CTE/window/set/join/attached-alias cases and every denial family in spec 7.3. Add injection tests for `sample_rows`, `column_stats`, `verify_join`, `describe_table`, and database arguments.
- [ ] **Step 2:** Run `uv run pytest -q tests/unit/test_mcp_sql_policy.py tests/unit/test_mcp_policy.py`; expected failures.
- [ ] **Step 3 GREEN:** Implement only AST and explicit structured-argument checks; do not use substring-only SQL authorization.
- [ ] **Step 4 REFACTOR:** Run `uv run pytest -q tests/unit/test_mcp_sql_policy.py tests/unit/test_mcp_policy.py`, then rely on the task's full-suite gate for existing tool regressions.
- [ ] **Step 5:** Focused task gate and commit:

```bash
git add src/labrat/mcp/sql_policy.py src/labrat/mcp/policy.py tests/unit/test_mcp_sql_policy.py tests/unit/test_mcp_policy.py
git commit -m "feat(mcp): authorize SQL sources and helper identifiers"
```

---

### Task 4: Mongo authorization, trusted source preparation, and DAB policy builder

**Files:** Create `src/labrat/mcp/mongo_policy.py`, `src/labrat/eval/benchmarks/dab/policy.py`, `tests/unit/test_mcp_mongo_policy.py`, `tests/unit/test_dab_policy.py`; modify `src/labrat/eval/benchmarks/dab/env.py`; extend `tests/unit/test_dab_env.py`.

**Interfaces:**

```python
def authorize_mongo_call(arguments: dict[str, Any], grant: MongoGrant, limits: PolicyLimits) -> None: ...
def declared_dab_sources(env: DabTaskEnv) -> DeclaredDabSources: ...
def verify_prepared_catalog(
    declared: DeclaredDabSources,
    prepared: PreparedSourceCatalog,
) -> AuthorizedSourceCatalog: ...
def build_dab_policy(
    *,
    run_manifest_sha256: str,
    task_id: str,
    trial_num: int,
    attempt_num: int,
    sources: AuthorizedSourceCatalog,
    tool_contract: TaskToolContract,
    limits: PolicyLimits,
    cartographer_enabled: bool,
) -> McpPolicy: ...
def write_scrubbed_policy(path: Path, policy: McpPolicy) -> None: ...
```

`declared_dab_sources` derives aliases and Mongo collection names only from trusted task declarations and selected BSON filenames; it never expands authority by enumerating a live server. The isolation plan starts the MCP sidecar, prepares only those declared sources using its MCP-only secret channel, and returns a scrubbed `PreparedSourceCatalog`. `verify_prepared_catalog` checks that live relations are a subset of the declarations before `build_dab_policy` can run. The policy binds the run-manifest digest plus task, trial, and attempt identity. It contains aliases and identifiers only: never file paths, DSNs, credentials, Mongo URL, DAB checkout path, or validator path. Mongo target tables use the deterministic `mongo_{alias}_{collection}` naming rule and become SQL-authorized only after a successful materialization.

Mongo allows JSON scalar/list/object values and this explicit operator set: `$and`, `$or`, `$nor`, `$not`, `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`, `$exists`, `$type`, `$regex`, `$options`, `$size`, `$all`, `$elemMatch`. Reject every other `$` operator, especially `$where`, `$function`, and `$accumulator`.

- [ ] **Step 1 RED:** Test other DB/collection, unsafe target, wrong primary, excessive/absent limit, JavaScript operators at any depth, and path/credential leakage. Test declared-versus-prepared catalog mismatch, run/trial/attempt identity changes, successful trusted SQLite/Postgres preparation with fakes, and deterministic policy digest.
- [ ] **Step 2:** Run `uv run pytest -q tests/unit/test_mcp_mongo_policy.py tests/unit/test_dab_policy.py tests/unit/test_dab_env.py`; expected failures.
- [ ] **Step 3 GREEN:** Implement the resolver and builder. Policy generation fails closed if an exact source catalog cannot be established.
- [ ] **Step 4 REFACTOR:** Verify `McpPolicy.model_dump_json()` contains none of `/Users/`, `host=`, `password`, `mongodb://`, `validate.py`, or `ground_truth`.
- [ ] **Step 5:** Focused task gate and commit:

```bash
git add src/labrat/mcp/mongo_policy.py src/labrat/eval/benchmarks/dab/policy.py src/labrat/eval/benchmarks/dab/env.py tests/unit/test_mcp_mongo_policy.py tests/unit/test_dab_policy.py tests/unit/test_dab_env.py
git commit -m "feat(dab): build scrubbed task-scoped MCP policies"
```

---

### Task 5: Append-only attempt lifecycle

**Files:** Create `src/labrat/eval/benchmarks/dab/attempts.py`, `tests/unit/test_dab_attempts.py`; modify `scripts/eval_dab.py`; extend `tests/unit/test_eval_dab_runner.py`.

**Interfaces:**

```python
@dataclass(frozen=True)
class AttemptRef:
    task_id: str
    trial_num: int
    attempt_num: int
    directory: Path

class AttemptManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["dab-attempt-v1"]
    run_manifest_sha256: str
    task_id: str
    trial_num: int
    attempt_num: int
    driver: str
    stages_reached: tuple[str, ...]
    terminal_state: Literal["infra", "audit-error", "semantic"] | None
    certification_record_sha256: str | None
    artifacts: tuple[AttemptArtifact, ...]
    sha256: str

def load_attempt_rows(trials_jsonl: Path) -> list[TrialAttempt]: ...
def next_attempt(run_dir: Path, task_id: str, trial_num: int) -> AttemptRef: ...
def initialize_attempt(ref: AttemptRef, driver: str) -> None: ...
def finalize_attempt_manifest(ref: AttemptRef, manifest: AttemptManifest) -> None: ...
def selected_semantic_attempts(rows: Iterable[TrialAttempt]) -> dict[tuple[str, int], TrialAttempt]: ...
```

Directory contract: a filesystem-safe task ID, zero-based trial number, and four-digit attempt number; for example, `scratch/stockindex_1__trial0/attempt-0001/`. Initialization uses exclusive directory creation and never truncates an existing attempt. For `codex-mcp`, it creates empty `mcp_tool_calls.jsonl`, `codex_events.jsonl`, and `codex_token_usage.jsonl`; `mcp_policy.json` must be written before launch. `attempt_manifest.json` records which terminal stages were actually reached plus hashes for present artifacts, so a valid early-infrastructure attempt is distinguishable from missing evidence. Each row records `meta.attempt_num`, `meta.attempt_dir`, and the attempt-manifest digest. Older rows without these fields retain the legacy root-trace fallback.

- [ ] **Step 1 RED:** Add tests proving retry directories are distinct, a collision fails instead of truncating, earlier bytes remain unchanged, terminal stages distinguish valid empty infra artifacts from missing evidence, corrupted rows fail closed, exactly one semantic attempt is selected, infra rows remain retryable, first 429 flushes then stops, and `audit-error` flushes then stops without launching the next key.
- [ ] **Step 2:** Run `uv run pytest -q tests/unit/test_dab_attempts.py tests/unit/test_eval_dab_runner.py`; expected failures.
- [ ] **Step 3 GREEN:** Route `_run_interim` through `next_attempt`; add `_AuditStopError` and exit code `5` alongside existing rate-limit exit code `4`.
- [ ] **Step 4 REFACTOR:** Correct the stale `_load_completed_trials` docstring: infra rows are retained and retried, never rewritten in place.
- [ ] **Step 5:** Focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/attempts.py scripts/eval_dab.py tests/unit/test_dab_attempts.py tests/unit/test_eval_dab_runner.py
git commit -m "feat(dab): retain append-only attempt artifacts"
```

---

### Task 6: Immutable experiment manifests and the legacy baseline sidecar

**Files:** Create `src/labrat/eval/benchmarks/dab/manifest.py`, `scripts/dab_manifest.py`, `tests/unit/test_dab_manifest.py`; modify `scripts/eval_dab.py`; extend `tests/unit/test_eval_dab_runner.py`.

**Interfaces:**

```python
RunClass = Literal["diagnostic", "registered", "official", "legacy"]
HostEligibility = Literal["diagnostic-only", "policy-certified", "submission-eligible"]

class ExperimentManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1]
    run_class: RunClass
    dab_commit: str
    selected_task_sha256: str
    labrat_commit: str
    labrat_dirty_sha256: str | None
    codex_cli_version: str | None
    model: str
    reasoning_effort: str | None
    driver: str
    host_eligibility: HostEligibility
    certification_contract_sha256: str | None
    tool_profile: str
    task_tool_contracts: tuple[TaskToolContract, ...]
    policy_schema_version: int
    policy_builder_sha256: str
    prompt_flags: dict[str, bool]
    prompt_sha256: str
    isolation_sha256: str
    trace_schema_version: int
    attempt_reset_policy: Literal["new-directory-per-attempt"]
    task_filter: tuple[str, ...]
    expected_semantic_trials: int

def build_selected_task_digest(tasks: Iterable[BenchmarkTask]) -> str: ...
def build_live_manifest(...) -> ExperimentManifest: ...
def create_or_validate_manifest(run_dir: Path, live: ExperimentManifest) -> ExperimentManifest: ...
def create_legacy_sidecar(run_dir: Path, dab_dir: Path) -> ExperimentManifest: ...
```

The task digest hashes ordered task IDs, prompts, DB config bytes, hint bytes, and validator bytes, never path strings or ground truth. Resume recursively compares every manifest field and reports dotted conflicts. `host_eligibility` is derived from a verified certification artifact, never accepted from a CLI string; diagnostic runs without certification are `diagnostic-only`. `registered` and `official` require a clean LabRat tree and a matching certification-contract digest when native isolation is required. A time-only refresh is accepted only when the contract digest is identical, and every attempt records the exact certification-record SHA it used. `diagnostic` records the dirty diff digest. Manifest creation uses exclusive create and happens before the first model call.

- [ ] **Step 1 RED:** Test stable hashes, prompt/config/validator/tool/policy/CLI/isolation/task-order drift, precise conflict text, clean-tree gating, no model invocation after conflict, and exclusive creation.
- [ ] **Step 2:** Run `uv run pytest -q tests/unit/test_dab_manifest.py tests/unit/test_eval_dab_runner.py`; expected failures.
- [ ] **Step 3 GREEN:** Add CLI flags `--run-class`, `--tool-profile`, and `--certification`; derive eligibility from the verified record, restore the certification-contract digest from the manifest on resume, and reject changed contracts before rewriting `config.json`.
- [ ] **Step 4:** Implement `dab_manifest.py legacy-sidecar`. It accepts only the fixed legacy run directory, recovers any recorded DAB identity from authoritative run evidence, and verifies exact selected prompts/configs/hints/validators and `legacy-full-20260710` schemas before adoption. It must not trust an arbitrary checkout merely because an operator supplied its path. In its test, hash `config.json` and `trials.jsonl` before/after and assert byte identity; if the historical fingerprint cannot be proven, fail without changing the run.
- [ ] **Step 5 REFACTOR:** Run:

```bash
uv run python scripts/dab_manifest.py --help
uv run pytest -q tests/unit/test_dab_manifest.py tests/unit/test_eval_dab_runner.py
```

- [ ] **Step 6:** Focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/manifest.py scripts/dab_manifest.py scripts/eval_dab.py tests/unit/test_dab_manifest.py tests/unit/test_eval_dab_runner.py
git commit -m "feat(dab): freeze experiment manifests and legacy sidecars"
```

After the commit, create the real sidecar without a model call:

```bash
uv run python scripts/dab_manifest.py legacy-sidecar --run-dir runs/dab/ablation-gpt56-luna-max-baseline --dab-dir /Users/ege/repos/DataAgentBench
```

If that checkout path does not exist, use the configured `DAB_DIR` value; do not invent a new DAB snapshot. The command must either create a valid sidecar or make no run-directory changes.

---

### Task 7: Pinned native Codex event parser and trace reconciliation

**Files:** Create `src/labrat/eval/benchmarks/dab/codex_events.py`, `tests/unit/test_dab_codex_events.py`, and JSON fixtures under `tests/fixtures/codex_exec/`.

**Interfaces:**

```python
class CodexEventAuditError(RuntimeError): ...

@dataclass(frozen=True)
class NativeMcpCall:
    server: str
    tool: str
    arguments: dict[str, Any]
    status: Literal["completed", "failed"]

@dataclass(frozen=True)
class CodexTrace:
    thread_id: str
    final_text: str
    usage: dict[str, int]
    mcp_calls: tuple[NativeMcpCall, ...]

def parse_codex_events(lines: Iterable[str], *, enabled_tools: tuple[str, ...]) -> CodexTrace: ...
def reconcile_mcp_trace(native: CodexTrace, server_trace: Path) -> None: ...
```

Pin CLI `0.144.1`'s JSONL contract: `thread.started`, `turn.started`, `item.started|updated|completed`, `turn.completed`, `turn.failed`, and `error`; permitted item types are only `reasoning`, `agent_message`, and `mcp_tool_call` for server `labrat`. Reject command execution, file change, web search, collaboration, unknown item/event, malformed JSON, duplicate terminal state, incomplete calls, and missing terminal turn. Final text is the last completed agent message. Aggregate usage is the terminal `turn.completed.usage`.

Reconciliation compares completed calls one-for-one by order, tool, canonical arguments, and status. A policy denial must be failed in both traces. Do not compare model-visible output text, which may be transport-normalized.

- [ ] **Step 1 RED:** Create one clean zero-tool fixture, one clean two-call fixture, and focused malformed/forbidden/mismatch fixtures; assert every failure is `CodexEventAuditError`.
- [ ] **Step 2:** Run `uv run pytest -q tests/unit/test_dab_codex_events.py`; expected import failure.
- [ ] **Step 3 GREEN:** Implement a strict parser with explicit event/item match arms; no unknown-event ignore branch.
- [ ] **Step 4 REFACTOR:** Ensure fixture payloads contain no real prompts, paths, tokens, or account data.
- [ ] **Step 5:** Focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/codex_events.py tests/unit/test_dab_codex_events.py tests/fixtures/codex_exec
git commit -m "feat(dab): audit native Codex events and reconcile MCP traces"
```

---

### Task 8: Private-rollout request usage extraction

**Files:** Create `src/labrat/eval/benchmarks/dab/codex_usage.py`, `tests/unit/test_dab_codex_usage.py`, and scrubbed rollout fixtures under `tests/fixtures/codex_rollout/`.

**Interfaces:**

```python
class CodexUsageError(RuntimeError): ...

@dataclass(frozen=True)
class RequestUsage:
    request_index: int
    input_tokens: int
    cached_input_tokens: int
    noncached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int

def find_matching_rollout(codex_home: Path, thread_id: str) -> Path: ...
def extract_request_usage(rollout: Path, thread_id: str) -> tuple[RequestUsage, ...]: ...
def write_scrubbed_request_usage(path: Path, usage: tuple[RequestUsage, ...]) -> None: ...
```

Match exactly one rollout whose `session_meta.payload.id` equals the native `thread_id`. Read only `event_msg` records with `payload.type == "token_count"`. Deduplicate exact repeated cumulative `total_token_usage` snapshots; require nondecreasing totals and consistent nonnegative `last_token_usage`; compute `noncached_input_tokens = input_tokens - cached_input_tokens`. Never copy raw rollout, config, history, auth, rate-limit account data, or prompt content.

- [ ] **Step 1 RED:** Test no/multiple/wrong rollout, malformed session metadata, exact duplicate removal, regressing cumulative totals, cached greater than input, and scrubbed JSONL shape.
- [ ] **Step 2:** Run `uv run pytest -q tests/unit/test_dab_codex_usage.py`; expected import failure.
- [ ] **Step 3 GREEN:** Implement date-sharded recursive lookup with regular-file checks and strict numeric validation.
- [ ] **Step 4 REFACTOR:** Scan fixture/output keys and prove only the six `RequestUsage` fields are emitted.
- [ ] **Step 5:** Focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/codex_usage.py tests/unit/test_dab_codex_usage.py tests/fixtures/codex_rollout
git commit -m "feat(dab): extract scrubbed Codex request cache usage"
```

---

### Task 9: Native Codex host adapter

**Files:** Create `src/labrat/eval/benchmarks/dab/codex_host.py`, `tests/unit/test_dab_codex_host.py`.

**Interfaces:**

```python
class CodexInfrastructureError(RuntimeError):
    reason: Literal["auth", "transport", "timeout", "process", "rate_limit"]
    meta: dict[str, Any]

@dataclass(frozen=True)
class CodexHostConfig:
    executable: Path
    expected_version: Literal["0.144.1"]
    model: Literal["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max", "ultra"]
    codex_home: Path
    scratch_dir: Path
    policy_path: Path
    enabled_tools: tuple[str, ...]
    mcp_bridge_command: tuple[str, ...]
    timeout_seconds: int

@dataclass(frozen=True)
class NativeRunResult:
    final_text: str
    tool_calls: int
    latency_seconds: float
    thread_id: str
    usage: dict[str, int]
    request_usage: tuple[RequestUsage, ...]

def build_codex_command(config: CodexHostConfig) -> list[str]: ...
def build_codex_environment(parent: Mapping[str, str], config: CodexHostConfig) -> dict[str, str]: ...
async def run_codex(prompt: str, config: CodexHostConfig) -> NativeRunResult: ...
```

The exact command begins:

```text
codex -a never exec --json --strict-config --ignore-user-config --ignore-rules --skip-git-repo-check -C ABSOLUTE_SCRATCH -s read-only -m MODEL
```

Pass prompt through stdin using final argument `-`; never use `--ephemeral`. Add explicit `-c` overrides for `model_reasoning_effort`, `web_search="disabled"`, required `mcp_servers.labrat`, its injected argument-list `mcp_bridge_command`, and the task's exact `enabled_tools`. In diagnostics the bridge may launch a local stdio server; submission isolation supplies a data-blind bridge to the private MCP sidecar. Disable `shell_tool`, `unified_exec`, apps, plugins/remote plugins, browser variants, computer use, image generation, workspace dependencies, skill dependency installation, hooks, memories, goals, and multi-agent. Remove `OPENAI_API_KEY` and `CODEX_API_KEY`; set only the fresh per-attempt `CODEX_HOME` plus a minimal process environment. Luna rejects `ultra`; Terra/Sol accept it. Host subagents remain disabled even under Sol Ultra.

- [ ] **Step 1 RED:** With an injected fake process runner, test exact argv ordering, absolute paths, no dangerous-bypass/ephemeral flag, env scrubbing, version mismatch, model/effort pairs, timeout termination, nonzero exit, 429/reset parsing, auth/transport classification, complete event capture, usage extraction, and four preinitialized artifacts.
- [ ] **Step 2:** Run `uv run pytest -q tests/unit/test_dab_codex_host.py`; expected import failure.
- [ ] **Step 3 GREEN:** Implement command construction as a pure function and subprocess execution with `asyncio.create_subprocess_exec`; stream stdout bytes verbatim into `codex_events.jsonl`, keep stderr sanitized and bounded, and parse only after process completion.
- [ ] **Step 4 REFACTOR:** Run `codex --version`, `codex exec --help`, and `codex debug models --bundled` only; these are no-model checks. Assert local version and model efforts match the pinned contract.
- [ ] **Step 5:** Focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/codex_host.py tests/unit/test_dab_codex_host.py
git commit -m "feat(dab): add pinned native Codex MCP host adapter"
```

---

### Task 10: `DabSuite` and CLI integration

**Files:** Modify `src/labrat/eval/benchmarks/dab/suite.py`, `scripts/eval_dab.py`; extend `tests/unit/test_dab_suite_run_trial.py`, `tests/unit/test_eval_dab_runner.py`, `tests/unit/test_dab_prompt_levers.py`.

**Required edits:**

- Extend `Driver` to `Literal["raw-bash", "labrat-agent", "claude-mcp", "codex-mcp"]`.
- Add `tool_profile`, verified `certification`, and injectable `codex_runner` arguments to `DabSuite.__init__`.
- Add `_build_codex_mcp_prompt(...)` that exposes aliases, allowed collections/targets, tool discipline, question, and flags but no source paths/DSNs.
- Add `_run_trial_codex_mcp(...) -> tuple[str, int, float]` and dispatch it in `_dispatch_driver_once`.
- Filter `labrat-agent` through the same resolved profile so the host A/B has identical names and schema hash.
- Map `NativeRunResult.usage` and request usage into the existing `_last_usage`/`_last_request_usage` seam.
- Run native-event audit and MCP reconciliation before `score_with_validator`; translate failures to `reason="audit-error"` and pause the runner.
- Add CLI choices for `codex-mcp`, `--run-class`, `--tool-profile`, and `--certification`; default new `codex-mcp` diagnostics to Luna Max. There is no `--host-eligibility` override. Verify the certification artifact and derive eligibility, then create/validate the manifest after tasks are resolved and before `run_trial` can execute.

- [ ] **Step 1 RED:** Test dispatch, safe prompt, exact per-task profile parity, usage propagation, no validator on audit failure, rejection of a caller-supplied eligibility claim, certification-digest mismatch, rate-limit exit `4`, audit exit `5`, resume conflict before runner invocation, and zero-tool semantic success with complete empty traces.
- [ ] **Step 2:** Run `uv run pytest -q tests/unit/test_dab_suite_run_trial.py tests/unit/test_eval_dab_runner.py tests/unit/test_dab_prompt_levers.py`; expected failures.
- [ ] **Step 3 GREEN:** Implement the narrow driver seam. Do not modify `CodexSubscriptionProvider` or make ContextLedger appear active under MCP.
- [ ] **Step 4 REFACTOR:** Run all DAB unit tests and prove existing driver defaults/config resumes remain compatible.
- [ ] **Step 5:** Focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/suite.py scripts/eval_dab.py tests/unit/test_dab_suite_run_trial.py tests/unit/test_eval_dab_runner.py tests/unit/test_dab_prompt_levers.py
git commit -m "feat(dab): integrate codex-mcp driver and immutable resume guard"
```

---

### Task 11: Attempt-aware taint audit and strict native bundle

**Files:** Create `src/labrat/eval/benchmarks/dab/bundle.py`; reduce `scripts/build_dab_trace_bundle.py` to a CLI wrapper; modify `src/labrat/eval/benchmarks/dab/taint.py`; extend `tests/unit/test_dab_taint.py`, `tests/unit/test_dab_trace_bundle.py`.

**Interfaces:**

```python
def required_attempt_artifacts(driver: str) -> tuple[str, ...]: ...
def audit_attempt(row: TrialAttempt, run_dir: Path) -> AttemptAudit: ...
def audit_run(trials_jsonl: Path, scratch_dir: Path) -> dict[str, str]: ...
def build_bundle(
    run_dir: Path,
    *,
    output_dir: Path | None = None,
    strict_official: bool = False,
    force: bool = False,
) -> Path: ...
```

For a selected `codex-mcp` semantic attempt require valid `mcp_tool_calls.jsonl`, `codex_events.jsonl`, `codex_token_usage.jsonl`, and `mcp_policy.json`, plus clean reconciliation and a matching certification digest. For `labrat-agent`, require its canonical agent trace but do not invent native policy/isolation artifacts. Infra attempts may contain empty pre-created files only when their attempt manifest proves the terminal stage was never reached. Bundle every attempt and hash every artifact; identify the one selected semantic attempt. This task implements driver-specific artifact validation and diagnostic bundling. The experiment plan owns the campaign/freeze/exact-270 strict-official gate.

- [ ] **Step 1 RED:** Extend tests for selected-attempt paths, retained infra artifacts, missing/malformed policy/native/usage files, native contamination, trace mismatch, certification mismatch, driver-specific agent/native requirements, diagnostic strict rejection, and bundle scans rejecting auth/token/home/validator/GT leakage.
- [ ] **Step 2:** Run `uv run pytest -q tests/unit/test_dab_taint.py tests/unit/test_dab_trace_bundle.py`; expected failures.
- [ ] **Step 3 GREEN:** Move reusable bundler logic into the package; wrapper imports `main`. Preserve legacy `labrat-agent` and `claude-mcp` contracts.
- [ ] **Step 4 REFACTOR:** Build a fixture bundle twice and assert deterministic manifest hashes; ensure raw private rollout files are never opened by the bundler.
- [ ] **Step 5:** Focused task gate and commit:

```bash
git add src/labrat/eval/benchmarks/dab/bundle.py src/labrat/eval/benchmarks/dab/taint.py scripts/build_dab_trace_bundle.py tests/unit/test_dab_taint.py tests/unit/test_dab_trace_bundle.py
git commit -m "feat(dab): bundle four-artifact native traces fail-closed"
```

---

### Task 12: Runtime documentation, no-model gate, and isolation handoff

**Files:** Modify `docs/dab-integration.md`, `docs/codex-caching-investigation.md`,
`docs/dab-solultra-ablation.md`, `CLAUDE.md`, and `decisions.md`.

- [ ] **Step 1:** Document `codex-mcp`, the exact restricted profile, per-task tool
  contracts, policy identity, append-only attempt manifests, immutable run manifest,
  stop codes `4` and `5`, request-index usage, certification-derived eligibility, and
  driver-specific bundle artifacts. Mark ContextLedger and program/dispatch/LLM tools
  AgentLoop-only and report-only.
- [ ] **Step 2:** Add a dated decision: native Codex begins diagnostic-only; server
  policy is authoritative; `enabled_tools` is defense in depth; eligibility cannot be
  asserted by an operator; and submission eligibility requires the companion
  two-container isolation certification.
- [ ] **Step 3:** Run the focused runtime/DAB tests from Tasks 1–11, then the single
  repository-wide implementation gate:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
git diff --check
uv run python scripts/dab_manifest.py verify --run-dir runs/dab/ablation-gpt56-luna-max-baseline --dab-dir /Users/ege/repos/DataAgentBench
```

- [ ] **Step 4:** Prove a diagnostic/native fixture bundle succeeds while
  `--strict-official` fails without a verified certification/campaign/freeze record.
  Do not invoke a model or validator in this task.
- [ ] **Step 5:** Commit the runtime documentation, then hand the exact public
  interfaces and commit SHA to
  `docs/superpowers/plans/2026-07-11-native-codex-mcp-isolation.md`.

```bash
git add docs/dab-integration.md docs/codex-caching-investigation.md docs/dab-solultra-ablation.md CLAUDE.md decisions.md
git commit -m "docs(dab): document native Codex runtime contract"
```

## Completion Audit

Before declaring this plan implemented, verify every item against current artifacts, not intent:

- [ ] Scrubbed native CLI `0.144.1` fixtures prove final-message, aggregate usage,
  request-index usage, canonical MCP trace, policy, and reconciliation behavior without
  a paid call; the isolation plan owns the live Luna-low canary.
- [ ] Exact `dab-core-v1` schemas are shared by `labrat-agent`, policy discovery, and Codex `enabled_tools`.
- [ ] SQL, helper, Mongo, policy-load, unknown-event, and trace-mismatch tests all fail closed; containment remains an explicit isolation-plan prerequisite.
- [ ] No semantic answer is validated or scored before policy/event/reconciliation gates pass.
- [ ] 429 writes one sanitized retryable row and stops; `audit-error` writes one row and stops; every attempt directory remains present.
- [ ] Registered/official resume rejects every manifest drift before a model call.
- [ ] The legacy baseline has a valid sidecar, and its original `config.json`/`trials.jsonl` bytes are unchanged.
- [ ] Diagnostic bundling is driver-specific and strict-official bundling rejects the
  absence of a certification/campaign/freeze record.
- [ ] Runtime prompts, traces, manifests, and fixture bundles contain no secret,
  validator, ground truth, answer key, user memory, unrelated DB, or raw rollout.
- [ ] Full ruff, pyright, pytest, and diff-check gates pass from a clean worktree.

After this audit, implement and certify the companion isolation plan, then follow the experiment plan. No paid experiment begins from this runtime plan alone.
