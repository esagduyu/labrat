"""dispatch_subagent: self-error without a runner, budget clamps, registration."""

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.dispatch_subagent import DispatchSubagentTool


def _args(**over: object) -> object:
    tool = DispatchSubagentTool()
    base: dict[str, object] = {"sub_task": "count the orders"}
    base.update(over)
    return tool.input_model.model_validate(base)


async def test_self_error_without_runner() -> None:
    tool = DispatchSubagentTool()
    out = await tool.execute(ToolContext(), _args())
    assert out.ok is False
    assert out.final_text == ""
    assert out.error is not None and "no subagent runner" in out.error


def test_budgets_clamped() -> None:
    args = _args(max_turns=99, max_tool_calls=0)
    assert args.max_turns == 8
    assert args.max_tool_calls == 1
    defaults = _args()
    assert defaults.max_turns == 6 and defaults.max_tool_calls == 10


def test_output_declares_json_ledger_payload() -> None:
    from labrat.agent.tools.dispatch_subagent import _Output

    out = _Output(ok=True, final_text="answer", turns_used=1, tool_calls_used=2)
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "json"
    assert isinstance(obj, dict) and obj["final_text"] == "answer"


def test_registered_in_standard_registry() -> None:
    names = {t.name for t in build_data_tools_registry().tools}
    assert "dispatch_subagent" in names


def test_ctx_field_defaults_none() -> None:
    assert ToolContext().subagent_runner is None
