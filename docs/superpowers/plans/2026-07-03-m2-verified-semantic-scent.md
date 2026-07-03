# M2 — Verified Semantic Scent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the `with_semantics` Cartographer pass so every structured claim (join / column-role) survives a live deterministic probe before it is persisted, and prose is authored conditionally — fixing T1c's −3.7pp.

**Architecture:** New `maze/semantic_claims.py` (claim model + line parser + deterministic verifier). The semantics author emits a parseable `## Semantic Claims` block; `generate_scent(with_semantics=True)` parses it, verifies each claim (verify_join for joins, a value-shape probe for column-roles), persists only survivors into `## Verified Semantics` (Source: verified), and merges conditional prose (Source: draft). Default-off, GT-firewalled, fail-loud audited.

**Tech Stack:** Python 3.12, Pydantic, DuckDB, pytest (`asyncio_mode=auto`), ruff, pyright strict.

## Global Constraints

- Default-off (`--cartograph-semantics`); GT-firewalled (LLM sees only the deterministic skeleton; probes read DB metadata/rows, never validator/answer-key files); every frozen doc still passes `audit_scent_doc` (fail-loud raise).
- Verification is DETERMINISTIC (no LLM in the probe step).
- **Unverified structured claims are DROPPED, not surfaced.** When a value-shape probe is ambiguous, DROP (false-drop is safe; false-keep is the failure being prevented).
- `with_semantics=False` path byte-identical to today.
- Before commit: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`. All clean.

---

## Phase 1 — claim model + parser

### Task 1: `Claim` model + `parse_semantic_claims`

**Files:**
- Create: `src/labrat/maze/semantic_claims.py`
- Test: `tests/unit/test_semantic_claims.py`

**Interfaces:**
- Produces:
  - `JoinClaim(left_table: str, left_col: str, right_table: str, right_col: str)` (pydantic).
  - `RoleClaim(table: str, code_col: str, name_col: str)` (pydantic).
  - `parse_semantic_claims(text: str) -> list[JoinClaim | RoleClaim]` — tolerant line parser; ignores unparseable lines.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_semantic_claims.py
from __future__ import annotations

from labrat.maze.semantic_claims import JoinClaim, RoleClaim, parse_semantic_claims


def test_parses_join_and_role_lines() -> None:
    text = (
        "JOIN orders.customer_id = customers.id\n"
        "ROLE clinical.icd_o_3_histology CODES clinical.histological_type\n"
        "this is prose, ignore it\n"
        "JOIN malformed line\n"
    )
    claims = parse_semantic_claims(text)
    assert JoinClaim(left_table="orders", left_col="customer_id",
                     right_table="customers", right_col="id") in claims
    assert RoleClaim(table="clinical", code_col="icd_o_3_histology",
                     name_col="histological_type") in claims
    assert len(claims) == 2  # garbage + malformed ignored


def test_empty_text_no_claims() -> None:
    assert parse_semantic_claims("") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_semantic_claims.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# src/labrat/maze/semantic_claims.py
"""Structured semantic claims (join / column-role) emitted by the semantics author,
parsed from a line-based block and verified deterministically before any is persisted.
Line grammar (one per line, case-insensitive keyword):
  JOIN <lt>.<lc> = <rt>.<rc>
  ROLE <t>.<code_col> CODES <t>.<name_col>
Unparseable lines are ignored (tolerant)."""

from __future__ import annotations

import re

from pydantic import BaseModel

_JOIN_RE = re.compile(
    r"^\s*JOIN\s+(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*$", re.IGNORECASE
)
_ROLE_RE = re.compile(
    r"^\s*ROLE\s+(\w+)\.(\w+)\s+CODES\s+(\w+)\.(\w+)\s*$", re.IGNORECASE
)


class JoinClaim(BaseModel):
    left_table: str
    left_col: str
    right_table: str
    right_col: str


class RoleClaim(BaseModel):
    table: str
    code_col: str
    name_col: str


def parse_semantic_claims(text: str) -> list[JoinClaim | RoleClaim]:
    claims: list[JoinClaim | RoleClaim] = []
    for line in text.splitlines():
        mj = _JOIN_RE.match(line)
        if mj:
            claims.append(JoinClaim(left_table=mj.group(1), left_col=mj.group(2),
                                    right_table=mj.group(3), right_col=mj.group(4)))
            continue
        mr = _ROLE_RE.match(line)
        if mr:
            # ROLE t.code CODES t2.name — same table expected; take table from code side
            claims.append(RoleClaim(table=mr.group(1), code_col=mr.group(2),
                                    name_col=mr.group(4)))
    return claims
```

(Note: the ROLE regex captures the name-side table in group(3); we key `RoleClaim.table` off the code side. If code/name tables differ, still parse — verification uses `table`/`code_col` and `name_col` on that table; a cross-table role claim will simply fail the probe and drop.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_semantic_claims.py -v`
Expected: PASS (2).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/semantic_claims.py tests/unit/test_semantic_claims.py
git commit -m "feat(maze): structured semantic claim model + line parser"
```

---

## Phase 2 — claim verifier

### Task 2: `_verify_role_claim` value-shape probe

**Files:**
- Modify: `src/labrat/maze/semantic_claims.py` (add the probe)
- Test: `tests/unit/test_semantic_claims.py` (extend)

**Interfaces:**
- Consumes: `RoleClaim`; a `Connection` (`db/base.py`; `conn.execute(sql) -> Polars DataFrame`).
- Produces: `verify_role_claim(conn: Connection, claim: RoleClaim) -> bool` — True iff the data supports the asserted roles (code_col holds code-shaped values, name_col holds name-shaped values). Conservative: drop (False) when ambiguous or reversed.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_semantic_claims.py
import duckdb
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.semantic_claims import RoleClaim, verify_role_claim


def _clinical_conn(tmp_path):
    p = str(tmp_path / "c.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE clinical(icd_o_3_histology VARCHAR, histological_type VARCHAR)")
    raw.execute(
        "INSERT INTO clinical VALUES "
        "('9400/3','Astrocytoma'),('9401/3','Astrocytoma'),('9450/3','Oligodendroglioma'),"
        "('[Not Applicable]','Oligodendroglioma'),('9382/3','Oligoastrocytoma')"
    )
    raw.close()
    c = DuckDBConnection(path=p, read_only=False); c.connect()
    return c


def test_role_claim_correct_direction_survives(tmp_path) -> None:
    conn = _clinical_conn(tmp_path)
    assert verify_role_claim(conn, RoleClaim(table="clinical",
        code_col="icd_o_3_histology", name_col="histological_type")) is True
    conn.disconnect()


def test_role_claim_reversed_direction_dropped(tmp_path) -> None:
    conn = _clinical_conn(tmp_path)
    # reversed: claims the NAME column is the code column → must be dropped
    assert verify_role_claim(conn, RoleClaim(table="clinical",
        code_col="histological_type", name_col="icd_o_3_histology")) is False
    conn.disconnect()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_semantic_claims.py -v`
Expected: FAIL — `verify_role_claim` undefined.

- [ ] **Step 3: Implement** (append to `semantic_claims.py`)

```python
from labrat.db.base import Connection  # add to imports

_CODE_SHAPE_RE = re.compile(r"(\d.*[/\-._]|^\[.*\]$|^[A-Za-z0-9]{1,10}$)")
_SAMPLE = 200
_SHAPE_THRESHOLD = 0.6


def _looks_like_code(values: list[str]) -> float:
    if not values:
        return 0.0
    hits = sum(1 for v in values if _CODE_SHAPE_RE.search(v.strip()))
    return hits / len(values)


def verify_role_claim(conn: Connection, claim: RoleClaim) -> bool:
    """True iff code_col holds code-shaped values and name_col does NOT (name-shaped).
    Conservative: any probe error or ambiguity → False (drop)."""
    try:
        def _vals(col: str) -> list[str]:
            df = conn.execute(
                f"SELECT DISTINCT {col} FROM {claim.table} "
                f"WHERE {col} IS NOT NULL LIMIT {_SAMPLE}"
            )
            return [str(r[0]) for r in df.iter_rows()]

        code_vals = _vals(claim.code_col)
        name_vals = _vals(claim.name_col)
    except Exception:
        return False
    if not code_vals or not name_vals:
        return False
    code_score = _looks_like_code(code_vals)
    name_score = _looks_like_code(name_vals)
    # code column must look code-shaped AND clearly more so than the name column
    return code_score >= _SHAPE_THRESHOLD and code_score > name_score
```

(The regex + thresholds are the spec's flagged fuzzy heuristic — conservative by construction. Verified against the pancancer-shaped fixture: `9400/3` matches `\d.*[/]`; `[Not Applicable]` matches the bracket alt; `Astrocytoma` (11 chars, no digit/sep) does not.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_semantic_claims.py -v`
Expected: PASS (correct direction survives, reversed dropped).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/semantic_claims.py tests/unit/test_semantic_claims.py
git commit -m "feat(maze): deterministic value-shape probe for column-role claims"
```

---

### Task 3: `verify_semantic_claims` → `## Verified Semantics` section

**Files:**
- Modify: `src/labrat/maze/semantic_claims.py`
- Test: `tests/unit/test_semantic_claims.py` (extend)

**Interfaces:**
- Consumes: `JoinClaim`/`RoleClaim`, `verify_role_claim`; `VerifyJoinTool` (`agent/tools/verify_join.py`), `ToolContext`.
- Produces: `async def verify_semantic_claims(claims, ctx, *, database) -> Section | None` — probes each claim (JOIN → `VerifyJoinTool.execute` keep iff `likely_valid`; ROLE → `verify_role_claim`); renders survivors as a `## Verified Semantics` Section (`source="verified"`) with one bullet per survivor; returns `None` if no survivors.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/test_semantic_claims.py
from labrat.agent.tools.base import ToolContext
from labrat.maze.semantic_claims import JoinClaim, verify_semantic_claims


async def test_verify_keeps_survivors_drops_bogus(tmp_path) -> None:
    p = str(tmp_path / "j.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE customers(id INT, name VARCHAR)")
    raw.execute("INSERT INTO customers VALUES (1,'a'),(2,'b')")
    raw.execute("CREATE TABLE orders(customer_id INT, amt INT)")
    raw.execute("INSERT INTO orders VALUES (1,10),(2,20)")
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False); conn.connect()
    ctx = ToolContext(connection=conn, catalog=conn.introspect_catalog(), primary="main")
    claims = [
        JoinClaim(left_table="orders", left_col="customer_id", right_table="customers", right_col="id"),  # real
        JoinClaim(left_table="orders", left_col="amt", right_table="customers", right_col="id"),          # bogus
    ]
    section = await verify_semantic_claims(claims, ctx, database="main")
    assert section is not None and section.source == "verified"
    assert "orders.customer_id" in section.body and "customers.id" in section.body
    assert "orders.amt" not in section.body  # bogus join dropped
    conn.disconnect()


async def test_verify_no_survivors_returns_none(tmp_path) -> None:
    p = str(tmp_path / "n.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(a INT, b INT)")
    raw.execute("INSERT INTO t VALUES (1,999)")
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False); conn.connect()
    ctx = ToolContext(connection=conn, catalog=conn.introspect_catalog(), primary="main")
    bogus = [JoinClaim(left_table="t", left_col="a", right_table="t", right_col="b")]
    assert await verify_semantic_claims(bogus, ctx, database="main") is None
    conn.disconnect()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_semantic_claims.py -v`
Expected: FAIL — `verify_semantic_claims` undefined.

- [ ] **Step 3: Implement**

```python
from typing import cast
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.verify_join import VerifyJoinTool
from labrat.maze.document import Section  # add to imports


async def verify_semantic_claims(
    claims: list[JoinClaim | RoleClaim], ctx: ToolContext, *, database: str
) -> Section | None:
    tool = VerifyJoinTool()
    conn = cast(Connection, ctx.connections[database])
    lines: list[str] = []
    for c in claims:
        if isinstance(c, JoinClaim):
            try:
                v = await tool.execute(ctx, tool.input_model(
                    left_table=c.left_table, left_column=c.left_col,
                    right_table=c.right_table, right_column=c.right_col, database=database))
            except Exception:
                continue
            if v.likely_valid:
                fan = "no fan-out" if v.max_right_rows_per_key <= 1 else f"fans out up to {v.max_right_rows_per_key}/key"
                lines.append(f"- Join `{c.left_table}.{c.left_col} = {c.right_table}.{c.right_col}` "
                             f"(verified {round(v.match_rate * 100, 1)}% match, {fan}).")
        else:  # RoleClaim
            if verify_role_claim(conn, c):
                lines.append(f"- For `{c.table}`, `{c.code_col}` holds coded values; "
                             f"`{c.name_col}` holds display names — group/filter by the code column "
                             f"when the question asks for codes.")
    if not lines:
        return None
    return Section(heading="Verified Semantics", body="\n".join(lines), source="verified")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_semantic_claims.py -v`
Expected: PASS (survivors kept, bogus dropped, no-survivors→None).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/semantic_claims.py tests/unit/test_semantic_claims.py
git commit -m "feat(maze): verify_semantic_claims — probe claims, persist only survivors"
```

---

## Phase 3 — conditional prose

### Task 4: conditional `_SEMANTICS_INSTRUCTION` + `draft_semantics` returns prose + claims

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (`_SEMANTICS_INSTRUCTION` ~325, `draft_semantics` ~343)
- Test: `tests/unit/test_dab_semantic_scent.py` (extend) or `tests/unit/test_cartographer_semantics.py`

**Interfaces:**
- Produces: `draft_semantics(skeleton, llm_fn) -> tuple[list[Section], str]` — returns `(prose_sections, raw_claims_text)`. The prose sections are the `## Gotchas`/`## Best Practices`/`## Cross-References` (source="draft"), EXCLUDING the `## Semantic Claims` section; the second element is that claims section's body (or `""`). `_SEMANTICS_INSTRUCTION` rewritten to (a) require a `## Semantic Claims` block with the `JOIN …`/`ROLE …` line grammar, (b) author prose CONDITIONALLY (forbid unconditional "use X not Y"), (c) self-check to drop restatements.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cartographer_semantics.py
from __future__ import annotations

from labrat.maze.cartographer import _SEMANTICS_INSTRUCTION, draft_semantics
from labrat.maze.document import ScentDoc, Section


async def test_draft_returns_prose_and_claims_separately() -> None:
    async def _llm(_prompt: str) -> str:
        return (
            "## Semantic Claims\n"
            "JOIN orders.customer_id = customers.id\n\n"
            "## Gotchas\n"
            "- When the question asks for coded values, use the code column.\n"
        )
    skeleton = ScentDoc(domain="x", sections=[Section(heading="Key Tables", body="...", source="verified")])
    prose, raw_claims = await draft_semantics(skeleton, _llm)
    assert "JOIN orders.customer_id = customers.id" in raw_claims
    assert all(s.heading.strip().lower() != "semantic claims" for s in prose)  # claims not in prose
    assert any(s.heading.strip().lower() == "gotchas" for s in prose)
    assert all(s.source == "draft" for s in prose)


def test_instruction_forbids_unconditional_rules() -> None:
    low = _SEMANTICS_INSTRUCTION.lower()
    assert "conditional" in low and "semantic claims" in low
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_semantics.py -v`
Expected: FAIL — `draft_semantics` returns `list[Section]`, not a tuple; instruction lacks the new text.

- [ ] **Step 3: Implement**

Rewrite `_SEMANTICS_INSTRUCTION` to instruct: emit a `## Semantic Claims` block (one claim per line, `JOIN <lt>.<lc> = <rt>.<rc>` for meaningful joins, `ROLE <t>.<code_col> CODES <t>.<name_col>` when one column holds codes and another display names); write `## Gotchas`/`## Best Practices` CONDITIONALLY ("when the question asks for X, use Y" — never an unconditional "use X not Y"); drop any bullet that merely restates a column name/type. Then change `draft_semantics`:

```python
async def draft_semantics(skeleton: ScentDoc, llm_fn: LLMFn) -> tuple[list[Section], str]:
    """Single LLM pass → (conditional prose sections tagged draft, raw claims-block text)."""
    raw = await llm_fn(_semantics_prompt(skeleton))
    parsed = parse_document(raw, domain="_draft")
    prose: list[Section] = []
    claims_text = ""
    for s in parsed.sections:
        if not s.heading:
            continue
        if s.heading.strip().lower() == "semantic claims":
            claims_text = s.body
            continue
        prose.append(Section(heading=s.heading, body=s.body, source="draft"))
    return prose, claims_text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_semantics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_semantics.py
git commit -m "feat(cartographer): conditional semantics prompt + draft_semantics returns prose + claims"
```

---

## Phase 4 — wire into generate_scent + regression

### Task 5: wire the verified-claims data flow into `generate_scent`

**Files:**
- Modify: `src/labrat/maze/cartographer.py` (`generate_scent` with_semantics block ~421-431)
- Test: `tests/unit/test_cartographer_semantics.py` (extend, integration)

**Interfaces:**
- Consumes: the new `draft_semantics` tuple; `parse_semantic_claims`, `verify_semantic_claims`.
- Produces: with_semantics flow = draft → parse claims → verify claims → merge (skeleton + `## Verified Semantics` (if any) + conditional prose) → `audit_scent_doc` (unchanged fail-loud).

- [ ] **Step 1: Write the failing integration test**

```python
async def test_generate_scent_persists_only_verified_claims(tmp_path) -> None:
    import duckdb
    from labrat.db.duckdb_engine import DuckDBConnection
    from labrat.maze.cartographer import generate_scent
    p = str(tmp_path / "g.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE customers(id INT, name VARCHAR)")
    raw.execute("INSERT INTO customers VALUES (1,'a'),(2,'b')")
    raw.execute("CREATE TABLE orders(customer_id INT, amt INT)")
    raw.execute("INSERT INTO orders VALUES (1,10),(2,20)")
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False); conn.connect()
    async def _llm(_prompt: str) -> str:
        return ("## Semantic Claims\n"
                "JOIN orders.customer_id = customers.id\n"      # real → survives
                "JOIN orders.amt = customers.id\n\n"            # bogus → dropped
                "## Gotchas\n- When joining orders to customers, use customer_id.\n")
    docs = await generate_scent(connections={"main": conn}, catalogs={"main": conn.introspect_catalog()},
                                primary="main", with_semantics=True, llm_fn=_llm)
    body = "\n".join(s.body for s in docs[0].sections)
    assert "orders.customer_id = customers.id" in body   # verified claim persisted
    assert "orders.amt = customers.id" not in body       # bogus claim dropped
    assert any(s.heading == "Verified Semantics" and s.source == "verified" for s in docs[0].sections)
    assert any(s.heading.strip().lower() == "gotchas" and s.source == "draft" for s in docs[0].sections)
    conn.disconnect()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cartographer_semantics.py -v`
Expected: FAIL — generate_scent still uses the old single-section merge.

- [ ] **Step 3: Implement** — replace the with_semantics block (~421-431):

```python
        if with_semantics and llm_fn is not None:
            from labrat.maze.semantic_claims import parse_semantic_claims, verify_semantic_claims
            prose, raw_claims = await draft_semantics(doc, llm_fn)
            claims = parse_semantic_claims(raw_claims)
            verified = await verify_semantic_claims(claims, ctx, database=name)
            new_sections = list(doc.sections)
            if verified is not None:
                new_sections.append(verified)
            new_sections = merge_sections(new_sections, prose)
            doc = doc.model_copy(update={"sections": new_sections})
            tag = audit_scent_doc(doc)
            if tag is not None:
                raise ScentContaminationError(
                    f"Scent doc for {name!r} failed contamination audit ({tag}); "
                    "refusing to freeze LLM-authored semantics."
                )
```

(`ctx` and `name` are already in scope in the `generate_scent` loop — confirm; the loop builds `ctx = ToolContext(connections=..., catalogs=..., primary=...)` and iterates `for name, conn in connections.items()`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cartographer_semantics.py tests/unit/test_dab_semantic_scent.py -v`
Expected: PASS (integration + existing semantics tests — note existing T1c tests may assert the OLD prose-only shape; if any assert `draft_semantics` returns a list or a specific merged shape, update them to the new tuple/verified-section shape and document why).

- [ ] **Step 5: Commit**

```bash
git add src/labrat/maze/cartographer.py tests/unit/test_cartographer_semantics.py
git commit -m "feat(cartographer): wire verified-claims flow into generate_scent(with_semantics)"
```

---

### Task 6: regression + with_semantics=False byte-identity

**Files:** none (verification) + minor test fixups if needed

- [ ] **Step 1:** `uv run ruff format . && uv run ruff check . && uv run pyright` — clean.
- [ ] **Step 2:** `uv run pytest -q` — all pass (baseline 770 + new tests). Fix any existing semantics test that asserted the old `draft_semantics` list shape (migrate to the tuple/verified-section shape; document the migration in the commit).
- [ ] **Step 3:** Confirm `with_semantics=False` byte-identity: run `tests/unit/test_dab_cartographer.py` + a check that `generate_scent(with_semantics=False)` never imports/calls `semantic_claims` (the import is inside the `with_semantics` branch).
- [ ] **Step 4:** Confirm the DAB `--cartograph-semantics` path still routes `_cartograph_llm_fn` (no change needed — it feeds `generate_scent(with_semantics=True, llm_fn=...)`, which now produces verified claims). Note in the report that the DAB flag is unchanged but its output is now verified.
- [ ] **Step 5:** Commit any format-only diffs.

---

## Self-Review

**Spec coverage:** Unit 1 (claim model+parser) → Task 1 ✓; Unit 2 (verify: role-probe + verify_semantic_claims) → Tasks 2, 3 ✓; Unit 3 (conditional prose + draft_semantics split) → Task 4 ✓; data-flow wiring → Task 5 ✓; regression + with_semantics=False byte-identity → Task 6 ✓. Default-off + GT-firewall + fail-loud audit preserved (Task 5 keeps `audit_scent_doc`).

**Placeholder scan:** Pure tasks (1, 2, 3, 4) carry complete code. Task 5 gives the exact replacement block + names the in-scope vars to confirm (`ctx`, `name`). The role-probe heuristic (Task 2) is concrete (regex + thresholds) and flagged as the tunable/fuzzy unit.

**Type consistency:** `JoinClaim`/`RoleClaim` (Tasks 1–5); `parse_semantic_claims -> list[JoinClaim|RoleClaim]`, `verify_role_claim(conn, claim)->bool`, `verify_semantic_claims(claims, ctx, *, database)->Section|None` (Tasks 1–5); `draft_semantics -> tuple[list[Section], str]` (Tasks 4, 5). Consistent.

---

## Follow-on

After M2 merges + ablates (Sonnet-5 subset + 4.6 control; structure-only vs verified-semantics; keep only if it clears T1c's −3.7pp), the roadmap's M3 (column-level lineage / T1b) is next. The `Source: verified` semantic claims also feed the eventual T3c provenance footer.
