"""Tool ABC, ToolRegistry, and DispatchResult for the agent tool-use system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError


class ToolContext:
    """Runtime context passed to every tool during execution.

    Supports both single-DB (legacy) and multi-DB construction:

      # Legacy — ctx.connection / ctx.catalog work as before:
      ToolContext(connection=conn, catalog=cat)

      # Multi-DB — agent can reach each DB by name:
      ToolContext(connections={"main": conn1, "aux": conn2}, catalogs={...}, primary="main")
    """

    def __init__(
        self,
        connection: object = None,
        catalog: object = None,
        *,
        connections: dict[str, object] | None = None,
        catalogs: dict[str, object] | None = None,
        primary: str = "primary",
        profile_name: str = "default",
        read_only: bool = False,
    ) -> None:
        if connection is not None:
            self.connections: dict[str, object] = {primary: connection}
        else:
            self.connections = dict(connections) if connections is not None else {}

        if catalog is not None:
            self.catalogs: dict[str, object] = {primary: catalog}
        else:
            self.catalogs = dict(catalogs) if catalogs is not None else {}

        self.primary = primary
        self.profile_name = profile_name
        self.read_only = read_only

    @property
    def connection(self) -> object:
        return self.connections[self.primary]

    @property
    def catalog(self) -> object:
        return self.catalogs[self.primary]


@dataclass
class DispatchResult:
    """Outcome of a single tool dispatch."""

    ok: bool
    value: object
    error: str | None = None


class Tool[InputT: BaseModel](ABC):
    """Abstract base for a single agent tool.

    Subclass, provide name/description/input_model, implement execute().
    The registry handles validation, dispatch, and schema generation.
    """

    # Class-level default consulted by the read-only Analyst-mode dispatch gate.
    # Structurally-mutating tools set this True; tools whose mutation depends on
    # the arguments (run_sql) override is_mutating() instead.
    mutating: bool = False

    def is_mutating(self, args: InputT) -> bool:
        """True if THIS call would mutate state. Default: the class-level flag."""
        _ = args
        return self.mutating

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique snake_case identifier used in API calls."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Agent-facing description of what the tool does and when to call it."""

    @property
    @abstractmethod
    def input_model(self) -> type[InputT]:
        """Pydantic model whose schema is exported and whose instance is passed to execute."""

    @abstractmethod
    async def execute(self, ctx: ToolContext, args: InputT) -> object:
        """Run the tool.  Raise any exception on failure — ToolRegistry catches it."""

    # ── schema helpers ────────────────────────────────────────────────────────

    def anthropic_schema(self) -> dict[str, Any]:
        """Return the Anthropic tool-use schema for this tool."""
        schema = self.input_model.model_json_schema()
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            },
        }

    def openai_schema(self) -> dict[str, Any]:
        """Return the OpenAI function-calling schema for this tool."""
        schema = self.input_model.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", []),
                },
            },
        }


class ToolRegistry:
    """Registry of all agent tools.

    Usage::

        registry = ToolRegistry()
        registry.register(MyTool())
        schemas = registry.to_anthropic_schemas()   # pass to the model
        result  = await registry.dispatch("my_tool", args_dict, ctx)
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> None:
        """Add a tool to the registry.  Overwrites any existing tool with the same name."""
        self._tools[tool.name] = tool

    @property
    def tools(self) -> list[Tool[Any]]:
        """Ordered list of registered tools."""
        return list(self._tools.values())

    def to_anthropic_schemas(self) -> list[dict[str, Any]]:
        """Return Anthropic tool-use schemas for all registered tools."""
        return [t.anthropic_schema() for t in self._tools.values()]

    def to_openai_schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI function-calling schemas for all registered tools."""
        return [t.openai_schema() for t in self._tools.values()]

    async def dispatch(
        self,
        name: str,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> DispatchResult:
        """Validate args and call the named tool.

        Always returns a DispatchResult — never raises.
        """
        if name not in self._tools:
            return DispatchResult(ok=False, value=None, error=f"Unknown tool: {name!r}")

        tool = self._tools[name]

        try:
            parsed = tool.input_model.model_validate(args)
        except ValidationError as exc:
            return DispatchResult(ok=False, value=None, error=str(exc))

        if ctx.read_only and tool.is_mutating(parsed):  # pyright: ignore[reportArgumentType]
            return DispatchResult(ok=False, value=None, error="blocked: read-only Analyst mode")

        try:
            result = await tool.execute(ctx, parsed)  # pyright: ignore[reportArgumentType]
            return DispatchResult(ok=True, value=result)
        except Exception as exc:
            return DispatchResult(ok=False, value=None, error=str(exc))
