"""DAB benchmark entrypoint.

Phase 1a: single-trial-per-query, no resumability, no pass@5.
Phase 1b: pass@5 (n_trials=5), JSONL resumability — re-run skips completed trials.
Phase 4 extracts the inline runner into BenchmarkOrchestrator.

Usage:
  uv run python scripts/eval_dab.py --datasets deps_dev_v1,github_repos
  uv run python scripts/eval_dab.py --tasks "deps_dev_v1:1,github_repos:2"
  uv run python scripts/eval_dab.py --hints
  # Resume a crashed run by pointing at the same output dir:
  uv run python scripts/eval_dab.py --output-dir runs/dab/dab-<id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from labrat.eval.benchmarks.dab.suite import DabSuite, Driver
from labrat.eval.reporting import report_to_markdown
from labrat.eval.types import BenchmarkReport, BenchmarkSuite, TrialResult


def _load_completed_trials(trials_jsonl: Path) -> set[tuple[str, int]]:
    """Return set of (task_id, trial_num) pairs already recorded in trials.jsonl.

    Infra failures (``reason`` starts with ``"infra:"``) are NOT considered
    completed — they never got a fair shot at the query, so a resume rerun
    should attempt them again. The harness rewrites those lines in place when
    they succeed on rerun.
    """
    completed: set[tuple[str, int]] = set()
    if not trials_jsonl.exists():
        return completed
    for line in trials_jsonl.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            reason = obj.get("reason") or ""
            if isinstance(reason, str) and reason.startswith("infra:"):
                continue
            completed.add((obj["task_id"], obj["trial_num"]))
        except (json.JSONDecodeError, KeyError):
            pass
    return completed


async def _run_interim(
    suite: BenchmarkSuite,
    n_trials: int,
    output_dir: Path,
    task_filter: list[str] | None,
) -> BenchmarkReport:
    """Inline interim runner. No DAB-specific logic. Replaced by BenchmarkOrchestrator in Phase 4."""
    output_dir.mkdir(parents=True, exist_ok=True)
    trials_jsonl = output_dir / "trials.jsonl"

    # Load any previously completed trials (resumability). Infra-failed trials
    # are intentionally NOT counted as completed (see _load_completed_trials).
    completed = _load_completed_trials(trials_jsonl)
    all_trials: list[TrialResult] = []
    infra_keys_to_rewrite: set[tuple[str, int]] = set()
    if trials_jsonl.exists():
        for line in trials_jsonl.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    tr = TrialResult.model_validate_json(line)
                except Exception:
                    continue
                reason = tr.reason or ""
                if reason.startswith("infra:"):
                    # Skip — we'll re-run this and the new line will be appended.
                    infra_keys_to_rewrite.add((tr.task_id, tr.trial_num))
                else:
                    all_trials.append(tr)
        if completed or infra_keys_to_rewrite:
            print(
                f"Resuming: {len(completed)} trials already complete (skipping); "
                f"{len(infra_keys_to_rewrite)} infra trials will be re-attempted.",
                flush=True,
            )

    tasks = list(suite.tasks())
    if task_filter:
        wanted = set(task_filter)
        tasks = [t for t in tasks if t.id in wanted]

    with trials_jsonl.open("a") as f:
        for task in tasks:
            for trial_num in range(n_trials):
                if (task.id, trial_num) in completed:
                    continue
                scratch = output_dir / "scratch" / f"{task.id.replace(':', '_')}__trial{trial_num}"
                scratch.mkdir(parents=True, exist_ok=True)
                result = await suite.run_trial(task, trial_num, scratch)
                f.write(result.model_dump_json() + "\n")
                f.flush()
                all_trials.append(result)
                reason = result.reason or ""
                if reason.startswith("infra:"):
                    status = f"INFRA: {reason[len('infra:') :]}"
                else:
                    status = "PASS" if result.passed else "FAIL"
                print(
                    f"[{task.id} trial {trial_num}] {status} ({result.latency_seconds:.1f}s)",
                    flush=True,
                )

    score = suite.aggregate(all_trials)
    return BenchmarkReport(
        benchmark=suite.name,
        run_id=output_dir.name,
        score=score,
        trials=all_trials,
        config={"n_trials": n_trials, "task_filter": task_filter},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run DAB benchmark")
    parser.add_argument("--dab-dir", type=Path, default=None)
    parser.add_argument("--hints", action="store_true")
    parser.add_argument(
        "--driver",
        choices=["raw-bash", "labrat-agent", "claude-mcp"],
        default=None,
        help=(
            "Agent driver. 'raw-bash' (default for new runs) reproduces the Phase 1b "
            "baseline by shelling claude --print with the native Bash tool (Max-plan "
            "billing). 'labrat-agent' routes through AgentLoop + LabRat tools + "
            "AnthropicProvider (metered API billing). 'claude-mcp' runs claude --print "
            "with the LabRat MCP server mounted (Max-plan billing, LabRat tools used "
            "natively — recommended Phase 4 measurement). On --output-dir resume, "
            "the driver is restored from config.json unless overridden here "
            "(mismatches are rejected to prevent mixed-driver runs)."
        ),
    )
    parser.add_argument(
        "--agent-model",
        default=None,
        help=(
            "Model id passed to the agent subprocess for the labrat-agent and "
            "claude-mcp drivers. Defaults to claude-sonnet-4-6 for new runs (Sonnet, "
            "to avoid burning Max-plan budget on Opus); restored from config.json on "
            "resume. Always pass --model explicitly to the underlying claude CLI so "
            "the subprocess doesn't fall through to whatever the parent session uses."
        ),
    )
    parser.add_argument(
        "--agent-provider",
        choices=["anthropic", "claude-code", "openai", "codex"],
        default=None,
        help=(
            "Model provider for the labrat-agent driver. 'anthropic' (default) uses "
            "metered API billing. 'claude-code' shells the claude CLI subprocess "
            "(Max-plan billing; subject to the documented text-protocol conflict for "
            "tool round-trips). 'openai' uses an OpenAI-compatible endpoint. 'codex' "
            "runs GPT-5.5 via the ChatGPT subscription (Codex Responses API + "
            "~/.codex/auth.json; no metered key)."
        ),
    )
    parser.add_argument(
        "--agent-reasoning",
        choices=["minimal", "low", "medium", "high"],
        default=None,
        help=(
            "Reasoning effort for the codex (GPT-5.5) provider on the labrat-agent "
            "driver. Default: medium. Ignored by the other providers."
        ),
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help=(
            "Cap on assistant turns per trial. Default: unbounded for labrat-agent; "
            "200 for claude-mcp (sent to claude --max-turns); 15 for raw-bash (Phase "
            "1b baseline value). Pass any positive int to override."
        ),
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=None,
        help=(
            "Cap on cumulative tool dispatches per trial. Hard-enforced under "
            "labrat-agent (AgentLoop drops overflow and exits). For claude-mcp it is "
            "advisory only (surfaced in the prompt — claude CLI has no native cap). "
            "Ignored under raw-bash (no LabRat registry in the loop). Default: "
            "unbounded."
        ),
    )
    parser.add_argument(
        "--agent-verify",
        action="store_true",
        default=None,
        help=(
            "Enable the LLM-as-judge verifier loop for the labrat-agent driver "
            "(opt-in; an extra LLM call gates each would-be-final answer). No effect "
            "under raw-bash / claude-mcp, whose loops live outside AgentLoop."
        ),
    )
    parser.add_argument(
        "--agent-autocontext",
        action="store_true",
        default=None,
        help=(
            "Run the deterministic cartographer pre-pass on each dataset's primary DB and "
            "let the agent consult the generated Scent via search_reference_docs "
            "(GT-firewalled AutoContext; off by default — for ablation)."
        ),
    )
    parser.add_argument(
        "--agent-timeout",
        type=int,
        default=None,
        help=(
            "Per-call provider timeout in seconds for the labrat-agent driver. Only the "
            "claude-code provider honours it (default 120s); useful to absorb slow turns "
            "when --agent-verify adds round-trips. None = provider default."
        ),
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=5,
        help="Trials per query for pass@k scoring (default 5)",
    )
    parser.add_argument(
        "--datasets",
        type=str,
        default=None,
        help="Comma-separated dataset filter, e.g. 'bookreview,yelp'",
    )
    parser.add_argument(
        "--tasks",
        type=str,
        default=None,
        help="Comma-separated task ID filter, e.g. 'bookreview:1,yelp:3'",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Resume a previous run by pointing at its output dir",
    )
    args = parser.parse_args(argv)

    # Resolve resume config first so driver / agent_model can be restored before
    # we construct the suite. CLI args always win; missing CLI args fall back to
    # the config.json from a previous run if --output-dir points at one.
    existing_cfg: dict[str, Any] = {}
    if args.output_dir:
        existing_cfg_path = args.output_dir / "config.json"
        if existing_cfg_path.exists():
            existing_cfg = json.loads(existing_cfg_path.read_text())

    for field, cli_val in [
        ("driver", args.driver),
        ("agent_model", args.agent_model),
        ("agent_provider", args.agent_provider),
        ("agent_max_turns", args.max_turns),
        ("agent_max_tool_calls", args.max_tool_calls),
        ("agent_verify", args.agent_verify),
        ("agent_autocontext", args.agent_autocontext),
        ("agent_timeout", args.agent_timeout),
        ("agent_reasoning", args.agent_reasoning),
    ]:
        prior = existing_cfg.get(field)
        if cli_val is not None and prior is not None and cli_val != prior:
            raise SystemExit(
                f"Resume conflict: --{field.replace('_', '-')}={cli_val!r} but "
                f"existing config.json has {prior!r}. Refusing to mix drivers/models/"
                f"providers/caps in one run (would invalidate aggregate scoring). Drop "
                f"the override to resume, or start a fresh --output-dir."
            )

    effective_driver: Driver = args.driver or existing_cfg.get("driver") or "raw-bash"
    effective_model: str = (
        args.agent_model or existing_cfg.get("agent_model") or "claude-sonnet-4-6"
    )
    effective_provider: str = (
        args.agent_provider or existing_cfg.get("agent_provider") or "anthropic"
    )
    effective_max_turns: int | None = (
        args.max_turns if args.max_turns is not None else existing_cfg.get("agent_max_turns")
    )
    effective_max_tool_calls: int | None = (
        args.max_tool_calls
        if args.max_tool_calls is not None
        else existing_cfg.get("agent_max_tool_calls")
    )
    effective_verify: bool = bool(
        args.agent_verify
        if args.agent_verify is not None
        else existing_cfg.get("agent_verify", False)
    )
    effective_autocontext: bool = bool(
        args.agent_autocontext
        if args.agent_autocontext is not None
        else existing_cfg.get("agent_autocontext", False)
    )
    effective_timeout: int | None = (
        args.agent_timeout if args.agent_timeout is not None else existing_cfg.get("agent_timeout")
    )
    effective_reasoning: str | None = (
        args.agent_reasoning
        if args.agent_reasoning is not None
        else existing_cfg.get("agent_reasoning")
    )

    suite = DabSuite(
        dab_dir=args.dab_dir,
        hints=args.hints,
        driver=effective_driver,
        agent_model=effective_model,
        agent_provider=effective_provider,
        agent_max_turns=effective_max_turns,
        agent_max_tool_calls=effective_max_tool_calls,
        agent_verify=effective_verify,
        agent_timeout=effective_timeout,
        agent_reasoning=effective_reasoning,
        autocontext=effective_autocontext,
    )

    task_filter: list[str] | None = None
    if args.tasks:
        task_filter = [t.strip() for t in args.tasks.split(",") if t.strip()]
    elif args.datasets:
        wanted = {ds.strip() for ds in args.datasets.split(",") if ds.strip()}
        task_filter = [t.id for t in suite.tasks() if t.config["dataset"] in wanted]

    if args.output_dir:
        output_dir = args.output_dir
        if existing_cfg and not args.tasks and not args.datasets:
            task_filter = existing_cfg.get("task_filter")
    else:
        run_id = f"dab-{int(time.time())}"
        output_dir = Path("runs") / "dab" / run_id

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "config.json").write_text(
        json.dumps(
            {
                "hints": args.hints,
                "driver": effective_driver,
                "agent_model": effective_model,
                "agent_provider": effective_provider,
                "agent_max_turns": effective_max_turns,
                "agent_max_tool_calls": effective_max_tool_calls,
                "agent_verify": effective_verify,
                "agent_autocontext": effective_autocontext,
                "agent_timeout": effective_timeout,
                "agent_reasoning": effective_reasoning,
                "n_trials": args.n_trials,
                "task_filter": task_filter,
            },
            indent=2,
        )
    )

    report = asyncio.run(_run_interim(suite, args.n_trials, output_dir, task_filter))

    suite.write_submission(report, output_dir)
    (output_dir / "report.md").write_text(report_to_markdown(report))
    print(f"\nRun complete: {output_dir}")
    print(f"Overall score: {report.score.overall:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
