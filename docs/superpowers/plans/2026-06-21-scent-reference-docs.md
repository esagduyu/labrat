# Scent reference-doc layer (`search_reference_docs`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a deterministic, no-LLM `search_reference_docs` tool that does section-level lexical retrieval over a dual reference-doc store, so the agent consults curated grounding (metric defs, join keys, data-quality gotchas) before it plans.

**Architecture:** A new kind-agnostic `src/labrat/maze/` package mirrors the on-disk `labrat_maze/` namespace: `_lexical.py` (tokenizer extracted from `link_schema`), `document.py` (frontmatter + section parser), `store.py` (ordered project/user source layers, project wins). The `search_reference_docs` tool scores sections (`2·heading + body`, same mechanics as `link_schema`), groups hits by doc, prepends each hit doc's Quick Reference, and returns `[]` when the store is empty — the benchmark-safety guarantee. Registered in `build_data_tools_registry()` so it reaches labrat-agent + MCP + TUI; a system-prompt router line tells the agent to call it first.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML 6 (already a direct dep), pytest (`asyncio_mode = "auto"`).

## Global Constraints

- Branch: `feat/scent-reference-docs` (already created; the spec is committed there).
- Spec: `docs/superpowers/specs/2026-06-21-scent-reference-docs-design.md`.
- `from __future__ import annotations` at the top of every new `.py` file.
- Pyright **strict** applies to all of `src/labrat/` — no `Unknown` leaks. For `yaml.safe_load(...)` results, narrow with `isinstance(x, dict)` then `cast(dict[str, Any], x)` (CLAUDE.md gotcha).
- Tool `name` / `description` / `input_model` must be `@property` methods, not class attributes.
- Full gate after every task, in this order: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. All must be clean/green before the commit step.
- Every commit message ends with these two trailer lines verbatim:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj
  ```
- Run Python via `uv run python` — the system `python3` lacks project deps.
- **Benchmark-safety invariant (never violate):** the store ships empty; the tool returns `results: []` on an empty/absent store. Never author benchmark-answer-shaped docs. The only shipped content targets the non-benchmark `ecommerce` fixture.

---

### Task 1: Extract shared lexical helpers into `maze/_lexical.py`

Pure refactor: move `link_schema.py`'s tokenizer/stemmer/stopwords into a shared module and import them back, so `search_reference_docs` can reuse the *exact* same lexical logic. `link_schema`'s behavior must not change (its existing tests are the regression guard).

**Files:**
- Create: `src/labrat/maze/__init__.py`
- Create: `src/labrat/maze/_lexical.py`
- Modify: `src/labrat/agent/tools/link_schema.py` (replace local `_STOPWORDS` / `_name_tokens` / `_question_tokens` / `_stem` with imports)
- Test: `tests/unit/test_maze_lexical.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `labrat.maze._lexical.STOPWORDS: set[str]`
  - `name_tokens(s: str) -> list[str]` — lowercased alnum tokens, **unfiltered**.
  - `question_tokens(s: str) -> list[str]` — `name_tokens` minus stopwords and tokens < 3 chars.
  - `stem(t: str) -> str` — drops a trailing `s` when `len > 3`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_maze_lexical.py
"""Tests for the shared lexical helpers extracted from link_schema (FEATURE_ROADMAP #26a)."""

from __future__ import annotations

from labrat.maze._lexical import name_tokens, question_tokens, stem


def test_name_tokens_splits_identifiers_unfiltered() -> None:
    assert name_tokens("article_metadata") == ["article", "metadata"]
    # unfiltered: short tokens and stopwords are kept at this layer
    assert name_tokens("the id") == ["the", "id"]


def test_question_tokens_drops_stopwords_and_short_tokens() -> None:
    toks = question_tokens("How many orders did each customer place?")
    assert "orders" in toks
    assert "customer" in toks
    assert "many" not in toks  # stopword
    assert "how" not in toks  # stopword
    assert "id" not in toks if "id" in toks else True  # <3 chars filtered


def test_stem_strips_trailing_s_only_when_long_enough() -> None:
    assert stem("orders") == "order"
    assert stem("is") == "is"  # len <= 3 untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_maze_lexical.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'labrat.maze'`.

- [ ] **Step 3: Create the package and the lexical module**

```python
# src/labrat/maze/__init__.py
"""The Rat Maze: the optional knowledge layer (Scent now; Trail/Warren later)."""
```

```python
# src/labrat/maze/_lexical.py
"""Shared deterministic lexical helpers for grounding tools.

Tokenizer / stemmer / stopword set, extracted verbatim from link_schema so that
link_schema and search_reference_docs share one implementation. No LLM, no I/O.
"""

from __future__ import annotations

import re

STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "by", "with",
    "which", "what", "how", "many", "most", "all", "list", "show", "each", "per",
    "is", "are", "that", "this", "number", "count", "total", "average", "sum",
    "find", "give", "get", "from", "where", "group", "order", "have", "has",
    "did", "do", "does", "between", "over", "into", "their", "its", "was",
    "were", "any",
}


def name_tokens(s: str) -> list[str]:
    """Split an identifier into alphanumeric tokens (article_metadata → article, metadata)."""
    return re.findall(r"[a-z0-9]+", s.lower())


def question_tokens(s: str) -> list[str]:
    return [t for t in name_tokens(s) if len(t) >= 3 and t not in STOPWORDS]


def stem(t: str) -> str:
    return t[:-1] if len(t) > 3 and t.endswith("s") else t
```

- [ ] **Step 4: Refactor `link_schema.py` to import the shared helpers**

In `src/labrat/agent/tools/link_schema.py`: delete the local `_STOPWORDS` block and the `_name_tokens` / `_question_tokens` / `_stem` functions, and add the import. Then rename the three call sites (`_name_tokens` → `name_tokens`, `_question_tokens` → `question_tokens`, `_stem` → `stem`). Replace the `import re` line region and the deleted block with:

```python
from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.catalog import Catalog
from labrat.maze._lexical import name_tokens, question_tokens, stem
```

(Remove the now-unused `import re` from `link_schema.py` if nothing else in the file uses it.) The four call sites inside `execute` become `name_tokens(...)`, `question_tokens(...)`, `stem(...)` — logic otherwise unchanged.

- [ ] **Step 5: Run the new test and the link_schema regression**

Run: `uv run pytest tests/unit/test_maze_lexical.py tests/unit/test_grounding_tools.py -q`
Expected: PASS — new lexical tests pass AND all existing `link_schema` tests in `test_grounding_tools.py` still pass (proves the refactor changed nothing).

- [ ] **Step 6: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 7: Commit**

```bash
git add src/labrat/maze/__init__.py src/labrat/maze/_lexical.py \
        src/labrat/agent/tools/link_schema.py tests/unit/test_maze_lexical.py
git commit -m "refactor(maze): extract link_schema lexical helpers into maze/_lexical

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 2: Reference-doc data model + parser (`maze/document.py`)

**Files:**
- Create: `src/labrat/maze/document.py`
- Test: `tests/unit/test_maze_document.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (uses PyYAML).
- Produces:
  - `Section(BaseModel)` with fields `heading: str`, `body: str`.
  - `ScentDoc(BaseModel)` with fields `domain: str`, `kind: str = "scent"`, `tables: list[str] = []`, `confidence: str | None = None`, `scope: str = ""`, `sections: list[Section] = []`, and method `quick_reference(self) -> Section | None`.
  - `parse_document(text: str, *, domain: str, scope: str = "") -> ScentDoc`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_maze_document.py
"""Tests for the Maze reference-doc parser + model (FEATURE_ROADMAP #26a)."""

from __future__ import annotations

from labrat.maze.document import ScentDoc, parse_document

_DOC = """---
kind: scent
domain: ecommerce_sales
tables: [orders, customers]
confidence: verified
---
Intro preamble before any heading.

## Quick Reference
Grain: one row per order line.

## Gotchas
- Dates are dirty mixed-format text; parse before any date math.
"""


def test_parses_frontmatter_and_sections() -> None:
    doc = parse_document(_DOC, domain="fallback", scope="project")
    assert doc.kind == "scent"
    assert doc.domain == "ecommerce_sales"  # frontmatter wins over the fallback
    assert doc.tables == ["orders", "customers"]
    assert doc.confidence == "verified"
    assert doc.scope == "project"
    headings = [s.heading for s in doc.sections]
    assert headings == ["", "Quick Reference", "Gotchas"]  # "" is the preamble
    qr = doc.quick_reference()
    assert qr is not None
    assert "one row per order line" in qr.body


def test_missing_frontmatter_loads_body_only_with_fallback_domain() -> None:
    doc = parse_document("## Gotchas\n- something", domain="sales", scope="user")
    assert doc.domain == "sales"  # falls back to the filename stem
    assert doc.kind == "scent"
    assert [s.heading for s in doc.sections] == ["Gotchas"]


def test_malformed_frontmatter_does_not_crash() -> None:
    bad = "---\n: : not yaml : :\n---\n## Notes\nbody"
    doc = parse_document(bad, domain="x")
    assert isinstance(doc, ScentDoc)
    assert [s.heading for s in doc.sections] == ["Notes"]


def test_h3_is_not_treated_as_a_section_boundary() -> None:
    doc = parse_document("## Key Tables\n### orders\ngrain stuff", domain="x")
    assert [s.heading for s in doc.sections] == ["Key Tables"]
    assert "### orders" in doc.sections[0].body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_maze_document.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'labrat.maze.document'`.

- [ ] **Step 3: Write the implementation**

```python
# src/labrat/maze/document.py
"""Reference-doc data model + markdown parser for the Maze store.

Kind-agnostic: ScentDoc carries a `kind` discriminator ("scent" now; "trail"/"warren"
later read by the same store/parser). Tolerates missing/malformed frontmatter.
"""

from __future__ import annotations

import re
from typing import Any, cast

import yaml
from pydantic import BaseModel

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_H2_RE = re.compile(r"^##\s+(.*)$", re.MULTILINE)


class Section(BaseModel):
    heading: str  # "" for the preamble before the first H2
    body: str


class ScentDoc(BaseModel):
    domain: str
    kind: str = "scent"
    tables: list[str] = []
    confidence: str | None = None
    scope: str = ""  # "project" | "user"; set by the store, not the file
    sections: list[Section] = []

    def quick_reference(self) -> Section | None:
        for s in self.sections:
            if s.heading.strip().lower() == "quick reference":
                return s
        return None


def _split_sections(body: str) -> list[Section]:
    """Split a markdown body on H2 (##) headings. Text before the first H2 is the preamble."""
    matches = list(_H2_RE.finditer(body))
    sections: list[Section] = []
    preamble = body[: matches[0].start()] if matches else body
    if preamble.strip():
        sections.append(Section(heading="", body=preamble.strip()))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append(Section(heading=m.group(1).strip(), body=body[start:end].strip()))
    return sections


def parse_document(text: str, *, domain: str, scope: str = "") -> ScentDoc:
    """Parse a reference-doc markdown string into a ScentDoc.

    `domain` is the fallback identity (the filename stem) used when frontmatter omits it.
    """
    meta: dict[str, Any] = {}
    body = text
    m = _FRONTMATTER_RE.match(text)
    if m:
        try:
            loaded = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            loaded = None
        if isinstance(loaded, dict):
            meta = cast(dict[str, Any], loaded)
        body = m.group(2)

    raw_tables = meta.get("tables")
    tables = [str(t) for t in cast(list[Any], raw_tables)] if isinstance(raw_tables, list) else []
    confidence = meta.get("confidence")
    return ScentDoc(
        domain=str(meta.get("domain") or domain),
        kind=str(meta.get("kind") or "scent"),
        tables=tables,
        confidence=str(confidence) if confidence is not None else None,
        scope=scope,
        sections=_split_sections(body),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_maze_document.py -q`
Expected: PASS (all four tests).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/maze/document.py tests/unit/test_maze_document.py
git commit -m "feat(maze): reference-doc model + frontmatter/section parser

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 3: The dual store with precedence (`maze/store.py`)

**Files:**
- Create: `src/labrat/maze/store.py`
- Test: `tests/unit/test_maze_store.py`

**Interfaces:**
- Consumes: `labrat.maze.document.ScentDoc`, `parse_document`.
- Produces:
  - `MazeStore(project_root: Path, home: Path, profile: str)` — constructor for tests.
  - `MazeStore.from_env(profile: str = "default") -> MazeStore` — `project_root = $LABRAT_MAZE_DIR or os.getcwd()`, `home = Path.home()`.
  - `MazeStore.docs(self, kind: str = "scent") -> list[ScentDoc]` — deduped by `domain`, **project wins**.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_maze_store.py
"""Tests for the Maze dual store + precedence (FEATURE_ROADMAP #26a)."""

from __future__ import annotations

from pathlib import Path

from labrat.maze.store import MazeStore


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_reads_both_layers_and_tags_scope(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    home = tmp_path / "home"
    _write(project / "labrat_maze" / "scent" / "sales.md", "---\ndomain: sales\n---\n## A\nx")
    _write(home / ".labrat" / "maze" / "acme" / "scent" / "events.md", "---\ndomain: events\n---\n## B\ny")

    docs = MazeStore(project_root=project, home=home, profile="acme").docs()
    by_domain = {d.domain: d for d in docs}
    assert set(by_domain) == {"sales", "events"}
    assert by_domain["sales"].scope == "project"
    assert by_domain["events"].scope == "user"


def test_project_wins_on_domain_conflict(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    home = tmp_path / "home"
    _write(project / "labrat_maze" / "scent" / "sales.md", "---\ndomain: sales\n---\n## P\nproject body")
    _write(home / ".labrat" / "maze" / "acme" / "scent" / "sales.md", "---\ndomain: sales\n---\n## U\nuser body")

    docs = MazeStore(project_root=project, home=home, profile="acme").docs()
    assert len(docs) == 1
    assert docs[0].scope == "project"
    assert docs[0].sections[0].heading == "P"


def test_missing_dirs_yield_empty(tmp_path: Path) -> None:
    docs = MazeStore(project_root=tmp_path / "nope", home=tmp_path / "alsonope", profile="x").docs()
    assert docs == []


def test_from_env_uses_labrat_maze_dir(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _write(tmp_path / "labrat_maze" / "scent" / "s.md", "---\ndomain: s\n---\n## A\nx")
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    docs = MazeStore.from_env(profile="default").docs()
    assert [d.domain for d in docs] == ["s"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_maze_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'labrat.maze.store'`.

- [ ] **Step 3: Write the implementation**

```python
# src/labrat/maze/store.py
"""The Maze store: resolves ordered reference-doc source layers from disk.

On-disk namespace (forward-compatible with trail/warren kinds + a future team layer):

    <project_root>/labrat_maze/<kind>/*.md             (project scope — wins on conflict)
    <home>/.labrat/maze/<profile>/<kind>/*.md          (user scope)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from labrat.maze.document import ScentDoc, parse_document


@dataclass(frozen=True)
class _Layer:
    scope: str
    root: Path  # the directory that holds the <kind>/ subdirs


class MazeStore:
    def __init__(self, project_root: Path, home: Path, profile: str) -> None:
        # Ordered low → high precedence: later layers overwrite earlier on domain conflict.
        self._layers: list[_Layer] = [
            _Layer("user", home / ".labrat" / "maze" / profile),
            _Layer("project", project_root / "labrat_maze"),
        ]

    @classmethod
    def from_env(cls, profile: str = "default") -> MazeStore:
        root = Path(os.environ.get("LABRAT_MAZE_DIR") or os.getcwd())
        return cls(project_root=root, home=Path.home(), profile=profile)

    def docs(self, kind: str = "scent") -> list[ScentDoc]:
        by_domain: dict[str, ScentDoc] = {}
        for layer in self._layers:  # low → high; project (last) wins
            directory = layer.root / kind
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                doc = parse_document(
                    path.read_text(encoding="utf-8"), domain=path.stem, scope=layer.scope
                )
                if doc.kind != kind:
                    continue
                by_domain[doc.domain] = doc
        return list(by_domain.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_maze_store.py -q`
Expected: PASS (all four tests).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/maze/store.py tests/unit/test_maze_store.py
git commit -m "feat(maze): dual reference-doc store with project-wins precedence

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 4: The `search_reference_docs` tool

Section-level scoring (`2·heading + body`), group hits by doc, prepend Quick Reference per hit doc (unless QR is itself a hit), `top_k` cap on matched sections, empty store → `results: []`.

**Files:**
- Create: `src/labrat/agent/tools/search_reference_docs.py`
- Test: `tests/unit/test_search_reference_docs.py`

**Interfaces:**
- Consumes: `labrat.maze._lexical.{question_tokens, stem}`, `labrat.maze.store.MazeStore`, `labrat.maze.document.Section`, `labrat.agent.tools.base.{Tool, ToolContext}`.
- Produces:
  - `SectionMatch(BaseModel)`: `heading: str`, `body: str`, `score: float`, `matched_terms: list[str]`.
  - `DocResult(BaseModel)`: `domain: str`, `quick_reference: str | None`, `sections: list[SectionMatch]`.
  - `SearchReferenceDocsTool` with `name == "search_reference_docs"`, output `_Output(question: str, results: list[DocResult])`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_search_reference_docs.py
"""Tests for the search_reference_docs tool (FEATURE_ROADMAP #26a, Scent consume half)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool

_STOCKINDEX = """---
kind: scent
domain: stockindex
---
## Quick Reference
One row per index per day. Use CloseUSD for cross-country comparisons.

## Gotchas
- The Date column is dirty mixed-format text; parse with try_strptime before any date math.

## Best Practices
- Prefer adjusted close.
"""


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    scent = tmp_path / "labrat_maze" / "scent"
    scent.mkdir(parents=True)
    (scent / "stockindex.md").write_text(_STOCKINDEX, encoding="utf-8")
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    return tmp_path


async def test_returns_matching_section_with_quick_reference(env: Path) -> None:
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="average return by country, the dates look wrong"),
    )
    assert len(out.results) == 1
    res = out.results[0]
    assert res.domain == "stockindex"
    # the Gotchas section (matches "dates"/"date") is returned
    headings = [s.heading for s in res.sections]
    assert "Gotchas" in headings
    # Quick Reference is prepended for context (it was not itself a hit here)
    assert res.quick_reference is not None
    assert "CloseUSD" in res.quick_reference


async def test_empty_store_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Benchmark-safety guarantee: no docs -> no results (never falls back to all)."""
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path / "nothing"))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="anything at all"),
    )
    assert out.results == []


async def test_no_lexical_match_returns_empty(env: Path) -> None:
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="kubernetes pod autoscaling latency"),
    )
    assert out.results == []


async def test_top_k_caps_matched_sections(env: Path) -> None:
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="index date close adjusted practices", top_k=1),
    )
    total_sections = sum(len(r.sections) for r in out.results)
    assert total_sections == 1


async def test_registered_in_data_tools_registry() -> None:
    names = {s["name"] for s in build_data_tools_registry().to_anthropic_schemas()}
    assert "search_reference_docs" in names
```

> Note: `test_registered_in_data_tools_registry` will FAIL until Task 5. That is expected — it documents the cross-task contract. Run the other four tests in Step 2/4 by name; the registry test goes green in Task 5.

- [ ] **Step 2: Run the tool tests to verify they fail**

Run: `uv run pytest tests/unit/test_search_reference_docs.py -q -k "not registered"`
Expected: FAIL — `ModuleNotFoundError: No module named 'labrat.agent.tools.search_reference_docs'`.

- [ ] **Step 3: Write the implementation**

```python
# src/labrat/agent/tools/search_reference_docs.py
"""search_reference_docs: retrieve curated reference docs relevant to a question.

Scent layer (FEATURE_ROADMAP #26a) — the consume half of the Rat Maze. Section-level
lexical retrieval over the dual reference-doc store; deterministic, no LLM, same mechanics
as link_schema. Empty/absent store → no results (the benchmark-safety guarantee).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.maze._lexical import question_tokens, stem
from labrat.maze.document import Section
from labrat.maze.store import MazeStore


def _stems(text: str) -> set[str]:
    return {stem(t) for t in question_tokens(text)}


class _Input(BaseModel):
    question: str = Field(
        description="The natural-language question to ground against the reference docs."
    )
    top_k: int = Field(default=5, description="Max number of matched sections to return.")


class SectionMatch(BaseModel):
    heading: str
    body: str
    score: float
    matched_terms: list[str]


class DocResult(BaseModel):
    domain: str
    quick_reference: str | None
    sections: list[SectionMatch]


class _Output(BaseModel):
    question: str
    results: list[DocResult]


@dataclass
class _Hit:
    domain: str
    order: int
    section: Section
    score: float
    matched: list[str]


class SearchReferenceDocsTool(Tool[_Input]):
    """Lexically retrieve the reference-doc sections most relevant to a question."""

    @property
    def name(self) -> str:
        return "search_reference_docs"

    @property
    def description(self) -> str:
        return (
            "Search the curated reference docs for grounding relevant to the question — "
            "metric definitions, join keys, table grain, and known data-quality gotchas for "
            "this warehouse. Call this FIRST, before profiling or writing SQL. Returns nothing "
            "if no reference docs are configured."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        docs = MazeStore.from_env(profile=ctx.profile_name).docs(kind="scent")
        q_stems = _stems(args.question)
        stem_to_term = {stem(t): t for t in question_tokens(args.question)}

        hits: list[_Hit] = []
        for doc in docs:
            for idx, section in enumerate(doc.sections):
                heading_stems = _stems(f"{doc.domain} {section.heading}")
                body_stems = _stems(section.body)
                name_hits = q_stems & heading_stems
                body_hits = (q_stems & body_stems) - name_hits
                if not name_hits and not body_hits:
                    continue
                matched = sorted(stem_to_term[s] for s in (name_hits | body_hits))
                hits.append(
                    _Hit(
                        domain=doc.domain,
                        order=idx,
                        section=section,
                        score=float(2 * len(name_hits) + len(body_hits)),
                        matched=matched,
                    )
                )

        hits.sort(key=lambda h: (-h.score, h.domain, h.order))
        top = hits[: args.top_k]

        results: list[DocResult] = []
        seen: dict[str, DocResult] = {}
        for h in top:
            dr = seen.get(h.domain)
            if dr is None:
                dr = DocResult(domain=h.domain, quick_reference=None, sections=[])
                seen[h.domain] = dr
                results.append(dr)
            dr.sections.append(
                SectionMatch(
                    heading=h.section.heading,
                    body=h.section.body,
                    score=h.score,
                    matched_terms=h.matched,
                )
            )

        # Prepend each hit doc's Quick Reference once, unless the QR is itself a matched section.
        qr_by_domain = {d.domain: d.quick_reference() for d in docs}
        for dr in results:
            qr = qr_by_domain.get(dr.domain)
            if qr is not None and all(s.heading != qr.heading for s in dr.sections):
                dr.quick_reference = qr.body

        return _Output(question=args.question, results=results)
```

- [ ] **Step 4: Run the tool tests to verify they pass**

Run: `uv run pytest tests/unit/test_search_reference_docs.py -q -k "not registered"`
Expected: PASS (the four behavioral tests).

- [ ] **Step 5: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean; all green **except** `test_registered_in_data_tools_registry` (goes green in Task 5). If you prefer a fully-green gate here, this test can be left failing only between Task 4 and Task 5 — note it explicitly in the commit body.

- [ ] **Step 6: Commit**

```bash
git add src/labrat/agent/tools/search_reference_docs.py tests/unit/test_search_reference_docs.py
git commit -m "feat(scent): search_reference_docs tool (section-level lexical retrieval)

Benchmark-safe: empty store -> results=[] (no fallback-to-all). Registry test is
red until Task 5 wires the tool into build_data_tools_registry().

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 5: Register the tool + add the system-prompt router line

**Files:**
- Modify: `src/labrat/agent/data_tools.py` (import + `register` + docstring list)
- Modify: `src/labrat/agent/prompts/system_base.md` (new Workflow step 1 + Tool Usage bullet)
- Test: `tests/unit/test_search_reference_docs.py::test_registered_in_data_tools_registry` (already written in Task 4) + a new prompt-content assertion.

**Interfaces:**
- Consumes: `SearchReferenceDocsTool` from Task 4.
- Produces: `search_reference_docs` present in `build_data_tools_registry()`.

- [ ] **Step 1: Add the prompt-content failing test**

Append to `tests/unit/test_search_reference_docs.py`:

```python
def test_system_prompt_routes_to_the_tool() -> None:
    from pathlib import Path

    text = Path("src/labrat/agent/prompts/system_base.md").read_text(encoding="utf-8")
    assert "search_reference_docs" in text
    assert "Consult reference docs" in text
```

- [ ] **Step 2: Run the two now-relevant tests to verify they fail**

Run: `uv run pytest tests/unit/test_search_reference_docs.py -q -k "registered or routes"`
Expected: FAIL — tool not in registry; prompt lacks the router line.

- [ ] **Step 3: Register the tool in `data_tools.py`**

Add the import alongside the others:

```python
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
```

Add the registration as the **first** `register` call in `build_data_tools_registry()` (it should run first, mirroring its "call this FIRST" guidance):

```python
    registry = ToolRegistry()
    registry.register(SearchReferenceDocsTool())
    registry.register(ProfileDatasetTool())
```

Update the docstring's tool list to include `search_reference_docs` (add it to the "Tools included:" sentence).

- [ ] **Step 4: Add the router line to `system_base.md`**

Replace the `## Workflow` numbered list so it begins with the new step 1 and renumbers the rest:

```markdown
## Workflow

For anything beyond a trivial lookup, follow this loop — it prevents wrong answers from premature querying:

1. **Consult reference docs.** Call `search_reference_docs` with the user's question to pull any curated grounding for this warehouse — metric definitions, join keys, and known data-quality gotchas. Treat returned **Gotchas** as authoritative. If nothing is returned, just proceed.
2. **Profile.** Call `profile_dataset` to ground yourself in the real schema, row counts, and sample values before you plan. Use `describe_table` / `sample_rows` / `column_stats` to drill into specifics. Never plan against assumed structure.
3. **Plan.** State a short numbered plan of the steps you'll take. Revise it as you learn, but say so.
4. **Execute step by step.** Run one step at a time and read each result before deciding the next — don't batch speculative queries.
5. **Verify before finishing.** Re-read the user's question and confirm your result actually answers *that* question. Sanity-check magnitudes, row counts, and units; make sure joins didn't drop or fan out rows. If anything looks off, investigate before reporting.
```

Add this bullet as the **first** item in the `## Tool Usage` list:

```markdown
- Use `search_reference_docs` first to pull curated grounding (metric definitions, join keys, data-quality gotchas) for the question; treat returned Gotchas as authoritative. Returns nothing if no reference docs are configured.
```

- [ ] **Step 5: Run the full tool test file**

Run: `uv run pytest tests/unit/test_search_reference_docs.py -q`
Expected: PASS — all tests now green, including `registered` and `routes`.

- [ ] **Step 6: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green.

- [ ] **Step 7: Commit**

```bash
git add src/labrat/agent/data_tools.py src/labrat/agent/prompts/system_base.md \
        tests/unit/test_search_reference_docs.py
git commit -m "feat(scent): register search_reference_docs + system-prompt router line

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 6: Shipped content — authoring template + one worked example

Ship a template and one worked example against the **non-benchmark** ecommerce fixture, plus an integration test that loads the example through the store and retrieves a section. This proves the shipped doc parses and is retrievable end-to-end.

**Files:**
- Create: `docs/scent/TEMPLATE.md`
- Create: `docs/scent/examples/ecommerce_sales.md`
- Test: `tests/unit/test_scent_example_doc.py`

**Interfaces:**
- Consumes: `MazeStore`, `SearchReferenceDocsTool` (end-to-end).
- Produces: shipped, user-copyable content. No new code interfaces.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/unit/test_scent_example_doc.py
"""The shipped Scent example parses and is retrievable end-to-end (FEATURE_ROADMAP #26a)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.maze.document import parse_document


def test_template_and_example_files_exist() -> None:
    assert Path("docs/scent/TEMPLATE.md").is_file()
    assert Path("docs/scent/examples/ecommerce_sales.md").is_file()


def test_example_parses_with_template_sections() -> None:
    doc = parse_document(
        Path("docs/scent/examples/ecommerce_sales.md").read_text(encoding="utf-8"),
        domain="ecommerce_sales",
    )
    assert doc.kind == "scent"
    headings = {s.heading for s in doc.sections}
    assert {"Quick Reference", "Key Tables", "Gotchas"} <= headings


async def test_example_is_retrievable_through_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scent = tmp_path / "labrat_maze" / "scent"
    scent.mkdir(parents=True)
    shutil.copy("docs/scent/examples/ecommerce_sales.md", scent / "ecommerce_sales.md")
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))

    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="how do I join orders to customers?"),
    )
    assert any(r.domain == "ecommerce_sales" for r in out.results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_scent_example_doc.py -q`
Expected: FAIL — files do not exist.

- [ ] **Step 3: Write `docs/scent/TEMPLATE.md`**

```markdown
---
kind: scent
domain: <short_domain_slug>          # e.g. ecommerce_sales — also the dedup key
tables: [<table_a>, <table_b>]       # optional, reserved (not scored yet)
confidence: verified                 # verified | draft
---

<!--
Write each section "for retrieval by an LLM": short, factual, routing-trigger
phrasing ("IF the question is about X, use Y / DO NOT use Z"), not prose essays.
Drop this file at:  ./labrat_maze/scent/<domain>.md  (project, version-controlled)
              or:   ~/.labrat/maze/<profile>/scent/<domain>.md  (personal)
Project docs win over personal docs on a domain-name conflict.
-->

## Quick Reference
Business context in 2-3 lines. The grain of the core table(s). Standard hygiene
filters every query in this domain should apply.

## Dimensions
How the key business concepts encode across tables (status codes, currency,
date semantics, soft-delete flags).

## Key Tables
For each canonical table: its grain, scope/exclusions, the join keys to reach it,
and when to use it (the usage trigger).

## Gotchas
Wrong-answer modes a senior analyst would warn a newcomer about. One bullet each.
(e.g. "Date is dirty mixed-format text; parse before any date math.")

## Best Practices
Preferred patterns, canonical metric definitions, columns to prefer/avoid.

## Cross-References
Related domains/docs and when to consult them.
```

- [ ] **Step 4: Write `docs/scent/examples/ecommerce_sales.md`**

```markdown
---
kind: scent
domain: ecommerce_sales
tables: [orders, order_items, customers, products]
confidence: verified
---

## Quick Reference
The ecommerce dataset tracks customer orders and their line items. Core grain:
`orders` is one row per placed order; `order_items` is one row per product within
an order. Exclude cancelled orders (`orders.status = 'cancelled'`) from revenue.

## Dimensions
- **Order status** encodes the lifecycle: `placed`, `shipped`, `delivered`, `cancelled`.
- **Money** is stored in minor units (cents) as integers — divide by 100 for dollars.

## Key Tables
- **orders** — grain: one row per order. Join to customers on `orders.customer_id = customers.id`.
- **order_items** — grain: one row per product line. Join to orders on `order_items.order_id = orders.id`;
  to products on `order_items.product_id = products.id`.
- **customers** — grain: one row per customer (`id`).
- **products** — grain: one row per product (`id`).

## Gotchas
- Revenue lives in `order_items` (price × quantity), NOT a single column on `orders`.
- Amounts are in cents; forgetting to divide by 100 inflates every dollar figure 100×.
- Cancelled orders still have rows — filter `status <> 'cancelled'` for revenue questions.

## Best Practices
- Net revenue = `SUM(order_items.price * order_items.quantity) / 100.0` over non-cancelled orders.
- Always confirm the join cardinality with `verify_join` before trusting an orders↔order_items join.

## Cross-References
- For schema/grain confirmation, run `profile_dataset` first; this doc states intent, the profiler states fact.
```

> Note: this example targets the `tests/fixtures/sample_dbs/ecommerce.duckdb` domain (non-benchmark). Adjust column names to match the fixture if they differ — the test only asserts retrievability and section presence, not exact column names, so it stays green either way, but keep the content honest to the fixture.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_scent_example_doc.py -q`
Expected: PASS (all three tests).

- [ ] **Step 6: Full gate**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: clean + all green (full suite, ~578+ tests).

- [ ] **Step 7: Commit**

```bash
git add docs/scent/TEMPLATE.md docs/scent/examples/ecommerce_sales.md \
        tests/unit/test_scent_example_doc.py
git commit -m "feat(scent): ship reference-doc template + ecommerce worked example

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- §4 components: `maze/__init__` + `_lexical` (T1), `document.py` (T2), `store.py` (T3), tool (T4); link_schema refactor (T1). ✅
- §5 data model (frontmatter, sections, graceful parse, derived scope): T2. ✅
- §6 store & precedence (two layers, project wins, `LABRAT_MAZE_DIR`, dedup-by-domain, profile segment): T3. ✅
- §7 retrieval engine (section scoring `2·heading+body`, group, QR-prepend, top_k, empty→[]): T4. ✅
- §8 tool I/O contract (`question`, `top_k`, `_Output`/`DocResult`/`SectionMatch`, no `database`): T4. ✅
- §9 system-prompt router (new Workflow step 1 + Tool Usage bullet): T5. ✅
- §10 benchmark safety (empty-store no-op test, non-benchmark example): T4 (`test_empty_store_is_a_noop`) + T6. ✅
- §11 shipped content (TEMPLATE + example): T6. ✅
- §12 testing plan (document/store/lexical/scoring/tool/registry): T1–T6. ✅
- Registration reaches MCP/agent/TUI: T5 registers in `build_data_tools_registry()`, the shared entry point. ✅

**2. Placeholder scan** — no "TBD"/"add error handling"/"similar to Task N"; every code step shows complete code; every run step shows the exact command + expected result. The `<short_domain_slug>` etc. in `TEMPLATE.md` are intentional author-fill placeholders in *shipped content*, not plan gaps. ✅

**3. Type consistency** — `question_tokens`/`stem`/`name_tokens` named identically across T1→T4; `ScentDoc`/`Section`/`parse_document(text, *, domain, scope)` identical across T2→T3→T4→T6; `MazeStore(project_root, home, profile)` + `from_env(profile)` + `docs(kind)` identical across T3→T4→T6; `_Output.results`, `DocResult.{domain,quick_reference,sections}`, `SectionMatch.{heading,body,score,matched_terms}` consistent T4→T6. ✅

**Cross-task note (intentional, flagged in the plan):** `test_registered_in_data_tools_registry` is authored in T4 but only passes after T5 — documented in T4 Step 5/Step 6 so the executor expects one red test in that window.
