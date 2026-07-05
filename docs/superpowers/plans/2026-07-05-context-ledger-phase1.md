# Context Ledger — Phase 1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-07-05-context-ledger-phase1-design.md`
**Branch:** `feat/context-ledger-phase1` (already checked out)

**Goal:** Bound what tool output enters `AgentLoop` model history — over-budget payloads are stored as addressable artifacts in a `ResultStore` and replaced by a mechanical summary + bounded preview + `artifact_ref`, opt-in on the loop and default-on in `run_agent_task`.

**Architecture:** Tool `DispatchResult` → `ContextLedger.record()` → `ModelVisibleToolResult` → `render()` → history. Under budget the rendered string is exactly today's `str(dispatch.value)`; over budget (rows OR bytes) the payload goes to a `ResultStore` (tables→Parquet+JSON meta, profiles→JSON, traces→JSONL) addressable by `result://<session>/<n>` refs. High-volume tools declare their large payload via an explicit typed hook (`ledger_payload()`); tools without the hook are bounded via a string fallback that never crashes.

**Tech Stack:** Python 3.12, Pydantic v2 (`PrivateAttr` for carrying DataFrames on tool outputs), Polars (`write_parquet`/`read_parquet`), pytest with `asyncio_mode="auto"`, ruff + pyright strict (all new `src/labrat/` code is strict-checked).

## Global Constraints

Copied from the spec — every task's requirements implicitly include these:

- **Bare `AgentLoop` with no ledger is BYTE-IDENTICAL to today.** "Ledger is opt-in on bare `AgentLoop` — absent → byte-identical to today (non-negotiable safety; protects existing paths + tests)." The `ledger=None` path is the existing code UNCHANGED: `output_str = str(dispatch.value) if dispatch.ok else f"Error: {dispatch.error}"`.
- **Mechanical summaries, no LLM.** "Summaries are row counts / column names / truncation notes, not model-generated. Cheap, reproducible, benchmark-safe." No LLM call anywhere in `ResultStore` or `ContextLedger`.
- **Ledger opt-in, default-on only in `run_agent_task`.** "Product paths (`run_agent_task`) default the ledger ON with an `enable_ledger=False` toggle."
- **`on_tool_call` still receives the FULL payload** (today's `str(dispatch.value)`) — "traces/audit stay complete — model-visible bounding affects history only." NOT the bounded string.
- **NOT a claude-mcp leaderboard lever.** "Explicitly NOT a claude-mcp DAB-score lever — its DAB relevance is reducing `labrat-agent`/Codex token burn only." (The claude-mcp path bypasses `AgentLoop`.)
- **Retrofit is additive.** A non-retrofitted tool (no `ledger_payload`) still bounds via the string fallback (or passes through) — never crashes the ledger. Off-ledger, retrofitted tools keep returning today's string.
- **Budgets (pinned in this plan):** `LedgerBudget(max_rows=50, max_bytes=8000)`. 50 rows is enough to read value patterns/formats; 8000 bytes ≈ ~2000 tokens per tool result — history is resent on every provider call, so one 1000-row `run_sql` output (~50–100 KB) is paid for on every subsequent turn. `preview` is capped by BOTH limits.
- **Tool payload contract (pinned in this plan):** an explicit, runtime-checkable protocol method on the tool's output object — `def ledger_payload(self) -> tuple[Literal["table", "json", "trace"], object] | None` — returning the big payload + kind, or `None` (e.g. for error/refused outputs). The ledger NEVER sniffs value shapes; absent hook → stringify fallback reproducing today's `str(value)`.
- **Pre-commit gate (CI-enforced, run before EVERY commit, in this order):**
  `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
- Tool `name`, `description`, `input_model` must be `@property` methods, not class attributes. `json.loads()` results are `Unknown` under pyright strict — use `isinstance` narrowing + `cast(dict[str, Any], x)`.

**File map (locked decomposition):**

| File | Responsibility |
|---|---|
| `src/labrat/results/__init__.py` | NEW package marker |
| `src/labrat/results/store.py` | NEW — `ResultStore`, `cap_bytes`, `render_table_head` |
| `src/labrat/agent/tools/serialization.py` | NEW — `ModelVisibleToolResult`, `render`, `LedgerPayloadKind`, `LedgerPayloadProvider` |
| `src/labrat/runtime/__init__.py` | NEW package marker |
| `src/labrat/runtime/context_ledger.py` | NEW — `LedgerBudget`, `ContextLedger` |
| `src/labrat/agent/loop.py` | MODIFY — `ledger` param + seam wiring |
| `src/labrat/agent/tools/run_sql.py` | MODIFY — `_Output.ledger_payload` (table) |
| `src/labrat/agent/tools/sample_rows.py` | MODIFY — `_Output.ledger_payload` (table) |
| `src/labrat/agent/tools/profile_dataset.py` | MODIFY — `_Output.ledger_payload` (json) |
| `src/labrat/agent/tools/column_stats.py` | MODIFY — `_Output.ledger_payload` (json) |
| `src/labrat/agent/runner.py` | MODIFY — `enable_ledger` / `ledger_dir` |

No import cycles: `labrat.runtime.context_ledger` imports `labrat.agent.tools.{base,serialization}` + `labrat.results.store`; `labrat.agent.loop` imports `labrat.runtime.context_ledger`. All package `__init__.py` files are docstring-only, so nothing loops back into `loop.py`.

---

### Task 1: ResultStore — Parquet table artifacts + artifact_ref resolution

**Files:**
- Create: `src/labrat/results/__init__.py`
- Create: `src/labrat/results/store.py`
- Test: `tests/unit/test_result_store.py`

**Interfaces:**
- Consumes: nothing (leaf module; Polars + stdlib only).
- Produces (later tasks rely on these exact signatures):
  - `ResultStore.__init__(self, root: Path, *, session: str | None = None) -> None`
  - `ResultStore.put_table(self, df: pl.DataFrame, *, meta: dict[str, Any] | None = None) -> str` — returns an opaque `artifact_ref` of the form `result://<session>/<n:04d>`
  - `ResultStore.get(self, ref: str) -> object` — table refs → `pl.DataFrame`
  - `ResultStore.meta(self, ref: str) -> dict[str, Any] | None` — the JSON sidecar for table refs
  - `ResultStore.session: str` and `ResultStore.directory: Path` (read-only properties)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_result_store.py`:

```python
"""ResultStore: addressable on-disk artifacts (tables→Parquet+meta, json, traces)."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from labrat.results.store import ResultStore


@pytest.fixture()
def df() -> pl.DataFrame:
    return pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})


def test_put_table_roundtrips_dataframe(tmp_path: Path, df: pl.DataFrame) -> None:
    store = ResultStore(tmp_path)
    ref = store.put_table(df)
    assert ref.startswith("result://")
    out = store.get(ref)
    assert isinstance(out, pl.DataFrame)
    assert out.equals(df)


def test_put_table_writes_meta_sidecar(tmp_path: Path, df: pl.DataFrame) -> None:
    store = ResultStore(tmp_path)
    ref = store.put_table(df, meta={"tool": "run_sql"})
    meta = store.meta(ref)
    assert meta is not None
    assert meta["columns"] == ["id", "name"]
    assert meta["row_count"] == 3
    assert meta["tool"] == "run_sql"
    assert len(meta["dtypes"]) == 2


def test_refs_are_sequential_and_session_scoped(tmp_path: Path, df: pl.DataFrame) -> None:
    store = ResultStore(tmp_path, session="sess1")
    ref_a = store.put_table(df)
    ref_b = store.put_table(df)
    assert ref_a == "result://sess1/0000"
    assert ref_b == "result://sess1/0001"
    assert store.session == "sess1"
    assert store.directory == tmp_path / "sess1"
    assert store.directory.is_dir()


def test_unknown_ref_raises_value_error(tmp_path: Path, df: pl.DataFrame) -> None:
    store = ResultStore(tmp_path, session="sess1")
    store.put_table(df)
    with pytest.raises(ValueError, match="unknown artifact_ref"):
        store.get("result://sess1/0099")
    with pytest.raises(ValueError, match="unknown artifact_ref"):
        store.get("result://other/0000")
    with pytest.raises(ValueError, match="unknown artifact_ref"):
        store.get("garbage")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_result_store.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'labrat.results'`

- [ ] **Step 3: Write minimal implementation**

Create `src/labrat/results/__init__.py`:

```python
"""Result artifacts: addressable storage for over-budget tool payloads."""
```

Create `src/labrat/results/store.py`:

```python
"""ResultStore: addressable on-disk store for over-budget tool payloads.

Artifacts are the provenance backbone ("Cheese"): tables → Parquet + a JSON
metadata sidecar. Every put returns an opaque ``artifact_ref``
("result://<session>/<n>") that ``get`` resolves back. Purely mechanical —
no LLM anywhere in this module.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import polars as pl


class ResultStore:
    """Per-session artifact directory under a caller-provided root."""

    def __init__(self, root: Path, *, session: str | None = None) -> None:
        self._session = session if session is not None else uuid.uuid4().hex[:8]
        self._dir = Path(root) / self._session
        self._dir.mkdir(parents=True, exist_ok=True)
        self._next_id = 0
        self._entries: dict[int, tuple[str, Path]] = {}  # n -> (kind, path)

    @property
    def session(self) -> str:
        return self._session

    @property
    def directory(self) -> Path:
        return self._dir

    # ── writers ───────────────────────────────────────────────────────────────

    def put_table(self, df: pl.DataFrame, *, meta: dict[str, Any] | None = None) -> str:
        """Store a DataFrame as Parquet + a JSON metadata sidecar; return its ref."""
        n = self._claim()
        path = self._dir / f"{n:04d}.table.parquet"
        df.write_parquet(path)
        sidecar: dict[str, Any] = {
            "columns": df.columns,
            "dtypes": [str(t) for t in df.dtypes],
            "row_count": df.height,
            **(meta or {}),
        }
        (self._dir / f"{n:04d}.table.meta.json").write_text(
            json.dumps(sidecar, default=str), encoding="utf-8"
        )
        self._entries[n] = ("table", path)
        return self._ref(n)

    # ── readers ───────────────────────────────────────────────────────────────

    def get(self, ref: str) -> object:
        """Resolve a ref back to its stored payload (table refs → pl.DataFrame)."""
        kind, path = self._resolve(ref)
        if kind == "table":
            return pl.read_parquet(path)
        raise ValueError(f"unknown artifact_ref: {ref!r}")

    def meta(self, ref: str) -> dict[str, Any] | None:
        """Return the JSON metadata sidecar for a table ref; None for other kinds."""
        kind, path = self._resolve(ref)
        if kind != "table":
            return None
        sidecar = path.with_name(path.stem + ".meta.json")
        data: object = json.loads(sidecar.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return cast(dict[str, Any], data)
        return None

    # ── internals ─────────────────────────────────────────────────────────────

    def _claim(self) -> int:
        n = self._next_id
        self._next_id += 1
        return n

    def _ref(self, n: int) -> str:
        return f"result://{self._session}/{n:04d}"

    def _resolve(self, ref: str) -> tuple[str, Path]:
        prefix = f"result://{self._session}/"
        if not ref.startswith(prefix):
            raise ValueError(f"unknown artifact_ref: {ref!r}")
        try:
            n = int(ref.removeprefix(prefix))
        except ValueError as exc:
            raise ValueError(f"unknown artifact_ref: {ref!r}") from exc
        if n not in self._entries:
            raise ValueError(f"unknown artifact_ref: {ref!r}")
        return self._entries[n]
```

(Note: `path.stem` of `0000.table.parquet` is `0000.table`, so the sidecar resolves to `0000.table.meta.json` — matching what `put_table` wrote.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_result_store.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Gate + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/results/__init__.py src/labrat/results/store.py tests/unit/test_result_store.py
git commit -m "feat(results): ResultStore — Parquet table artifacts + artifact_ref resolution"
```

---

### Task 2: ResultStore — JSON and JSONL-trace artifacts

**Files:**
- Modify: `src/labrat/results/store.py` (add `put_json`; extend `get`)
- Test: `tests/unit/test_result_store.py` (append)

**Interfaces:**
- Consumes: Task 1's `ResultStore` (`_claim`, `_ref`, `_resolve`, `_entries`, `get`).
- Produces:
  - `ResultStore.put_json(self, obj: object, kind: Literal["json", "trace"] = "json") -> str` — `"json"` → `<n>.json` file; `"trace"` → `<n>.trace.jsonl` (obj must be a `list`, one JSON line per item; `TypeError` otherwise)
  - `ResultStore.get(ref)` now also resolves json refs (→ parsed object) and trace refs (→ `list` of parsed items)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_result_store.py`:

```python
def test_put_json_roundtrips_object(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    obj = {"database": "main", "tables": [{"name": "t", "row_count": 42}]}
    ref = store.put_json(obj)
    assert store.get(ref) == obj


def test_put_json_trace_roundtrips_as_jsonl(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    items = [{"step": 1, "tool": "run_sql"}, {"step": 2, "tool": "sample_rows"}]
    ref = store.put_json(items, kind="trace")
    assert store.get(ref) == items
    # trace files are JSONL on disk (one JSON object per line)
    jsonl_files = list(store.directory.glob("*.trace.jsonl"))
    assert len(jsonl_files) == 1
    lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_put_json_trace_requires_list(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    with pytest.raises(TypeError, match="trace payload must be a list"):
        store.put_json({"not": "a list"}, kind="trace")


def test_mixed_kinds_resolve_independently(tmp_path: Path, df: pl.DataFrame) -> None:
    store = ResultStore(tmp_path)
    table_ref = store.put_table(df)
    json_ref = store.put_json({"k": "v"})
    trace_ref = store.put_json([{"i": 1}], kind="trace")
    assert isinstance(store.get(table_ref), pl.DataFrame)
    assert store.get(json_ref) == {"k": "v"}
    assert store.get(trace_ref) == [{"i": 1}]
    assert store.meta(json_ref) is None  # meta sidecar is table-only
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_result_store.py -v`
Expected: the 4 new tests FAIL with `AttributeError: 'ResultStore' object has no attribute 'put_json'`; the 4 Task 1 tests still PASS.

- [ ] **Step 3: Write minimal implementation**

In `src/labrat/results/store.py`, change the typing import line to:

```python
from typing import Any, Literal, cast
```

Add `put_json` directly below `put_table` (inside the `# ── writers ──` section):

```python
    def put_json(self, obj: object, kind: Literal["json", "trace"] = "json") -> str:
        """Store a JSON payload (kind="json") or a JSONL trace (kind="trace")."""
        n = self._claim()
        if kind == "trace":
            if not isinstance(obj, list):
                raise TypeError("trace payload must be a list of JSON-serialisable items")
            path = self._dir / f"{n:04d}.trace.jsonl"
            lines = [json.dumps(item, default=str) for item in obj]
            path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            self._entries[n] = ("trace", path)
            return self._ref(n)
        path = self._dir / f"{n:04d}.json"
        path.write_text(json.dumps(obj, default=str), encoding="utf-8")
        self._entries[n] = ("json", path)
        return self._ref(n)
```

Replace the whole `get` method with:

```python
    def get(self, ref: str) -> object:
        """Resolve a ref back to its stored payload.

        table → pl.DataFrame; json → the parsed object; trace → list of parsed items.
        """
        kind, path = self._resolve(ref)
        if kind == "table":
            return pl.read_parquet(path)
        if kind == "trace":
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line
            ]
        return json.loads(path.read_text(encoding="utf-8"))
```

Also update the module docstring's first paragraph to:

```python
"""ResultStore: addressable on-disk store for over-budget tool payloads.

Artifacts are the provenance backbone ("Cheese"): tables → Parquet + a JSON
metadata sidecar, profile snapshots → JSON, traces → JSONL. Every put returns
an opaque ``artifact_ref`` ("result://<session>/<n>") that ``get`` resolves
back. Purely mechanical — no LLM anywhere in this module.
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_result_store.py -v`
Expected: 8 PASSED

- [ ] **Step 5: Gate + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/results/store.py tests/unit/test_result_store.py
git commit -m "feat(results): ResultStore JSON + JSONL trace artifacts"
```

---

### Task 3: ResultStore.preview + cap_bytes/render_table_head helpers

**Files:**
- Modify: `src/labrat/results/store.py` (add module-level helpers + `preview` method)
- Test: `tests/unit/test_result_store.py` (append)

**Interfaces:**
- Consumes: Tasks 1–2 `ResultStore`.
- Produces (Task 6's `ContextLedger` imports these exact names from `labrat.results.store`):
  - `cap_bytes(text: str, max_bytes: int) -> str` — strict UTF-8 byte cap, no suffix marker, never splits a multibyte char into invalid output
  - `render_table_head(df: pl.DataFrame, max_rows: int) -> str` — deterministic TSV: header line + first `max_rows` rows, `None` → `""`
  - `ResultStore.preview(self, ref: str, *, max_rows: int = 50, max_bytes: int = 8000) -> str` — respects BOTH caps for every kind

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_result_store.py`:

```python
def test_cap_bytes_strict_and_multibyte_safe() -> None:
    from labrat.results.store import cap_bytes

    assert cap_bytes("short", 100) == "short"
    capped = cap_bytes("é" * 100, 15)  # "é" is 2 bytes in UTF-8
    assert len(capped.encode("utf-8")) <= 15
    assert "�" not in capped  # no replacement chars from a split code point


def test_render_table_head_tsv() -> None:
    from labrat.results.store import render_table_head

    frame = pl.DataFrame({"a": [1, 2, 3], "b": ["x", None, "z"]})
    rendered = render_table_head(frame, 2)
    assert rendered.splitlines() == ["a\tb", "1\tx", "2\t"]


def test_preview_table_respects_row_and_byte_caps(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    big = pl.DataFrame({"n": list(range(1000)), "s": ["value"] * 1000})
    ref = store.put_table(big)

    by_rows = store.preview(ref, max_rows=5, max_bytes=100_000)
    assert len(by_rows.splitlines()) == 6  # header + 5 rows

    by_bytes = store.preview(ref, max_rows=1000, max_bytes=64)
    assert len(by_bytes.encode("utf-8")) <= 64


def test_preview_json_and_trace_respect_caps(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    json_ref = store.put_json({"blob": "y" * 500})
    assert len(store.preview(json_ref, max_rows=50, max_bytes=64).encode("utf-8")) <= 64

    trace_ref = store.put_json([{"i": i} for i in range(20)], kind="trace")
    trace_preview = store.preview(trace_ref, max_rows=3, max_bytes=100_000)
    assert len(trace_preview.splitlines()) == 3
    assert len(store.preview(trace_ref, max_rows=20, max_bytes=32).encode("utf-8")) <= 32
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_result_store.py -v`
Expected: the 4 new tests FAIL (`ImportError: cannot import name 'cap_bytes'` / `AttributeError: ... no attribute 'preview'`); prior 8 still PASS.

- [ ] **Step 3: Write minimal implementation**

In `src/labrat/results/store.py`, add module-level helpers between the imports and `class ResultStore`:

```python
def cap_bytes(text: str, max_bytes: int) -> str:
    """Truncate to at most ``max_bytes`` of UTF-8. Strict — no suffix marker.

    Truncation is signalled by the caller (ModelVisibleToolResult.truncated /
    the mechanical summary), not by mutating the preview past its budget.
    """
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def render_table_head(df: pl.DataFrame, max_rows: int) -> str:
    """Deterministic TSV rendering: header line + the first ``max_rows`` rows."""
    lines = ["\t".join(df.columns)]
    for row in df.head(max_rows).iter_rows():
        lines.append("\t".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)
```

Add `preview` to `ResultStore`, below `meta` (inside the `# ── readers ──` section):

```python
    def preview(self, ref: str, *, max_rows: int = 50, max_bytes: int = 8000) -> str:
        """Bounded human/model-readable preview of an artifact (row AND byte capped)."""
        kind, path = self._resolve(ref)
        if kind == "table":
            return cap_bytes(render_table_head(pl.read_parquet(path), max_rows), max_bytes)
        if kind == "trace":
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
            return cap_bytes("\n".join(lines[:max_rows]), max_bytes)
        return cap_bytes(path.read_text(encoding="utf-8"), max_bytes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_result_store.py -v`
Expected: 12 PASSED

- [ ] **Step 5: Gate + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/results/store.py tests/unit/test_result_store.py
git commit -m "feat(results): ResultStore.preview with row+byte caps"
```

---

### Task 4: ModelVisibleToolResult + render + LedgerPayloadProvider contract

**Files:**
- Create: `src/labrat/agent/tools/serialization.py`
- Test: `tests/unit/test_ledger_serialization.py`

**Interfaces:**
- Consumes: nothing (Pydantic + typing only).
- Produces (Tasks 5–11 rely on these exact names from `labrat.agent.tools.serialization`):
  - `LedgerPayloadKind = Literal["table", "json", "trace"]`
  - `LedgerPayloadProvider` — `@runtime_checkable` `Protocol` with `def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None: ...`
  - `class ModelVisibleToolResult(BaseModel)` with fields `summary: str`, `preview: str`, `artifact_ref: str | None = None`, `full_row_count: int | None = None`, `truncated: bool = False`
  - `render(result: ModelVisibleToolResult) -> str` — `truncated=False` → returns `preview` EXACTLY (byte-identity for under-budget passthrough); `truncated=True` → the framed multi-line format below, with `artifact_ref: <ref>` on its own line (Task 7/11 tests parse that line)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_ledger_serialization.py`:

```python
"""ModelVisibleToolResult rendering + the explicit tool-payload contract."""

from __future__ import annotations

from labrat.agent.tools.serialization import (
    LedgerPayloadKind,
    LedgerPayloadProvider,
    ModelVisibleToolResult,
    render,
)


def test_render_passthrough_is_exactly_the_preview() -> None:
    mvtr = ModelVisibleToolResult(summary="", preview="ok=True rows=[['1']]", truncated=False)
    assert render(mvtr) == "ok=True rows=[['1']]"


def test_render_truncated_frames_summary_ref_and_preview() -> None:
    mvtr = ModelVisibleToolResult(
        summary="run_sql: 1000 rows × 2 columns (a, b); first 50 shown; full result stored.",
        preview="a\tb\n1\tx",
        artifact_ref="result://sess/0000",
        full_row_count=1000,
        truncated=True,
    )
    rendered = render(mvtr)
    lines = rendered.splitlines()
    assert lines[0].startswith("[context ledger] run_sql: 1000 rows")
    assert "full_row_count: 1000" in lines
    assert "artifact_ref: result://sess/0000" in lines
    assert rendered.endswith("preview:\na\tb\n1\tx")


def test_render_truncated_without_row_count_omits_line() -> None:
    mvtr = ModelVisibleToolResult(
        summary="big: 20000-byte text output.",
        preview="xxx",
        artifact_ref="result://sess/0001",
        truncated=True,
    )
    rendered = render(mvtr)
    assert "full_row_count" not in rendered
    assert "artifact_ref: result://sess/0001" in rendered


def test_ledger_payload_provider_is_duck_typed() -> None:
    class _Hooked:
        def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
            return ("json", {"k": 1})

    class _Plain:
        pass

    assert isinstance(_Hooked(), LedgerPayloadProvider)
    assert not isinstance(_Plain(), LedgerPayloadProvider)
    assert not isinstance("a string", LedgerPayloadProvider)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_ledger_serialization.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'labrat.agent.tools.serialization'`

- [ ] **Step 3: Write minimal implementation**

Create `src/labrat/agent/tools/serialization.py`:

```python
"""Model-visible tool-result contract: what the Context Ledger lets into history.

Two pieces:
  - ``ModelVisibleToolResult`` + ``render()`` — the bounded string that replaces
    a raw tool payload in AgentLoop history. When ``truncated`` is False the
    rendered string is EXACTLY the preview (== today's ``str(dispatch.value)``),
    so an under-budget result is byte-identical to the no-ledger path.
  - ``LedgerPayloadProvider`` — the explicit typed hook by which a tool output
    declares its large payload. The ledger never sniffs value shapes.

Purely mechanical — no LLM call anywhere in this module.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

LedgerPayloadKind = Literal["table", "json", "trace"]


@runtime_checkable
class LedgerPayloadProvider(Protocol):
    """A tool output that declares its large payload for the ledger to store.

    Return ``(kind, payload)`` — payload is a ``pl.DataFrame`` for ``"table"``,
    a JSON-serialisable object for ``"json"``, or a list of JSON-serialisable
    items for ``"trace"`` — or ``None`` when this particular result carries no
    large payload (e.g. an error/refused output). Tool outputs WITHOUT this
    hook fall back to today's ``str(value)`` (byte-bounded only).
    """

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None: ...


class ModelVisibleToolResult(BaseModel):
    """The bounded representation of one tool result that enters model history."""

    summary: str
    preview: str
    artifact_ref: str | None = None
    full_row_count: int | None = None
    truncated: bool = False


def render(result: ModelVisibleToolResult) -> str:
    """Compact string for AgentLoop history.

    Not truncated → exactly the preview. Truncated → a framed block with the
    mechanical summary, optional full_row_count, the artifact_ref (the model can
    cite it; provenance resolves through the ResultStore), and the preview.
    """
    if not result.truncated:
        return result.preview
    lines = [f"[context ledger] {result.summary}"]
    if result.full_row_count is not None:
        lines.append(f"full_row_count: {result.full_row_count}")
    if result.artifact_ref is not None:
        lines.append(f"artifact_ref: {result.artifact_ref}")
    lines.append("preview:")
    lines.append(result.preview)
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_ledger_serialization.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Gate + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/serialization.py tests/unit/test_ledger_serialization.py
git commit -m "feat(agent): ModelVisibleToolResult + render + LedgerPayloadProvider contract"
```

---

### Task 5: ContextLedger — budgets + string-fallback bounding

**Files:**
- Create: `src/labrat/runtime/__init__.py`
- Create: `src/labrat/runtime/context_ledger.py`
- Test: `tests/unit/test_context_ledger.py`

**Interfaces:**
- Consumes: `ResultStore` + `cap_bytes` (Tasks 1–3), `ModelVisibleToolResult` (Task 4), `DispatchResult` from `labrat.agent.tools.base` (`ok: bool, value: object, error: str | None`).
- Produces (Tasks 6–11 rely on these):
  - `@dataclass(frozen=True) class LedgerBudget` with `max_rows: int = 50`, `max_bytes: int = 8000`
  - `ContextLedger.__init__(self, store: ResultStore, *, budget: LedgerBudget | None = None) -> None`
  - `ContextLedger.record(self, tool_name: str, dispatch: DispatchResult) -> ModelVisibleToolResult` — only ever called with `dispatch.ok=True` (the loop keeps the error path unchanged)
  - Fallback over-budget storage shape: `store.put_json({"tool": tool_name, "text": full_str}, kind="json")` (Task 7/11 tests assert this exact dict)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_context_ledger.py`:

```python
"""ContextLedger: mechanical budgets; over-budget payloads go to the ResultStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.agent.tools.base import DispatchResult
from labrat.agent.tools.serialization import render
from labrat.results.store import ResultStore
from labrat.runtime.context_ledger import ContextLedger, LedgerBudget


@pytest.fixture()
def store(tmp_path: Path) -> ResultStore:
    return ResultStore(tmp_path)


def test_budget_defaults_are_conservative() -> None:
    budget = LedgerBudget()
    assert budget.max_rows == 50
    assert budget.max_bytes == 8000


def test_under_budget_string_passes_through_byte_identical(store: ResultStore) -> None:
    ledger = ContextLedger(store)
    dispatch = DispatchResult(ok=True, value="echoed: hi")
    mvtr = ledger.record("echo", dispatch)
    assert mvtr.truncated is False
    assert mvtr.artifact_ref is None
    assert mvtr.preview == "echoed: hi"
    assert render(mvtr) == str(dispatch.value)  # exactly today's string


def test_over_budget_string_is_stored_and_bounded(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=50, max_bytes=64))
    big = "x" * 500
    mvtr = ledger.record("big_tool", DispatchResult(ok=True, value=big))
    assert mvtr.truncated is True
    assert len(mvtr.preview.encode("utf-8")) <= 64
    assert "big_tool" in mvtr.summary and "500-byte" in mvtr.summary
    assert mvtr.artifact_ref is not None
    assert store.get(mvtr.artifact_ref) == {"tool": "big_tool", "text": big}


def test_non_string_value_uses_str_like_today(store: ResultStore) -> None:
    ledger = ContextLedger(store)
    value = {"ok": True, "rows": [["1", "a"]]}
    mvtr = ledger.record("some_tool", DispatchResult(ok=True, value=value))
    assert mvtr.truncated is False
    assert render(mvtr) == str(value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_context_ledger.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'labrat.runtime'`

- [ ] **Step 3: Write minimal implementation**

Create `src/labrat/runtime/__init__.py`:

```python
"""LabRat runtime: session-scoped services around the agent loop."""
```

Create `src/labrat/runtime/context_ledger.py`:

```python
"""ContextLedger: bounds what tool output enters model history.

Mechanical only — summaries are row counts / column names / byte counts /
truncation notes. NO LLM call anywhere in this module. Attached to AgentLoop
as an opt-in; when absent the loop is byte-identical to today.
"""

from __future__ import annotations

from dataclasses import dataclass

from labrat.agent.tools.base import DispatchResult
from labrat.agent.tools.serialization import ModelVisibleToolResult
from labrat.results.store import ResultStore, cap_bytes


@dataclass(frozen=True)
class LedgerBudget:
    """Per-tool-result model-visibility budget.

    Defaults are conservative: 50 rows is enough to read value patterns and
    formats; 8000 bytes ≈ roughly 2000 tokens per tool result. History is
    resent on every provider call, so each oversized result is paid for on
    every subsequent turn — one 1000-row run_sql output (~50–100 KB) can
    dominate the context. ``preview`` is capped by BOTH limits.
    """

    max_rows: int = 50
    max_bytes: int = 8000


class ContextLedger:
    """Records tool DispatchResults; over-budget payloads go to the ResultStore.

    Only called for ``dispatch.ok`` results — AgentLoop keeps the error path
    (``f"Error: {dispatch.error}"``) unchanged.
    """

    def __init__(self, store: ResultStore, *, budget: LedgerBudget | None = None) -> None:
        self._store = store
        self._budget = budget if budget is not None else LedgerBudget()

    @property
    def store(self) -> ResultStore:
        return self._store

    def record(self, tool_name: str, dispatch: DispatchResult) -> ModelVisibleToolResult:
        full_str = str(dispatch.value)
        return self._record_fallback(tool_name, full_str)

    # ── paths ─────────────────────────────────────────────────────────────────

    def _record_fallback(self, tool_name: str, full_str: str) -> ModelVisibleToolResult:
        """String fallback for outputs with no (usable) ledger_payload hook."""
        if not self._over_bytes(full_str):
            return _passthrough(full_str)
        ref = self._store.put_json({"tool": tool_name, "text": full_str}, kind="json")
        n_bytes = len(full_str.encode("utf-8"))
        summary = (
            f"{tool_name}: {n_bytes}-byte text output; preview capped at "
            f"{self._budget.max_bytes} bytes; full output stored."
        )
        return ModelVisibleToolResult(
            summary=summary,
            preview=cap_bytes(full_str, self._budget.max_bytes),
            artifact_ref=ref,
            full_row_count=None,
            truncated=True,
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _over_bytes(self, text: str) -> bool:
        return len(text.encode("utf-8")) > self._budget.max_bytes


def _passthrough(full_str: str, *, row_count: int | None = None) -> ModelVisibleToolResult:
    return ModelVisibleToolResult(
        summary="",
        preview=full_str,
        artifact_ref=None,
        full_row_count=row_count,
        truncated=False,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_context_ledger.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Gate + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/runtime/__init__.py src/labrat/runtime/context_ledger.py tests/unit/test_context_ledger.py
git commit -m "feat(runtime): ContextLedger — budgets + string-fallback bounding"
```

---

### Task 6: ContextLedger — typed payload paths (table / json / trace)

**Files:**
- Modify: `src/labrat/runtime/context_ledger.py`
- Test: `tests/unit/test_context_ledger.py` (append)

**Interfaces:**
- Consumes: `LedgerPayloadProvider` + `LedgerPayloadKind` (Task 4), `render_table_head` (Task 3), Task 5's `ContextLedger` internals.
- Produces: `record()` now dispatches on the hook — `("table", pl.DataFrame)` → `put_table` (budget: rows OR bytes); `("json", obj)` → `put_json(kind="json")` (budget: bytes of `str(value)`); `("trace", list)` → `put_json(kind="trace")` (budget: items OR bytes); hook returning `None` or a malformed payload (wrong type for its kind) → the Task 5 string fallback, never a crash.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_context_ledger.py`:

```python
import polars as pl

from labrat.agent.tools.serialization import LedgerPayloadKind


class _HookedTable:
    """Minimal tool-output stand-in exposing a DataFrame via the contract."""

    def __init__(self, df: pl.DataFrame) -> None:
        self._df = df

    def __str__(self) -> str:
        return f"rows={self._df.rows()}"

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("table", self._df)


class _HookedJson:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    def __str__(self) -> str:
        return f"payload={self._obj}"

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("json", self._obj)


class _HookedTrace:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def __str__(self) -> str:
        return f"trace={self._items}"

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("trace", self._items)


class _MalformedHook:
    def __str__(self) -> str:
        return "malformed-but-small"

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("table", "not a dataframe")


def test_under_budget_table_passes_through_with_row_count(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=5, max_bytes=8000))
    value = _HookedTable(pl.DataFrame({"a": [1, 2]}))
    mvtr = ledger.record("run_sql", DispatchResult(ok=True, value=value))
    assert mvtr.truncated is False
    assert mvtr.full_row_count == 2
    assert render(mvtr) == str(value)


def test_over_row_budget_table_stored_and_previewed(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=5, max_bytes=8000))
    df = pl.DataFrame({"a": list(range(10)), "b": ["v"] * 10})
    mvtr = ledger.record("run_sql", DispatchResult(ok=True, value=_HookedTable(df)))
    assert mvtr.truncated is True
    assert mvtr.full_row_count == 10
    assert len(mvtr.preview.splitlines()) == 6  # header + max_rows
    assert "run_sql: 10 rows" in mvtr.summary and "a, b" in mvtr.summary
    assert mvtr.artifact_ref is not None
    stored = store.get(mvtr.artifact_ref)
    assert isinstance(stored, pl.DataFrame) and stored.equals(df)
    meta = store.meta(mvtr.artifact_ref)
    assert meta is not None and meta["tool"] == "run_sql"


def test_over_byte_budget_table_triggers_even_under_row_cap(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=50, max_bytes=64))
    df = pl.DataFrame({"a": ["y" * 200]})  # 1 row, but str(value) > 64 bytes
    mvtr = ledger.record("run_sql", DispatchResult(ok=True, value=_HookedTable(df)))
    assert mvtr.truncated is True
    assert len(mvtr.preview.encode("utf-8")) <= 64


def test_over_budget_json_payload_stored(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=50, max_bytes=64))
    obj = {"tables": [{"name": f"t{i}", "rows": 100} for i in range(20)]}
    mvtr = ledger.record("profile_dataset", DispatchResult(ok=True, value=_HookedJson(obj)))
    assert mvtr.truncated is True
    assert "profile_dataset" in mvtr.summary and "JSON payload" in mvtr.summary
    assert mvtr.artifact_ref is not None
    assert store.get(mvtr.artifact_ref) == obj
    assert len(mvtr.preview.encode("utf-8")) <= 64


def test_over_budget_trace_stored_as_jsonl(store: ResultStore) -> None:
    ledger = ContextLedger(store, budget=LedgerBudget(max_rows=3, max_bytes=8000))
    items: list[object] = [{"step": i} for i in range(10)]
    mvtr = ledger.record("workflow", DispatchResult(ok=True, value=_HookedTrace(items)))
    assert mvtr.truncated is True
    assert mvtr.full_row_count == 10
    assert len(mvtr.preview.splitlines()) == 3
    assert mvtr.artifact_ref is not None
    assert store.get(mvtr.artifact_ref) == items


def test_malformed_hook_degrades_to_string_fallback(store: ResultStore) -> None:
    ledger = ContextLedger(store)
    mvtr = ledger.record("buggy", DispatchResult(ok=True, value=_MalformedHook()))
    assert mvtr.truncated is False  # small string → passthrough, no crash
    assert render(mvtr) == "malformed-but-small"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_context_ledger.py -v`
Expected: the 4 hooked over/under-budget tests FAIL (e.g. `test_under_budget_table_passes_through_with_row_count` fails on `full_row_count == 2` because `record` ignores the hook); `test_malformed_hook_degrades_to_string_fallback` may already pass; the Task 5 tests still PASS.

- [ ] **Step 3: Write the implementation**

In `src/labrat/runtime/context_ledger.py`, replace the import block with:

```python
from __future__ import annotations

import json
from dataclasses import dataclass

import polars as pl

from labrat.agent.tools.base import DispatchResult
from labrat.agent.tools.serialization import (
    LedgerPayloadProvider,
    ModelVisibleToolResult,
)
from labrat.results.store import ResultStore, cap_bytes, render_table_head
```

Replace the `record` method with:

```python
    def record(self, tool_name: str, dispatch: DispatchResult) -> ModelVisibleToolResult:
        value = dispatch.value
        full_str = str(value)
        payload = value.ledger_payload() if isinstance(value, LedgerPayloadProvider) else None
        if payload is not None:
            kind, obj = payload
            if kind == "table" and isinstance(obj, pl.DataFrame):
                return self._record_table(tool_name, full_str, obj)
            if kind == "json":
                return self._record_json(tool_name, full_str, obj)
            if kind == "trace" and isinstance(obj, list):
                return self._record_trace(tool_name, full_str, obj)
            # Malformed hook (e.g. kind "table" but payload isn't a DataFrame):
            # never crash the loop — degrade to the string fallback.
        return self._record_fallback(tool_name, full_str)
```

Add the three typed paths above `_record_fallback` (inside the `# ── paths ──` section):

```python
    def _record_table(
        self, tool_name: str, full_str: str, df: pl.DataFrame
    ) -> ModelVisibleToolResult:
        if df.height <= self._budget.max_rows and not self._over_bytes(full_str):
            return _passthrough(full_str, row_count=df.height)
        ref = self._store.put_table(df, meta={"tool": tool_name})
        shown = min(self._budget.max_rows, df.height)
        cols = ", ".join(df.columns[:20]) + (", …" if df.width > 20 else "")
        summary = (
            f"{tool_name}: {df.height} rows × {df.width} columns ({cols}); "
            f"showing first {shown} rows; full result stored."
        )
        return ModelVisibleToolResult(
            summary=summary,
            preview=cap_bytes(
                render_table_head(df, self._budget.max_rows), self._budget.max_bytes
            ),
            artifact_ref=ref,
            full_row_count=df.height,
            truncated=True,
        )

    def _record_json(self, tool_name: str, full_str: str, obj: object) -> ModelVisibleToolResult:
        if not self._over_bytes(full_str):
            return _passthrough(full_str)
        ref = self._store.put_json(obj, kind="json")
        rendered = json.dumps(obj, default=str)
        summary = (
            f"{tool_name}: {len(rendered.encode('utf-8'))}-byte JSON payload; "
            f"preview capped at {self._budget.max_bytes} bytes; full result stored."
        )
        return ModelVisibleToolResult(
            summary=summary,
            preview=cap_bytes(rendered, self._budget.max_bytes),
            artifact_ref=ref,
            full_row_count=None,
            truncated=True,
        )

    def _record_trace(
        self, tool_name: str, full_str: str, items: list[object]
    ) -> ModelVisibleToolResult:
        if len(items) <= self._budget.max_rows and not self._over_bytes(full_str):
            return _passthrough(full_str, row_count=len(items))
        ref = self._store.put_json(items, kind="trace")
        shown = min(self._budget.max_rows, len(items))
        summary = (
            f"{tool_name}: {len(items)} trace items; showing first {shown}; "
            "full trace stored."
        )
        lines = [json.dumps(item, default=str) for item in items[: self._budget.max_rows]]
        return ModelVisibleToolResult(
            summary=summary,
            preview=cap_bytes("\n".join(lines), self._budget.max_bytes),
            artifact_ref=ref,
            full_row_count=len(items),
            truncated=True,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_context_ledger.py -v`
Expected: 10 PASSED

- [ ] **Step 5: Gate + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/runtime/context_ledger.py tests/unit/test_context_ledger.py
git commit -m "feat(runtime): ContextLedger typed payload paths (table/json/trace)"
```

---

### Task 7: AgentLoop opt-in wiring (byte-identical when absent)

**Files:**
- Modify: `src/labrat/agent/loop.py` (imports; `__init__` signature ~line 57; the seam at line 164)
- Test: `tests/unit/test_agent_loop_ledger.py`

**Interfaces:**
- Consumes: `ContextLedger` (Tasks 5–6), `render` (Task 4).
- Produces: `AgentLoop.__init__(..., verifier: Verifier | None = None, max_verify_rounds: int = 2, ledger: ContextLedger | None = None)` — new keyword-only param appended LAST, all existing params/order preserved. `ledger=None` → the seam code UNCHANGED. `on_tool_call` always receives the full `output_str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_agent_loop_ledger.py`:

```python
"""AgentLoop × ContextLedger: byte-identity without a ledger, bounding with one."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from labrat.agent.loop import AgentLoop, ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry
from labrat.results.store import ResultStore
from labrat.runtime.context_ledger import ContextLedger

BIG_TEXT = "x" * 20_000  # over the 8000-byte default budget


class _MockProvider(ModelProvider):
    """Fake provider that emits a pre-configured sequence of responses."""

    def __init__(self, responses: list[list[ContentBlock]]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        blocks = self._responses[self._call_count]
        self._call_count += 1

        async def _iter() -> AsyncIterator[ContentBlock]:
            for block in blocks:
                yield block

        return _iter()


class _EchoInput(BaseModel):
    message: str


class _EchoTool(Tool[_EchoInput]):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the message."

    @property
    def input_model(self) -> type[_EchoInput]:
        return _EchoInput

    async def execute(self, ctx: ToolContext, args: _EchoInput) -> object:
        return f"echoed: {args.message}"


class _BigInput(BaseModel):
    pass


class _BigTool(Tool[_BigInput]):
    @property
    def name(self) -> str:
        return "big_output"

    @property
    def description(self) -> str:
        return "Return a deliberately oversized string."

    @property
    def input_model(self) -> type[_BigInput]:
        return _BigInput

    async def execute(self, ctx: ToolContext, args: _BigInput) -> object:
        return BIG_TEXT


@pytest.fixture()
def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(_EchoTool())
    r.register(_BigTool())
    return r


@pytest.fixture()
def ctx() -> ToolContext:
    return ToolContext(connection=object(), catalog=object())


def _script(tool_name: str, tool_input: dict[str, Any]) -> list[list[ContentBlock]]:
    return [
        [ToolUseBlock(id="c1", name=tool_name, input=tool_input)],
        [TextBlock(text="done")],
    ]


def _tool_result_contents(loop: AgentLoop) -> list[str]:
    out: list[str] = []
    for m in loop.history:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    out.append(b["content"])
    return out


async def test_no_ledger_history_carries_full_string(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    """ledger=None → tool_result content is exactly str(dispatch.value) (today)."""
    provider = _MockProvider(_script("big_output", {}))
    loop = AgentLoop(provider=provider, registry=registry, ctx=ctx)
    await loop.run("go")
    assert _tool_result_contents(loop) == [BIG_TEXT]


async def test_ledger_under_budget_history_is_byte_identical(
    registry: ToolRegistry, ctx: ToolContext, tmp_path: Path
) -> None:
    provider = _MockProvider(_script("echo", {"message": "hi"}))
    loop = AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        ledger=ContextLedger(ResultStore(tmp_path)),
    )
    await loop.run("go")
    assert _tool_result_contents(loop) == ["echoed: hi"]


async def test_ledger_bounds_oversized_result_and_roundtrips(
    registry: ToolRegistry, ctx: ToolContext, tmp_path: Path
) -> None:
    store = ResultStore(tmp_path)
    provider = _MockProvider(_script("big_output", {}))
    loop = AgentLoop(
        provider=provider, registry=registry, ctx=ctx, ledger=ContextLedger(store)
    )
    await loop.run("go")
    (content,) = _tool_result_contents(loop)
    assert len(content.encode("utf-8")) < len(BIG_TEXT)
    ref = next(
        line.removeprefix("artifact_ref: ")
        for line in content.splitlines()
        if line.startswith("artifact_ref: ")
    )
    assert store.get(ref) == {"tool": "big_output", "text": BIG_TEXT}


async def test_on_tool_call_receives_full_payload_with_ledger(
    registry: ToolRegistry, ctx: ToolContext, tmp_path: Path
) -> None:
    outputs: list[str] = []

    def on_tool_call(
        name: str, tool_input: dict[str, Any], ok: bool, output: str, latency_ms: float
    ) -> None:
        outputs.append(output)

    provider = _MockProvider(_script("big_output", {}))
    loop = AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        ledger=ContextLedger(ResultStore(tmp_path)),
    )
    await loop.run("go", on_tool_call=on_tool_call)
    assert outputs == [BIG_TEXT]  # trace/audit hook gets the FULL payload
    assert _tool_result_contents(loop) != [BIG_TEXT]  # ...but history is bounded


async def test_error_dispatch_path_unchanged_with_ledger(
    registry: ToolRegistry, ctx: ToolContext, tmp_path: Path
) -> None:
    provider = _MockProvider(_script("no_such_tool", {}))
    loop = AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        ledger=ContextLedger(ResultStore(tmp_path)),
    )
    await loop.run("go")
    (content,) = _tool_result_contents(loop)
    assert content == "Error: Unknown tool: 'no_such_tool'"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_agent_loop_ledger.py -v`
Expected: `test_no_ledger_history_carries_full_string` PASSES (current behavior); the other 4 FAIL with `TypeError: AgentLoop.__init__() got an unexpected keyword argument 'ledger'`

- [ ] **Step 3: Write the implementation**

In `src/labrat/agent/loop.py`:

(a) Add two imports after the existing `from labrat.agent.verifier import Verifier` line:

```python
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.tools.serialization import render
from labrat.agent.verifier import Verifier
from labrat.runtime.context_ledger import ContextLedger
```

(b) In `__init__` (line 57), append the keyword-only param LAST — the signature becomes:

```python
    def __init__(
        self,
        *,
        provider: Any,  # ModelProvider — typed as Any to avoid import cycle at runtime
        registry: ToolRegistry,
        ctx: ToolContext,
        system: str = "",
        dialect: str = "duckdb",
        max_turns: int | None = None,
        max_tool_calls: int | None = None,
        verifier: Verifier | None = None,
        max_verify_rounds: int = 2,
        ledger: ContextLedger | None = None,
    ) -> None:
```

and add, next to the other assignments (after `self._max_verify_rounds = max_verify_rounds`):

```python
        self._ledger = ledger
```

(c) At the seam (line 164), replace:

```python
                output_str = str(dispatch.value) if dispatch.ok else f"Error: {dispatch.error}"
                tool_result_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": output_str,
                    }
                )
                if on_tool_call is not None:
                    on_tool_call(tu.name, tu.input, dispatch.ok, output_str, latency_ms)
```

with:

```python
                output_str = str(dispatch.value) if dispatch.ok else f"Error: {dispatch.error}"
                # Ledger bounds the MODEL-VISIBLE string only; the trace/audit hook
                # (on_tool_call) always receives the full output_str. No ledger →
                # byte-identical to the pre-ledger loop.
                model_visible = output_str
                if self._ledger is not None and dispatch.ok:
                    model_visible = render(self._ledger.record(tu.name, dispatch))
                tool_result_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": model_visible,
                    }
                )
                if on_tool_call is not None:
                    on_tool_call(tu.name, tu.input, dispatch.ok, output_str, latency_ms)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_agent_loop_ledger.py tests/unit/test_agent_loop.py -v`
Expected: all PASS — including every pre-existing `test_agent_loop.py` test (the byte-identity guarantee).

- [ ] **Step 5: Gate + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/loop.py tests/unit/test_agent_loop_ledger.py
git commit -m "feat(agent): AgentLoop opt-in ContextLedger wiring (byte-identical when absent)"
```

---

### Task 8: run_sql retrofit — declare the result DataFrame

**Files:**
- Modify: `src/labrat/agent/tools/run_sql.py` (`_Output` + the success return in `execute`)
- Test: `tests/unit/test_run_sql_ledger_payload.py`

**Interfaces:**
- Consumes: `LedgerPayloadKind` (Task 4).
- Produces: `run_sql`'s `_Output` satisfies `LedgerPayloadProvider` — `ledger_payload()` returns `("table", <the executed pl.DataFrame>)` on success, `None` on refused/error outputs. The DataFrame rides on a Pydantic `PrivateAttr` (`_result_df`), so the model-dump/JSON surface and the off-ledger `str(_Output)` are unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_sql_ledger_payload.py`:

```python
"""run_sql ledger_payload hook: successful results expose their DataFrame."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.agent.tools.serialization import LedgerPayloadProvider
from labrat.db.duckdb_engine import DuckDBConnection


def _conn(tmp_path: Path) -> DuckDBConnection:
    p = str(tmp_path / "lp.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(a INT, b VARCHAR)")
    raw.execute("INSERT INTO t VALUES (1, 'x'), (2, 'y')")
    raw.close()
    c = DuckDBConnection(path=p, read_only=False)
    c.connect()
    return c


async def test_success_exposes_table_payload(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    tool = RunSqlTool()
    out = await tool.execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        tool.input_model(query="SELECT * FROM t ORDER BY a"),
    )
    assert out.ok
    assert isinstance(out, LedgerPayloadProvider)
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "table"
    assert isinstance(obj, pl.DataFrame)
    assert obj.height == 2
    assert obj.columns == ["a", "b"]
    conn.disconnect()


async def test_refused_mutation_has_no_payload(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    tool = RunSqlTool()
    out = await tool.execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        tool.input_model(query="DROP TABLE t"),
    )
    assert not out.ok
    assert out.ledger_payload() is None
    conn.disconnect()


async def test_sql_error_has_no_payload(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    tool = RunSqlTool()
    out = await tool.execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        tool.input_model(query="SELECT nope FROM t"),
    )
    assert not out.ok
    assert out.ledger_payload() is None
    conn.disconnect()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_run_sql_ledger_payload.py -v`
Expected: 3 FAIL with `AttributeError: '_Output' object has no attribute 'ledger_payload'` (the first also fails the `isinstance` assert)

- [ ] **Step 3: Write the implementation**

In `src/labrat/agent/tools/run_sql.py`:

(a) Update the pydantic import and add the serialization import:

```python
from pydantic import BaseModel, Field, PrivateAttr
```

and after the existing `from labrat.agent.tools.base import Tool, ToolContext` line:

```python
from labrat.agent.tools.serialization import LedgerPayloadKind
```

(b) Extend `_Output` (keep every existing field untouched) by appending to the class body:

```python
class _Output(BaseModel):
    ok: bool
    query: str
    columns: list[str] | None = None
    rows: list[list[str]] | None = None
    row_count: int | None = None
    refused: bool = False
    needs_confirmation: bool = False
    error: str | None = None
    error_category: str | None = None
    executed_sql: str | None = None
    hint: str | None = None
    warnings: list[str] = []

    # The executed result frame, carried outside the serialised surface so the
    # ContextLedger can store it as a Parquet artifact. PrivateAttr → excluded
    # from model_dump/JSON and from str(); off-ledger behavior is unchanged.
    _result_df: pl.DataFrame | None = PrivateAttr(default=None)

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        if self.ok and self._result_df is not None:
            return ("table", self._result_df)
        return None
```

(c) In `execute`, replace the final success return:

```python
        return _Output(
            ok=True,
            query=args.query,
            columns=df.columns,
            rows=rows,
            row_count=len(df),
            warnings=warnings,
        )
```

with:

```python
        out = _Output(
            ok=True,
            query=args.query,
            columns=df.columns,
            rows=rows,
            row_count=len(df),
            warnings=warnings,
        )
        out._result_df = df
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_run_sql_ledger_payload.py tests/unit/test_run_sql_warnings.py tests/unit/test_run_sql_repair.py -v`
Expected: all PASS (existing run_sql behavior untouched)

- [ ] **Step 5: Gate + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/run_sql.py tests/unit/test_run_sql_ledger_payload.py
git commit -m "feat(run_sql): declare result DataFrame via ledger_payload"
```

---

### Task 9: sample_rows retrofit — declare the sample DataFrame

**Files:**
- Modify: `src/labrat/agent/tools/sample_rows.py`
- Test: `tests/unit/test_sample_rows_ledger_payload.py`

**Interfaces:**
- Consumes: `LedgerPayloadKind` (Task 4).
- Produces: `sample_rows`' `_Output.ledger_payload()` returns `("table", <sampled pl.DataFrame>)` when a frame was captured, else `None`. Same `PrivateAttr` pattern as Task 8.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_sample_rows_ledger_payload.py`:

```python
"""sample_rows ledger_payload hook: the sampled DataFrame is declared for the ledger."""

from __future__ import annotations

import polars as pl

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.sample_rows import SampleRowsTool


class _StubConn:
    def sample_table(self, table: str, n: int = 10) -> pl.DataFrame:
        return pl.DataFrame({"a": list(range(n)), "b": ["v"] * n})


async def test_sample_rows_exposes_table_payload() -> None:
    tool = SampleRowsTool()
    out = await tool.execute(
        ToolContext(connection=_StubConn(), catalog=None, primary="main"),
        tool.input_model(table="t", n=4),
    )
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "table"
    assert isinstance(obj, pl.DataFrame)
    assert obj.height == 4
    assert obj.columns == ["a", "b"]
    # existing serialised surface unchanged
    assert out.rows == [[str(i), "v"] for i in range(4)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sample_rows_ledger_payload.py -v`
Expected: FAIL with `AttributeError: '_Output' object has no attribute 'ledger_payload'`

- [ ] **Step 3: Write the implementation**

Replace `src/labrat/agent/tools/sample_rows.py` in full:

```python
"""sample_rows tool: return a small sample of rows from a table."""

from __future__ import annotations

from typing import cast

import polars as pl
from pydantic import BaseModel, Field, PrivateAttr

from labrat.agent.tools.base import Tool, ToolContext
from labrat.agent.tools.serialization import LedgerPayloadKind
from labrat.db.base import Connection


class _Input(BaseModel):
    table: str
    n: int = 10
    database: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )


class _Output(BaseModel):
    table_name: str
    row_count: int
    columns: list[str]
    rows: list[list[str]]

    # Sampled frame carried outside the serialised surface for the ContextLedger.
    _result_df: pl.DataFrame | None = PrivateAttr(default=None)

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        if self._result_df is not None:
            return ("table", self._result_df)
        return None


class SampleRowsTool(Tool[_Input]):
    """Sample a small number of rows from a table to understand its contents."""

    @property
    def name(self) -> str:
        return "sample_rows"

    @property
    def description(self) -> str:
        return (
            "Return a sample of rows from a table. "
            "Use this to inspect actual data values, understand column formats, "
            "and discover common values before writing a query."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        conn = cast(Connection, ctx.connections[args.database or ctx.primary])
        df = conn.sample_table(args.table, n=args.n)
        rows = [[str(v) if v is not None else "" for v in row] for row in df.iter_rows()]
        out = _Output(
            table_name=args.table,
            row_count=len(df),
            columns=df.columns,
            rows=rows,
        )
        out._result_df = df
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_sample_rows_ledger_payload.py tests/unit/test_schema_tools.py -v`
Expected: all PASS

- [ ] **Step 5: Gate + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/sample_rows.py tests/unit/test_sample_rows_ledger_payload.py
git commit -m "feat(sample_rows): declare sample DataFrame via ledger_payload"
```

---

### Task 10: profile_dataset + column_stats retrofit — JSON payloads

**Files:**
- Modify: `src/labrat/agent/tools/profile_dataset.py` (`_Output` only)
- Modify: `src/labrat/agent/tools/column_stats.py` (`_Output` only)
- Test: `tests/unit/test_tool_ledger_payloads.py`

**Interfaces:**
- Consumes: `LedgerPayloadKind` (Task 4).
- Produces: both `_Output` classes gain `def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None` returning `("json", self.model_dump())`. Profile snapshots are the spec's "profiles → JSON" store case; `column_stats` is tiny and will pass through in practice, but declares the same contract so all four high-volume tools are uniform.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_tool_ledger_payloads.py`:

```python
"""profile_dataset + column_stats ledger_payload hooks (json kind)."""

from __future__ import annotations

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.column_stats import ColumnStatsTool
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.db.catalog import Catalog, Column, ColumnStats, Schema, Table

_CATALOG = Catalog(
    database_name="db",
    schemas=[
        Schema(
            name="main",
            tables=[
                Table(
                    name="t",
                    schema_name="main",
                    columns=[Column(name="a", data_type="INTEGER", nullable=True)],
                    foreign_keys=[],
                    row_count=7,
                )
            ],
        )
    ],
)


class _StubStatsConn:
    def column_stats(self, table: str, column: str) -> ColumnStats:
        return ColumnStats(
            column_name=column,
            table_name=table,
            data_type="INTEGER",
            null_count=0,
            distinct_count=7,
            min_value="1",
            max_value="7",
        )


async def test_profile_dataset_exposes_json_payload() -> None:
    ctx = ToolContext(connection=object(), catalog=_CATALOG, primary="main")
    tool = ProfileDatasetTool()
    # sample_rows=0 + row_count set on the catalog → the connection is never touched
    out = await tool.execute(ctx, tool.input_model(sample_rows=0))
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "json"
    assert isinstance(obj, dict)
    assert obj["database"] == "main"
    assert obj["tables"][0]["name"] == "t"
    assert obj["tables"][0]["row_count"] == 7


async def test_column_stats_exposes_json_payload() -> None:
    ctx = ToolContext(connection=_StubStatsConn(), catalog=None, primary="main")
    tool = ColumnStatsTool()
    out = await tool.execute(ctx, tool.input_model(table="t", column="a"))
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "json"
    assert isinstance(obj, dict)
    assert obj["column_name"] == "a"
    assert obj["distinct_count"] == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_tool_ledger_payloads.py -v`
Expected: 2 FAIL with `AttributeError: '_Output' object has no attribute 'ledger_payload'`

- [ ] **Step 3: Write the implementation**

(a) In `src/labrat/agent/tools/profile_dataset.py`, add the import after the existing `from labrat.agent.tools.base import Tool, ToolContext` line:

```python
from labrat.agent.tools.serialization import LedgerPayloadKind
```

and extend `_Output` (existing fields untouched):

```python
class _Output(BaseModel):
    database: str
    tables_total: int
    tables_profiled: int
    truncated: bool = False
    note: str | None = None
    tables: list[_TableProfile] = []

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("json", self.model_dump())
```

(b) In `src/labrat/agent/tools/column_stats.py`, add the same import after the existing `from labrat.agent.tools.base import Tool, ToolContext` line:

```python
from labrat.agent.tools.serialization import LedgerPayloadKind
```

and extend `_Output` (existing fields untouched):

```python
class _Output(BaseModel):
    column_name: str
    table_name: str
    data_type: str
    null_count: int
    distinct_count: int
    min_value: str | None = None
    max_value: str | None = None

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("json", self.model_dump())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_tool_ledger_payloads.py tests/unit/test_profile_dataset_tool.py -v`
Expected: all PASS

- [ ] **Step 5: Gate + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/tools/profile_dataset.py src/labrat/agent/tools/column_stats.py tests/unit/test_tool_ledger_payloads.py
git commit -m "feat(tools): profile_dataset + column_stats ledger_payload (json)"
```

---

### Task 11: run_agent_task default-on toggle + end-to-end regression

**Files:**
- Modify: `src/labrat/agent/runner.py`
- Modify: `decisions.md` (dated entry)
- Test: `tests/unit/test_agent_runner_ledger.py`

**Interfaces:**
- Consumes: `ContextLedger` (Tasks 5–6), `ResultStore` (Tasks 1–3), `AgentLoop(ledger=...)` (Task 7).
- Produces: `run_agent_task(..., on_tool_call=..., enable_ledger: bool = True, ledger_dir: Path | None = None)`.
  **ResultStore root decision (pinned):** default is a fresh per-call temp directory via `tempfile.mkdtemp(prefix="labrat-ledger-")` — session-isolated, survives the call for in-process provenance, cleaned by the OS temp reaper. Callers that want durable provenance (benchmark run dirs, TUI profile dirs) pass `ledger_dir=<their run dir>`. `enable_ledger=False` passes no ledger → bare-loop behavior.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_agent_runner_ledger.py`:

```python
"""run_agent_task ledger toggle: default-on bounding, enable_ledger=False bare."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.runner import run_agent_task
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry

BIG_TEXT = "x" * 20_000  # over the 8000-byte default budget


class _CapturingProvider(ModelProvider):
    """Scripted provider that snapshots the messages of every stream() call."""

    def __init__(self, script: list[list[ContentBlock]]) -> None:
        self._script = script
        self._call = 0
        self.captured: list[list[dict[str, Any]]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        self.captured.append(list(messages))
        blocks = self._script[self._call]
        self._call += 1

        async def _emit() -> AsyncIterator[ContentBlock]:
            for b in blocks:
                yield b

        return _emit()


class _BigInput(BaseModel):
    pass


class _BigTool(Tool[_BigInput]):
    @property
    def name(self) -> str:
        return "big_output"

    @property
    def description(self) -> str:
        return "Return a deliberately oversized string."

    @property
    def input_model(self) -> type[_BigInput]:
        return _BigInput

    async def execute(self, ctx: ToolContext, args: _BigInput) -> object:
        return BIG_TEXT


def _setup() -> tuple[ToolContext, ToolRegistry, _CapturingProvider]:
    ctx = ToolContext(connections={"primary": object()}, catalogs={"primary": object()})
    registry = ToolRegistry()
    registry.register(_BigTool())
    provider = _CapturingProvider(
        [
            [ToolUseBlock(id="t1", name="big_output", input={})],
            [TextBlock(text="done")],
        ]
    )
    return ctx, registry, provider


def _tool_result_content(messages: list[dict[str, Any]]) -> str:
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    return b["content"]
    raise AssertionError("no tool_result in captured messages")


async def test_default_ledger_bounds_model_visible_result(tmp_path: Path) -> None:
    ctx, registry, provider = _setup()
    result = await run_agent_task(
        prompt="go",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="s",
        ledger_dir=tmp_path,
    )
    assert result.final_text == "done"
    content = _tool_result_content(provider.captured[1])
    assert len(content.encode("utf-8")) < len(BIG_TEXT)
    assert "artifact_ref: result://" in content
    # the artifact landed under the caller-provided ledger_dir
    assert list(tmp_path.glob("*/*.json"))


async def test_enable_ledger_false_restores_bare_behavior(tmp_path: Path) -> None:
    ctx, registry, provider = _setup()
    await run_agent_task(
        prompt="go",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="s",
        enable_ledger=False,
        ledger_dir=tmp_path,
    )
    assert _tool_result_content(provider.captured[1]) == BIG_TEXT
    assert not list(tmp_path.glob("*/*"))


async def test_default_ledger_uses_temp_dir_when_no_ledger_dir() -> None:
    ctx, registry, provider = _setup()
    result = await run_agent_task(
        prompt="go",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="s",
    )
    # default-on with no explicit dir still bounds (root = per-call temp dir)
    assert result.tool_calls == 1
    content = _tool_result_content(provider.captured[1])
    assert "artifact_ref: result://" in content


async def test_on_tool_call_still_gets_full_payload_by_default(tmp_path: Path) -> None:
    ctx, registry, provider = _setup()
    outputs: list[str] = []

    def on_tool_call(
        name: str, tool_input: dict[str, Any], ok: bool, output: str, latency_ms: float
    ) -> None:
        outputs.append(output)

    await run_agent_task(
        prompt="go",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="s",
        on_tool_call=on_tool_call,
        ledger_dir=tmp_path,
    )
    assert outputs == [BIG_TEXT]  # DAB trace-validity invariant holds ledger-on
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_agent_runner_ledger.py -v`
Expected: FAIL — `test_default_ledger_bounds_model_visible_result`, `test_default_ledger_uses_temp_dir_when_no_ledger_dir` fail on the bounded-content asserts (full string in history); `test_enable_ledger_false_restores_bare_behavior` fails with `TypeError: run_agent_task() got an unexpected keyword argument 'enable_ledger'`.

- [ ] **Step 3: Write the implementation**

In `src/labrat/agent/runner.py`:

(a) Update imports — the top of the file becomes:

```python
from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from labrat.agent.loop import AgentLoop
from labrat.agent.providers.base import ModelProvider
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.results.store import ResultStore
from labrat.runtime.context_ledger import ContextLedger
```

(b) Change the `run_agent_task` signature (existing params/order preserved; two new keyword params appended):

```python
async def run_agent_task(
    *,
    prompt: str,
    ctx: ToolContext,
    registry: ToolRegistry,
    provider: ModelProvider,
    system_prompt: str,
    max_turns: int | None = None,
    max_tool_calls: int | None = None,
    verify: bool = False,
    max_verify_rounds: int = 2,
    on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None = None,
    enable_ledger: bool = True,
    ledger_dir: Path | None = None,
) -> AgentTaskResult:
```

(c) Append to the docstring (after the existing `verify` paragraph):

```python
    ``enable_ledger`` (default True) attaches a ContextLedger so oversized tool
    outputs enter model history as bounded summaries + artifact_refs; the
    ``on_tool_call`` hook still receives full payloads. The ResultStore root is
    ``ledger_dir`` when given (pass the run dir for durable provenance);
    otherwise a per-call temp directory (``tempfile.mkdtemp``, OS-reaped).
    ``enable_ledger=False`` restores bare-loop behavior.
```

(d) Before the `loop = AgentLoop(...)` construction, add:

```python
    ledger: ContextLedger | None = None
    if enable_ledger:
        root = (
            ledger_dir
            if ledger_dir is not None
            else Path(tempfile.mkdtemp(prefix="labrat-ledger-"))
        )
        ledger = ContextLedger(ResultStore(root))
```

and pass it in the constructor:

```python
    loop = AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        system=system_prompt,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
        verifier=verifier,
        max_verify_rounds=max_verify_rounds,
        ledger=ledger,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_agent_runner_ledger.py tests/unit/test_agent_runner.py tests/unit/test_agent_loop.py -v`
Expected: all PASS — including the pre-existing `test_run_agent_task_trace_collector_persists_output` (its echo output is under budget → passthrough is byte-identical, and the trace hook gets the full payload regardless).

- [ ] **Step 5: Add the decisions.md entry**

Append to `decisions.md`:

```markdown
## 2026-07-05 — Context Ledger Phase 1 (T1d foundation)

Tool outputs no longer necessarily enter AgentLoop history verbatim: an opt-in
ContextLedger (`src/labrat/runtime/context_ledger.py`) bounds the model-visible
string (budget: 50 rows / 8000 bytes) and stores over-budget payloads in a
ResultStore (`src/labrat/results/store.py`; tables→Parquet+meta, profiles→JSON,
traces→JSONL) addressable by `result://<session>/<n>` refs. Tools declare large
payloads via an explicit `ledger_payload()` hook (run_sql/sample_rows → table,
profile_dataset/column_stats → json); hookless tools get a byte-bounded string
fallback. Summaries are mechanical (no LLM). Bare AgentLoop without a ledger is
byte-identical to before; `run_agent_task` defaults the ledger ON
(`enable_ledger=False` restores bare behavior; `ledger_dir=` for durable
provenance, else a per-call temp dir). `on_tool_call` still receives full
payloads, so DAB trace validity is unaffected. NOT a claude-mcp lever (that
path bypasses AgentLoop) — this is the M4 program-mode/`llm_extract` foundation.
```

- [ ] **Step 6: Full gate + commit**

```bash
cd /Users/ege/repos/labrat
uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q
git add src/labrat/agent/runner.py tests/unit/test_agent_runner_ledger.py decisions.md
git commit -m "feat(runner): run_agent_task enable_ledger default-on toggle + ledger_dir"
```

---

## Post-plan notes (context for the executor, no action required)

- **Behavior change surface:** `run_agent_task` callers (DAB `labrat-agent` driver, `scripts/run_task.py`) now get the ledger by default. Under-budget results are byte-identical, so only genuinely oversized tool outputs render differently. Before relying on this in a scored DAB `labrat-agent` submission, run a small A/B (`enable_ledger` on/off) — the spec frames this as a token-burn/product win, not a score lever, and score-neutrality is asserted, not yet measured.
- **claude-mcp path untouched by construction:** nothing in this plan modifies `src/labrat/mcp/` or the DAB `claude-mcp` driver.
- Env-sensitive `tests/tui/test_app_renders.py` is unrelated to this work — ignore it if it flakes locally.

## Self-review (writing-plans checklist — performed at authoring time)

1. **Spec coverage:** §5.1 ResultStore → Tasks 1–3 (Parquet+meta / JSON / JSONL, `artifact_ref`, `get`, `preview` with both caps). §5.2 MVTR + serialization + payload contract → Task 4. §5.3 ContextLedger (record/budgets, pure, no LLM) → Tasks 5–6. §5.4 loop wiring (byte-identity, full `on_tool_call` payload) → Task 7. §5.5 retrofit of all four named tools → Tasks 8–10. §5.6 `run_agent_task` toggle + regression + gates → Task 11 (plus the per-task full-suite gate). §8 testing bullets each map to a named test. §9 open questions all pinned: budgets (50/8000), contract (`LedgerPayloadProvider` hook, no sniffing), store root (caller `ledger_dir`, temp-dir default). No gaps found.
2. **Placeholder scan:** no TBD/TODO/"similar to Task N"; every code step contains complete code; every run step has an exact command + expected outcome.
3. **Type consistency:** `ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None` identical across Task 4 (protocol), Task 6 (dispatch), Tasks 8–10 (implementations). `ResultStore` methods used by Tasks 5–7/11 match Task 1–3 signatures (`put_table(df, *, meta)`, `put_json(obj, kind)`, `get(ref)`, `meta(ref)`, `preview(ref, *, max_rows, max_bytes)`). `ContextLedger(store, *, budget)` / `record(tool_name, dispatch)` consistent across Tasks 5–7. `AgentLoop(..., ledger=...)` and `run_agent_task(..., enable_ledger, ledger_dir)` match their test usages. Fallback storage dict `{"tool", "text"}` asserted identically in Tasks 5, 7, and 11.
