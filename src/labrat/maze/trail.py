"""Trail v1: promote a completed analysis into a kind="trail" Scent doc.

A Trail is a named, intent-retrieved analysis SOP — read-as-guidance, never
auto-executed. Drafted human-gated from a Finding; every draft is
contamination-audited (fail-loud) before any write. Reuses the Scent
store/parser/provenance/audit machinery parameterized by kind="trail".
"""

from __future__ import annotations

import re
from pathlib import Path

import sqlglot
from sqlglot import expressions as exp

from labrat.maze.document import ScentDoc, Section
from labrat.maze.gitmeta import current_git_sha
from labrat.maze.scent_audit import ScentContaminationError, audit_scent_doc
from labrat.maze.store import MazeStore
from labrat.thread.model import Finding
from labrat.validations.model import ValidationRule

_TRAIL_HEADINGS = ("When to use", "Steps", "Reference SQL", "Validations", "Gotchas")


def intent_slug(question: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")
    return s or "untitled-trail"


def referenced_tables(sql: str) -> list[str]:
    try:
        parsed = sqlglot.parse_one(sql)
    except Exception:
        return []
    cte_names = {cte.alias for cte in parsed.find_all(exp.CTE)}
    seen: dict[str, None] = {}
    for t in parsed.find_all(exp.Table):
        name = t.name
        # A schema/catalog-qualified reference (e.g. raw.events) is never a bare
        # CTE alias reference, even when its unqualified name collides with one
        # (WITH events AS (...) ... FROM raw.events must still surface raw.events).
        qualified = bool(t.db) or bool(t.catalog)
        if name and (qualified or name not in cte_names):
            seen.setdefault(name, None)
    return list(seen)


def applicable_validations(rules: list[ValidationRule], tables: list[str]) -> list[ValidationRule]:
    tset = set(tables)
    return [r for r in rules if r.enabled and (r.table_scope is None or r.table_scope in tset)]


def _source_tier(finding: Finding) -> str:
    p = finding.provenance
    if (
        p is not None
        and p.verifier_verdict is not None
        and p.verifier_verdict.startswith("sufficient")
    ):
        return "verified"
    return "draft"


def draft_trail_from_finding(
    finding: Finding,
    *,
    all_validations: list[ValidationRule],
    generated_at: str,
    model_id: str | None = None,
    schema_hash: str | None = None,
    git_sha: str | None = None,
) -> ScentDoc:
    tables = referenced_tables(finding.sql)
    source = _source_tier(finding)
    vals = applicable_validations(all_validations, tables)

    when = finding.question.strip()
    if tables:
        when += f"\n\nTables: {', '.join(tables)}"
    steps = (
        f"1. {finding.question.strip()}\n"
        "2. (edit this in review: describe the ordered steps a rat should follow)"
    )
    ref_sql = f"```sql\n{finding.sql.strip()}\n```"
    validations_body = (
        "\n".join(f"- {r.natural_language_rule}" for r in vals) if vals else "None applicable."
    )
    gotchas = finding.note.strip() if finding.note.strip() else "(none noted)"

    bodies = {
        "When to use": when,
        "Steps": steps,
        "Reference SQL": ref_sql,
        "Validations": validations_body,
        "Gotchas": gotchas,
    }
    sections = [
        Section(
            heading=h,
            body=bodies[h],
            source=source,
            generated_at=generated_at,
            model_id=model_id,
            schema_hash=schema_hash,
            git_sha=git_sha,
        )
        for h in _TRAIL_HEADINGS
    ]
    doc = ScentDoc(
        domain=intent_slug(finding.question),
        kind="trail",
        tables=tables,
        sections=sections,
    )
    tag = audit_scent_doc(doc)
    if tag:
        raise ScentContaminationError(
            f"drafted trail {doc.domain!r} tripped contamination guard: {tag}"
        )
    return doc


def apply_trail(store: MazeStore, doc: ScentDoc, *, git_root: Path | None = None) -> None:
    """Audit (fail-loud) then write the Trail to the PROJECT layer under kind='trail'.

    Replace-semantics per intent-slug: a Trail is one authored doc, so
    re-promoting the same intent overwrites (unlike harvest's bullet-append).
    git_sha stamped on all sections when git_root resolves.
    """
    sha = current_git_sha(git_root) if git_root is not None else None
    if sha:
        doc = doc.model_copy(
            update={"sections": [s.model_copy(update={"git_sha": sha}) for s in doc.sections]}
        )
    tag = audit_scent_doc(doc)
    if tag:
        raise ScentContaminationError(f"trail {doc.domain!r} tripped contamination guard: {tag}")
    store.write_doc(doc, scope="project", kind="trail")
