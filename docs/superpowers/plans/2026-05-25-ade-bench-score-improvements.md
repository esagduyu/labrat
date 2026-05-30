# ADE-bench Score Improvements (67% → 75%+) Implementation Plan

> **STALE PATH WARNING (2026-05-29):** Tasks 1/2 reference `src/labrat/eval/runners/ade_bench_runner.py`
> and `labrat.eval.runners.ade_bench_runner`. That module was deleted during the ADE-bench refactor;
> the active integration now lives at `src/labrat/eval/benchmarks/ade_bench/` (suite.py, external_runner.py,
> reporter.py). Update any task that touches `runners/ade_bench_runner.py` to target the new paths.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push LabRat's ADE-bench score from 67% (40/60) toward 75%+ by applying Tier 1 prompt improvements (pre-submit verification, anti-patterns, N=3 retries) and Tier 2 per-family hints, then re-running the full suite to measure lift.

**Architecture:** All prompt improvements live in `LabratLocalAgent._DOCKER_PREAMBLE` and a new `_FAMILY_HINTS` dict in the ade-bench repo at `~/repos/ade-bench/ade_bench/agents/installed_agents/labrat_local/labrat_local_agent.py`. The `--n-attempts` flag is wired through `AdeBenchRunner` (labrat repo) and the `eval_ade_bench.py` wrapper. Nearly all 20 failures are **equality failures** (model compiles, table exists, but data is wrong) — the goal is to make the agent verify data content before finishing.

**Tech Stack:** Python 3.12, pytest, ADE-bench harness (separate repo at `~/repos/ade-bench`), `ade` CLI, DuckDB, dbt 1.10.

---

## Failure analysis summary (baseline 2026-05-24__23-15-04__none)

- 20/60 tasks failed; **17/20 failures are data-equality errors** (model exists, but row content doesn't match gold)
- `helixops_saas009`: outlier — only 5 turns, quit without verifying model was named correctly
- `analytics_engineering` family: 3/4 fail (25% pass rate) — worst family by ratio
- `asana`: 3/6 fail (50% pass rate)
- `airbnb012`: unique — fails `unit_tests_exist` and `broken_models_caught`, a test-writing task
- Cost ceiling is not the issue — average failure uses 10–26 turns and <$0.55

---

## File structure

| Repo | File | Changes |
|------|------|---------|
| `~/repos/ade-bench` | `ade_bench/agents/installed_agents/labrat_local/labrat_local_agent.py` | Expand `_DOCKER_PREAMBLE` with verification section + anti-patterns; add `_FAMILY_HINTS` dict |
| `~/repos/labrat` | `src/labrat/eval/runners/ade_bench_runner.py` | Add `n_attempts` param; fix multi-trial result grouping |
| `~/repos/labrat` | `scripts/eval_ade_bench.py` | Add `--n-attempts` CLI flag |
| `~/repos/labrat` | `tests/unit/test_eval/test_ade_bench_runner.py` | New tests: n_attempts cmd inclusion, multi-trial best-of grouping |
| `~/repos/labrat` | `scripts/analyze_ade_failures.py` | New: reads experiment cast files, prints turn-by-turn failure patterns |

---

## Task 1: Test `AdeBenchRunner` n_attempts and multi-trial grouping

**Files:**
- Create: `tests/unit/test_eval/test_ade_bench_runner.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_eval/test_ade_bench_runner.py`:

```python
"""Tests for AdeBenchRunner n_attempts and multi-trial result grouping."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from labrat.eval.models import EvalStatus
from labrat.eval.runners.ade_bench_runner import AdeBenchRunner


def _make_case(task_id: str):
    from labrat.eval.models import EvalCase
    return EvalCase(id=task_id, question=f"Question for {task_id}")


def _make_results_json(trials: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, dir="/tmp"
    )
    json.dump({"results": trials}, tmp)
    tmp.flush()
    return Path(tmp.name)


def _trial(task_id: str, is_resolved: bool, attempt: int = 1, n_attempts: int = 1) -> dict:
    return {
        "trial_name": f"{task_id}.base.{attempt}-of-{n_attempts}",
        "task_id": task_id,
        "task_prompt": "...",
        "is_resolved": is_resolved,
        "failure_mode": "none" if is_resolved else "unset",
        "runtime_ms": 1000,
    }


# ── n_attempts CLI flag ────────────────────────────────────────────────────────


def test_n_attempts_included_in_ade_command():
    """--n-attempts N is passed to the ade run command."""
    ade_bench_dir = Path("/tmp/fake-ade-bench")
    runner = AdeBenchRunner(
        cases=[_make_case("airbnb001")],
        ade_bench_dir=ade_bench_dir,
        n_attempts=3,
    )

    with patch("subprocess.run") as mock_run, \
         patch.object(runner, "_find_results_json", return_value=None):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner.run()

    cmd = mock_run.call_args[0][0]
    assert "--n-attempts" in cmd
    assert "3" in cmd


def test_n_attempts_default_is_one():
    """Without specifying n_attempts, --n-attempts 1 is sent."""
    ade_bench_dir = Path("/tmp/fake-ade-bench")
    runner = AdeBenchRunner(
        cases=[_make_case("airbnb001")],
        ade_bench_dir=ade_bench_dir,
    )

    with patch("subprocess.run") as mock_run, \
         patch.object(runner, "_find_results_json", return_value=None):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner.run()

    cmd = mock_run.call_args[0][0]
    assert "--n-attempts" in cmd
    idx = cmd.index("--n-attempts")
    assert cmd[idx + 1] == "1"


# ── multi-trial best-of grouping ──────────────────────────────────────────────


def test_pass_if_any_trial_passes():
    """With 3 attempts, task is marked correct if any attempt passes."""
    results_path = _make_results_json([
        _trial("airbnb001", is_resolved=False, attempt=1, n_attempts=3),
        _trial("airbnb001", is_resolved=False, attempt=2, n_attempts=3),
        _trial("airbnb001", is_resolved=True, attempt=3, n_attempts=3),
    ])
    ade_bench_dir = results_path.parent
    runner = AdeBenchRunner(
        cases=[_make_case("airbnb001")],
        ade_bench_dir=ade_bench_dir,
        n_attempts=3,
    )

    with patch("subprocess.run") as mock_run, \
         patch.object(runner, "_find_results_json", return_value=results_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        report = runner.run()

    assert report.results[0].status == EvalStatus.correct


def test_fail_if_all_trials_fail():
    """Task is wrong if all 3 attempts fail."""
    results_path = _make_results_json([
        _trial("airbnb001", is_resolved=False, attempt=1, n_attempts=3),
        _trial("airbnb001", is_resolved=False, attempt=2, n_attempts=3),
        _trial("airbnb001", is_resolved=False, attempt=3, n_attempts=3),
    ])
    ade_bench_dir = results_path.parent
    runner = AdeBenchRunner(
        cases=[_make_case("airbnb001")],
        ade_bench_dir=ade_bench_dir,
        n_attempts=3,
    )

    with patch("subprocess.run") as mock_run, \
         patch.object(runner, "_find_results_json", return_value=results_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        report = runner.run()

    assert report.results[0].status == EvalStatus.wrong
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ~/repos/labrat && uv run pytest tests/unit/test_eval/test_ade_bench_runner.py -v
```

Expected: `FAILED` — `AdeBenchRunner` has no `n_attempts` param, and result grouping doesn't exist yet.

- [ ] **Step 3: Commit the test file**

```bash
cd ~/repos/labrat
git add tests/unit/test_eval/test_ade_bench_runner.py
git commit -m "$(cat <<'EOF'
test: add AdeBenchRunner n_attempts and multi-trial grouping tests

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Implement n_attempts and multi-trial grouping in AdeBenchRunner

**Files:**
- Modify: `src/labrat/eval/runners/ade_bench_runner.py`

- [ ] **Step 1: Read the current file**

```bash
cat ~/repos/labrat/src/labrat/eval/runners/ade_bench_runner.py
```

- [ ] **Step 2: Replace the file with the updated implementation**

Replace `src/labrat/eval/runners/ade_bench_runner.py` with:

```python
"""AdeBenchRunner: shells out to the ade CLI and maps results to EvalReport."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from labrat.eval.models import EvalCase, EvalResult, EvalStatus
from labrat.eval.report import EvalReport

# Failure modes that indicate infrastructure problems, not wrong answers
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
}


def _map_trial(trial: dict[str, Any]) -> EvalResult:
    failure_mode: str = trial.get("failure_mode", "none")
    is_resolved: bool | None = trial.get("is_resolved")
    runtime_ms: int = trial.get("runtime_ms") or 0

    if is_resolved is True:
        status = EvalStatus.correct
    elif failure_mode in _INFRA_FAILURE_MODES:
        status = EvalStatus.error
    else:
        status = EvalStatus.wrong

    return EvalResult(
        case_id=trial["task_id"],
        status=status,
        error_message=failure_mode if status == EvalStatus.error else None,
        latency_seconds=runtime_ms / 1000.0,
    )


def _best_trial(trials: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the first passing trial; fall back to the last trial."""
    for t in trials:
        if t.get("is_resolved"):
            return t
    return trials[-1]


class AdeBenchRunner:
    """Runs ADE-bench tasks via the ade CLI and returns an EvalReport.

    Uses `ade run` with the sage agent by default (no LLM cost).
    Requires Docker to be running and ADE_BENCH_DIR to be set up.
    With n_attempts > 1, a task passes if any attempt passes (pass@k semantics).
    """

    def __init__(
        self,
        cases: list[EvalCase],
        ade_bench_dir: Path,
        agent: str = "sage",
        output_path: Path | None = None,
        n_concurrent_trials: int = 1,
        n_attempts: int = 1,
        no_diffs: bool = True,
    ) -> None:
        self._cases = cases
        self._ade_bench_dir = ade_bench_dir
        self._agent = agent
        self._output_path = output_path or (ade_bench_dir / "experiments")
        self._n_concurrent_trials = n_concurrent_trials
        self._n_attempts = n_attempts
        self._no_diffs = no_diffs

    def run(self) -> EvalReport:
        if not self._cases:
            return EvalReport(suite_name="ade-bench", results=[])

        task_ids = [c.id for c in self._cases]
        cmd = [
            "ade",
            "run",
            *task_ids,
            "--db",
            "duckdb",
            "--project-type",
            "dbt",
            "--agent",
            self._agent,
            "--n-concurrent-trials",
            str(self._n_concurrent_trials),
            "--n-attempts",
            str(self._n_attempts),
            "--output-path",
            str(self._output_path),
        ]
        if self._no_diffs:
            cmd.append("--no-diffs")

        proc = subprocess.run(
            cmd,
            cwd=str(self._ade_bench_dir),
            capture_output=True,
            text=True,
        )

        results_file = self._find_results_json()
        if results_file is None:
            results = [
                EvalResult(
                    case_id=c.id,
                    status=EvalStatus.error,
                    error_message=(f"ade run failed (rc={proc.returncode}): {proc.stderr[:300]}"),
                )
                for c in self._cases
            ]
            return EvalReport(suite_name="ade-bench", results=results)

        data: dict[str, Any] = json.loads(results_file.read_text())

        # Group all trials by task_id; with n_attempts > 1, pick best (pass if any passes)
        trial_groups: dict[str, list[dict[str, Any]]] = {}
        for trial in data.get("results", []):
            trial_groups.setdefault(trial["task_id"], []).append(trial)

        results: list[EvalResult] = []
        for case in self._cases:
            trials = trial_groups.get(case.id)
            if not trials:
                results.append(
                    EvalResult(
                        case_id=case.id,
                        status=EvalStatus.error,
                        error_message="task not found in results.json",
                    )
                )
            else:
                results.append(_map_trial(_best_trial(trials)))
        return EvalReport(suite_name="ade-bench", results=results)

    def _find_results_json(self) -> Path | None:
        """Find the run-level results.json at output_path/{run_id}/results.json."""
        if not self._output_path.exists():
            return None
        # Run-level file is exactly two levels deep: output_path/{run_id}/results.json
        candidates = [
            p
            for p in self._output_path.rglob("results.json")
            if p.parent.parent == self._output_path
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)
```

- [ ] **Step 3: Run the tests to verify they pass**

```bash
cd ~/repos/labrat && uv run pytest tests/unit/test_eval/test_ade_bench_runner.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 4: Run full test suite to check for regressions**

```bash
cd ~/repos/labrat && uv run pytest tests/unit/test_eval/ -v
```

Expected: all existing eval tests still pass.

- [ ] **Step 5: Commit**

```bash
cd ~/repos/labrat
git add src/labrat/eval/runners/ade_bench_runner.py
git commit -m "$(cat <<'EOF'
feat: add n_attempts support to AdeBenchRunner with pass@k grouping

With n_attempts > 1, a task passes if any trial passes (best-of-N).
Fixes silent overwrite bug where later trials clobbered earlier ones.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add --n-attempts flag to eval_ade_bench.py

**Files:**
- Modify: `scripts/eval_ade_bench.py`

- [ ] **Step 1: Read the current file**

```bash
cat ~/repos/labrat/scripts/eval_ade_bench.py
```

- [ ] **Step 2: Add --n-attempts argument**

Add `--n-attempts` argument and wire it through to `AdeBenchRunner`. The full file after changes:

```python
#!/usr/bin/env python3
"""Run ADE-bench evaluation against the ade CLI.

Usage:
    uv run scripts/eval_ade_bench.py [--tasks TASK_ID ...] [--agent sage|claude]

Requires:
    - ADE_BENCH_DIR env var pointing to the ade-bench repo checkout
    - Docker running
    - ade CLI installed in the ade-bench venv
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ADE-bench evaluation")
    parser.add_argument(
        "--tasks",
        nargs="+",
        metavar="TASK_ID",
        help="Task IDs to run (default: all ready duckdb+dbt tasks)",
    )
    parser.add_argument(
        "--agent",
        default="sage",
        help="ADE-bench agent to use (default: sage)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override output directory for ade results",
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=1,
        help="Number of concurrent trials (default: 1)",
    )
    parser.add_argument(
        "--n-attempts",
        type=int,
        default=1,
        help="Number of attempts per task; pass if any attempt passes (default: 1)",
    )
    args = parser.parse_args()

    ade_bench_dir = Path(os.environ.get("ADE_BENCH_DIR", "~/repos/ade-bench")).expanduser()
    if not ade_bench_dir.exists():
        sys.exit(f"ADE_BENCH_DIR not found: {ade_bench_dir}")

    from labrat.eval.runners.ade_bench_runner import AdeBenchRunner
    from labrat.eval.suites.ade_bench import AdeBenchSuite

    suite = AdeBenchSuite(ade_bench_dir=ade_bench_dir)
    cases = suite.cases
    if args.tasks:
        ids = set(args.tasks)
        cases = [c for c in cases if c.id in ids]
        missing = ids - {c.id for c in cases}
        if missing:
            print(f"Warning: tasks not found in suite: {', '.join(sorted(missing))}")

    print(f"Running {len(cases)} ADE-bench task(s) with agent={args.agent}, n_attempts={args.n_attempts}")

    runner = AdeBenchRunner(
        cases=cases,
        ade_bench_dir=ade_bench_dir,
        agent=args.agent,
        output_path=args.output_dir,
        n_concurrent_trials=args.concurrent,
        n_attempts=args.n_attempts,
    )
    report = runner.run()
    print(report.to_markdown())


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify the script still parses args correctly**

```bash
cd ~/repos/labrat && uv run scripts/eval_ade_bench.py --help
```

Expected: help text includes `--n-attempts`.

- [ ] **Step 4: Commit**

```bash
cd ~/repos/labrat
git add scripts/eval_ade_bench.py
git commit -m "$(cat <<'EOF'
feat: add --n-attempts flag to eval_ade_bench.py

Passes through to AdeBenchRunner for pass@k scoring.
Usage: uv run scripts/eval_ade_bench.py --n-attempts 3

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Enhanced DOCKER_PREAMBLE — verification + anti-patterns

This task modifies the ade-bench repo, not the labrat repo.

**Files:**
- Modify: `~/repos/ade-bench/ade_bench/agents/installed_agents/labrat_local/labrat_local_agent.py`

- [ ] **Step 1: Read the current file**

```bash
cat ~/repos/ade-bench/ade_bench/agents/installed_agents/labrat_local/labrat_local_agent.py
```

- [ ] **Step 2: Replace _DOCKER_PREAMBLE with the enhanced version**

In `labrat_local_agent.py`, replace the `_DOCKER_PREAMBLE` string (the module-level constant, lines 16–53) with:

```python
_DOCKER_PREAMBLE = """\
You are working on a dbt project inside Docker container `{container_name}`.
Your ONLY means of interacting with the project files and running commands is \
via your local Bash tool using `docker exec` and `docker cp`.

HOW TO INTERACT WITH THE CONTAINER

Read a file:
  docker exec {container_name} cat /app/models/staging/example.sql

List a directory:
  docker exec {container_name} ls /app/models/

Run dbt commands:
  docker exec {container_name} bash -c "cd /app && dbt run --select <model_name>"
  docker exec {container_name} bash -c "cd /app && dbt test --select <model_name>"
  docker exec {container_name} bash -c "cd /app && dbt compile"

Edit a file (write to /tmp locally, then docker cp into the container):
  cat > /tmp/_edit.sql << 'HEREDOC'
  <new file contents>
  HEREDOC
  docker cp /tmp/_edit.sql {container_name}:/app/models/staging/example.sql

Create a new file (same pattern — write locally then docker cp):
  cat > /tmp/_new.sql << 'HEREDOC'
  <file contents>
  HEREDOC
  docker cp /tmp/_new.sql {container_name}:/app/models/staging/new_model.sql

The dbt project root inside the container is `/app`.

BEFORE WRITING ANY SQL — explore the project first:
  docker exec {container_name} ls /app/models/
  docker exec {container_name} find /app/models -name "*.sql" | sort
  docker exec {container_name} cat /app/dbt_project.yml
Read existing models to understand the grain, join keys, and naming conventions \
before writing anything new.

MANDATORY VERIFICATION — do this before finishing:
1. Build your changed model and its dependents:
   docker exec {container_name} bash -c "cd /app && dbt build --select +<your_model_name>"
2. Confirm the output shows PASS=N WARN=0 ERROR=0. If any ERROR appears, debug and fix it.
3. Inspect a sample of your model's output to verify the data makes sense:
   docker exec {container_name} bash -c "cd /app && dbt show --select <your_model_name> --limit 5"
4. If the task asks for specific columns or renamed columns, confirm they appear in the output.
5. Do NOT finish if any test fails or if ERROR appears in dbt output.

COMMON DuckDB/dbt MISTAKES TO AVOID:
- DATE_TRUNC('month', col) + 1 is wrong — use col + INTERVAL '1 day' or dateadd(day, 1, col)
- {{ get_current_timestamp() }} is not available in DuckDB — use current_timestamp
- Never assume a model or table exists; always verify with ls or cat before referencing it
- When renaming a column, check if downstream models reference the old name: grep for it
- Check dbt package models: docker exec {container_name} ls /app/dbt_packages/
- Use {{ ref('model_name') }} for dbt model references, never raw table names
- After dbt compile passes, always run dbt build (not just dbt run) to catch test failures

## Task

{task_prompt}
"""
```

- [ ] **Step 3: Verify the file still parses (syntax check)**

```bash
python3 -c "import ast; ast.parse(open('/Users/ege/repos/ade-bench/ade_bench/agents/installed_agents/labrat_local/labrat_local_agent.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Spot-check the prompt renders correctly**

```bash
python3 - << 'EOF'
import sys
sys.path.insert(0, '/Users/ege/repos/ade-bench')
from ade_bench.agents.installed_agents.labrat_local.labrat_local_agent import _DOCKER_PREAMBLE
prompt = _DOCKER_PREAMBLE.format(container_name="test_container", task_prompt="Create a dim_accounts_v2 model.")
assert "MANDATORY VERIFICATION" in prompt
assert "COMMON DuckDB/dbt MISTAKES" in prompt
assert "explore the project first" in prompt
assert "test_container" in prompt
print("Prompt renders correctly. Length:", len(prompt))
EOF
```

Expected: prints length (>2000 chars), no errors.

- [ ] **Step 5: Commit in the ade-bench repo**

```bash
cd ~/repos/ade-bench
git add ade_bench/agents/installed_agents/labrat_local/labrat_local_agent.py
git commit -m "$(cat <<'EOF'
feat(labrat_local): add verification + anti-patterns to DOCKER_PREAMBLE

Adds mandatory pre-submit verification (dbt build + dbt show) and a
list of common DuckDB/dbt mistakes. Targets 17/20 equality failures
from the 2026-05-24 baseline run.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Per-family domain hints in LabratLocalAgent

This task adds a `_FAMILY_HINTS` dict that injects task-family-specific context into the prompt when the task ID matches a known family prefix. Targets the two worst-performing families: `analytics_engineering` (25% pass) and `asana` (50% pass).

**Files:**
- Modify: `~/repos/ade-bench/ade_bench/agents/installed_agents/labrat_local/labrat_local_agent.py`

- [ ] **Step 1: Add _FAMILY_HINTS constant and hint injection to perform_task**

After the `_DOCKER_PREAMBLE` definition and before the `LabratLocalAgent` class, add:

```python
# Per-family hints injected when task_name matches a prefix.
# These encode family-specific conventions learned from failure analysis.
_FAMILY_HINTS: dict[str, str] = {
    "analytics_engineering": """\

ANALYTICS_ENGINEERING FAMILY HINTS:
- These tasks often require creating OBT (one big table) models by joining multiple dimensions.
- Always read ALL existing staging and intermediate models first to understand available columns.
- The grain of an OBT is usually the grain of the fact table (one row per transaction/event).
- Check for existing tests in /app/tests/ — they reveal expected columns and row counts.
- Run: docker exec {container_name} find /app -name "*.yml" | xargs grep -l "tests:" to find test configs.
""",
    "asana": """\

ASANA FAMILY HINTS:
- The dbt_asana package provides base models. Check /app/dbt_packages/asana/models/ before writing.
- Intermediate models (int_*) usually aggregate at the project or user level.
- Common join keys: task_id, project_id, user_id — verify foreign keys in staging models.
- Run: docker exec {container_name} find /app/dbt_packages -name "*.sql" | head -20 to discover package models.
""",
    "f1": """\

F1 FAMILY HINTS:
- The F1 dataset tracks races, drivers, constructors, and results with race_id + driver_id + constructor_id.
- Aggregation tasks (most wins, most podiums) typically group by driver_id over race_results.
- A podium finish is position IN (1, 2, 3); a pole position is grid = 1 (qualifying position).
- Run: docker exec {container_name} bash -c "cd /app && dbt show --select stg_results --limit 3" to see column names.
""",
}
```

Then inside `LabratLocalAgent.perform_task`, after building `enhanced_prompt`, add hint injection:

```python
    def perform_task(
        self,
        task_prompt: str,
        session: TmuxSession,
        logging_dir: Path | None = None,
        task_name: str | None = None,
    ) -> AgentResult:
        container_name = session.container.name
        enhanced_prompt = _DOCKER_PREAMBLE.format(
            container_name=container_name,
            task_prompt=task_prompt,
        )

        # Inject per-family hints when task_name matches a known prefix
        if task_name:
            for family_prefix, hint_template in _FAMILY_HINTS.items():
                if task_name.startswith(family_prefix):
                    hint = hint_template.format(container_name=container_name)
                    # Insert hints just before the "## Task" section
                    enhanced_prompt = enhanced_prompt.replace(
                        "\n## Task\n", f"{hint}\n## Task\n", 1
                    )
                    break
        # ... rest of the method unchanged
```

The full updated `perform_task` method (complete replacement):

```python
    def perform_task(
        self,
        task_prompt: str,
        session: TmuxSession,
        logging_dir: Path | None = None,
        task_name: str | None = None,
    ) -> AgentResult:
        container_name = session.container.name
        enhanced_prompt = _DOCKER_PREAMBLE.format(
            container_name=container_name,
            task_prompt=task_prompt,
        )

        # Inject per-family hints when task_name matches a known prefix
        if task_name:
            for family_prefix, hint_template in _FAMILY_HINTS.items():
                if task_name.startswith(family_prefix):
                    hint = hint_template.format(container_name=container_name)
                    enhanced_prompt = enhanced_prompt.replace(
                        "\n## Task\n", f"{hint}\n## Task\n", 1
                    )
                    break

        cmd = [
            "claude",
            "--output-format",
            "stream-json",
            "--verbose",
            "-p",
            enhanced_prompt,
            "--allowedTools",
            "Bash",
        ]
        if self._model_name:
            cmd.extend(["--model", self._model_name])

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=config.default_agent_timeout_sec,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                input_tokens=0,
                output_tokens=0,
                failure_mode=FailureMode.AGENT_TIMEOUT,
                runtime_ms=int((time.monotonic() - start) * 1000),
            )
        except Exception:
            return AgentResult(
                input_tokens=0,
                output_tokens=0,
                failure_mode=FailureMode.UNKNOWN_AGENT_ERROR,
                runtime_ms=int((time.monotonic() - start) * 1000),
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if logging_dir is not None:
            log_path = Path(logging_dir) / "agent.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(proc.stdout + proc.stderr)

        metrics = self._parser.parse(proc.stdout)

        failure_mode = FailureMode.NONE
        if proc.returncode != 0 and not metrics.get("success"):
            failure_mode = FailureMode.UNKNOWN_AGENT_ERROR

        return AgentResult(
            input_tokens=metrics.get("input_tokens", 0),
            output_tokens=metrics.get("output_tokens", 0),
            cache_tokens=metrics.get("cache_tokens", 0),
            num_turns=metrics.get("num_turns", 0),
            runtime_ms=metrics.get("runtime_ms") or elapsed_ms,
            cost_usd=metrics.get("cost_usd", 0.0),
            model_name=metrics.get("model_name"),
            failure_mode=failure_mode,
        )
```

- [ ] **Step 2: Verify hint injection works**

```bash
python3 - << 'EOF'
import sys
sys.path.insert(0, '/Users/ege/repos/ade-bench')
from ade_bench.agents.installed_agents.labrat_local.labrat_local_agent import (
    _DOCKER_PREAMBLE, _FAMILY_HINTS
)

# Simulate hint injection for an analytics_engineering task
task_name = "analytics_engineering004"
container_name = "test_container"
task_prompt = "Create a model called obt_product_inventory."
enhanced_prompt = _DOCKER_PREAMBLE.format(container_name=container_name, task_prompt=task_prompt)

for family_prefix, hint_template in _FAMILY_HINTS.items():
    if task_name.startswith(family_prefix):
        hint = hint_template.format(container_name=container_name)
        enhanced_prompt = enhanced_prompt.replace("\n## Task\n", f"{hint}\n## Task\n", 1)
        break

assert "ANALYTICS_ENGINEERING FAMILY HINTS" in enhanced_prompt
assert "OBT" in enhanced_prompt
assert "## Task" in enhanced_prompt
assert "obt_product_inventory" in enhanced_prompt
print("Hint injection works. Prompt length:", len(enhanced_prompt))

# Verify asana hint injects correctly
task_name2 = "asana004"
prompt2 = _DOCKER_PREAMBLE.format(container_name=container_name, task_prompt="Create an asana model.")
for family_prefix, hint_template in _FAMILY_HINTS.items():
    if task_name2.startswith(family_prefix):
        hint = hint_template.format(container_name=container_name)
        prompt2 = prompt2.replace("\n## Task\n", f"{hint}\n## Task\n", 1)
        break
assert "ASANA FAMILY HINTS" in prompt2
print("Asana hint also works.")

# Verify non-matching task gets no hint
task_name3 = "helixops_saas009"
prompt3 = _DOCKER_PREAMBLE.format(container_name=container_name, task_prompt="Create v2.")
for family_prefix, hint_template in _FAMILY_HINTS.items():
    if task_name3.startswith(family_prefix):
        hint = hint_template.format(container_name=container_name)
        prompt3 = prompt3.replace("\n## Task\n", f"{hint}\n## Task\n", 1)
        break
assert "ANALYTICS_ENGINEERING" not in prompt3
assert "ASANA" not in prompt3
print("Non-matching task correctly gets no hint.")
EOF
```

Expected: all three assertions pass, prints confirmation messages.

- [ ] **Step 3: Syntax check**

```bash
python3 -c "import ast; ast.parse(open('/Users/ege/repos/ade-bench/ade_bench/agents/installed_agents/labrat_local/labrat_local_agent.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit in ade-bench repo**

```bash
cd ~/repos/ade-bench
git add ade_bench/agents/installed_agents/labrat_local/labrat_local_agent.py
git commit -m "$(cat <<'EOF'
feat(labrat_local): add per-family domain hints for analytics_engineering, asana, f1

Injects family-specific context when task_name prefix matches.
Targets analytics_engineering (25% pass rate) and asana (50% pass rate).

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Cast analysis script

Build a script that reads ADE-bench experiment directories and extracts structured failure patterns from agent.cast files. Output is a human-readable summary of what each failing agent actually did (commands run, errors seen, turn count).

**Files:**
- Create: `~/repos/labrat/scripts/analyze_ade_failures.py`

- [ ] **Step 1: Write the script**

Create `~/repos/labrat/scripts/analyze_ade_failures.py`:

```python
#!/usr/bin/env python3
"""Analyze ADE-bench experiment failures from cast + results files.

Usage:
    uv run scripts/analyze_ade_failures.py ~/repos/ade-bench/experiments/2026-05-24__23-15-04__none

Reads all trials from a run directory, filters to failures, and prints:
- task_id, turns, cost, which tests failed
- The dbt commands the agent ran (extracted from cast file)
- Any ERROR or FAIL lines seen in the cast output
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def _read_cast_commands(cast_path: Path) -> list[str]:
    """Extract bash commands run by the agent from an asciinema cast file."""
    commands: list[str] = []
    try:
        lines = cast_path.read_text(errors="replace").splitlines()
    except OSError:
        return commands

    output_chunks: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, list) or len(event) < 3:
            continue
        if event[1] == "o":
            output_chunks.append(event[2])

    full_output = _strip_ansi("".join(output_chunks))

    # Heuristic: extract lines that look like shell prompts followed by a command
    # The pattern is: "root@<hash>:/app# <command>"
    prompt_re = re.compile(r"root@[0-9a-f]+:/app#\s+(.+)")
    for line in full_output.splitlines():
        m = prompt_re.match(line.strip())
        if m:
            cmd = m.group(1).strip()
            if cmd and not cmd.startswith("echo"):
                commands.append(cmd)
    return commands


def _find_error_lines(cast_path: Path) -> list[str]:
    """Extract ERROR/FAIL lines from cast output."""
    error_lines: list[str] = []
    try:
        lines = cast_path.read_text(errors="replace").splitlines()
    except OSError:
        return error_lines

    output_chunks: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, list) and len(event) >= 3 and event[1] == "o":
            output_chunks.append(event[2])

    full_output = _strip_ansi("".join(output_chunks))
    for line in full_output.splitlines():
        stripped = line.strip()
        if re.search(r"\b(ERROR|FAIL|Error:|Exception:)\b", stripped) and len(stripped) < 300:
            error_lines.append(stripped)
    return error_lines[:10]  # Cap at 10 lines per task


def analyze_run(run_dir: Path) -> None:
    results_file = run_dir / "results.json"
    if not results_file.exists():
        sys.exit(f"No results.json in {run_dir}")

    data = json.loads(results_file.read_text())
    all_results = data.get("results", [])

    failures = [r for r in all_results if not r.get("is_resolved")]
    passes = [r for r in all_results if r.get("is_resolved")]

    print(f"Run: {run_dir.name}")
    print(f"Total: {len(all_results)}  Passed: {len(passes)}  Failed: {len(failures)}")
    print(f"Pass rate: {len(passes)/len(all_results)*100:.1f}%\n")
    print("=" * 70)

    for result in sorted(failures, key=lambda r: r["task_id"]):
        task_id = result["task_id"]
        trial_name = result.get("trial_name", "")
        turns = result.get("num_turns", "?")
        cost = result.get("cost_usd", 0.0)
        parser = result.get("parser_results", {})
        failed_tests = [k for k, v in parser.items() if v == "failed"]

        print(f"\n{'─'*60}")
        print(f"FAIL  {task_id}  ({turns} turns, ${cost:.3f})")
        print(f"      Failed tests: {', '.join(failed_tests) or 'none'}")

        # Find cast file
        cast_path: Path | None = None
        recording = result.get("recording_path", "")
        if recording:
            # recording_path is relative to the experiments/ parent
            experiments_dir = run_dir.parent
            candidate = experiments_dir / recording
            if candidate.exists():
                cast_path = candidate

        if cast_path:
            commands = _read_cast_commands(cast_path)
            dbt_cmds = [c for c in commands if "dbt" in c]
            print(f"      dbt commands ({len(dbt_cmds)}):")
            for cmd in dbt_cmds[:8]:
                print(f"        $ {cmd}")
            if len(dbt_cmds) > 8:
                print(f"        ... ({len(dbt_cmds) - 8} more)")

            errors = _find_error_lines(cast_path)
            if errors:
                print(f"      Error lines:")
                for e in errors:
                    print(f"        {e[:120]}")
        else:
            print(f"      (no cast file found at {recording})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    analyze_run(Path(sys.argv[1]).expanduser())
```

- [ ] **Step 2: Run the script against the baseline experiment**

```bash
cd ~/repos/labrat && uv run scripts/analyze_ade_failures.py ~/repos/ade-bench/experiments/2026-05-24__23-15-04__none
```

Expected: output shows 20 failures with per-task turn counts, dbt commands, and error lines. Review the output carefully — look for patterns:
- Tasks where the agent ran `dbt run` but not `dbt test`
- Tasks where `ERROR` appears but the agent continued anyway
- Tasks where the agent ran only 1–2 dbt commands (under-exploration)

- [ ] **Step 3: Commit**

```bash
cd ~/repos/labrat
git add scripts/analyze_ade_failures.py
git commit -m "$(cat <<'EOF'
feat: add analyze_ade_failures.py script for failure pattern extraction

Reads asciinema cast files and results.json to print per-task:
dbt commands run, error lines seen, turn count, cost, failed tests.
Used for Tier 2 failure-driven prompt engineering.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Validate Tier 1 improvements on the 20 failing tasks

Run the 20 failing tasks with the new prompt (Tasks 4+5) and N=3 (Task 2+3). Compare against the baseline.

- [ ] **Step 1: Run the 20 failing tasks with n_attempts=3**

This command runs only the previously-failing tasks with 3 attempts each. Expected runtime: ~2–3 hours, ~$12–15.

```bash
cd ~/repos/ade-bench && uv run ade run \
  analytics_engineering004 analytics_engineering006 analytics_engineering007.medium \
  quickbooks003 quickbooks004 \
  asana004 asana005 "asana005.hard" \
  airbnb010 "airbnb011.hint" airbnb012 \
  "helixops_saas007.no_location_hint" helixops_saas009 helixops_saas010 helixops_saas015 \
  f1002 f1005 "f1005.medium" f1009 \
  intercom002 \
  --db duckdb --project-type dbt --agent labrat_local \
  --n-attempts 3 --n-concurrent-trials 3 --no-diffs
```

- [ ] **Step 2: Analyze results**

```bash
# Find the new experiment dir
ls -t ~/repos/ade-bench/experiments/ | head -3

# Check the new results
cd ~/repos/labrat && uv run scripts/analyze_ade_failures.py ~/repos/ade-bench/experiments/<NEW_RUN_DIR>
```

Expected: at least 5 of the 20 previously-failing tasks now pass. If fewer than 3 new passes, read the cast files to understand why the verification step isn't triggering.

- [ ] **Step 3: Update benchmark_plan.md with new baseline**

If any tasks newly pass, update the failure table in `benchmark_plan.md`:
- Change the "Failed tasks" column to reflect which are still failing
- Note the new pass rate and date

- [ ] **Step 4: Run the full 60-task suite (optional, ~$22)**

Only run this if Step 2 shows promising results (≥5 new passes on the 20-task subset):

```bash
cd ~/repos/ade-bench && uv run ade run \
  --db duckdb --project-type dbt --agent labrat_local \
  --n-attempts 3 --n-concurrent-trials 3 --no-diffs
```

- [ ] **Step 5: Push labrat repo changes**

```bash
cd ~/repos/labrat && git push origin master
```

---

## Post-implementation checklist

- [ ] All labrat tests still pass: `uv run pytest tests/unit/test_eval/ -v`
- [ ] `scripts/eval_ade_bench.py --help` shows `--n-attempts` option
- [ ] `analyze_ade_failures.py` produces readable output on the baseline run
- [ ] ade-bench changes are committed in `~/repos/ade-bench`
- [ ] New pass rate documented in `benchmark_plan.md` with the run timestamp
- [ ] If new failures are found from Task 7 Step 2, add findings to `_FAMILY_HINTS` or the anti-patterns section and re-run Task 7

## What comes next (after this plan)

1. **Tier 2 #4 — Full cast analysis**: after running Task 7, read all still-failing cast files using the new `analyze_ade_failures.py` script. Look for the top 3 recurring patterns. Add specific fixes to `_DOCKER_PREAMBLE` or `_FAMILY_HINTS`.
2. **Tier 2 #5 — Hint variant probe**: for the 5 variant tasks (`.hint`, `.medium`, `.hard`) that failed, manually re-inject the hint text and test if they pass. If so, the fix is a "find context before writing" loop, not more model capacity.
3. **Tier 3 — Plan-then-execute pattern**: if equality failures persist after Tier 1+2, add a mandatory planning phase to `_DOCKER_PREAMBLE` that requires the agent to write out its transformation logic before writing SQL.
