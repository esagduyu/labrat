"""Emit the exact labrat-agent opening prompts (per official query) for a DAB submission audit.

For each official-12 query, reconstructs BOTH messages the agent received under the
GPT-5.6 Luna Max submission configuration (`submission-gpt56-luna-max-ledger-final-270`):

- the SYSTEM prompt: ``_build_labrat_agent_system_prompt`` (levers on) plus the
  Cartographer consult line — and, for the two bounded-recovery AG News queries
  (``agnews:3`` / ``agnews:4``), the evaluator row-budget paragraph that their
  disclosed per-task override added (inserted before the Cartographer line, matching
  the run-time assembly order in ``DabSuite._dispatch_driver_once_labrat_agent``);
- the opening USER message: ``task.prompt`` (base description + appended hints file,
  hints declared Yes).

The 5 runs of a query share the same deterministic prompt pair, so 54 pairs cover all
270 trials. The Cartographer "Scent" content is NOT in the opening prompt; it was
retrieved mid-run via the ``search_reference_docs`` tool and appears as tool OUTPUT in
the per-trial ``agent_tool_calls.jsonl`` trace logs. Consensus/diversity extra
instructions were disabled for this run (``agent_consensus: null``).
"""

from __future__ import annotations

import json
from pathlib import Path

from labrat.eval.benchmarks.dab.env import build_dab_task_env
from labrat.eval.benchmarks.dab.suite import (
    DabSuite,
    _build_labrat_agent_system_prompt,
    _cartographer_prompt_line,
)

_OFFICIAL = {
    "agnews",
    "bookreview",
    "crmarenapro",
    "deps_dev_v1",
    "github_repos",
    "googlelocal",
    "music_brainz_20k",
    "pancancer_atlas",
    "patents",
    "stockindex",
    "stockmarket",
    "yelp",
}

# The disclosed bounded-recovery override for the two AG News classification queries
# (see the run's config.json `bounded_recovery.task_overrides`).
_ROW_BUDGET_TASKS = {"agnews:3", "agnews:4"}
_ROW_BUDGET = 200


def _budget_paragraph(row_budget: int) -> str:
    return (
        "\n\nEvaluator budget: llm_classify may process at most "
        f"{row_budget} cumulative rows in this trial. "
        "The cap spans all calls. Use the budget deliberately and produce "
        "the best final answer from the resulting evidence."
    )


def main() -> None:
    suite = DabSuite(driver="labrat-agent", hints=True)  # hints on, as submitted
    tasks = [t for t in suite.tasks() if t.config["dataset"] in _OFFICIAL]
    prompts: dict[str, dict[str, str]] = {}
    for t in tasks:
        env = build_dab_task_env(Path(t.config["db_config_path"]))
        system = _build_labrat_agent_system_prompt(env, include_levers=True)
        if t.id in _ROW_BUDGET_TASKS:
            system = system + _budget_paragraph(_ROW_BUDGET)
        system = system + "\n" + _cartographer_prompt_line()
        prompts[t.id] = {"system": system, "user": t.prompt}
    payload = {
        "_note": (
            "labrat-agent opening SYSTEM + USER prompt per official query, exactly as "
            "submitted (levers on, hints appended, Cartographer consult line; agnews:3/4 "
            "additionally carry the disclosed evaluator row-budget paragraph). Identical "
            "across the 5 runs of each query. The Cartographer 'Scent' content is NOT in "
            "the opening prompt; it was retrieved mid-run via the search_reference_docs "
            "tool and appears as tool OUTPUT in the per-trial agent_tool_calls.jsonl "
            "trace logs."
        ),
        "num_queries": len(prompts),
        "prompts": dict(sorted(prompts.items())),
    }
    dest = Path("runs/dab/submission-gpt56-luna-max-ledger-final-270/labrat_opening_prompts.json")
    dest.write_text(json.dumps(payload, indent=2))
    print(f"wrote {len(prompts)} opening prompt pairs to {dest}")


if __name__ == "__main__":
    main()
