"""Thread persistence: JSON files at ~/.local/share/labrat/threads/ (M17)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from labrat.thread.model import Thread, Version

_DEFAULT_DIR = Path.home() / ".local" / "share" / "labrat" / "threads"


class ThreadStore:
    """Reads and writes Thread and Version records as JSON files.

    Layout:
      <store_dir>/
        threads.json         — list of Thread dicts
        versions/<id>.json   — one file per Version
    """

    def __init__(self, store_dir: Path | None = None) -> None:
        self._dir = store_dir or _DEFAULT_DIR
        self._threads_file = self._dir / "threads.json"
        self._versions_dir = self._dir / "versions"

    def _ensure_dirs(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._versions_dir.mkdir(parents=True, exist_ok=True)

    # ── threads ───────────────────────────────────────────────────────────────

    def load_threads(self) -> list[Thread]:
        if not self._threads_file.exists():
            return []
        raw: list[dict[str, Any]] = json.loads(self._threads_file.read_text())
        return [Thread.model_validate(d) for d in raw]

    def save_threads(self, threads: list[Thread]) -> None:
        self._ensure_dirs()
        data = [t.model_dump(mode="json") for t in threads]
        self._threads_file.write_text(json.dumps(data, indent=2))

    def append_thread(self, thread: Thread) -> None:
        threads = self.load_threads()
        threads.append(thread)
        self.save_threads(threads)

    def replace_thread(self, thread: Thread) -> None:
        threads = self.load_threads()
        threads = [t if t.id != thread.id else thread for t in threads]
        self.save_threads(threads)

    # ── versions ──────────────────────────────────────────────────────────────

    def load_version(self, version_id: str) -> Version | None:
        path = self._versions_dir / f"{version_id}.json"
        if not path.exists():
            return None
        return Version.model_validate(json.loads(path.read_text()))

    def save_version(self, version: Version) -> None:
        self._ensure_dirs()
        path = self._versions_dir / f"{version.id}.json"
        path.write_text(json.dumps(version.model_dump(mode="json"), indent=2))

    def load_versions_for_thread(self, thread_id: str) -> list[Version]:
        if not self._versions_dir.exists():
            return []
        versions: list[Version] = []
        for path in sorted(self._versions_dir.glob("*.json")):
            v = Version.model_validate(json.loads(path.read_text()))
            if v.thread_id == thread_id:
                versions.append(v)
        versions.sort(key=lambda v: v.created_at)
        return versions
