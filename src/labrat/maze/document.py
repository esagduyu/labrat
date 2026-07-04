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
_RECOGNIZED_SOURCES = {"verified", "draft", "human", "lineage"}
_SOURCE_LINE_RE = re.compile(r"^\*\*Source:\*\*\s*(\w+)\b.*$")


class Section(BaseModel):
    heading: str  # "" for the preamble before the first H2
    body: str
    source: str = (
        "human"  # "verified" | "draft" | "human" | "lineage"; provenance for #26b cartographer
    )


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
