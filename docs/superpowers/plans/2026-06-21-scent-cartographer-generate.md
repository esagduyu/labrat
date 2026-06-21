# Scent auto-cartographer (GENERATE) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cartographer that explores a database and writes a curated Scent reference doc — a deterministic, mechanically-verified structure skeleton plus an opt-in single LLM deep pass for business semantics — into the `labrat_maze/scent/` store that #26a consumes.

**Architecture:** Extend `src/labrat/maze/document.py` with a `Section.source` provenance field + a `render_document` serializer (inverse of `parse_document`). Add `src/labrat/maze/cartographer.py` that reuses the existing `profile_dataset` + `verify_join` tools to build a `Source: verified` skeleton, optionally calls an injected `llm_fn` once for `Source: draft` semantics, and assembles a `ScentDoc`. A thin `scripts/cartograph.py` CLI wires real connections + provider, mirroring `scripts/run_task.py`.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML 6, Polars (DuckDB results), pytest (`asyncio_mode = "auto"`).

## Global Constraints

- Branch: `feat/scent-cartographer` (already created; the spec is committed there).
- Spec: `docs/superpowers/specs/2026-06-21-scent-cartographer-generate-design.md`.
- Builds on #26a (shipped): `src/labrat/maze/{_lexical,document,store}.py`, `agent/tools/search_reference_docs.py`.
- `from __future__ import annotations` at the top of every new/edited `.py` file.
- Pyright **strict** on all of `src/labrat/`. `tool.execute(...)` returns `object` — `cast(...)` results of `ProfileDatasetTool`/`VerifyJoinTool` to their `_Output` types (import the private classes; this codebase already does this). `yaml.safe_load` → narrow with `isinstance` then `cast`.
- Tool `name`/`description`/`input_model` are `@property` methods (not relevant here — no new Tool — but keep the convention if touched).
- Full gate after every task, in order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. All clean/green before the commit step.
- Every commit message ends with these two trailer lines verbatim:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj
  ```
- Run Python via `uv run python` / `uv run pytest` (system python lacks deps).
- **Provenance tokens** are exactly `verified` / `draft` / `human`; unrecognized or absent → `human`.
- **Benchmark-safety invariant:** the deterministic pass reads schema+samples+distinct-probes only; the LLM pass sees only the assembled profile; `with_semantics=False` performs ZERO LLM calls. Never author + commit Scent docs for a held-out benchmark.
- Single-schema (DuckDB default) assumption for SQL probes in cycle A: tables are referenced by bare name (as `verify_join` already does). Multi-schema qualification is a later refinement.

---

### Task 1: Extend `document.py` — `Section.source` + `render_document` + marker round-trip

**Files:**
- Modify: `src/labrat/maze/document.py`
- Test: `tests/unit/test_maze_document_render.py`

**Interfaces:**
- Consumes: existing `ScentDoc`, `Section`, `parse_document`, `_split_sections`.
- Produces:
  - `Section.source: str` (default `"human"`).
  - `render_document(doc: ScentDoc) -> str`.
  - `parse_document` now lifts a leading `**Source:** <token>` line out of each section body into `Section.source` and removes it from `Section.body`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_maze_document_render.py
"""Tests for Section.source provenance + render_document round-trip (#26b)."""

from __future__ import annotations

from labrat.maze.document import ScentDoc, Section, parse_document, render_document


def test_section_source_defaults_to_human_when_unmarked() -> None:
    doc = parse_document("## Gotchas\n- something", domain="x")
    assert doc.sections[0].source == "human"


def test_parse_lifts_source_marker_out_of_body() -> None:
    doc = parse_document("## Key Tables\n**Source:** verified\n\n- orders ...", domain="x")
    s = doc.sections[0]
    assert s.source == "verified"
    assert "**Source:**" not in s.body  # marker removed from body
    assert s.body.strip() == "- orders ..."


def test_unrecognized_source_token_falls_back_to_human() -> None:
    doc = parse_document("## Gotchas\n**Source:** robot\n\nbody", domain="x")
    assert doc.sections[0].source == "human"


def test_render_then_parse_round_trips_sections_and_sources() -> None:
    doc = ScentDoc(
        domain="sales",
        kind="scent",
        tables=["orders", "customers"],
        confidence="draft",
        sections=[
            Section(heading="Quick Reference", body="2 tables.", source="verified"),
            Section(heading="Gotchas", body="- watch out", source="draft"),
        ],
    )
    reparsed = parse_document(render_document(doc), domain="sales")
    assert reparsed.domain == "sales"
    assert reparsed.kind == "scent"
    assert reparsed.tables == ["orders", "customers"]
    assert reparsed.confidence == "draft"
    got = [(s.heading, s.body, s.source) for s in reparsed.sections]
    assert got == [
        ("Quick Reference", "2 tables.", "verified"),
        ("Gotchas", "- watch out", "draft"),
    ]


def test_existing_unmarked_doc_body_is_unchanged() -> None:
    """A #26a hand-authored doc without markers parses with body intact."""
    doc = parse_document("## Gotchas\n- a\n- b", domain="x")
    assert doc.sections[0].body == "- a\n- b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_maze_document_render.py -q`
Expected: FAIL — `render_document` not defined / `Section` has no `source`.

- [ ] **Step 3: Edit `src/labrat/maze/document.py`**

Add `import yaml` is already present. Add the source field to `Section`:

```python
class Section(BaseModel):
    heading: str  # "" for the preamble before the first H2
    body: str
    source: str = "human"  # "verified" | "draft" | "human"; provenance for #26b cartographer
```

Add these module-level helpers (after `_H2_RE`):

```python
_RECOGNIZED_SOURCES = {"verified", "draft", "human"}
_SOURCE_LINE_RE = re.compile(r"^\*\*Source:\*\*\s*(\w+)\b.*$")


def _extract_source(body: str) -> tuple[str, str]:
    """Lift a leading ``**Source:** <token>`` line into a source value.

    If the first non-empty line of ``body`` is a Source marker, return
    (token-or-"human", body-without-that-line). Otherwise ("human", body unchanged).
    """
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == "":
            continue
        m = _SOURCE_LINE_RE.match(line.strip())
        if m is None:
            return "human", body  # first real line is not a marker
        token = m.group(1).lower()
        source = token if token in _RECOGNIZED_SOURCES else "human"
        rest = "\n".join(lines[:i] + lines[i + 1 :]).strip()
        return source, rest
    return "human", body
```

Update `_split_sections` to set `source` on every section it builds:

```python
def _split_sections(body: str) -> list[Section]:
    """Split a markdown body on H2 (##) headings. Text before the first H2 is the preamble."""
    matches = list(_H2_RE.finditer(body))
    sections: list[Section] = []
    preamble = body[: matches[0].start()] if matches else body
    if preamble.strip():
        src, clean = _extract_source(preamble.strip())
        sections.append(Section(heading="", body=clean, source=src))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        src, clean = _extract_source(body[start:end].strip())
        sections.append(Section(heading=m.group(1).strip(), body=clean, source=src))
    return sections
```

Add the serializer at the end of the file:

```python
def render_document(doc: ScentDoc) -> str:
    """Serialize a ScentDoc back to markdown (inverse of parse_document).

    Emits YAML frontmatter then each section as ``## heading`` + a ``**Source:**``
    marker line + the body. A section with an empty heading (preamble) is emitted
    body-only without a marker.
    """
    fm: dict[str, Any] = {"kind": doc.kind, "domain": doc.domain}
    if doc.tables:
        fm["tables"] = doc.tables
    if doc.confidence is not None:
        fm["confidence"] = doc.confidence
    front = yaml.safe_dump(fm, sort_keys=False).strip()

    parts: list[str] = [f"---\n{front}\n---", ""]
    for s in doc.sections:
        if s.heading:
            parts.append(f"## {s.heading}")
            parts.append(f"**Source:** {s.source}")
            parts.append("")
        if s.body:
            parts.append(s.body)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_maze_document_render.py tests/unit/test_maze_document.py -q`
Expected: PASS — new round-trip tests AND the existing #26a parser tests (regression guard).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/maze/document.py tests/unit/test_maze_document_render.py
git commit -m "feat(maze): Section.source provenance + render_document serializer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 2: Deterministic skeleton builders (Quick Reference / Key Tables / Dimensions)

**Files:**
- Create: `src/labrat/maze/cartographer.py`
- Test: `tests/unit/test_cartographer_skeleton.py`

**Interfaces:**
- Consumes: `ProfileDatasetTool` (its `_Output` / `_TableProfile` types), `Section` (Task 1), `labrat.db.base.Connection`.
- Produces:
  - `VerifiedJoin(BaseModel){left: str, right: str, match_rate: float, fanout: int}`.
  - `build_quick_reference(profile) -> Section`
  - `build_key_tables(profile, joins: list[VerifiedJoin]) -> Section`
  - `build_dimensions(profile, conn, *, cap: int = 25) -> Section`
  - all return `Section` with `source="verified"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartographer_skeleton.py
"""Deterministic skeleton builders for the cartographer (#26b)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool, _Output as ProfileOutput
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import build_dimensions, build_key_tables, build_quick_reference

_FIXTURE = "tests/fixtures/sample_dbs/ecommerce.duckdb"


@pytest.fixture()
def ctx() -> Iterator[ToolContext]:
    conn = DuckDBConnection(Path(_FIXTURE), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    yield ToolContext(connection=conn, catalog=catalog)
    conn.disconnect()


async def _profile(ctx: ToolContext) -> ProfileOutput:
    tool = ProfileDatasetTool()
    out = await tool.execute(ctx, tool.input_model(sample_rows=0))
    assert isinstance(out, ProfileOutput)
    return out


async def test_quick_reference_lists_tables_and_grain(ctx: ToolContext) -> None:
    qr = build_quick_reference(await _profile(ctx))
    assert qr.source == "verified"
    assert qr.heading == "Quick Reference"
    assert "orders" in qr.body
    assert "rows" in qr.body


async def test_key_tables_lists_columns(ctx: ToolContext) -> None:
    kt = build_key_tables(await _profile(ctx), [])
    assert kt.source == "verified"
    assert "customer_id" in kt.body
    assert "total_amount" in kt.body


async def test_dimensions_lists_low_cardinality_skips_high(ctx: ToolContext) -> None:
    conn = DuckDBConnection(Path(_FIXTURE), read_only=True)
    conn.connect()
    try:
        dims = build_dimensions(await _profile(ctx), conn, cap=25)
    finally:
        conn.disconnect()
    assert dims.source == "verified"
    assert "status" in dims.body  # low-cardinality enum is listed
    assert "email" not in dims.body  # high-cardinality column is skipped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_skeleton.py -q`
Expected: FAIL — `No module named 'labrat.maze.cartographer'`.

- [ ] **Step 3: Create `src/labrat/maze/cartographer.py`**

```python
"""Scent auto-cartographer (FEATURE_ROADMAP #26b, GENERATE half).

Explores a database and writes a curated Scent reference doc: a deterministic,
mechanically-verified structure skeleton (Source: verified) plus an opt-in single
LLM deep pass for business semantics (Source: draft). Reuses the existing
profile_dataset + verify_join tools; never reads ground-truth artifacts.
"""

from __future__ import annotations

from pydantic import BaseModel

from labrat.agent.tools.profile_dataset import _Output as ProfileOutput
from labrat.db.base import Connection

_STRINGY = ("CHAR", "TEXT", "STRING", "VARCHAR")


class VerifiedJoin(BaseModel):
    left: str  # "orders.customer_id"
    right: str  # "customers.customer_id"
    match_rate: float
    fanout: int  # max right rows per key (>1 means the join fans out)


def _is_stringy(data_type: str) -> bool:
    up = data_type.upper()
    return any(tok in up for tok in _STRINGY)


def build_quick_reference(profile: ProfileOutput) -> "Section":
    lines = [f"Database `{profile.database}`: {profile.tables_profiled} tables profiled."]
    for t in profile.tables:
        rc = "unknown" if t.row_count is None else f"{t.row_count}"
        lines.append(f"- `{t.name}`: {rc} rows.")
    if profile.note:
        lines.append(f"_{profile.note}_")
    return Section(heading="Quick Reference", body="\n".join(lines), source="verified")


def build_key_tables(profile: ProfileOutput, joins: list[VerifiedJoin]) -> "Section":
    joins_by_table: dict[str, list[VerifiedJoin]] = {}
    for j in joins:
        joins_by_table.setdefault(j.left.split(".")[0], []).append(j)

    blocks: list[str] = []
    for t in profile.tables:
        cols = ", ".join(f"{c.name} ({c.data_type})" for c in t.columns)
        block = [f"### {t.name}", f"- Columns: {cols}"]
        if t.row_count is not None:
            block.append(f"- Grain: {t.row_count} rows.")
        for j in joins_by_table.get(t.name, []):
            fan = "no fan-out" if j.fanout <= 1 else f"fans out up to {j.fanout}/key"
            pct = round(j.match_rate * 100, 1)
            block.append(f"- Join: `{j.left} = {j.right}` (verified {pct}% match, {fan}).")
        blocks.append("\n".join(block))
    return Section(heading="Key Tables", body="\n\n".join(blocks), source="verified")


def build_dimensions(profile: ProfileOutput, conn: Connection, *, cap: int = 25) -> "Section":
    lines: list[str] = []
    for t in profile.tables:
        for col in t.columns:
            if not _is_stringy(col.data_type):
                continue
            try:
                df = conn.execute(
                    f"SELECT DISTINCT {col.name} FROM {t.name} "
                    f"WHERE {col.name} IS NOT NULL LIMIT {cap + 1}"
                )
            except Exception:
                continue
            vals = [str(row[0]) for row in df.iter_rows()]
            if 0 < len(vals) <= cap:
                lines.append(f"- `{t.name}.{col.name}`: {', '.join(sorted(vals))}")
    body = "\n".join(lines) if lines else "No low-cardinality categorical columns detected."
    return Section(heading="Dimensions", body=body, source="verified")


# Imported at end to keep the module's public surface readable.
from labrat.maze.document import Section  # noqa: E402
```

> Note on the `Section` import placement: it is imported at the bottom only to keep the
> dataclass/builders at the top readable; pyright resolves the forward-referenced return
> annotations via `from __future__ import annotations`. If ruff/pyright object, move
> `from labrat.maze.document import Section` to the top import block and drop the `# noqa`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cartographer_skeleton.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green. (If the bottom import trips ruff E402/pyright, move it to the top import block per the note and re-run.)

- [ ] **Step 6: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_skeleton.py
git commit -m "feat(maze): cartographer deterministic skeleton builders

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 3: Join discovery + verification

**Files:**
- Modify: `src/labrat/maze/cartographer.py`
- Test: `tests/unit/test_cartographer_joins.py`

**Interfaces:**
- Consumes: `VerifyJoinTool` (its `_Output`), `ToolContext`, `ProfileOutput`, `VerifiedJoin` (Task 2).
- Produces: `async discover_joins(ctx: ToolContext, profile: ProfileOutput, *, database: str) -> list[VerifiedJoin]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartographer_joins.py
"""Join discovery + verification for the cartographer (#26b)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool, _Output as ProfileOutput
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import discover_joins

_FIXTURE = "tests/fixtures/sample_dbs/ecommerce.duckdb"


@pytest.fixture()
def ctx() -> Iterator[ToolContext]:
    conn = DuckDBConnection(Path(_FIXTURE), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    yield ToolContext(connection=conn, catalog=catalog, primary="primary")
    conn.disconnect()


async def _profile(ctx: ToolContext) -> ProfileOutput:
    tool = ProfileDatasetTool()
    out = await tool.execute(ctx, tool.input_model(sample_rows=0))
    assert isinstance(out, ProfileOutput)
    return out


async def test_discovers_real_cross_table_joins(ctx: ToolContext) -> None:
    joins = await discover_joins(ctx, await _profile(ctx), database="primary")
    pairs = {(j.left, j.right) for j in joins}
    assert ("orders.customer_id", "customers.customer_id") in pairs
    assert ("orders.product_id", "products.product_id") in pairs
    assert ("events.customer_id", "customers.customer_id") in pairs
    # all kept joins are mechanically valid
    assert all(j.match_rate >= 0.95 for j in joins)


async def test_excludes_self_joins(ctx: ToolContext) -> None:
    joins = await discover_joins(ctx, await _profile(ctx), database="primary")
    for j in joins:
        assert j.left.split(".")[0] != j.right.split(".")[0]  # no <table>_id -> same table
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_joins.py -q`
Expected: FAIL — `cannot import name 'discover_joins'`.

- [ ] **Step 3: Add `discover_joins` to `src/labrat/maze/cartographer.py`**

Add to the top import block:

```python
from typing import cast

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.verify_join import VerifyJoinTool, _Output as VerifyJoinOutput
```

Add the function:

```python
async def discover_joins(
    ctx: ToolContext, profile: ProfileOutput, *, database: str
) -> list[VerifiedJoin]:
    """Find candidate joins by declared FKs + an ``<base>_id`` name heuristic, then
    mechanically verify each with verify_join. Keeps only ``likely_valid`` joins and
    excludes self-joins.
    """
    by_name = {t.name.lower(): t for t in profile.tables}
    candidates: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for t in profile.tables:
        for col in t.columns:
            cname = col.name.lower()
            if not cname.endswith("_id"):
                continue
            base = cname[:-3]
            for rt_name in (base, base + "s"):
                rt = by_name.get(rt_name)
                if rt is None or rt.name == t.name:  # missing table or self-join → skip
                    continue
                rt_cols = {rc.name.lower(): rc.name for rc in rt.columns}
                for cand in (cname, "id"):
                    if cand in rt_cols:
                        key = (t.name, col.name, rt.name, rt_cols[cand])
                        if key not in seen:
                            seen.add(key)
                            candidates.append(key)
                        break

    tool = VerifyJoinTool()
    joins: list[VerifiedJoin] = []
    for lt, lc, rt, rc in candidates:
        out = await tool.execute(
            ctx,
            tool.input_model(
                left_table=lt, left_column=lc, right_table=rt, right_column=rc, database=database
            ),
        )
        verdict = cast(VerifyJoinOutput, out)
        if verdict.likely_valid:
            joins.append(
                VerifiedJoin(
                    left=f"{lt}.{lc}",
                    right=f"{rt}.{rc}",
                    match_rate=verdict.match_rate,
                    fanout=verdict.max_right_rows_per_key,
                )
            )
    return joins
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cartographer_joins.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_joins.py
git commit -m "feat(maze): cartographer join discovery + mechanical verification

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 4: LLM deep pass (draft semantics) + immutability merge

**Files:**
- Modify: `src/labrat/maze/cartographer.py`
- Test: `tests/unit/test_cartographer_semantics.py`

**Interfaces:**
- Consumes: `parse_document` (Task 1), `Section`, `ScentDoc`, `LLMFn` (from `labrat.agent.verifier`).
- Produces:
  - `async draft_semantics(skeleton: ScentDoc, profile: ProfileOutput, llm_fn: LLMFn) -> list[Section]` — returns sections tagged `source="draft"`.
  - `merge_sections(verified: list[Section], drafted: list[Section]) -> list[Section]` — appends drafted sections whose heading does not collide (case-insensitive) with a verified heading.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartographer_semantics.py
"""LLM draft pass + immutability merge for the cartographer (#26b)."""

from __future__ import annotations

from labrat.agent.tools.profile_dataset import _Output as ProfileOutput
from labrat.maze.cartographer import draft_semantics, merge_sections
from labrat.maze.document import ScentDoc, Section

_LLM_OUTPUT = """## Gotchas
- Revenue is total_amount; exclude is_test rows.

## Key Tables
- (the model tried to overwrite a verified section)
"""


async def _stub_llm(prompt: str) -> str:
    return _LLM_OUTPUT


async def test_draft_sections_are_tagged_draft() -> None:
    skeleton = ScentDoc(
        domain="sales",
        sections=[Section(heading="Key Tables", body="- verified facts", source="verified")],
    )
    profile = ProfileOutput(database="sales", tables_total=0, tables_profiled=0)
    drafted = await draft_semantics(skeleton, profile, _stub_llm)
    by_heading = {s.heading: s for s in drafted}
    assert "Gotchas" in by_heading
    assert all(s.source == "draft" for s in drafted)


def test_merge_keeps_verified_immutable() -> None:
    verified = [Section(heading="Key Tables", body="- verified facts", source="verified")]
    drafted = [
        Section(heading="Gotchas", body="- a gotcha", source="draft"),
        Section(heading="Key Tables", body="- LLM override attempt", source="draft"),
    ]
    merged = merge_sections(verified, drafted)
    kt = [s for s in merged if s.heading == "Key Tables"]
    assert len(kt) == 1  # the draft "Key Tables" was dropped
    assert kt[0].source == "verified"
    assert kt[0].body == "- verified facts"  # untouched
    assert any(s.heading == "Gotchas" and s.source == "draft" for s in merged)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_semantics.py -q`
Expected: FAIL — `cannot import name 'draft_semantics'`.

- [ ] **Step 3: Add to `src/labrat/maze/cartographer.py`**

Add to the top import block:

```python
from labrat.agent.verifier import LLMFn
from labrat.maze.document import ScentDoc, parse_document, render_document
```

(Then remove the bottom `from labrat.maze.document import Section` line and add `Section` to that top import: `from labrat.maze.document import ScentDoc, Section, parse_document, render_document`.)

Add the functions:

```python
_SEMANTICS_INSTRUCTION = (
    "You are a senior data analyst writing a reference doc for an LLM data agent.\n"
    "The VERIFIED FACTS below are mechanically confirmed ground truth — DO NOT alter, "
    "repeat, or contradict them. Write ONLY the interpretive sections a senior analyst "
    "would add: ## Gotchas (wrong-answer modes, dirty-data warnings), ## Best Practices "
    "(canonical metric definitions, preferred columns), and ## Cross-References. Use short, "
    "retrieval-oriented bullets and routing-trigger phrasing. If you are unsure about a "
    "business rule, say so rather than invent. Output GitHub-flavored markdown with ## "
    "headings only; do not emit a ## Quick Reference, ## Dimensions, or ## Key Tables "
    "section (those are already verified)."
)


def _semantics_prompt(skeleton: ScentDoc, profile: ProfileOutput) -> str:
    facts = render_document(skeleton)
    return f"{_SEMANTICS_INSTRUCTION}\n\n--- VERIFIED FACTS ---\n{facts}\n--- END FACTS ---\n"


async def draft_semantics(
    skeleton: ScentDoc, profile: ProfileOutput, llm_fn: LLMFn
) -> list[Section]:
    """Single LLM pass: draft the interpretive sections, tagged Source: draft."""
    raw = await llm_fn(_semantics_prompt(skeleton, profile))
    parsed = parse_document(raw, domain="_draft")
    return [
        Section(heading=s.heading, body=s.body, source="draft")
        for s in parsed.sections
        if s.heading
    ]


def merge_sections(verified: list[Section], drafted: list[Section]) -> list[Section]:
    """Append drafted sections whose heading does not collide with a verified one."""
    taken = {s.heading.strip().lower() for s in verified}
    return list(verified) + [d for d in drafted if d.heading.strip().lower() not in taken]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cartographer_semantics.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_semantics.py
git commit -m "feat(maze): cartographer LLM draft pass + immutability merge

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 5: `generate_scent` orchestrator + `write_docs`

**Files:**
- Modify: `src/labrat/maze/cartographer.py`
- Test: `tests/unit/test_cartographer_generate.py`

**Interfaces:**
- Consumes: all of the above; `ProfileDatasetTool`; `ToolContext`; `Connection`.
- Produces:
  - `async generate_scent(*, connections: dict[str, object], catalogs: dict[str, object], primary: str, with_semantics: bool = False, llm_fn: LLMFn | None = None, table_budget: int = 40, distinct_cap: int = 25, relevance: dict[str, float] | None = None) -> list[ScentDoc]`
  - `write_docs(docs: list[ScentDoc], out_dir: Path) -> list[Path]`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartographer_generate.py
"""End-to-end generate_scent + write_docs + benchmark-safety (#26b)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import generate_scent, write_docs

_FIXTURE = "tests/fixtures/sample_dbs/ecommerce.duckdb"


def _conns() -> tuple[dict[str, object], dict[str, object]]:
    conn = DuckDBConnection(Path(_FIXTURE), read_only=True)
    conn.connect()
    return {"shop": conn}, {"shop": conn.introspect_catalog()}


async def test_generate_writes_retrievable_verified_doc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections, catalogs = _conns()
    try:
        docs = await generate_scent(connections=connections, catalogs=catalogs, primary="shop")
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]

    assert len(docs) == 1
    doc = docs[0]
    assert doc.domain == "shop"
    assert doc.confidence == "draft"
    headings = {s.heading for s in doc.sections}
    assert {"Quick Reference", "Key Tables", "Dimensions"} <= headings
    assert all(s.source == "verified" for s in doc.sections)  # no LLM → all verified

    # write into a store and confirm #26a can retrieve it
    out = tmp_path / "labrat_maze" / "scent"
    write_docs(docs, out)
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    tool = SearchReferenceDocsTool()
    res = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="how do I join orders to customers?"),
    )
    assert any(r.domain == "shop" for r in res.results)


async def test_with_semantics_false_makes_zero_llm_calls() -> None:
    connections, catalogs = _conns()
    calls = {"n": 0}

    async def _spy(prompt: str) -> str:
        calls["n"] += 1
        return "## Gotchas\n- x"

    try:
        await generate_scent(
            connections=connections,
            catalogs=catalogs,
            primary="shop",
            with_semantics=False,
            llm_fn=_spy,
        )
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]
    assert calls["n"] == 0  # benchmark-safety: deterministic-only path never calls the model


async def test_with_semantics_appends_draft_sections() -> None:
    connections, catalogs = _conns()

    async def _llm(prompt: str) -> str:
        return "## Gotchas\n- Exclude is_test rows from metrics."

    try:
        docs = await generate_scent(
            connections=connections,
            catalogs=catalogs,
            primary="shop",
            with_semantics=True,
            llm_fn=_llm,
        )
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]
    doc = docs[0]
    gotchas = [s for s in doc.sections if s.heading == "Gotchas"]
    assert len(gotchas) == 1
    assert gotchas[0].source == "draft"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_generate.py -q`
Expected: FAIL — `cannot import name 'generate_scent'`.

- [ ] **Step 3: Add to `src/labrat/maze/cartographer.py`**

Add to the top import block:

```python
from pathlib import Path

from labrat.agent.tools.profile_dataset import ProfileDatasetTool
```

Add the functions:

```python
async def generate_scent(
    *,
    connections: dict[str, object],
    catalogs: dict[str, object],
    primary: str,
    with_semantics: bool = False,
    llm_fn: LLMFn | None = None,
    table_budget: int = 40,
    distinct_cap: int = 25,
    relevance: dict[str, float] | None = None,
) -> list[ScentDoc]:
    """Generate one Scent doc per connection: a verified deterministic skeleton plus,
    when ``with_semantics`` and ``llm_fn`` are given, an LLM-drafted semantics pass.
    """
    profiler = ProfileDatasetTool()
    docs: list[ScentDoc] = []

    for name, conn in connections.items():
        ctx = ToolContext(connections=connections, catalogs=catalogs, primary=primary)
        out = await profiler.execute(
            ctx, profiler.input_model(database=name, sample_rows=0, max_tables=10_000)
        )
        profile = cast(ProfileOutput, out)

        # budget: rank by relevance (when supplied) else row count, keep top N
        if len(profile.tables) > table_budget:
            kept = sorted(
                profile.tables,
                key=lambda t: (relevance.get(t.name, 0.0) if relevance else (t.row_count or 0)),
                reverse=True,
            )[:table_budget]
            omitted = len(profile.tables) - len(kept)
            profile = profile.model_copy(
                update={
                    "tables": kept,
                    "tables_profiled": len(kept),
                    "note": f"Budgeted to top {table_budget} of {profile.tables_total} "
                    f"tables; {omitted} omitted.",
                }
            )

        joins = await discover_joins(ctx, profile, database=name)
        sections = [
            build_quick_reference(profile),
            build_key_tables(profile, joins),
            build_dimensions(profile, cast(Connection, conn), cap=distinct_cap),
        ]
        doc = ScentDoc(
            domain=name,
            kind="scent",
            tables=[t.name for t in profile.tables],
            confidence="draft",
            sections=sections,
        )
        if with_semantics and llm_fn is not None:
            drafted = await draft_semantics(doc, profile, llm_fn)
            doc = doc.model_copy(update={"sections": merge_sections(doc.sections, drafted)})
        docs.append(doc)

    return docs


def write_docs(docs: list[ScentDoc], out_dir: Path) -> list[Path]:
    """Write each doc to ``<out_dir>/<domain>.md``; returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for doc in docs:
        path = out_dir / f"{doc.domain}.md"
        path.write_text(render_document(doc), encoding="utf-8")
        paths.append(path)
    return paths
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cartographer_generate.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_generate.py
git commit -m "feat(maze): generate_scent orchestrator + write_docs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 6: `scripts/cartograph.py` CLI

**Files:**
- Create: `scripts/cartograph.py`
- Test: `tests/unit/test_cartograph_cli.py`

**Interfaces:**
- Consumes: `generate_scent`, `write_docs`; `build_provider` + `provider_llm_fn`; `DuckDBConnection`.
- Produces: a runnable CLI; the testable seam is a `_run(args) -> int` coroutine that does NOT require a real model unless `--with-semantics` is passed.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartograph_cli.py
"""The cartograph CLI builds docs into the store without a model (#26b)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

_FIXTURE = "tests/fixtures/sample_dbs/ecommerce.duckdb"
_CLI = Path("scripts/cartograph.py")


def _load_cli():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("cartograph", _CLI)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_cli_writes_doc_without_model(tmp_path: Path) -> None:
    cli = _load_cli()
    out = tmp_path / "labrat_maze" / "scent"
    args = SimpleNamespace(
        connections=json.dumps({"shop": {"db_type": "duckdb", "db_path": _FIXTURE}}),
        primary=None,
        out=str(out),
        with_semantics=False,
        provider="anthropic",
        model="claude-sonnet-4-6",
        table_budget=40,
        distinct_cap=25,
    )
    rc = await cli._run(args)
    assert rc == 0
    written = out / "shop.md"
    assert written.is_file()
    text = written.read_text(encoding="utf-8")
    assert "## Key Tables" in text
    assert "**Source:** verified" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartograph_cli.py -q`
Expected: FAIL — file `scripts/cartograph.py` does not exist.

- [ ] **Step 3: Create `scripts/cartograph.py`**

```python
#!/usr/bin/env python3
"""Generate Scent reference docs for a database (FEATURE_ROADMAP #26b, GENERATE).

Builds a DuckDB connection set from a JSON spec (like scripts/run_task.py), runs the
deterministic cartographer (profile + verified joins + dimensions), optionally adds an
LLM-drafted semantics pass, and writes one ``<domain>.md`` per connection into the
Scent store (default ``labrat_maze/scent/``).

Usage::

    uv run python scripts/cartograph.py \\
      --connections '{"shop": {"db_type": "duckdb", "db_path": "/path/to.duckdb"}}' \\
      --out labrat_maze/scent
    # add --with-semantics --provider anthropic --model claude-sonnet-4-6 for the LLM pass
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from labrat.agent.providers import PROVIDER_NAMES, build_provider
from labrat.agent.verifier import LLMFn, provider_llm_fn
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import generate_scent, write_docs


def _build_connections(spec: dict[str, dict[str, Any]]) -> dict[str, object]:
    conns: dict[str, object] = {}
    for name, meta in spec.items():
        if str(meta.get("db_type", "")).lower() != "duckdb":
            raise SystemExit(f"cartograph supports db_type=duckdb only (got {meta.get('db_type')!r}).")
        conn = DuckDBConnection(path=str(meta["db_path"]), read_only=bool(meta.get("read_only", True)))
        conn.connect()
        conns[name] = conn
    return conns


async def _run(args: argparse.Namespace) -> int:
    spec: dict[str, dict[str, Any]] = json.loads(args.connections)
    if not spec:
        raise SystemExit("--connections must contain at least one entry")
    connections = _build_connections(spec)
    primary = args.primary or next(iter(connections))
    catalogs: dict[str, object] = {
        name: conn.introspect_catalog()  # type: ignore[attr-defined]
        for name, conn in connections.items()
    }

    llm_fn: LLMFn | None = None
    if args.with_semantics:
        llm_fn = provider_llm_fn(build_provider(args.provider, args.model))

    try:
        docs = await generate_scent(
            connections=connections,
            catalogs=catalogs,
            primary=primary,
            with_semantics=args.with_semantics,
            llm_fn=llm_fn,
            table_budget=args.table_budget,
            distinct_cap=args.distinct_cap,
        )
        paths = write_docs(docs, Path(args.out))
    finally:
        for conn in connections.values():
            conn.disconnect()  # type: ignore[attr-defined]

    for p in paths:
        print(f"wrote {p}")
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--connections", required=True, help="JSON: {name: {db_type, db_path, ...}}")
    p.add_argument("--primary", default=None, help="Primary connection name (default: first key).")
    p.add_argument("--out", default="labrat_maze/scent", help="Output dir (default: labrat_maze/scent).")
    p.add_argument("--with-semantics", action="store_true", help="Add the opt-in LLM draft pass.")
    p.add_argument("--provider", default="anthropic", choices=list(PROVIDER_NAMES))
    p.add_argument("--model", default="claude-sonnet-4-6")
    p.add_argument("--table-budget", type=int, default=40)
    p.add_argument("--distinct-cap", type=int, default=25)
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(_parse_args())))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cartograph_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green (full suite). `scripts/*` is in ruff's per-file-ignores for some lint rules but `ruff format` + pyright still apply — keep it clean.

- [ ] **Step 6: Commit**

```bash
git add scripts/cartograph.py tests/unit/test_cartograph_cli.py
git commit -m "feat(scent): cartograph CLI for generating Scent docs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

## Self-Review

**1. Spec coverage:**
- §3 provenance marker (`**Source:**`, structured round-trip, unmarked→human, out of scoring) → Task 1. ✅
- §4 components (document extend / cartographer / CLI) → Tasks 1 / 2-5 / 6. ✅
- §5 data flow: profile → Task 5; skeleton (QR/Key Tables/Dimensions, distinct probe) → Task 2; join discovery+verify (FK + name heuristic + self-join guard) → Task 3; opt-in single LLM pass (ground-truth instruction, draft tag) → Task 4; assemble `confidence: draft` + write per connection → Task 5; table budget (relevance else row count, reported) → Task 5. ✅
- §6 design points (deterministic always / LLM opt-in single-shot / immutable verified / budget reported / born draft / one doc per connection) → Tasks 2-5. ✅
- §7 benchmark safety (deterministic reads schema+samples+probes only; LLM sees only profile; `with_semantics=False` → zero LLM calls) → Task 5 `test_with_semantics_false_makes_zero_llm_calls`. ✅
- §8 testing (round-trip; skeleton; join discovery+verify; LLM stub draft+immutability; end-to-end + retrieval; benchmark-safety) → Tasks 1-6. ✅

**2. Placeholder scan:** No TBD/TODO/"similar to". Every code step has complete code; every run step has an exact command + expected result. The bottom-import note in Task 2 gives a concrete fallback, not a placeholder.

**3. Type consistency:** `VerifiedJoin{left,right,match_rate,fanout}` consistent Tasks 2/3/5. `Section.source` (Task 1) used Tasks 2/4. `discover_joins(ctx, profile, *, database)` consistent Tasks 3/5. `draft_semantics(skeleton, profile, llm_fn)` / `merge_sections(verified, drafted)` consistent Tasks 4/5. `generate_scent(... ) -> list[ScentDoc]` / `write_docs(docs, out_dir) -> list[Path]` consistent Tasks 5/6. `ProfileOutput`/`VerifyJoinOutput` are the imported private `_Output` types, cast at every `execute` call site. `LLMFn` imported from `labrat.agent.verifier` in Tasks 4/5/6.

**Cross-task note:** the `Section` import location in `cartographer.py` is created at the bottom in Task 2 (with a documented fallback) and moved to the top import block in Task 4 when more `document` symbols are needed — Task 4 Step 3 states this explicitly.
