"""Tests for MemoryStore (M31)."""

from __future__ import annotations

from pathlib import Path

from labrat.memory.model import Memory, MemoryKind, MemoryScope


def _mem(profile: str = "dev", text: str = "join on user_id") -> Memory:
    return Memory(
        profile=profile,
        scope=MemoryScope.global_,
        kind=MemoryKind.edit_derived,
        text=text,
    )


# ── MemoryStore ────────────────────────────────────────────────────────────────


def test_store_append_and_read(tmp_path: Path) -> None:
    from labrat.memory.store import MemoryStore

    store = MemoryStore(memory_dir=tmp_path)
    mem = _mem()
    store.append(mem)
    results = store.read_profile("dev")
    assert len(results) == 1
    assert results[0].id == mem.id


def test_store_read_returns_only_given_profile(tmp_path: Path) -> None:
    from labrat.memory.store import MemoryStore

    store = MemoryStore(memory_dir=tmp_path)
    store.append(_mem(profile="dev", text="dev memory"))
    store.append(_mem(profile="prod", text="prod memory"))
    dev_mems = store.read_profile("dev")
    assert len(dev_mems) == 1
    assert dev_mems[0].text == "dev memory"


def test_store_delete_removes_by_id(tmp_path: Path) -> None:
    from labrat.memory.store import MemoryStore

    store = MemoryStore(memory_dir=tmp_path)
    mem = _mem()
    store.append(mem)
    store.delete(profile="dev", memory_id=mem.id)
    assert store.read_profile("dev") == []


def test_store_delete_nonexistent_id_is_noop(tmp_path: Path) -> None:
    from labrat.memory.store import MemoryStore

    store = MemoryStore(memory_dir=tmp_path)
    store.delete(profile="dev", memory_id="no-such-id")  # must not raise


def test_store_update_increments_application_count(tmp_path: Path) -> None:
    from labrat.memory.store import MemoryStore

    store = MemoryStore(memory_dir=tmp_path)
    mem = _mem()
    store.append(mem)
    store.increment_applied(profile="dev", memory_id=mem.id)
    updated = store.read_profile("dev")[0]
    assert updated.application_count == 1


def test_store_multiple_appends_all_persisted(tmp_path: Path) -> None:
    from labrat.memory.store import MemoryStore

    store = MemoryStore(memory_dir=tmp_path)
    for i in range(5):
        store.append(_mem(text=f"memory {i}"))
    assert len(store.read_profile("dev")) == 5


def test_store_file_is_jsonl(tmp_path: Path) -> None:
    from labrat.memory.store import MemoryStore

    store = MemoryStore(memory_dir=tmp_path)
    store.append(_mem())
    jsonl_file = tmp_path / "dev.jsonl"
    assert jsonl_file.exists()
    lines = jsonl_file.read_text().strip().splitlines()
    assert len(lines) == 1
