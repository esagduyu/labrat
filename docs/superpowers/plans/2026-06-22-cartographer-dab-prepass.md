# Cartographer DAB pre-pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run #26b's deterministic cartographer as a GT-firewalled first-contact pre-pass on each DAB dataset's primary DB, so the agent consults generated structure-only Scent (`search_reference_docs`) during Q&A — behind an ablation flag.

**Architecture:** A reusable `cartograph_prepass(...)` (lazy first-contact cache over `generate_scent`) in `maze/cartographer.py`. A DAB helper `_autocontext_prepass(env_spec, dataset, cache_root)` connects the primary, introspects, runs the pre-pass into a per-dataset store, and returns the maze root. Both DAB drivers, gated by an `autocontext` flag, call it before the agent and point the agent at the store via `LABRAT_MAZE_DIR` (with a hermetic `HOME` so the user Scent layer can't leak in) plus a one-line "consult `search_reference_docs` first" prompt addition.

**Tech Stack:** Python 3.12, Pydantic v2, DuckDB, pytest (`asyncio_mode = "auto"`), the `ecommerce_db` conftest fixture.

## Global Constraints

- Branch: `feat/cartographer-dab-prepass` (already created; the spec is committed there).
- Spec: `docs/superpowers/specs/2026-06-22-cartographer-dab-prepass-design.md`.
- `from __future__ import annotations` at the top of every new/edited `.py` file.
- Pyright **strict** on all of `src/labrat/` — no Unknown leaks.
- **Deterministic-only on DAB:** the DAB pre-pass calls `cartograph_prepass(..., with_semantics=False, llm_fn=None)` — **zero LLM calls**. (Asserted by test.)
- **GT-firewalled by construction:** the pre-pass operates only on DB `Connection` objects + writes into its `scent_dir`; it never receives or opens benchmark answer-key paths (`validate.py`/`ground_truth.csv`).
- **Per-dataset store isolation** + **hermetic HOME** for the agent/MCP subprocess (the user Scent layer must not contribute on DAB).
- **Ablation flag:** the pre-pass is OFF by default; enabled via `--agent-autocontext` (suite `autocontext=True`). With the flag off, behavior is byte-identical to today (no store, no env var, no prompt line).
- Tests use the **`ecommerce_db`** conftest fixture (never the gitignored `tests/fixtures/sample_dbs/ecommerce.duckdb`).
- Full gate after every task: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- Every commit message ends with these two trailer lines verbatim:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj
  ```
- Run Python via `uv run python` / `uv run pytest`.

---

### Task 1: `cartograph_prepass` — the reusable first-contact seam

**Files:**
- Modify: `src/labrat/maze/cartographer.py`
- Test: `tests/unit/test_cartograph_prepass.py`

**Interfaces:**
- Consumes: `generate_scent`, `write_docs` (Task-existing in cartographer.py); `LLMFn`.
- Produces: `async cartograph_prepass(connections, catalogs, primary, scent_dir: Path, *, with_semantics=False, llm_fn=None, table_budget=40, distinct_cap=25) -> list[Path]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartograph_prepass.py
"""First-contact Scent pre-pass (FEATURE: cartographer DAB pre-pass)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import cartograph_prepass


def _conn(ecommerce_db: Path) -> tuple[dict[str, object], dict[str, object]]:
    conn = DuckDBConnection(ecommerce_db, read_only=True)
    conn.connect()
    return {"shop": conn}, {"shop": conn.introspect_catalog()}


async def test_prepass_generates_then_caches(ecommerce_db: Path, tmp_path: Path) -> None:
    connections, catalogs = _conn(ecommerce_db)
    scent_dir = tmp_path / "labrat_maze" / "scent"
    try:
        paths = await cartograph_prepass(connections, catalogs, "shop", scent_dir)
        assert paths and all(p.exists() for p in paths)
        # mark a doc, then re-run: first-contact cache must NOT regenerate (mark survives)
        paths[0].write_text(paths[0].read_text(encoding="utf-8") + "\nSENTINEL", encoding="utf-8")
        again = await cartograph_prepass(connections, catalogs, "shop", scent_dir)
        assert again == paths
        assert "SENTINEL" in paths[0].read_text(encoding="utf-8")  # reused, not regenerated
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]


async def test_prepass_deterministic_makes_zero_llm_calls(ecommerce_db: Path, tmp_path: Path) -> None:
    connections, catalogs = _conn(ecommerce_db)
    calls = {"n": 0}

    async def _spy(prompt: str) -> str:
        calls["n"] += 1
        return "## Gotchas\n- x"

    try:
        await cartograph_prepass(
            connections, catalogs, "shop", tmp_path / "labrat_maze" / "scent",
            with_semantics=False, llm_fn=_spy,
        )
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]
    assert calls["n"] == 0  # deterministic pre-pass never calls the model


async def test_prepass_output_is_retrievable(
    ecommerce_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections, catalogs = _conn(ecommerce_db)
    try:
        await cartograph_prepass(connections, catalogs, "shop", tmp_path / "labrat_maze" / "scent")
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="how do I join orders to customers?"),
    )
    assert any(r.domain == "shop" for r in out.results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartograph_prepass.py -q`
Expected: FAIL — `cannot import name 'cartograph_prepass'`.

- [ ] **Step 3: Add `cartograph_prepass` to `src/labrat/maze/cartographer.py`**

Add `from pathlib import Path` to the imports if not present (Task 6 of #26b already imported it for `write_docs`). Append:

```python
async def cartograph_prepass(
    connections: dict[str, object],
    catalogs: dict[str, object],
    primary: str,
    scent_dir: Path,
    *,
    with_semantics: bool = False,
    llm_fn: LLMFn | None = None,
    table_budget: int = 40,
    distinct_cap: int = 25,
) -> list[Path]:
    """First-contact Scent pre-pass: if ``scent_dir`` already holds docs, reuse them
    (idempotent first-contact cache); otherwise generate_scent(...) and write them.

    Deterministic by default (``with_semantics=False`` → no LLM). The reusable seam:
    DAB calls this deterministic-only; the agent's first-connect path will later call it
    with semantics + the dual store. Caller owns ``scent_dir`` isolation.
    """
    existing = sorted(scent_dir.glob("*.md")) if scent_dir.exists() else []
    if existing:
        return existing
    docs = await generate_scent(
        connections=connections,
        catalogs=catalogs,
        primary=primary,
        with_semantics=with_semantics,
        llm_fn=llm_fn,
        table_budget=table_budget,
        distinct_cap=distinct_cap,
    )
    return write_docs(docs, scent_dir)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cartograph_prepass.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartograph_prepass.py
git commit -m "feat(maze): cartograph_prepass — first-contact Scent cache seam

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 2: DAB `_autocontext_prepass` helper + `autocontext` flag + CLI

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py`
- Modify: `scripts/eval_dab.py`
- Test: `tests/unit/test_dab_autocontext.py`

**Interfaces:**
- Consumes: `cartograph_prepass` (Task 1); `build_dab_task_env` / `introspect_env_catalogs` / `DabTaskEnv` (existing in `dab/env.py`).
- Produces:
  - module-level `async _autocontext_prepass(env_spec: DabTaskEnv, dataset: str, cache_root: Path) -> Path` in `suite.py` — connects the primary, introspects, runs the deterministic pre-pass into `<cache_root>/<dataset>/labrat_maze/scent`, disconnects, and returns the **maze root** `<cache_root>/<dataset>` (to set as `LABRAT_MAZE_DIR`).
  - suite `__init__` gains `autocontext: bool = False`; the suite holds `self._autocontext` and `self._scent_cache_root` (a per-run temp dir).
  - `eval_dab.py` gains `--agent-autocontext` (store_true) wired into the suite.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_dab_autocontext.py
"""DAB AutoContext pre-pass helper (FEATURE: cartographer DAB pre-pass)."""

from __future__ import annotations

from pathlib import Path

from labrat.agent.tools.base import ToolContext
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.eval.benchmarks.dab.env import DabTaskEnv
from labrat.eval.benchmarks.dab.suite import _autocontext_prepass


def _env(ecommerce_db: Path) -> DabTaskEnv:
    # primary connection is NOT connected (mirrors build_dab_task_env)
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    ctx = ToolContext(
        connections={"shop": conn},
        catalogs={"shop": Catalog(database_name="shop", schemas=[])},
        primary="shop",
    )
    return DabTaskEnv(ctx=ctx, attachable=[], mongo=[])


async def test_autocontext_populates_per_dataset_store(ecommerce_db: Path, tmp_path: Path) -> None:
    maze_root = await _autocontext_prepass(_env(ecommerce_db), "stockindex", tmp_path)
    assert maze_root == tmp_path / "stockindex"
    docs = list((maze_root / "labrat_maze" / "scent").glob("*.md"))
    assert docs, "pre-pass should have written at least one Scent doc"


async def test_autocontext_isolates_datasets(ecommerce_db: Path, tmp_path: Path) -> None:
    a = await _autocontext_prepass(_env(ecommerce_db), "ds_a", tmp_path)
    b = await _autocontext_prepass(_env(ecommerce_db), "ds_b", tmp_path)
    assert a != b  # different datasets -> different maze roots (no collision)
    assert (a / "labrat_maze" / "scent").exists()
    assert (b / "labrat_maze" / "scent").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_autocontext.py -q`
Expected: FAIL — `cannot import name '_autocontext_prepass'`.

- [ ] **Step 3: Add the helper to `src/labrat/eval/benchmarks/dab/suite.py`**

Add imports near the top (module level):

```python
import re
import tempfile

from labrat.eval.benchmarks.dab.env import introspect_env_catalogs
from labrat.maze.cartographer import cartograph_prepass
```

(If `DabTaskEnv` / `build_dab_task_env` are imported lazily inside the driver methods today, keep those as-is; the helper takes `env_spec` so it needs only the type for annotation — import `DabTaskEnv` at module level for the signature.)

Add the helper (module level):

```python
def _safe_name(name: str) -> str:
    """Filesystem-safe per-dataset dir name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "dataset"


async def _autocontext_prepass(env_spec: DabTaskEnv, dataset: str, cache_root: Path) -> Path:
    """Run the deterministic, GT-firewalled cartographer on the task's primary DB and
    return the maze root to expose as LABRAT_MAZE_DIR.

    Per-dataset store under ``cache_root`` so datasets that share a connection key
    (e.g. 'main') never collide; idempotent (cartograph_prepass caches on first contact).
    Deterministic-only — never calls a model, never touches answer-key files.
    """
    maze_root = cache_root / _safe_name(dataset)
    scent_dir = maze_root / "labrat_maze" / "scent"

    ctx = env_spec.ctx
    for conn in ctx.connections.values():
        connect = getattr(conn, "connect", None)
        if callable(connect):
            connect()
    try:
        introspect_env_catalogs(ctx)
        await cartograph_prepass(
            ctx.connections, ctx.catalogs, ctx.primary, scent_dir, with_semantics=False
        )
    finally:
        for conn in ctx.connections.values():
            disconnect = getattr(conn, "disconnect", None)
            if callable(disconnect):
                disconnect()
    return maze_root
```

In the suite class `__init__`, add the parameter + state (place `autocontext: bool = False` among the existing keyword params, e.g. next to `hints`):

```python
        autocontext: bool = False,
```
and in the body:
```python
        self._autocontext = autocontext
        self._scent_cache_root = Path(tempfile.mkdtemp(prefix="labrat-dab-scent-"))
```

- [ ] **Step 4: Add the `--agent-autocontext` flag to `scripts/eval_dab.py`**

Add the argument (next to `--agent-verify`):

```python
    parser.add_argument(
        "--agent-autocontext",
        action="store_true",
        help=(
            "Run the deterministic cartographer pre-pass on each dataset's primary DB and "
            "let the agent consult the generated Scent via search_reference_docs "
            "(GT-firewalled AutoContext; off by default — for ablation)."
        ),
    )
```

Thread it into the suite construction wherever the suite is built (pass `autocontext=args.agent_autocontext`). If the suite is built via a config dict / resume config like the other agent flags, add `autocontext` alongside `agent_verify` in that config (mirror how `agent_verify` is plumbed), defaulting to `False` on resume.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_dab_autocontext.py -q`
Expected: PASS (2 tests).

- [ ] **Step 6: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 7: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py scripts/eval_dab.py tests/unit/test_dab_autocontext.py
git commit -m "feat(dab): AutoContext pre-pass helper + --agent-autocontext flag

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 3: Wire the pre-pass into both DAB drivers (gated by the flag)

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py`
- Test: `tests/unit/test_dab_autocontext.py` (extend)

**Interfaces:**
- Consumes: `_autocontext_prepass` (Task 2); `self._autocontext`, `self._scent_cache_root`.
- Produces: when `self._autocontext` is True, both drivers run the pre-pass, expose `LABRAT_MAZE_DIR` (+ hermetic `HOME`) to the agent, and add a `search_reference_docs`-first prompt line. When False, no behavior change.

- [ ] **Step 1: Write the failing test (prompt line gated by the flag)**

Append to `tests/unit/test_dab_autocontext.py`:

```python
from labrat.eval.benchmarks.dab.suite import _autocontext_prompt_line


def test_autocontext_prompt_line_mentions_search_reference_docs() -> None:
    line = _autocontext_prompt_line()
    assert "search_reference_docs" in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_autocontext.py::test_autocontext_prompt_line_mentions_search_reference_docs -q`
Expected: FAIL — `cannot import name '_autocontext_prompt_line'`.

- [ ] **Step 3: Add the prompt-line helper + wire both drivers**

Add the prompt-line helper (module level, so it's testable and shared):

```python
def _autocontext_prompt_line() -> str:
    return (
        "A curated reference doc for this database has been pre-generated. Call "
        "search_reference_docs(question) FIRST for grounding (table grain, verified join "
        "keys, observed dimension values) before profiling or writing SQL."
    )
```

**claude-mcp driver** (`_run_trial_claude_mcp`): right after `env_spec = build_dab_task_env(db_config_path)` and the `primary_conn` DuckDB check, before building `mcp_config`, add:

```python
        maze_root: Path | None = None
        if self._autocontext:
            dataset = task.id.split(":")[0]
            maze_root = await _autocontext_prepass(env_spec, dataset, self._scent_cache_root)
```

In the `mcp_config` server `"env"` dict (the one with `LABRAT_MCP_CONNECTIONS` / `LABRAT_MCP_PRIMARY` / `LABRAT_MCP_LOG_DIR`), append a conditional entry so the MCP server reads the store and the user Scent layer is hermetically empty:

```python
                        **(
                            {
                                "LABRAT_MAZE_DIR": str(maze_root),
                                # hermetic: the user Scent layer (~/.labrat) must not
                                # contribute on the benchmark — point HOME at an empty dir.
                                "HOME": str((maze_root / "_home")),
                            }
                            if maze_root is not None
                            else {}
                        ),
```

(Create the empty home dir when building it: add `if maze_root is not None: (maze_root / "_home").mkdir(parents=True, exist_ok=True)` right after the pre-pass call.)

Add the prompt line to `prompt_lines` when enabled — insert just before the existing `link_schema` guidance line:

```python
        if maze_root is not None:
            prompt_lines.append(_autocontext_prompt_line())
```

**labrat-agent driver** (`_run_trial_labrat_agent`): the in-process agent reads the store via `MazeStore.from_env` (process env). After `introspect_env_catalogs(env.ctx)` and before `run_agent_task`, gate on the flag and set the env around the call:

```python
        autocontext_root: Path | None = None
        if self._autocontext:
            dataset = task.id.split(":")[0]
            autocontext_root = await _autocontext_prepass(env, dataset, self._scent_cache_root)
```

Wrap the `run_agent_task(...)` call so `LABRAT_MAZE_DIR` (+ hermetic `HOME`) are set only for it (sequential trials, restored after):

```python
        import os

        saved = {k: os.environ.get(k) for k in ("LABRAT_MAZE_DIR", "HOME")}
        if autocontext_root is not None:
            (autocontext_root / "_home").mkdir(parents=True, exist_ok=True)
            os.environ["LABRAT_MAZE_DIR"] = str(autocontext_root)
            os.environ["HOME"] = str(autocontext_root / "_home")
        try:
            result = await run_agent_task(...)  # the existing call, unchanged
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
```

Also add the prompt line to `_build_labrat_agent_system_prompt`'s output when autocontext is on — simplest: append `_autocontext_prompt_line()` to the system prompt in the driver when `autocontext_root is not None` (string-concat after the call), e.g.:

```python
        system_prompt = _build_labrat_agent_system_prompt(env)
        if autocontext_root is not None:
            system_prompt = system_prompt + "\n" + _autocontext_prompt_line()
```

> Note: `env` in the labrat-agent path is the `DabTaskEnv` (its `.ctx` is already connected + introspected by the existing code). `_autocontext_prepass` connects/introspects/disconnects its own pass; calling it on the already-connected `env` is safe because `connect()`/`introspect` are idempotent here, but to avoid double-connect surprises, pass `env` (the same object) — the pre-pass's `finally` disconnect would close the connections the driver still needs. **Therefore, in the labrat-agent path, run the pre-pass on a FRESH `build_dab_task_env(db_config_path)` instance**, not the live `env`:

```python
        if self._autocontext:
            from labrat.eval.benchmarks.dab.env import build_dab_task_env
            dataset = task.id.split(":")[0]
            autocontext_root = await _autocontext_prepass(
                build_dab_task_env(db_config_path), dataset, self._scent_cache_root
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_dab_autocontext.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green (existing DAB suite tests unaffected — the flag defaults False → no behavior change).

- [ ] **Step 6: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py tests/unit/test_dab_autocontext.py
git commit -m "feat(dab): wire AutoContext pre-pass into both drivers (flag-gated, hermetic)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

## After the build: ablation (run by the controller, not a task)

Once merged, measure with vs without on the tuning subset (deterministic pre-pass, claude-mcp/Max-plan or codex):
```
# without
uv run python scripts/eval_dab.py --datasets deps_dev_v1,music_brainz_20k,stockindex --driver claude-mcp --n-trials 3 --output-dir runs/dab/ac-off
# with
uv run python scripts/eval_dab.py --datasets deps_dev_v1,music_brainz_20k,stockindex --driver claude-mcp --n-trials 3 --agent-autocontext --output-dir runs/dab/ac-on
```
Keep AutoContext for the full run **only if net-positive**; run the 9-task ADE smoke set as a regression check. Disclose AutoContext in the submission.

## Self-Review

**1. Spec coverage:**
- §3 reusable seam (`cartograph_prepass`, first-contact cache, deterministic default) → Task 1. ✅
- §4 DAB wiring (per-dataset store, `LABRAT_MAZE_DIR`, both drivers, prompt line) → Tasks 2 (helper+flag+CLI) + 3 (drivers+prompt). ✅
- §5 guardrails: deterministic-only (Task 1 test asserts zero LLM) ✅; GT-firewalled (helper takes connections + scent_dir only, never answer-key paths) ✅; sandbox unchanged (flag off = byte-identical; on = only adds env+prompt) ✅; hermetic HOME (Task 3) ✅; disclose + agnews caveat (ablation section / submission) ✅.
- §6 ablation (with/without flag) → the flag (Task 2) + the ablation section. ✅
- §7 testing (idempotency, deterministic zero-LLM, end-to-end retrieval, per-dataset isolation, prompt line) → Tasks 1-3. ✅
- §2 out-of-scope (general auto-trigger, drift, LLM-on-DAB) → not built. ✅

**2. Placeholder scan:** No TBD/TODO/"similar to". The `run_agent_task(...)  # the existing call, unchanged` references the already-present call (not new code to write). Path placeholders (`<cache_root>/<dataset>`) are illustrative; the code uses real expressions.

**3. Type consistency:** `cartograph_prepass(connections, catalogs, primary, scent_dir, *, with_semantics=False, ...)` identical Task 1→2. `_autocontext_prepass(env_spec, dataset, cache_root) -> Path` (returns the maze root) identical Tasks 2→3. `_autocontext_prompt_line() -> str` Tasks 3. `self._autocontext` / `self._scent_cache_root` defined Task 2, used Task 3. The labrat-agent path runs the pre-pass on a **fresh** `build_dab_task_env` instance (not the live `env`) so the pre-pass's disconnect doesn't close the driver's live connections — called out explicitly in Task 3.
