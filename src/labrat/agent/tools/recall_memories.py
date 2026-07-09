"""Agent tool: recall_memories — retrieve relevant past corrections (M31)."""

from __future__ import annotations

from pydantic import BaseModel

from labrat.agent.tools.base import Tool, ToolContext
from labrat.memory.retrieval import relevant_memories
from labrat.memory.store import MemoryStore

_memory_store = MemoryStore()


class _Input(BaseModel):
    context: str
    tables: list[str] = []
    limit: int = 5


class RecallMemoriesTool(Tool[_Input]):
    @property
    def name(self) -> str:
        return "recall_memories"

    @property
    def description(self) -> str:
        return (
            "Recall durable corrections and rules learned from past interactions. "
            "Call during planning to avoid repeating known mistakes."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> object:
        profile: str = ctx.profile_name
        all_memories = _memory_store.read_profile(profile)
        top = relevant_memories(
            all_memories, tables=args.tables, query=args.context, limit=args.limit
        )
        if not top:
            return {"memories": [], "note": "No relevant memories found."}
        return {
            "memories": [
                {"text": m.text, "kind": m.kind.value, "applied": m.application_count} for m in top
            ]
        }
