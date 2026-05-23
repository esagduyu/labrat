"""Tests for Memory model (M31)."""

from __future__ import annotations

from datetime import UTC, datetime

from labrat.memory.model import Memory, MemoryKind, MemoryScope


def _mem(**kw: object) -> Memory:
    defaults: dict[str, object] = {
        "profile": "dev",
        "scope": MemoryScope.global_,
        "kind": MemoryKind.edit_derived,
        "text": "always join orders to users via user_id",
    }
    defaults.update(kw)
    return Memory(**defaults)  # type: ignore[arg-type]


# ── model fields ──────────────────────────────────────────────────────────────


def test_memory_has_required_fields() -> None:
    mem = _mem()
    assert mem.profile == "dev"
    assert mem.scope == MemoryScope.global_
    assert mem.kind == MemoryKind.edit_derived
    assert mem.text == "always join orders to users via user_id"


def test_memory_id_auto_generated() -> None:
    a = _mem()
    b = _mem()
    assert a.id != b.id


def test_memory_created_at_defaults_to_now() -> None:
    before = datetime.now(tz=UTC)
    mem = _mem()
    after = datetime.now(tz=UTC)
    assert before <= mem.created_at <= after


def test_memory_application_count_starts_at_zero() -> None:
    mem = _mem()
    assert mem.application_count == 0


def test_memory_optional_fields_default_to_none() -> None:
    mem = _mem()
    assert mem.source is None
    assert mem.last_applied_at is None
    assert mem.embedding is None
    assert mem.table_scope is None


def test_memory_scope_table_specific() -> None:
    mem = _mem(scope=MemoryScope.table_, table_scope="orders")
    assert mem.scope == MemoryScope.table_
    assert mem.table_scope == "orders"


def test_memory_kinds() -> None:
    assert MemoryKind.edit_derived.value == "edit_derived"
    assert MemoryKind.chat_correction.value == "chat_correction"
    assert MemoryKind.explicit_user_rule.value == "explicit_user_rule"


def test_memory_serialization_roundtrip() -> None:
    mem = _mem()
    restored = Memory.model_validate_json(mem.model_dump_json())
    assert restored.id == mem.id
    assert restored.text == mem.text
