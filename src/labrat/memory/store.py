"""JSONL-backed MemoryStore (M31).

One file per profile: ~/.local/share/labrat/memories/{profile}.jsonl
Soft-deletes are implemented by rewriting the file without the removed entry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from labrat.memory.model import Memory

_DEFAULT_MEMORY_DIR = Path.home() / ".local" / "share" / "labrat" / "memories"


class MemoryStore:
    def __init__(self, memory_dir: Path | None = None) -> None:
        self._memory_dir = memory_dir or _DEFAULT_MEMORY_DIR

    def append(self, memory: Memory) -> None:
        path = self._path(memory.profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(memory.model_dump_json() + "\n")

    def read_profile(self, profile: str) -> list[Memory]:
        path = self._path(profile)
        if not path.exists():
            return []
        memories: list[Memory] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                memories.append(Memory.model_validate_json(line))
        return memories

    def delete(self, profile: str, memory_id: str) -> None:
        memories = [m for m in self.read_profile(profile) if m.id != memory_id]
        self._rewrite(profile, memories)

    def increment_applied(self, profile: str, memory_id: str) -> None:
        memories = self.read_profile(profile)
        for m in memories:
            if m.id == memory_id:
                m.application_count += 1
                m.last_applied_at = datetime.now(tz=UTC)
                break
        self._rewrite(profile, memories)

    def _path(self, profile: str) -> Path:
        return self._memory_dir / f"{profile}.jsonl"

    def _rewrite(self, profile: str, memories: list[Memory]) -> None:
        path = self._path(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        suffix = "\n" if memories else ""
        path.write_text("\n".join(m.model_dump_json() for m in memories) + suffix)
