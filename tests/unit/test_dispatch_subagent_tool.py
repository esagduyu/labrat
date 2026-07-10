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


class _CapturingRunner:
    def __init__(self) -> None:
        self.seed: str | None = None
        self.refs: list[str] | None = None
        self.budgets: tuple[int, int] | None = None

    async def __call__(
        self,
        *,
        seed_prompt: str,
        artifact_refs: list[str],
        max_turns: int,
        max_tool_calls: int,
    ) -> tuple[str, int, int]:
        self.seed = seed_prompt
        self.refs = artifact_refs
        self.budgets = (max_turns, max_tool_calls)
        return ("sub answer", 2, 3)


async def test_seed_sections_and_passthrough(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))  # empty store → no Scent section
    runner = _CapturingRunner()
    ctx = ToolContext(subagent_runner=runner)
    tool = DispatchSubagentTool()
    out = await tool.execute(
        ctx,
        _args(
            sub_task="count the orders",
            context_hint="only completed ones",
            artifact_refs=["result://abc/0001"],
            max_turns=4,
        ),
    )
    assert out.ok is True and out.final_text == "sub answer"
    assert out.turns_used == 2 and out.tool_calls_used == 3
    assert runner.seed is not None
    assert runner.seed.startswith("## Sub-task\n\ncount the orders")
    assert "only completed ones" in runner.seed
    assert "## Relevant reference notes" not in runner.seed  # empty store → omitted
    assert "result://abc/0001" not in runner.seed  # refs go to the runner, not the seed text
    assert runner.refs == ["result://abc/0001"]
    assert runner.budgets == (4, 10)


async def test_seed_includes_scent_when_docs_exist(tmp_path, monkeypatch) -> None:
    from labrat.maze.document import ScentDoc, Section, render_document

    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    scent = tmp_path / "labrat_maze" / "scent"
    scent.mkdir(parents=True)
    doc = ScentDoc(
        domain="orders",
        sections=[Section(heading="Gotchas", body="- exclude test orders", source="verified")],
    )
    (scent / "orders.md").write_text(render_document(doc), encoding="utf-8")
    runner = _CapturingRunner()
    out = await DispatchSubagentTool().execute(
        ToolContext(subagent_runner=runner), _args(sub_task="orders gotchas please")
    )
    assert out.ok is True
    assert "## Relevant reference notes" in (runner.seed or "")
    assert "exclude test orders" in (runner.seed or "")


async def test_scent_notes_nonempty_for_seeded_store(tmp_path, monkeypatch) -> None:
    """Red test for the _scent_notes silent-drift hazard.

    SearchReferenceDocsTool's input is built via a raw dict + model_validate inside
    _scent_notes; if that tool's _Input fields ever rename/drift, the mismatch raises
    a ValidationError that _scent_notes silently swallows into "" (retrieval must
    never block dispatch). That fail-open is correct for genuine retrieval errors,
    but it must not silently mask a construction bug. This test seeds a real store
    and asserts non-empty notes come back, so a field-name drift shows up as a
    failing assertion here instead of vanishing into an empty string.
    """
    from labrat.agent.tools.dispatch_subagent import _scent_notes
    from labrat.maze.document import ScentDoc, Section, render_document

    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    scent = tmp_path / "labrat_maze" / "scent"
    scent.mkdir(parents=True)
    doc = ScentDoc(
        domain="orders",
        sections=[Section(heading="Gotchas", body="- exclude test orders", source="verified")],
    )
    (scent / "orders.md").write_text(render_document(doc), encoding="utf-8")

    notes = await _scent_notes(ToolContext(), "orders gotchas please")
    assert notes != ""
    assert "exclude test orders" in notes


async def test_runner_exception_fails_open(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))

    class _Boom:
        async def __call__(self, **_: object) -> tuple[str, int, int]:
            raise RuntimeError("provider melted")

    out = await DispatchSubagentTool().execute(ToolContext(subagent_runner=_Boom()), _args())
    assert out.ok is False and "provider melted" in (out.error or "")
