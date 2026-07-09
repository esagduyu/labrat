from labrat.agent.prompts import build_system_prompt, build_tui_system_prompt


def test_tui_prompt_is_base_plus_addendum() -> None:
    base = build_system_prompt("duckdb")
    tui = build_tui_system_prompt("duckdb")
    assert tui.startswith(base)
    assert "draft_sql" in tui and "create_chart" in tui
