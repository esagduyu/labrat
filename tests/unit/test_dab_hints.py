"""--hints loader appends the benchmark's hint file to the base description (not replace).

Mirrors DAB's own run_agent.py (`db_description += "\\n\\n" + hints`). Self-contained
(builds a fake DAB tree) so it never depends on the ~/repos/DataAgentBench checkout.
"""

from __future__ import annotations

import json
from pathlib import Path

from labrat.eval.benchmarks.dab.suite import DabSuite

_BASE = "SCHEMA: table tracks(track_id int, title text). Full base description here."
_HINT = "HINTS:\n- tracks has duplicates; do entity resolution, not exact match."


def _fake_dab(tmp_path: Path) -> Path:
    ds = tmp_path / "query_demo"
    (ds / "query1").mkdir(parents=True)
    (ds / "db_config.yaml").write_text(
        "db_clients:\n  main:\n    db_type: duckdb\n    db_path: query_dataset/x.db\n"
    )
    (ds / "db_description.txt").write_text(_BASE)
    (ds / "db_description_withhint.txt").write_text(_HINT)
    (ds / "query1" / "query.json").write_text(json.dumps("How many unique tracks?"))
    (ds / "query1" / "validate.py").write_text("# validator\n")
    return tmp_path


def _demo_prompt(dab_dir: Path, *, hints: bool) -> str:
    suite = DabSuite(dab_dir=dab_dir, hints=hints, driver="claude-mcp")
    tasks = [t for t in suite.tasks() if t.id == "demo:1"]
    assert tasks, "fake dataset should yield demo:1"
    return tasks[0].prompt


def test_hints_off_uses_base_only(tmp_path: Path) -> None:
    prompt = _demo_prompt(_fake_dab(tmp_path), hints=False)
    assert _BASE in prompt
    assert "entity resolution" not in prompt  # hint NOT injected


def test_hints_on_appends_hint_to_base(tmp_path: Path) -> None:
    prompt = _demo_prompt(_fake_dab(tmp_path), hints=True)
    # the bug was replace-not-append: base MUST survive alongside the hint
    assert _BASE in prompt, "schema description must not be dropped when hints on"
    assert "entity resolution" in prompt, "hint must be appended when hints on"
