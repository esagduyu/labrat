# LLM-Semantic Scent (T1c) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn on the Cartographer's LLM "semantics" pass as a trustworthy, general capability — guarded by a freeze-time contamination audit — and wire it into the DAB driver with an independent authoring model, so the leaderboard run can consume semantics-enriched Scent.

**Architecture:** The engine already exists (`generate_scent(with_semantics, llm_fn)` + `draft_semantics` + the idempotent `cartograph_prepass` freeze-cache + a general CLI). This plan adds (1) a shared freeze-time contamination audit guard, (2) the audit wired into `generate_scent` (fail-loud), (3) DAB wiring that builds an independent authoring `llm_fn` (Max-plan-routed) and threads `with_semantics` through `_run_cartographer`, plus an optional persistent scent dir, and (4) the `eval_dab.py` flags. DAB already authors-once-per-dataset and shares it across trials via the suite's single `_scent_cache_root` tmpdir, so no per-trial copy logic is needed.

**Tech Stack:** Python 3.12, Pydantic v2, pytest (`asyncio_mode = "auto"`). Reuses `maze/cartographer.py`, `maze/document.py`, `agent/verifier.py::provider_llm_fn`, `agent/providers::build_provider`.

## Global Constraints

- Branch: `feat/llm-semantic-scent` (spec committed there: `docs/superpowers/specs/2026-06-25-llm-semantic-scent-design.md`).
- `from __future__ import annotations` at the top of every new/edited `.py`.
- Pyright **strict** on all of `src/labrat/` — no Unknown leaks (`json.loads` → cast/`# type: ignore[arg-type]`).
- **One contamination pattern list:** the detector moves to a shared module; the DAB suite imports it. Never fork the pattern tuple.
- **GT-firewall by construction:** the authoring LLM receives only `render_document(skeleton)` (structure + sampled rows). The freeze-time audit is the backstop; on a hit it must **raise** (`ScentContaminationError`) so nothing tainted is frozen.
- **Off path byte-identical:** with semantics disabled (`with_semantics=False` / `cartograph_semantics=False`), behavior is exactly today's — deterministic Scent, zero LLM calls (guarded by the existing `test_with_semantics_false_makes_zero_llm_calls`).
- **Independent authoring model:** the DAB semantics authoring model/provider are separate params from `--agent-model`/`--agent-provider`. Default authoring model `claude-sonnet-4-6`. On the `claude-mcp` driver the authoring provider is routed to `claude-code` (Max-plan OAuth; `ANTHROPIC_API_KEY` is stripped there) — mirroring `_verify_llm_fn`.
- Run Python via `uv run`. Full gate after every task: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- Tests must never depend on the gitignored `ecommerce.duckdb` directly — use the conftest `ecommerce_db` fixture.
- Commit messages end with these two trailer lines verbatim:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NJNie7NBnDUyd8Yvgpnq6f
  ```

---

### Task 1: Shared Scent contamination audit guard

**Files:**
- Create: `src/labrat/maze/scent_audit.py`
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` (replace the local `_CONTAMINATION_PATTERNS` tuple + `_detect_contamination` def, ~lines 95–122, with imports from the shared module)
- Test: `tests/unit/test_scent_audit.py`

**Interfaces:**
- Produces: `CONTAMINATION_PATTERNS: tuple[tuple[str, str], ...]`; `detect_contamination(text: str) -> str | None`; `audit_scent_doc(doc: ScentDoc) -> str | None`; `class ScentContaminationError(RuntimeError)`.
- Consumes: `ScentDoc`, `render_document` from `labrat.maze.document`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_scent_audit.py
"""Shared Scent contamination audit (FEATURE: LLM-semantic Scent / T1c)."""

from __future__ import annotations

from labrat.maze.document import ScentDoc, Section
from labrat.maze.scent_audit import audit_scent_doc, detect_contamination


def _doc(body: str) -> ScentDoc:
    return ScentDoc(
        domain="d",
        sections=[Section(heading="Gotchas", body=body, source="draft")],
    )


def test_clean_doc_passes() -> None:
    assert audit_scent_doc(_doc("Dates come in 3 mixed formats; filter with LIKE '%2018%'.")) is None


def test_answer_key_phrase_flagged() -> None:
    assert audit_scent_doc(_doc("the ground truth answer is 2020")) == "answer_key"


def test_external_dataset_flagged() -> None:
    assert audit_scent_doc(_doc("pull labels via load_dataset from huggingface")) == "external_dataset"


def test_detect_contamination_text() -> None:
    assert detect_contamination("matches the gold answer") == "answer_key"
    assert detect_contamination("clean analytical text") is None


def test_suite_detector_uses_shared_patterns() -> None:
    # the DAB suite must reuse the same detector (one pattern list)
    from labrat.eval.benchmarks.dab.suite import _detect_contamination

    assert _detect_contamination("read ground_truth.csv") == "answer_key"
    assert _detect_contamination("clean") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_scent_audit.py -q`
Expected: FAIL — `cannot import name 'audit_scent_doc'` (module doesn't exist).

- [ ] **Step 3: Create the shared module**

```python
# src/labrat/maze/scent_audit.py
"""Contamination audit for Scent docs and benchmark trial output.

Single source of truth for the answer-key / gold-answer / external-dataset
substring patterns. Used at two seams:
  - generate_scent freeze-time: audit_scent_doc(doc) guards LLM-authored semantics
    before a doc is frozen/consumed (fail-loud via ScentContaminationError).
  - DAB trial scoring: detect_contamination(trial_text) withdraws leaked trials.
"""

from __future__ import annotations

from labrat.maze.document import ScentDoc, render_document

# Tags checked in order; answer-key access is the more severe signal. These mark
# answer-key leakage (validate.py / ground_truth.csv, or NL gold-answer assertions)
# or external labelled datasets (HuggingFace load_dataset). See DAB PR #54.
CONTAMINATION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("validate.py", "answer_key"),
    ("ground_truth", "answer_key"),
    ("ground truth", "answer_key"),
    ("ground-truth", "answer_key"),
    ("answer key", "answer_key"),
    ("gold answer", "answer_key"),
    ("load_dataset", "external_dataset"),
    ("huggingface", "external_dataset"),
    ("fancyzhx/ag_news", "external_dataset"),
)


class ScentContaminationError(RuntimeError):
    """Raised when an authored Scent doc contains answer-shaped content."""


def detect_contamination(text: str) -> str | None:
    """Return a contamination tag ('answer_key' / 'external_dataset') if the text
    shows answer-key or external-label leakage; otherwise None. Case-insensitive."""
    low = text.lower()
    for needle, tag in CONTAMINATION_PATTERNS:
        if needle in low:
            return tag
    return None


def audit_scent_doc(doc: ScentDoc) -> str | None:
    """Return a contamination tag if a rendered Scent doc contains answer-shaped
    content; otherwise None. Run before freezing/consuming an LLM-authored doc."""
    return detect_contamination(render_document(doc))
```

- [ ] **Step 4: Refactor the DAB suite to import the shared detector**

In `src/labrat/eval/benchmarks/dab/suite.py`, delete the local `_CONTAMINATION_PATTERNS` tuple and the `_detect_contamination` function (the block at ~lines 95–122, including its comment), and add to the imports near the other `labrat.maze` import:

```python
from labrat.maze.scent_audit import (
    CONTAMINATION_PATTERNS as _CONTAMINATION_PATTERNS,
    detect_contamination as _detect_contamination,
)
```
(The `as` aliases preserve both existing names so the rest of `suite.py` and existing tests are untouched.)

- [ ] **Step 5: Run tests to verify they pass (incl. no DAB regression)**

Run: `uv run pytest tests/unit/test_scent_audit.py tests/unit/test_dab_infra_patterns.py -q` and any existing contamination test: `uv run pytest -k contamination -q`
Expected: PASS — shared module works; the DAB suite still classifies contamination identically.

- [ ] **Step 6: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add src/labrat/maze/scent_audit.py src/labrat/eval/benchmarks/dab/suite.py tests/unit/test_scent_audit.py
git commit -m "feat(scent): shared contamination audit guard (audit_scent_doc + detect_contamination)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NJNie7NBnDUyd8Yvgpnq6f"
```

---

### Task 2: Wire the audit into `generate_scent` (fail-loud at freeze)

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (the `with_semantics` block in `generate_scent`, ~lines 254–257; imports)
- Test: `tests/unit/test_cartographer_audit.py`

**Interfaces:**
- Consumes: `audit_scent_doc`, `ScentContaminationError` (Task 1).
- Produces: `generate_scent` raises `ScentContaminationError` when an authored doc fails the audit; unchanged otherwise.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartographer_audit.py
"""generate_scent freeze-time contamination audit (FEATURE: LLM-semantic Scent / T1c)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import generate_scent
from labrat.maze.scent_audit import ScentContaminationError


def _conns(db: Path) -> tuple[dict[str, object], dict[str, object]]:
    conn = DuckDBConnection(db, read_only=True)
    conn.connect()
    return {"shop": conn}, {"shop": conn.introspect_catalog()}


async def test_audit_raises_on_answer_shaped_semantics(ecommerce_db: Path) -> None:
    connections, catalogs = _conns(ecommerce_db)

    async def _leaky(prompt: str) -> str:
        return "## Gotchas\n- The ground truth answer for revenue is 12345."

    try:
        with pytest.raises(ScentContaminationError):
            await generate_scent(
                connections=connections,
                catalogs=catalogs,
                primary="shop",
                with_semantics=True,
                llm_fn=_leaky,
            )
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]


async def test_audit_passes_clean_semantics(ecommerce_db: Path) -> None:
    connections, catalogs = _conns(ecommerce_db)

    async def _clean(prompt: str) -> str:
        return "## Gotchas\n- Exclude is_test rows from revenue metrics."

    try:
        docs = await generate_scent(
            connections=connections,
            catalogs=catalogs,
            primary="shop",
            with_semantics=True,
            llm_fn=_clean,
        )
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]
    assert any(s.heading == "Gotchas" and s.source == "draft" for s in docs[0].sections)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_audit.py -q`
Expected: FAIL — `test_audit_raises_on_answer_shaped_semantics` fails (no audit yet, no exception raised).

- [ ] **Step 3: Add the audit to `generate_scent`**

In `src/labrat/maze/cartographer.py`, add the import near the other `labrat.maze` import (line 26):
```python
from labrat.maze.scent_audit import ScentContaminationError, audit_scent_doc
```
Replace the `with_semantics` block (currently):
```python
        if with_semantics and llm_fn is not None:
            drafted = await draft_semantics(doc, llm_fn)
            doc = doc.model_copy(update={"sections": merge_sections(doc.sections, drafted)})
        docs.append(doc)
```
with:
```python
        if with_semantics and llm_fn is not None:
            drafted = await draft_semantics(doc, llm_fn)
            doc = doc.model_copy(update={"sections": merge_sections(doc.sections, drafted)})
            tag = audit_scent_doc(doc)
            if tag is not None:
                raise ScentContaminationError(
                    f"Scent doc for {name!r} failed contamination audit ({tag}); "
                    "refusing to freeze LLM-authored semantics."
                )
        docs.append(doc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_audit.py tests/unit/test_cartographer_generate.py -q`
Expected: PASS (audit raises on the leaky stub; clean semantics still append; the existing generate tests are unaffected).

- [ ] **Step 5: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_audit.py
git commit -m "feat(scent): fail-loud contamination audit in generate_scent (freeze-time guard)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NJNie7NBnDUyd8Yvgpnq6f"
```

---

### Task 3: DAB authoring `llm_fn` + suite params + `_run_cartographer` threading

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` (`_run_cartographer` signature ~line 366; `DabSuite.__init__` ~line 404; `_scent_cache_root` init ~line 441; the two `_run_cartographer` call sites ~lines 856 and 1040; add `_cartograph_llm_fn`)
- Test: `tests/unit/test_dab_semantic_scent.py`

**Interfaces:**
- Consumes: `provider_llm_fn` (`labrat.agent.verifier`), `build_provider` (`labrat.agent.providers`), `cartograph_prepass` (already imported).
- Produces: `DabSuite.__init__` gains `cartograph_semantics: bool = False`, `cartograph_semantics_model: str = "claude-sonnet-4-6"`, `cartograph_semantics_provider: str = "anthropic"`, `cartograph_scent_root: Path | None = None`. New method `DabSuite._cartograph_llm_fn(self) -> LLMFn`. `_run_cartographer(env_spec, dataset, cache_root, *, with_semantics: bool = False, llm_fn: LLMFn | None = None) -> Path`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_dab_semantic_scent.py
"""DAB LLM-semantic Scent wiring (FEATURE: T1c)."""

from __future__ import annotations

from pathlib import Path

import labrat.eval.benchmarks.dab.suite as suite_mod
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.eval.benchmarks.dab.env import DabTaskEnv
from labrat.eval.benchmarks.dab.suite import DabSuite, _run_cartographer


def _env(ecommerce_db: Path) -> DabTaskEnv:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    from labrat.agent.tools.base import ToolContext

    return DabTaskEnv(
        ctx=ToolContext(
            connections={"shop": conn},
            catalogs={"shop": Catalog(database_name="shop", schemas=[])},
            primary="shop",
        )
    )


async def test_run_cartographer_threads_semantics(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    async def _cap(connections, catalogs, primary, scent_dir, *, with_semantics=False, llm_fn=None, **kw):
        captured["with_semantics"] = with_semantics
        captured["has_llm"] = llm_fn is not None
        return []

    monkeypatch.setattr(suite_mod, "cartograph_prepass", _cap)

    async def _stub(prompt: str) -> str:
        return "## Gotchas\n- x"

    await _run_cartographer(_env(ecommerce_db), "ds", tmp_path, with_semantics=True, llm_fn=_stub)
    assert captured["with_semantics"] is True
    assert captured["has_llm"] is True


def test_cartograph_llm_fn_routes_claude_code_on_mcp(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def _fake_build(name, model, *a, **k):
        captured["name"] = name
        captured["model"] = model

        class _P: ...

        return _P()

    monkeypatch.setattr(suite_mod, "build_provider", _fake_build, raising=False)
    monkeypatch.setattr(suite_mod, "provider_llm_fn", lambda p: (lambda x: x), raising=False)

    suite = DabSuite(
        driver="claude-mcp",
        cartograph=True,
        cartograph_semantics=True,
        cartograph_semantics_model="claude-sonnet-4-6",
    )
    suite._cartograph_llm_fn()
    assert captured["name"] == "claude-code"  # Max-plan auth on the claude-mcp path
    assert captured["model"] == "claude-sonnet-4-6"


def test_cartograph_llm_fn_honors_provider_off_mcp(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def _fake_build(name, model, *a, **k):
        captured["name"] = name

        class _P: ...

        return _P()

    monkeypatch.setattr(suite_mod, "build_provider", _fake_build, raising=False)
    monkeypatch.setattr(suite_mod, "provider_llm_fn", lambda p: (lambda x: x), raising=False)

    suite = DabSuite(
        driver="labrat-agent",
        cartograph=True,
        cartograph_semantics=True,
        cartograph_semantics_provider="anthropic",
    )
    suite._cartograph_llm_fn()
    assert captured["name"] == "anthropic"  # non-mcp path honors the configured provider
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_semantic_scent.py -q`
Expected: FAIL — `_run_cartographer` has no `with_semantics`/`llm_fn` kwargs; `DabSuite.__init__` rejects `cartograph_semantics`; no `_cartograph_llm_fn`.

- [ ] **Step 3: Thread params + add `_cartograph_llm_fn`**

(a) `_run_cartographer` (~line 366): add keyword-only params and pass them through:
```python
async def _run_cartographer(
    env_spec: DabTaskEnv,
    dataset: str,
    cache_root: Path,
    *,
    with_semantics: bool = False,
    llm_fn: LLMFn | None = None,
) -> Path:
```
and update the `cartograph_prepass(...)` call (~line 384) to:
```python
        await cartograph_prepass(
            ctx.connections,
            ctx.catalogs,
            ctx.primary,
            scent_dir,
            with_semantics=with_semantics,
            llm_fn=llm_fn,
        )
```
Add the import if `LLMFn` isn't already imported in suite.py (it is used by `_verify_llm_fn`; confirm — if absent, add `from labrat.agent.verifier import LLMFn`).

(b) `DabSuite.__init__` (~line 404): add the params after `cartograph: bool = False`:
```python
        cartograph: bool = False,
        cartograph_semantics: bool = False,
        cartograph_semantics_model: str = "claude-sonnet-4-6",
        cartograph_semantics_provider: str = "anthropic",
        cartograph_scent_root: Path | None = None,
```
and store them next to `self._cartograph = cartograph`:
```python
        self._cartograph = cartograph
        self._cartograph_semantics = cartograph_semantics
        self._cartograph_semantics_model = cartograph_semantics_model
        self._cartograph_semantics_provider = cartograph_semantics_provider
```
Then update the `_scent_cache_root` init (~line 441) so an explicit persistent dir overrides the tmpdir (enables freeze-and-commit for submissions; default unchanged):
```python
        self._scent_cache_root = (
            cartograph_scent_root
            if cartograph_scent_root is not None
            else (Path(tempfile.mkdtemp(prefix="labrat-dab-scent-")) if cartograph else Path())
        )
```

(c) Add `_cartograph_llm_fn` near `_verify_llm_fn`:
```python
    def _cartograph_llm_fn(self) -> LLMFn:
        # Author the semantics pass with the independent semantics model. Route to the
        # claude-code provider on the claude-mcp path (Max-plan OAuth; ANTHROPIC_API_KEY
        # is stripped there), mirroring _verify_llm_fn; honor the configured provider
        # on the other drivers.
        provider = (
            "claude-code"
            if self._driver == "claude-mcp"
            else self._cartograph_semantics_provider
        )
        return provider_llm_fn(build_provider(provider, self._cartograph_semantics_model))
```
(`build_provider` / `provider_llm_fn` are already imported by `_verify_llm_fn`; reuse the same import — if it is a local import inside `_verify_llm_fn`, add the same local import inside `_cartograph_llm_fn`.)

(d) Update the two `_run_cartographer` call sites to pass semantics. At ~line 856 (`maze_root = await _run_cartographer(env_spec, dataset, self._scent_cache_root)`) and ~line 1040 (`cartograph_root = await _run_cartographer(_build_fresh_env(db_config_path), dataset, self._scent_cache_root)`), pass:
```python
            ...,
            with_semantics=self._cartograph_semantics,
            llm_fn=self._cartograph_llm_fn() if self._cartograph_semantics else None,
        )
```

- [ ] **Step 4: Run tests to verify they pass (incl. no DAB regression)**

Run: `uv run pytest tests/unit/test_dab_semantic_scent.py tests/unit/test_dab_cartographer.py tests/unit/test_dab_suite_run_trial.py -q`
Expected: PASS (new wiring works; existing cartographer/run-trial behavior unchanged — off path passes `with_semantics=False`, `llm_fn=None`).

- [ ] **Step 5: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add src/labrat/eval/benchmarks/dab/suite.py tests/unit/test_dab_semantic_scent.py
git commit -m "feat(dab): semantic-Scent authoring llm_fn (Max-plan-routed) + suite params

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NJNie7NBnDUyd8Yvgpnq6f"
```

---

### Task 4: `eval_dab.py` flags `--cartograph-semantics` / `--cartograph-semantics-model` / `--cartograph-semantics-provider` / `--cartograph-scent-dir`

**Files:**
- Modify: `scripts/eval_dab.py`
- Test: `tests/unit/test_dab_semantic_scent.py` (extend — flag plumbing)

**Interfaces:**
- Consumes: suite params `cartograph_semantics` / `cartograph_semantics_model` / `cartograph_semantics_provider` / `cartograph_scent_root` (Task 3).
- Produces: four CLI flags threaded into `DabSuite(...)`, mirroring `--agent-cartograph`'s 4-site plumbing (arg → resume-conflict guard → `effective_*` with config fallback → `DabSuite(...)` kwarg + `config.json`).

- [ ] **Step 1: Write the failing test (extend test_dab_semantic_scent.py)**

```python
def test_eval_dab_threads_semantic_flags(monkeypatch, tmp_path) -> None:
    import scripts.eval_dab as ed

    captured: dict[str, object] = {}

    class _FakeSuite:
        name = "dab"

        def __init__(self, **kw):
            captured.update(kw)

        def tasks(self):
            return []

        def write_submission(self, report, output_dir):
            pass

    async def _fake_interim(suite, n_trials, output_dir, task_filter):
        from labrat.eval.types import BenchmarkReport

        return BenchmarkReport(benchmark="dab", results=[])

    monkeypatch.setattr(ed, "DabSuite", _FakeSuite)
    monkeypatch.setattr(ed, "_run_interim", _fake_interim)
    ed.main(
        [
            "--driver", "claude-mcp",
            "--agent-cartograph",
            "--cartograph-semantics",
            "--cartograph-semantics-model", "claude-sonnet-4-6",
            "--cartograph-semantics-provider", "anthropic",
            "--datasets", "deps_dev_v1",
            "--output-dir", str(tmp_path / "r"),
        ]
    )
    assert captured.get("cartograph_semantics") is True
    assert captured.get("cartograph_semantics_model") == "claude-sonnet-4-6"
    assert captured.get("cartograph_semantics_provider") == "anthropic"
```
(If `main`'s wiring differs from the verification-flags test in this same file, mirror that test's structure; the binding assertion is that `cartograph_semantics is True` + the model/provider strings reach `DabSuite(...)`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_semantic_scent.py -k semantic_flags -q`
Expected: FAIL — unrecognized arguments / kwargs absent.

- [ ] **Step 3: Add the flags (mirror `--agent-cartograph` at all sites)**

In `scripts/eval_dab.py`, add the arguments next to `--agent-cartograph`:
```python
    parser.add_argument(
        "--cartograph-semantics",
        action="store_true",
        default=None,
        help="Enable the LLM semantics pass for the cartographer pre-pass (requires "
        "--agent-cartograph). Author-once, audited, frozen. Off by default.",
    )
    parser.add_argument(
        "--cartograph-semantics-model",
        default="claude-sonnet-4-6",
        help="Model used to author the semantics pass (independent of --agent-model).",
    )
    parser.add_argument(
        "--cartograph-semantics-provider",
        choices=list(PROVIDER_NAMES),
        default="anthropic",
        help="Provider for the semantics authoring model. Auto-routed to claude-code on "
        "the claude-mcp driver (Max-plan auth); honored as-is on other drivers.",
    )
    parser.add_argument(
        "--cartograph-scent-dir",
        type=Path,
        default=None,
        help="Persistent dir for authored Scent docs (enables freeze-and-commit for a "
        "submission). Default: a per-run temp dir.",
    )
```
Import `PROVIDER_NAMES` if not already imported: `from labrat.agent.providers import PROVIDER_NAMES`.

Resume-conflict guard loop: add `("cartograph_semantics", args.cartograph_semantics)` (mirror the `agent_cartograph` entry).

Effective resolution (next to `effective_cartograph`):
```python
    effective_cartograph_semantics: bool = bool(
        args.cartograph_semantics
        if args.cartograph_semantics is not None
        else existing_cfg.get("cartograph_semantics", False)
    )
    effective_cartograph_semantics_model: str = (
        args.cartograph_semantics_model
        or existing_cfg.get("cartograph_semantics_model", "claude-sonnet-4-6")
    )
    effective_cartograph_semantics_provider: str = (
        args.cartograph_semantics_provider
        or existing_cfg.get("cartograph_semantics_provider", "anthropic")
    )
```
`DabSuite(...)` kwargs: add
```python
        cartograph_semantics=effective_cartograph_semantics,
        cartograph_semantics_model=effective_cartograph_semantics_model,
        cartograph_semantics_provider=effective_cartograph_semantics_provider,
        cartograph_scent_root=args.cartograph_scent_dir,
```
`config.json` dict: add `"cartograph_semantics"`, `"cartograph_semantics_model"`, `"cartograph_semantics_provider"` with the `effective_*` values.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_dab_semantic_scent.py -q`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add scripts/eval_dab.py tests/unit/test_dab_semantic_scent.py
git commit -m "feat(dab): --cartograph-semantics flags (independent authoring model + persistent scent dir)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NJNie7NBnDUyd8Yvgpnq6f"
```

---

## After the build: validation + ablation (controller, not a task)

1. **End-to-end semantics smoke (manual, LLM-gated).** Author semantics on the `ecommerce` fixture via the general CLI to eyeball quality:
   ```bash
   uv run python scripts/cartograph.py --connections '{"shop":{"db_type":"duckdb","db_path":"<ecommerce.duckdb>"}}' \
       --with-semantics --provider anthropic --model claude-sonnet-4-6 --out /tmp/scent-smoke
   ```
   Confirm a `## Gotchas` / `## Best Practices` section appears, reads usefully (dirty-data/format-quirk style), and the run did not raise the audit. If output is weak/generic, lightly tune `_SEMANTICS_INSTRUCTION` toward the dirty-data/format-quirk class (stockindex mixed-date example as the canonical target) and re-eval.

2. **DAB ablation** (after the Sonnet weekly limit resets; structure-only vs semantics-enriched), tuning subset, n=3, claude-mcp/Sonnet, on top of `--hints`:
   ```
   --agent-cartograph --hints                          # structure-only (baseline)
   --agent-cartograph --hints --cartograph-semantics   # semantics-enriched
   ```
   Keep `--cartograph-semantics` for the full run only if net-positive (target ≈ Altimate's +8pp). Report per-dataset deltas; watch stockindex noise. (Reuse the verification-ablation harness/waiter pattern; author with the claude-code-routed Sonnet authoring model.)

3. **Submission reproducibility (only if kept):** author once into `--cartograph-scent-dir dab_scent/`, force-commit the frozen docs (gitignored by default), declare the grounding docs publicly auditable.

## Self-Review

**1. Spec coverage:** §5.1 shared audit guard → Task 1 ✅. §5.1 wire-into-authoring (fail-loud) → Task 2 ✅. §5.2 DAB author-once-freeze-consume + independent authoring flags + Max-plan routing + persistent/commit dir → Tasks 3+4 ✅ (author-once-per-run is the existing shared `_scent_cache_root`; persistent dir via `cartograph_scent_root`). §5.3 ablation → controller section ✅. §5.4 audit/merge tests + e2e smoke → Tasks 1–2 tests + controller smoke ✅. §3.1 general-first: the CLI path is pre-existing + Task 1's audit now guards it; DAB is one consumer ✅. §3.4 independent Sonnet-default authoring model → Task 3/4 ✅. §8 open questions: authoring provider on Max-plan → resolved (claude-code routing, Task 3); frozen-doc location/commit → resolved (`cartograph_scent_root`, Tasks 3/4) ✅. Non-goals (TUI T2c, staleness refresh, lineage) → untouched ✅.

**2. Placeholder scan:** No TBD/TODO. All code steps show full code. The one "mirror the verification-flags test if main's wiring differs" note points at a concrete sibling test in the same file, not a placeholder; the binding assertions are spelled out.

**3. Type consistency:** `detect_contamination(text:str)->str|None` and `audit_scent_doc(doc:ScentDoc)->str|None` consistent Tasks 1→2. `ScentContaminationError` defined Task 1, raised Task 2. `_run_cartographer(..., *, with_semantics:bool=False, llm_fn:LLMFn|None=None)->Path` consistent Task 3 def + call sites. `DabSuite` params `cartograph_semantics:bool`, `cartograph_semantics_model:str`, `cartograph_semantics_provider:str`, `cartograph_scent_root:Path|None` defined Task 3, plumbed Task 4. `_cartograph_llm_fn(self)->LLMFn` defined + used Task 3.
