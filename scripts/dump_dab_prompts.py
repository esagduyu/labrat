"""Emit the exact claude-mcp opening prompts (per official query) for a DAB submission audit.

For each official-12 query, reconstructs the opening USER message the agent received using
the same `_build_claude_mcp_prompt` the run used (hints on, Cartographer consult line on).
The 5 runs of a query share the same deterministic prompt, so 54 prompts cover all 270 trials.

The SYSTEM prompt is the stock Claude Code CLI system prompt — the driver passes no custom
`--system-prompt`. The Cartographer "Scent" content is NOT in the opening prompt; it was
retrieved mid-run via the `search_reference_docs` tool and appears as tool OUTPUT in the
per-trial MCP trace logs.
"""

from __future__ import annotations

import json
from pathlib import Path

from labrat.eval.benchmarks.dab.env import build_dab_task_env
from labrat.eval.benchmarks.dab.suite import DabSuite, _build_claude_mcp_prompt

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


def main() -> None:
    suite = DabSuite(driver="claude-mcp", hints=True)  # hints on, as submitted
    tasks = [t for t in suite.tasks() if t.config["dataset"] in _OFFICIAL]
    prompts: dict[str, str] = {}
    for t in tasks:
        env = build_dab_task_env(Path(t.config["db_config_path"]))
        prompts[t.id] = _build_claude_mcp_prompt(
            env.ctx.primary,
            env,
            t,
            include_cartographer_line=True,  # Cartographer was enabled
            max_tool_calls=None,  # no tool-call cap was set
        )
    payload = {
        "_note": (
            "claude-mcp opening USER prompt per official query, exactly as submitted "
            "(hints appended + Cartographer consult line). Identical across the 5 runs of "
            "each query. SYSTEM prompt = the stock Claude Code CLI system prompt (no custom "
            "--system-prompt is passed). The Cartographer 'Scent' content is NOT in the "
            "opening prompt; it was retrieved mid-run via the search_reference_docs tool and "
            "appears as tool OUTPUT in the per-trial MCP trace logs."
        ),
        "num_queries": len(prompts),
        "prompts": dict(sorted(prompts.items())),
    }
    dest = Path(
        "runs/dab/dab-submission-cartograph-hints/submission_package/labrat_opening_prompts.json"
    )
    dest.write_text(json.dumps(payload, indent=2))
    print(f"wrote {len(prompts)} opening prompts to {dest}")


if __name__ == "__main__":
    main()
