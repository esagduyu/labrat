"""The Maze store: resolves ordered reference-doc source layers from disk.

On-disk namespace (forward-compatible with trail/warren kinds + a future team layer):

    <project_root>/labrat_maze/<kind>/*.md             (project scope — wins on conflict)
    <home>/.labrat/maze/<profile>/<kind>/*.md          (user scope)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from labrat.maze.document import ScentDoc, Section, parse_document, render_document


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
        by_domain: dict[str, list[ScentDoc]] = {}
        for layer in self._layers:  # user first, project second
            directory = layer.root / kind
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                doc = parse_document(
                    path.read_text(encoding="utf-8"), domain=path.stem, scope=layer.scope
                )
                if doc.kind != kind:
                    continue
                by_domain.setdefault(doc.domain, []).append(doc)
        return [_merge_domain(parts) for parts in by_domain.values()]

    def load_domain(
        self, domain: str, kind: str = "scent", *, scope: str | None = None
    ) -> ScentDoc | None:
        if scope is not None:
            layer = next((la for la in self._layers if la.scope == scope), None)
            if layer is None:
                raise ValueError(f"unknown scope: {scope!r}")
            path = layer.root / kind / f"{domain}.md"
            if not path.is_file():
                return None
            doc = parse_document(path.read_text(encoding="utf-8"), domain=domain, scope=layer.scope)
            return doc if doc.kind == kind else None
        for doc in self.docs(kind):
            if doc.domain == domain:
                return doc
        return None

    def write_doc(self, doc: ScentDoc, *, scope: str = "project", kind: str = "scent") -> Path:
        if doc.kind != kind:
            raise ValueError(f"doc.kind {doc.kind!r} != write kind {kind!r}")
        layer = next((layer for layer in self._layers if layer.scope == scope), None)
        if layer is None:
            raise ValueError(f"unknown scope: {scope!r}")
        directory = layer.root / kind
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{doc.domain}.md"
        path.write_text(render_document(doc), encoding="utf-8")
        return path


def _merge_domain(parts: list[ScentDoc]) -> ScentDoc:
    """Union a domain's layer docs (user first, project second) into one view.

    Sections dedup by body (strip): a project-layer copy of a user section —
    the legacy pre-v2 apply behavior — collapses into the union, which is why
    no on-disk migration is needed.
    """
    if len(parts) == 1:
        return parts[0]
    sections: list[Section] = []
    seen_bodies: set[str] = set()
    for doc in parts:
        for s in doc.sections:
            key = s.body.strip()
            if key in seen_bodies:
                continue
            seen_bodies.add(key)
            sections.append(s)
    tables = sorted({t for doc in parts for t in doc.tables})
    confidence = next(
        (doc.confidence for doc in reversed(parts) if doc.confidence is not None), None
    )
    return ScentDoc(
        domain=parts[0].domain,
        kind=parts[0].kind,
        tables=tables,
        confidence=confidence,
        scope="merged",
        sections=sections,
    )


def user_scent_dir(profile: str, home: Path | None = None) -> Path:
    """The user-scope scent directory for *profile* — MazeStore's user layer + 'scent'.

    Single source of truth for the TUI pre-pass target: cartograph_prepass writes
    here, and SearchReferenceDocsTool reads it back via MazeStore.from_env(profile).
    """
    return (home or Path.home()) / ".labrat" / "maze" / profile / "scent"
