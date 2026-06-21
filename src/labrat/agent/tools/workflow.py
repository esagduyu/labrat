"""workflow tool: track progress through the data-analysis SOP (FEATURE_ROADMAP #30).

Record-and-inspect, fail-open: the agent marks each SOP step as it walks them; the tool
returns the rendered checklist so progress is visible. It never blocks the agent.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.agent.workflow import STEP_KEYS, WorkflowState


class _Input(BaseModel):
    step: str | None = Field(
        default=None,
        description=(
            "Workflow step key to update; one of: " + ", ".join(STEP_KEYS) + ". "
            "Omit to just return the current checklist."
        ),
    )
    status: str = Field(
        default="done",
        description="New status for the step: 'doing' (started) or 'done' (finished).",
    )
    note: str | None = Field(default=None, description="Optional short note recorded on the step.")


class _Output(BaseModel):
    checklist: str
    statuses: dict[str, str]
    repair_attempts: int


class WorkflowTool(Tool[_Input]):
    """Track and inspect progress through the data-analysis workflow (fail-open)."""

    def __init__(self) -> None:
        self._states: dict[str, WorkflowState] = {}

    @property
    def name(self) -> str:
        return "workflow"

    @property
    def description(self) -> str:
        return (
            "Track your progress through the data-analysis workflow. Call with a `step` key plus "
            "`status` ('doing' when you start a step, 'done' when you finish) to record progress; "
            "call with no arguments to see the current checklist. Walk the steps in order. Returns "
            "the rendered checklist. Advisory — it never blocks."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        state = self._states.setdefault(ctx.profile_name, WorkflowState.new())
        if args.step is not None:
            if args.step not in STEP_KEYS:
                raise ValueError(
                    f"unknown workflow step {args.step!r}; valid: {', '.join(STEP_KEYS)}"
                )
            if args.step == "repair" and args.status == "doing":
                state.note_repair_failure()
            state.mark(args.step, args.status, note=args.note)
        return _Output(
            checklist=state.render(),
            statuses=dict(state.statuses),
            repair_attempts=state.repair_attempts,
        )
