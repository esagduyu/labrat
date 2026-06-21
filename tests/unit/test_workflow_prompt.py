"""system_base.md carries the #30 SOP + workflow instruction."""

from __future__ import annotations

from pathlib import Path


def test_prompt_has_workflow_sop_and_repair_guidance() -> None:
    text = Path("src/labrat/agent/prompts/system_base.md").read_text(encoding="utf-8")
    assert "`workflow`" in text  # the tracking tool is named
    assert "error_category" in text and "hint" in text  # repair guidance
    for word in ("Clarify", "Ground", "Repair", "Verify joins"):
        assert word in text  # representative SOP steps
