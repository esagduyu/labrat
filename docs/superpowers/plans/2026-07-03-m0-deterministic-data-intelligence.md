# M0 — Deterministic Data-Intelligence Pack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship seven deterministic, GT-firewalled data-intelligence features — `check_sql`, `normalize_text` macro, three Cartographer enrichments, a `top_n_with_ties` lever, and a taint-audit submission gate.

**Architecture:** One new tool (`check_sql`) + one DuckDB session macro (`normalize_text`) + three new/extended Cartographer section builders wired into `generate_scent` + one lever line + one eval-infra gate. All deterministic (no LLM), all unit-testable with fixtures.

**Tech Stack:** Python 3.12, Pydantic, `sqlglot` (already a dep, v30.8.0), `difflib` (stdlib), DuckDB, pytest (`asyncio_mode=auto`), ruff, pyright strict.

## Global Constraints

- No LLM/API calls in any feature. GT-firewalled: read only DB metadata/sampled rows, never validator/answer-key files.
- Benchmark-safe: structure/process only, no answer content. Every Cartographer addition must still pass `audit_scent_doc`.
- `normalize_text` registers on DuckDB **only**, and must work on a **read-only** connection (`CREATE OR REPLACE TEMPORARY MACRO`).
- Tools: `name`/`description`/`input_model` are `@property` methods (not class attrs). Route via `ctx.connections[args.database or ctx.primary]`; catalog via `ctx.catalogs[...]`.
- `json.loads` results are `Unknown` under pyright strict — narrow/cast per the codebase convention.
- Before commit: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. All clean.
- Cartographer enrichments must be size-budgeted (bounded output) and leave existing `build_dimensions`/`build_key_tables` tests passing (add, threshold-gate — don't rewrite existing behavior).

---

## Phase 1 — `check_sql` tool

### Task 1: `check_sql` pre-execution validator

**Files:**
- Create: `src/labrat/agent/tools/check_sql.py`
- Modify: `src/labrat/agent/data_tools.py` (import + register, mirroring `VerifyJoinTool` at lines 24 / 52)
- Test: `tests/unit/test_check_sql.py`

**Interfaces:**
- Consumes: `Tool`/`ToolContext` (`agent/tools/base.py`); `Catalog`/`Schema`/`Table`/`Column` (`db/catalog.py` — `Catalog.schemas[].tables[].{name,columns[].name}`); `ctx.connections`/`ctx.catalogs`/`ctx.primary`.
- Produces: `CheckSqlTool` (name `"check_sql"`), `_Input{sql: str, database: str | None}`, `_Output{valid: bool, unknown_tables: list[_UnknownRef], unknown_columns: list[_UnknownCol], parse_error: str | None}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_check_sql.py
from __future__ import annotations

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.check_sql import CheckSqlTool
from labrat.db.catalog import Catalog, Column, Schema, Table


def _catalog() -> Catalog:
    orders = Table(name="orders", schema_name="main",
                   columns=[Column(name="id", data_type="INTEGER"),
                            Column(name="total", data_type="DOUBLE"),
                            Column(name="customer_id", data_type="INTEGER")])
    customers = Table(name="customers", schema_name="main",
                      columns=[Column(name="id", data_type="INTEGER"),
                               Column(name="name", data_type="VARCHAR")])
    return Catalog(database_name="main", schemas=[Schema(name="main", tables=[orders, customers])])


def _ctx() -> ToolContext:
    return ToolContext(connections={"main": object()}, catalogs={"main": _catalog()}, primary="main")


async def test_clean_sql_is_valid() -> None:
    out = await CheckSqlTool().execute(_ctx(), CheckSqlTool().input_model(
        sql="SELECT o.total FROM orders o JOIN customers c ON o.customer_id = c.id"))
    assert out.valid and not out.unknown_tables and not out.unknown_columns


async def test_typo_column_flagged_with_suggestion() -> None:
    out = await CheckSqlTool().execute(_ctx(), CheckSqlTool().input_model(
        sql="SELECT totl FROM orders"))
    assert not out.valid
    cols = {u.ref: u.suggestions for u in out.unknown_columns}
    assert "totl" in cols and "total" in cols["totl"]


async def test_unknown_table_flagged() -> None:
    out = await CheckSqlTool().execute(_ctx(), CheckSqlTool().input_model(sql="SELECT * FROM ordrs"))
    assert not out.valid
    assert any(u.ref == "ordrs" and "orders" in u.suggestions for u in out.unknown_tables)


async def test_ambiguous_unqualified_column_not_flagged() -> None:
    # 'id' exists in both orders and customers; unqualified -> ambiguous -> don't flag
    out = await CheckSqlTool().execute(_ctx(), CheckSqlTool().input_model(
        sql="SELECT id FROM orders JOIN customers ON orders.customer_id = customers.id"))
    assert not any(u.ref == "id" for u in out.unknown_columns)


async def test_malformed_sql_returns_parse_error_not_raise() -> None:
    out = await CheckSqlTool().execute(_ctx(), CheckSqlTool().input_model(sql="SELECT FROM WHERE"))
    assert out.valid is False and out.parse_error is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_check_sql.py -v`
Expected: FAIL — `No module named 'labrat.agent.tools.check_sql'`.

- [ ] **Step 3: Implement the tool**

```python
# src/labrat/agent/tools/check_sql.py
"""check_sql: deterministic pre-execution validation of table/column references.

Parses SQL with sqlglot and resolves every referenced table + column against the
catalog, returning unresolved refs with the closest real names (difflib). No
execution, no LLM. Catches wrong-table/typo'd-column before a warehouse round-trip.
"""

from __future__ import annotations

import difflib
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.catalog import Catalog

_SUGGEST_N = 3
_SUGGEST_CUTOFF = 0.6


class _UnknownRef(BaseModel):
    ref: str
    suggestions: list[str]


class _UnknownCol(BaseModel):
    table: str | None
    ref: str
    suggestions: list[str]


class _Input(BaseModel):
    sql: str = Field(description="The SQL to validate (not executed).")
    database: str | None = Field(default=None, description="Connection name; defaults to primary.")


class _Output(BaseModel):
    valid: bool
    unknown_tables: list[_UnknownRef]
    unknown_columns: list[_UnknownCol]
    parse_error: str | None


def _catalog_index(cat: Catalog) -> dict[str, set[str]]:
    """{table_name_lower: {column_name_lower, ...}} across all schemas."""
    idx: dict[str, set[str]] = {}
    for schema in cat.schemas:
        for t in schema.tables:
            idx.setdefault(t.name.lower(), set()).update(c.name.lower() for c in t.columns)
    return idx


def _suggest(ref: str, candidates: list[str]) -> list[str]:
    return difflib.get_close_matches(ref.lower(), candidates, n=_SUGGEST_N, cutoff=_SUGGEST_CUTOFF)


class CheckSqlTool(Tool[_Input]):
    @property
    def name(self) -> str:
        return "check_sql"

    @property
    def description(self) -> str:
        return (
            "Validate a SQL query's table and column references against the schema BEFORE "
            "running it. Returns any unknown tables/columns with the closest real names. "
            "Use this to catch typos and wrong table/column names without a failed query."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        cat = cast(Catalog, ctx.catalogs[args.database or ctx.primary])
        idx = _catalog_index(cat)
        all_tables = list(idx.keys())

        try:
            tree = sqlglot.parse_one(args.sql)
        except ParseError as e:
            return _Output(valid=False, unknown_tables=[], unknown_columns=[], parse_error=str(e))
        if tree is None:
            return _Output(valid=False, unknown_tables=[], unknown_columns=[],
                           parse_error="empty statement")

        # alias -> real table name, for tables in scope
        alias_map: dict[str, str] = {}
        in_scope: list[str] = []
        unknown_tables: list[_UnknownRef] = []
        for tbl in tree.find_all(exp.Table):
            tname = tbl.name
            if not tname:
                continue
            alias = tbl.alias or tname
            alias_map[alias.lower()] = tname.lower()
            if tname.lower() in idx:
                in_scope.append(tname.lower())
            else:
                unknown_tables.append(_UnknownRef(ref=tname, suggestions=_suggest(tname, all_tables)))

        unknown_cols: list[_UnknownCol] = []
        for col in tree.find_all(exp.Column):
            cname = col.name
            if not cname:
                continue
            qualifier = col.table  # alias or table, '' if unqualified
            if qualifier:
                real = alias_map.get(qualifier.lower())
                if real is None or real not in idx:
                    continue  # unknown/foreign qualifier — table already flagged or out of scope
                if cname.lower() not in idx[real]:
                    unknown_cols.append(_UnknownCol(table=real, ref=cname,
                                                    suggestions=_suggest(cname, sorted(idx[real]))))
            else:
                owners = [t for t in in_scope if cname.lower() in idx.get(t, set())]
                if len(owners) == 0 and in_scope:
                    pool = sorted({c for t in in_scope for c in idx.get(t, set())})
                    unknown_cols.append(_UnknownCol(table=None, ref=cname, suggestions=_suggest(cname, pool)))
                # len>=1 (resolved) or ambiguous(len>1) -> don't flag

        valid = not unknown_tables and not unknown_cols
        return _Output(valid=valid, unknown_tables=unknown_tables,
                       unknown_columns=unknown_cols, parse_error=None)
```

Register in `data_tools.py`: add `from labrat.agent.tools.check_sql import CheckSqlTool` next to the `verify_join` import (line 24) and `registry.register(CheckSqlTool())` next to line 52.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_check_sql.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/agent/tools/check_sql.py src/labrat/agent/data_tools.py tests/unit/test_check_sql.py
git commit -m "feat(tools): check_sql — deterministic pre-execution ref validation"
```

---

## Phase 2 — `normalize_text` macro

### Task 2: register `normalize_text` on DuckDB connect (read-only safe)

**Files:**
- Modify: `src/labrat/db/duckdb_engine.py` (`connect()`, ~line 36)
- Test: `tests/unit/test_normalize_text_macro.py`

**Interfaces:**
- Produces: every `DuckDBConnection` after `connect()` has a session macro `normalize_text(x)` returning lowercase + accent-stripped + non-alphanumeric-removed text. Works whether `read_only` is True or False.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_normalize_text_macro.py
from __future__ import annotations

from labrat.db.duckdb_engine import DuckDBConnection


def test_normalize_text_registered(tmp_path) -> None:
    p = str(tmp_path / "t.duckdb")
    conn = DuckDBConnection(path=p)  # match the real ctor
    conn.connect()
    df = conn.execute("SELECT normalize_text('Café  Del-Mar!') AS n")
    assert df.row(0)[0] == "cafedelmar"
    conn.disconnect()


def test_normalize_text_works_read_only(tmp_path) -> None:
    p = str(tmp_path / "t.duckdb")
    seed = DuckDBConnection(path=p); seed.connect()
    seed._connection.execute("CREATE TABLE x(a INT); INSERT INTO x VALUES (1)")
    seed.disconnect()
    ro = DuckDBConnection(path=p, read_only=True); ro.connect()  # macro must register on read-only
    assert ro.execute("SELECT normalize_text('  A B c ')").row(0)[0] == "abc"
    ro.disconnect()
```

(Confirm `DuckDBConnection`'s real constructor signature — `path=`, `read_only=` — from `db/duckdb_engine.py`; adjust the test to it.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_normalize_text_macro.py -v`
Expected: FAIL — `Catalog Error: Scalar Function "normalize_text" does not exist`.

- [ ] **Step 3: Implement in `connect()`**

```python
_NORMALIZE_TEXT_MACRO = (
    "CREATE OR REPLACE TEMPORARY MACRO normalize_text(x) AS "
    "regexp_replace(lower(strip_accents(CAST(x AS VARCHAR))), '[^a-z0-9]', '', 'g')"
)

    def connect(self) -> None:
        self._conn = duckdb.connect(self._path, read_only=self._read_only)
        # Session macro for diacritic/whitespace/case-insensitive matching. TEMPORARY so it
        # works even on a read-only database (temp objects live in an in-memory schema).
        self._conn.execute(_NORMALIZE_TEXT_MACRO)
```

(Place `_NORMALIZE_TEXT_MACRO` as a module-level constant. `strip_accents` and `regexp_replace` are native DuckDB functions.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_normalize_text_macro.py -v`
Expected: PASS (both, incl. read-only).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/db/duckdb_engine.py tests/unit/test_normalize_text_macro.py
git commit -m "feat(db): register normalize_text() TEMPORARY macro on DuckDB connect (read-only safe)"
```

---

## Phase 3 — Cartographer enrichments

### Task 3: join-transform detection (`build_join_keys`)

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (add `build_join_keys`; wire into `generate_scent` section list at ~line 243-247)
- Test: `tests/unit/test_cartographer_join_transforms.py`

**Interfaces:**
- Consumes: `_candidate_joins(profile)` (line 73), `discover_joins` result `list[VerifiedJoin]` (`.left`/`.right` as `"table.col"`), `Connection.execute` (returns a Polars DataFrame; `.row(0)[0]`), `Section` (`heading`/`body`/`source`).
- Produces: `build_join_keys(profile: ProfileOutput, conn: Connection, verified: list[VerifiedJoin]) -> Section | None` — a `## Join Keys` section listing, for each *unverified* candidate whose match-rate a deterministic transform lifts past `_JOIN_XFORM_MIN` (0.5), the normalization SQL for both sides; `None` if none found.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartographer_join_transforms.py
from __future__ import annotations

import duckdb
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.agent.tools.profile_dataset import ProfileDatasetTool  # for ProfileOutput type
from labrat.maze.cartographer import build_join_keys


def _conn(tmp_path):
    p = str(tmp_path / "j.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE customers(id VARCHAR, name VARCHAR)")
    raw.execute("INSERT INTO customers VALUES ('12345','a'),('67890','b')")
    raw.execute("CREATE TABLE orders(customer_id VARCHAR, amt INT)")
    raw.execute("INSERT INTO orders VALUES ('CUST-0012345',10),('CUST-0067890',20)")
    raw.close()
    c = DuckDBConnection(path=p); c.connect()
    return c


async def test_detects_extract_digits_transform(tmp_path) -> None:
    conn = _conn(tmp_path)
    # profile the two tables via the profiler so field shapes match generate_scent
    from labrat.agent.tools.base import ToolContext
    prof = await ProfileDatasetTool().execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        ProfileDatasetTool().input_model(sample_rows=0, max_tables=100))
    # orders.customer_id -> customers.id needs digit-extraction; raw match is 0
    section = build_join_keys(prof, conn, verified=[])
    assert section is not None
    assert "orders" in section.body and "customers" in section.body
    assert "[^0-9]" in section.body  # emits the extract-digits normalization SQL
    conn.disconnect()
```

(If `_candidate_joins` doesn't surface `customer_id -> customers.id` for this fixture via the `<base>_id` heuristic — `customer_id` → base `customer` → table `customers` (plural) → confirm the heuristic at cartographer.py:99-114 matches; the fixture uses `customers` so `base+"s"` hits. If needed, add a declared FK to the fixture.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_join_transforms.py -v`
Expected: FAIL — `build_join_keys` undefined.

- [ ] **Step 3: Implement**

```python
# add to src/labrat/maze/cartographer.py

_JOIN_XFORM_MIN = 0.5
# (label, SQL template applied to a column expr) — deterministic, symmetric on both sides
_JOIN_TRANSFORMS: list[tuple[str, str]] = [
    ("extract-digits", "regexp_replace(CAST({c} AS VARCHAR), '[^0-9]', '', 'g')"),
    ("lower-trim", "lower(trim(CAST({c} AS VARCHAR)))"),
    ("strip-leading-num", "regexp_replace(CAST({c} AS VARCHAR), '^[0-9]+[-.]', '')"),
]


def _match_rate(conn: Connection, lt: str, lexpr: str, rt: str, rexpr: str) -> float:
    def _scalar(sql: str) -> int:
        v = conn.execute(sql).row(0)[0]
        return int(v) if v is not None else 0

    denom = _scalar(f"SELECT COUNT(*) FROM {lt} WHERE {lexpr} IS NOT NULL")
    if denom == 0:
        return 0.0
    matched = _scalar(
        f"SELECT COUNT(*) FROM {lt} WHERE {lexpr} IN "
        f"(SELECT {rexpr} FROM {rt} WHERE {rexpr} IS NOT NULL)"
    )
    return matched / denom


def build_join_keys(
    profile: ProfileOutput, conn: Connection, verified: list[VerifiedJoin]
) -> Section | None:
    """For candidate joins that don't match raw, detect a normalizing transform and
    emit the exact normalization SQL. Deterministic (bounded COUNT probes)."""
    verified_pairs = {(j.left, j.right) for j in verified}
    lines: list[str] = []
    for lt, lc, rt, rc in _candidate_joins(profile):
        if (f"{lt}.{lc}", f"{rt}.{rc}") in verified_pairs:
            continue
        raw = _match_rate(conn, lt, lc, rt, rc)
        if raw >= 0.95:
            continue  # already clean; discover_joins handles it
        best: tuple[str, str, float] | None = None
        for label, tmpl in _JOIN_TRANSFORMS:
            lexpr, rexpr = tmpl.format(c=lc), tmpl.format(c=rc)
            try:
                rate = _match_rate(conn, lt, lexpr, rt, rexpr)
            except Exception:
                continue
            if rate >= _JOIN_XFORM_MIN and rate > raw and (best is None or rate > best[2]):
                best = (label, tmpl, rate)
        if best is not None:
            label, tmpl, rate = best
            lexpr, rexpr = tmpl.format(c=f"{lt}.{lc}"), tmpl.format(c=f"{rt}.{rc}")
            lines.append(
                f"- `{lt}.{lc}` ↔ `{rt}.{rc}` needs **{label}** "
                f"({round(rate * 100, 1)}% after transform). Join on: "
                f"`{lexpr} = {rexpr}`"
            )
    if not lines:
        return None
    return Section(heading="Join Keys", body="\n".join(lines), source="verified")
```

Wire into `generate_scent` (insert after `build_key_tables`, filtering None):

```python
        joins = await discover_joins(ctx, profile, database=name)
        sections = [
            build_quick_reference(profile),
            build_key_tables(profile, joins),
        ]
        jk = build_join_keys(profile, cast(Connection, conn), joins)
        if jk is not None:
            sections.append(jk)
        sections.append(build_dimensions(profile, cast(Connection, conn), cap=distinct_cap))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_join_transforms.py tests/unit/test_dab_cartographer.py -v`
Expected: PASS (new test + existing cartographer tests unaffected — join-keys only appears when a transform is found).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_join_transforms.py
git commit -m "feat(cartographer): join-transform detection — prescribe normalization SQL for shifted keys"
```

---

### Task 4: value-ranges + stratified format-sampling (extend `build_dimensions`)

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (`build_dimensions`, line 118-136 — APPEND, don't rewrite existing behavior)
- Test: `tests/unit/test_cartographer_value_profile.py`

**Interfaces:**
- Produces: `build_dimensions` still returns a `## Dimensions` Section, but its body now ALSO includes, budgeted: for numeric/date columns a `min..max` range line; and up to `_FORMAT_SAMPLE_CAP` (2) example values per column whose values have *unusual structure* (contain `>`, `::`, `|`, or length > 60). Existing low-cardinality dimension lines are unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartographer_value_profile.py
from __future__ import annotations

import duckdb
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.maze.cartographer import build_dimensions


async def test_ranges_and_format_samples(tmp_path) -> None:
    p = str(tmp_path / "v.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(n INT, path VARCHAR)")
    raw.execute("INSERT INTO t VALUES (1,'a>b>c'),(50,'plain'),(999,'x::y::z')")
    raw.close()
    conn = DuckDBConnection(path=p); conn.connect()
    prof = await ProfileDatasetTool().execute(
        ToolContext(connection=conn, catalog=None, primary="main"),
        ProfileDatasetTool().input_model(sample_rows=0, max_tables=100))
    body = build_dimensions(prof, conn).body
    assert "1..999" in body or "min 1" in body  # numeric range for n
    assert "a>b>c" in body or "x::y::z" in body  # unusual-structure sample surfaced
    conn.disconnect()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_value_profile.py -v`
Expected: FAIL — ranges/format samples not in body.

- [ ] **Step 3: Implement (append to `build_dimensions`)**

Add module constants and extend the function to append two more line groups after the existing dimension loop, before building `body`:

```python
_FORMAT_SAMPLE_CAP = 2
_UNUSUAL_CHARS = (">", "::", "|")


def _is_numeric_or_date(data_type: str) -> bool:
    dt = data_type.lower()
    return any(k in dt for k in ("int", "float", "double", "decimal", "numeric", "date", "timestamp"))
```

Inside `build_dimensions`, after the existing `for t ... for col ...` dimension loop (keep it intact), add a second pass that appends to `lines`:

```python
    for t in profile.tables:
        for col in t.columns:
            # numeric/date range
            if _is_numeric_or_date(col.data_type):
                try:
                    r = conn.execute(f"SELECT MIN({col.name}), MAX({col.name}) FROM {t.name}").row(0)
                    if r[0] is not None:
                        lines.append(f"- `{t.name}.{col.name}` range: {r[0]}..{r[1]}")
                except Exception:
                    pass
            # unusual-structure sample for stringy cols
            elif _is_stringy(col.data_type):
                try:
                    df = conn.execute(
                        f"SELECT DISTINCT {col.name} FROM {t.name} "
                        f"WHERE {col.name} IS NOT NULL LIMIT 200"
                    )
                    odd = [str(v[0]) for v in df.iter_rows()
                           if any(ch in str(v[0]) for ch in _UNUSUAL_CHARS) or len(str(v[0])) > 60]
                    for ex in odd[:_FORMAT_SAMPLE_CAP]:
                        lines.append(f"- `{t.name}.{col.name}` format e.g.: `{ex}`")
                except Exception:
                    pass
```

Keep the existing `body = "\n".join(lines) if lines else "No low-cardinality..."` return. (The extra lines ride the same Section; bounded by `_FORMAT_SAMPLE_CAP` + one range line per column.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_value_profile.py tests/unit/test_dab_cartographer.py -v`
Expected: PASS (existing dimension assertions still hold; new lines added).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_value_profile.py
git commit -m "feat(cartographer): value ranges + stratified format-sampling in Dimensions"
```

---

### Task 5: wide-DB schema compaction (`build_key_tables`)

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (`build_key_tables`, the builder at ~lines 55-70 — threshold-gated so normal schemas render unchanged)
- Test: `tests/unit/test_cartographer_compaction.py`

**Interfaces:**
- Produces: `build_key_tables` renders as before, EXCEPT when ≥ `_COMPACT_THRESHOLD` (8) tables share an identical column signature (same ordered column names+types) — those collapse to one representative block + a compact name list.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartographer_compaction.py
from __future__ import annotations

from labrat.agent.tools.profile_dataset import _Output as ProfileOutput  # ProfileOutput type
# Build a minimal ProfileOutput with 10 identical-structure tables + join list empty.
# (Use the real ProfileOutput/table field names from profile_dataset.py.)
from labrat.maze.cartographer import build_key_tables


def _mk_profile():
    from labrat.agent.tools.profile_dataset import _Table, _Column  # confirm real names
    cols = [_Column(name="d", data_type="DATE"), _Column(name="close", data_type="DOUBLE")]
    tables = [_Table(name=f"ticker_{i}", columns=cols, row_count=100, foreign_keys=[]) for i in range(10)]
    return ProfileOutput(tables=tables, tables_total=10, tables_profiled=10, note=None)


def test_identical_tables_compacted() -> None:
    body = build_key_tables(_mk_profile(), joins=[]).body
    assert "share this structure" in body.lower() or "10 tables" in body
    assert body.count("### ticker_") <= 1  # not 10 separate blocks
```

(Confirm the real `ProfileOutput`/table/column class names + required fields in `agent/tools/profile_dataset.py`; adjust imports/constructors.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_compaction.py -v`
Expected: FAIL — 10 separate `### ticker_` blocks rendered.

- [ ] **Step 3: Implement (threshold-gate `build_key_tables`)**

At the top of the table loop in `build_key_tables`, group tables by column signature and, for groups ≥ `_COMPACT_THRESHOLD`, render one representative + a name list instead of each table. Add:

```python
_COMPACT_THRESHOLD = 8


def _col_signature(t: object) -> tuple:
    return tuple((c.name, c.data_type) for c in t.columns)  # type: ignore[attr-defined]
```

Rewrite the `for t in profile.tables:` render loop to first bucket by signature:

```python
    from collections import defaultdict
    buckets: dict[tuple, list] = defaultdict(list)
    for t in profile.tables:
        buckets[_col_signature(t)].append(t)

    blocks: list[str] = []
    for sig, group in buckets.items():
        if len(group) >= _COMPACT_THRESHOLD:
            rep = group[0]
            cols = ", ".join(f"{c.name} ({c.data_type})" for c in rep.columns)
            names = ", ".join(t.name for t in group)
            blocks.append(
                f"### ⚠ {len(group)} tables share this structure\n"
                f"- Columns: {cols}\n- Tables: {names}"
            )
        else:
            for t in group:
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
```

(Preserve the existing `joins_by_table` construction above the loop.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_compaction.py tests/unit/test_dab_cartographer.py -v`
Expected: PASS (small fixtures < 8 identical tables render exactly as before).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_compaction.py
git commit -m "feat(cartographer): compact many identical-structure tables in Key Tables"
```

---

## Phase 4 — `top_n_with_ties` lever

### Task 6: add the `top_n_with_ties` lever line

**Files:**
- Modify: `src/labrat/agent/prompts/levers.py`
- Test: `tests/unit/test_levers.py` (extend)

**Interfaces:**
- Produces: a new lever string appended to `EXECUTION_LEVERS` (so it flows to both DAB drivers + `build_system_prompt` automatically). `EXECUTION_LEVERS` length becomes 4; `all_levers()` length becomes 8.

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_levers.py`)

```python
def test_top_n_with_ties_lever_present() -> None:
    joined = " ".join(EXECUTION_LEVERS)
    assert "truncates ties" in joined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_levers.py -v`
Expected: FAIL — string absent. NOTE: the existing `test_execution_levers_are_the_existing_three` asserts `len == 3`; update it to `len == 4` in this task.

- [ ] **Step 3: Implement** — append to `EXECUTION_LEVERS` in `levers.py`:

```python
    "For 'top N' / 'highest' questions, remember a bare LIMIT N silently truncates ties: "
    "if the Nth value can repeat, rank with ties (RANK()/DENSE_RANK() or fetch the full tie "
    "band) rather than LIMIT alone.",
```

Update `tests/unit/test_levers.py::test_execution_levers_are_the_existing_three`: `assert len(EXECUTION_LEVERS) == 4` and keep the three substring checks.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_levers.py tests/unit/test_dab_prompt_levers.py tests/unit/test_system_prompts.py -v`
Expected: PASS (the lever flows to all three prompt surfaces).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/agent/prompts/levers.py tests/unit/test_levers.py
git commit -m "feat(prompts): top_n_with_ties execution lever"
```

---

## Phase 5 — Taint-audit gate

### Task 7: taint audit module + submission gate

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/taint.py`
- Modify: `scripts/eval_dab.py` (call the gate at submission assembly)
- Test: `tests/unit/test_dab_taint.py`

**Interfaces:**
- Consumes: `detect_contamination` (`maze/scent_audit.py`) — single source of the pattern list.
- Produces:
  - `classify_trial(text: str) -> str` — `"external-oracle-cheating"` if `detect_contamination(text)` hits, else `"clean"`.
  - `audit_run(trials_jsonl: Path, scratch_dir: Path) -> dict[str, str]` — for each trial, scan its answer text + its trace file (`<scratch>/<task>__trial<n>/mcp_tool_calls.jsonl` if present, else classify `"audit-error"` when a trace is expected but missing); returns `{f"{task}:{n}": verdict}` and writes `taint.json` beside `trials_jsonl`.
  - `gate(verdicts: dict[str, str]) -> tuple[bool, list[str]]` — `(ok, offending_keys)`; `ok` is False if any verdict is `"external-oracle-cheating"` (unaudited/unwithdrawn).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_dab_taint.py
from __future__ import annotations

from labrat.eval.benchmarks.dab.taint import classify_trial, gate


def test_classify_flags_answer_key_read() -> None:
    assert classify_trial("cat query1/ground_truth.csv -> 42") == "external-oracle-cheating"
    assert classify_trial("SELECT COUNT(*) FROM orders") == "clean"


def test_gate_blocks_on_contamination() -> None:
    ok, offenders = gate({"agnews:0": "external-oracle-cheating", "yelp:1": "clean"})
    assert ok is False and "agnews:0" in offenders


def test_gate_passes_when_clean() -> None:
    ok, offenders = gate({"yelp:1": "clean", "yelp:2": "clean"})
    assert ok is True and offenders == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_taint.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `taint.py`**

```python
# src/labrat/eval/benchmarks/dab/taint.py
"""Pre-submission taint gate: classify each DAB trial as clean / external-oracle-cheating /
audit-error, and refuse to assemble a submission from an unaudited run. Uses the single
contamination pattern list in maze/scent_audit."""

from __future__ import annotations

import json
from pathlib import Path

from labrat.maze.scent_audit import detect_contamination

CLEAN = "clean"
CHEATING = "external-oracle-cheating"
AUDIT_ERROR = "audit-error"


def classify_trial(text: str) -> str:
    return CHEATING if detect_contamination(text) else CLEAN


def audit_run(trials_jsonl: Path, scratch_dir: Path) -> dict[str, str]:
    verdicts: dict[str, str] = {}
    for line in trials_jsonl.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        key = f"{r['task_id']}:{r['trial_num']}"
        parts = [str(r.get("artifact") or ""), str(r.get("reason") or "")]
        safe = r["task_id"].replace(":", "_")
        trace = scratch_dir / f"{safe}__trial{r['trial_num']}" / "mcp_tool_calls.jsonl"
        if trace.exists():
            parts.append(trace.read_text())
        verdicts[key] = classify_trial("\n".join(parts))
    (trials_jsonl.parent / "taint.json").write_text(json.dumps(verdicts, indent=1))
    return verdicts


def gate(verdicts: dict[str, str]) -> tuple[bool, list[str]]:
    offenders = [k for k, v in verdicts.items() if v == CHEATING]
    return (not offenders, offenders)
```

Wire into `scripts/eval_dab.py` submission assembly: after trials complete and before writing `submission.json`, call `audit_run` + `gate`; if not `ok`, print the offenders and `sys.exit(3)` (do not write the submission). (Find the submission-writing site and guard it; the detection-only backstop at `suite.py:568` stays.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dab_taint.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/taint.py scripts/eval_dab.py tests/unit/test_dab_taint.py
git commit -m "feat(dab): taint-audit gate — refuse to assemble a submission from an unaudited run"
```

---

## Phase 6 — Regression

### Task 8: full regression + smoke

**Files:** none (verification only)

- [ ] **Step 1:** `uv run ruff format . && uv run ruff check . && uv run pyright` — all clean.
- [ ] **Step 2:** `uv run pytest -q` — all pass, no regressions (prior baseline 715 + the new tests).
- [ ] **Step 3:** Sanity: `uv run python -c "from labrat.agent.data_tools import build_data_tools_registry as b; print('check_sql' in [t.name for t in b()._tools.values()])"` → confirm the tool registered. (Adjust to the registry's real accessor.)
- [ ] **Step 4:** If Docker is up, run the ADE 9-task smoke (`uv run python scripts/run_smoke_regression.py check --n-attempts 3`) as the product-path regression gate; else note it as owed.
- [ ] **Step 5:** Commit any format-only diffs.

---

## Self-Review

**Spec coverage (`2026-07-03-m0-deterministic-data-intelligence-design.md`):**
- Unit 1 check_sql → Task 1 ✓ · Unit 2 normalize_text (read-only) → Task 2 ✓ · Unit 3 join-transform/value-ranges+format/compaction → Tasks 3/4/5 ✓ · Unit 4 top_n_with_ties → Task 6 ✓ · Unit 5 taint-gate → Task 7 ✓. Regression → Task 8 ✓.
- Open questions resolved with concrete constants: `_SUGGEST_N=3`/`_SUGGEST_CUTOFF=0.6` (Q1); normalize form = drop-non-alphanumeric (Q2); `_FORMAT_SAMPLE_CAP=2` + unusual chars `>`,`::`,`|`,len>60 (Q3); `_COMPACT_THRESHOLD=8` (Q4); `top_n_with_ties` → `EXECUTION_LEVERS` (Q5); `eval/benchmarks/dab/taint.py` + `taint.json` dict schema (Q6).

**Placeholder scan:** Concrete code in every code step. Soft spots explicitly flagged as "confirm the real name and follow it": `DuckDBConnection` ctor (T2), `ProfileOutput`/`_Table`/`_Column` names + the profiler `ToolContext(connection=...)` shim (T3/T4/T5), the registry accessor (T8), the `eval_dab.py` submission-write site (T7). Each names exactly what to confirm.

**Type consistency:** `check_sql` `_Input/_Output/_UnknownRef/_UnknownCol` (T1); `build_join_keys(profile, conn, verified)->Section|None`, `_match_rate` (T3); extended `build_dimensions` (T4); threshold-gated `build_key_tables` + `_col_signature` (T5); `EXECUTION_LEVERS` len 4 (T6, existing test updated); `classify_trial`/`audit_run`/`gate` (T7). Consistent across tasks.

---

## Follow-on

After M0 merges: **M1 (verification-v2)** is the next milestone (`docs/superpowers/plans/2026-07-03-competitive-build-milestones.md`), authored via brainstorming → writing-plans at its kickoff.
