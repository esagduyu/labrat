"""File-system watcher for dbt manifest.json changes (M30).

Uses watchdog to detect when the manifest is rewritten (e.g. after `dbt parse`)
and invokes the provided callback so the catalog can be reloaded.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from watchdog.events import (  # type: ignore[import-untyped]
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer  # type: ignore[import-untyped]


class _ManifestHandler(FileSystemEventHandler):  # type: ignore[misc]
    def __init__(self, manifest_path: Path, on_change: Callable[[], None]) -> None:
        super().__init__()
        self._manifest_path = manifest_path.resolve()
        self._on_change = on_change

    def on_modified(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if not event.is_directory and Path(str(event.src_path)).resolve() == self._manifest_path:
            self._on_change()


class ManifestWatcher:
    """Watch a dbt manifest.json and call *on_change* whenever it is rewritten."""

    def __init__(self, manifest_path: Path, on_change: Callable[[], None]) -> None:
        self._manifest_path = manifest_path
        self._on_change = on_change
        self._observer: Any = None

    def start(self) -> None:
        handler = _ManifestHandler(self._manifest_path, self._on_change)
        observer: Any = Observer()  # pyright: ignore[reportPrivateImportUsage]
        observer.schedule(handler, str(self._manifest_path.parent), recursive=False)
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
