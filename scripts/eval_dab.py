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
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from labrat.agent.providers import (
    CODEX_REASONING_EFFORTS,
    PROVIDER_NAMES,
    resolve_codex_model_config,
)
from labrat.eval.benchmarks.dab.suite import DabSuite, Driver, is_rate_limit_error
from labrat.eval.benchmarks.dab.taint import audit_run, gate, task_trial_dir_name
from labrat.eval.reporting import report_to_markdown
from labrat.eval.types import BenchmarkReport, BenchmarkSuite, TrialResult

_RATE_LIMIT_EXIT_CODE = 4


class _RateLimitStopError(RuntimeError):
    """Internal control flow: one rate-limit trial was durably recorded."""

    def __init__(self, result: TrialResult) -> None:
        self.result = result
        super().__init__(f"{result.task_id} trial {result.trial_num}")


def _rate_limit_reset_summary(result: TrialResult) -> str:
    details = result.meta.get("rate_limit")
    if not isinstance(details, dict):
        return ""
    parts: list[str] = []
    resets_at = details.get("resets_at")
    if isinstance(resets_at, int) and not isinstance(resets_at, bool):
        try:
            reset_iso = datetime.fromtimestamp(resets_at, tz=UTC).isoformat()
        except (OSError, OverflowError, ValueError):
            reset_iso = str(resets_at)
        parts.append(f"reset_at={reset_iso}")
    resets_in = details.get("resets_in_seconds")
    if isinstance(resets_in, int) and not isinstance(resets_in, bool):
        parts.append(f"resets_in_seconds={resets_in}")
    return f" ({', '.join(parts)})" if parts else ""


def _load_completed_trials(trials_jsonl: Path) -> set[tuple[str, int]]:
    """Return set of (task_id, trial_num) pairs already recorded in trials.jsonl.

    Infra failures (``reason`` starts with ``"infra:"``) are NOT considered
    completed — they never got a fair shot at the query, so a resume rerun
    should attempt them again. A rerun that is still infra keeps both rows
    (append-only audit trail); a rerun that succeeds with a real semantic
    result replaces the stale infra row(s) for that key — see
    ``_compact_stale_infra_rows``.
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


def _compact_stale_infra_rows(trials_jsonl: Path, key: tuple[str, int]) -> None:
    """Drop stale infra row(s) for ``key`` from trials.jsonl in place.

    Called right after a resume rerun of a previously-infra (task_id, trial_num)
    trial produces a semantic (non-infra) result: the fresh row has already been
    appended, so any earlier infra row(s) for the same key are now stale
    duplicates left behind by the append-only resume writer. Every other line
    — including the just-appended new row, and infra rows for OTHER keys — is
    left untouched, so this only closes the "infra row stays, new row also
    appended" leak; it does not otherwise renumber or reorder the file.

    Safe to call while the caller still holds its own file handle open in
    append ("a") mode: append-mode writes always seek to the current end of
    file before writing, so a later ``f.write()`` on that handle lands after
    whatever this rewrite leaves in place.
    """
    task_id, trial_num = key
    lines = trials_jsonl.read_text().splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        reason = obj.get("reason") or ""
        is_stale_infra = (
            obj.get("task_id") == task_id
            and obj.get("trial_num") == trial_num
            and isinstance(reason, str)
            and reason.startswith("infra:")
        )
        if is_stale_infra:
            continue
        kept.append(line)
    trials_jsonl.write_text("".join(f"{line}\n" for line in kept))


def _has_semantic_trials(trials_jsonl: Path) -> bool:
    """Whether a run already contains any non-infrastructure trial row."""
    if not trials_jsonl.exists():
        return False
    for line in trials_jsonl.read_text().splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        reason = obj.get("reason") or ""
        if not isinstance(reason, str) or not reason.startswith("infra:"):
            return True
    return False


def _append_trial_row(trials_jsonl: Path, result: TrialResult) -> None:
    """Append one trial row, re-resolving the path on every call.

    A single long-lived append handle was observed (2026-07-30) to lose EVERY row
    on most shards of a 3-arm campaign: the file at the path was replaced mid-run,
    so writes landed on an orphaned inode and trials.jsonl ended 0 bytes even though
    each write was explicitly flushed. report.md/submission.json were unaffected
    (they derive from the in-memory list), which is what made it silent — and the
    taint audit then passed vacuously on zero rows.

    Re-opening per row re-resolves the path, and fsync makes the row durable before
    the next trial starts. The cost is one open+fsync per trial against a trial that
    takes tens of seconds.
    """
    with trials_jsonl.open("a", encoding="utf-8") as f:
        f.write(result.model_dump_json() + "\n")
        f.flush()
        os.fsync(f.fileno())


def _verify_trials_file(trials_jsonl: Path, recorded: list[TrialResult]) -> bool:
    """Ensure the on-disk row count covers everything we recorded; self-heal if not.

    Returns True if a repair was performed. Belt-and-braces behind
    ``_append_trial_row``: if the on-disk file somehow still holds fewer rows than
    the in-memory list, rewrite it from memory so the run cannot silently ship a
    truncated trials.jsonl (which would also make the taint audit vacuous).
    """
    try:
        on_disk = sum(1 for line in trials_jsonl.read_text().splitlines() if line.strip())
    except OSError:
        on_disk = 0
    if on_disk >= len(recorded):
        return False
    print(
        f"WARNING: {trials_jsonl} holds {on_disk} rows but {len(recorded)} were recorded "
        "— rewriting from memory (see _append_trial_row docstring).",
        file=sys.stderr,
        flush=True,
    )
    trials_jsonl.write_text("".join(r.model_dump_json() + "\n" for r in recorded))
    return True


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

    trials_jsonl.touch(exist_ok=True)
    for task in tasks:
        for trial_num in range(n_trials):
            if (task.id, trial_num) in completed:
                continue
            scratch = output_dir / "scratch" / task_trial_dir_name(task.id, trial_num)
            scratch.mkdir(parents=True, exist_ok=True)
            result = await suite.run_trial(task, trial_num, scratch)
            artifact = result.artifact
            payload = artifact.get("payload", "") if isinstance(artifact, dict) else artifact
            if result.reason != "infra:rate_limit" and is_rate_limit_error(payload):
                result = result.model_copy(update={"passed": False, "reason": "infra:rate_limit"})
            _append_trial_row(trials_jsonl, result)
            all_trials.append(result)
            reason = result.reason or ""
            key = (task.id, trial_num)
            if key in infra_keys_to_rewrite and not reason.startswith("infra:"):
                # This resume rerun of a previously-infra trial produced a real
                # semantic result — drop the stale infra row(s) for this key so
                # the file keeps exactly one row per (task_id, trial_num). A
                # rerun that is STILL infra keeps both rows (append-only audit
                # trail intact) — see _compact_stale_infra_rows.
                _compact_stale_infra_rows(trials_jsonl, key)
                infra_keys_to_rewrite.discard(key)
            if reason.startswith("infra:"):
                status = f"INFRA: {reason[len('infra:') :]}"
            else:
                status = "PASS" if result.passed else "FAIL"
            print(
                f"[{task.id} trial {trial_num}] {status} ({result.latency_seconds:.1f}s)",
                flush=True,
            )
            if reason == "infra:rate_limit":
                # The row is flushed above and remains retryable on resume. Stop
                # immediately so a quota outage cannot fill trials.jsonl with
                # zero-token attempts for every remaining task.
                raise _RateLimitStopError(result)

    _verify_trials_file(trials_jsonl, all_trials)

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
    parser.add_argument(
        "--hints",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Append DAB's benchmark-provided data hints to each task prompt. Off by "
            "default for new runs; restored from config.json on resume."
        ),
    )
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
            "claude-mcp drivers. New codex runs default to gpt-5.6-luna; other "
            "providers default to claude-sonnet-4-6. Restored from config.json on "
            "resume. Always pass --model explicitly to subprocess-backed providers so "
            "they do not fall through to whatever the parent session uses."
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
            "runs GPT-5.5 or the GPT-5.6 Sol/Terra/Luna family via the ChatGPT "
            "subscription (Codex Responses API + ~/.codex/auth.json; no metered key)."
        ),
    )
    parser.add_argument(
        "--agent-reasoning",
        choices=list(CODEX_REASONING_EFFORTS),
        default=None,
        help=(
            "Responses reasoning effort for the codex provider on the labrat-agent "
            "driver. GPT-5.6 Luna supports low/medium/high/xhigh/max; Sol and Terra "
            "also support ultra (automatic task delegation). GPT-5.5 retains the "
            "legacy minimal value. New Codex runs default to Luna Max. Ignored by "
            "the other providers."
        ),
    )
    parser.add_argument(
        "--llm-classify-model",
        default=None,
        help=(
            "Dedicated model for nested llm_classify requests. Defaults to the "
            "main agent model, preserving existing behavior. Restored from config.json."
        ),
    )
    parser.add_argument(
        "--llm-classify-reasoning",
        choices=list(CODEX_REASONING_EFFORTS),
        default=None,
        help=(
            "Reasoning effort for the dedicated nested llm_classify model. Defaults "
            "to the main agent effort. For DAB AG News, Luna Low avoids spending "
            "Max reasoning on constrained label selection."
        ),
    )
    parser.add_argument(
        "--llm-classify-backend",
        choices=["llm", "local-embed"],
        default=None,
        help=(
            "Backend for nested llm_classify calls: 'llm' (default; per-row model "
            "calls) or 'local-embed' (zero-token local embedding classifier via the "
            "`semantic` extra). Restored from config.json on resume."
        ),
    )
    parser.add_argument(
        "--llm-classify-concurrency",
        type=int,
        choices=[1, 2],
        default=None,
        help=(
            "Maximum concurrent batched llm_classify requests. Defaults to 1; hard "
            "capped at 2 and restored from config.json."
        ),
    )
    parser.add_argument(
        "--llm-classify-row-budget",
        type=int,
        default=None,
        help=(
            "Evaluator-enforced cumulative llm_classify row cap per trial, spanning "
            "all tool calls. Unset preserves the normal per-call limits. Restored "
            "from config.json."
        ),
    )
    parser.add_argument(
        "--agent-levers",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable the benchmark-safe DAB prompt levers (force-query, SQL repair, "
            "SQL-side aggregation, tie handling). On by default for new runs; restored "
            "from config.json on resume. Use --no-agent-levers for the ablation control."
        ),
    )
    parser.add_argument(
        "--agent-ledger",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable the ContextLedger for labrat-agent trials and persist its artifacts "
            "in the trial scratch directory. On by default for new runs; restored from "
            "config.json on resume. Use --no-agent-ledger for the ablation control."
        ),
    )
    parser.add_argument(
        "--agent-mcp-ledger",
        action="store_true",
        default=None,
        help=(
            "Enable the server-side Context Ledger on the claude-mcp driver's MCP "
            "server subprocess (sets LABRAT_MCP_LEDGER + LABRAT_MCP_RESULT_STORE_DIR "
            "under the trial scratch dir; exposes the get_artifact tool). Distinct "
            "from --agent-ledger, which is the in-process ledger on the labrat-agent "
            "driver only. Off by default — for ablation."
        ),
    )
    parser.add_argument(
        "--agent-answer-gate",
        action="store_true",
        default=None,
        help=(
            "Deterministic answer-shape gate on the claude-mcp driver: checks the final "
            "answer for delivery failures we have measured costing otherwise-correct "
            "trials (requested-count shortfall, list delivered as a markdown table, "
            "fraction answered as a percentage) and makes ONE presentation-only "
            "corrective pass. The corrective call carries no MCP config and blocks all "
            "native tools, so it cannot reach a database. Off by default — for ablation."
        ),
    )
    parser.add_argument(
        "--agent-taxonomy",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Append the answer-discipline taxonomy (Lever Pack v2: shape/grain "
            "pinning, literal delivery, verify-before-commit) to the labrat-agent "
            "system prompt. Off by default for new runs; restored from config.json "
            "on resume. Benchmark-agnostic process guidance only."
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
        "--agent-cartograph",
        action="store_true",
        default=None,
        help=(
            "Run the deterministic cartographer pre-pass on each dataset's primary DB and "
            "let the agent consult the generated Scent via search_reference_docs "
            "(GT-firewalled Cartographer; off by default — for ablation)."
        ),
    )
    parser.add_argument(
        "--cartograph-semantics",
        action="store_true",
        default=None,
        help="Enable the LLM semantics pass for the cartographer pre-pass (requires "
        "--agent-cartograph). Author-once, audited, frozen. Off by default.",
    )
    parser.add_argument(
        "--cartograph-semantics-model",
        default="claude-sonnet-4-6",
        help="Model used to author the semantics pass (independent of --agent-model).",
    )
    parser.add_argument(
        "--cartograph-semantics-provider",
        choices=list(PROVIDER_NAMES),
        default="anthropic",
        help="Provider for the semantics authoring model. Auto-routed to claude-code on "
        "the claude-mcp driver (Max-plan auth); honored as-is on other drivers.",
    )
    parser.add_argument(
        "--cartograph-scent-dir",
        type=Path,
        default=None,
        help="Persistent dir for authored Scent docs (enables freeze-and-commit for a "
        "submission). Default: a per-run temp dir.",
    )
    parser.add_argument(
        "--agent-consensus",
        type=int,
        default=None,
        help=(
            "K-of-N self-consistency: run each trial K times and take the modal answer "
            "(off by default)."
        ),
    )
    parser.add_argument(
        "--agent-reverify",
        action="store_true",
        default=None,
        help="Independent re-derive + reconcile-on-mismatch verify stage (off by default).",
    )
    parser.add_argument(
        "--agent-argue-rounds",
        type=int,
        default=None,
        help=(
            "Bounded argumentation rounds on a split K-of-N consensus vote (requires "
            "--agent-consensus). When the vote comes back low_confidence, re-dispatch "
            "each sub-run with the other sub-runs' answers appended and re-vote, up to "
            "N rounds, stopping early once a majority forms. Off (0) by default."
        ),
    )
    parser.add_argument(
        "--agent-postverify",
        action="store_true",
        default=None,
        help=(
            "Deterministic question-constraint check on the FINAL answer (post-consensus, "
            "post-argue, post-reverify); on a violation, one bounded revise dispatch. "
            "Independent of --agent-consensus/--agent-reverify — works standalone. Off by "
            "default."
        ),
    )
    parser.add_argument(
        "--no-consensus-diversity",
        dest="no_consensus_diversity",
        action="store_true",
        default=None,
        help=(
            "Ablation control: disable per-sub-run diversity (variant Scent + rotated "
            "framing) in K-of-N consensus (--agent-consensus). Diversity is ON by "
            "default — this makes every sub-run an identical dispatch "
            "(diversity_index=None throughout), the null-baseline arm."
        ),
    )
    parser.add_argument(
        "--agent-timeout",
        type=int,
        default=None,
        help=(
            "Per-trial wall-clock timeout in seconds. None uses the 1200-second DAB "
            "default; provider implementations may also apply it to individual calls."
        ),
    )
    parser.add_argument(
        "--terminalize-timeouts",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Record a wall-clock timeout as a traced scored failure instead of retryable "
            "infrastructure. Off by default; intended for explicitly bounded submission "
            "runs where timeout is part of the declared evaluator policy."
        ),
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=None,
        help=(
            "Trials per query for pass@k scoring. Defaults to 5 for new runs and is "
            "restored from config.json on resume."
        ),
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
        elif _has_semantic_trials(args.output_dir / "trials.jsonl"):
            parser.error(
                "Cannot safely resume an output directory that has semantic trial rows "
                "but no config.json. Restore the original config or start a fresh "
                "--output-dir; inferring model and classifier settings would mix "
                "incompatible trials."
            )

    # Tri-state --no-consensus-diversity: None (unset) inherits from resume config /
    # defaults to True; True/False resolved from the negation flag once it's set.
    cli_consensus_diversity: bool | None = (
        (not args.no_consensus_diversity) if args.no_consensus_diversity is not None else None
    )

    for field, cli_val in [
        ("n_trials", args.n_trials),
        ("hints", args.hints),
        ("driver", args.driver),
        ("agent_model", args.agent_model),
        ("agent_provider", args.agent_provider),
        ("agent_max_turns", args.max_turns),
        ("agent_max_tool_calls", args.max_tool_calls),
        ("agent_verify", args.agent_verify),
        ("agent_cartograph", args.agent_cartograph),
        ("cartograph_semantics", args.cartograph_semantics),
        ("agent_consensus", args.agent_consensus),
        ("agent_reverify", args.agent_reverify),
        ("agent_argue_rounds", args.agent_argue_rounds),
        ("agent_postverify", args.agent_postverify),
        ("consensus_diversity", cli_consensus_diversity),
        ("agent_timeout", args.agent_timeout),
        ("agent_reasoning", args.agent_reasoning),
        ("llm_classify_model", args.llm_classify_model),
        ("llm_classify_reasoning", args.llm_classify_reasoning),
        ("llm_classify_concurrency", args.llm_classify_concurrency),
        ("llm_classify_row_budget", args.llm_classify_row_budget),
        ("llm_classify_backend", args.llm_classify_backend),
        ("terminalize_timeouts", args.terminalize_timeouts),
        ("agent_levers", args.agent_levers),
        ("agent_ledger", args.agent_ledger),
        ("agent_mcp_ledger", args.agent_mcp_ledger),
        ("agent_answer_gate", args.agent_answer_gate),
        ("agent_taxonomy", args.agent_taxonomy),
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
    effective_provider: str = (
        args.agent_provider or existing_cfg.get("agent_provider") or "anthropic"
    )
    default_model = "gpt-5.6-luna" if effective_provider == "codex" else "claude-sonnet-4-6"
    effective_model: str = args.agent_model or existing_cfg.get("agent_model") or default_model
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
    effective_cartograph: bool = bool(
        args.agent_cartograph
        if args.agent_cartograph is not None
        else existing_cfg.get("agent_cartograph", False)
    )
    effective_mcp_ledger: bool = bool(
        args.agent_mcp_ledger
        if args.agent_mcp_ledger is not None
        else existing_cfg.get("agent_mcp_ledger", False)
    )
    effective_answer_gate: bool = bool(
        args.agent_answer_gate
        if args.agent_answer_gate is not None
        else existing_cfg.get("agent_answer_gate", False)
    )
    effective_cartograph_semantics: bool = bool(
        args.cartograph_semantics
        if args.cartograph_semantics is not None
        else existing_cfg.get("cartograph_semantics", False)
    )
    effective_cartograph_semantics_model: str = args.cartograph_semantics_model or existing_cfg.get(
        "cartograph_semantics_model", "claude-sonnet-4-6"
    )
    effective_cartograph_semantics_provider: str = (
        args.cartograph_semantics_provider
        or existing_cfg.get("cartograph_semantics_provider", "anthropic")
    )
    if effective_cartograph_semantics and not effective_cartograph:
        raise SystemExit(
            "--cartograph-semantics requires --agent-cartograph (the semantics pass runs "
            "inside the cartographer pre-pass)."
        )
    effective_cartograph_scent_dir: Path | None = (
        args.cartograph_scent_dir
        if args.cartograph_scent_dir is not None
        else (
            Path(existing_cfg["cartograph_scent_dir"])
            if existing_cfg.get("cartograph_scent_dir")
            else None
        )
    )
    effective_consensus: int | None = (
        args.agent_consensus
        if args.agent_consensus is not None
        else existing_cfg.get("agent_consensus")
    )
    effective_reverify: bool = bool(
        args.agent_reverify
        if args.agent_reverify is not None
        else existing_cfg.get("agent_reverify", False)
    )
    effective_argue_rounds: int = (
        args.agent_argue_rounds
        if args.agent_argue_rounds is not None
        else existing_cfg.get("agent_argue_rounds", 0)
    )
    effective_postverify: bool = bool(
        args.agent_postverify
        if args.agent_postverify is not None
        else existing_cfg.get("agent_postverify", False)
    )
    effective_consensus_diversity: bool = bool(
        cli_consensus_diversity
        if cli_consensus_diversity is not None
        else existing_cfg.get("consensus_diversity", True)
    )
    effective_timeout: int | None = (
        args.agent_timeout if args.agent_timeout is not None else existing_cfg.get("agent_timeout")
    )
    effective_reasoning: str | None = (
        args.agent_reasoning
        if args.agent_reasoning is not None
        else existing_cfg.get("agent_reasoning")
    )
    if effective_provider == "codex":
        if effective_reasoning is None and effective_model in {
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
        }:
            effective_reasoning = "max"
        try:
            effective_model, effective_reasoning = resolve_codex_model_config(
                effective_model, effective_reasoning
            )
        except ValueError as exc:
            parser.error(str(exc))
    effective_classify_model: str = (
        args.llm_classify_model or existing_cfg.get("llm_classify_model") or effective_model
    )
    effective_classify_reasoning: str | None = (
        args.llm_classify_reasoning
        if args.llm_classify_reasoning is not None
        else existing_cfg.get("llm_classify_reasoning", effective_reasoning)
    )
    if effective_provider == "codex":
        try:
            effective_classify_model, effective_classify_reasoning = resolve_codex_model_config(
                effective_classify_model, effective_classify_reasoning
            )
        except ValueError as exc:
            parser.error(str(exc))
    effective_classify_concurrency: int = (
        args.llm_classify_concurrency
        if args.llm_classify_concurrency is not None
        else existing_cfg.get("llm_classify_concurrency", 1)
    )
    effective_classify_row_budget: int | None = (
        args.llm_classify_row_budget
        if args.llm_classify_row_budget is not None
        else existing_cfg.get("llm_classify_row_budget")
    )
    effective_classify_backend: str = (
        args.llm_classify_backend
        if args.llm_classify_backend is not None
        else existing_cfg.get("llm_classify_backend", "llm")
    )
    if effective_classify_row_budget is not None and effective_classify_row_budget < 1:
        parser.error("--llm-classify-row-budget must be positive")
    effective_terminalize_timeouts: bool = bool(
        args.terminalize_timeouts
        if args.terminalize_timeouts is not None
        else existing_cfg.get("terminalize_timeouts", False)
    )
    write_terminal_timeout_config = (
        not existing_cfg
        or "terminalize_timeouts" in existing_cfg
        or args.terminalize_timeouts is not None
    )
    nested_keys = (
        "llm_classify_model",
        "llm_classify_reasoning",
        "llm_classify_concurrency",
        "llm_classify_row_budget",
        "llm_classify_backend",
    )
    legacy_nested_config = bool(existing_cfg) and not any(
        key in existing_cfg for key in nested_keys
    )
    nested_override = any(
        value is not None
        for value in (
            args.llm_classify_model,
            args.llm_classify_reasoning,
            args.llm_classify_concurrency,
            args.llm_classify_row_budget,
            args.llm_classify_backend,
        )
    )
    nested_differs_from_legacy = (
        effective_classify_model != effective_model
        or effective_classify_reasoning != effective_reasoning
        or effective_classify_concurrency != 1
        or effective_classify_row_budget is not None
        or effective_classify_backend != "llm"
    )
    if (
        legacy_nested_config
        and nested_override
        and nested_differs_from_legacy
        and args.output_dir is not None
        and _has_semantic_trials(args.output_dir / "trials.jsonl")
    ):
        parser.error(
            "Cannot add differing llm_classify model/reasoning/concurrency to a legacy "
            "run that already has semantic rows. Start a fresh --output-dir so nested "
            "classifier settings remain uniform across the campaign."
        )
    write_nested_config = (
        not existing_cfg
        or not legacy_nested_config
        or (nested_override and nested_differs_from_legacy)
    )
    effective_hints: bool = bool(
        args.hints if args.hints is not None else existing_cfg.get("hints", False)
    )
    effective_levers: bool = bool(
        args.agent_levers
        if args.agent_levers is not None
        else existing_cfg.get("agent_levers", True)
    )
    effective_ledger: bool = bool(
        args.agent_ledger
        if args.agent_ledger is not None
        else existing_cfg.get("agent_ledger", True)
    )
    effective_taxonomy: bool = bool(
        args.agent_taxonomy
        if args.agent_taxonomy is not None
        else existing_cfg.get("agent_taxonomy", False)
    )
    effective_n_trials: int = (
        args.n_trials if args.n_trials is not None else existing_cfg.get("n_trials", 5)
    )

    suite = DabSuite(
        dab_dir=args.dab_dir,
        hints=effective_hints,
        driver=effective_driver,
        agent_model=effective_model,
        agent_provider=effective_provider,
        agent_max_turns=effective_max_turns,
        agent_max_tool_calls=effective_max_tool_calls,
        agent_verify=effective_verify,
        agent_timeout=effective_timeout,
        agent_reasoning=effective_reasoning,
        llm_classify_model=effective_classify_model,
        llm_classify_reasoning=effective_classify_reasoning,
        llm_classify_concurrency=effective_classify_concurrency,
        llm_classify_row_budget=effective_classify_row_budget,
        llm_classify_backend=effective_classify_backend,
        terminalize_timeouts=effective_terminalize_timeouts,
        agent_levers=effective_levers,
        agent_ledger=effective_ledger,
        agent_mcp_ledger=effective_mcp_ledger,
        agent_answer_gate=effective_answer_gate,
        agent_taxonomy=effective_taxonomy,
        cartograph=effective_cartograph,
        cartograph_semantics=effective_cartograph_semantics,
        cartograph_semantics_model=effective_cartograph_semantics_model,
        cartograph_semantics_provider=effective_cartograph_semantics_provider,
        cartograph_scent_root=effective_cartograph_scent_dir,
        consensus_k=effective_consensus,
        reverify=effective_reverify,
        consensus_diversity=effective_consensus_diversity,
        argue_rounds=effective_argue_rounds,
        postverify=effective_postverify,
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

    stale_submission = output_dir / "submission.json"
    if effective_driver == "raw-bash" and (
        stale_submission.exists() or stale_submission.is_symlink()
    ):
        parser.error(
            f"stale submission.json exists at {stale_submission}; raw-bash is "
            "legacy report-only, so refusing to execute until this output is "
            "explicitly migrated to a fresh directory"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    config_payload: dict[str, Any] = {
        "hints": effective_hints,
        "driver": effective_driver,
        "agent_model": effective_model,
        "agent_provider": effective_provider,
        "agent_max_turns": effective_max_turns,
        "agent_max_tool_calls": effective_max_tool_calls,
        "agent_verify": effective_verify,
        "agent_cartograph": effective_cartograph,
        "cartograph_semantics": effective_cartograph_semantics,
        "cartograph_semantics_model": effective_cartograph_semantics_model,
        "cartograph_semantics_provider": effective_cartograph_semantics_provider,
        "cartograph_scent_dir": (
            str(effective_cartograph_scent_dir) if effective_cartograph_scent_dir else None
        ),
        "agent_consensus": effective_consensus,
        "agent_reverify": effective_reverify,
        "agent_argue_rounds": effective_argue_rounds,
        "agent_postverify": effective_postverify,
        "consensus_diversity": effective_consensus_diversity,
        "agent_timeout": effective_timeout,
        "agent_reasoning": effective_reasoning,
        "agent_levers": effective_levers,
        "agent_ledger": effective_ledger,
        "agent_mcp_ledger": effective_mcp_ledger,
        "agent_answer_gate": effective_answer_gate,
        "agent_taxonomy": effective_taxonomy,
        "trace_attempt_policy": (
            "not-applicable" if effective_driver == "raw-bash" else "reset_on_attempt"
        ),
        "submission_eligibility": (
            "legacy-report-only" if effective_driver == "raw-bash" else "trace-audited"
        ),
        "trace_contract": (
            "answer-only" if effective_driver == "raw-bash" else "per-attempt-jsonl"
        ),
        "n_trials": effective_n_trials,
        "task_filter": task_filter,
    }
    if write_terminal_timeout_config:
        config_payload["terminalize_timeouts"] = effective_terminalize_timeouts
    if write_nested_config:
        config_payload.update(
            {
                "llm_classify_model": effective_classify_model,
                "llm_classify_reasoning": effective_classify_reasoning,
                "llm_classify_concurrency": effective_classify_concurrency,
                "llm_classify_row_budget": effective_classify_row_budget,
                "llm_classify_backend": effective_classify_backend,
            }
        )
    (output_dir / "config.json").write_text(json.dumps(config_payload, indent=2))

    try:
        report = asyncio.run(_run_interim(suite, effective_n_trials, output_dir, task_filter))
    except _RateLimitStopError as exc:
        reset = _rate_limit_reset_summary(exc.result)
        print(
            f"\nDAB run paused after an API rate limit at {exc}{reset}. "
            "The infra row was saved and will be retried. No taint audit or submission "
            "was generated. Resume the same run with:\n"
            f"  uv run python scripts/eval_dab.py --output-dir {output_dir}",
            file=sys.stderr,
            flush=True,
        )
        return _RATE_LIMIT_EXIT_CODE

    if effective_driver == "raw-bash":
        report_body = report_to_markdown(report)
        (output_dir / "report.md").write_text(
            "# Legacy report-only DAB run\n\n"
            "> Submission eligibility: **ineligible**. The raw-bash driver has an "
            "answer-only contamination audit and does not claim trace completeness.\n\n"
            + report_body
        )

    # Pre-submission taint-audit gate: classify every trial (answer text + MCP
    # trace) for answer-key / external-dataset leakage before assembling
    # submission.json. Refuse to write a submission from an unaudited run — see
    # taint.py. The detection-only backstop at suite.py's run_trial seam
    # (_detect_contamination) stays; this is the second, submission-time gate.
    trials_jsonl = output_dir / "trials.jsonl"
    scratch_dir = output_dir / "scratch"
    verdicts = audit_run(trials_jsonl, scratch_dir)
    if report.trials:
        # gate() fails closed on an empty verdict set, which is what catches a
        # trials.jsonl that was written to an orphaned inode: rows exist in memory
        # but the audit saw none, so nothing was actually screened (2026-07-30).
        ok, offenders = gate(verdicts)
    else:
        # A run that executed no trials (empty dab-dir / fully-filtered invocation)
        # has nothing to audit and nothing meaningful to submit.
        ok, offenders = True, []
    if not ok:
        print(
            f"\nTaint audit FAILED: {len(offenders)} trial(s) flagged as "
            "external-oracle-cheating. Refusing to write submission.json.",
            file=sys.stderr,
        )
        for key in offenders:
            print(f"  - {key}", file=sys.stderr)
        print(f"See {output_dir / 'taint.json'} for full verdicts.", file=sys.stderr)
        return 3

    if effective_driver != "raw-bash":
        suite.write_submission(report, output_dir)
        (output_dir / "report.md").write_text(report_to_markdown(report))
    print(f"\nRun complete: {output_dir}")
    print(f"Overall score: {report.score.overall:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
