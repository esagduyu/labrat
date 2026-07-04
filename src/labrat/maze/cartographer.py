"""Scent auto-cartographer (FEATURE_ROADMAP #26b, GENERATE half).

Explores a database and writes a curated Scent reference doc: a deterministic,
mechanically-verified structure skeleton (Source: verified) plus an opt-in single
LLM deep pass for business semantics (Source: draft). Reuses the existing
profile_dataset + verify_join tools; never reads ground-truth artifacts.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import cast

import sqlglot
from pydantic import BaseModel
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.explain_lineage import (
    _catalog_schema_dict,  # pyright: ignore[reportPrivateUsage]
    _flatten,  # pyright: ignore[reportPrivateUsage]
)
from labrat.agent.tools.profile_dataset import (
    ProfileDatasetTool,
    _TableProfile,  # pyright: ignore[reportPrivateUsage]
)
from labrat.agent.tools.profile_dataset import (
    _Output as ProfileOutput,  # pyright: ignore[reportPrivateUsage]
)
from labrat.agent.tools.verify_join import VerifyJoinTool
from labrat.agent.verifier import LLMFn
from labrat.db.base import Connection
from labrat.db.catalog import Catalog
from labrat.maze.document import ScentDoc, Section, parse_document, render_document
from labrat.maze.scent_audit import ScentContaminationError, audit_scent_doc, detect_contamination

_STRINGY = ("CHAR", "TEXT", "STRING", "VARCHAR")


class VerifiedJoin(BaseModel):
    left: str  # "orders.customer_id"
    right: str  # "customers.customer_id"
    match_rate: float
    fanout: int  # max right rows per key (>1 means the join fans out)


def _is_stringy(data_type: str) -> bool:
    up = data_type.upper()
    return any(tok in up for tok in _STRINGY)


_FORMAT_SAMPLE_CAP = 2
_UNUSUAL_CHARS = (">", "::", "|")
_SAMPLE_TRUNCATE = 80


def _is_numeric_or_date(data_type: str) -> bool:
    dt = data_type.lower()
    return any(
        k in dt for k in ("int", "float", "double", "decimal", "numeric", "date", "timestamp")
    )


def build_quick_reference(profile: ProfileOutput) -> Section:
    lines = [f"Database `{profile.database}`: {profile.tables_profiled} tables profiled."]
    for t in profile.tables:
        rc = "unknown" if t.row_count is None else f"{t.row_count}"
        lines.append(f"- `{t.name}`: {rc} rows.")
    if profile.note:
        lines.append(f"_{profile.note}_")
    return Section(heading="Quick Reference", body="\n".join(lines), source="verified")


_COMPACT_THRESHOLD = 8


def _col_signature(t: _TableProfile) -> tuple[tuple[str, str], ...]:
    return tuple((c.name, c.data_type) for c in t.columns)


def build_key_tables(profile: ProfileOutput, joins: list[VerifiedJoin]) -> Section:
    joins_by_table: dict[str, list[VerifiedJoin]] = {}
    for j in joins:
        joins_by_table.setdefault(j.left.split(".")[0], []).append(j)

    buckets: dict[tuple[tuple[str, str], ...], list[_TableProfile]] = defaultdict(list)
    for t in profile.tables:
        buckets[_col_signature(t)].append(t)

    blocks: list[str] = []
    for group in buckets.values():
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


def _candidate_joins(profile: ProfileOutput) -> list[tuple[str, str, str, str]]:
    """Candidate (left_table, left_col, right_table, right_col) join keys from declared
    FKs and an ``<base>_id`` name heuristic. Excludes self-joins; de-duplicates."""
    by_name = {t.name.lower(): t for t in profile.tables}
    candidates: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _add(lt: str, lc: str, rt: str, rc: str) -> None:
        if rt.lower() == lt.lower():
            return  # self-join
        key = (lt, lc, rt, rc)
        if key not in seen:
            seen.add(key)
            candidates.append(key)

    # (a) declared FKs from the profile ("col -> reftable.refcol")
    for t in profile.tables:
        for fk in t.foreign_keys:
            if " -> " not in fk:
                continue
            lc, rhs = fk.split(" -> ", 1)
            if "." not in rhs:
                continue
            rt_name, rc = rhs.split(".", 1)
            _add(t.name, lc.strip(), rt_name.strip(), rc.strip())

    # (b) <base>_id name heuristic
    for t in profile.tables:
        for col in t.columns:
            cname = col.name.lower()
            if not cname.endswith("_id"):
                continue
            base = cname[:-3]
            for rt_name in (base, base + "s"):
                rt = by_name.get(rt_name)
                if rt is None:
                    continue
                rt_cols = {rc.name.lower(): rc.name for rc in rt.columns}
                for cand in (cname, "id"):
                    if cand in rt_cols:
                        _add(t.name, col.name, rt.name, rt_cols[cand])
                        break
    return candidates


_JOIN_XFORM_MIN = 0.5
# (label, SQL template applied to a column expr) — deterministic, symmetric on both sides
_JOIN_TRANSFORMS: list[tuple[str, str]] = [
    ("extract-digits", "regexp_replace(CAST({c} AS VARCHAR), '[^0-9]', '', 'g')"),
    ("lower-trim", "lower(trim(CAST({c} AS VARCHAR)))"),
    ("strip-leading-num", "regexp_replace(CAST({c} AS VARCHAR), '^[0-9]+[-.]', '')"),
    (
        "numeric-id",
        "CAST(TRY_CAST(regexp_replace(CAST({c} AS VARCHAR), '[^0-9]', '', 'g') "
        "AS BIGINT) AS VARCHAR)",
    ),
]


def _match_rate(conn: Connection, lt: str, lexpr: str, rt: str, rexpr: str) -> float:
    def _scalar(sql: str) -> int:
        v = conn.execute(sql).row(0)[0]
        return int(v) if v is not None else 0

    denom = _scalar(f"SELECT COUNT(*) FROM {lt} WHERE {lexpr} IS NOT NULL AND {lexpr} <> ''")
    if denom == 0:
        return 0.0
    matched = _scalar(
        f"SELECT COUNT(*) FROM {lt} WHERE {lexpr} IN "
        f"(SELECT {rexpr} FROM {rt} WHERE {rexpr} IS NOT NULL AND {rexpr} <> '')"
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
        try:
            raw = _match_rate(conn, lt, lc, rt, rc)
        except Exception:
            continue
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


def build_dimensions(
    profile: ProfileOutput, conn: Connection, *, cap: int = 25, variant_seed: int = 0
) -> Section:
    lines: list[str] = []
    for t in profile.tables:
        for col in t.columns:
            if not _is_stringy(col.data_type):
                continue
            order_clause = (
                f"ORDER BY hash(CAST({col.name} AS VARCHAR) || '{variant_seed}') "
                if variant_seed > 0
                else ""
            )
            try:
                df = conn.execute(
                    f"SELECT DISTINCT {col.name} FROM {t.name} "
                    f"WHERE {col.name} IS NOT NULL "
                    f"{order_clause}"
                    f"LIMIT {cap + 1}"
                )
            except Exception:
                continue
            vals = [str(row[0]) for row in df.iter_rows()]
            # Skip if cardinality exceeds cap OR if cardinality equals row count (all unique)
            if 0 < len(vals) <= cap and t.row_count is not None and len(vals) < t.row_count:
                lines.append(f"- `{t.name}.{col.name}`: {', '.join(sorted(vals))}")

    for t in profile.tables:
        for col in t.columns:
            # numeric/date range
            if _is_numeric_or_date(col.data_type):
                try:
                    r = conn.execute(f"SELECT MIN({col.name}), MAX({col.name}) FROM {t.name}").row(
                        0
                    )
                    if r[0] is not None:
                        lines.append(f"- `{t.name}.{col.name}` range: {r[0]}..{r[1]}")
                except Exception:
                    pass
            # unusual-structure sample for stringy cols
            elif _is_stringy(col.data_type):
                order_clause = (
                    f"ORDER BY hash(CAST({col.name} AS VARCHAR) || '{variant_seed}') "
                    if variant_seed > 0
                    else ""
                )
                try:
                    df = conn.execute(
                        f"SELECT DISTINCT {col.name} FROM {t.name} "
                        f"WHERE {col.name} IS NOT NULL "
                        f"{order_clause}"
                        f"LIMIT 200"
                    )
                    odd = [
                        str(v[0])
                        for v in df.iter_rows()
                        if any(ch in str(v[0]) for ch in _UNUSUAL_CHARS) or len(str(v[0])) > 60
                    ]
                    # Bound bloat (truncate long free-text) and keep answer-shaped strings
                    # out of the doc by construction — this deterministic path never runs
                    # audit_scent_doc, so it must be clean by construction, not by audit.
                    shown = 0
                    for ex in odd:
                        if shown >= _FORMAT_SAMPLE_CAP:
                            break
                        if detect_contamination(ex) is not None:
                            continue
                        sample = ex[:_SAMPLE_TRUNCATE] + "…" if len(ex) > _SAMPLE_TRUNCATE else ex
                        lines.append(f"- `{t.name}.{col.name}` format e.g.: `{sample}`")
                        shown += 1
                except Exception:
                    pass

    body = "\n".join(lines) if lines else "No low-cardinality categorical columns detected."
    return Section(heading="Dimensions", body=body, source="verified")


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


def build_view_lineage(catalog: Catalog, *, database: str) -> Section | None:
    """Deterministic column-level lineage for every view in *catalog* (Unit D).

    Reads ONLY ``Table.view_definition`` SQL metadata — no connection, no data, no
    LLM (GT-firewall by construction: the signature cannot reach a database).
    Fail-soft per view/column: unparseable definitions or unresolvable columns are
    skipped, never fatal. Returns None when the catalog has no views, so a no-views
    DB yields byte-identical deterministic Scent.
    """
    _ = database  # doc identity is per-connection already; kept for call-site clarity
    # _catalog_schema_dict already drops zero-column entries (e.g. a table/view
    # whose columns failed to introspect) — sqlglot's MappingSchema rejects any
    # table with no columns at all, which would otherwise poison every lineage()
    # call in this catalog, not just that one table.
    schema = _catalog_schema_dict(catalog)
    lines: list[str] = []
    for sch in catalog.schemas:
        for t in sch.tables:
            if t.view_definition is None:
                continue
            try:
                tree = sqlglot.parse_one(t.view_definition)
            except SqlglotError:
                continue  # unparseable view — skip it, never block Scent generation
            select = tree.expression if isinstance(tree, exp.Create) else tree
            if not isinstance(select, exp.Query):
                continue
            select_sql = select.sql()
            for proj in select.selects:
                out_name = proj.alias_or_name
                if out_name == "*":
                    continue
                try:
                    node = lineage(out_name, select_sql, schema=schema)
                except SqlglotError:
                    continue  # unresolvable column — skip the bullet, keep the rest
                refs = _flatten(node)
                if not refs:
                    continue  # literal-only column: no base sources to report
                srcs = ", ".join(f"`{r.table}`.`{r.column}`" for r in refs)
                lines.append(f"- view `{t.name}`.`{out_name}` ← {srcs}")
    if not lines:
        return None
    return Section(heading="View Lineage", body="\n".join(lines), source="lineage")


async def discover_joins(
    ctx: ToolContext, profile: ProfileOutput, *, database: str
) -> list[VerifiedJoin]:
    """Find candidate joins by declared FKs + an ``<base>_id`` name heuristic, then
    mechanically verify each with verify_join. Keeps only ``likely_valid`` joins and
    excludes self-joins.
    """
    tool = VerifyJoinTool()
    joins: list[VerifiedJoin] = []
    for lt, lc, rt, rc in _candidate_joins(profile):
        verdict = await tool.execute(
            ctx,
            tool.input_model(
                left_table=lt, left_column=lc, right_table=rt, right_column=rc, database=database
            ),
        )
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


_SEMANTICS_INSTRUCTION = (
    "You are a senior data analyst writing a reference doc for an LLM data agent.\n"
    "The VERIFIED FACTS below are mechanically confirmed ground truth — DO NOT alter, "
    "repeat, or contradict them.\n\n"
    "First, emit a ## Semantic Claims block: one machine-checkable claim per line, no "
    "prose, using ONLY these two grammars:\n"
    "  JOIN <left_table>.<left_col> = <right_table>.<right_col>   (a join that is "
    "meaningful for answering questions, beyond what is already verified)\n"
    "  ROLE <table>.<code_col> CODES <table>.<name_col>   (when one column holds a coded "
    "value — an id, abbreviation, or enum — and another column on the same table holds the "
    "human-readable display name for that same concept)\n"
    "Only claim what you can support from the verified facts; omit a claim rather than "
    "guess.\n\n"
    "Then write the interpretive sections a senior analyst would add: ## Gotchas "
    "(wrong-answer modes, dirty-data warnings) and ## Best Practices (canonical metric "
    "definitions, preferred columns), plus ## Cross-References if useful. Every bullet in "
    'these sections MUST be conditional — phrase it as "WHEN the question asks for X, use '
    'Y" (or "WHEN <condition>, ..."), tied to a specific trigger. NEVER write an '
    'unconditional rule like "use X not Y" — an unconditional rule silently overrides '
    "cases the analyst didn't anticipate. Before finishing, self-check every bullet: if it "
    "merely restates a column's name or type (something already visible in the verified "
    "facts) with no added interpretation, drop it — only keep high-signal, non-obvious "
    "guidance.\n\n"
    "Use short, retrieval-oriented bullets and routing-trigger phrasing. If you are unsure "
    "about a business rule, say so rather than invent. Output GitHub-flavored markdown with "
    "## headings only; do not emit a ## Quick Reference, ## Dimensions, or ## Key Tables "
    "section (those are already verified). "
    "COHORT VS FILTER: a quality/status filter (e.g. FILTER='PASS', a sequenced-only or "
    "is_test flag) scopes WHICH ROWS COUNT AS POSITIVE (the numerator); it must NEVER be "
    "authored as a Best Practice that narrows the cohort denominator (the population) — "
    "never restrict the population to a filtered subset. "
    "GROUNDING: you are an annotator, not an inventor — build conditional routing guidance "
    "ON TOP OF the verified facts below and NEVER introduce a claim the verified facts do "
    "not support."
)


def _semantics_prompt(skeleton: ScentDoc) -> str:
    facts = render_document(skeleton)
    return f"{_SEMANTICS_INSTRUCTION}\n\n--- VERIFIED FACTS ---\n{facts}\n--- END FACTS ---\n"


async def draft_semantics(skeleton: ScentDoc, llm_fn: LLMFn) -> tuple[list[Section], str]:
    """Single LLM pass → (conditional prose sections tagged draft, raw claims-block text).

    Two safety nets against unverified claim lines reaching the doc as prose:
    (1) a fuzzy heading match catches renamed/drifted "Semantic Claims" sections (e.g.
    "Semantic Claims:"), and (2) any claim-shaped line found stray inside an otherwise
    legitimate prose section (e.g. a JOIN/ROLE line under "## Gotchas") is rerouted into
    the claims text so it still goes through verification instead of being emitted raw.
    """
    # Lazy import: draft_semantics is only called from the with_semantics branch of
    # generate_scent, so this never runs on the deterministic path (byte-identity kept).
    from labrat.maze.semantic_claims import is_claim_line

    raw = await llm_fn(_semantics_prompt(skeleton))
    parsed = parse_document(raw, domain="_draft")
    prose: list[Section] = []
    claims_text = ""
    for s in parsed.sections:
        if not s.heading:
            continue
        if s.heading.strip().lower().startswith("semantic claims"):
            claims_text += s.body + "\n"
            continue
        claim_lines: list[str] = []
        prose_lines: list[str] = []
        for line in s.body.splitlines():
            (claim_lines if is_claim_line(line) else prose_lines).append(line)
        if claim_lines:
            claims_text += "\n".join(claim_lines) + "\n"
        remaining_body = "\n".join(prose_lines).strip()
        if remaining_body:
            prose.append(Section(heading=s.heading, body=remaining_body, source="draft"))
    return prose, claims_text


_PRUNE_INSTRUCTION = (
    "PRUNE PASS. Below are VERIFIED FACTS and a list of DRAFT BULLETS an author wrote. "
    "Return ONLY the draft bullets that are FULLY SUPPORTED by a verified fact, each on "
    "its own line, verbatim (copy the bullet text exactly, including the leading '- '). "
    "Drop any bullet that makes a claim the verified facts do not support. Output nothing "
    "but the kept bullet lines."
)


def _normalize_bullet(line: str) -> str:
    """Normalize a bullet line for fuzzy matching: strip surrounding whitespace, drop a
    leading '-' marker, strip trailing punctuation, and casefold. Tolerates cosmetic LLM
    echo drift (missing trailing period, extra whitespace) without weakening the
    verbatim-from-original guarantee — this is only used to find WHICH original line a
    kept critique line refers to; the emitted text always comes from the original."""
    s = line.strip()
    if s.startswith("-"):
        s = s[1:]
    return s.strip().rstrip(".;").strip().casefold()


async def prune_unsupported(
    skeleton: ScentDoc, prose: list[Section], llm_fn: LLMFn
) -> list[Section]:
    """Self-critique prune: ask the LLM which drafted bullets are fully supported by a
    verified fact and keep only those (verbatim, from the ORIGINAL draft text — never the
    LLM's echo). Fail-open — any error, empty response, or a critique that matches ZERO
    original bullets (garbled/hallucinated/ambiguous) returns the original ``prose``
    unchanged (never worse than the draft). Matching is normalized (whitespace/trailing
    punctuation/case) so cosmetic LLM formatting drift doesn't cause a real match to be
    silently dropped. Catches the T1c/M2 failure mode: a bullet that NAMES real columns
    but makes an unsupported claim (a vocabulary filter would miss it; the critique judges
    the claim)."""
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
    kept_normalized = {
        _normalize_bullet(ln) for ln in raw.splitlines() if ln.strip().startswith("-")
    }
    kept_normalized.discard("")
    if not kept_normalized:
        return prose  # fail-open: empty / unparseable response

    # Fail-open guard: only apply the filter if the critique actually corresponds to the
    # draft — i.e. at least one ORIGINAL bullet (across all sections) normalized-matches
    # a kept critique line. Zero matches means a garbled/hallucinated critique (or an
    # ambiguous "drop everything") that must not be trusted to erase real content.
    matched_any = any(
        _normalize_bullet(ln) in kept_normalized
        for s in prose
        for ln in s.body.splitlines()
        if ln.strip().startswith("-")
    )
    if not matched_any:
        return prose  # fail-open: critique matches nothing real in the draft

    result: list[Section] = []
    for s in prose:
        new_lines = [
            ln
            for ln in s.body.splitlines()
            if not ln.strip().startswith("-") or _normalize_bullet(ln) in kept_normalized
        ]
        body = "\n".join(new_lines).strip()
        if body:
            result.append(Section(heading=s.heading, body=body, source=s.source))
    return result


_RESERVED_HEADINGS = {"verified semantics", "semantic claims"}


def merge_sections(verified: list[Section], drafted: list[Section]) -> list[Section]:
    """Append drafted sections whose heading does not collide with a verified one.

    "verified semantics" and "semantic claims" headings are always reserved — an
    LLM-authored prose section can't spoof a verified-looking heading even when no real
    verified section exists (e.g. verify_semantic_claims returned None).
    """
    taken = {s.heading.strip().lower() for s in verified} | _RESERVED_HEADINGS
    return list(verified) + [d for d in drafted if d.heading.strip().lower() not in taken]


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
    variant_seed: int = 0,
) -> list[ScentDoc]:
    """Generate one Scent doc per connection: a verified deterministic skeleton plus,
    when ``with_semantics`` and ``llm_fn`` are given, an LLM-drafted semantics pass.
    """
    profiler = ProfileDatasetTool()
    docs: list[ScentDoc] = []

    for name, conn in connections.items():
        ctx = ToolContext(connections=connections, catalogs=catalogs, primary=primary)
        profile = await profiler.execute(
            ctx, profiler.input_model(database=name, sample_rows=0, max_tables=10_000)
        )

        # budget: rank by relevance (when supplied) else row count, keep top N
        if len(profile.tables) > table_budget:
            kept = sorted(
                profile.tables,
                key=lambda t: relevance.get(t.name, 0.0) if relevance else (t.row_count or 0),
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
        ]
        jk = build_join_keys(profile, cast(Connection, conn), joins)
        if jk is not None:
            sections.append(jk)
        sections.append(
            build_dimensions(
                profile, cast(Connection, conn), cap=distinct_cap, variant_seed=variant_seed
            )
        )
        cn = build_code_name_notes(profile, cast(Connection, conn))
        if cn is not None:
            sections.append(cn)
        vl = build_view_lineage(cast(Catalog, catalogs[name]), database=name)
        if vl is not None:
            sections.append(vl)
        doc = ScentDoc(
            domain=name,
            kind="scent",
            tables=[t.name for t in profile.tables],
            confidence="draft",
            sections=sections,
        )
        if with_semantics and llm_fn is not None:
            from labrat.maze.semantic_claims import parse_semantic_claims, verify_semantic_claims

            prose, raw_claims = await draft_semantics(doc, llm_fn)
            prose = await prune_unsupported(doc, prose, llm_fn)  # C4.2 self-critique prune
            claims = parse_semantic_claims(raw_claims)
            verified = await verify_semantic_claims(claims, ctx, database=name)
            new_sections = list(doc.sections)
            if verified is not None:
                new_sections.append(verified)
            new_sections = merge_sections(new_sections, prose)
            doc = doc.model_copy(update={"sections": new_sections})
            # Audit the full merged doc (skeleton + verified claims + drafted prose):
            # fail-loud is the safe default, and a sampled dimension value that happens
            # to match a pattern is better caught than a real leak missed.
            tag = audit_scent_doc(doc)
            if tag is not None:
                raise ScentContaminationError(
                    f"Scent doc for {name!r} failed contamination audit ({tag}); "
                    "refusing to freeze LLM-authored semantics."
                )
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
    variant_seed: int = 0,
) -> list[Path]:
    """First-contact Scent pre-pass: if ``scent_dir`` already holds docs, reuse them
    (idempotent first-contact cache); otherwise generate_scent(...) and write them.

    Deterministic by default (``with_semantics=False`` → no LLM). The reusable seam:
    DAB calls this deterministic-only; the agent's first-connect path will later call it
    with semantics + the dual store. Caller owns ``scent_dir`` isolation.

    ``variant_seed`` (default 0 = baseline) is threaded into ``generate_scent`` to give
    per-sub-run diversity on high-cardinality dimension sampling; callers that want
    distinct variants must also give each one a distinct ``scent_dir`` (this cache is
    keyed purely on directory contents, not on the seed).
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
        variant_seed=variant_seed,
    )
    return write_docs(docs, scent_dir)
