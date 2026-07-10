from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.run_sql import RunSqlTool


def test_registry_uses_injected_run_sql_instance() -> None:
    calls: list[str] = []
    custom = RunSqlTool(on_draft=lambda sql: calls.append(sql))
    registry = build_data_tools_registry(run_sql_tool=custom)
    run_sql = next(t for t in registry.tools if t.name == "run_sql")
    assert run_sql is custom


def test_registry_default_run_sql_unchanged() -> None:
    registry = build_data_tools_registry()
    names = {t.name for t in registry.tools}
    assert "run_sql" in names and "run_program" in names


def test_include_dispatch_false_excludes_dispatch_subagent() -> None:
    """Mirrors include_program's mechanics — used by run_program's step sub-registry
    to close the laundering path (a program step can't fire dispatch_subagent)."""
    registry = build_data_tools_registry(include_dispatch=False)
    names = {t.name for t in registry.tools}
    assert "dispatch_subagent" not in names


def test_include_dispatch_true_by_default() -> None:
    registry = build_data_tools_registry()
    names = {t.name for t in registry.tools}
    assert "dispatch_subagent" in names
