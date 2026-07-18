"""Tool ABC, ToolRegistry, and DispatchResult for the agent tool-use system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from polars.exceptions import PanicException
from pydantic import BaseModel, ValidationError

# One-shot async LLM call: prompt in, raw reply text out. Structurally identical to
# the alias in labrat.agent.verifier (kept there for its own callers); tools import
# this one. run_agent_task injects an implementation onto ToolContext.llm_fn.
LLMFn = Callable[[str], Awaitable[str]]

# Large batched classifiers may overlap a small number of independent one-shot
# requests, but must never create an unbounded provider fan-out.
MAX_LLM_CLASSIFY_CONCURRENCY = 2


class SubagentRunner(Protocol):
    """Runs a scoped sub-agent loop; injected by build_agent_session (llm_fn precedent).

    The runner owns the ResultStore, so it resolves ``artifact_refs`` to previews
    and splices them into the seed; the tool never touches the store. Returns
    ``(final_text, turns_used, tool_calls_used)``.
    """

    async def __call__(
        self,
        *,
        seed_prompt: str,
        artifact_refs: list[str],
        max_turns: int,
        max_tool_calls: int,
    ) -> tuple[str, int, int]: ...


class ToolContext:
    """Runtime context passed to every tool during execution.

    Supports both single-DB (legacy) and multi-DB construction:

      # Legacy — ctx.connection / ctx.catalog work as before:
      ToolContext(connection=conn, catalog=cat)

      # Multi-DB — agent can reach each DB by name:
      ToolContext(connections={"main": conn1, "aux": conn2}, catalogs={...}, primary="main")

    ``llm_fn`` is an optional one-shot LLM callable (prompt -> reply) injected by
    ``run_agent_task`` for the per-row llm_extract/llm_classify tools; it defaults
    to None, and every deterministic context leaves it None. ``subagent_runner``
    follows the same pattern for scoped sub-agent dispatch; deterministic contexts
    leave it None. ``active_maps`` follows the same injection pattern for the Map
    activation retrieval filter (search_reference_docs / search_trails): a
    non-empty list of Map slugs narrows retrieval to those Maps' member domains;
    None or an empty list (every existing/benchmark construction site) leaves
    retrieval byte-identical to today.

    ``raise_rate_limits`` (default False) is the benchmark fail-fast opt-in:
    when True, provider rate-limit errors (429) escape ``ToolRegistry.dispatch``
    and the llm_extract/llm_classify tools instead of degrading to a structured
    tool error, so eval runners can persist a retryable infra row and stop.
    Product hosts (TUI, MCP server) leave it False and keep the historical
    "dispatch always returns a DispatchResult" contract.
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
        llm_fn: LLMFn | None = None,
        llm_classify_fn: LLMFn | None = None,
        subagent_runner: SubagentRunner | None = None,
        active_maps: list[str] | None = None,
        llm_classify_concurrency: int = 1,
        llm_classify_row_budget: int | None = None,
        raise_rate_limits: bool = False,
        hybrid_retrieval: bool = False,
        llm_classify_backend: str = "llm",
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
        self.llm_fn = llm_fn
        self.llm_classify_fn = llm_classify_fn
        self.subagent_runner = subagent_runner
        self.active_maps = active_maps
        if not 1 <= llm_classify_concurrency <= MAX_LLM_CLASSIFY_CONCURRENCY:
            raise ValueError(
                f"llm_classify_concurrency must be between 1 and {MAX_LLM_CLASSIFY_CONCURRENCY}"
            )
        if llm_classify_row_budget is not None and llm_classify_row_budget < 1:
            raise ValueError("llm_classify_row_budget must be positive when set")
        self.llm_classify_concurrency = llm_classify_concurrency
        self.llm_classify_row_budget = llm_classify_row_budget
        self.llm_classify_rows_used = 0
        if llm_classify_backend not in ("llm", "local-embed"):
            raise ValueError(
                f"llm_classify_backend must be 'llm' or 'local-embed', got {llm_classify_backend!r}"
            )
        self.llm_classify_backend = llm_classify_backend
        self.raise_rate_limits = raise_rate_limits
        # Hybrid (lexical+semantic RRF) retrieval for search_reference_docs /
        # search_trails. Additive, default OFF: no eval/MCP path sets it, so the
        # benchmark retrieval behavior is byte-identical (same pattern as
        # ``active_maps``). Set by the TUI from Profile.hybrid_retrieval.
        self.hybrid_retrieval = hybrid_retrieval

    @property
    def connection(self) -> object:
        return self.connections[self.primary]

    @property
    def catalog(self) -> object:
        return self.catalogs[self.primary]


class ResultConversionError(RuntimeError):
    """A polars result could not be converted to Python values.

    Raised by :func:`stringify_rows` when row extraction fails — most notably
    pyo3's ``PanicException`` (a ``BaseException``!) on out-of-range dates,
    which would otherwise sail through every ``except Exception`` isolation
    layer and kill the host process (observed on DAB deps_dev_v1, 2026-07-16).
    Normal ``Exception`` subclass so existing per-tool error handling applies.
    """


def stringify_rows(df: Any) -> list[list[str]]:
    """Convert a polars frame's rows to strings, containing pyo3 panics.

    Use this instead of bare ``df.iter_rows()`` at every tool-output seam.
    """
    try:
        return [[str(v) if v is not None else "" for v in row] for row in df.iter_rows()]
    except (Exception, PanicException) as exc:
        raise ResultConversionError(
            f"query succeeded but its result rows could not be converted: {exc}. "
            "A column value is unrepresentable in Python (e.g. an out-of-range "
            "date) — CAST the offending column to VARCHAR in the query and retry."
        ) from None


def reraise_if_rate_limited(ctx: ToolContext, exc: Exception) -> None:
    """Re-raise ``exc`` when the context opted into rate-limit fail-fast.

    The single seam behind ``ctx.raise_rate_limits``: call it first in any
    ``except Exception`` block that would otherwise degrade a provider 429 to a
    structured tool error. Default (product) contexts return without effect;
    opted-in contexts (benchmark runners) get the original exception re-raised
    with its traceback intact so the harness can fail fast.
    """
    if not ctx.raise_rate_limits:
        return
    from labrat.agent.providers.base import is_rate_limit_error

    if is_rate_limit_error(exc):
        raise exc


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

        Always returns a DispatchResult — failures degrade to a structured tool
        error the model can see and react to. The one opt-in exception:
        contexts with ``raise_rate_limits=True`` (benchmark runners) re-raise
        provider rate-limit errors so the harness can fail fast and preserve a
        retryable infrastructure row.
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
        except PanicException as exc:
            # pyo3 panics are BaseExceptions; contain them here so a single
            # pathological value cannot kill the host process/run.
            return DispatchResult(ok=False, value=None, error=str(exc))
        except Exception as exc:
            reraise_if_rate_limited(ctx, exc)
            return DispatchResult(ok=False, value=None, error=str(exc))
