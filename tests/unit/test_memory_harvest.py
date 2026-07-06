from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from labrat.history.events import QueryEvent
from labrat.memory.harvest import SessionHarvester
from labrat.memory.store import MemoryStore


async def _fake_llm(_prompt: str) -> str:
    return "Filter soft-deleted rows with deleted_at IS NULL."


def _event() -> QueryEvent:
    # QueryEvent requires timestamp, profile, thread_id, version_id, sql_final.
    return QueryEvent(
        timestamp=datetime.now(tz=UTC),
        profile="p1",
        thread_id="t1",
        version_id="v1",
        sql_final="SELECT 1 WHERE deleted_at IS NULL",
        edit_diff="- SELECT 1\n+ SELECT 1 WHERE deleted_at IS NULL",
    )


async def test_harvest_events_appends_edit_memories(tmp_path: Path) -> None:
    store = MemoryStore(memory_dir=tmp_path)
    h = SessionHarvester(profile="p1", llm_fn=_fake_llm, store=store, enabled=True)
    mems = await h.harvest_events([_event()])
    assert len(mems) == 1
    assert "soft-deleted" in mems[0].text
    assert store.read_profile("p1")  # persisted


async def test_disabled_harvester_is_noop(tmp_path: Path) -> None:
    store = MemoryStore(memory_dir=tmp_path)
    h = SessionHarvester(profile="p1", llm_fn=_fake_llm, store=store, enabled=False)
    assert await h.harvest_events([_event()]) == []
    assert store.read_profile("p1") == []
