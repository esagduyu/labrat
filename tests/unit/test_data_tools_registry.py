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
