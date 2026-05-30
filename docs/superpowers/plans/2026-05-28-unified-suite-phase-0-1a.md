# Unified Suite + DAB Phase 1a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the unified benchmark suite scaffolding (`BenchmarkSuite` protocol, `eval/benchmarks/<bench>/` layout, smoke regression infrastructure) along with DAB Phase 1a (multi-DB `ToolContext`, `DabSuite` enumeration, single-trial DAB runs on the 5 DuckDB+SQLite-only datasets). Exit at DAB Phase 1a's gate: all 17 single-trial DAB queries complete on the new harness, ADE smoke regression passes through the new shape.

**Architecture:** Layer the unified-suite shape onto LabRat's `src/labrat/eval/` package. Add `types.py` with the small `BenchmarkSuite` protocol. Port the existing ADE-bench integration from `eval/suites/ade_bench.py` + `eval/runners/ade_bench_runner.py` to `eval/benchmarks/ade_bench/`. Create `eval/smoke.py` with a generic `SubsetSuite` plus a fixed ADE smoke-task list. Capture a baseline for the smoke set through the new code path. Then add multi-DB `ToolContext` with backwards-compat shims, build `DabSuite` (enumeration + `run_trial` + stratified `aggregate` + DAB-format `write_submission`), and write `scripts/eval_dab.py` with an inline interim runner. The shared `BenchmarkOrchestrator` and `LabRatAgentDriver` are deliberately **not** built here — they extract in Phase 4 (Spider2 onboarding).

**Tech Stack:** Python 3.12, `uv`, Pydantic v2, asyncio, Polars, DuckDB, psycopg2-binary, pymongo, the existing `labrat.agent.AgentLoop` + `ClaudeCodeProvider`. Pyright strict. `pytest` with `asyncio_mode = "auto"`. PostgreSQL 17 + MongoDB 8 installed locally via brew.

**Branch:** `feat/dab-integration` (already created). All work commits here. No merge to master until DAB Phase 1c exits.

**Specs this plan implements:**
- `docs/superpowers/specs/2026-05-28-unified-benchmark-suite-design.md` — protocol, file layout, phasing
- `docs/superpowers/specs/2026-05-27-dab-bench-integration-design.md` — DAB-specific contract (file paths patched to align with the unified spec)

---

## Working notes

**TDD discipline.** Every code task follows red → green → commit. Run tests with `uv run pytest <path> -v`. Never edit production code without a failing test against the new behavior.

**Pre-commit gate.** Before every commit:
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q
```
`ruff format` must come before `ruff check` — format violations are check failures.

**Pyright strict applies to all of `src/labrat/` except `dspy_opt/` and `screens/`.** Test files don't need to satisfy strict mode but should pass basic typing.

**`asyncio_mode = "auto"`** is set globally. Async tests need no `@pytest.mark.asyncio` decorator.

**Fixtures location.** Existing DuckDB fixtures live at `tests/fixtures/sample_dbs/`. New DAB-shaped fixtures go under `tests/fixtures/dab/`.

**Commit messages.** Use the project's existing style (`feat:`, `refactor:`, `test:`, `docs:`). Co-author trailer matches existing commits.

---

## Phase 0 — Pre-flight (1–2 days)

### Task 1: `ClaudeCodeProvider` tool-registry compatibility spike

**Files:**
- Create: `scripts/spikes/claude_code_tool_registry_spike.py`
- Create: `tests/integration/test_claude_code_tool_registry_spike.py`

This is a go/no-go gate. The DAB spec depends on `ClaudeCodeProvider` correctly exposing LabRat's tool registry to the model. If the `claude` CLI's own built-in tools interfere with our custom registry, the entire design pivots to a Bash-tool-prompting approach. Verify before committing to the rest of the plan.

- [ ] **Step 1: Write the spike script that exercises a 2-tool registry**

Create `scripts/spikes/claude_code_tool_registry_spike.py`:

```python
"""Spike: confirm ClaudeCodeProvider round-trips tool calls from a custom registry.

Exit 0 on success, 1 on failure. Prints diagnostic output.
"""

from __future__ import annotations

import asyncio
import sys

from pydantic import BaseModel

from labrat.agent.loop import AgentLoop
from labrat.agent.providers.claude_code import ClaudeCodeProvider
from labrat.agent.tools.base import DispatchResult, Tool, ToolContext
from labrat.agent.tools.registry import ToolRegistry


class AddInput(BaseModel):
    a: int
    b: int


class AddTool(Tool[AddInput]):
    @property
    def name(self) -> str:
        return "add"

    @property
    def description(self) -> str:
        return "Add two integers and return the sum."

    @property
    def input_model(self) -> type[AddInput]:
        return AddInput

    async def execute(self, ctx: ToolContext, input: AddInput) -> DispatchResult:
        return DispatchResult.ok({"sum": input.a + input.b})


class EchoInput(BaseModel):
    text: str


class EchoTool(Tool[EchoInput]):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo a string back verbatim."

    @property
    def input_model(self) -> type[EchoInput]:
        return EchoInput

    async def execute(self, ctx: ToolContext, input: EchoInput) -> DispatchResult:
        return DispatchResult.ok({"echoed": input.text})


async def main() -> int:
    registry = ToolRegistry()
    registry.register(AddTool())
    registry.register(EchoTool())

    provider = ClaudeCodeProvider()
    loop = AgentLoop(provider=provider, tool_registry=registry, max_turns=5)

    prompt = "Use the add tool to compute 17 + 25, then use echo to echo the answer as 'result: <n>'."
    result = await loop.run(ctx=None, prompt=prompt)  # ctx not needed for stateless tools

    print("=== Final text ===")
    print(result.final_text)
    print("=== Tool calls ===")
    print(result.tool_calls)
    print("=== Tool call log ===")
    for entry in result.tool_call_log:
        print(entry)

    add_called = any(c.get("tool") == "add" for c in result.tool_call_log)
    echo_called = any(c.get("tool") == "echo" for c in result.tool_call_log)
    has_42 = "42" in result.final_text

    if add_called and echo_called and has_42:
        print("\nSPIKE PASS: both tools were called and final answer contains '42'.")
        return 0
    else:
        print(
            f"\nSPIKE FAIL: add_called={add_called} echo_called={echo_called} has_42={has_42}"
        )
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: Adapt to the actual `AgentLoop.run` signature**

Read `src/labrat/agent/loop.py` to confirm `AgentLoop.run`'s signature (the spike above assumes `ctx`, `prompt`, returning an object with `final_text`, `tool_calls`, `tool_call_log`). Adjust the spike script to match whatever the real API looks like. Adjust `print` calls accordingly. Keep the success criteria the same.

- [ ] **Step 3: Run the spike**

```bash
uv run python scripts/spikes/claude_code_tool_registry_spike.py
```

Expected (on success): exit code 0, output ending with `SPIKE PASS`.

- [ ] **Step 4: If the spike fails — STOP**

Diagnose in this order:
1. Does `ClaudeCodeProvider` accept a tool registry at all? Read `src/labrat/agent/providers/claude_code.py`.
2. If yes but tool calls don't fire: check whether `claude` CLI's own `Bash`/`Read`/`Edit` tools are shadowing our registry. Check `provider`'s system prompt / settings JSON for tool restrictions.
3. If the provider has no tool-registry support: this is the documented spec risk. **Halt this plan, patch `docs/superpowers/specs/2026-05-28-unified-benchmark-suite-design.md` and the DAB spec to reflect a pivot to a Bash-tool-prompting approach (the `LabratLocalAgent` pattern, adapted for query-answering), then revise this plan from Task 7 onward.**

Do not continue past this point until the spike passes or the spec pivot is committed.

- [ ] **Step 5: Commit the spike**

```bash
git add scripts/spikes/claude_code_tool_registry_spike.py
git commit -m "spike: confirm ClaudeCodeProvider tool-registry round-trip works"
```

---

### Task 2: Install PostgreSQL 17 and MongoDB 8 (user-side)

**Files:**
- Create: `docs/dab_local_setup.md` — record what got installed and the connection URIs used

This is operator work, not code. Steps codify what to do; verify with the smoke commands.

- [ ] **Step 1: Install via brew**

```bash
brew install postgresql@17 mongodb-community@8.0
brew services start postgresql@17
brew services start mongodb-community@8.0
```

- [ ] **Step 2: Verify PostgreSQL reachable**

```bash
psql -h localhost -U "$USER" -d postgres -c 'SELECT version();'
```

Expected: one row with `PostgreSQL 17.x`.

- [ ] **Step 3: Verify MongoDB reachable**

```bash
mongosh --quiet --eval 'db.adminCommand({listDatabases: 1}).databases.map(d => d.name)'
```

Expected: a list including `admin`, `config`, `local`.

- [ ] **Step 4: Record the connection URIs in `docs/dab_local_setup.md`**

Create `docs/dab_local_setup.md`:

```markdown
# DAB Local DB Setup

## Connection URIs (development workstation)

- PostgreSQL: `postgresql://<USER>@localhost:5432/<dataset_db>`
- MongoDB: `mongodb://localhost:27017`

(Replace `<USER>` with `$USER` from the install shell; replace `<dataset_db>` with the per-dataset name created by `scripts/dab_setup.py`.)

## Brew installs

- `postgresql@17` started via `brew services`
- `mongodb-community@8.0` started via `brew services`

## Per-dataset notes

(Populated by `scripts/dab_setup.py` runs — see that script's output.)
```

- [ ] **Step 5: Commit the setup doc**

```bash
git add docs/dab_local_setup.md
git commit -m "docs: record DAB local DB setup procedure"
```

---

### Task 3: Explore DAB repo data layout

**Files:** none modified; output goes into Task 4's `dab_setup.py`.

`dab_setup.py` needs to load 5 PG datasets and 2 Mongo datasets from `~/repos/DataAgentBench`. The exact file layout (where the SQL dumps and Mongo dumps live) needs to be confirmed before writing the loader.

- [ ] **Step 1: Inspect the DAB repo's data layout**

```bash
ls ~/repos/DataAgentBench/
ls ~/repos/DataAgentBench/query_bookreview/
ls ~/repos/DataAgentBench/query_bookreview/query_1/ | head -20
cat ~/repos/DataAgentBench/query_bookreview/query_1/db_config.yaml 2>/dev/null | head -40
```

Look for: `db_config.yaml`, `db_description.txt`, `db_description_withhint.txt`, `validate.py`, raw data dumps.

- [ ] **Step 2: Find the raw data dumps**

Likely locations: a top-level `data/`, a per-query `data/`, or a separate download script (`download.sh` was visible in the repo root).

```bash
cat ~/repos/DataAgentBench/download.sh 2>/dev/null
find ~/repos/DataAgentBench -maxdepth 3 -name '*.sql' -o -name '*.bson' -o -name '*.dump' 2>/dev/null | head -20
```

Record the actual paths found — Task 4's loader hardcodes them.

- [ ] **Step 3: Per-DBMS dataset mapping**

From the DAB spec, the dataset → DB mapping is:
- **PostgreSQL** (5): `bookreview`, `crmarenapro`, `googlelocal`, `pancancer_atlas`, `patents`
- **MongoDB** (2): `agnews`, `yelp`
- **DuckDB + SQLite only** (5): `deps_dev_v1`, `github_repos`, `music_brainz_20k`, `stockindex`, `stockmarket`

Note: `query_civic_unstructured`, `query_cve`, `query_imdb`, `query_krama`, `query_usaspending` also appeared in the repo's directory listing — they're outside the official 12-dataset benchmark, but the loader should ignore them quietly.

- [ ] **Step 4: No commit — this is read-only exploration**

---

### Task 4: Write `scripts/dab_setup.py`

**Files:**
- Create: `scripts/dab_setup.py`
- Create: `tests/integration/test_dab_setup.py`

Idempotent loader: PostgreSQL via `psql`, MongoDB via `mongorestore`. Skips already-loaded DBs. Run once per workstation.

- [ ] **Step 1: Write a failing test for the PG idempotency check**

`tests/integration/test_dab_setup.py`:

```python
"""Integration tests for scripts/dab_setup.py.

These tests require a local PostgreSQL instance. They use a sentinel DB name
that won't collide with real DAB datasets.
"""

from __future__ import annotations

import subprocess

import pytest

from scripts.dab_setup import pg_database_exists, pg_load_dataset

_SENTINEL_DB = "dabsetup_test_sentinel"


@pytest.fixture(autouse=True)
def _cleanup_sentinel():
    yield
    subprocess.run(
        ["psql", "-h", "localhost", "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {_SENTINEL_DB};"],
        check=False,
    )


def test_pg_database_exists_false_before_create():
    assert pg_database_exists(_SENTINEL_DB) is False


def test_pg_database_exists_true_after_create(tmp_path):
    sql_file = tmp_path / "init.sql"
    sql_file.write_text("CREATE TABLE t (x int); INSERT INTO t VALUES (1), (2);")
    pg_load_dataset(_SENTINEL_DB, sql_file)
    assert pg_database_exists(_SENTINEL_DB) is True


def test_pg_load_dataset_is_idempotent(tmp_path):
    sql_file = tmp_path / "init.sql"
    sql_file.write_text("CREATE TABLE t (x int); INSERT INTO t VALUES (1);")
    pg_load_dataset(_SENTINEL_DB, sql_file)
    pg_load_dataset(_SENTINEL_DB, sql_file)  # second call must not raise
    assert pg_database_exists(_SENTINEL_DB) is True
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
uv run pytest tests/integration/test_dab_setup.py -v
```

Expected: ImportError on `scripts.dab_setup`.

- [ ] **Step 3: Implement `scripts/dab_setup.py`**

```python
"""One-time setup: load all 12 DAB datasets into local PostgreSQL + MongoDB.

Run: uv run python scripts/dab_setup.py [--dab-dir ~/repos/DataAgentBench]
Idempotent: skips already-loaded DBs.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_DAB_DIR = Path("~/repos/DataAgentBench").expanduser()

# Per the DAB spec — the 12 official benchmark datasets and their DBMS requirements.
PG_DATASETS: list[str] = ["bookreview", "crmarenapro", "googlelocal", "pancancer_atlas", "patents"]
MONGO_DATASETS: list[str] = ["agnews", "yelp"]


def pg_database_exists(name: str) -> bool:
    """True if a PG database with this name exists."""
    result = subprocess.run(
        [
            "psql",
            "-h",
            "localhost",
            "-d",
            "postgres",
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname='{name}';",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() == "1"


def pg_load_dataset(name: str, sql_file: Path) -> None:
    """Create PG database `name` and load `sql_file` into it. Idempotent."""
    if pg_database_exists(name):
        print(f"  [pg] {name}: already loaded, skipping")
        return
    subprocess.run(
        ["psql", "-h", "localhost", "-d", "postgres", "-c", f"CREATE DATABASE {name};"],
        check=True,
    )
    subprocess.run(
        ["psql", "-h", "localhost", "-d", name, "-f", str(sql_file)],
        check=True,
    )
    print(f"  [pg] {name}: loaded from {sql_file}")


def mongo_database_exists(name: str) -> bool:
    """True if a Mongo database with this name has at least one collection."""
    result = subprocess.run(
        ["mongosh", "--quiet", name, "--eval", "db.getCollectionNames().length"],
        capture_output=True,
        text=True,
        check=True,
    )
    try:
        return int(result.stdout.strip()) > 0
    except ValueError:
        return False


def mongo_load_dataset(name: str, dump_dir: Path) -> None:
    """Restore `dump_dir` into Mongo database `name`. Idempotent."""
    if mongo_database_exists(name):
        print(f"  [mongo] {name}: already loaded, skipping")
        return
    subprocess.run(
        ["mongorestore", "--db", name, str(dump_dir)],
        check=True,
    )
    print(f"  [mongo] {name}: loaded from {dump_dir}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load DAB datasets into local PG + Mongo.")
    parser.add_argument("--dab-dir", type=Path, default=DEFAULT_DAB_DIR)
    args = parser.parse_args(argv)

    dab_dir: Path = args.dab_dir.expanduser()
    if not dab_dir.exists():
        print(f"DAB dir not found: {dab_dir}", file=sys.stderr)
        return 1

    print(f"Loading datasets from {dab_dir}")

    # PG datasets — assumes each dataset's SQL dump lives at:
    #   <dab_dir>/data/<dataset>/<dataset>.sql
    # If Task 3 exploration revealed a different layout, update this path.
    for ds in PG_DATASETS:
        sql_file = dab_dir / "data" / ds / f"{ds}.sql"
        if not sql_file.exists():
            print(f"  [pg] {ds}: dump not found at {sql_file} — skipping", file=sys.stderr)
            continue
        pg_load_dataset(ds, sql_file)

    # Mongo datasets — assumes each dataset's BSON dump lives at:
    #   <dab_dir>/data/<dataset>/dump/
    for ds in MONGO_DATASETS:
        dump_dir = dab_dir / "data" / ds / "dump"
        if not dump_dir.exists():
            print(f"  [mongo] {ds}: dump dir not found at {dump_dir} — skipping", file=sys.stderr)
            continue
        mongo_load_dataset(ds, dump_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Reconcile paths against Task 3 findings**

If Task 3 revealed that PG dumps live at e.g. `<dab_dir>/data/<dataset>/init.sql` (not `<dataset>.sql`), update the `sql_file` path. If Mongo dumps live at e.g. `<dab_dir>/data/<dataset>/bson/` (not `dump/`), update `dump_dir`. **The path discovery is the actual work of this task.**

- [ ] **Step 5: Run integration tests**

```bash
uv run pytest tests/integration/test_dab_setup.py -v
```

Expected: 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/dab_setup.py tests/integration/test_dab_setup.py
git commit -m "feat: dab_setup.py loads PG + Mongo datasets idempotently"
```

---

### Task 5: Run `dab_setup.py` and verify all 12 datasets queryable

**Files:** none modified.

- [ ] **Step 1: Run the loader**

```bash
uv run python scripts/dab_setup.py
```

Expected: prints "loaded" for each of the 5 PG + 2 Mongo datasets the first time; "skipping" on a second invocation.

- [ ] **Step 2: Verify PG datasets**

For each PG dataset (`bookreview`, `crmarenapro`, `googlelocal`, `pancancer_atlas`, `patents`):

```bash
psql -h localhost -d <dataset> -c '\dt' | head -5
```

Expected: at least one table per dataset.

- [ ] **Step 3: Verify Mongo datasets**

```bash
mongosh --quiet agnews --eval 'db.getCollectionNames()'
mongosh --quiet yelp --eval 'db.getCollectionNames()'
```

Expected: non-empty collection lists.

- [ ] **Step 4: Append observed dataset stats to `docs/dab_local_setup.md`**

Add a "Per-dataset notes" section noting table/collection counts. This is documentation, not code.

- [ ] **Step 5: Commit the doc update**

```bash
git add docs/dab_local_setup.md
git commit -m "docs: record DAB dataset table counts after setup"
```

---

### Task 6: Select 9 ADE smoke task IDs

**Files:**
- Create: `docs/superpowers/notes/2026-05-28-ade-smoke-selection.md`

The smoke set is **immutable** once baselined — change it and the baseline becomes meaningless. Pick now, record rationale, never edit.

Selection criteria (from the DAB spec's Testing section):
- 3 easy / 3 medium / 3 hard
- 3 easy = one each from `analytics_engineering`, `asana`, `f1` family (different hint-injection paths)
- 3 medium = diverse table-shape complexity, exercising `run_sql` / `list_tables` / `describe_table`
- 3 hard = at least one currently passing, at least one currently failing

- [ ] **Step 1: Read the failure analysis**

```bash
cat docs/ade_bench_failure_analysis.md
```

Identify currently-passing and currently-failing tasks per difficulty tier.

- [ ] **Step 2: Enumerate easy/medium/hard candidate tasks**

```bash
cd ~/repos/ade-bench && uv run python -c "
import yaml; from pathlib import Path
for d in sorted(Path('tasks').iterdir()):
    f = d / 'task.yaml'
    if not f.exists(): continue
    data = yaml.safe_load(f.read_text())
    if data.get('status')!='ready': continue
    if not any(v.get('db_type')=='duckdb' and v.get('project_type')=='dbt' for v in data.get('variants',[])):
        continue
    print(f\"{data.get('difficulty', '?')}\t{d.name}\")
" | sort
```

- [ ] **Step 3: Pick 9 tasks and record rationale**

Create `docs/superpowers/notes/2026-05-28-ade-smoke-selection.md`:

```markdown
# ADE-bench Smoke Task Selection (2026-05-28)

This file is the audit trail for `ADE_SMOKE_TASK_IDS` in `src/labrat/eval/smoke.py`.
Once `tests/baselines/ade_smoke_baseline.json` is captured, this set is **frozen**.
Changing the composition invalidates the baseline.

## Selection criteria

- 3 easy / 3 medium / 3 hard (from `task.yaml::difficulty`)
- Easy tier: one each from `analytics_engineering`, `asana`, `f1` family prefixes — exercises distinct `_FAMILY_HINTS` paths
- Medium tier: diverse table-shape complexity, exercising the agent's exploration tools
- Hard tier: at least one currently-passing, at least one currently-failing (per `docs/ade_bench_failure_analysis.md`)

## Selected tasks

### Easy
- `<task_id_1>` — analytics_engineering family, [brief why]
- `<task_id_2>` — asana family, [brief why]
- `<task_id_3>` — f1 family, [brief why]

### Medium
- `<task_id_4>` — [brief why]
- `<task_id_5>` — [brief why]
- `<task_id_6>` — [brief why]

### Hard
- `<task_id_7>` — currently passing, [brief why]
- `<task_id_8>` — currently failing per failure analysis, [brief why]
- `<task_id_9>` — [brief why]

## Notes

- These IDs are copied into `src/labrat/eval/smoke.py::ADE_SMOKE_TASK_IDS` verbatim.
- Baseline capture: `tests/baselines/ade_smoke_baseline.json` (n=3 runs × 3 attempts = 9 attempts/task).
```

Replace the placeholders with real task IDs from Step 2's enumeration and the failure analysis.

- [ ] **Step 4: Commit the selection notes**

```bash
git add docs/superpowers/notes/2026-05-28-ade-smoke-selection.md
git commit -m "docs: record ADE smoke task selection rationale"
```

---

## Phase 1 — Unified-suite scaffolding + DAB Phase 1a (1 week)

### Task 7: Create `eval/types.py` with task + result + score + report models

**Files:**
- Create: `src/labrat/eval/types.py`
- Create: `tests/unit/test_eval_types.py`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_eval_types.py`:

```python
from labrat.eval.types import (
    AggregateScore,
    BenchmarkReport,
    BenchmarkTask,
    TrialResult,
)


def test_benchmark_task_required_fields():
    task = BenchmarkTask(id="t1", benchmark="dab", prompt="What is X?")
    assert task.id == "t1"
    assert task.benchmark == "dab"
    assert task.prompt == "What is X?"
    assert task.difficulty is None
    assert task.tags == []
    assert task.config == {}


def test_benchmark_task_is_frozen():
    task = BenchmarkTask(id="t1", benchmark="dab", prompt="hi")
    import pydantic

    try:
        task.id = "t2"  # type: ignore[misc]
    except (pydantic.ValidationError, TypeError):
        return
    raise AssertionError("BenchmarkTask should be frozen")


def test_trial_result_defaults():
    r = TrialResult(task_id="t1", trial_num=0, passed=True, latency_seconds=1.5)
    assert r.tool_calls == 0
    assert r.cost_usd == 0.0
    assert r.artifact == {}
    assert r.meta == {}
    assert r.reason is None


def test_aggregate_score_carries_dimensions():
    score = AggregateScore(
        overall=0.5,
        per_task={"a": 1.0, "b": 0.0},
        by_dimension={"difficulty": {"easy": 1.0, "hard": 0.0}},
        n_tasks=2,
        n_trials=4,
        n_passes=2,
    )
    assert score.by_dimension["difficulty"]["easy"] == 1.0


def test_benchmark_report_roundtrips():
    report = BenchmarkReport(
        benchmark="dab",
        run_id="r1",
        score=AggregateScore(
            overall=1.0, per_task={"t1": 1.0}, n_tasks=1, n_trials=1, n_passes=1
        ),
        trials=[
            TrialResult(task_id="t1", trial_num=0, passed=True, latency_seconds=1.0)
        ],
        config={"hints": True},
    )
    dumped = report.model_dump()
    restored = BenchmarkReport.model_validate(dumped)
    assert restored == report
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
uv run pytest tests/unit/test_eval_types.py -v
```

Expected: ImportError on `labrat.eval.types`.

- [ ] **Step 3: Implement `src/labrat/eval/types.py`**

```python
"""Unified benchmark-suite types and protocol.

See docs/superpowers/specs/2026-05-28-unified-benchmark-suite-design.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class BenchmarkTask(BaseModel):
    """A single benchmark task. Superset of the legacy EvalCase."""

    model_config = ConfigDict(frozen=True)

    id: str
    benchmark: str
    prompt: str
    difficulty: str | None = None
    tags: list[str] = []
    config: dict[str, Any] = {}


class TrialResult(BaseModel):
    """Outcome of one trial of one task."""

    task_id: str
    trial_num: int
    passed: bool
    reason: str | None = None
    latency_seconds: float
    tool_calls: int = 0
    cost_usd: float = 0.0
    artifact: dict[str, Any] = {}
    meta: dict[str, Any] = {}


class AggregateScore(BaseModel):
    """Aggregated score for one benchmark run."""

    overall: float
    per_task: dict[str, float]
    by_dimension: dict[str, dict[str, float]] = {}
    n_tasks: int
    n_trials: int
    n_passes: int


class BenchmarkReport(BaseModel):
    """Full report of one benchmark run."""

    benchmark: str
    run_id: str
    score: AggregateScore
    trials: list[TrialResult]
    config: dict[str, Any]


@runtime_checkable
class BenchmarkSuite(Protocol):
    """The contract every benchmark integration implements."""

    name: str

    def tasks(self) -> Iterable[BenchmarkTask]: ...

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult: ...

    def aggregate(self, results: list[TrialResult]) -> AggregateScore: ...

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None: ...
```

- [ ] **Step 4: Run tests + pyright**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/unit/test_eval_types.py -v
```

Expected: all 5 tests pass, ruff clean, pyright clean.

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/types.py tests/unit/test_eval_types.py
git commit -m "feat: add unified BenchmarkSuite protocol + task/result/report types"
```

---

### Task 8: Add markdown reporting helpers

**Files:**
- Create: `src/labrat/eval/reporting.py`
- Create: `tests/unit/test_eval_reporting.py`

Markdown rendering for `BenchmarkReport`. Used by every benchmark's `report.md` output.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_eval_reporting.py`:

```python
from labrat.eval.reporting import report_to_markdown
from labrat.eval.types import AggregateScore, BenchmarkReport, TrialResult


def test_report_to_markdown_includes_score_and_failures():
    report = BenchmarkReport(
        benchmark="dab",
        run_id="r1",
        score=AggregateScore(
            overall=0.5,
            per_task={"t1": 1.0, "t2": 0.0},
            by_dimension={"dataset": {"agnews": 0.0, "yelp": 1.0}},
            n_tasks=2,
            n_trials=2,
            n_passes=1,
        ),
        trials=[
            TrialResult(task_id="t1", trial_num=0, passed=True, latency_seconds=1.0),
            TrialResult(
                task_id="t2",
                trial_num=0,
                passed=False,
                reason="validator_no_match",
                latency_seconds=2.0,
            ),
        ],
        config={"hints": True},
    )
    md = report_to_markdown(report)
    assert "# dab" in md
    assert "Overall: 0.50" in md or "Overall: 50" in md
    assert "agnews" in md
    assert "yelp" in md
    assert "validator_no_match" in md
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_eval_reporting.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `src/labrat/eval/reporting.py`**

```python
"""Markdown rendering for BenchmarkReport."""

from __future__ import annotations

from labrat.eval.types import BenchmarkReport


def report_to_markdown(report: BenchmarkReport) -> str:
    """Render a BenchmarkReport as Markdown."""
    lines: list[str] = [
        f"# {report.benchmark}",
        "",
        f"**Run ID:** `{report.run_id}`",
        "",
        "## Score",
        "",
        f"- Overall: {report.score.overall:.2f}",
        f"- Tasks: {report.score.n_tasks}",
        f"- Trials: {report.score.n_trials}",
        f"- Passes: {report.score.n_passes}",
        "",
    ]

    if report.score.by_dimension:
        lines.append("## Score by Dimension")
        lines.append("")
        for dim_name, dim_values in report.score.by_dimension.items():
            lines.append(f"### {dim_name}")
            lines.append("")
            for k, v in sorted(dim_values.items()):
                lines.append(f"- {k}: {v:.2f}")
            lines.append("")

    failures = [t for t in report.trials if not t.passed]
    if failures:
        lines.append("## Failures")
        lines.append("")
        for t in failures:
            lines.append(f"- `{t.task_id}` (trial {t.trial_num}): {t.reason or 'no reason'}")
        lines.append("")

    lines.append("## Config")
    lines.append("")
    lines.append("```json")
    import json

    lines.append(json.dumps(report.config, indent=2, sort_keys=True))
    lines.append("```")

    return "\n".join(lines)
```

- [ ] **Step 4: Run pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/unit/test_eval_reporting.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/reporting.py tests/unit/test_eval_reporting.py
git commit -m "feat: add markdown rendering for BenchmarkReport"
```

---

### Task 9: Create `eval/smoke.py` with `SubsetSuite` and `ade_smoke_suite()`

**Files:**
- Create: `src/labrat/eval/smoke.py`
- Create: `tests/unit/test_eval_smoke.py`

`SubsetSuite` wraps any `BenchmarkSuite` to expose only a fixed task subset. `ade_smoke_suite()` is a factory returning `SubsetSuite(AdeBenchSuite(), ADE_SMOKE_TASK_IDS)` — but `AdeBenchSuite` doesn't exist yet (Task 12). The factory imports from the eventual location; the unit test uses a fake suite.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_eval_smoke.py`:

```python
from collections.abc import Iterable
from pathlib import Path

from labrat.eval.smoke import SubsetSuite
from labrat.eval.types import AggregateScore, BenchmarkReport, BenchmarkTask, TrialResult


class _FakeSuite:
    name = "fake"

    def __init__(self):
        self._tasks = [
            BenchmarkTask(id="a", benchmark="fake", prompt="?"),
            BenchmarkTask(id="b", benchmark="fake", prompt="?"),
            BenchmarkTask(id="c", benchmark="fake", prompt="?"),
        ]
        self.run_trial_calls: list[tuple[str, int]] = []

    def tasks(self) -> Iterable[BenchmarkTask]:
        return self._tasks

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult:
        self.run_trial_calls.append((task.id, trial_num))
        return TrialResult(task_id=task.id, trial_num=trial_num, passed=True, latency_seconds=0.0)

    def aggregate(self, results: list[TrialResult]) -> AggregateScore:
        return AggregateScore(
            overall=1.0,
            per_task={r.task_id: 1.0 for r in results},
            n_tasks=len(results),
            n_trials=len(results),
            n_passes=sum(1 for r in results if r.passed),
        )

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None:
        pass


def test_subset_suite_filters_tasks():
    parent = _FakeSuite()
    sub = SubsetSuite(parent, ["a", "c"], name="fake-sub")
    task_ids = sorted(t.id for t in sub.tasks())
    assert task_ids == ["a", "c"]


def test_subset_suite_uses_provided_name():
    parent = _FakeSuite()
    assert SubsetSuite(parent, ["a"], name="explicit").name == "explicit"


def test_subset_suite_default_name_extends_parent():
    parent = _FakeSuite()
    assert SubsetSuite(parent, ["a"]).name == "fake-subset"


async def test_subset_suite_delegates_run_trial(tmp_path):
    parent = _FakeSuite()
    sub = SubsetSuite(parent, ["b"])
    task = next(t for t in sub.tasks() if t.id == "b")
    result = await sub.run_trial(task, 0, tmp_path)
    assert result.task_id == "b"
    assert parent.run_trial_calls == [("b", 0)]


def test_subset_suite_write_submission_is_noop(tmp_path):
    parent = _FakeSuite()
    sub = SubsetSuite(parent, ["a"])
    report = BenchmarkReport(
        benchmark="fake",
        run_id="r",
        score=AggregateScore(overall=1.0, per_task={}, n_tasks=0, n_trials=0, n_passes=0),
        trials=[],
        config={},
    )
    sub.write_submission(report, tmp_path)
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_eval_smoke.py -v
```

Expected: ImportError on `labrat.eval.smoke`.

- [ ] **Step 3: Implement `src/labrat/eval/smoke.py`**

```python
"""Smoke regression: SubsetSuite wrapper + ADE smoke task list.

Per docs/superpowers/specs/2026-05-28-unified-benchmark-suite-design.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from labrat.eval.types import (
    AggregateScore,
    BenchmarkReport,
    BenchmarkSuite,
    BenchmarkTask,
    TrialResult,
)


class SubsetSuite:
    """Wraps a BenchmarkSuite to expose only a fixed task subset.

    Delegates run_trial / aggregate to the parent. write_submission is a no-op
    (subsets never produce submissions).
    """

    def __init__(
        self,
        parent: BenchmarkSuite,
        task_ids: list[str],
        name: str | None = None,
    ) -> None:
        self._parent = parent
        self._task_ids: set[str] = set(task_ids)
        self.name: str = name or f"{parent.name}-subset"

    def tasks(self) -> Iterable[BenchmarkTask]:
        return [t for t in self._parent.tasks() if t.id in self._task_ids]

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult:
        return await self._parent.run_trial(task, trial_num, scratch_dir)

    def aggregate(self, results: list[TrialResult]) -> AggregateScore:
        return self._parent.aggregate(results)

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None:
        return None


# Frozen smoke task set. Populated from docs/superpowers/notes/2026-05-28-ade-smoke-selection.md.
# Changing this invalidates tests/baselines/ade_smoke_baseline.json.
ADE_SMOKE_TASK_IDS: list[str] = [
    # Populated in Task 11 once AdeBenchSuite exists.
]


def ade_smoke_suite() -> BenchmarkSuite:
    """Build the ADE smoke regression suite — frozen task subset of AdeBenchSuite."""
    # Import locally to avoid a top-level cycle if AdeBenchSuite ever depends on smoke.
    from labrat.eval.benchmarks.ade_bench.suite import AdeBenchSuite

    return SubsetSuite(AdeBenchSuite(), ADE_SMOKE_TASK_IDS, name="ade-smoke")
```

- [ ] **Step 4: Pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/unit/test_eval_smoke.py -v
```

Expected: 5 tests pass. (`ade_smoke_suite()` is not exercised yet — `AdeBenchSuite` doesn't exist.)

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/smoke.py tests/unit/test_eval_smoke.py
git commit -m "feat: SubsetSuite + ADE smoke suite skeleton"
```

---

### Task 10: Create the `eval/benchmarks/` package skeleton

**Files:**
- Create: `src/labrat/eval/benchmarks/__init__.py` (empty)
- Create: `src/labrat/eval/benchmarks/ade_bench/__init__.py` (empty)
- Create: `src/labrat/eval/benchmarks/dab/__init__.py` (empty)
- Create: `src/labrat/eval/benchmarks/spider2_dbt/README.md`

Stub directories so subsequent tasks can import cleanly.

- [ ] **Step 1: Create the package init files**

```bash
mkdir -p src/labrat/eval/benchmarks/ade_bench src/labrat/eval/benchmarks/dab src/labrat/eval/benchmarks/spider2_dbt
touch src/labrat/eval/benchmarks/__init__.py
touch src/labrat/eval/benchmarks/ade_bench/__init__.py
touch src/labrat/eval/benchmarks/dab/__init__.py
```

- [ ] **Step 2: Write the Spider2 stub README**

`src/labrat/eval/benchmarks/spider2_dbt/README.md`:

```markdown
# Spider2-DBT Benchmark (stub)

Implementation is deferred to a follow-on spec. This README captures design intent
so the unified-suite decisions don't get forgotten.

## When implemented

- Tasks come from `~/repos/Spider2/spider2-dbt/examples/spider2-dbt.jsonl` (67 entries).
- `Spider2DbtSuite.run_trial()` will copy the dbt project to `scratch_dir`, build a
  `ToolContext` over the starter DuckDB, invoke the agent via `LabRatAgentDriver`
  (extracted in Phase 4), then table-match against
  `~/repos/Spider2/spider2-dbt/evaluation_suite/gold/<task_id>/<db>.duckdb` using
  the `duckdb_match` / `tables_match` logic ported from Spider2's `evaluate.py`.
- `artifact = {"type": "duckdb_state", "payload": {"db_path": "..."}}`.
- Dataset triage (Fivetran `_tmp` unsolvability, allowlist for "fair score") is a
  Spider2-spec concern, not architectural.

## See also

- `docs/superpowers/specs/2026-05-28-unified-benchmark-suite-design.md` — protocol
- Memory: `project_spider2_revisit.md`, `project_spider2_autoresearch.md`
```

- [ ] **Step 3: Run pyright and tests**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q
```

Expected: clean. Empty `__init__.py` files don't break anything.

- [ ] **Step 4: Commit**

```bash
git add src/labrat/eval/benchmarks/
git commit -m "feat: scaffold eval/benchmarks/ package with Spider2 stub README"
```

---

### Task 11: Port `AdeBenchSuite` to `eval/benchmarks/ade_bench/`

**Files:**
- Create: `src/labrat/eval/benchmarks/ade_bench/suite.py`
- Create: `src/labrat/eval/benchmarks/ade_bench/external_runner.py`
- Create: `src/labrat/eval/benchmarks/ade_bench/reporter.py`
- Create: `tests/unit/test_ade_bench_port.py`
- Read for reference: `src/labrat/eval/suites/ade_bench.py`, `src/labrat/eval/runners/ade_bench_runner.py`

Port the existing ADE-bench integration to the new layout. Same logic, new file paths, implements `BenchmarkSuite`. Keep legacy files in place — Task 13 deletes them after the port-acceptance test passes.

- [ ] **Step 1: Re-read the legacy files**

```bash
cat src/labrat/eval/suites/ade_bench.py
cat src/labrat/eval/runners/ade_bench_runner.py
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_ade_bench_port.py`:

```python
from pathlib import Path
from unittest.mock import patch

from labrat.eval.benchmarks.ade_bench.suite import AdeBenchSuite
from labrat.eval.types import BenchmarkSuite, TrialResult


def test_ade_bench_suite_implements_protocol():
    suite = AdeBenchSuite()
    assert isinstance(suite, BenchmarkSuite)
    assert suite.name == "ade-bench"


def test_ade_bench_suite_enumerates_tasks(tmp_path):
    tasks_dir = tmp_path / "tasks" / "fake_easy_01"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "task.yaml").write_text(
        """
task_id: fake_easy_01
status: ready
difficulty: easy
tags: [demo]
variants:
  - db_type: duckdb
    project_type: dbt
prompts:
  - key: base
    prompt: Do the thing.
"""
    )

    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    tasks = list(suite.tasks())
    assert len(tasks) == 1
    t = tasks[0]
    assert t.id == "fake_easy_01"
    assert t.benchmark == "ade_bench"
    assert t.difficulty == "easy"
    assert t.prompt == "Do the thing."


def test_ade_bench_suite_skips_non_ready(tmp_path):
    d = tmp_path / "tasks" / "draft_task"
    d.mkdir(parents=True)
    (d / "task.yaml").write_text(
        """
task_id: draft_task
status: draft
variants:
  - db_type: duckdb
    project_type: dbt
prompts:
  - key: base
    prompt: x
"""
    )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    assert list(suite.tasks()) == []


def test_ade_bench_suite_skips_non_duckdb(tmp_path):
    d = tmp_path / "tasks" / "pg_task"
    d.mkdir(parents=True)
    (d / "task.yaml").write_text(
        """
task_id: pg_task
status: ready
variants:
  - db_type: postgres
    project_type: dbt
prompts:
  - key: base
    prompt: x
"""
    )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    assert list(suite.tasks()) == []


async def test_ade_bench_suite_run_trial_maps_passing_attempt(tmp_path):
    tasks_dir = tmp_path / "tasks" / "fake_easy_01"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "task.yaml").write_text(
        """
task_id: fake_easy_01
status: ready
difficulty: easy
variants:
  - db_type: duckdb
    project_type: dbt
prompts:
  - key: base
    prompt: Do the thing.
"""
    )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    task = next(iter(suite.tasks()))

    # Patch the external runner to return a passing trial.
    with patch(
        "labrat.eval.benchmarks.ade_bench.external_runner.run_one"
    ) as mock_run_one:
        mock_run_one.return_value = {
            "task_id": "fake_easy_01",
            "is_resolved": True,
            "failure_mode": "none",
            "runtime_ms": 12345,
            "experiment_dir": str(tmp_path / "experiments" / "e1"),
        }
        result = await suite.run_trial(task, 0, tmp_path / "scratch")

    assert isinstance(result, TrialResult)
    assert result.passed is True
    assert result.task_id == "fake_easy_01"
    assert result.trial_num == 0
    assert result.latency_seconds == 12345 / 1000.0


def test_ade_bench_suite_aggregate_stratifies_by_difficulty(tmp_path):
    for diff in ("easy", "medium"):
        d = tmp_path / "tasks" / f"fake_{diff}_01"
        d.mkdir(parents=True)
        (d / "task.yaml").write_text(
            f"""
task_id: fake_{diff}_01
status: ready
difficulty: {diff}
variants:
  - db_type: duckdb
    project_type: dbt
prompts:
  - key: base
    prompt: x
"""
        )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    list(suite.tasks())  # populate cache
    results = [
        TrialResult(task_id="fake_easy_01", trial_num=0, passed=True, latency_seconds=1.0),
        TrialResult(task_id="fake_medium_01", trial_num=0, passed=False, latency_seconds=1.0),
    ]
    score = suite.aggregate(results)
    assert score.by_dimension["difficulty"]["easy"] == 1.0
    assert score.by_dimension["difficulty"]["medium"] == 0.0
    assert score.overall == 0.5
```

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest tests/unit/test_ade_bench_port.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement `external_runner.py`**

`src/labrat/eval/benchmarks/ade_bench/external_runner.py`:

```python
"""Shells to the external `ade` CLI for one task.

Ported from src/labrat/eval/runners/ade_bench_runner.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def run_one(
    task_id: str,
    *,
    ade_bench_dir: Path,
    agent: str = "labrat_local",
    no_diffs: bool = True,
) -> dict[str, Any]:
    """Run one task via `ade run` and return the parsed trial dict.

    Returns a dict with keys: task_id, is_resolved, failure_mode, runtime_ms,
    experiment_dir.
    """
    cmd = [
        "uv",
        "run",
        "ade",
        "run",
        task_id,
        "--db",
        "duckdb",
        "--project-type",
        "dbt",
        "--agent",
        agent,
        "--n-attempts",
        "1",
    ]
    if no_diffs:
        cmd.append("--no-diffs")
    subprocess.run(cmd, cwd=ade_bench_dir, check=True)

    # Locate the most recent experiment dir and parse its results.
    experiments = ade_bench_dir / "experiments"
    if not experiments.exists():
        return {
            "task_id": task_id,
            "is_resolved": False,
            "failure_mode": "no_experiments_dir",
            "runtime_ms": 0,
            "experiment_dir": None,
        }
    most_recent = max(experiments.iterdir(), key=lambda p: p.stat().st_mtime)
    metadata_path = most_recent / "results_metadata.jsonl"
    if not metadata_path.exists():
        return {
            "task_id": task_id,
            "is_resolved": False,
            "failure_mode": "no_metadata",
            "runtime_ms": 0,
            "experiment_dir": str(most_recent),
        }

    with metadata_path.open() as f:
        for line in f:
            row = json.loads(line.strip())
            if row.get("task_id") == task_id:
                return {
                    "task_id": task_id,
                    "is_resolved": bool(row.get("is_resolved", False)),
                    "failure_mode": row.get("failure_mode", "none"),
                    "runtime_ms": int(row.get("runtime_ms") or 0),
                    "experiment_dir": str(most_recent),
                }

    return {
        "task_id": task_id,
        "is_resolved": False,
        "failure_mode": "task_not_in_metadata",
        "runtime_ms": 0,
        "experiment_dir": str(most_recent),
    }
```

- [ ] **Step 5: Implement `reporter.py`**

`src/labrat/eval/benchmarks/ade_bench/reporter.py`:

```python
"""Maps a parsed ADE trial dict to TrialResult.

Ported from src/labrat/eval/runners/ade_bench_runner.py (_map_trial).
"""

from __future__ import annotations

from typing import Any

from labrat.eval.types import TrialResult

_INFRA_FAILURE_MODES = {
    "setup_failed",
    "setup_timeout",
    "agent_setup_timeout",
    "agent_timeout",
    "test_timeout",
    "unknown_agent_error",
    "unknown_harness_error",
    "harness_panic",
    "parse_error",
    "fatal_llm_parse_error",
    "context_length_exceeded",
    "quota_exceeded",
    "no_experiments_dir",
    "no_metadata",
    "task_not_in_metadata",
}


def trial_dict_to_result(trial: dict[str, Any], trial_num: int) -> TrialResult:
    failure_mode: str = trial.get("failure_mode", "none") or "none"
    is_resolved: bool = bool(trial.get("is_resolved"))
    runtime_ms: int = int(trial.get("runtime_ms") or 0)

    if is_resolved:
        passed = True
        reason = None
    elif failure_mode in _INFRA_FAILURE_MODES:
        passed = False
        reason = f"infra:{failure_mode}"
    else:
        passed = False
        reason = failure_mode

    return TrialResult(
        task_id=trial["task_id"],
        trial_num=trial_num,
        passed=passed,
        reason=reason,
        latency_seconds=runtime_ms / 1000.0,
        artifact={
            "type": "container_state",
            "payload": {"experiment_dir": trial.get("experiment_dir")},
        },
    )
```

- [ ] **Step 6: Implement `suite.py`**

`src/labrat/eval/benchmarks/ade_bench/suite.py`:

```python
"""ADE-bench BenchmarkSuite — port of legacy eval/suites/ade_bench.py."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from labrat.eval.benchmarks.ade_bench import external_runner, reporter
from labrat.eval.types import (
    AggregateScore,
    BenchmarkReport,
    BenchmarkTask,
    TrialResult,
)


class AdeBenchSuite:
    """Reads task definitions from an ADE-bench checkout and shells to the ade CLI."""

    name = "ade-bench"

    def __init__(
        self,
        ade_bench_dir: Path | None = None,
        status_filter: str = "ready",
    ) -> None:
        self._dir = (
            ade_bench_dir
            or Path(os.environ.get("ADE_BENCH_DIR", "~/repos/ade-bench")).expanduser()
        )
        self._status_filter = status_filter
        self._tasks_cache: list[BenchmarkTask] | None = None

    def tasks(self) -> Iterable[BenchmarkTask]:
        if self._tasks_cache is None:
            self._tasks_cache = self._load_tasks()
        return self._tasks_cache

    def _load_tasks(self) -> list[BenchmarkTask]:
        tasks_dir = self._dir / "tasks"
        if not tasks_dir.exists():
            return []

        result: list[BenchmarkTask] = []
        for task_yaml in sorted(tasks_dir.glob("*/task.yaml")):
            data = yaml.safe_load(task_yaml.read_text())
            if not isinstance(data, dict):
                continue

            if self._status_filter and data.get("status") != self._status_filter:
                continue

            variants = data.get("variants") or []
            if not any(
                v.get("db_type") == "duckdb" and v.get("project_type", "dbt") == "dbt"
                for v in variants
            ):
                continue

            prompts = data.get("prompts") or []
            base_prompt = next(
                (p["prompt"] for p in prompts if p.get("key") == "base"),
                prompts[0]["prompt"] if prompts else "",
            )
            if not base_prompt:
                continue

            result.append(
                BenchmarkTask(
                    id=data["task_id"],
                    benchmark="ade_bench",
                    prompt=base_prompt,
                    difficulty=data.get("difficulty"),
                    tags=list(data.get("tags") or []),
                    config={"variant": "duckdb_dbt"},
                )
            )
        return result

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult:
        trial_dict = external_runner.run_one(
            task.id, ade_bench_dir=self._dir
        )
        return reporter.trial_dict_to_result(trial_dict, trial_num=trial_num)

    def aggregate(self, results: list[TrialResult]) -> AggregateScore:
        if not results:
            return AggregateScore(
                overall=0.0, per_task={}, n_tasks=0, n_trials=0, n_passes=0
            )

        per_task: dict[str, list[bool]] = {}
        for r in results:
            per_task.setdefault(r.task_id, []).append(r.passed)
        per_task_pass_rate = {
            tid: sum(passes) / len(passes) for tid, passes in per_task.items()
        }

        # Stratify by difficulty using cached task definitions.
        difficulty_by_id = {t.id: t.difficulty for t in self.tasks()}
        by_difficulty: dict[str, list[float]] = {}
        for tid, pr in per_task_pass_rate.items():
            diff = difficulty_by_id.get(tid) or "unknown"
            by_difficulty.setdefault(diff, []).append(pr)

        return AggregateScore(
            overall=sum(per_task_pass_rate.values()) / len(per_task_pass_rate),
            per_task=per_task_pass_rate,
            by_dimension={
                "difficulty": {
                    diff: sum(vs) / len(vs) for diff, vs in by_difficulty.items()
                }
            },
            n_tasks=len(per_task),
            n_trials=len(results),
            n_passes=sum(1 for r in results if r.passed),
        )

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None:
        # ADE-bench has no submission format.
        return None
```

- [ ] **Step 7: Pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/unit/test_ade_bench_port.py -v
```

Expected: all ade_bench port tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/labrat/eval/benchmarks/ade_bench/ tests/unit/test_ade_bench_port.py
git commit -m "feat: port AdeBenchSuite to eval/benchmarks/ade_bench/"
```

---

### Task 12: Port-acceptance test — legacy vs new produce identical results

**Files:**
- Create: `scripts/spikes/ade_port_acceptance.py`

Before deleting the legacy paths, prove the new shape produces the same per-task `is_resolved` / `failure_mode` / `runtime_ms` for a known task. This is a one-shot acceptance test, not a permanent test fixture.

- [ ] **Step 1: Write the acceptance script**

`scripts/spikes/ade_port_acceptance.py`:

```python
"""Acceptance test: legacy AdeBenchSuite/Runner vs new benchmarks/ade_bench produce
identical per-trial results for one easy task.

Exit 0 on match, 1 on mismatch.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from labrat.eval.benchmarks.ade_bench.suite import AdeBenchSuite as NewSuite
from labrat.eval.runners.ade_bench_runner import AdeBenchRunner as LegacyRunner
from labrat.eval.suites.ade_bench import AdeBenchSuite as LegacySuite

# Pick an easy task ID that's known to complete quickly. Update if the chosen
# task no longer behaves predictably.
TEST_TASK_ID = "<easy_task_id_from_task_6_selection>"


async def main() -> int:
    ade_bench_dir = Path(
        os.environ.get("ADE_BENCH_DIR", "~/repos/ade-bench")
    ).expanduser()

    # Legacy path
    legacy_suite = LegacySuite(ade_bench_dir=ade_bench_dir)
    legacy_cases = [c for c in legacy_suite.cases if c.id == TEST_TASK_ID]
    if not legacy_cases:
        print(f"Task {TEST_TASK_ID} not found in legacy suite", file=sys.stderr)
        return 1
    legacy_runner = LegacyRunner(
        cases=legacy_cases,
        ade_bench_dir=ade_bench_dir,
        agent="labrat_local",
        n_concurrent_trials=1,
        n_attempts=1,
    )
    legacy_report = await legacy_runner.run()
    legacy_result = legacy_report.results[0]

    # New path
    new_suite = NewSuite(ade_bench_dir=ade_bench_dir)
    new_tasks = [t for t in new_suite.tasks() if t.id == TEST_TASK_ID]
    if not new_tasks:
        print(f"Task {TEST_TASK_ID} not found in new suite", file=sys.stderr)
        return 1
    scratch = ade_bench_dir / "experiments" / "_port_acceptance_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    new_result = await new_suite.run_trial(new_tasks[0], trial_num=0, scratch_dir=scratch)

    print(f"Legacy: passed={legacy_result.status} latency={legacy_result.latency_seconds:.2f}")
    print(f"New:    passed={new_result.passed}   latency={new_result.latency_seconds:.2f}")

    legacy_pass = legacy_result.status == "correct"
    new_pass = new_result.passed
    if legacy_pass != new_pass:
        print(f"MISMATCH: legacy pass={legacy_pass} new pass={new_pass}", file=sys.stderr)
        return 1

    # Latencies can vary across separate runs since these are independent invocations.
    # Just require pass/fail to match.
    print("ACCEPTANCE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

Replace `TEST_TASK_ID` with one of the easy tasks selected in Task 6.

- [ ] **Step 2: Run the acceptance script**

```bash
uv run python scripts/spikes/ade_port_acceptance.py
```

Expected: `ACCEPTANCE PASS`, exit 0.

If the acceptance test fails: investigate the difference, fix the new path until it matches, do not proceed to Task 13.

- [ ] **Step 3: Commit the acceptance script**

```bash
git add scripts/spikes/ade_port_acceptance.py
git commit -m "test: ADE-bench port-acceptance spike script"
```

---

### Task 13: Delete legacy ADE-bench code paths

**Files:**
- Delete: `src/labrat/eval/suites/ade_bench.py`
- Delete: `src/labrat/eval/runners/ade_bench_runner.py`
- Modify: `scripts/eval_ade_bench.py` to use the new `AdeBenchSuite`

- [ ] **Step 1: Inspect what currently imports the legacy paths**

```bash
grep -rn "from labrat.eval.suites.ade_bench\|from labrat.eval.runners.ade_bench_runner\|eval.suites.ade_bench\|eval.runners.ade_bench_runner" src/ tests/ scripts/
```

Expected callers: `scripts/eval_ade_bench.py`, perhaps `tests/unit/test_ade_bench.py` (legacy test).

- [ ] **Step 2: Rewire `scripts/eval_ade_bench.py`**

Read the existing script:
```bash
cat scripts/eval_ade_bench.py
```

Update the imports and the runner construction to use `labrat.eval.benchmarks.ade_bench.suite.AdeBenchSuite`. Since the orchestrator doesn't exist yet, the script needs a minimal inline runner loop that mirrors what the legacy `AdeBenchRunner.run()` did (call `suite.run_trial` for each task × attempt, collect `TrialResult`s, call `suite.aggregate`).

Write the inline loop as a short, focused block (~30 lines) — it will move into `BenchmarkOrchestrator` in Phase 4, so keep it benchmark-agnostic.

```python
# scripts/eval_ade_bench.py (excerpt — adapt to existing argparse / CLI shape)

import asyncio
from pathlib import Path

from labrat.eval.benchmarks.ade_bench.suite import AdeBenchSuite
from labrat.eval.reporting import report_to_markdown
from labrat.eval.types import BenchmarkReport, TrialResult


async def run(suite: AdeBenchSuite, task_ids: list[str] | None, n_attempts: int) -> BenchmarkReport:
    tasks = [t for t in suite.tasks() if task_ids is None or t.id in set(task_ids)]
    trials: list[TrialResult] = []
    for task in tasks:
        # Best-of-k: stop early if any attempt passes
        for attempt in range(n_attempts):
            scratch = Path("runs") / "ade_bench" / "scratch" / f"{task.id}__trial{attempt}"
            scratch.mkdir(parents=True, exist_ok=True)
            r = await suite.run_trial(task, attempt, scratch)
            trials.append(r)
            if r.passed:
                break
    score = suite.aggregate(trials)
    return BenchmarkReport(
        benchmark=suite.name,
        run_id="ade-bench-local",
        score=score,
        trials=trials,
        config={"n_attempts": n_attempts, "tasks": task_ids},
    )


# Existing CLI plumbing wires `run` to argparse, prints `report_to_markdown(report)`.
```

- [ ] **Step 3: Delete the legacy files**

```bash
git rm src/labrat/eval/suites/ade_bench.py src/labrat/eval/runners/ade_bench_runner.py
```

If `src/labrat/eval/suites/` becomes empty after this and contains only `__init__.py`, leave it — `bird.py`, `custom_scenarios.py`, `latency.py` still live there.

If `src/labrat/eval/runners/` becomes empty (only `__init__.py`), leave it too — preserves git history clarity, will be cleaned in Phase 6.

- [ ] **Step 4: Delete or migrate the legacy test**

```bash
grep -ln "from labrat.eval.suites.ade_bench\|from labrat.eval.runners.ade_bench_runner" tests/
```

For each match, port the test to use the new `AdeBenchSuite` if the test's behavior is still relevant. If the test duplicates `tests/unit/test_ade_bench_port.py`, delete it.

- [ ] **Step 5: Pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q
```

Expected: clean. All non-LLM tests pass.

- [ ] **Step 6: Smoke check — `scripts/eval_ade_bench.py` runs**

```bash
uv run scripts/eval_ade_bench.py --tasks <one_easy_task_id>
```

Expected: the script completes, prints a markdown report, exits 0. The task either passes or fails identically to how it would have under the legacy path (already verified in Task 12).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete legacy ADE-bench suite/runner; rewire eval_ade_bench.py"
```

---

### Task 14: Populate `ADE_SMOKE_TASK_IDS` and write smoke regression script

**Files:**
- Modify: `src/labrat/eval/smoke.py` (replace empty list with selected task IDs)
- Create: `scripts/run_smoke_regression.py`
- Create: `tests/unit/test_smoke_regression_script.py`

- [ ] **Step 1: Populate `ADE_SMOKE_TASK_IDS`**

Open `src/labrat/eval/smoke.py` and replace the empty `ADE_SMOKE_TASK_IDS` list with the 9 task IDs from `docs/superpowers/notes/2026-05-28-ade-smoke-selection.md`.

```python
ADE_SMOKE_TASK_IDS: list[str] = [
    # easy
    "<task_id_1>",
    "<task_id_2>",
    "<task_id_3>",
    # medium
    "<task_id_4>",
    "<task_id_5>",
    "<task_id_6>",
    # hard
    "<task_id_7>",
    "<task_id_8>",
    "<task_id_9>",
]
```

- [ ] **Step 2: Write the failing test for the regression script**

`tests/unit/test_smoke_regression_script.py`:

```python
"""Tests for scripts.run_smoke_regression — the comparison logic, not the live runs."""

from __future__ import annotations

from scripts.run_smoke_regression import RegressionVerdict, compare_against_baseline


def test_compare_pass_when_all_within_envelope():
    baseline = {"t1": {"passes": 8, "attempts": 9}, "t2": {"passes": 9, "attempts": 9}}
    current = {"t1": 8 / 9, "t2": 9 / 9}
    verdict = compare_against_baseline(baseline, current)
    assert verdict.kind == "pass"


def test_compare_hard_fail_on_strong_drop():
    baseline = {"t1": {"passes": 8, "attempts": 9}}
    current = {"t1": 3 / 9}
    verdict = compare_against_baseline(baseline, current)
    assert verdict.kind == "hard_fail"
    assert "t1" in verdict.message


def test_compare_soft_signal_on_small_drop():
    baseline = {
        "t1": {"passes": 9, "attempts": 9},
        "t2": {"passes": 9, "attempts": 9},
        "t3": {"passes": 9, "attempts": 9},
    }
    current = {"t1": 1.0, "t2": 1.0, "t3": 0.66}  # one task drops a bit
    verdict = compare_against_baseline(baseline, current)
    assert verdict.kind in ("soft_signal", "pass")


def test_verdict_exit_codes():
    assert RegressionVerdict(kind="pass", message="").exit_code == 0
    assert RegressionVerdict(kind="soft_signal", message="").exit_code == 0
    assert RegressionVerdict(kind="hard_fail", message="").exit_code == 1
```

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest tests/unit/test_smoke_regression_script.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement `scripts/run_smoke_regression.py`**

```python
"""Run ADE smoke regression and compare against baseline.

Usage:
  uv run python scripts/run_smoke_regression.py --capture       # write baseline
  uv run python scripts/run_smoke_regression.py --check         # run + compare
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from labrat.eval.smoke import ade_smoke_suite

BASELINE_PATH = Path("tests/baselines/ade_smoke_baseline.json")

# Regression thresholds — from spec.
# Hard fail: any task previously at >=7/9 drops below 4/9.
# Soft signal: aggregate resolved-task count drops by 1.
_HARD_FAIL_BASELINE_PASSES = 7
_HARD_FAIL_CURRENT_THRESHOLD = 4 / 9


@dataclass
class RegressionVerdict:
    kind: Literal["pass", "soft_signal", "hard_fail"]
    message: str

    @property
    def exit_code(self) -> int:
        return 1 if self.kind == "hard_fail" else 0


def compare_against_baseline(
    baseline: dict[str, dict[str, int]],
    current: dict[str, float],
) -> RegressionVerdict:
    """baseline maps task_id → {passes, attempts}; current maps task_id → pass-rate."""
    hard_fail_tasks: list[str] = []
    for tid, base in baseline.items():
        if tid not in current:
            continue
        if base["passes"] >= _HARD_FAIL_BASELINE_PASSES and current[tid] < _HARD_FAIL_CURRENT_THRESHOLD:
            hard_fail_tasks.append(tid)

    if hard_fail_tasks:
        return RegressionVerdict(
            kind="hard_fail",
            message=f"Hard fail on tasks: {hard_fail_tasks}",
        )

    baseline_resolved = sum(
        1 for tid, base in baseline.items() if base["passes"] / base["attempts"] >= 0.5
    )
    current_resolved = sum(1 for tid, pr in current.items() if pr >= 0.5)
    if current_resolved < baseline_resolved:
        drop = baseline_resolved - current_resolved
        return RegressionVerdict(
            kind="soft_signal",
            message=f"Resolved-task count dropped by {drop} ({baseline_resolved} → {current_resolved})",
        )

    return RegressionVerdict(kind="pass", message="All tasks within envelope")


async def _run_smoke(n_attempts: int) -> dict[str, float]:
    """Run the smoke suite once at n_attempts per task; return per-task pass-rate."""
    suite = ade_smoke_suite()
    tasks = list(suite.tasks())
    per_task_passes: dict[str, int] = {t.id: 0 for t in tasks}
    for task in tasks:
        for attempt in range(n_attempts):
            scratch = Path("runs") / "ade_smoke" / "scratch" / f"{task.id}__attempt{attempt}"
            scratch.mkdir(parents=True, exist_ok=True)
            r = await suite.run_trial(task, attempt, scratch)
            if r.passed:
                per_task_passes[task.id] += 1
                break  # best-of-k early exit
    return {tid: per_task_passes[tid] / n_attempts for tid in per_task_passes}


def _capture(args: argparse.Namespace) -> int:
    """Capture baseline: run smoke n_runs times × n_attempts each."""
    n_runs: int = args.n_runs
    n_attempts: int = args.n_attempts
    aggregate: dict[str, int] = {}

    for run_i in range(n_runs):
        print(f"=== Smoke run {run_i + 1}/{n_runs} ===", flush=True)
        per_task = asyncio.run(_run_smoke(n_attempts))
        for tid, pr in per_task.items():
            passes = int(round(pr * n_attempts))
            aggregate[tid] = aggregate.get(tid, 0) + passes

    baseline = {
        tid: {"passes": passes, "attempts": n_runs * n_attempts}
        for tid, passes in aggregate.items()
    }
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    print(f"Baseline written to {BASELINE_PATH}")
    return 0


def _check(args: argparse.Namespace) -> int:
    """Run smoke once and compare against baseline."""
    if not BASELINE_PATH.exists():
        print(f"No baseline at {BASELINE_PATH} — run --capture first", file=sys.stderr)
        return 2

    baseline = json.loads(BASELINE_PATH.read_text())
    current = asyncio.run(_run_smoke(args.n_attempts))
    verdict = compare_against_baseline(baseline, current)
    print(f"Verdict: {verdict.kind} — {verdict.message}")
    return verdict.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADE smoke regression check")
    sub = parser.add_subparsers(dest="mode", required=True)

    cap = sub.add_parser("capture")
    cap.add_argument("--n-runs", type=int, default=3)
    cap.add_argument("--n-attempts", type=int, default=3)
    cap.set_defaults(func=_capture)

    chk = sub.add_parser("check")
    chk.add_argument("--n-attempts", type=int, default=3)
    chk.set_defaults(func=_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/unit/test_smoke_regression_script.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/labrat/eval/smoke.py scripts/run_smoke_regression.py tests/unit/test_smoke_regression_script.py
git commit -m "feat: populate ADE smoke task IDs; add run_smoke_regression.py"
```

---

### Task 15: Capture the smoke baseline

**Files:**
- Create: `tests/baselines/ade_smoke_baseline.json` (output of the capture run)

Run smoke 3× through the new code path. Each run uses n_attempts=3 (best-of-3). The baseline records `passes / attempts` per task (9 total attempts per task: 3 runs × 3 attempts, with early exit on first pass per run).

This takes ~30–60 min and uses real LLM calls. Run from a dev shell (not CI).

- [ ] **Step 1: Capture the baseline**

```bash
uv run python scripts/run_smoke_regression.py capture --n-runs 3 --n-attempts 3
```

Expected: writes `tests/baselines/ade_smoke_baseline.json` with one entry per task.

- [ ] **Step 2: Eyeball the baseline**

```bash
cat tests/baselines/ade_smoke_baseline.json
```

Sanity checks:
- 9 entries (one per smoke task)
- `attempts` field is 9 for each task (3 × 3)
- Currently-passing easy tasks have `passes >= 8`
- Currently-failing hard tasks have `passes <= 3`

If any sanity check fails: investigate (broken port? wrong task IDs?) before committing.

- [ ] **Step 3: Verify the regression check itself works**

```bash
uv run python scripts/run_smoke_regression.py check --n-attempts 3
```

Expected: verdict `pass` (the check just-captured against the just-captured baseline should match within envelope).

- [ ] **Step 4: Commit the baseline**

```bash
git add tests/baselines/ade_smoke_baseline.json
git commit -m "test: capture ADE smoke baseline on new BenchmarkSuite shape"
```

**Exit gate for the smoke-regression portion of Phase 1:** baseline is committed, regression check produces verdict `pass` when run immediately after capture. From now on, this check runs at every DAB phase exit.

---

### Task 16: Multi-DB `ToolContext` with backwards-compat shims

**Files:**
- Modify: `src/labrat/agent/tools/base.py` (or wherever `ToolContext` lives — confirm)
- Create: `tests/unit/test_multi_db_tool_context.py`

The DAB integration needs `ToolContext` to hold multiple connections by name. Existing single-DB callers (TUI, ADE-bench, existing tests) must keep working unchanged via shims.

- [ ] **Step 1: Locate `ToolContext`**

```bash
grep -rn "class ToolContext" src/labrat/
```

Open the file. Note the current shape — likely `connection: Connection`, `catalog: Catalog`, plus history, memory, etc.

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_multi_db_tool_context.py`:

```python
from labrat.agent.tools.base import ToolContext  # adjust import to actual location


def test_tool_context_legacy_single_db_construction():
    """Legacy construction: ctx.connection / ctx.catalog work as before."""
    from tests.fixtures.fake_connection import FakeConnection  # see Step 3 below
    from tests.fixtures.fake_catalog import FakeCatalog

    conn = FakeConnection("duckdb")
    cat = FakeCatalog()
    ctx = ToolContext(connection=conn, catalog=cat)
    assert ctx.connection is conn
    assert ctx.catalog is cat


def test_tool_context_multi_db_construction():
    """New construction: ctx.connections / ctx.catalogs accept a dict; primary is required."""
    from tests.fixtures.fake_connection import FakeConnection
    from tests.fixtures.fake_catalog import FakeCatalog

    conns = {"main": FakeConnection("duckdb"), "books": FakeConnection("postgres")}
    cats = {"main": FakeCatalog(), "books": FakeCatalog()}
    ctx = ToolContext(connections=conns, catalogs=cats, primary="main")
    assert ctx.connection is conns["main"]
    assert ctx.catalog is cats["main"]
    assert set(ctx.connections.keys()) == {"main", "books"}


def test_tool_context_legacy_and_multi_db_match():
    """Constructing both ways with the same primary connection should be equivalent."""
    from tests.fixtures.fake_connection import FakeConnection
    from tests.fixtures.fake_catalog import FakeCatalog

    conn = FakeConnection("duckdb")
    cat = FakeCatalog()
    legacy = ToolContext(connection=conn, catalog=cat)
    new = ToolContext(connections={"primary": conn}, catalogs={"primary": cat}, primary="primary")
    assert legacy.connection is new.connection
    assert legacy.catalog is new.catalog
```

- [ ] **Step 3: Add fixture stubs if not present**

Create `tests/fixtures/fake_connection.py` (skip if a working fake already exists in the repo — check `tests/fixtures/` first):

```python
"""Minimal Connection fake for ToolContext tests."""

from __future__ import annotations

from typing import Any


class FakeConnection:
    def __init__(self, dialect: str):
        self.dialect = dialect

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError
```

Create `tests/fixtures/fake_catalog.py`:

```python
"""Minimal Catalog fake for ToolContext tests."""


class FakeCatalog:
    schemas: list[str] = []
```

- [ ] **Step 4: Run tests to confirm failure**

```bash
uv run pytest tests/unit/test_multi_db_tool_context.py -v
```

Expected: failures (either constructor errors or wrong behavior).

- [ ] **Step 5: Refactor `ToolContext`**

Edit `ToolContext` (location confirmed in Step 1). The dataclass should accept either shape and expose both:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ... existing imports for Connection, Catalog ...


@dataclass
class ToolContext:
    """Carries the live Connection(s) and Catalog(s) for tool execution.

    Supports both single-DB (legacy) and multi-DB construction:

      # Legacy:
      ToolContext(connection=conn, catalog=cat)
      # New:
      ToolContext(connections={"main": conn}, catalogs={"main": cat}, primary="main")
    """

    # Multi-DB fields (canonical storage)
    connections: dict[str, Connection] = field(default_factory=dict)
    catalogs: dict[str, Catalog] = field(default_factory=dict)
    primary: str = "primary"

    # ... other existing fields (history, memory, etc.) ...

    def __init__(
        self,
        connection: Connection | None = None,
        catalog: Catalog | None = None,
        *,
        connections: dict[str, Connection] | None = None,
        catalogs: dict[str, Catalog] | None = None,
        primary: str = "primary",
        # ... other existing kwargs ...
    ):
        # Normalize: single-DB form populates the dicts under `primary`.
        if connection is not None:
            assert connections is None, "pass either `connection=` or `connections=`, not both"
            self.connections = {primary: connection}
        else:
            self.connections = connections or {}

        if catalog is not None:
            assert catalogs is None, "pass either `catalog=` or `catalogs=`, not both"
            self.catalogs = {primary: catalog}
        else:
            self.catalogs = catalogs or {}

        self.primary = primary
        # ... assign other existing fields ...

    @property
    def connection(self) -> Connection:
        return self.connections[self.primary]

    @property
    def catalog(self) -> Catalog:
        return self.catalogs[self.primary]
```

Adjust the field assignments for any other existing `ToolContext` fields (history, memory, etc.) — preserve their current behavior.

- [ ] **Step 6: Run tests to confirm pass**

```bash
uv run pytest tests/unit/test_multi_db_tool_context.py -v
```

Expected: 3 tests pass.

- [ ] **Step 7: Run the full test suite**

```bash
uv run pytest -q
```

Expected: every existing test still passes. The shim preserves `ctx.connection` / `ctx.catalog` for all callers.

- [ ] **Step 8: Pre-commit gate + smoke regression**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q
uv run python scripts/run_smoke_regression.py check --n-attempts 3
```

Expected: smoke verdict `pass`.

- [ ] **Step 9: Commit**

```bash
git add src/labrat/agent/tools/base.py tests/unit/test_multi_db_tool_context.py tests/fixtures/fake_connection.py tests/fixtures/fake_catalog.py
git commit -m "feat: multi-DB ToolContext with backwards-compat single-DB shims"
```

---

### Task 17: `DabSuite` task enumeration

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/suite.py` (enumeration only — `run_trial`/`aggregate`/`write_submission` are stubs that raise `NotImplementedError`)
- Create: `tests/fixtures/dab/datasets/synthetic1/query_1/db_config.yaml` and related files
- Create: `tests/unit/test_dab_suite_enumeration.py`

- [ ] **Step 1: Build a minimal DAB fixture**

`tests/fixtures/dab/datasets/synthetic1/query_1/db_config.yaml`:

```yaml
databases:
  - name: main
    type: duckdb
    path: synthetic1.duckdb
```

`tests/fixtures/dab/datasets/synthetic1/query_1/db_description.txt`:

```
Synthetic dataset with one DuckDB. Tables: t1 (id, value).
```

`tests/fixtures/dab/datasets/synthetic1/query_1/db_description_withhint.txt`:

```
Synthetic dataset with one DuckDB. Tables: t1 (id, value).
Hint: row count of t1 is 3.
```

`tests/fixtures/dab/datasets/synthetic1/query_1/question.txt`:

```
How many rows are in t1?
```

`tests/fixtures/dab/datasets/synthetic1/query_1/validate.py`:

```python
def validate(llm_output: str) -> tuple[bool, str]:
    return ("3" in llm_output, "expected '3' in output")
```

(If the real DAB repo uses a different layout — e.g. files at the dataset level instead of per-query — adjust the fixture and the suite to match. Task 3's exploration confirms the real shape.)

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_dab_suite_enumeration.py`:

```python
from pathlib import Path

from labrat.eval.benchmarks.dab.suite import DabSuite
from labrat.eval.types import BenchmarkSuite


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "dab" / "datasets"


def test_dab_suite_implements_protocol():
    suite = DabSuite(dab_dir=FIXTURE_DIR.parent)
    assert isinstance(suite, BenchmarkSuite)
    assert suite.name == "dab"


def test_dab_suite_enumerates_synthetic_dataset():
    suite = DabSuite(dab_dir=FIXTURE_DIR.parent)
    tasks = list(suite.tasks())
    ids = sorted(t.id for t in tasks)
    assert "synthetic1:1" in ids


def test_dab_suite_task_carries_dataset_in_config():
    suite = DabSuite(dab_dir=FIXTURE_DIR.parent)
    t = next(t for t in suite.tasks() if t.id == "synthetic1:1")
    assert t.benchmark == "dab"
    assert t.config["dataset"] == "synthetic1"
    assert t.config["validator_path"].endswith("validate.py")
    assert t.prompt  # not empty
```

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest tests/unit/test_dab_suite_enumeration.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement enumeration in `suite.py`**

`src/labrat/eval/benchmarks/dab/suite.py`:

```python
"""DAB BenchmarkSuite — task enumeration (Task 17) + run_trial (Task 18+).

Layout (per DAB repo at ~/repos/DataAgentBench/):
  <dab_dir>/query_<DATASET>/query_<N>/
    ├── db_config.yaml
    ├── db_description.txt
    ├── db_description_withhint.txt
    ├── question.txt   (or similar — see Task 3 exploration)
    └── validate.py

This implementation walks `query_*/query_*` directories. If Task 3 revealed
a different layout, adjust `_iter_query_dirs` to match.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

from labrat.eval.types import (
    AggregateScore,
    BenchmarkReport,
    BenchmarkTask,
    TrialResult,
)


_DAB_QUERY_DIR_RE = re.compile(r"^query_(\d+)$")
_DAB_DATASET_DIR_RE = re.compile(r"^query_(.+)$")


class DabSuite:
    """Reads DAB queries from a DataAgentBench checkout."""

    name = "dab"

    def __init__(
        self,
        dab_dir: Path | None = None,
        hints: bool = False,
    ) -> None:
        self._dir = (
            dab_dir
            or Path(os.environ.get("DAB_DIR", "~/repos/DataAgentBench")).expanduser()
        )
        self._hints = hints
        self._tasks_cache: list[BenchmarkTask] | None = None

    def tasks(self) -> Iterable[BenchmarkTask]:
        if self._tasks_cache is None:
            self._tasks_cache = self._load_tasks()
        return self._tasks_cache

    def _load_tasks(self) -> list[BenchmarkTask]:
        result: list[BenchmarkTask] = []
        if not self._dir.exists():
            return result

        for dataset_dir in sorted(self._dir.iterdir()):
            m_ds = _DAB_DATASET_DIR_RE.match(dataset_dir.name)
            if not m_ds:
                continue
            dataset_name = m_ds.group(1).lower()

            for query_dir in sorted(dataset_dir.iterdir()):
                m_q = _DAB_QUERY_DIR_RE.match(query_dir.name)
                if not m_q:
                    continue
                query_num = m_q.group(1)

                db_config = query_dir / "db_config.yaml"
                validator = query_dir / "validate.py"
                question_file = query_dir / "question.txt"
                desc_file = query_dir / (
                    "db_description_withhint.txt" if self._hints else "db_description.txt"
                )

                if not db_config.exists() or not validator.exists():
                    continue

                prompt_parts: list[str] = []
                if desc_file.exists():
                    prompt_parts.append(desc_file.read_text().strip())
                if question_file.exists():
                    prompt_parts.append(question_file.read_text().strip())
                prompt = "\n\n".join(prompt_parts)
                if not prompt:
                    continue

                result.append(
                    BenchmarkTask(
                        id=f"{dataset_name}:{query_num}",
                        benchmark="dab",
                        prompt=prompt,
                        config={
                            "dataset": dataset_name,
                            "query_num": query_num,
                            "db_config_path": str(db_config),
                            "validator_path": str(validator),
                            "hints": self._hints,
                        },
                    )
                )

        return result

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult:
        raise NotImplementedError("Implemented in Task 18")

    def aggregate(self, results: list[TrialResult]) -> AggregateScore:
        raise NotImplementedError("Implemented in Task 20")

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None:
        raise NotImplementedError("Implemented in Task 21")
```

- [ ] **Step 5: If the real DAB question lives elsewhere**

Task 3 may have revealed the question is in `db_config.yaml` (e.g. a `question:` field) rather than a separate `question.txt`. If so, adjust `_load_tasks` to read it from there. The unit test fixture in Step 1 should also be updated for consistency.

- [ ] **Step 6: Pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/unit/test_dab_suite_enumeration.py -v
```

- [ ] **Step 7: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py tests/fixtures/dab/ tests/unit/test_dab_suite_enumeration.py
git commit -m "feat: DabSuite task enumeration"
```

---

### Task 18: DAB `env.py` — multi-DB `ToolContext` factory

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/env.py`
- Create: `tests/unit/test_dab_env.py`

Build a `ToolContext` for one DAB trial: parse `db_config.yaml`, open each named connection, populate `connections` + `catalogs` + `primary`. The DuckDB connection is always primary (it's how the agent's `run_sql` defaults route).

- [ ] **Step 1: Write the failing test**

`tests/unit/test_dab_env.py`:

```python
from pathlib import Path

import yaml

from labrat.eval.benchmarks.dab.env import build_dab_tool_context


def test_build_tool_context_with_only_duckdb(tmp_path):
    duckdb_path = tmp_path / "main.duckdb"
    duckdb_path.touch()
    config_path = tmp_path / "db_config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "databases": [
                    {"name": "main", "type": "duckdb", "path": str(duckdb_path)},
                ]
            }
        )
    )
    ctx = build_dab_tool_context(config_path)
    assert ctx.primary == "main"
    assert set(ctx.connections.keys()) == {"main"}


def test_build_tool_context_picks_first_duckdb_as_primary(tmp_path):
    main_db = tmp_path / "main.duckdb"
    main_db.touch()
    aux_db = tmp_path / "aux.duckdb"
    aux_db.touch()
    config_path = tmp_path / "db_config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "databases": [
                    {"name": "aux", "type": "sqlite", "path": str(aux_db)},
                    {"name": "main", "type": "duckdb", "path": str(main_db)},
                ]
            }
        )
    )
    ctx = build_dab_tool_context(config_path)
    assert ctx.primary == "main"  # DuckDB wins as primary regardless of yaml order
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_dab_env.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `env.py`**

`src/labrat/eval/benchmarks/dab/env.py`:

```python
"""Build a multi-DB ToolContext for one DAB trial from db_config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from labrat.agent.tools.base import ToolContext
from labrat.db.catalog import Catalog
from labrat.db.connection import Connection
from labrat.db.duckdb_engine import DuckDBConnection


def _build_connection(spec: dict[str, Any]) -> Connection:
    """Build a Connection from a db_config.yaml entry."""
    db_type = spec["type"].lower()
    if db_type == "duckdb":
        return DuckDBConnection(path=spec["path"])
    if db_type == "sqlite":
        # The agent reaches SQLite via DuckDB ATTACH (federation tool, Phase 1b).
        # For Phase 1a we still need a Connection object — fall back to DuckDB ATTACH.
        return DuckDBConnection(path=":memory:", attach_sqlite={spec["name"]: spec["path"]})
    if db_type == "postgres":
        # Phase 1a doesn't ship PG support — see Phase 1b. Build a placeholder
        # that raises if anyone tries to execute against it.
        raise NotImplementedError("PostgreSQL Connection lands in Phase 1b")
    if db_type == "mongodb":
        raise NotImplementedError("MongoDB Connection lands in Phase 1b")
    raise ValueError(f"Unknown DB type: {db_type}")


def build_dab_tool_context(db_config_path: Path) -> ToolContext:
    """Parse db_config.yaml and build a multi-DB ToolContext.

    Primary = the first DuckDB connection (the agent's run_sql default routes here).
    If no DuckDB is configured, an in-memory DuckDB is added so the agent can
    use it as a federation host.
    """
    config = yaml.safe_load(db_config_path.read_text())
    db_specs = config.get("databases") or []

    connections: dict[str, Connection] = {}
    for spec in db_specs:
        name = spec["name"]
        try:
            connections[name] = _build_connection(spec)
        except NotImplementedError:
            # Phase 1a skips PG/Mongo entries — they're handled in Phase 1b.
            continue

    duckdb_primary: str | None = None
    for name, conn in connections.items():
        if isinstance(conn, DuckDBConnection):
            duckdb_primary = name
            break

    if duckdb_primary is None:
        connections["__federation"] = DuckDBConnection(path=":memory:")
        duckdb_primary = "__federation"

    catalogs: dict[str, Catalog] = {name: Catalog() for name in connections}

    return ToolContext(
        connections=connections,
        catalogs=catalogs,
        primary=duckdb_primary,
    )
```

(The `DuckDBConnection(path=":memory:", attach_sqlite=...)` parameter is a small extension to `DuckDBConnection.__init__` — check its current signature in `src/labrat/db/duckdb_engine.py`. If `attach_sqlite` doesn't already exist, either add it as part of this task or simplify by attaching via raw SQL after construction. Pick whichever is smaller.)

- [ ] **Step 4: Pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/unit/test_dab_env.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/env.py tests/unit/test_dab_env.py src/labrat/db/duckdb_engine.py
git commit -m "feat: DAB multi-DB ToolContext factory (Phase 1a — DuckDB+SQLite only)"
```

---

### Task 19: DAB scorer — wrap per-query `validate.py`

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/scorer.py`
- Create: `tests/unit/test_dab_scorer.py`

The DAB benchmark scores by calling the per-query `validate.py`'s `validate(llm_output)` function. Wrap with error handling.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_dab_scorer.py`:

```python
from pathlib import Path

from labrat.eval.benchmarks.dab.scorer import score_with_validator


def _write_validator(path: Path, body: str) -> None:
    path.write_text(body)


def test_validator_returns_pass(tmp_path):
    v = tmp_path / "validate.py"
    _write_validator(
        v,
        "def validate(out: str):\n    return ('foo' in out, 'expected foo')\n",
    )
    passed, reason = score_with_validator(v, "the foo bar")
    assert passed is True


def test_validator_returns_fail(tmp_path):
    v = tmp_path / "validate.py"
    _write_validator(
        v,
        "def validate(out: str):\n    return ('foo' in out, 'expected foo')\n",
    )
    passed, reason = score_with_validator(v, "no f-word here")
    assert passed is False
    assert reason  # non-empty


def test_validator_with_runtime_error_returns_validator_error(tmp_path):
    v = tmp_path / "validate.py"
    _write_validator(
        v,
        "def validate(out: str):\n    raise RuntimeError('boom')\n",
    )
    passed, reason = score_with_validator(v, "anything")
    assert passed is False
    assert reason is not None
    assert reason.startswith("validator_error")


def test_validator_with_import_error_returns_validator_error(tmp_path):
    v = tmp_path / "validate.py"
    _write_validator(
        v,
        "import this_module_does_not_exist\n"
        "def validate(out: str): return (True, '')\n",
    )
    passed, reason = score_with_validator(v, "anything")
    assert passed is False
    assert reason is not None
    assert reason.startswith("validator_error")
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_dab_scorer.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `scorer.py`**

`src/labrat/eval/benchmarks/dab/scorer.py`:

```python
"""Wraps each DAB query's validate.py for safe invocation."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def score_with_validator(validator_path: Path, llm_output: str) -> tuple[bool, str | None]:
    """Import the validator module and call validate(llm_output).

    Returns (passed, reason). On import or runtime error, returns
    (False, "validator_error: <message>").
    """
    try:
        spec = importlib.util.spec_from_file_location(
            f"dab_validator_{hash(str(validator_path))}", validator_path
        )
        if spec is None or spec.loader is None:
            return (False, f"validator_error: could not load spec from {validator_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:
        return (False, f"validator_error: import: {type(e).__name__}: {e}")

    validate = getattr(module, "validate", None)
    if validate is None:
        return (False, "validator_error: no `validate` function in module")

    try:
        result = validate(llm_output)
    except Exception as e:
        return (False, f"validator_error: runtime: {type(e).__name__}: {e}")

    # DAB validators return (bool, str). Be defensive about unexpected shapes.
    if isinstance(result, tuple) and len(result) == 2:
        passed, reason = bool(result[0]), str(result[1]) if result[1] else None
        return (passed, reason)
    if isinstance(result, bool):
        return (result, None)
    return (False, f"validator_error: unexpected return shape: {type(result).__name__}")
```

- [ ] **Step 4: Pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/unit/test_dab_scorer.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/scorer.py tests/unit/test_dab_scorer.py
git commit -m "feat: DAB scorer wraps per-query validate.py with error guarding"
```

---

### Task 20: `DabSuite.run_trial` v0 — single-trial agent invocation

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` (replace the `NotImplementedError` in `run_trial`)
- Create: `tests/unit/test_dab_suite_run_trial.py`

Single-trial agent invocation: build `ToolContext` from `env.py`, run the agent loop inline (no driver yet — driver extracts in Phase 4), capture final text, score with `scorer.py`, return `TrialResult`.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_dab_suite_run_trial.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

from labrat.eval.benchmarks.dab.suite import DabSuite
from labrat.eval.types import BenchmarkTask


def _make_synthetic_fixture(tmp_path):
    dataset_dir = tmp_path / "query_synthetic1" / "query_1"
    dataset_dir.mkdir(parents=True)

    duckdb_path = tmp_path / "main.duckdb"
    duckdb_path.touch()

    (dataset_dir / "db_config.yaml").write_text(
        yaml.safe_dump(
            {"databases": [{"name": "main", "type": "duckdb", "path": str(duckdb_path)}]}
        )
    )
    (dataset_dir / "db_description.txt").write_text("Synthetic")
    (dataset_dir / "question.txt").write_text("How many?")
    (dataset_dir / "validate.py").write_text(
        "def validate(out): return ('3' in out, 'expected 3')\n"
    )


async def test_run_trial_records_passing_answer(tmp_path):
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path)
    task = next(iter(suite.tasks()))

    with patch(
        "labrat.eval.benchmarks.dab.suite._invoke_agent",
        new=AsyncMock(return_value={"final_text": "The answer is 3", "tool_calls": 4}),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")

    assert result.passed is True
    assert result.task_id == task.id
    assert result.trial_num == 0
    assert result.artifact["type"] == "text"
    assert result.artifact["payload"] == "The answer is 3"
    assert result.tool_calls == 4


async def test_run_trial_records_failing_answer(tmp_path):
    _make_synthetic_fixture(tmp_path)
    suite = DabSuite(dab_dir=tmp_path)
    task = next(iter(suite.tasks()))

    with patch(
        "labrat.eval.benchmarks.dab.suite._invoke_agent",
        new=AsyncMock(return_value={"final_text": "no number", "tool_calls": 1}),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")

    assert result.passed is False
    assert result.reason  # non-empty


async def test_run_trial_records_validator_error(tmp_path):
    _make_synthetic_fixture(tmp_path)
    # Overwrite the validator with a broken one.
    (tmp_path / "query_synthetic1" / "query_1" / "validate.py").write_text(
        "def validate(out): raise RuntimeError('boom')\n"
    )
    suite = DabSuite(dab_dir=tmp_path)
    task = next(iter(suite.tasks()))

    with patch(
        "labrat.eval.benchmarks.dab.suite._invoke_agent",
        new=AsyncMock(return_value={"final_text": "whatever", "tool_calls": 0}),
    ):
        result = await suite.run_trial(task, trial_num=0, scratch_dir=tmp_path / "scratch")

    assert result.passed is False
    assert result.reason is not None
    assert result.reason.startswith("validator_error")
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_dab_suite_run_trial.py -v
```

Expected: `NotImplementedError` from the existing stub.

- [ ] **Step 3: Implement `run_trial`**

Edit `src/labrat/eval/benchmarks/dab/suite.py`. Add at the top:

```python
import time
from pathlib import Path

from labrat.agent.loop import AgentLoop
from labrat.agent.providers.claude_code import ClaudeCodeProvider
from labrat.agent.tools.registry import get_default_registry
from labrat.eval.benchmarks.dab.env import build_dab_tool_context
from labrat.eval.benchmarks.dab.scorer import score_with_validator
```

(If `get_default_registry()` doesn't exist, replace with the explicit construction used elsewhere in the codebase — read `src/labrat/agent/tools/registry.py` and adapt.)

Replace the stub `run_trial`:

```python
_DAB_SYSTEM_PROMPT = """You are LabRat, a data agent answering a question about a database.
Use the available tools to explore tables and run SQL. Return your final answer as plain text
once you are confident — do not add explanation or formatting around it."""


async def _invoke_agent(
    prompt: str,
    system_prompt: str,
    ctx,
    max_turns: int,
) -> dict:
    """Run AgentLoop and return a dict with final_text + tool_calls.

    Extracted as a top-level helper so unit tests can patch it without spinning
    up real LLM calls. Will be replaced by LabRatAgentDriver in Phase 4.
    """
    provider = ClaudeCodeProvider()
    registry = get_default_registry()
    loop = AgentLoop(
        provider=provider,
        tool_registry=registry,
        max_turns=max_turns,
        system_prompt=system_prompt,
    )
    run_result = await loop.run(ctx=ctx, prompt=prompt)
    return {
        "final_text": run_result.final_text,
        "tool_calls": run_result.tool_calls,
    }


async def run_trial(self, task: BenchmarkTask, trial_num: int, scratch_dir: Path) -> TrialResult:
    scratch_dir.mkdir(parents=True, exist_ok=True)
    db_config_path = Path(task.config["db_config_path"])
    validator_path = Path(task.config["validator_path"])

    ctx = build_dab_tool_context(db_config_path)

    start = time.monotonic()
    agent_out = await _invoke_agent(
        prompt=task.prompt,
        system_prompt=_DAB_SYSTEM_PROMPT,
        ctx=ctx,
        max_turns=100,
    )
    latency = time.monotonic() - start

    passed, reason = score_with_validator(validator_path, agent_out["final_text"])

    return TrialResult(
        task_id=task.id,
        trial_num=trial_num,
        passed=passed,
        reason=reason,
        latency_seconds=latency,
        tool_calls=agent_out["tool_calls"],
        artifact={"type": "text", "payload": agent_out["final_text"]},
    )
```

Then bind `run_trial` to the class — either by defining it as a method directly inside `class DabSuite:` (replacing the `NotImplementedError` stub), or by patching the class attribute. Prefer the direct method form.

Adjust the `_invoke_agent` signature/return shape if `AgentLoop.run` returns something different than assumed. The Task 1 spike already confirmed the actual shape.

- [ ] **Step 4: Pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/unit/test_dab_suite_run_trial.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py tests/unit/test_dab_suite_run_trial.py
git commit -m "feat: DabSuite.run_trial v0 (single-trial inline agent invocation)"
```

---

### Task 21: `DabSuite.aggregate` — stratified per-dataset score

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` (replace `aggregate` stub)
- Create: `tests/unit/test_dab_suite_aggregate.py`

DAB scoring is stratified: each of the 12 datasets contributes equally regardless of query count.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_dab_suite_aggregate.py`:

```python
from labrat.eval.benchmarks.dab.suite import DabSuite
from labrat.eval.types import TrialResult


def _r(task_id: str, passed: bool) -> TrialResult:
    return TrialResult(task_id=task_id, trial_num=0, passed=passed, latency_seconds=0.0)


def test_aggregate_uses_stratified_dataset_weighting():
    suite = DabSuite(dab_dir=__import__("pathlib").Path("/nonexistent"))
    # Dataset A: 4 queries, 4 pass; dataset B: 1 query, 0 pass.
    # Per-query mean would be 4/5 = 0.8.
    # Stratified mean is (1.0 + 0.0) / 2 = 0.5.
    results = [
        _r("a:1", True), _r("a:2", True), _r("a:3", True), _r("a:4", True),
        _r("b:1", False),
    ]
    score = suite.aggregate(results)
    assert score.overall == 0.5
    assert score.by_dimension["dataset"]["a"] == 1.0
    assert score.by_dimension["dataset"]["b"] == 0.0


def test_aggregate_accumulates_trials_per_query():
    suite = DabSuite(dab_dir=__import__("pathlib").Path("/nonexistent"))
    # Same query, 3 trials: 2 pass, 1 fail → pass-rate 2/3.
    results = [
        _r("a:1", True),
        _r("a:1", False),
        _r("a:1", True),
    ]
    score = suite.aggregate(results)
    assert score.per_task["a:1"] == 2 / 3
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_dab_suite_aggregate.py -v
```

Expected: `NotImplementedError` from the stub.

- [ ] **Step 3: Implement `aggregate`**

Replace the `aggregate` stub in `suite.py`:

```python
def aggregate(self, results: list[TrialResult]) -> AggregateScore:
    if not results:
        return AggregateScore(
            overall=0.0, per_task={}, n_tasks=0, n_trials=0, n_passes=0
        )

    per_task: dict[str, list[bool]] = {}
    for r in results:
        per_task.setdefault(r.task_id, []).append(r.passed)
    per_task_pass_rate = {
        tid: sum(passes) / len(passes) for tid, passes in per_task.items()
    }

    # Stratified by dataset: each dataset's mean pass-rate contributes equally.
    by_dataset: dict[str, list[float]] = {}
    for tid, pr in per_task_pass_rate.items():
        dataset = tid.split(":", 1)[0]
        by_dataset.setdefault(dataset, []).append(pr)
    dataset_means = {ds: sum(prs) / len(prs) for ds, prs in by_dataset.items()}

    return AggregateScore(
        overall=sum(dataset_means.values()) / len(dataset_means),
        per_task=per_task_pass_rate,
        by_dimension={"dataset": dataset_means},
        n_tasks=len(per_task),
        n_trials=len(results),
        n_passes=sum(1 for r in results if r.passed),
    )
```

- [ ] **Step 4: Pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/unit/test_dab_suite_aggregate.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py tests/unit/test_dab_suite_aggregate.py
git commit -m "feat: DabSuite stratified per-dataset aggregate score"
```

---

### Task 22: `DabSuite.write_submission` — DAB submission JSON

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/reporter.py`
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` (replace `write_submission` stub to call reporter)
- Create: `tests/unit/test_dab_reporter.py`

DAB submission format (from the spec): `[{dataset, query, run, answer}, ...]`. Written to `submission.json` in the output dir.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_dab_reporter.py`:

```python
import json
from pathlib import Path

from labrat.eval.benchmarks.dab.reporter import build_submission, write_submission_json
from labrat.eval.types import AggregateScore, BenchmarkReport, TrialResult


def _report_with_trials(trials: list[TrialResult]) -> BenchmarkReport:
    return BenchmarkReport(
        benchmark="dab",
        run_id="r1",
        score=AggregateScore(
            overall=0.5,
            per_task={t.task_id: 1.0 if t.passed else 0.0 for t in trials},
            n_tasks=len({t.task_id for t in trials}),
            n_trials=len(trials),
            n_passes=sum(1 for t in trials if t.passed),
        ),
        trials=trials,
        config={},
    )


def test_build_submission_shape():
    trials = [
        TrialResult(
            task_id="bookreview:1",
            trial_num=0,
            passed=True,
            latency_seconds=0.0,
            artifact={"type": "text", "payload": "answer A"},
        ),
        TrialResult(
            task_id="bookreview:1",
            trial_num=1,
            passed=False,
            latency_seconds=0.0,
            artifact={"type": "text", "payload": "answer B"},
        ),
    ]
    entries = build_submission(trials)
    assert len(entries) == 2
    assert entries[0] == {"dataset": "bookreview", "query": "1", "run": 0, "answer": "answer A"}
    assert entries[1] == {"dataset": "bookreview", "query": "1", "run": 1, "answer": "answer B"}


def test_write_submission_json_creates_file(tmp_path):
    trials = [
        TrialResult(
            task_id="yelp:3",
            trial_num=0,
            passed=True,
            latency_seconds=0.0,
            artifact={"type": "text", "payload": "x"},
        ),
    ]
    report = _report_with_trials(trials)
    write_submission_json(report, tmp_path)
    output = tmp_path / "submission.json"
    assert output.exists()
    data = json.loads(output.read_text())
    assert data == [{"dataset": "yelp", "query": "3", "run": 0, "answer": "x"}]
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_dab_reporter.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `reporter.py`**

```python
"""DAB submission.json writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from labrat.eval.types import BenchmarkReport, TrialResult


def build_submission(trials: list[TrialResult]) -> list[dict[str, Any]]:
    """Materialize DAB's required submission format from a list of trials."""
    entries: list[dict[str, Any]] = []
    for t in trials:
        dataset, _, query = t.task_id.partition(":")
        answer = t.artifact.get("payload") if t.artifact.get("type") == "text" else ""
        entries.append(
            {
                "dataset": dataset,
                "query": query,
                "run": t.trial_num,
                "answer": answer or "",
            }
        )
    return entries


def write_submission_json(report: BenchmarkReport, output_dir: Path) -> None:
    """Write submission.json into `output_dir`."""
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = build_submission(report.trials)
    (output_dir / "submission.json").write_text(
        json.dumps(entries, indent=2, sort_keys=False)
    )
```

- [ ] **Step 4: Wire `write_submission` in `suite.py`**

Replace the `NotImplementedError` stub:

```python
def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None:
    from labrat.eval.benchmarks.dab.reporter import write_submission_json

    write_submission_json(report, output_dir)
```

- [ ] **Step 5: Pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest tests/unit/test_dab_reporter.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/reporter.py src/labrat/eval/benchmarks/dab/suite.py tests/unit/test_dab_reporter.py
git commit -m "feat: DAB submission.json writer"
```

---

### Task 23: `scripts/eval_dab.py` with inline interim runner

**Files:**
- Create: `scripts/eval_dab.py`

Entrypoint for DAB runs. Phase 1a is single-trial-per-query, no pass@5, no resumability. The interim runner code lives inline here and must contain **no DAB-specific logic** — it's the orchestrator-in-waiting that Phase 4 extracts.

- [ ] **Step 1: Implement the script**

`scripts/eval_dab.py`:

```python
"""DAB benchmark entrypoint.

Phase 1a: single-trial-per-query, no resumability, no pass@5.
Phase 1b adds concurrency / jsonl / resumability inline here.
Phase 4 extracts the inline runner into BenchmarkOrchestrator.

Usage:
  uv run python scripts/eval_dab.py --datasets bookreview,yelp
  uv run python scripts/eval_dab.py --hints
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from labrat.eval.benchmarks.dab.suite import DabSuite
from labrat.eval.reporting import report_to_markdown
from labrat.eval.types import BenchmarkReport, BenchmarkSuite, TrialResult


async def _run_interim(
    suite: BenchmarkSuite,
    n_trials: int,
    output_dir: Path,
    task_filter: list[str] | None,
) -> BenchmarkReport:
    """Inline interim runner. No DAB-specific logic. Replaced by BenchmarkOrchestrator in Phase 4."""
    output_dir.mkdir(parents=True, exist_ok=True)
    trials_jsonl = output_dir / "trials.jsonl"
    all_trials: list[TrialResult] = []

    tasks = list(suite.tasks())
    if task_filter:
        wanted = set(task_filter)
        tasks = [t for t in tasks if t.id in wanted]

    with trials_jsonl.open("w") as f:
        for task in tasks:
            for trial_num in range(n_trials):
                scratch = output_dir / "scratch" / f"{task.id.replace(':', '_')}__trial{trial_num}"
                scratch.mkdir(parents=True, exist_ok=True)
                result = await suite.run_trial(task, trial_num, scratch)
                f.write(result.model_dump_json() + "\n")
                f.flush()
                all_trials.append(result)
                print(
                    f"[{task.id} trial {trial_num}] {'PASS' if result.passed else 'FAIL'} "
                    f"({result.latency_seconds:.1f}s)",
                    flush=True,
                )

    score = suite.aggregate(all_trials)
    return BenchmarkReport(
        benchmark=suite.name,
        run_id=output_dir.name,
        score=score,
        trials=all_trials,
        config={"n_trials": n_trials, "task_filter": task_filter},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run DAB benchmark")
    parser.add_argument("--dab-dir", type=Path, default=None)
    parser.add_argument("--hints", action="store_true")
    parser.add_argument("--n-trials", type=int, default=1, help="Phase 1a default is 1; bumps to 5 in Phase 1b")
    parser.add_argument("--datasets", type=str, default=None, help="Comma-separated dataset filter (e.g. 'bookreview,yelp')")
    parser.add_argument("--tasks", type=str, default=None, help="Comma-separated task ID filter (e.g. 'bookreview:1,yelp:3')")
    args = parser.parse_args(argv)

    suite = DabSuite(dab_dir=args.dab_dir, hints=args.hints)

    task_filter: list[str] | None = None
    if args.tasks:
        task_filter = [t.strip() for t in args.tasks.split(",") if t.strip()]
    elif args.datasets:
        wanted = {ds.strip() for ds in args.datasets.split(",") if ds.strip()}
        task_filter = [t.id for t in suite.tasks() if t.config["dataset"] in wanted]

    run_id = f"dab-{int(time.time())}"
    output_dir = Path("runs") / "dab" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "config.json").write_text(
        json.dumps(
            {"hints": args.hints, "n_trials": args.n_trials, "task_filter": task_filter},
            indent=2,
        )
    )

    report = asyncio.run(
        _run_interim(suite, args.n_trials, output_dir, task_filter)
    )

    suite.write_submission(report, output_dir)
    (output_dir / "report.md").write_text(report_to_markdown(report))
    print(f"\nRun complete: {output_dir}")
    print(f"Overall score: {report.score.overall:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Pre-commit gate**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q
```

- [ ] **Step 3: Commit**

```bash
git add scripts/eval_dab.py
git commit -m "feat: scripts/eval_dab.py with inline interim runner (Phase 1a)"
```

---

### Task 24: End-to-end DAB run on 5 DuckDB+SQLite-only datasets

**Files:** none modified.

Real LLM run. Exit gate for Phase 1a.

- [ ] **Step 1: Run DAB on the 5 DuckDB+SQLite datasets**

```bash
uv run python scripts/eval_dab.py --datasets deps_dev_v1,github_repos,music_brainz_20k,stockindex,stockmarket
```

Expected: each query runs single-trial, prints PASS/FAIL per trial, ends with a `report.md` + `submission.json` in `runs/dab/dab-<timestamp>/`.

Roughly 17 queries × 1 trial × ~30s each = ~10 min wall time. If it runs much longer or hangs, abort and diagnose.

- [ ] **Step 2: Eyeball the submission JSON shape**

```bash
cat runs/dab/dab-*/submission.json | python -m json.tool | head -20
```

Expected: list of `{dataset, query, run, answer}` dicts. `answer` field is non-empty for at least some trials.

- [ ] **Step 3: Eyeball the report markdown**

```bash
cat runs/dab/dab-*/report.md
```

Expected: overall score, `by_dimension.dataset` table, failure list.

- [ ] **Step 4: Record results in a Phase 1a notes file**

`docs/superpowers/notes/2026-05-28-dab-phase-1a-results.md`:

```markdown
# DAB Phase 1a Results (2026-05-28)

Run ID: `<run-id>`

## Overall

- Per-query trials: 17 × 1 = 17 trials
- Pass count: N
- Stratified overall: X.XX

## Per-dataset

| Dataset | Queries | Pass-rate |
|---|---|---|
| deps_dev_v1 | n | 0.xx |
| github_repos | n | 0.xx |
| music_brainz_20k | n | 0.xx |
| stockindex | n | 0.xx |
| stockmarket | n | 0.xx |

## Observations

(short notes on common failure modes — informs Phase 1b prompt iteration)
```

Replace `<run-id>` and the numbers with the actual run output.

- [ ] **Step 5: Commit the notes**

```bash
git add docs/superpowers/notes/2026-05-28-dab-phase-1a-results.md
git commit -m "docs: record DAB Phase 1a run results"
```

---

### Task 25: Phase 1a exit gate — smoke regression check

**Files:** none modified.

The final exit gate.

- [ ] **Step 1: Run the smoke regression**

```bash
uv run python scripts/run_smoke_regression.py check --n-attempts 3
```

Expected: verdict `pass`.

- [ ] **Step 2: If smoke fails**

- Hard fail (verdict `hard_fail`): investigate. Likely culprits: the `ToolContext` multi-DB change (Task 16) introduced a latent bug; the `AdeBenchSuite` port (Task 11) drifted from legacy behavior. Roll forward — don't merge until smoke passes.
- Soft signal: investigate but proceed. If the next attempt also produces a soft signal, escalate to full ADE-bench (`uv run scripts/eval_ade_bench.py`) to localize.

- [ ] **Step 3: Final commit — record Phase 1a complete**

If smoke passes:

```bash
git commit --allow-empty -m "milestone: DAB Phase 1a exit gate met — smoke clean, 17 single-trial queries complete"
git log --oneline -10
```

**Phase 1a is complete.** Next phases (1b: pass@5 + Mongo + new tools; 1c: hints + competitive score) are out of scope for this plan.

---

## Self-Review Checklist

After all tasks complete:

- [ ] All 17 DuckDB+SQLite-only DAB queries ran end-to-end through the new harness (Task 24).
- [ ] ADE smoke regression baseline captured on new shape, regression check verdict `pass` (Tasks 15 + 25).
- [ ] Legacy `eval/suites/ade_bench.py` and `eval/runners/ade_bench_runner.py` deleted (Task 13).
- [ ] All new code passes ruff + pyright + pytest.
- [ ] No `BenchmarkOrchestrator` or `LabRatAgentDriver` exists yet — those are Phase 4 (Spider2 onboarding).
- [ ] `scripts/eval_dab.py` inline runner contains no DAB-specific logic; ready to extract verbatim in Phase 4.
- [ ] `bird.py`, `latency.py`, `custom_scenarios.py` unchanged — internal evals stay on legacy `EvalCase`/`EvalRunner`.
- [ ] `ADE_SMOKE_TASK_IDS` populated and matches `docs/superpowers/notes/2026-05-28-ade-smoke-selection.md`.
- [ ] Submission JSON shape validates against the example in DAB's README.
- [ ] `feat/dab-integration` branch never merged to master — Phase 1c is the merge trigger.
