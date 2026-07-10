# Cartographer Attached-DB Grounding + Code/Name Detection + Beefed-Up Semantics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Cartographer's DB-coverage blind spot (attached Postgres/SQLite are never profiled), add a deterministic code/name-pair detector, and ground + prune the LLM semantics author so a re-ablation of semantic Scent is finally a valid test.

**Architecture:** Four units land in phase order **C2 (detector) → C1 (attached-DB profiling) → C3+C4.1 (instruction) → C4.2 (prune)**. The Cartographer *core* (`generate_scent`/`cartograph_prepass`) profiles whatever connections it is handed and never learns about `AttachSpec`; the DAB layer (`env.py`/`suite.py`) builds throw-away `:memory:` DuckDB profiling connections that ATTACH each secondary DB and hand *merged copies* of the connection/catalog dicts to the prepass, leaving the benchmarked agent's `ctx.connections` DuckDB-only. C2 adds one deterministic `build_code_name_notes` section to every doc; C3/C4.1 tighten `_SEMANTICS_INSTRUCTION`; C4.2 adds a bounded fail-open self-critique pass after `draft_semantics`.

**Tech Stack:** Python 3.12, DuckDB (`ATTACH ... (TYPE SQLITE|POSTGRES)`), Polars, Pydantic, pytest (`asyncio_mode="auto"`), ruff, pyright (strict on `src/labrat/` except `dspy_opt/` and `screens/`).

## Global Constraints

Copied verbatim from the spec's non-negotiables — every task's requirements implicitly include these:

- The benchmarked **agent runtime is unchanged** — the re-ablation baseline must stay comparable to the old 0.773; C1 touches only the Cartographer's profiling path, **never** the agent's `ctx.connections`.
- **GT-firewall preserved** — the Cartographer reads only DB metadata + sampled rows, never validator/answer-key files.
- Every frozen doc still passes `audit_scent_doc` (**fail-loud**).
- The **deterministic path stays default** (`with_semantics=False`).
- **C1 adds nothing for DuckDB-only datasets** (byte-identical w.r.t. C1), while **C2 deliberately adds a code/name section where a code+name pair exists** (an intentional deterministic-output change, updated golden tests). A DuckDB-only dataset with **no** code/name pair remains byte-identical.
- **Semantics stays default-off** until the re-ablation proves it net-positive.

**Repo gates (run in this order before every commit — CI enforces all):**
```bash
uv run ruff format .   # must run first
uv run ruff check .    # must be clean
uv run pyright         # must be clean
uv run pytest -q       # must pass
```

**Concrete engineering decision resolving the `introspect_catalog`-vs-attach risk (verified 2026-07-04):** DuckDB's `information_schema.schemata` + `current_database()` are scoped to the *current* catalog, so `introspect_catalog()` on a `:memory:` connection does **not** list an ATTACHed catalog's schemas, and the Cartographer's `build_dimensions`/`build_code_name_notes` sample via the **bare** table name (`FROM clinical_info`), which only resolves inside the current catalog. Therefore each profiling connection ATTACHes exactly **one** secondary DB and immediately runs `USE <alias>`, making that catalog current — after which `introspect_catalog()` returns its tables (schema `main`, database_name `<alias>`) **and** every bare-name sample resolves. This keeps the Cartographer core untouched (no qualified-name changes to `build_dimensions`). The attached-DB doc's `domain` is `<alias>`, so retrieval and the Quick-Reference header carry the alias the agent attached under. Verified: `ATTACH ... (TYPE SQLITE)` auto-loads the sqlite extension (no explicit `INSTALL/LOAD`), and a bogus path raises on `attach()` (skippable).

---

## File Structure

- `src/labrat/maze/cartographer.py` — **modify**: add `build_code_name_notes` + `_confirms_code_name` (C2), wire into `generate_scent`; edit `_SEMANTICS_INSTRUCTION` (C3+C4.1); add `prune_unsupported` + `_PRUNE_INSTRUCTION` and wire into `generate_scent` (C4.2).
- `src/labrat/db/duckdb_engine.py` — **modify**: add public `use_database(alias)` (C1 needs it to switch catalogs without private access).
- `src/labrat/eval/benchmarks/dab/env.py` — **modify**: add module logger + `build_profiling_connections` (C1).
- `src/labrat/eval/benchmarks/dab/suite.py` — **modify**: `_run_cartographer` builds/merges/disconnects profiling connections (C1).
- Tests (new): `tests/unit/test_cartographer_code_name.py`, `tests/unit/test_dab_profiling_connections.py`, `tests/unit/test_cartographer_prune.py`. Tests (extend): `tests/unit/test_cartographer_generate.py`, `tests/unit/test_dab_cartographer.py`, `tests/unit/test_cartographer_semantics.py`, `tests/unit/test_cartographer_audit.py`.

---

## Phase 1 — C2 deterministic code/name detector

### Task 1: `build_code_name_notes` detector (pure, fixture-tested)

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (add `_CODE_NAME_SAMPLE`, `_confirms_code_name`, `build_code_name_notes`)
- Test: `tests/unit/test_cartographer_code_name.py` (create)

**Interfaces:**
- Consumes: `ProfileOutput` (the `_Output` alias already imported in `cartographer.py`), `labrat.db.base.Connection`, and from `labrat.maze.semantic_claims`: `_looks_like_code(values: list[str]) -> float`, `_SHAPE_THRESHOLD = 0.6`, `_NAME_CEILING = 0.4`.
- Produces: `build_code_name_notes(profile: ProfileOutput, conn: Connection) -> Section | None` — a `Section(heading="Code Columns", ..., source="verified")` naming each confirmed code column as the grouping key, or `None` when nothing is confirmed. `_confirms_code_name(conn: Connection, table: str, code_col: str, name_col: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_cartographer_code_name.py`:

```python
"""Deterministic code/name-pair detector (C2)."""

from __future__ import annotations

from typing import cast

import duckdb

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.agent.tools.profile_dataset import _Output as ProfileOutput  # pyright: ignore[reportPrivateUsage]
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import build_code_name_notes


async def _profile(conn: DuckDBConnection, db: str = "main") -> ProfileOutput:
    ctx = ToolContext(
        connections={db: conn},
        catalogs={db: conn.introspect_catalog()},
        primary=db,
    )
    tool = ProfileDatasetTool()
    return await tool.execute(ctx, tool.input_model(database=db, sample_rows=0, max_tables=10_000))


def _conn(path: str, ddl: list[str]) -> DuckDBConnection:
    raw = duckdb.connect(path)
    for stmt in ddl:
        raw.execute(stmt)
    raw.close()
    c = DuckDBConnection(path=path, read_only=False)
    c.connect()
    return c


async def test_code_name_pair_names_code_column(tmp_path) -> None:
    conn = _conn(
        str(tmp_path / "a.duckdb"),
        [
            "CREATE TABLE clinical_info(icd_o_3_histology VARCHAR, histological_type VARCHAR)",
            "INSERT INTO clinical_info VALUES "
            "('9400/3','Astrocytoma'),('9401/3','Astrocytoma'),"
            "('9450/3','Oligodendroglioma'),('9382/3','Oligoastrocytoma')",
        ],
    )
    try:
        section = build_code_name_notes(await _profile(conn), conn)
    finally:
        conn.disconnect()
    assert section is not None
    assert section.heading == "Code Columns"
    assert section.source == "verified"
    assert "icd_o_3_histology" in section.body  # the code column is the grouping key
    assert "histological_type" in section.body  # named as the display label


async def test_name_only_table_emits_nothing(tmp_path) -> None:
    conn = _conn(
        str(tmp_path / "b.duckdb"),
        [
            "CREATE TABLE city(name VARCHAR)",
            "INSERT INTO city VALUES ('London'),('Paris'),('Berlin')",
        ],
    )
    try:
        section = build_code_name_notes(await _profile(conn), conn)
    finally:
        conn.disconnect()
    assert section is None


async def test_two_code_shaped_columns_are_ambiguous_and_dropped(tmp_path) -> None:
    # both columns are code-shaped -> neither qualifies as the display-name column -> drop
    conn = _conn(
        str(tmp_path / "c.duckdb"),
        [
            "CREATE TABLE pair(a VARCHAR, b VARCHAR)",
            "INSERT INTO pair VALUES ('9400/3','X12'),('9401/3','X13'),('9450/3','X14')",
        ],
    )
    try:
        section = build_code_name_notes(await _profile(conn), conn)
    finally:
        conn.disconnect()
    assert section is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cartographer_code_name.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_code_name_notes'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/labrat/maze/cartographer.py`, add after `build_dimensions` (after its `return` on ~line 294):

```python
_CODE_NAME_SAMPLE = 200


def _confirms_code_name(conn: Connection, table: str, code_col: str, name_col: str) -> bool:
    """True iff (a) each code maps to <=1 name (functional dependency code->name) AND
    (b) grouping by the name collapses distinct codes (fewer names than codes). Any probe
    error or ambiguity -> False (conservative: a wrong note is the failure to avoid)."""
    try:
        multi = conn.execute(
            f"SELECT COUNT(*) FROM (SELECT {code_col} FROM {table} "
            f"WHERE {code_col} IS NOT NULL AND {name_col} IS NOT NULL "
            f"GROUP BY {code_col} HAVING COUNT(DISTINCT {name_col}) > 1) q"
        ).row(0)[0]
        if multi is None or int(multi) > 0:
            return False
        counts = conn.execute(
            f"SELECT COUNT(DISTINCT {code_col}), COUNT(DISTINCT {name_col}) FROM {table} "
            f"WHERE {code_col} IS NOT NULL AND {name_col} IS NOT NULL"
        ).row(0)
    except Exception:
        return False
    n_code, n_name = counts[0], counts[1]
    if n_code is None or n_name is None:
        return False
    return int(n_code) > int(n_name)


def build_code_name_notes(profile: ProfileOutput, conn: Connection) -> Section | None:
    """Deterministic detector: per table, find a code column (code-shaped values) paired
    with a display-name column (name-shaped, functionally determined by the code) and warn
    that grouping/filtering must use the code column. Conservative: emits nothing when
    ambiguous. Reuses the code-shape scorers from semantic_claims (verifier -> detector)."""
    from labrat.maze.semantic_claims import (
        _NAME_CEILING,  # pyright: ignore[reportPrivateUsage]
        _SHAPE_THRESHOLD,  # pyright: ignore[reportPrivateUsage]
        _looks_like_code,  # pyright: ignore[reportPrivateUsage]
    )

    lines: list[str] = []
    for t in profile.tables:
        scores: dict[str, float] = {}
        for col in t.columns:
            if not _is_stringy(col.data_type):
                continue
            try:
                df = conn.execute(
                    f"SELECT DISTINCT {col.name} FROM {t.name} "
                    f"WHERE {col.name} IS NOT NULL LIMIT {_CODE_NAME_SAMPLE}"
                )
            except Exception:
                continue
            vals = [str(r[0]) for r in df.iter_rows()]
            if vals:
                scores[col.name] = _looks_like_code(vals)
        code_cols = [c for c, s in scores.items() if s >= _SHAPE_THRESHOLD]
        name_cols = [c for c, s in scores.items() if s <= _NAME_CEILING]
        for code_col in code_cols:
            for name_col in name_cols:
                if name_col == code_col or scores[code_col] <= scores[name_col]:
                    continue
                if _confirms_code_name(conn, t.name, code_col, name_col):
                    lines.append(
                        f"- For coded values in `{t.name}`, group/filter by `{code_col}` "
                        f"(the code); `{name_col}` is the display label — grouping by the "
                        f"name column collapses distinct codes."
                    )
                    break  # one note per code column (conservative)
    if not lines:
        return None
    return Section(heading="Code Columns", body="\n".join(lines), source="verified")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_code_name.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_code_name.py
git commit -m "feat(cartographer): deterministic code/name-pair detector (C2)"
```

---

### Task 2: Wire `build_code_name_notes` into `generate_scent`

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (`generate_scent` per-connection section list, ~line 458-462)
- Test: `tests/unit/test_cartographer_generate.py` (extend)

**Interfaces:**
- Consumes: `build_code_name_notes(profile, conn) -> Section | None` (Task 1).
- Produces: `generate_scent(...)` now appends a `Code Columns` section to a doc **iff** a code/name pair is confirmed; runs on the `with_semantics=False` deterministic path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cartographer_generate.py`:

```python
async def test_code_name_section_present_on_deterministic_path(tmp_path) -> None:
    import duckdb

    from labrat.db.duckdb_engine import DuckDBConnection

    p = str(tmp_path / "clinical.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE clinical_info(icd_o_3_histology VARCHAR, histological_type VARCHAR)")
    raw.execute(
        "INSERT INTO clinical_info VALUES "
        "('9400/3','Astrocytoma'),('9401/3','Astrocytoma'),"
        "('9450/3','Oligodendroglioma'),('9382/3','Oligoastrocytoma')"
    )
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        docs = await generate_scent(
            connections={"clin": conn},
            catalogs={"clin": conn.introspect_catalog()},
            primary="clin",
            with_semantics=False,
        )
    finally:
        conn.disconnect()
    headings = {s.heading for s in docs[0].sections}
    assert "Code Columns" in headings
    body = next(s.body for s in docs[0].sections if s.heading == "Code Columns")
    assert "icd_o_3_histology" in body


async def test_no_code_name_section_when_no_pair(tmp_path) -> None:
    # byte-identity w.r.t. C2: a DuckDB dataset with no code/name pair gets no new section
    import duckdb

    from labrat.db.duckdb_engine import DuckDBConnection

    p = str(tmp_path / "plain.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE city(id INTEGER, name VARCHAR)")
    raw.execute("INSERT INTO city VALUES (1,'London'),(2,'Paris'),(3,'Berlin')")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        docs = await generate_scent(
            connections={"c": conn},
            catalogs={"c": conn.introspect_catalog()},
            primary="c",
            with_semantics=False,
        )
    finally:
        conn.disconnect()
    assert "Code Columns" not in {s.heading for s in docs[0].sections}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cartographer_generate.py -k "code_name" -v`
Expected: FAIL — `test_code_name_section_present_on_deterministic_path` fails on `assert "Code Columns" in headings` (section not yet wired).

- [ ] **Step 3: Write the minimal implementation**

In `generate_scent`, after the `build_dimensions(...)` append block (the `sections.append(build_dimensions(...))` around line 458-462) and before `doc = ScentDoc(...)`, insert:

```python
        cn = build_code_name_notes(profile, cast(Connection, conn))
        if cn is not None:
            sections.append(cn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_generate.py -v`
Expected: PASS (all, including the pre-existing generate tests).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_generate.py
git commit -m "feat(cartographer): emit Code Columns section on deterministic path (C2 wiring)"
```

---

## Phase 2 — C1 attached-DB profiling

### Task 3: `build_profiling_connections` + `use_database`

**Files:**
- Modify: `src/labrat/db/duckdb_engine.py` (add `use_database`)
- Modify: `src/labrat/eval/benchmarks/dab/env.py` (add module logger + `build_profiling_connections`)
- Test: `tests/unit/test_dab_profiling_connections.py` (create)

**Interfaces:**
- Consumes: `DuckDBConnection(path, read_only)`, `.connect()`, `.attach(path, alias, db_type)`, `.introspect_catalog()`, `.disconnect()`; `AttachSpec{alias, path, db_type}`.
- Produces: `DuckDBConnection.use_database(self, alias: str) -> None` (switches the current catalog to an attached one). `build_profiling_connections(attachable: list[AttachSpec]) -> tuple[dict[str, object], dict[str, object]]` — returns `(connections, catalogs)` keyed by `spec.alias`, each a connected `:memory:` DuckDB with the one secondary ATTACHed + `USE`d; a failed attach is skipped with a logged warning (never raises).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dab_profiling_connections.py`:

```python
"""C1: build_profiling_connections profiles attached SQLite/Postgres for the cartographer."""

from __future__ import annotations

import logging
import sqlite3
from typing import cast

from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.eval.benchmarks.dab.env import AttachSpec, build_profiling_connections


def _sqlite_clinical(path: str) -> None:
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE clinical_info(icd_o_3_histology TEXT, histological_type TEXT)")
    for code, name in [
        ("9400/3", "Astrocytoma"),
        ("9401/3", "Astrocytoma"),
        ("9450/3", "Oligodendroglioma"),
        ("9382/3", "Oligoastrocytoma"),
    ]:
        c.execute("INSERT INTO clinical_info VALUES (?,?)", (code, name))
    c.commit()
    c.close()


def test_profiles_attached_sqlite(tmp_path) -> None:
    spath = str(tmp_path / "sec.sqlite")
    _sqlite_clinical(spath)
    conns, cats = build_profiling_connections(
        [AttachSpec(alias="sec", path=spath, db_type="sqlite")]
    )
    try:
        assert set(conns) == {"sec"}
        cat = cast(Catalog, cats["sec"])
        names = [t.name for s in cat.schemas for t in s.tables]
        assert "clinical_info" in names
        # USE <alias> applied -> bare-name query resolves against the attached catalog
        conn = cast(DuckDBConnection, conns["sec"])
        assert conn.execute("SELECT COUNT(*) FROM clinical_info").item() == 4
    finally:
        for c in conns.values():
            cast(DuckDBConnection, c).disconnect()


def test_skips_bad_attach(tmp_path, caplog) -> None:
    spec = AttachSpec(alias="bad", path="/nonexistent_dir_xyz/nope.sqlite", db_type="sqlite")
    with caplog.at_level(logging.WARNING):
        conns, cats = build_profiling_connections([spec])
    assert conns == {}
    assert cats == {}
    assert any("bad" in r.getMessage() for r in caplog.records)  # warned, no exception
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_dab_profiling_connections.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_profiling_connections'`.

- [ ] **Step 3: Add `use_database` to `DuckDBConnection`**

In `src/labrat/db/duckdb_engine.py`, add after `attach` (after line 71):

```python
    def use_database(self, alias: str) -> None:
        """Switch the active database to an attached catalog so its tables are addressable
        by bare name — used by the cartographer's attached-DB profiling path so that its
        bare-name sampling (``FROM <table>``) resolves against the attached catalog."""
        if not alias.replace("_", "").isalnum():
            raise ValueError(f"alias must be alphanumeric/underscore: {alias!r}")
        self._connection.execute(f"USE {alias}")
```

- [ ] **Step 4: Add the logger + `build_profiling_connections` to `env.py`**

In `src/labrat/eval/benchmarks/dab/env.py`, add after the imports (after line 30, `from labrat.db.duckdb_engine import DuckDBConnection`):

```python
import logging

logger = logging.getLogger(__name__)
```

Then add at the end of the file (after `introspect_env_catalogs`):

```python
def build_profiling_connections(
    attachable: list[AttachSpec],
) -> tuple[dict[str, object], dict[str, object]]:
    """Build throw-away DuckDB profiling connections for the cartographer, one per
    attachable secondary DB (postgres|sqlite), WITHOUT touching the agent's ctx.

    Each spec gets a fresh ``:memory:`` DuckDBConnection that ATTACHes only that one
    secondary and runs ``USE <alias>`` so its tables are addressable by bare name (the
    cartographer's sampling idiom). Returns ``(connections, catalogs)`` keyed by
    ``spec.alias``. A failed attach (server down, missing file/extension) is skipped
    with a logged warning and never aborts the caller.
    """
    connections: dict[str, object] = {}
    catalogs: dict[str, object] = {}
    for spec in attachable:
        conn = DuckDBConnection(path=":memory:", read_only=False)
        try:
            conn.connect()
            conn.attach(spec.path, spec.alias, spec.db_type)
            conn.use_database(spec.alias)
            catalog = conn.introspect_catalog()
        except Exception as exc:
            logger.warning(
                "skipping attach for profiling (alias=%s, type=%s): %s",
                spec.alias,
                spec.db_type,
                exc,
            )
            try:
                conn.disconnect()
            except Exception:
                pass
            continue
        connections[spec.alias] = conn
        catalogs[spec.alias] = catalog
    return connections, catalogs
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dab_profiling_connections.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add src/labrat/db/duckdb_engine.py src/labrat/eval/benchmarks/dab/env.py tests/unit/test_dab_profiling_connections.py
git commit -m "feat(dab): build_profiling_connections for attached SQLite/Postgres (C1)"
```

---

### Task 4: Wire profiling connections into `_run_cartographer`

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` (import + `_run_cartographer`, lines 34, 388-409)
- Test: `tests/unit/test_dab_cartographer.py` (extend)

**Interfaces:**
- Consumes: `build_profiling_connections(env_spec.attachable) -> (dict, dict)` (Task 3); `cartograph_prepass(connections, catalogs, primary, scent_dir, *, with_semantics, llm_fn, variant_seed)`.
- Produces: `_run_cartographer` writes one Scent doc per attached DB (domain `<alias>`) alongside the DuckDB docs, and leaves `env_spec.ctx.connections`/`catalogs` DuckDB-only.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dab_cartographer.py`:

```python
def _sqlite_clinical(path: str) -> None:
    import sqlite3

    c = sqlite3.connect(path)
    c.execute("CREATE TABLE clinical_info(icd_o_3_histology TEXT, histological_type TEXT)")
    for code, name in [
        ("9400/3", "Astrocytoma"),
        ("9401/3", "Astrocytoma"),
        ("9450/3", "Oligodendroglioma"),
        ("9382/3", "Oligoastrocytoma"),
    ]:
        c.execute("INSERT INTO clinical_info VALUES (?,?)", (code, name))
    c.commit()
    c.close()


async def test_run_cartographer_profiles_attached_sqlite(tmp_path: Path) -> None:
    import duckdb

    from labrat.eval.benchmarks.dab.env import AttachSpec

    dpath = str(tmp_path / "main.duckdb")
    raw = duckdb.connect(dpath)
    raw.execute("CREATE TABLE t(id INTEGER, label VARCHAR)")
    raw.execute("INSERT INTO t VALUES (1,'a'),(2,'b')")
    raw.close()
    spath = str(tmp_path / "sec.sqlite")
    _sqlite_clinical(spath)

    conn = DuckDBConnection(path=dpath, read_only=True)
    ctx = ToolContext(
        connections={"main": conn},
        catalogs={"main": Catalog(database_name="main", schemas=[])},
        primary="main",
    )
    env = DabTaskEnv(
        ctx=ctx,
        attachable=[AttachSpec(alias="sec", path=spath, db_type="sqlite")],
        mongo=[],
    )
    maze_root = await _run_cartographer(env, "pancancer", tmp_path / "cache")
    scent = maze_root / "labrat_maze" / "scent"
    sec_doc = scent / "sec.md"
    assert sec_doc.exists(), "a Scent doc should be written for the attached DB"
    text = sec_doc.read_text()
    assert "Code Columns" in text  # C2 runs on the attached DB
    assert "icd_o_3_histology" in text
    # agent runtime untouched: ctx stays DuckDB-only
    assert set(env.ctx.connections) == {"main"}
    assert set(env.ctx.catalogs) == {"main"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_cartographer.py::test_run_cartographer_profiles_attached_sqlite -v`
Expected: FAIL — no `sec.md` written (attachables not yet profiled).

- [ ] **Step 3: Write the implementation**

In `src/labrat/eval/benchmarks/dab/suite.py`, extend the env import (line 34) to include the helper:

```python
from labrat.eval.benchmarks.dab.env import (
    DabTaskEnv,
    build_profiling_connections,
    introspect_env_catalogs,
)
```

Then replace the `_run_cartographer` body from the `ctx = env_spec.ctx` line through `return maze_root` (lines 388-409) with:

```python
    ctx = env_spec.ctx
    for conn in ctx.connections.values():
        connect = getattr(conn, "connect", None)
        if callable(connect):
            connect()
    prof_conns, prof_cats = build_profiling_connections(env_spec.attachable)
    try:
        introspect_env_catalogs(ctx)
        # Pass MERGED COPIES to the prepass; never mutate the agent's ctx (runtime stays
        # DuckDB-only). Attached-DB entries live only in these local dicts.
        merged_conns: dict[str, object] = {**ctx.connections, **prof_conns}
        merged_cats: dict[str, object] = {**ctx.catalogs, **prof_cats}
        await cartograph_prepass(
            merged_conns,
            merged_cats,
            ctx.primary,
            scent_dir,
            with_semantics=with_semantics,
            llm_fn=llm_fn,
            variant_seed=variant_seed,
        )
    finally:
        for conn in ctx.connections.values():
            disconnect = getattr(conn, "disconnect", None)
            if callable(disconnect):
                disconnect()
        for conn in prof_conns.values():
            disconnect = getattr(conn, "disconnect", None)
            if callable(disconnect):
                disconnect()
    return maze_root
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dab_cartographer.py -v`
Expected: PASS (new test + all pre-existing dab cartographer tests).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/suite.py tests/unit/test_dab_cartographer.py
git commit -m "feat(dab): profile attached DBs in _run_cartographer via merged copies (C1)"
```

---

## Phase 3 — C3 + C4.1 instruction changes

### Task 5: C3 — cohort-vs-filter rule in `_SEMANTICS_INSTRUCTION`

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (`_SEMANTICS_INSTRUCTION`, ~line 349-352)
- Test: `tests/unit/test_cartographer_semantics.py` (extend)

**Interfaces:**
- Consumes: `_SEMANTICS_INSTRUCTION` (module-level string).
- Produces: `_SEMANTICS_INSTRUCTION` now contains the cohort-vs-filter rule (substrings `the numerator` and `cohort denominator (the population)`).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_cartographer_semantics.py`:

```python
def test_instruction_has_cohort_vs_filter_rule() -> None:
    assert "the numerator" in _SEMANTICS_INSTRUCTION
    assert "cohort denominator (the population)" in _SEMANTICS_INSTRUCTION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_semantics.py::test_instruction_has_cohort_vs_filter_rule -v`
Expected: FAIL on `assert "the numerator" in _SEMANTICS_INSTRUCTION`.

- [ ] **Step 3: Write the implementation**

In `src/labrat/maze/cartographer.py`, edit the tail of `_SEMANTICS_INSTRUCTION`. Replace:

```python
    "## headings only; do not emit a ## Quick Reference, ## Dimensions, or ## Key Tables "
    "section (those are already verified)."
)
```

with:

```python
    "## headings only; do not emit a ## Quick Reference, ## Dimensions, or ## Key Tables "
    "section (those are already verified). "
    "COHORT VS FILTER: a quality/status filter (e.g. FILTER='PASS', a sequenced-only or "
    "is_test flag) scopes WHICH ROWS COUNT AS POSITIVE (the numerator); it must NEVER be "
    "authored as a Best Practice that narrows the cohort denominator (the population) — "
    "never restrict the population to a filtered subset."
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_semantics.py -v`
Expected: PASS (new test + pre-existing `test_instruction_forbids_unconditional_rules`).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_semantics.py
git commit -m "feat(cartographer): cohort-vs-filter rule in semantics instruction (C3)"
```

---

### Task 6: C4.1 — ground the author in verified facts

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (`_SEMANTICS_INSTRUCTION` tail, after Task 5's change)
- Test: `tests/unit/test_cartographer_semantics.py` (extend)

**Interfaces:**
- Consumes: `_SEMANTICS_INSTRUCTION` (as amended by Task 5).
- Produces: `_SEMANTICS_INSTRUCTION` now contains the grounding rule (substrings `annotator, not an inventor` and `NEVER introduce a claim`).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_cartographer_semantics.py`:

```python
def test_instruction_grounds_author_in_verified_facts() -> None:
    assert "annotator, not an inventor" in _SEMANTICS_INSTRUCTION
    assert "NEVER introduce a claim" in _SEMANTICS_INSTRUCTION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_semantics.py::test_instruction_grounds_author_in_verified_facts -v`
Expected: FAIL on `assert "annotator, not an inventor" in _SEMANTICS_INSTRUCTION`.

- [ ] **Step 3: Write the implementation**

In `src/labrat/maze/cartographer.py`, edit the tail of `_SEMANTICS_INSTRUCTION` (the lines added in Task 5). Replace:

```python
    "authored as a Best Practice that narrows the cohort denominator (the population) — "
    "never restrict the population to a filtered subset."
)
```

with:

```python
    "authored as a Best Practice that narrows the cohort denominator (the population) — "
    "never restrict the population to a filtered subset. "
    "GROUNDING: you are an annotator, not an inventor — build conditional routing guidance "
    "ON TOP OF the verified facts below and NEVER introduce a claim the verified facts do "
    "not support."
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_semantics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_semantics.py
git commit -m "feat(cartographer): ground semantics author in verified facts (C4.1)"
```

---

## Phase 4 — C4.2 self-critique / prune pass

### Task 7: `prune_unsupported` function (fail-open)

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (add `_PRUNE_INSTRUCTION`, `prune_unsupported`)
- Test: `tests/unit/test_cartographer_prune.py` (create)

**Interfaces:**
- Consumes: `ScentDoc`, `Section` (already imported), `render_document` (already imported), `LLMFn` (already imported).
- Produces: `prune_unsupported(skeleton: ScentDoc, prose: list[Section], llm_fn: LLMFn) -> list[Section]` — keeps only draft bullets echoed back by the critique; fail-open (any error, empty response, or unparseable result returns the original `prose` unchanged).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_cartographer_prune.py`:

```python
"""C4.2: bounded self-critique prune pass (fail-open)."""

from __future__ import annotations

from labrat.maze.cartographer import prune_unsupported
from labrat.maze.document import ScentDoc, Section


async def test_prune_drops_unsupported_bullet() -> None:
    skeleton = ScentDoc(
        domain="x",
        sections=[Section(heading="Key Tables", body="- t has column foo", source="verified")],
    )
    prose = [
        Section(
            heading="Gotchas",
            body="- WHEN X, use foo.\n- WHEN Y, use invented_col.",
            source="draft",
        )
    ]

    async def _llm(prompt: str) -> str:
        return "- WHEN X, use foo."

    kept = await prune_unsupported(skeleton, prose, _llm)
    body = "\n".join(s.body for s in kept)
    assert "WHEN X, use foo." in body
    assert "invented_col" not in body


async def test_prune_fail_open_on_error() -> None:
    skeleton = ScentDoc(domain="x", sections=[])
    prose = [Section(heading="Gotchas", body="- a\n- b", source="draft")]

    async def _boom(prompt: str) -> str:
        raise RuntimeError("llm down")

    kept = await prune_unsupported(skeleton, prose, _boom)
    assert kept == prose  # full draft kept on error


async def test_prune_fail_open_on_empty_response() -> None:
    skeleton = ScentDoc(domain="x", sections=[])
    prose = [Section(heading="Gotchas", body="- a\n- b", source="draft")]

    async def _empty(prompt: str) -> str:
        return "   "

    kept = await prune_unsupported(skeleton, prose, _empty)
    assert kept == prose  # unparseable / kept-nothing -> fail-open
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cartographer_prune.py -v`
Expected: FAIL with `ImportError: cannot import name 'prune_unsupported'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/labrat/maze/cartographer.py`, add after `draft_semantics` (after its `return prose, claims_text` on ~line 392):

```python
_PRUNE_INSTRUCTION = (
    "PRUNE PASS. Below are VERIFIED FACTS and a list of DRAFT BULLETS an author wrote. "
    "Return ONLY the draft bullets that are FULLY SUPPORTED by a verified fact, each on "
    "its own line, verbatim (copy the bullet text exactly, including the leading '- '). "
    "Drop any bullet that makes a claim the verified facts do not support. Output nothing "
    "but the kept bullet lines."
)


async def prune_unsupported(
    skeleton: ScentDoc, prose: list[Section], llm_fn: LLMFn
) -> list[Section]:
    """Self-critique prune: ask the LLM which drafted bullets are fully supported by a
    verified fact and keep only those (verbatim). Fail-open — any error, empty response,
    or unparseable result returns the original ``prose`` unchanged (never worse than the
    draft). Catches the T1c/M2 failure mode: a bullet that NAMES real columns but makes an
    unsupported claim (a vocabulary filter would miss it; the critique judges the claim)."""
    if not prose:
        return prose
    facts = render_document(skeleton)
    bullets = "\n".join(
        ln for s in prose for ln in s.body.splitlines() if ln.strip().startswith("-")
    )
    prompt = (
        f"{_PRUNE_INSTRUCTION}\n\n--- VERIFIED FACTS ---\n{facts}\n--- END FACTS ---\n"
        f"--- DRAFT BULLETS ---\n{bullets}\n--- END BULLETS ---\n"
    )
    try:
        raw = await llm_fn(prompt)
    except Exception:
        return prose  # fail-open on error
    kept = {ln.strip() for ln in raw.splitlines() if ln.strip().startswith("-")}
    if not kept:
        return prose  # fail-open: empty / unparseable / kept-nothing
    result: list[Section] = []
    for s in prose:
        new_lines = [
            ln
            for ln in s.body.splitlines()
            if not ln.strip().startswith("-") or ln.strip() in kept
        ]
        body = "\n".join(new_lines).strip()
        if body:
            result.append(Section(heading=s.heading, body=body, source=s.source))
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_prune.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_prune.py
git commit -m "feat(cartographer): self-critique prune pass, fail-open (C4.2)"
```

---

### Task 8: Wire `prune_unsupported` into `generate_scent`

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (`generate_scent` with_semantics branch, ~line 473)
- Test: `tests/unit/test_cartographer_generate.py` (extend)

**Interfaces:**
- Consumes: `prune_unsupported(skeleton, prose, llm_fn)` (Task 7); `draft_semantics(doc, llm_fn) -> (prose, raw_claims)`.
- Produces: `generate_scent` prunes drafted prose (via the same `llm_fn`) before merging when `with_semantics=True`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_cartographer_generate.py`:

```python
async def test_generate_prunes_unsupported_draft_bullets(tmp_path) -> None:
    import duckdb

    from labrat.db.duckdb_engine import DuckDBConnection

    p = str(tmp_path / "prune.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(id INTEGER, label VARCHAR)")
    raw.execute("INSERT INTO t VALUES (1,'a'),(2,'b')")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()

    async def _llm(prompt: str) -> str:
        if "PRUNE PASS" in prompt:  # the prune call keeps only the supported bullet
            return "- WHEN the question asks for a label, read t.label."
        return (  # the draft call emits one supported + one unsupported bullet
            "## Gotchas\n"
            "- WHEN the question asks for a label, read t.label.\n"
            "- Revenue always excludes fabricated_flag rows.\n"
        )

    try:
        docs = await generate_scent(
            connections={"d": conn},
            catalogs={"d": conn.introspect_catalog()},
            primary="d",
            with_semantics=True,
            llm_fn=_llm,
        )
    finally:
        conn.disconnect()
    body = "\n".join(s.body for s in docs[0].sections)
    assert "read t.label" in body  # supported bullet kept
    assert "fabricated_flag" not in body  # unsupported bullet pruned
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_generate.py::test_generate_prunes_unsupported_draft_bullets -v`
Expected: FAIL on `assert "fabricated_flag" not in body` (prune not yet wired; both bullets survive).

- [ ] **Step 3: Write the implementation**

In `src/labrat/maze/cartographer.py`, in the `with_semantics and llm_fn is not None` branch of `generate_scent`, replace:

```python
            prose, raw_claims = await draft_semantics(doc, llm_fn)
            claims = parse_semantic_claims(raw_claims)
```

with:

```python
            prose, raw_claims = await draft_semantics(doc, llm_fn)
            prose = await prune_unsupported(doc, prose, llm_fn)  # C4.2 self-critique prune
            claims = parse_semantic_claims(raw_claims)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_generate.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_generate.py
git commit -m "feat(cartographer): prune drafted prose before merge in generate_scent (C4.2 wiring)"
```

---

## Phase 5 — regression + byte-identity + audit fail-loud + gates

### Task 9: Full regression, audit fail-loud on code/name + semantics doc, and gates

**Files:**
- Test: `tests/unit/test_cartographer_audit.py` (extend — audit stays fail-loud with C2 + prune in the pipeline)

**Interfaces:**
- Consumes: `generate_scent(..., with_semantics=True, llm_fn=...)`, `ScentContaminationError`.
- Produces: no new production code — a regression gate confirming the full pipeline (C1+C2+C3+C4.1+C4.2) keeps `audit_scent_doc` fail-loud on a doc that now includes code/name content.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_cartographer_audit.py`:

```python
async def test_audit_still_fail_loud_with_code_name_and_prune(tmp_path) -> None:
    # Full pipeline: a doc with a C2 Code Columns section + a leaky drafted/pruned bullet
    # must still raise at the freeze-time audit (GT-firewall preserved).
    import duckdb

    p = str(tmp_path / "clinical.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE clinical_info(icd_o_3_histology VARCHAR, histological_type VARCHAR)")
    raw.execute(
        "INSERT INTO clinical_info VALUES "
        "('9400/3','Astrocytoma'),('9401/3','Astrocytoma'),"
        "('9450/3','Oligodendroglioma'),('9382/3','Oligoastrocytoma')"
    )
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()

    async def _leaky(prompt: str) -> str:
        # returned for BOTH the draft and the PRUNE call (prune echoes it back as kept)
        return "## Gotchas\n- The ground truth answer for revenue is 12345."

    try:
        with pytest.raises(ScentContaminationError):
            await generate_scent(
                connections={"clin": conn},
                catalogs={"clin": conn.introspect_catalog()},
                primary="clin",
                with_semantics=True,
                llm_fn=_leaky,
            )
    finally:
        conn.disconnect()
```

- [ ] **Step 2: Run test to verify it passes (or exposes a regression)**

Run: `uv run pytest tests/unit/test_cartographer_audit.py -v`
Expected: PASS — the doc now also carries a `Code Columns` section, and the leaky bullet survives draft+prune (prune echoes it back) but is caught by `audit_scent_doc`, which raises `ScentContaminationError`.

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS (all ~670+ tests). If a pre-existing golden test asserted a fixed set of section headings for a fixture that happens to contain a code/name pair, update it deliberately to include `Code Columns` (an intentional C2 output change — note it in the commit message).

- [ ] **Step 4: Run the format / lint / type gates**

Run:
```bash
uv run ruff format .
uv run ruff check .
uv run pyright
```
Expected: `ruff format` reports files unchanged (or reformats and you re-stage); `ruff check` clean; `pyright` clean (0 errors).

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_cartographer_audit.py
git commit -m "test(cartographer): audit stays fail-loud with C2 + prune; Phase 5 regression gate"
```

---

## Self-Review

**1. Spec coverage**

| Spec unit / requirement | Task(s) |
|---|---|
| C1 — profile attached Postgres/SQLite (`build_profiling_connections`) | Task 3 |
| C1 — wire into `_run_cartographer`, merged copies, agent ctx untouched | Task 4 |
| C1 — failed attach skipped with logged warning | Task 3 (`test_skips_bad_attach`) |
| C1 — `introspect_catalog`-vs-attach resolution (USE alias) | Global Constraints "concrete decision" + Task 3 impl |
| C2 — deterministic code/name detector (`build_code_name_notes`) | Task 1 |
| C2 — repurpose semantic_claims shape helpers (no regex dup) | Task 1 (imports `_looks_like_code`/`_SHAPE_THRESHOLD`/`_NAME_CEILING`) |
| C2 — conservative drop (name-only, reversed/ambiguous) | Task 1 (`test_name_only...`, `test_two_code_shaped...`) |
| C2 — runs on `with_semantics=False` path | Task 2 (`test_code_name_section_present_on_deterministic_path`) |
| C2 — byte-identity when no pair | Task 2 (`test_no_code_name_section_when_no_pair`) |
| C2 — runs on attached DBs | Task 4 (`Code Columns` in `sec.md`) |
| C3 — cohort-vs-filter rule | Task 5 |
| C4.1 — ground author in verified facts | Task 6 |
| C4.2 — self-critique prune, fail-open | Task 7 |
| C4.2 — wire into `generate_scent` | Task 8 |
| Regression / audit fail-loud / gates | Task 9 |

No spec unit is unaddressed. (Postgres end-to-end profiling is exercised by the post-build re-ablation against DAB-local PG, per the spec's testing note — no unit-test PG server assumed.)

**2. Placeholder scan:** No `TBD`/`TODO`/"add error handling"/"similar to Task N". Every code step shows complete code; every edit shows exact old→new strings.

**3. Type consistency:**
- `build_code_name_notes(profile: ProfileOutput, conn: Connection) -> Section | None` — same name/signature in Task 1 (def), Task 2 (call), Task 4 (via attached-DB doc).
- `_confirms_code_name(conn, table, code_col, name_col) -> bool` — Task 1 only.
- `use_database(alias: str) -> None` — Task 3 (def in `duckdb_engine.py`, call in `build_profiling_connections`).
- `build_profiling_connections(attachable: list[AttachSpec]) -> tuple[dict[str, object], dict[str, object]]` — Task 3 (def), Task 4 (call). Return-dict value type `object` matches `cartograph_prepass`/`generate_scent`'s `connections: dict[str, object]` / `catalogs: dict[str, object]` parameters.
- `prune_unsupported(skeleton: ScentDoc, prose: list[Section], llm_fn: LLMFn) -> list[Section]` — Task 7 (def), Task 8 (call). `LLMFn` is the alias already imported in `cartographer.py`.
- `_SEMANTICS_INSTRUCTION` amended additively in Task 5 then Task 6 (Task 6's `old_string` includes Task 5's added lines, so the edits compose in order).

No inconsistencies found.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-04-cartographer-attached-db-grounding.md`.

**Post-build (not a plan task):** run the single re-ablation described in the spec §Re-ablation — 5-dataset subset (`deps_dev_v1, music_brainz_20k, stockindex, pancancer_atlas, yelp`), Sonnet 5, claude-mcp, `--hints`, n=3, arms `baseline-fixed` (deterministic + C1 + C2) and `semantics-fixed` (+ C3 + C4) vs the old 0.773 baseline. Keep-if-net-positive; both stay default-off until proven. Primary signal: pancancer:1 recovering (emits the code column) and pancancer:3 not regressing.
