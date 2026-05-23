"""Tests for Tool ABC, ToolRegistry, and dispatch logic."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry

# ── test fixtures ─────────────────────────────────────────────────────────────


class _EchoInput(BaseModel):
    message: str
    repeat: int = 1


class _EchoTool(Tool[_EchoInput]):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Repeat a message N times."

    @property
    def input_model(self) -> type[_EchoInput]:
        return _EchoInput

    async def execute(self, ctx: ToolContext, args: _EchoInput) -> object:
        return args.message * args.repeat


class _BoomTool(Tool[_EchoInput]):
    @property
    def name(self) -> str:
        return "boom"

    @property
    def description(self) -> str:
        return "Always raises."

    @property
    def input_model(self) -> type[_EchoInput]:
        return _EchoInput

    async def execute(self, ctx: ToolContext, args: _EchoInput) -> object:
        raise RuntimeError("kaboom")


@pytest.fixture()
def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(_EchoTool())
    r.register(_BoomTool())
    return r


@pytest.fixture()
def ctx() -> ToolContext:
    return ToolContext(connection=object(), catalog=object())


# ── schema: Anthropic format ─────────────────────────────────────────────────


def test_anthropic_schema_top_level_keys(registry: ToolRegistry) -> None:
    """Each Anthropic schema has name, description, input_schema."""
    schemas = registry.to_anthropic_schemas()
    echo = next(s for s in schemas if s["name"] == "echo")
    assert echo["description"] == "Repeat a message N times."
    assert "input_schema" in echo


def test_anthropic_schema_properties(registry: ToolRegistry) -> None:
    """input_schema.properties contains all fields from the Pydantic model."""
    schemas = registry.to_anthropic_schemas()
    echo = next(s for s in schemas if s["name"] == "echo")
    props = echo["input_schema"]["properties"]
    assert "message" in props
    assert "repeat" in props


def test_anthropic_schema_required(registry: ToolRegistry) -> None:
    """required only lists fields without defaults."""
    schemas = registry.to_anthropic_schemas()
    echo = next(s for s in schemas if s["name"] == "echo")
    required = echo["input_schema"].get("required", [])
    assert "message" in required
    assert "repeat" not in required


def test_anthropic_schema_type_object(registry: ToolRegistry) -> None:
    """input_schema.type is 'object'."""
    schemas = registry.to_anthropic_schemas()
    for s in schemas:
        assert s["input_schema"]["type"] == "object"


# ── schema: OpenAI format ────────────────────────────────────────────────────


def test_openai_schema_top_level_keys(registry: ToolRegistry) -> None:
    """Each OpenAI schema has type='function' and a nested function object."""
    schemas = registry.to_openai_schemas()
    echo = next(s for s in schemas if s["function"]["name"] == "echo")
    assert echo["type"] == "function"
    assert "parameters" in echo["function"]


def test_openai_schema_parameters_properties(registry: ToolRegistry) -> None:
    """function.parameters.properties contains all Pydantic model fields."""
    schemas = registry.to_openai_schemas()
    echo = next(s for s in schemas if s["function"]["name"] == "echo")
    props = echo["function"]["parameters"]["properties"]
    assert "message" in props
    assert "repeat" in props


# ── dispatch: success paths ───────────────────────────────────────────────────


async def test_dispatch_returns_result(registry: ToolRegistry, ctx: ToolContext) -> None:
    """dispatch() returns ok=True and the tool's return value on success."""
    result = await registry.dispatch("echo", {"message": "hi", "repeat": 3}, ctx)
    assert result.ok is True
    assert result.value == "hihihi"
    assert result.error is None


async def test_dispatch_default_args(registry: ToolRegistry, ctx: ToolContext) -> None:
    """dispatch() applies Pydantic field defaults when args are omitted."""
    result = await registry.dispatch("echo", {"message": "x"}, ctx)
    assert result.ok is True
    assert result.value == "x"


# ── dispatch: error paths ─────────────────────────────────────────────────────


async def test_dispatch_unknown_tool(registry: ToolRegistry, ctx: ToolContext) -> None:
    """dispatch() returns ok=False for unregistered tool names."""
    result = await registry.dispatch("no_such_tool", {}, ctx)
    assert result.ok is False
    assert result.error is not None


async def test_dispatch_invalid_args(registry: ToolRegistry, ctx: ToolContext) -> None:
    """dispatch() returns ok=False when args fail Pydantic validation."""
    result = await registry.dispatch("echo", {"wrong_key": 99}, ctx)
    assert result.ok is False
    assert result.error is not None


async def test_dispatch_tool_raises(registry: ToolRegistry, ctx: ToolContext) -> None:
    """dispatch() catches exceptions from execute() and returns ok=False."""
    result = await registry.dispatch("boom", {"message": "x"}, ctx)
    assert result.ok is False
    assert "kaboom" in (result.error or "")


# ── registration ──────────────────────────────────────────────────────────────


def test_registry_tools_property(registry: ToolRegistry) -> None:
    """registry.tools lists all registered Tool instances."""
    names = {t.name for t in registry.tools}
    assert names == {"echo", "boom"}


def test_schema_count_matches_registered(registry: ToolRegistry) -> None:
    """to_anthropic_schemas() and to_openai_schemas() return one entry per tool."""
    assert len(registry.to_anthropic_schemas()) == 2
    assert len(registry.to_openai_schemas()) == 2
