# Native Codex MCP DAB Whole-Host Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the native `codex-mcp` DataAgentBench path submission-eligible with a resource-bounded two-container isolation envelope: a data-blind Codex worker and a credential-bearing LabRat MCP sidecar on disjoint networks, plus fresh per-attempt Codex state, exact read-only data mounts, executable containment canaries, and a digest-bound eligibility artifact.

**Architecture:** The trusted launcher builds a secret-free worker request and a separate MCP-only bootstrap. It starts an installed LabRat sidecar with exact task files/credentials on a sidecar-data network and no internet, then starts an installed Codex worker with inference-proxy access and no data mounts or data-plane route. An installed data-blind stdio bridge forwards Codex MCP frames over the per-attempt worker/sidecar control network. The worker receives a fresh per-attempt `CODEX_HOME` seeded from a minimal auth-only store; historical rollouts are never reused. Server policy remains authoritative. Dual-container inspection, self-checks, traces, network logs, and containment probes form immutable certification evidence.

**Tech Stack:** Python 3.12, Pydantic v2, Docker Engine/Compose, Codex CLI 0.144.1, DuckDB with build-time SQLite/PostgreSQL extensions, MCP stdio bridge plus private Streamable HTTP sidecar transport, pytest, ruff, pyright.

**Authoritative design:** `docs/superpowers/specs/2026-07-11-native-codex-mcp-dab-design.md`, especially §§6, 8–10, 12.1, 13, and 16.

**Companion plans:**

- Runtime prerequisite: `docs/superpowers/plans/2026-07-11-native-codex-mcp-runtime.md`.
- Experiment consumer: `docs/superpowers/plans/2026-07-11-gpt56-dab-experiments.md`.

## Dependency contract

Tasks 1–4 may proceed alongside the runtime plan. Task 5 waits for these settled runtime seams and imports them directly rather than defining aliases or duplicate models:

```python
# src/labrat/eval/benchmarks/dab/codex_host.py
# CodexHostConfig, NativeRunResult, run_codex
# src/labrat/eval/benchmarks/dab/policy.py
# McpPolicy, write_scrubbed_policy, verify_prepared_catalog
# src/labrat/eval/benchmarks/dab/codex_events.py
# parse_codex_events, reconcile_mcp_trace
# src/labrat/eval/benchmarks/dab/attempts.py
# AttemptRef, AttemptManifest, finalize_attempt_manifest
# src/labrat/eval/benchmarks/dab/manifest.py
# ExperimentManifest and certification-derived HostEligibility
```

The runtime plan owns Codex command construction, event parsing, rollout-token extraction, MCP policy semantics, native/server trace reconciliation, attempt failure classification, and experiment-manifest resume checks. This plan owns Docker isolation, mounts, credentials, network/resource constraints, isolation evidence, containment certification, and the transition to `submission-eligible`. The experiment plan may consume an eligibility artifact but may not synthesize one.

## Global constraints

- Work on `feat/codex-caching-gpt56`; preserve unrelated dirty-worktree changes.
- The final image contains installed LabRat and Codex CLI, never a LabRat or DataAgentBench checkout.
- Pin Codex CLI to `0.144.1`. A different version requires a fresh image and complete recertification.
- The model-visible tool surface is supplied by the runtime plan's `dab-core-v1`; Docker isolation never substitutes for MCP authorization.
- Runtime root filesystem is read-only. Run as UID/GID `10001:10001`, drop every capability, set `no-new-privileges`, and never mount the Docker socket.
- Mount only individual selected DuckDB/SQLite files read-only. Never mount `query_dataset`, a dataset directory, the DAB root, a validator, an answer key, a user home, or the LabRat checkout.
- The persistent Codex volume contains only minimal auth material and is never mounted into the worker. A trusted bootstrap copies it into a fresh per-attempt home, and trusted cleanup persists only refreshed auth material before destroying that home.
- Database secrets live in mode-`0600` operator files under `~/.config/labrat/dab/credentials` and mount only into the MCP sidecar. Their contents never enter the worker, argv, environment variables, policies, manifests, logs, traces, reports, or bundles.
- No model/native call is made before the no-model certification suite passes. Paid probes require the literal `--allow-paid` flag.
- No dangerous Codex bypass flag and no `--ephemeral`. Native shell, files, web, browser, apps, plugins, image generation, workspace installers, and subagents stay disabled by the runtime adapter.
- All Docker subprocesses use argument lists with `shell=False`. Treat a malformed `docker inspect`, unknown mount/network, or cleanup failure as an audit error.
- Keep intermediate verification task-scoped. Run the full repository gate once after
  all isolation implementation tasks, before certification:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
git diff --check
```

## File map

**Create:**

- `.dockerignore`
- `infra/dab-isolation/Dockerfile`
- `infra/dab-isolation/compose.yaml`
- `infra/dab-isolation/squid.conf`
- `infra/dab-isolation/codex-egress.allowlist`
- `src/labrat/eval/benchmarks/dab/isolation.py`
- `src/labrat/eval/benchmarks/dab/container_entrypoint.py`
- `src/labrat/mcp/private_bridge.py`
- `src/labrat/eval/benchmarks/dab/data_plane.py`
- `src/labrat/eval/benchmarks/dab/certification.py`
- `scripts/dab_codex_auth.py`
- `scripts/dab_provision_readonly_db_roles.py`
- `scripts/certify_dab_codex.py`
- `tests/unit/test_dab_isolation.py`
- `tests/unit/test_dab_data_plane.py`
- `tests/unit/test_dab_certification.py`
- `tests/integration/test_dab_isolation_container.py`
- `tests/integration/test_dab_containment_canaries.py`
- `tests/fixtures/dab/isolation/approved.duckdb.sql`
- `tests/fixtures/dab/isolation/approved.sqlite.sql`
- `docs/dab-isolation-runbook.md`

**Modify:**

- `src/labrat/eval/benchmarks/dab/env.py`
- `src/labrat/eval/benchmarks/dab/suite.py`
- `src/labrat/mcp/config.py`
- `src/labrat/mcp/server.py`
- `src/labrat/agent/tools/base.py`
- `src/labrat/agent/tools/load_mongo_collection.py`
- `scripts/eval_dab.py`
- `src/labrat/eval/benchmarks/dab/bundle.py`
- `scripts/build_dab_trace_bundle.py`
- `tests/unit/test_dab_env.py`
- `tests/unit/test_mcp_config.py`
- `tests/unit/test_mcp_server.py`
- `tests/unit/test_dab_suite_run_trial.py`
- `tests/unit/test_dab_trace_bundle.py`
- `.github/workflows/ci.yml`
- `pyproject.toml`
- `docs/dab-integration.md`

---

### Task 1: Installed-only image and deny-by-default network plane

**Files:** `.dockerignore`, `infra/dab-isolation/Dockerfile`, `infra/dab-isolation/compose.yaml`, `infra/dab-isolation/squid.conf`, `infra/dab-isolation/codex-egress.allowlist`, `tests/integration/test_dab_isolation_container.py`

**Produces:** image `labrat-dab-codex:0.144.1`; per-attempt worker-inference, worker-sidecar, and sidecar-data networks; services `codex-egress`, `labrat-postgres`, and `labrat-mongo`.

- [ ] **Red — add the gated image contract test.** Mark the module skipped unless `LABRAT_RUN_CONTAINER_TESTS=1`. Build the image once in a session fixture, then assert:

```python
def test_image_is_installed_only(image: str) -> None:
    result = docker_run(
        image,
        "python", "-c",
        "import os,labrat; "
        "assert os.getuid()==10001; "
        "assert not os.path.exists('/src'); "
        "assert not os.path.exists('/workspace'); "
        "assert not os.path.exists('/opt/DataAgentBench')",
    )
    assert result.returncode == 0


def test_image_pins_codex_01441(image: str) -> None:
    result = docker_run(image, "codex", "--version")
    assert result.stdout.strip() == "codex-cli 0.144.1"
```

- [ ] Run `LABRAT_RUN_CONTAINER_TESTS=1 uv run pytest tests/integration/test_dab_isolation_container.py -v`; expect failure because the image and fixture do not exist.

- [ ] **Green — add `.dockerignore`.** Exclude `.git`, `.venv`, `.codex`, `.claude`, `.env*`, `runs`, `autoresearch_output`, `eval_output`, `screenshots`, caches, generated reports, and every `**/query_dataset/**`. Re-include only `pyproject.toml`, `uv.lock`, `README.md`, and `src/**` needed by the build stage. Add a test that parses `.dockerignore` and pins these exclusions.

- [ ] **Green — add the multi-stage image.** Use a Python 3.12 slim build stage to build the LabRat wheel and a Node 22 slim stage to install `@openai/codex@0.144.1`. The final Python 3.12 slim stage copies only the wheel, Node runtime, and Codex installation; it creates `labrat` UID/GID 10001 and `/home/labrat`, `/opt/labrat`, and `/dab`. Install DuckDB `sqlite` and `postgres` extensions during image construction under `/home/labrat/.duckdb/extensions`, then disable runtime auto-install in the container entrypoint. Add labels:

```dockerfile
LABEL org.opencontainers.image.title="labrat-dab-codex"
LABEL com.labrat.dab.codex-version="0.144.1"
LABEL com.labrat.dab.contract="native-codex-mcp-v1"
USER 10001:10001
WORKDIR /dab/work
CMD ["python", "-m", "labrat.eval.benchmarks.dab.container_entrypoint"]
```

- [ ] **Green — add network services.** The launcher creates three random-name,
  per-attempt internal networks. Only the worker and `codex-egress` join worker-inference;
  only worker and MCP sidecar join worker-sidecar; only sidecar and the exact database
  gateway(s) join sidecar-data. `codex-egress` alone also joins a public network and
  runs Squid with `http_access deny all` after exact allows. Local-host gateways add
  `host.docker.internal:host-gateway` explicitly; the CI override connects gateways
  directly to Compose fixture service names. No service exposes a host port, worker
  never joins sidecar-data, and sidecar never joins worker-inference/public.

- [ ] Pin `codex-egress.allowlist` to exactly:

```text
chatgpt.com
auth.openai.com
```

If a valid cheap certification probe later records a denied Codex endpoint, amend this file prospectively, rebuild, and recertify. Do not add wildcard domains.

- [ ] `squid.conf` permits CONNECT only to port 443 and only to the allowlist, denies IP-literal destinations, emits an access log, disables caching, and denies every other request. Compose health checks must pass before a container launches.

- [ ] **Refactor/verify.** Run the focused image/container checks:

```bash
docker compose -f infra/dab-isolation/compose.yaml config --quiet
docker build -f infra/dab-isolation/Dockerfile -t labrat-dab-codex:0.144.1 .
LABRAT_RUN_CONTAINER_TESTS=1 uv run pytest tests/integration/test_dab_isolation_container.py -v
uv run ruff format --check tests/integration/test_dab_isolation_container.py
uv run ruff check tests/integration/test_dab_isolation_container.py
git diff --check
```

- [ ] Commit:

```bash
git add .dockerignore infra/dab-isolation tests/integration/test_dab_isolation_container.py
git commit -m "build(dab): add installed-only Codex isolation image"
```

---

### Task 2: Fail-closed container specification and launcher

**Files:** `src/labrat/eval/benchmarks/dab/isolation.py`, `tests/unit/test_dab_isolation.py`

**Interfaces:**

```python
class ResourceLimits(BaseModel):
    cpus: float = 2.0
    memory_bytes: int = 8 * 1024**3
    pids: int = 256
    tmpfs_bytes: int = 512 * 1024**2
    wall_seconds: int = 1200


class ReadOnlyMount(BaseModel):
    source: Path
    target: str


class IsolationRequest(BaseModel):
    attempt_id: str
    image: str
    image_digest: str
    auth_store_volume: str
    fresh_codex_home_volume: str
    artifact_dir: Path
    worker_request_path: Path
    policy_path: Path
    sidecar_bootstrap_path: Path
    data_mounts: list[ReadOnlyMount]
    sidecar_secret_mounts: list[ReadOnlyMount]
    limits: ResourceLimits = ResourceLimits()


class IsolationResult(BaseModel):
    returncode: int
    worker_container_id: str
    sidecar_container_id: str
    isolation_audit_path: Path
    result_path: Path


# Public coroutine signature:
# launch_isolated_attempt(request: IsolationRequest) -> IsolationResult
```

- [ ] **Red — unit-test the exact dual-container contract.** Fake `subprocess.run`/`Popen`; assert both creates contain `--read-only`, `--user 10001:10001`, `--cap-drop ALL`, `--security-opt no-new-privileges:true`, resource limits, tmpfs, and no Docker socket. Assert worker has only worker request, scrubbed policy, artifact storage, and fresh home; sidecar alone has task data, bootstrap, and DB secret mounts. Assert three per-attempt networks have exact two-member sets, both containers are inspected before start, sidecar becomes healthy before worker starts, timeout kills both, and cleanup removes both containers, all three networks, and the fresh home volume.

- [ ] Add rejection tests for every listed unsafe mount plus any worker data/DB-secret mount, any sidecar auth/home mount, worker membership on sidecar-data, sidecar membership on worker-inference, a persistent home used as the attempt home, unknown network, missing image digest, and any secret-like environment key. An individual selected data file under the trusted DAB root remains valid only for the sidecar.

- [ ] Run `uv run pytest tests/unit/test_dab_isolation.py -v`; expect import failure.

- [ ] **Green — implement value validation and Docker argv builders.** Resolve every source with `Path.resolve(strict=True)` and compare it against the exact allowed selected file list, not a broad parent. Both containers may write the shared artifact directory but ownership tests restrict files by role. Worker alone mounts the fresh per-attempt home. Sidecar alone receives exact data and secret mounts. The persistent auth store is mounted only into a one-shot trusted bootstrap/cleanup helper, never worker or sidecar.

- [ ] Define separate fixed environment allowlists. Worker receives `HOME`, `CODEX_HOME`, `TMPDIR`, proxy settings, artifact paths, and the private MCP endpoint; it receives no data/bootstrap/DB variables. Sidecar receives policy/bootstrap/log paths and data gateway names; it receives no proxy, Codex home, or inference/auth variables. DB credential contents are files, never environment values.

- [ ] After both `docker create` calls, parse both inspections and compare image IDs, platform/architecture, users, read-only roots, capabilities, security options, limits, mounts, networks, entrypoint roles, and forbidden cross-membership. Write one normalized secret-free `isolation_audit.json` with canonical SHA-256. Any mismatch prevents either workload from starting.

- [ ] Use `asyncio.wait_for` around the dual-process lifecycle; on timeout kill both, record `infra:timeout`, and retain partial evidence. A malformed inspect/result, sidecar health failure, network disagreement, or cleanup failure returns `audit-error`, never a semantic result.

- [ ] **Refactor/verify and commit.** Keep command construction pure and separately testable.

```bash
uv run pytest tests/unit/test_dab_isolation.py -v
uv run ruff format --check src/labrat/eval/benchmarks/dab/isolation.py tests/unit/test_dab_isolation.py
uv run ruff check src/labrat/eval/benchmarks/dab/isolation.py tests/unit/test_dab_isolation.py
uv run pyright src/labrat/eval/benchmarks/dab/isolation.py
git diff --check
git add src/labrat/eval/benchmarks/dab/isolation.py tests/unit/test_dab_isolation.py
git commit -m "feat(dab): add fail-closed whole-host isolation launcher"
```

---

### Task 3: Private Codex authentication volume lifecycle

**Files:** `scripts/dab_codex_auth.py`, `tests/unit/test_dab_isolation.py`, `docs/dab-isolation-runbook.md`

**CLI:**

```text
dab_codex_auth.py create --image labrat-dab-codex:0.144.1 --volume labrat-dab-codex-home-v1
dab_codex_auth.py login --image labrat-dab-codex:0.144.1 --volume labrat-dab-codex-home-v1 --device-auth
dab_codex_auth.py check --image labrat-dab-codex:0.144.1 --volume labrat-dab-codex-home-v1
dab_codex_auth.py seed-attempt --volume labrat-dab-codex-home-v1 --attempt-volume ATTEMPT_HOME
dab_codex_auth.py collect-attempt --volume labrat-dab-codex-home-v1 --attempt-volume ATTEMPT_HOME
dab_codex_auth.py delete --volume labrat-dab-codex-home-v1 --confirm labrat-dab-codex-home-v1
```

- [ ] **Red — add subprocess-fake tests.** Pin auth-store labels and mode/ownership; login/check through the inference proxy; `seed-attempt` creating a newly labeled empty attempt volume and copying only the allowed auth files; `collect-attempt` copying back only refreshed auth files before deleting the attempt volume; and deletion requiring the exact repeated store name. Reject an existing unlabeled/wrong-version volume, an attempt volume with preexisting history, and mounting the store directly into a worker.

- [ ] Add artifact-leak tests: after interactive login, retain only the minimum documented auth files in the store; reject or delete config, history, session, and rollout material. Seed a fresh attempt, run collection, and assert no raw file, bearer/refresh token, encrypted reasoning, history, or rollout appears under the run directory, later attempt home, or mock bundle.

- [ ] Run `uv run pytest tests/unit/test_dab_isolation.py -k auth -v`; expect failure.

- [ ] **Green — implement the script with argument-list subprocesses.** `create` creates the minimal auth store. `login --device-auth` invokes Codex interactively, then a trusted helper removes every non-auth artifact. `check` verifies only the allowlisted auth files, ownership, and mode without printing contents. `seed-attempt`/`collect-attempt` run as trusted one-shot helpers; the worker sees only the fresh attempt home, never the persistent store.

- [ ] The fresh attempt home remains writable so Codex can refresh authentication and create the one attempt's private rollout. Trusted cleanup extracts scrubbed request usage first, persists only refreshed auth material, then destroys the attempt home. The experiment manifest records only the auth-store contract label/version and fresh-home lifecycle digest, not names, paths, file hashes, or contents.

- [ ] **Refactor/verify and commit.** Add the exact lifecycle commands to the runbook, including `umask 077` before credential operations.

```bash
uv run pytest tests/unit/test_dab_isolation.py -k auth -v
uv run ruff format --check scripts/dab_codex_auth.py tests/unit/test_dab_isolation.py
uv run ruff check scripts/dab_codex_auth.py tests/unit/test_dab_isolation.py
uv run pyright scripts/dab_codex_auth.py
git diff --check
git add scripts/dab_codex_auth.py tests/unit/test_dab_isolation.py docs/dab-isolation-runbook.md
git commit -m "feat(dab): manage Codex auth in a private labeled volume"
```

---

### Task 4: Exact data mounts, server-side pre-attachment, and scoped credentials

**Files:** `src/labrat/eval/benchmarks/dab/data_plane.py`, `src/labrat/eval/benchmarks/dab/env.py`, `src/labrat/mcp/config.py`, `src/labrat/mcp/server.py`, `src/labrat/agent/tools/base.py`, `src/labrat/agent/tools/load_mongo_collection.py`, `scripts/dab_provision_readonly_db_roles.py`, `tests/unit/test_dab_data_plane.py`, `tests/unit/test_dab_env.py`, `tests/unit/test_mcp_config.py`, `tests/unit/test_mcp_server.py`

**Interfaces:**

```python
class FileDataSource(BaseModel):
    alias: str
    db_type: Literal["duckdb", "sqlite"]
    host_path: Path = Field(exclude=True)
    container_path: str


class PostgresDataSource(BaseModel):
    alias: str
    database: str
    username: str
    password_secret: str


class MongoDataSource(BaseModel):
    alias: str
    database: str
    collections: list[str]
    uri_secret: str


class DabDataPlane(BaseModel):
    primary: str
    files: list[FileDataSource]
    postgres: list[PostgresDataSource]
    mongo: list[MongoDataSource]


# Public function signature:
# build_dab_data_plane(db_config_path: Path, *, dab_root: Path,
#                      credentials_dir: Path) -> DabDataPlane
# write_mcp_bootstrap(plan: DabDataPlane, path: Path) -> str
```

- [ ] **Red — cover all official task shapes with temporary configs.** Assert file targets are computed as `PurePosixPath("/dab/data") / alias / host_path.name`, an in-memory `__federation` primary when there is no DuckDB, exact PostgreSQL database/role names, and collection discovery from BSON filenames (`articles_db.articles`, `yelp_db.business`, `yelp_db.checkin`). Assert serialized policy/manifest forms omit `host_path`, credential values, DSNs, and host homes.

- [ ] Add negative tests for absolute paths from YAML, `..`, symlinked files, directories, devices, missing files, unsafe aliases/database/collection names, BSON outside the selected dump, credentials not mode 0600, and a credential file outside the configured credential root.

- [ ] Run `uv run pytest tests/unit/test_dab_data_plane.py tests/unit/test_dab_env.py -v`; expect import failures.

- [ ] **Green — implement trusted resolution.** The host reads `db_config.yaml`; the container never does. Require `db_config_path` under the trusted DAB root, and each resolved file under that config's selected dataset directory. Mount only the resolved regular file. Derive Mongo collections from selected dump BSON basenames, sort them, and include them in the policy input.

- [ ] Extend MCP bootstrap loading behind `LABRAT_MCP_BOOTSTRAP_PATH`. It is fail-closed, DAB-only, and accepted only by the sidecar entrypoint. Open the exact DuckDB primary read-only or create `:memory:`; pre-attach exact SQLite and PostgreSQL aliases before catalog introspection and tool discovery. PostgreSQL DSNs are assembled only inside the sidecar from public gateway/database/user fields and a password read from its private `/run/secrets`; redact exceptions before stderr or audit output. The worker cannot mount or route to any of these sources. `attach_database` stays absent from `dab-core-v1`.

- [ ] Add `mongo_url: str | None = None` to `ToolContext`. Only the MCP sidecar reads the URI secret into this in-memory field. `LoadMongoCollectionTool` prefers `ctx.mongo_url` and otherwise preserves the existing product fallback. Runtime policy still rejects undeclared DBs/collections, `$where`, unsafe targets, primary overrides, and excessive limits.

- [ ] Implement `scripts/dab_provision_readonly_db_roles.py` with `inspect` and explicit `apply` subcommands. For each selected PostgreSQL DB, create the role with `"dab_" + normalize_identifier(database)`, revoke its unrelated memberships, grant only CONNECT on that DB, USAGE on required schemas, SELECT on existing tables, and matching default privileges. For Mongo, first require server authorization to be enabled, then create one random user per DB with only `{role: "read", db: exact_database}`; fail certification if an authenticated client can read another DB. Write generated secrets atomically under the `postgres` and `mongo` subdirectories of `~/.config/labrat/dab/credentials` with `umask 077`; print paths and role names only.

- [ ] Provisioning must never revoke broad privileges from unrelated roles. Test emitted SQL/commands without contacting a real server. Integration containment tests in Task 6 prove the resulting role cannot access a sentinel DB.

- [ ] **Refactor/verify and commit.** Keep current non-DAB env/profile behavior byte-compatible when `LABRAT_MCP_BOOTSTRAP_PATH` is unset.

```bash
uv run pytest tests/unit/test_dab_data_plane.py tests/unit/test_dab_env.py tests/unit/test_mcp_config.py tests/unit/test_mcp_server.py -v
uv run ruff format --check src/labrat/eval/benchmarks/dab/data_plane.py src/labrat/eval/benchmarks/dab/env.py src/labrat/mcp/config.py src/labrat/mcp/server.py tests/unit/test_dab_data_plane.py tests/unit/test_dab_env.py tests/unit/test_mcp_config.py tests/unit/test_mcp_server.py
uv run ruff check src/labrat/eval/benchmarks/dab/data_plane.py src/labrat/eval/benchmarks/dab/env.py src/labrat/mcp/config.py src/labrat/mcp/server.py tests/unit/test_dab_data_plane.py tests/unit/test_dab_env.py tests/unit/test_mcp_config.py tests/unit/test_mcp_server.py
uv run pyright src/labrat/eval/benchmarks/dab/data_plane.py src/labrat/eval/benchmarks/dab/env.py src/labrat/mcp
git diff --check
git add src/labrat/eval/benchmarks/dab/data_plane.py src/labrat/eval/benchmarks/dab/env.py src/labrat/mcp/config.py src/labrat/mcp/server.py src/labrat/agent/tools/base.py src/labrat/agent/tools/load_mongo_collection.py scripts/dab_provision_readonly_db_roles.py tests/unit/test_dab_data_plane.py tests/unit/test_dab_env.py tests/unit/test_mcp_config.py tests/unit/test_mcp_server.py
git commit -m "feat(dab): add exact mounted data plane and scoped credentials"
```

---

### Task 5: Installed entrypoint and `codex-mcp` runtime integration

**Dependency:** Complete the companion runtime plan first. Do not recreate its Codex adapter, policy builder, event auditor, trace reconciler, or manifest logic.

**Files:** `src/labrat/eval/benchmarks/dab/container_entrypoint.py`, `src/labrat/eval/benchmarks/dab/isolation.py`, `src/labrat/mcp/private_bridge.py`, `src/labrat/mcp/server.py`, `src/labrat/eval/benchmarks/dab/suite.py`, `scripts/eval_dab.py`, `tests/unit/test_dab_isolation.py`, `tests/unit/test_dab_suite_run_trial.py`

**Container protocol:**

```text
/dab/artifacts/worker_request.json     worker-only, read-only host request
/dab/artifacts/mcp_policy.json         scrubbed, read-only to both roles
/run/labrat/mcp_bootstrap.json         sidecar-only source/secret references
/dab/artifacts/mcp_tool_calls.jsonl      MCP server output
/dab/artifacts/codex_events.jsonl        complete Codex JSONL stdout
/dab/artifacts/codex_token_usage.jsonl   scrubbed matching-rollout usage
/dab/artifacts/attempt_result.json       atomic entrypoint result
/dab/artifacts/worker_self_check.json    worker environment evidence
/dab/artifacts/sidecar_self_check.json   sidecar environment evidence
```

- [ ] **Red — dual-entrypoint tests.** The trusted host attempt initializer exclusively creates the three JSONL outputs and writes the policy; neither role truncates any existing artifact. Sidecar self-check proves it has exact data/secret mounts, no Codex home/proxy/inference route, prepares the catalog, verifies/finalizes policy, and starts private Streamable HTTP. Worker self-check proves fresh `HOME`/`CODEX_HOME`, no data/secret mounts or sidecar-data route, then calls runtime `run_codex` with the installed stdio bridge command. Both run non-root from `/dab/work`, disable DuckDB auto-install, and write atomic role/result artifacts. Any malformed request, digest mismatch, wrong UID, forbidden mount/network, or runtime audit failure is `audit-error` without an answer.

- [ ] **Red — bridge tests.** Feed real MCP initialize/list/call frames to `private_bridge.py` against a fixture sidecar. Require byte/semantic parity, bounded message sizes, exact per-attempt endpoint, no redirect/general URL support, no filesystem/data access, and fail-closed behavior when the sidecar certificate/network identity or protocol sequence is wrong.

- [ ] **Red — suite integration test.** Configure `DabSuite(driver="codex-mcp")`, replace `launch_isolated_attempt` with a fake, and assert the request includes runtime `CodexHostConfig`, exact policy/run/attempt digest, separate worker and sidecar inputs, per-task data plane, artifact directory, image/network digests, and auth lifecycle contract. Assert a diagnostic local adapter or self-asserted eligibility cannot be selected for a registered/official run.

- [ ] Run `uv run pytest tests/unit/test_dab_isolation.py tests/unit/test_dab_suite_run_trial.py -k 'entrypoint or isolated or codex_mcp' -v`; expect failure.

- [ ] **Green — implement the two role entrypoints and bridge.** Sidecar reads its private bootstrap, prepares sources, finalizes the scrubbed policy, then serves MCP only on the control network. Worker reads only the secret-free request/policy, launches installed `private_bridge.py` through runtime `run_codex`, and never scores. It extracts request usage from the matching thread in the fresh home; raw rollouts remain there until trusted cleanup destroys it.

- [ ] Route certified `codex-mcp` dispatch through `launch_isolated_attempt`; retain the runtime local adapter for diagnostics only. Host-side scoring occurs only after both self-checks, policy finalization, native/server reconciliation, and cleanup succeed; validators never cross the boundary. Normalize timeout/transport/auth/429 through the runtime plan, and every inspect/network/self-check/policy/trace disagreement as immediate `audit-error`.

- [ ] Add diagnostic CLI inputs for image, auth-store volume, and credentials directory. Registered/official runs restore image/network/auth-lifecycle/credential-schema digests from the verified certification and runtime manifest; operators cannot supply an eligibility state or change these values.

- [ ] **Refactor/verify and commit.** Ensure `config.json`, prompts, traces, manifests, and bundles contain neither credential values nor private auth/rollout data.

```bash
uv run pytest tests/unit/test_dab_isolation.py tests/unit/test_dab_suite_run_trial.py tests/unit/test_eval_dab_runner.py -v
uv run ruff format --check src/labrat/eval/benchmarks/dab/container_entrypoint.py src/labrat/eval/benchmarks/dab/isolation.py src/labrat/mcp/private_bridge.py src/labrat/mcp/server.py src/labrat/eval/benchmarks/dab/suite.py scripts/eval_dab.py tests/unit/test_dab_isolation.py tests/unit/test_dab_suite_run_trial.py tests/unit/test_eval_dab_runner.py
uv run ruff check src/labrat/eval/benchmarks/dab/container_entrypoint.py src/labrat/eval/benchmarks/dab/isolation.py src/labrat/mcp/private_bridge.py src/labrat/mcp/server.py src/labrat/eval/benchmarks/dab/suite.py scripts/eval_dab.py tests/unit/test_dab_isolation.py tests/unit/test_dab_suite_run_trial.py tests/unit/test_eval_dab_runner.py
uv run pyright src/labrat/eval/benchmarks/dab/container_entrypoint.py src/labrat/eval/benchmarks/dab/isolation.py src/labrat/mcp/private_bridge.py src/labrat/eval/benchmarks/dab/suite.py
git diff --check
git add src/labrat/eval/benchmarks/dab/container_entrypoint.py src/labrat/eval/benchmarks/dab/isolation.py src/labrat/mcp/private_bridge.py src/labrat/mcp/server.py src/labrat/eval/benchmarks/dab/suite.py scripts/eval_dab.py tests/unit/test_dab_isolation.py tests/unit/test_dab_suite_run_trial.py tests/unit/test_eval_dab_runner.py
git commit -m "feat(dab): run native Codex and MCP inside certified isolation"
```

---

### Task 6: No-model containment canaries and positive database matrix

**Files:** `src/labrat/eval/benchmarks/dab/certification.py`, `tests/unit/test_dab_certification.py`, `tests/integration/test_dab_containment_canaries.py`, `tests/fixtures/dab/isolation/approved.duckdb.sql`, `tests/fixtures/dab/isolation/approved.sqlite.sql`

**Evidence model:**

```python
class CertificationCheck(BaseModel):
    name: str
    passed: bool
    evidence_sha256: str


class CertificationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["dab-certification-v1"]
    driver: Literal["codex-mcp"]
    codex_cli_version: Literal["0.144.1"]
    labrat_commit: str
    dab_commit: str
    tool_profile: Literal["dab-core-v1"]
    tool_schema_sha256: str
    policy_builder_sha256: str
    image_digest: str
    platform: str
    architecture: str
    certification_suite_sha256: str
    isolation_config_sha256: str
    network_policy_sha256: str
    credential_plane_schema: str
    gateway_identities_sha256: str
    checks: tuple[CertificationCheck, ...]
    policy_certified: bool
    submission_eligible: bool
    no_model_matrix_clean: bool
    containment_canaries_clean: bool
    native_event_audit_clean: bool
    trace_reconciliation_clean: bool
    luna_low_canary_clean: bool
    issued_at: datetime
    valid_until: datetime
    contract_sha256: str
    sha256: str
```

- [ ] **Red — unit-test certification transitions.** `diagnostic-only` is the default. Exact policy/trace tests can produce `policy-certified`. `submission-eligible` requires all policy checks, all containment checks, the positive DB matrix, an image/CLI/commit/platform match, both role/network inspections, and a clean live integrated canary. Missing, expired, revoked, stale, false, duplicate, or unknown checks fail closed. Changing any bound commit/digest/schema/gateway identity invalidates the artifact. Registered launch also performs a fresh per-attempt drift preflight.

- [ ] **Red — add Docker containment tests** gated by `LABRAT_RUN_CONTAINER_TESTS=1`. Build unique random sentinels in:

  - an unmounted host file and a symlink to it;
  - an HTTP service reachable only through the deny proxy;
  - `forbidden_canary_db` in PostgreSQL;
  - `forbidden_canary_db` in MongoDB.

  Assert the container cannot read the file, the data-plane builder rejects the symlink, Squid denies the HTTP request and the sentinel records zero successes, the selected PostgreSQL role cannot connect to the forbidden DB, and the selected Mongo user cannot list/read it. Never print sentinel values on failure.

- [ ] Exercise the runtime policy's malicious inputs without a model: DuckDB external readers/scanners, URL reads, `glob`, `sqlite_scan`, `postgres_scan`, dynamic query, secrets/settings, multi-statements, `PRAGMA`, DDL/DML, `force=true`, helper identifier injection, attach variants, other Mongo DB/collection, `$where`, unsafe target, primary override, and limit overflow. Require canonical denial records.

- [ ] Feed the runtime auditor a valid synthetic Codex stream, then one stream containing a native command/file/web item. The valid stream reconciles; the forbidden stream produces `audit-error` and cannot advance eligibility.

- [ ] Add a no-model positive matrix using container-local fixtures:

  1. DuckDB primary plus pre-attached SQLite;
  2. in-memory DuckDB plus pre-attached PostgreSQL and SQLite;
  3. in-memory DuckDB plus scoped Mongo materialization and SQLite.

  Each case requires exact tool discovery, successful approved query, one-for-one canonical traces, clean policy audit, read-only source behavior, and no visible benchmark source/answer path.

- [ ] Run the new tests and expect failure before implementation:

```bash
uv run pytest tests/unit/test_dab_certification.py -v
LABRAT_RUN_CONTAINER_TESTS=1 uv run pytest tests/integration/test_dab_containment_canaries.py -v
```

- [ ] **Green — implement pure evidence aggregation.** Evidence references artifact hashes, never mutable paths alone. Compute the output as `Path("runs/dab/certification/codex-mcp-0.144.1") / image_digest.removeprefix("sha256:") / "certification.json"` and write it atomically. A certification run may replace only its own incomplete directory; a successful artifact is immutable. Set a short explicit validity window and support a write-once revocation record when a bound input changes or a later canary fails.

- [ ] **Refactor/verify and commit.** Tests must clean up their containers, networks, users, and sentinel DBs even on failure.

```bash
uv run pytest tests/unit/test_dab_certification.py -v
LABRAT_RUN_CONTAINER_TESTS=1 uv run pytest tests/integration/test_dab_isolation_container.py tests/integration/test_dab_containment_canaries.py -v
uv run ruff format --check src/labrat/eval/benchmarks/dab/certification.py tests/unit/test_dab_certification.py tests/integration/test_dab_containment_canaries.py
uv run ruff check src/labrat/eval/benchmarks/dab/certification.py tests/unit/test_dab_certification.py tests/integration/test_dab_containment_canaries.py
uv run pyright src/labrat/eval/benchmarks/dab/certification.py
git diff --check
git add src/labrat/eval/benchmarks/dab/certification.py tests/unit/test_dab_certification.py tests/integration/test_dab_containment_canaries.py tests/fixtures/dab/isolation
git commit -m "test(dab): certify native containment and database isolation"
```

---

### Task 7: Certification CLI, cheap live canaries, and eligibility artifact

**Files:** `scripts/certify_dab_codex.py`, `src/labrat/eval/benchmarks/dab/certification.py`, `tests/unit/test_dab_certification.py`, `docs/dab-isolation-runbook.md`

**CLI stages:** `no-model`, `live-no-tool`, `live-integrated`, `finalize`.

- [ ] **Red — CLI tests.** Assert `live-*` refuses to run without `--allow-paid`; `finalize` refuses missing stages, non-Luna-low config, stale digests, dirty/cross-image evidence, any audit error, any sentinel success, or absent trace reconciliation. Assert the first 429 records `infra:rate_limit`, returns exit code 4, and does not run another canary.

- [ ] Run `uv run pytest tests/unit/test_dab_certification.py -k cli -v`; expect failure.

- [ ] **Green — implement `no-model`.** It verifies the runtime plan's policy/trace unit suite, Task 6 container suite, image labels/digest, CLI version, network configuration, auth volume contract, and credential-file modes. It writes `policy-certified` evidence but never calls a model.

- [ ] **Green — implement `live-no-tool`.** Run one isolated `gpt-5.6-luna`/`low` prompt: `Reply exactly DAB_CODEX_AUTH_OK without calling a tool.` Require exactly one clean terminal agent message, zero tool calls, valid aggregate/request telemetry, no forbidden native event, and no file/network sentinel access. This evidence is infrastructure-only and is never scored.

- [ ] **Green — implement `live-integrated`.** Run one isolated Luna-low tool-use prompt against each synthetic positive family from Task 6. Require exact `dab-core-v1` discovery for the task shape, at least one approved tool call, correct deterministic fixture result, reconciled host/server traces, clean policy/isolation/taint audits, and no DAB validator or answer mount. These attempts are infrastructure evidence, not benchmark score evidence.

- [ ] **Green — implement `finalize`.** Recompute every artifact digest from disk. Only then write `submission_eligible=true`. `contract_sha256` hashes every bound field except issuance/expiry, check-run timestamps, and record `sha256`; a refresh must reproduce it exactly. Include issuance/expiry and exact CLI/commit/image/platform/config/network/policy/tool/credential/gateway digests. Do not include volume names, host paths, credential paths, prompts, answers, tokens, or raw rollouts.
- [ ] `finalize` writes the exact `CertificationRecord` from Task 6; there is no
  caller-supplied eligibility field. It also binds the clean LabRat commit containing
  the runtime, isolation, and experiment-controller implementation. Therefore the
  paid `live-*` and `finalize` operations are deliberately deferred until experiment
  plan Tasks 1–9 are committed. A time-only refresh reruns all checks and appends the
  new record SHA while retaining the same contract digest; any code/config/credential-
  schema/gateway change requires a new contract and therefore a new campaign.

- [ ] Add exact operator commands to `docs/dab-isolation-runbook.md` under a
  clearly labeled post-implementation certification section. `live-integrated` uses
  synthetic deterministic fixtures and never invokes a DAB validator or records a
  benchmark score:

```bash
export DAB_DIR="$HOME/repos/DataAgentBench"
export LABRAT_DAB_IMAGE="labrat-dab-codex:0.144.1"
export LABRAT_DAB_AUTH_VOLUME="labrat-dab-codex-home-v1"
export LABRAT_DAB_CREDENTIALS_DIR="$HOME/.config/labrat/dab/credentials"

docker build -f infra/dab-isolation/Dockerfile -t "$LABRAT_DAB_IMAGE" .
docker compose -f infra/dab-isolation/compose.yaml up -d --wait
uv run python scripts/dab_codex_auth.py create --image "$LABRAT_DAB_IMAGE" --volume "$LABRAT_DAB_AUTH_VOLUME"
uv run python scripts/dab_codex_auth.py login --image "$LABRAT_DAB_IMAGE" --volume "$LABRAT_DAB_AUTH_VOLUME" --device-auth
uv run python scripts/dab_codex_auth.py check --image "$LABRAT_DAB_IMAGE" --volume "$LABRAT_DAB_AUTH_VOLUME"
uv run python scripts/dab_provision_readonly_db_roles.py inspect --dab-dir "$DAB_DIR" --credentials-dir "$LABRAT_DAB_CREDENTIALS_DIR"
uv run python scripts/dab_provision_readonly_db_roles.py apply --dab-dir "$DAB_DIR" --credentials-dir "$LABRAT_DAB_CREDENTIALS_DIR"
uv run python scripts/certify_dab_codex.py no-model --image "$LABRAT_DAB_IMAGE" --codex-home-volume "$LABRAT_DAB_AUTH_VOLUME" --credentials-dir "$LABRAT_DAB_CREDENTIALS_DIR"
uv run python scripts/certify_dab_codex.py live-no-tool --allow-paid --image "$LABRAT_DAB_IMAGE" --codex-home-volume "$LABRAT_DAB_AUTH_VOLUME" --model gpt-5.6-luna --reasoning low
uv run python scripts/certify_dab_codex.py live-integrated --allow-paid --image "$LABRAT_DAB_IMAGE" --codex-home-volume "$LABRAT_DAB_AUTH_VOLUME" --credentials-dir "$LABRAT_DAB_CREDENTIALS_DIR" --model gpt-5.6-luna --reasoning low
uv run python scripts/certify_dab_codex.py finalize --image "$LABRAT_DAB_IMAGE"
```

- [ ] **Refactor/verify and commit.** Mock all paid calls in the default test suite; no CI job receives auth.

```bash
uv run pytest tests/unit/test_dab_certification.py -v
uv run ruff format --check scripts/certify_dab_codex.py src/labrat/eval/benchmarks/dab/certification.py tests/unit/test_dab_certification.py
uv run ruff check scripts/certify_dab_codex.py src/labrat/eval/benchmarks/dab/certification.py tests/unit/test_dab_certification.py
uv run pyright scripts/certify_dab_codex.py src/labrat/eval/benchmarks/dab/certification.py
git diff --check
git add scripts/certify_dab_codex.py src/labrat/eval/benchmarks/dab/certification.py tests/unit/test_dab_certification.py docs/dab-isolation-runbook.md
git commit -m "feat(dab): issue digest-bound native eligibility evidence"
```

---

### Task 8: Strict bundle gate, CI, documentation, and final audit

**Files:** `src/labrat/eval/benchmarks/dab/bundle.py`, `scripts/build_dab_trace_bundle.py`, `tests/unit/test_dab_trace_bundle.py`, `.github/workflows/ci.yml`, `pyproject.toml`, `docs/dab-integration.md`, `docs/dab-isolation-runbook.md`

- [ ] **Red — extend certification-aware bundle tests.** A certified `codex-mcp` attempt must fail without matching `submission_eligible=true`, dual-container `isolation_audit.json`, `worker_self_check.json`, `sidecar_self_check.json`, and the runtime four-artifact contract. It must fail on expiry/revocation/digest drift, cross-network/mount disagreement, policy/trace disagreement, taint, raw rollout/auth/config/history files, secret-like values, or host paths. It preserves and hashes infrastructure-attempt evidence. Exact campaign/freeze/270 semantics remain owned by the experiment plan.

- [ ] Run `uv run pytest tests/unit/test_dab_trace_bundle.py -v`; expect new failures.

- [ ] **Green — enforce isolation evidence in package bundling.** Recompute certification and per-attempt hashes. A diagnostic/policy-certified artifact can produce a diagnostic bundle only; the experiment plan layers campaign/freeze/exact-270 checks over this API. Keep the runtime four-artifact contract and one-for-one trace audit authoritative.

- [ ] Add pytest markers in `pyproject.toml` for `container` and `paid`; default CI must never select `paid`.

- [ ] Add a `dab-container-cert` CI job on Ubuntu that builds `labrat-dab-codex:0.144.1`, runs Compose config validation, starts synthetic PostgreSQL/Mongo/HTTP fixtures, and runs:

```bash
LABRAT_RUN_CONTAINER_TESTS=1 uv run pytest -m container -q
```

The job has no repository secrets, does not mount the runner home, and never runs the live certification stages.

- [ ] Update `docs/dab-integration.md` with the diagnostic/policy-certified/submission-eligible distinction, installed-only mount contract, credential setup, certification commands, recertification triggers, and the rule that the experiment plan must verify the eligibility artifact before a registered host A/B or full run.

- [ ] Final artifact/image/mount audit (scan produced artifacts and `docker inspect`
  output, not the whole source tree):

```bash
docker compose -f infra/dab-isolation/compose.yaml config --quiet
docker build -f infra/dab-isolation/Dockerfile -t labrat-dab-codex:0.144.1 .
LABRAT_RUN_CONTAINER_TESTS=1 uv run pytest -m container -q
uv run python scripts/build_dab_trace_bundle.py --help
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest -q
git diff --check
```

Run the tested artifact scanner over certification fixtures and generated diagnostic
bundles; documentation may name prohibited artifacts, but no runtime config, log,
manifest, or bundle may contain a secret or answer path.

- [ ] Commit:

```bash
git add src/labrat/eval/benchmarks/dab/bundle.py scripts/build_dab_trace_bundle.py tests/unit/test_dab_trace_bundle.py .github/workflows/ci.yml pyproject.toml docs/dab-integration.md docs/dab-isolation-runbook.md
git commit -m "ci(dab): gate native runs on certified whole-host isolation"
```

## Handoff to the experiment plan

After Task 8, give `docs/superpowers/plans/2026-07-11-gpt56-dab-experiments.md` these immutable inputs:

- the `submission-eligible` certification path and SHA-256;
- image digest and Codex CLI version;
- isolation/network config digests;
- `dab-core-v1` tool-schema and policy-builder digests from the runtime plan;
- the exact auth-volume contract label, without its name or contents;
- the exact credential-plane schema version, without paths or values.

The experiment controller must reject registered or official `codex-mcp` launches when any live digest differs. A fresh image, Codex upgrade, network allowlist change, container-security change, tool/profile/policy change, credential-plane schema change, unknown native event, or failed canary returns the host to `diagnostic-only` until this plan's certification sequence is rerun.

## Completion criteria

- [ ] Final image reports Codex CLI 0.144.1, runs as UID 10001, contains installed LabRat, and contains no source/DAB checkout.
- [ ] Dual-container inspection proves read-only roots, exact disjoint mounts/networks,
  limits, dropped capabilities, no Docker socket, and only approved writable locations.
- [ ] Only minimal auth persists in the labeled store; every attempt uses a fresh home,
  and raw private rollouts never enter a later attempt, `runs/`, or a bundle.
- [ ] File databases and DB credentials mount only into the MCP sidecar; worker has no
  data-plane route. SQLite/PostgreSQL are pre-attached server-side and PostgreSQL/Mongo
  principals are exact-database read-only.
- [ ] Negative filesystem, HTTP, PostgreSQL, Mongo, SQL/helper, symlink, and native-event canaries reveal no sentinel and record no successful forbidden access.
- [ ] Positive DuckDB/SQLite, PostgreSQL/SQLite, and Mongo/SQLite matrices pass with exact discovery and reconciled traces.
- [ ] Luna-low no-tool and integrated live canaries are clean and retained only as infrastructure evidence.
- [ ] A digest-bound `submission-eligible` artifact exists and becomes invalid on any relevant drift.
- [ ] Strict native bundles cannot be produced without matching certification and complete isolation/native/server trace evidence.
- [ ] CI runs the complete no-model container certification path without credentials or paid calls.
