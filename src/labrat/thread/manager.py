"""ThreadManager: business logic for threads and versions (M17)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from labrat.thread.model import Thread, Version
from labrat.thread.store import ThreadStore


class ThreadManager:
    """High-level API for creating, loading, and branching threads."""

    def __init__(self, store_dir: Path | None = None) -> None:
        self._store = ThreadStore(store_dir=store_dir)

    # ── thread operations ─────────────────────────────────────────────────────

    def create_thread(
        self,
        name: str,
        profile_name: str,
        parent_version_id: str | None = None,
    ) -> Thread:
        """Create a new thread and persist it."""
        thread = Thread(
            id=str(uuid.uuid4()),
            name=name,
            profile_name=profile_name,
            created_at=datetime.now(tz=UTC),
            parent_version_id=parent_version_id,
        )
        self._store.append_thread(thread)
        return thread

    def list_threads(self) -> list[Thread]:
        """Return all threads sorted by creation time."""
        return sorted(self._store.load_threads(), key=lambda t: t.created_at)

    def get_thread(self, thread_id: str) -> Thread | None:
        """Return the thread with the given ID, or None if not found."""
        return next((t for t in self._store.load_threads() if t.id == thread_id), None)

    # ── version operations ────────────────────────────────────────────────────

    def append_version(
        self,
        thread_id: str,
        sql: str,
        chat_history: list[dict[str, Any]],
        results_ref: str | None = None,
    ) -> Version:
        """Append a new version to a thread and persist it."""
        version = Version(
            id=str(uuid.uuid4()),
            thread_id=thread_id,
            sql=sql,
            results_ref=results_ref,
            chat_history=chat_history,
            created_at=datetime.now(tz=UTC),
        )
        self._store.save_version(version)
        return version

    def get_versions(self, thread_id: str) -> list[Version]:
        """Return all versions for a thread in creation order."""
        return self._store.load_versions_for_thread(thread_id)

    # ── branching ─────────────────────────────────────────────────────────────

    def branch_from_version(self, version_id: str, name: str) -> Thread:
        """Create a new thread branching from an existing version.

        The new thread's parent_version_id points back to the given version,
        recording the branch point in the version graph.
        """
        version = self._store.load_version(version_id)
        if version is None:
            raise ValueError(f"Version {version_id!r} not found")
        source_thread = self.get_thread(version.thread_id)
        profile_name = source_thread.profile_name if source_thread else "default"
        return self.create_thread(
            name=name,
            profile_name=profile_name,
            parent_version_id=version_id,
        )
