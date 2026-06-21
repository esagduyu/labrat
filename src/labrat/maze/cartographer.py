"""Scent auto-cartographer (FEATURE_ROADMAP #26b, GENERATE half).

Explores a database and writes a curated Scent reference doc: a deterministic,
mechanically-verified structure skeleton (Source: verified) plus an opt-in single
LLM deep pass for business semantics (Source: draft). Reuses the existing
profile_dataset + verify_join tools; never reads ground-truth artifacts.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import (
    ProfileDatasetTool,
)
from labrat.agent.tools.profile_dataset import (
    _Output as ProfileOutput,  # pyright: ignore[reportPrivateUsage]
)
from labrat.agent.tools.verify_join import VerifyJoinTool
from labrat.agent.verifier import LLMFn
from labrat.db.base import Connection
from labrat.maze.document import ScentDoc, Section, parse_document, render_document

_STRINGY = ("CHAR", "TEXT", "STRING", "VARCHAR")


class VerifiedJoin(BaseModel):
    left: str  # "orders.customer_id"
    right: str  # "customers.customer_id"
    match_rate: float
    fanout: int  # max right rows per key (>1 means the join fans out)


def _is_stringy(data_type: str) -> bool:
    up = data_type.upper()
    return any(tok in up for tok in _STRINGY)


def build_quick_reference(profile: ProfileOutput) -> Section:
    lines = [f"Database `{profile.database}`: {profile.tables_profiled} tables profiled."]
    for t in profile.tables:
        rc = "unknown" if t.row_count is None else f"{t.row_count}"
        lines.append(f"- `{t.name}`: {rc} rows.")
    if profile.note:
        lines.append(f"_{profile.note}_")
    return Section(heading="Quick Reference", body="\n".join(lines), source="verified")


def build_key_tables(profile: ProfileOutput, joins: list[VerifiedJoin]) -> Section:
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


def build_dimensions(profile: ProfileOutput, conn: Connection, *, cap: int = 25) -> Section:
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
            # Skip if cardinality exceeds cap OR if cardinality equals row count (all unique)
            if 0 < len(vals) <= cap and t.row_count is not None and len(vals) < t.row_count:
                lines.append(f"- `{t.name}.{col.name}`: {', '.join(sorted(vals))}")
    body = "\n".join(lines) if lines else "No low-cardinality categorical columns detected."
    return Section(heading="Dimensions", body=body, source="verified")


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
    "repeat, or contradict them. Write ONLY the interpretive sections a senior analyst "
    "would add: ## Gotchas (wrong-answer modes, dirty-data warnings), ## Best Practices "
    "(canonical metric definitions, preferred columns), and ## Cross-References. Use short, "
    "retrieval-oriented bullets and routing-trigger phrasing. If you are unsure about a "
    "business rule, say so rather than invent. Output GitHub-flavored markdown with ## "
    "headings only; do not emit a ## Quick Reference, ## Dimensions, or ## Key Tables "
    "section (those are already verified)."
)


def _semantics_prompt(skeleton: ScentDoc) -> str:
    facts = render_document(skeleton)
    return f"{_SEMANTICS_INSTRUCTION}\n\n--- VERIFIED FACTS ---\n{facts}\n--- END FACTS ---\n"


async def draft_semantics(skeleton: ScentDoc, llm_fn: LLMFn) -> list[Section]:
    """Single LLM pass: draft the interpretive sections, tagged Source: draft."""
    raw = await llm_fn(_semantics_prompt(skeleton))
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
            drafted = await draft_semantics(doc, llm_fn)
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
