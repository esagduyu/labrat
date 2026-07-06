"""Wire the correction extractors into a session-boundary harvest loop (T2b v1).

The extractors already exist (memory/extractor.py) but had no callers. This runs
them on a session's events/corrections and persists the derived memories. Gated by
`enabled` so benchmark paths never harvest.
"""

from __future__ import annotations

from labrat.history.events import QueryEvent
from labrat.memory.extractor import ChatCorrectionExtractor, EditExtractor, LLMFn
from labrat.memory.model import Memory
from labrat.memory.store import MemoryStore


class SessionHarvester:
    def __init__(
        self, profile: str, llm_fn: LLMFn, store: MemoryStore, enabled: bool = True
    ) -> None:
        self._profile = profile
        self._store = store
        self._enabled = enabled
        self._edit = EditExtractor(profile, llm_fn)
        self._chat = ChatCorrectionExtractor(profile, llm_fn)

    async def harvest_events(self, events: list[QueryEvent]) -> list[Memory]:
        if not self._enabled:
            return []
        out: list[Memory] = []
        for ev in events:
            if not ev.edit_diff:
                continue
            for mem in await self._edit.extract(ev):
                self._store.append(mem)
                out.append(mem)
        return out

    async def harvest_correction(self, user_message: str, context_sql: str) -> list[Memory]:
        if not self._enabled:
            return []
        out: list[Memory] = []
        for mem in await self._chat.extract(user_message, context_sql):
            self._store.append(mem)
            out.append(mem)
        return out
