"""The Maze store: resolves ordered reference-doc source layers from disk.

On-disk namespace (forward-compatible with trail/warren kinds + a future team layer):

    <project_root>/labrat_maze/<kind>/*.md             (project scope — wins on conflict)
    <home>/.labrat/maze/<profile>/<kind>/*.md          (user scope)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from labrat.maze.document import ScentDoc, parse_document, render_document


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

    def load_domain(self, domain: str, kind: str = "scent") -> ScentDoc | None:
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
