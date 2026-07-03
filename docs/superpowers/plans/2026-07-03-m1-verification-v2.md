# M1 — Verification-v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the null-ablated consensus as the MinusX-proven version — input-diversity (K seeded Scent variants + rotated framing) consensus, argumentation rounds, and SCRIBE's zero-LLM post-step verifiers.

**Architecture:** Three units across four phases. Unit 3 (deterministic verifiers) is pure/zero-LLM and ships first. Unit 1 adds per-sub-run seeded Scent variants + framing to `DabSuite`'s consensus dispatch. Unit 2 adds bounded argumentation on a split vote. All default-off, flag-gated, fail-open, built on the shipped `agent/verification/` + `DabSuite._run_trial_verified`.

**Tech Stack:** Python 3.12, Pydantic, DuckDB, pytest (`asyncio_mode=auto`), ruff, pyright strict.

## Global Constraints

- Default-off; each unit independently flag-gated; fail-open everywhere (a failed sub-run / unparseable judge verdict never traps the loop).
- GT-firewalled: Scent variants sample DB rows only; every generated doc still passes `audit_scent_doc`.
- The equivalence judge routes to the `claude-code` provider on the claude-mcp path (`_verify_llm_fn`, already shipped) — do not change that.
- `diversity_index=None` / default flags → the verified path is byte-identical to today's behavior.
- No LLM calls in Unit 3 (deterministic).
- Before commit: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. All clean. `json.loads`→Unknown: narrow/cast per convention.

---

## Phase 1 — Unit 3: deterministic post-step verifiers (zero-LLM)

### Task 1: `run_sql` success warnings

**Files:**
- Modify: `src/labrat/agent/tools/run_sql.py` (`_Output` model + the success return at ~line 284)
- Test: `tests/unit/test_run_sql_warnings.py`

**Interfaces:**
- Produces: `_Output` gains `warnings: list[str] = []`. On a successful SELECT, `warnings` contains deterministic danger-signal strings: empty-result-when-filtered, and per-column all-NULL. Default `[]` (back-compat; error/refusal paths unchanged).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_run_sql_warnings.py
from __future__ import annotations

import duckdb
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.db.duckdb_engine import DuckDBConnection


def _conn(tmp_path):
    p = str(tmp_path / "w.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(a INT, b INT)")
    raw.execute("INSERT INTO t VALUES (1, NULL), (2, NULL)")
    raw.close()
    c = DuckDBConnection(path=p, read_only=False)
    c.connect()
    return c


async def test_empty_result_when_filtered_warns(tmp_path) -> None:
    conn = _conn(tmp_path)
    out = await RunSqlTool().execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        RunSqlTool().input_model(query="SELECT * FROM t WHERE a = 999"),
    )
    assert out.ok
    assert any("0 rows" in w.lower() or "empty" in w.lower() for w in out.warnings)
    conn.disconnect()


async def test_all_null_column_warns(tmp_path) -> None:
    conn = _conn(tmp_path)
    out = await RunSqlTool().execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        RunSqlTool().input_model(query="SELECT a, b FROM t"),
    )
    assert out.ok
    assert any("b" in w and "null" in w.lower() for w in out.warnings)
    conn.disconnect()


async def test_clean_query_no_warnings(tmp_path) -> None:
    conn = _conn(tmp_path)
    out = await RunSqlTool().execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        RunSqlTool().input_model(query="SELECT a FROM t"),
    )
    assert out.ok and out.warnings == []
    conn.disconnect()
```

(Confirm `RunSqlTool().input_model` field name is `query` and the `ToolContext(connection=..., catalog=None, primary=...)` single-DB shim — from run_sql.py / base.py.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_run_sql_warnings.py -v`
Expected: FAIL — `_Output` has no `warnings`.

- [ ] **Step 3: Implement**

Add to `_Output`: `warnings: list[str] = []`.

Compute warnings before the success `_Output(...)` return (after `rows`/`df` exist). A statement "has a filter" is a cheap check on the SQL text:

```python
        warnings: list[str] = []
        lowered = sql.lower()
        if len(df) == 0 and (" where " in lowered or " join " in lowered):
            warnings.append("Query returned 0 rows despite a WHERE/JOIN — check the predicate and join keys.")
        elif len(df) > 0:
            for col in df.columns:
                if df[col].null_count() == len(df):
                    warnings.append(f"Column `{col}` is entirely NULL in the result — likely a bad join or wrong column.")
```

Then pass `warnings=warnings` into the success `_Output(ok=True, ...)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_run_sql_warnings.py -v`
Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/agent/tools/run_sql.py tests/unit/test_run_sql_warnings.py
git commit -m "feat(run_sql): deterministic success warnings (empty-after-filter, all-null column)"
```

---

### Task 2: question-constraint checker (pure)

**Files:**
- Create: `src/labrat/agent/verification/constraints.py`
- Test: `tests/unit/test_answer_constraints.py`

**Interfaces:**
- Produces: `check_answer_constraints(question: str, answer: str) -> list[str]` — deterministic (no LLM). Returns violation strings when the answer's shape contradicts an expectation extracted from the question (conservative: only high-confidence mismatches).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_answer_constraints.py
from __future__ import annotations

from labrat.agent.verification.constraints import check_answer_constraints


def test_top_n_count_mismatch_flagged() -> None:
    v = check_answer_constraints("What are the top 5 products by revenue?", "Widget, Gadget, Gizmo")
    assert any("5" in s for s in v)  # asked for 5, answer lists 3


def test_top_n_satisfied_no_flag() -> None:
    v = check_answer_constraints("top 3 products", "A\nB\nC")
    assert v == []


def test_percentage_expected_but_absent_flagged() -> None:
    v = check_answer_constraints("What percentage of users churned?", "About 4 thousand users")
    assert any("percent" in s.lower() for s in v)


def test_percentage_present_no_flag() -> None:
    assert check_answer_constraints("what percentage churned?", "12.5%") == []


def test_no_extractable_constraint_no_flag() -> None:
    assert check_answer_constraints("Which city has the most stores?", "Chicago") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_answer_constraints.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# src/labrat/agent/verification/constraints.py
"""Deterministic question→answer constraint checks (no LLM).

Extract high-confidence shape expectations from the question text and flag when the
candidate answer clearly contradicts them. Conservative by design — only flag when the
mismatch is unambiguous, to avoid false-positive reviser churn.
"""

from __future__ import annotations

import re

_TOP_N_RE = re.compile(r"\btop\s+(\d{1,3})\b", re.IGNORECASE)
_PERCENT_Q_RE = re.compile(r"\b(percentage|percent|what\s+%|proportion)\b", re.IGNORECASE)
_PERCENT_A_RE = re.compile(r"\d+(\.\d+)?\s*%|\bpercent\b", re.IGNORECASE)


def _answer_item_count(answer: str) -> int:
    """Rough item count: prefer newlines, else commas, else 1 for a non-empty scalar."""
    lines = [ln for ln in answer.splitlines() if ln.strip()]
    if len(lines) > 1:
        return len(lines)
    parts = [p for p in answer.split(",") if p.strip()]
    if len(parts) > 1:
        return len(parts)
    return 1 if answer.strip() else 0


def check_answer_constraints(question: str, answer: str) -> list[str]:
    violations: list[str] = []
    m = _TOP_N_RE.search(question)
    if m:
        n = int(m.group(1))
        got = _answer_item_count(answer)
        # only flag a clear shortfall (agent listed materially fewer than asked)
        if 0 < got < n:
            violations.append(f"Question asks for the top {n}, but the answer lists {got} items.")
    if _PERCENT_Q_RE.search(question) and not _PERCENT_A_RE.search(answer):
        violations.append("Question asks for a percentage, but the answer has no percentage value.")
    return violations
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_answer_constraints.py -v`
Expected: PASS (5).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/agent/verification/constraints.py tests/unit/test_answer_constraints.py
git commit -m "feat(verification): deterministic question-constraint checker"
```

---

## Phase 2 — Unit 1a: seeded Scent variants

### Task 3: seeded sampling in `generate_scent` / `build_dimensions`

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (`build_dimensions`, `generate_scent`, and the M0 format/example sampling — thread `variant_seed`)
- Test: `tests/unit/test_cartographer_variants.py`

**Interfaces:**
- Produces: `generate_scent(..., variant_seed: int = 0)` and `build_dimensions(profile, conn, *, cap=25, variant_seed: int = 0)`. `variant_seed=0` → today's deterministic output byte-for-byte. `variant_seed=i>0` → the same low-cardinality dimensions but *different sampled example rows* on high-cardinality columns (seeded, reproducible).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartographer_variants.py
from __future__ import annotations

import duckdb
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import build_dimensions


async def _profile(tmp_path):
    p = str(tmp_path / "v.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(id INT, note VARCHAR)")
    raw.execute("INSERT INTO t SELECT i, 'note-' || i || '->x' FROM range(200) tbl(i)")
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False)
    conn.connect()
    prof = await ProfileDatasetTool().execute(
        ToolContext(connection=conn, catalog=conn.introspect_catalog(), primary="main"),
        ProfileDatasetTool().input_model(sample_rows=0, max_tables=100),
    )
    return conn, prof


async def test_variant_seed_changes_high_card_samples(tmp_path) -> None:
    conn, prof = await _profile(tmp_path)
    b0 = build_dimensions(prof, conn, variant_seed=0).body
    b1 = build_dimensions(prof, conn, variant_seed=1).body
    assert b0 != b1  # different seeded example samples on the high-cardinality note column
    conn.disconnect()


async def test_variant_seed_zero_is_stable(tmp_path) -> None:
    conn, prof = await _profile(tmp_path)
    assert build_dimensions(prof, conn, variant_seed=0).body == build_dimensions(prof, conn, variant_seed=0).body
    conn.disconnect()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_variants.py -v`
Expected: FAIL — `build_dimensions` takes no `variant_seed`.

- [ ] **Step 3: Implement**

Add `variant_seed: int = 0` to `build_dimensions` and `generate_scent` (thread it into the `build_dimensions` call in `generate_scent`). In `build_dimensions`, make the example/format-sample scans seeded: where M0 does `SELECT DISTINCT col ... LIMIT n` / the format-sample scan, order by a seed-mixed hash so a different seed picks different rows but each seed is reproducible:

```python
                df = conn.execute(
                    f"SELECT DISTINCT {col.name} FROM {t.name} "
                    f"WHERE {col.name} IS NOT NULL "
                    f"ORDER BY hash(CAST({col.name} AS VARCHAR) || '{variant_seed}') "
                    f"LIMIT {cap + 1}"
                )
```

Apply the same `ORDER BY hash(... || '{variant_seed}')` to the M0 format-sample scan (the `LIMIT 200` unusual-structure scan). Leave the min/max range lines unseeded (ranges are seed-invariant). The low-cardinality dimension gate (`len(vals) <= cap`) still collapses small-domain columns to identical values across seeds — correct (nothing to diversify).

NOTE: `variant_seed` is interpolated into SQL as a literal string; it is an `int` (never user input), so no injection risk — but keep the `int` type to be safe.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_variants.py tests/unit/test_cartographer_value_profile.py tests/unit/test_dab_cartographer.py -v`
Expected: PASS (new + existing cartographer regression — `variant_seed=0` keeps existing output stable).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_variants.py
git commit -m "feat(cartographer): variant_seed for reproducible per-variant sampling"
```

---

### Task 4: variant-scoped `_run_cartographer`

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` (`_run_cartographer` ~line 340)
- Test: `tests/unit/test_dab_cartographer.py` (extend) or a new `tests/unit/test_dab_cartographer_variant.py`

**Interfaces:**
- Produces: `_run_cartographer(env_spec, dataset, cache_root, *, with_semantics=False, llm_fn=None, variant_seed: int = 0) -> Path` — when `variant_seed > 0`, writes to a variant-scoped maze root (`cache_root / <safe dataset> / f"variant{variant_seed}"` containing `labrat_maze/scent`) and passes `variant_seed` into `generate_scent`. `variant_seed=0` → unchanged path + output.

- [ ] **Step 1: Write the failing test** (new file `tests/unit/test_dab_cartographer_variant.py` — mirror the construction in the existing `test_dab_cartographer.py`; if that test builds an env/DB fixture, reuse its helper)

```python
from labrat.eval.benchmarks.dab.suite import _run_cartographer
# Build the same env_spec/db fixture the existing cartographer test uses, then:
# root0 = await _run_cartographer(env, "ds", tmp_path, variant_seed=0)
# root1 = await _run_cartographer(env, "ds", tmp_path, variant_seed=1)
# assert root0 != root1
# assert (root1 / "labrat_maze" / "scent").exists()
```

(Read `test_dab_cartographer.py` for the exact env/DB fixture; write the concrete test against it. Assert the two seeds produce distinct maze roots and both have a scent dir.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_cartographer_variant.py -v`
Expected: FAIL — `_run_cartographer` takes no `variant_seed`.

- [ ] **Step 3: Implement** — add `variant_seed: int = 0`; when >0, suffix the maze root with `f"variant{variant_seed}"`; thread `variant_seed` into the `generate_scent(...)` call inside `_run_cartographer`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dab_cartographer_variant.py tests/unit/test_dab_cartographer.py -v`
Expected: PASS (variant-0 path unchanged; variant-1 distinct root).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py tests/unit/test_dab_cartographer_variant.py
git commit -m "feat(dab): variant-scoped _run_cartographer(variant_seed=)"
```

---

## Phase 3 — Unit 1b: framing rotation + diverse-consensus wiring

### Task 5: thread `diversity_index` through the claude-mcp dispatch

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` — `_dispatch_driver_once` (~732), `_run_trial_claude_mcp` (~840), and a new module-level `_CONSENSUS_FRAMINGS`.
- Test: `tests/unit/test_dab_diversity_dispatch.py`

**Interfaces:**
- Consumes: `_run_cartographer(variant_seed=)` (Task 4).
- Produces:
  - `_CONSENSUS_FRAMINGS: list[str]` — ≥4 neutral analytical-emphasis lines (process-only, no answer content).
  - `_dispatch_driver_once(..., diversity_index: int | None = None)` — passes it to `_run_trial_claude_mcp`.
  - `_run_trial_claude_mcp(..., diversity_index: int | None = None)` — when not None, runs `_run_cartographer(..., variant_seed=diversity_index)` (variant maze root) AND appends `_CONSENSUS_FRAMINGS[diversity_index % len(_CONSENSUS_FRAMINGS)]` to `extra_instructions`. When None → byte-identical to today (`variant_seed=0`, no framing).

- [ ] **Step 1: Write the failing test** — this is integration-heavy; test the pure decomposition. Add a small pure helper `_framing_for(diversity_index: int | None) -> str` returning `""` for None else the rotated framing, and unit-test IT (avoids driving the real claude subprocess):

```python
# tests/unit/test_dab_diversity_dispatch.py
from labrat.eval.benchmarks.dab.suite import _CONSENSUS_FRAMINGS, _framing_for


def test_framing_none_is_empty() -> None:
    assert _framing_for(None) == ""


def test_framing_rotates() -> None:
    assert _framing_for(0) == _CONSENSUS_FRAMINGS[0]
    assert _framing_for(len(_CONSENSUS_FRAMINGS)) == _CONSENSUS_FRAMINGS[0]  # wraps
    assert _framing_for(1) != _framing_for(0)


def test_framings_are_process_only() -> None:
    joined = " ".join(_CONSENSUS_FRAMINGS).lower()
    for banned in ("ground truth", "the answer is"):
        assert banned not in joined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_diversity_dispatch.py -v`
Expected: FAIL — `_CONSENSUS_FRAMINGS`/`_framing_for` undefined.

- [ ] **Step 3: Implement**

Add the constant + helper, then thread the param:

```python
_CONSENSUS_FRAMINGS: list[str] = [
    "Pay extra attention to filters and NULL-handling — confirm no rows were wrongly dropped.",
    "Double-check join grain and whether the top value ties with others.",
    "Confirm units, magnitudes, and that aggregates aren't double-counting from a fan-out join.",
    "Re-read the question's exact wording (which column, which date, coded vs named values) before finalizing.",
]


def _framing_for(diversity_index: int | None) -> str:
    if diversity_index is None:
        return ""
    return _CONSENSUS_FRAMINGS[diversity_index % len(_CONSENSUS_FRAMINGS)]
```

In `_dispatch_driver_once`, add `diversity_index: int | None = None` and pass to `_run_trial_claude_mcp` (and `_run_trial_labrat_agent` — there, just append the framing; variant Scent is claude-mcp-scoped for now). In `_run_trial_claude_mcp`: pass `variant_seed=diversity_index or 0` to `_run_cartographer`, and set `extra_instructions = (extra_instructions + "\n" + _framing_for(diversity_index)).strip()` when framing is non-empty. Guard: only vary the maze root when `self._agent_cartograph` is on (else variant_seed is moot — framing still applies).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dab_diversity_dispatch.py -v`
Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py tests/unit/test_dab_diversity_dispatch.py
git commit -m "feat(dab): diversity_index (variant Scent + rotated framing) through claude-mcp dispatch"
```

---

### Task 6: diverse consensus in `_run_trial_verified` + `--no-consensus-diversity`

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` — `_run_trial_verified` (`_run_once` passes `diversity_index`), `DabSuite.__init__` (add `consensus_diversity: bool = True`), `scripts/eval_dab.py` (`--no-consensus-diversity`).
- Test: `tests/unit/test_dab_verification.py` (extend)

**Interfaces:**
- Produces: when consensus K>1 and `self._consensus_diversity`, `_run_once(i)` dispatches with `diversity_index=i` (diverse sub-runs); when diversity off, `diversity_index=None` (the null-baseline A/B). Re-derive `_run_once(900)` stays `diversity_index=None`. CLI `--no-consensus-diversity` sets `consensus_diversity=False`.

- [ ] **Step 1: Write the failing test** — assert `_run_once` passes `diversity_index` when diversity on. Since `_run_once` is a closure, test via a stubbed `_dispatch_driver_once` capturing the kwarg (monkeypatch), running a K=2 verified trial with a fake driver. Mirror the existing `test_dab_verification.py` stubbing pattern:

```python
# in tests/unit/test_dab_verification.py — add:
async def test_consensus_passes_diversity_index_when_on(monkeypatch) -> None:
    seen: list[int | None] = []
    async def _fake_dispatch(self, task, dbc, sub, *, extra_instructions="", diversity_index=None):
        seen.append(diversity_index)
        return (f"ans{diversity_index}", 1, 0.1)
    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _fake_dispatch)
    # also stub choose_modal to avoid a live judge (return idx 0, low=False)
    ...
    suite = DabSuite(driver="claude-mcp", consensus_k=2, consensus_diversity=True, ...)
    await suite._run_trial_verified(task, db_config_path, scratch)
    assert 0 in seen and 1 in seen  # both sub-runs got distinct diversity indices
```

(Fill in against the existing test file's construction + `choose_modal` stub — read `test_dab_verification.py` for how it stubs the judge/dispatch.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_verification.py -v`
Expected: FAIL — `_run_once` doesn't pass `diversity_index` / `DabSuite` has no `consensus_diversity`.

- [ ] **Step 3: Implement** — add `consensus_diversity: bool = True` to `DabSuite.__init__` (store `self._consensus_diversity`). In `_run_trial_verified`, change `_run_once` to accept and forward `diversity_index`, and in the K-loop pass `diversity_index=(i if self._consensus_diversity else None)`. Add `--no-consensus-diversity` (tri-state `default=None`, mirror `agent_cartograph` resolution → `consensus_diversity`) in `scripts/eval_dab.py` and pass to `DabSuite(...)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dab_verification.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py scripts/eval_dab.py tests/unit/test_dab_verification.py
git commit -m "feat(dab): diverse consensus sub-runs + --no-consensus-diversity ablation control"
```

---

## Phase 4 — Unit 2 (argumentation) + Unit 3b wiring

### Task 7: argumentation rounds on a split vote

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` — `_run_trial_verified` (add argue loop), `DabSuite.__init__` (add `argue_rounds: int = 0`), `scripts/eval_dab.py` (`--agent-argue-rounds N`).
- Test: `tests/unit/test_dab_verification.py` (extend, stubbed — no live LLM)

**Interfaces:**
- Produces: after the K-run `choose_modal`, if `low_confidence` and `self._argue_rounds > 0`, run up to `argue_rounds` rounds: re-dispatch each sub-run with the other sub-runs' `(answer, justification)` appended (justification = the sub-run's `final_text`, truncated to ~1500 chars), re-`choose_modal`, stop early on majority. Fail-open: after the rounds, return the current modal answer (flagged low_confidence if still split). Rounds recorded in the `verification.json` meta. CLI `--agent-argue-rounds` (int, default 0).

- [ ] **Step 1: Write the failing test** (stubbed dispatch + judge; first vote low-confidence, majority after one argue round → argued answer returned). Mirror the test-file stubbing.

```python
async def test_argue_round_resolves_split(monkeypatch) -> None:
    # dispatch returns different answers first, then converges after argue prompt is present
    calls = {"n": 0}
    async def _fake_dispatch(self, task, dbc, sub, *, extra_instructions="", diversity_index=None):
        argued = "Other analysts concluded" in extra_instructions
        return ("CONVERGED" if argued else f"ans{diversity_index}", 1, 0.1)
    # choose_modal: low-confidence when answers differ, high once they match
    async def _fake_modal(answers, *, question, llm_fn):
        distinct = set(answers)
        return (0, len(distinct) > 1)
    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _fake_dispatch)
    monkeypatch.setattr("labrat.agent.verification.consensus.choose_modal", _fake_modal)
    suite = DabSuite(driver="claude-mcp", consensus_k=2, argue_rounds=2, ...)
    final, _, _ = await suite._run_trial_verified(task, db_config_path, scratch)
    assert final == "CONVERGED"
```

(Adapt imports/patch targets to how `_run_trial_verified` imports `choose_modal` — it does `from labrat.agent.verification.consensus import choose_modal` inside the function, so patch `labrat.agent.verification.consensus.choose_modal`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_verification.py::test_argue_round_resolves_split -v`
Expected: FAIL — no argue loop.

- [ ] **Step 3: Implement** the bounded argue loop in `_run_trial_verified` (after the initial K-run modal, before re-derive). Build the "Other analysts concluded: …" block from the other sub-runs' answers+truncated justifications; re-dispatch each sub-run with it appended (`diversity_index` preserved per sub-run); re-`choose_modal`; break on `not low_confidence`; cap at `self._argue_rounds`. Record `argue_rounds_used` in the persisted meta. Add `argue_rounds: int = 0` to `__init__` and the `--agent-argue-rounds` flag + resolution in `eval_dab.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dab_verification.py -v`
Expected: PASS (incl. the new argue test + existing verification tests).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py scripts/eval_dab.py tests/unit/test_dab_verification.py
git commit -m "feat(dab): bounded argumentation rounds on split consensus (--agent-argue-rounds)"
```

---

### Task 8: wire the constraint checker into finalization (`--agent-postverify`)

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` — `_run_trial_verified` finalization, `DabSuite.__init__` (`postverify: bool = False`), `scripts/eval_dab.py` (`--agent-postverify`).
- Test: `tests/unit/test_dab_verification.py` (extend, stubbed)

**Interfaces:**
- Consumes: `check_answer_constraints` (Task 2).
- Produces: when `self._postverify`, after the final answer is chosen, run `check_answer_constraints(question, final_answer)`; if violations, do ONE bounded revise dispatch (re-run with the violations noted as `extra_instructions`), accept its answer, fail-open (any dispatch error → keep the original). Recorded in meta. CLI `--agent-postverify`.

- [ ] **Step 1: Write the failing test** (stubbed): a chosen answer that violates a constraint (e.g. "top 5" question, 3-item answer) triggers one revise dispatch returning a corrected answer; assert the corrected answer is returned and only one revise happened.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_verification.py -v`
Expected: FAIL — no constraint wiring.

- [ ] **Step 3: Implement** the finalization hook (gated by `self._postverify`): call `check_answer_constraints`, on non-empty run one revise dispatch with the violations appended, fail-open. Add `postverify: bool` + `--agent-postverify` flag/resolution.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dab_verification.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py scripts/eval_dab.py tests/unit/test_dab_verification.py
git commit -m "feat(dab): --agent-postverify — constraint check + one bounded revise"
```

---

## Phase 5 — Regression + separated-re-derive hardening

### Task 9: separated-re-derive test + full regression

**Files:**
- Test: `tests/unit/test_dab_verification.py` (add a hardening test)

- [ ] **Step 1:** Add a test asserting the re-derive dispatch (`_run_once(900)`) does NOT receive the primary sub-run's transcript in its prompt — i.e. reverify's re-run gets only the base task prompt (+ any framing), not the primary answer. (Stub `_dispatch_driver_once` to capture `extra_instructions` for the 900-run; assert the primary's `final_text` is not in it.) This locks the Spacedock "separated context" invariant the spec relies on.
- [ ] **Step 2:** `uv run ruff format . && uv run ruff check . && uv run pyright` — clean.
- [ ] **Step 3:** `uv run pytest -q` — all pass (baseline 730 + new tests).
- [ ] **Step 4:** Sanity: default-off path unchanged — `grep`/inspect that with no verification flags, `_run_trial_verified` is not entered (the run_trial gate) and `_dispatch_driver_once(diversity_index=None)` is byte-identical.
- [ ] **Step 5:** Commit any format-only diffs.

---

## Self-Review

**Spec coverage:** Unit 1a (seeded variants) → Tasks 3–4 ✓; Unit 1b (framing + wiring) → Tasks 5–6 ✓; Unit 2 (argumentation) → Task 7 ✓; Unit 3a (run_sql warnings) → Task 1 ✓; Unit 3b (constraint checker + wiring) → Tasks 2, 8 ✓; separated-re-derive hardening → Task 9 ✓; flags (`--no-consensus-diversity`/`--agent-argue-rounds`/`--agent-postverify`) → Tasks 6/7/8 ✓. Default-off + fail-open across all.

**Placeholder scan:** Pure/self-contained tasks (1, 2, 3, 5) carry complete code. The integration-heavy tasks (4, 6, 7, 8, 9) give the exact interface + key code + precise wiring instructions with named surfaces to read (`_run_trial_verified`, `_run_trial_claude_mcp`, `test_dab_verification.py`'s stubbing pattern) — necessary because they edit large existing functions; each names exactly what to add and where.

**Type consistency:** `variant_seed: int` (Tasks 3, 4, 5); `diversity_index: int | None` (Tasks 5, 6, 7); `_framing_for`/`_CONSENSUS_FRAMINGS` (Task 5); `consensus_diversity`/`argue_rounds`/`postverify` bools/int on `DabSuite` (Tasks 6, 7, 8); `check_answer_constraints(question, answer)->list[str]` (Tasks 2, 8); `_Output.warnings` (Task 1). Consistent.

---

## Follow-on

After M1 merges + ablates net-positive (Sonnet-5 subset + 4.6 control; keep only net-positive units), the roadmap's M2 (verified semantic Scent) is next. The re-derive/argumentation primitives here also feed the eventual product `run_agent_task` verification params (deferred).
