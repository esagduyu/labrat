"""RecallMemoriesTool must key off ctx.profile_name, not a nonexistent ctx.profile."""

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.recall_memories import RecallMemoriesTool
from labrat.memory.model import Memory, MemoryKind, MemoryScope
from labrat.memory.store import MemoryStore


async def test_recall_uses_ctx_profile_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore(memory_dir=tmp_path)
    store.append(
        Memory(
            profile="prof-a",
            scope=MemoryScope.global_,
            kind=MemoryKind.explicit_user_rule,
            text="always exclude test orders",
        )
    )
    # Point the tool's module-level singleton at the temp store.
    import labrat.agent.tools.recall_memories as mod

    monkeypatch.setattr(mod, "_memory_store", store)  # type: ignore[attr-defined]

    tool = RecallMemoriesTool()
    ctx = ToolContext(profile_name="prof-a")
    args = tool.input_model.model_validate({"context": "test orders"})
    out = await tool.execute(ctx, args)
    assert "exclude test orders" in str(out)


async def test_recall_defaults_to_default_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default-constructed ToolContext still reads the 'default' profile."""
    store = MemoryStore(memory_dir=tmp_path)
    store.append(
        Memory(
            profile="default",
            scope=MemoryScope.global_,
            kind=MemoryKind.explicit_user_rule,
            text="always exclude cancelled orders",
        )
    )
    import labrat.agent.tools.recall_memories as mod

    monkeypatch.setattr(mod, "_memory_store", store)  # type: ignore[attr-defined]

    tool = RecallMemoriesTool()
    ctx = ToolContext()  # profile_name defaults to "default"
    args = tool.input_model.model_validate({"context": "cancelled orders"})
    out = await tool.execute(ctx, args)
    assert "exclude cancelled orders" in str(out)
