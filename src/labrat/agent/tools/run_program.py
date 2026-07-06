"""run_program tool: execute a multi-step tool pipeline in ONE call.

Program mode (M4 2.2): the model emits one JSON pipeline of registered-tool
steps with handle binding; the interpreter runs them sequentially and
materializes intermediate tables as DuckDB temp tables (``program_<bind>``) —
only the bounded ProgramResult summary returns to model context. Safe by
construction: steps dispatch through the standard registry MINUS run_program
itself (no nested programs), so every existing gate applies per step.
"""

from __future__ import annotations

from typing import Any

from labrat.agent.program.dsl import Program
from labrat.agent.program.interpreter import DEFAULT_MAX_STEPS, ProgramResult, run_program
from labrat.agent.tools.base import Tool, ToolContext


class RunProgramTool(Tool[Program]):
    """Run a bounded pipeline of tool steps with handle-bound intermediates."""

    mutating = True  # materializes temp tables → blocked under read-only Analyst mode

    @property
    def name(self) -> str:
        return "run_program"

    @property
    def description(self) -> str:
        return (
            f"Execute a pipeline of up to {DEFAULT_MAX_STEPS} tool steps in ONE call, e.g. "
            '{"steps": [{"tool": "run_sql", "args": {"query": "SELECT id, abstract FROM t"}, '
            '"bind": "docs"}, {"tool": "llm_extract", "args": {"table": "$docs", ...}, '
            '"bind": "facts"}]}. Later steps reference earlier ones: $handle is that '
            "step's materialized temp table (program_<handle>, usable inside SQL), and "
            "$handle.field is a scalar field of that step's output. Steps run "
            "sequentially and stop on the first error. Intermediate results stay in "
            "temp tables — only a bounded per-step summary returns; query final_table "
            "with run_sql to read the data. Programs cannot call run_program."
        )

    @property
    def input_model(self) -> type[Program]:
        return Program

    # ── schema export: Program nests ProgramStep under $defs, which the base
    # helpers drop (dangling $ref). Pass $defs through — additive per-tool
    # override; base.py is untouched.

    def anthropic_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
                "$defs": schema.get("$defs", {}),
            },
        }

    def openai_schema(self) -> dict[str, Any]:
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
                    "$defs": schema.get("$defs", {}),
                },
            },
        }

    async def execute(self, ctx: ToolContext, args: Program) -> ProgramResult:
        # Deferred import: data_tools imports this module at top level to
        # register the tool, so a top-level reverse import would be circular.
        # The sub-registry is built at EXECUTE time and EXCLUDES run_program —
        # a step {"tool": "run_program"} is an unknown-tool error (no nested
        # programs / recursion by construction).
        from labrat.agent.data_tools import build_data_tools_registry

        registry = build_data_tools_registry(include_program=False)
        return await run_program(args, ctx, registry, max_steps=DEFAULT_MAX_STEPS)
