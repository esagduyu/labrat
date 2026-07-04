"""DAB BenchmarkSuite — task enumeration (Phase 1a).

Real DAB repo layout (~/repos/DataAgentBench/):
  query_<DATASET>/
    db_config.yaml             ← dataset-level; key is db_clients:
    db_description.txt
    db_description_withhint.txt
    query_dataset/             ← raw data files
    query1/
      query.json               ← question as a JSON string
      validate.py
      ground_truth.csv
    query2/ ...
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from labrat.agent.verifier import LLMFn

from labrat.agent.providers import build_provider
from labrat.agent.verifier import provider_llm_fn
from labrat.eval.benchmarks.dab.env import (
    DabTaskEnv,
    build_profiling_connections,
    introspect_env_catalogs,
)
from labrat.eval.types import (
    AggregateScore,
    BenchmarkReport,
    BenchmarkTask,
    TrialResult,
)
from labrat.maze.cartographer import cartograph_prepass
from labrat.maze.scent_audit import detect_contamination as _detect_contamination

Driver = Literal["raw-bash", "labrat-agent", "claude-mcp"]

# Absolute path to the labrat repo root, needed when generating mcp-config files
# that reference this codebase via `uv --directory <labrat>`.
_LABRAT_ROOT = Path(__file__).resolve().parents[4]

_DAB_SYSTEM_PROMPT = (
    "You are a data analyst. Query the databases using Python+DuckDB/SQLite via Bash. "
    "Return your final answer as plain text once you are confident."
)

_DAB_TIMEOUT = (
    1200  # per-trial wall-clock timeout for the claude subprocess (override: --agent-timeout)
)

# Native Claude Code tools the claude-mcp driver blocks so the agent can only
# reach the LabRat MCP server — closes the answer-key/external-label leakage path.
_BLOCKED_NATIVE_TOOLS = "Bash,WebFetch,WebSearch,Task,Read,Write,Edit,NotebookEdit,Glob,Grep"


# Substrings in a trial's final_text that mark it as an infrastructure failure
# (not a model semantic failure). Trials with these markers get reason="infra:..."
# and are excluded from aggregate scoring.
#
# Detection happens once at the run_trial seam — drivers don't need to know about
# infra patterns individually.
_INFRA_PATTERNS: tuple[tuple[str, str], ...] = (
    ("You've hit your session limit", "session_limit"),
    # Per-model / usage quota (e.g. "You've hit your Sonnet limit · resets 5am"). The
    # claude CLI surfaces these as plain text with exit 0, so without this pattern they
    # were miscounted as semantic FAILs — a 2026-06-25 ablation lost 3 of 4 arms this way.
    ("hit your Sonnet limit", "rate_limit"),
    ("hit your Opus limit", "rate_limit"),
    ("hit your Haiku limit", "rate_limit"),
    ("hit your usage limit", "rate_limit"),
    ("Credit balance is too low", "no_api_credit"),
    ("[trial exceeded ", "timeout"),
    # Server-side API outages (the claude CLI surfaces these as "API Error: <code> …").
    # 5xx / overloaded are transient infra, NOT a real attempt — must be retried, not
    # scored as a semantic fail. (A 2026-06-23 Claude outage miscounted 14 such trials.)
    ("API Error: 5", "api_error"),
    ("API Error: 429", "api_error"),
    ("Overloaded", "api_error"),
)


def _detect_infra_failure(final_text: str) -> str | None:
    """Return an infra reason tag (e.g. 'session_limit') if the trial output looks
    like an infrastructure failure rather than a real attempt; otherwise None."""
    for needle, tag in _INFRA_PATTERNS:
        if needle in final_text:
            return tag
    return None


# Contamination detection (answer-key / external-dataset leakage) is shared with the
# Scent-authoring guard — see labrat.maze.scent_audit. The `as` aliases preserve the
# module-local names so the rest of this module (and existing tests) are untouched.
# Contaminated trials are withdrawn from aggregate scoring (see aggregate()), never
# counted as a pass.


def _build_labrat_agent_system_prompt(env: DabTaskEnv) -> str:
    parts = [
        "You are a data analyst. Answer the question by querying the available databases "
        "using the provided tools.",
        "",
        "Tools available:",
        "  profile_dataset — one call returns every table's columns, row counts, foreign "
        "keys, and sample rows (call this FIRST to ground yourself before planning)",
        "  link_schema — given the question, returns the most relevant tables (ranked) to "
        "narrow a wide schema before you describe tables or write SQL",
        "  list_tables / describe_table / search_columns — discover schema",
        "  sample_rows / column_stats — inspect actual values",
        "  verify_join — probe a join's match rate + fan-out BEFORE trusting it (catches "
        "wrong join keys and fan-out that makes aggregates double-count)",
        "  run_sql — execute one SQL statement (DuckDB dialect; primary connection by default)",
        "  explain_sql — show the query plan without executing",
        "  attach_database — pull a SQLite/Postgres/MySQL file into the primary DuckDB "
        "session for cross-database JOINs",
        "  load_file — load a CSV/TSV/JSON/Parquet file into the session as a table",
        "  load_mongo_collection — materialize a MongoDB collection into a DuckDB "
        "table on the primary connection (nested fields become STRUCTs; address with dot)",
    ]
    if env.attachable:
        parts.append("")
        parts.append("Secondary databases you may attach (call attach_database first):")
        for spec in env.attachable:
            parts.append(f"  alias={spec.alias} path={spec.path} db_type={spec.db_type}")
        parts.append("Once attached, refer to its tables as <alias>.<table_name> in run_sql.")
    if env.mongo:
        parts.append("")
        parts.append("MongoDB databases you may load (call load_mongo_collection per collection):")
        for mspec in env.mongo:
            parts.append(f"  alias={mspec.alias} database={mspec.database}")
        parts.append("Materialized collections become DuckDB tables you query with run_sql.")
    levers = _dab_lever_lines()
    parts.extend(
        [
            "",
            "Approach:",
            "  1. Call profile_dataset first to ground yourself in the real schema, row "
            "counts, and sample values before planning.",
            "  2. On a wide or unfamiliar schema, call link_schema with the question to "
            "narrow to the relevant tables before planning.",
            "  3. Plan the steps, then run them one at a time, reading each result before "
            "the next. Before any multi-table JOIN, call verify_join to confirm the keys "
            "match and won't fan out.",
            "  4. Before answering, re-read the question and confirm your result actually "
            "answers it (check magnitudes, units, and that joins didn't drop or fan out rows).",
            *[f"  {5 + i}. {lever}" for i, lever in enumerate(levers)],
            "",
            "Run queries until you are confident, then respond with a single plain answer "
            "on the last line.",
        ]
    )
    return "\n".join(parts)


async def _invoke_agent(
    prompt: str,
    ctx: Any,
    max_turns: int = 15,
) -> dict[str, Any]:
    """Invoke claude --print with Bash tool to query DuckDB/SQLite databases.

    Uses --disable-slash-commands (skip superpowers skill overhead) and
    --dangerously-skip-permissions (auto-approve Bash) so the model can run
    Python+DuckDB queries directly without a permission dialog.

    Extracted as a module-level helper so unit tests can patch it.
    Will be replaced by LabRatAgentDriver in Phase 4.
    """
    import shutil
    import subprocess

    if not shutil.which("claude"):
        raise RuntimeError(
            "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
        )

    env = {
        k: v
        for k, v in os.environ.items()
        if k != "ANTHROPIC_API_KEY" and k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE")
    }

    cmd = [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--model",
        "claude-sonnet-4-6",
        "--disable-slash-commands",
        "--dangerously-skip-permissions",
    ]

    full_prompt = f"SYSTEM:\n{_DAB_SYSTEM_PROMPT}\n\n{prompt}"

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            input=full_prompt.encode(),
            capture_output=True,
            timeout=_DAB_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired:
        # Record the timeout as a trial-level failure rather than crashing the run.
        return {
            "final_text": f"[trial exceeded {_DAB_TIMEOUT}s timeout]",
            "tool_calls": 0,
        }

    raw = result.stdout.decode(errors="replace").strip()
    err = result.stderr.decode(errors="replace").strip()

    # claude CLI sometimes exits non-zero but still emits a JSON result (e.g.
    # error_max_turns). Try to extract the result before deciding to fail.
    final_text = raw
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "result" in data:
            final_text = str(data["result"])  # type: ignore[arg-type]
    except json.JSONDecodeError:
        pass

    if result.returncode != 0 and not final_text:
        raise RuntimeError(
            f"claude CLI error (exit {result.returncode}): "
            f"stderr={err[:200] or '(empty)'} stdout={raw[:200] or '(empty)'}"
        )

    return {"final_text": final_text, "tool_calls": 0}


def _safe_name(name: str) -> str:
    """Filesystem-safe per-dataset dir name."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", name).strip(".")
    return cleaned or "dataset"


def _cartographer_prompt_line() -> str:
    return (
        "A curated reference doc for this database has been pre-generated. Call "
        "search_reference_docs(question) FIRST for grounding (table grain, verified join "
        "keys, observed dimension values) before profiling or writing SQL."
    )


def _dab_lever_lines() -> list[str]:
    """Benchmark-safe process levers shared by both DAB driver prompts.

    Pure process/structure — no answer content, so safe on held-out datasets.
    Target the failure classes Cartographer doesn't cover: answer-from-memory
    (force-query), implementation errors (repair via run_sql diagnostics), and
    broad-fetch-then-tally (push aggregation into SQL).
    """
    return [
        "Always derive the answer by querying the database — never answer from prior "
        "knowledge or memory, even for facts you think you know.",
        "If run_sql returns an error, read its error_category and hint and fix the query "
        "— don't guess; re-run until it executes cleanly.",
        "Compute counts/sums/aggregates in SQL (GROUP BY + aggregate functions), not by "
        "fetching rows and tallying them yourself.",
        "For 'top N' / 'highest' questions, remember a bare LIMIT N silently truncates ties: "
        "if the Nth value can repeat, rank with ties (RANK()/DENSE_RANK() or fetch the full tie "
        "band) rather than LIMIT alone.",
    ]


_CONSENSUS_FRAMINGS: list[str] = [
    "Pay extra attention to filters and NULL-handling — confirm no rows were wrongly dropped.",
    "Double-check join grain and whether the top value ties with others.",
    "Confirm units, magnitudes, and that aggregates aren't double-counting from a fan-out join.",
    "Re-read the question's exact wording (which column, which date, coded vs named values) "
    "before finalizing.",
]


def _framing_for(diversity_index: int | None) -> str:
    """Rotated, process-only framing line for a consensus sub-run.

    Pure function of the sub-run index — ``None`` (single-run / no diversity) returns
    "" so callers can append unconditionally without an extra branch. Never mentions
    answer content; only shifts analytical emphasis across K sub-runs.
    """
    if diversity_index is None:
        return ""
    return _CONSENSUS_FRAMINGS[diversity_index % len(_CONSENSUS_FRAMINGS)]


def _build_claude_mcp_prompt(
    primary_name: str,
    env_spec: DabTaskEnv,
    task: BenchmarkTask,
    *,
    include_cartographer_line: bool,
    max_tool_calls: int | None,
) -> str:
    """Build the claude-mcp driver's opening user message.

    Pure function of the task + flags — extracted from `_run_trial_claude_mcp` so the
    exact opening prompt can be emitted for audit (e.g. the leaderboard prompt-leakage
    check) without re-running the agent. The model also receives the stock Claude Code
    CLI system prompt (the driver passes no custom `--system-prompt`).
    """
    prompt_lines = [
        "You have a labrat MCP server connected. It exposes data tools "
        "(link_schema, list_tables, describe_table, sample_rows, run_sql, "
        "verify_join, attach_database, load_mongo_collection, …) against "
        f"the primary DuckDB database '{primary_name}'.",
        "On a wide/unfamiliar schema call link_schema(question) first to find the "
        "relevant tables; before any multi-table JOIN call verify_join to confirm the "
        "keys match and won't fan out.",
    ]
    if include_cartographer_line:
        prompt_lines.insert(1, _cartographer_prompt_line())
    prompt_lines.extend(_dab_lever_lines())
    if env_spec.attachable:
        prompt_lines.append("")
        prompt_lines.append(
            "Secondary databases you can bring in via attach_database (alias / path / db_type):"
        )
        for spec in env_spec.attachable:
            prompt_lines.append(f"  {spec.alias} / {spec.path} / {spec.db_type}")
        prompt_lines.append("After attach, query tables as <alias>.<table_name> in run_sql.")
    if env_spec.mongo:
        prompt_lines.append("")
        prompt_lines.append(
            "MongoDB databases you can pull into DuckDB via load_mongo_collection "
            "(alias / database):"
        )
        for mspec in env_spec.mongo:
            prompt_lines.append(f"  {mspec.alias} / {mspec.database}")
        prompt_lines.append(
            "Each call materializes one collection into a DuckDB table you query with run_sql."
        )
    prompt_lines.extend(
        [
            "",
            "Question:",
            task.prompt,
            "",
            "When confident, respond with the final answer on the last line.",
        ]
    )
    # max_tool_calls is advisory under claude-mcp (the CLI has no native cap);
    # surface it in the prompt so the model self-regulates.
    if max_tool_calls is not None:
        prompt_lines.append(f"\nBudget: at most {max_tool_calls} tool calls. Plan accordingly.")
    return "\n".join(prompt_lines)


async def _run_cartographer(
    env_spec: DabTaskEnv,
    dataset: str,
    cache_root: Path,
    *,
    with_semantics: bool = False,
    llm_fn: LLMFn | None = None,
    variant_seed: int = 0,
) -> Path:
    """Run the deterministic, GT-firewalled cartographer on the task's primary DB and
    return the maze root to expose as LABRAT_MAZE_DIR.

    Per-dataset store under ``cache_root`` so datasets that share a connection key
    (e.g. 'main') never collide; idempotent (cartograph_prepass caches on first contact).
    Deterministic-only — never calls a model, never touches answer-key files.

    ``variant_seed`` (default 0) is per-sub-run diversity plumbing: when >0 the maze
    root is suffixed with ``variant<seed>`` so each variant gets its own Scent store,
    and the seed is threaded into ``generate_scent`` (via ``cartograph_prepass``) so
    high-cardinality dimension sampling varies across variants. ``variant_seed=0``
    leaves the maze root and behavior unchanged from before this parameter existed.
    """
    maze_root = cache_root / _safe_name(dataset)
    if variant_seed > 0:
        maze_root = maze_root / f"variant{variant_seed}"
    scent_dir = maze_root / "labrat_maze" / "scent"

    ctx = env_spec.ctx
    for conn in ctx.connections.values():
        connect = getattr(conn, "connect", None)
        if callable(connect):
            connect()
    prof_conns, prof_cats = build_profiling_connections(env_spec.attachable)
    try:
        introspect_env_catalogs(ctx)
        # Pass MERGED COPIES to the prepass; never mutate the agent's ctx (runtime stays
        # DuckDB-only). Attached-DB entries live only in these local dicts.
        merged_conns: dict[str, object] = {**ctx.connections, **prof_conns}
        merged_cats: dict[str, object] = {**ctx.catalogs, **prof_cats}
        await cartograph_prepass(
            merged_conns,
            merged_cats,
            ctx.primary,
            scent_dir,
            with_semantics=with_semantics,
            llm_fn=llm_fn,
            variant_seed=variant_seed,
        )
    finally:
        for conn in ctx.connections.values():
            disconnect = getattr(conn, "disconnect", None)
            if callable(disconnect):
                disconnect()
        for conn in prof_conns.values():
            disconnect = getattr(conn, "disconnect", None)
            if callable(disconnect):
                disconnect()
    return maze_root


_DATASET_DIR_RE = re.compile(r"^query_(.+)$", re.IGNORECASE)
_QUERY_DIR_RE = re.compile(r"^query(\d+)$")


class DabSuite:
    """Reads DAB queries from a DataAgentBench checkout."""

    name = "dab"

    def __init__(
        self,
        dab_dir: Path | None = None,
        hints: bool = False,
        driver: Driver = "raw-bash",
        agent_model: str = "claude-sonnet-4-6",
        agent_provider: str = "anthropic",
        agent_max_turns: int | None = None,
        agent_max_tool_calls: int | None = None,
        agent_verify: bool = False,
        agent_timeout: int | None = None,
        agent_reasoning: str | None = None,
        cartograph: bool = False,
        cartograph_semantics: bool = False,
        cartograph_semantics_model: str = "claude-sonnet-4-6",
        cartograph_semantics_provider: str = "anthropic",
        cartograph_scent_root: Path | None = None,
        consensus_k: int | None = None,
        reverify: bool = False,
        consensus_diversity: bool = True,
        argue_rounds: int = 0,
        postverify: bool = False,
    ) -> None:
        self._dir = (
            dab_dir or Path(os.environ.get("DAB_DIR", "~/repos/DataAgentBench")).expanduser()
        )
        self._hints = hints
        self._driver: Driver = driver
        self._agent_model = agent_model
        self._agent_provider = agent_provider
        self._agent_max_turns = agent_max_turns
        self._agent_max_tool_calls = agent_max_tool_calls
        # Opt-in LLM-as-judge verifier for the labrat-agent driver (loop-level, so it
        # has no effect under raw-bash / claude-mcp, whose loops live elsewhere).
        self._agent_verify = agent_verify
        # Per-call provider timeout override (seconds); only the claude-code provider
        # honours it. None = provider default (120s for claude-code).
        self._agent_timeout = agent_timeout
        # Reasoning effort for the codex (GPT-5.5) provider; ignored by the others.
        # None = provider default (medium).
        self._agent_reasoning = agent_reasoning
        # Opt-in deterministic cartographer pre-pass: generates per-dataset Scent docs
        # into a per-run temp dir so the agent can consult them via search_reference_docs.
        self._cartograph = cartograph
        # Opt-in LLM-semantic authoring on the cartographer pre-pass. The authoring model
        # is independent of the agent/trial model and routed via _cartograph_llm_fn.
        self._cartograph_semantics = cartograph_semantics
        self._cartograph_semantics_model = cartograph_semantics_model
        self._cartograph_semantics_provider = cartograph_semantics_provider
        # An explicit persistent dir overrides the per-run tmpdir (enables freeze-and-commit
        # of authored Scent for submissions); default unchanged.
        self._scent_cache_root = (
            cartograph_scent_root
            if cartograph_scent_root is not None
            else (Path(tempfile.mkdtemp(prefix="labrat-dab-scent-")) if cartograph else Path())
        )
        self._consensus_k = consensus_k
        self._reverify = reverify
        # When True (default) and consensus_k > 1, each K sub-run gets a distinct
        # diversity_index (variant Scent + rotated framing — see _framing_for /
        # _run_trial_claude_mcp). False = the null-baseline A/B for the ablation
        # (all sub-runs identical, diversity_index=None throughout).
        self._consensus_diversity = consensus_diversity
        # Bounded argumentation rounds run only when the K-run consensus vote above
        # comes back low_confidence (a split vote) — see _run_trial_verified. 0 (default)
        # disables the loop entirely, leaving the existing consensus/reverify behavior
        # byte-identical.
        self._argue_rounds = argue_rounds
        # Deterministic post-hoc constraint check on the FINAL answer (post-consensus,
        # post-argue, post-reverify). Independent of consensus/reverify — works standalone.
        # On a violation, one bounded revise dispatch; fail-open on any error. See
        # _run_trial_verified's postverify block.
        self._postverify = postverify
        self._tasks_cache: list[BenchmarkTask] | None = None

    @property
    def driver(self) -> Driver:
        return self._driver

    def tasks(self) -> Iterable[BenchmarkTask]:
        if self._tasks_cache is None:
            self._tasks_cache = self._load_tasks()
        return self._tasks_cache

    def _load_tasks(self) -> list[BenchmarkTask]:
        result: list[BenchmarkTask] = []
        if not self._dir.exists():
            return result

        for dataset_dir in sorted(self._dir.iterdir()):
            m = _DATASET_DIR_RE.match(dataset_dir.name)
            if not m or not dataset_dir.is_dir():
                continue
            dataset_name = m.group(1).lower()

            db_config = dataset_dir / "db_config.yaml"
            if not db_config.exists():
                continue

            # Base schema description, then APPEND the hints when --hints is on —
            # mirroring DAB's own run_agent.py (`db_description += "\n\n" + hints`).
            # The withhint file is hints-only (a few lines of data-quirk guidance), so
            # loading it *instead of* the base would drop the entire schema.
            base_file = dataset_dir / "db_description.txt"
            description = base_file.read_text().strip() if base_file.exists() else ""
            if self._hints:
                hint_file = dataset_dir / "db_description_withhint.txt"
                hint_text = hint_file.read_text().strip() if hint_file.exists() else ""
                if hint_text:
                    description = f"{description}\n\n{hint_text}".strip()

            for query_dir in sorted(dataset_dir.iterdir()):
                qm = _QUERY_DIR_RE.match(query_dir.name)
                if not qm or not query_dir.is_dir():
                    continue
                query_num = qm.group(1)

                query_file = query_dir / "query.json"
                validator = query_dir / "validate.py"
                if not query_file.exists() or not validator.exists():
                    continue

                question = json.loads(query_file.read_text().strip())
                if not isinstance(question, str) or not question:
                    continue

                prompt_parts = [p for p in [description, question] if p]
                prompt = "\n\n".join(prompt_parts)

                result.append(
                    BenchmarkTask(
                        id=f"{dataset_name}:{query_num}",
                        benchmark="dab",
                        prompt=prompt,
                        config={
                            "dataset": dataset_name,
                            "query_num": query_num,
                            "db_config_path": str(db_config),
                            "validator_path": str(validator),
                            "hints": self._hints,
                        },
                    )
                )

        return result

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult:
        from labrat.eval.benchmarks.dab.scorer import score_with_validator

        scratch_dir.mkdir(parents=True, exist_ok=True)
        db_config_path = Path(task.config["db_config_path"])
        validator_path = Path(task.config["validator_path"])
        self._last_usage: dict[str, int] | None = None

        try:
            final_text, tool_calls, latency = await self._run_trial_verified(
                task, db_config_path, scratch_dir
            )
        except Exception as exc:
            # A provider/agent exception (e.g. claude-code's per-call TimeoutError) must
            # fail only THIS trial, not crash the whole run. Record it as an infra failure
            # so aggregate() skips it and a --output-dir resume auto-retries it.
            tag = "timeout" if isinstance(exc, TimeoutError) else "agent_error"
            return TrialResult(
                task_id=task.id,
                trial_num=trial_num,
                passed=False,
                reason=f"infra:{tag}",
                latency_seconds=0.0,
                tool_calls=0,
                artifact={"type": "text", "payload": f"{type(exc).__name__}: {exc}"},
            )

        # Infra failures (Max-plan session limit, API credit, wall-clock timeout)
        # don't reflect the agent's ability and shouldn't pollute aggregate scoring.
        # Mark them with reason="infra:<tag>" so aggregate() can skip them and
        # eval_dab.py can print INFRA instead of FAIL.
        infra_tag = _detect_infra_failure(final_text)
        if infra_tag is not None:
            return TrialResult(
                task_id=task.id,
                trial_num=trial_num,
                passed=False,
                reason=f"infra:{infra_tag}",
                latency_seconds=latency,
                tool_calls=tool_calls,
                artifact={"type": "text", "payload": final_text},
            )

        # Data-leakage backstop: if the trace shows the agent reached the answer key
        # or an external labelled dataset, withdraw the trial regardless of whether
        # it would have scored a pass. With a properly sandboxed driver this never
        # fires; if it does, the reason flags a sandbox regression loudly.
        contamination_tag = _detect_contamination(final_text)
        if contamination_tag is not None:
            return TrialResult(
                task_id=task.id,
                trial_num=trial_num,
                passed=False,
                reason=f"contaminated:{contamination_tag}",
                latency_seconds=latency,
                tool_calls=tool_calls,
                artifact={"type": "text", "payload": final_text},
            )

        passed, reason = score_with_validator(validator_path, final_text)

        return TrialResult(
            task_id=task.id,
            trial_num=trial_num,
            passed=passed,
            reason=reason,
            latency_seconds=latency,
            tool_calls=tool_calls,
            artifact={"type": "text", "payload": final_text},
            meta={"usage": self._last_usage} if self._last_usage else {},
        )

    # ── verified dispatch (consensus + re-derive) ─────────────────────────────

    def _verify_llm_fn(self) -> LLMFn:
        # The claude-mcp driver runs on Max-plan OAuth with ANTHROPIC_API_KEY stripped,
        # so the in-process "anthropic" judge can't authenticate there. Route the judge
        # through the claude-code provider (same OAuth) on that path; otherwise reuse
        # the provider the agent itself runs on.
        judge_provider = "claude-code" if self._driver == "claude-mcp" else self._agent_provider
        provider = build_provider(judge_provider, self._agent_model)
        return provider_llm_fn(provider)

    def _cartograph_llm_fn(self) -> LLMFn:
        # Author the semantics pass with the independent semantics model. Route to the
        # claude-code provider on the claude-mcp path (Max-plan OAuth; ANTHROPIC_API_KEY
        # is stripped there), mirroring _verify_llm_fn; honor the configured provider
        # on the other drivers.
        provider = (
            "claude-code" if self._driver == "claude-mcp" else self._cartograph_semantics_provider
        )
        return provider_llm_fn(build_provider(provider, self._cartograph_semantics_model))

    async def _run_trial_verified(
        self,
        task: BenchmarkTask,
        db_config_path: Path,
        scratch_dir: Path,
    ) -> tuple[str, int, float]:
        from labrat.agent.verification.agreement import answers_agree
        from labrat.agent.verification.consensus import choose_modal

        question = task.prompt
        k = self._consensus_k or 1
        # postverify is an independent mechanism from consensus/reverify — it must route
        # into this verified path (sub-run scratch dirs, verification.json) even when it's
        # the only verification flag on.
        verification_active = k > 1 or self._reverify or self._postverify

        async def _run_once(
            i: int, extra: str = "", diversity_index: int | None = None
        ) -> tuple[str, int, float]:
            sub = scratch_dir / f"subrun{i}" if verification_active else scratch_dir
            sub.mkdir(parents=True, exist_ok=True)
            return await self._dispatch_driver_once(
                task,
                db_config_path,
                sub,
                extra_instructions=extra,
                diversity_index=diversity_index,
            )

        total_latency = 0.0

        # ── Consensus: K sub-runs → modal ──────────────────────────────
        modal_index: int | None = None
        low_confidence: bool | None = None
        consensus_answers: list[str] | None = None
        chosen_subdir_i: int | None = None
        results: list[tuple[str, int, float]] = []

        if k > 1:
            for i in range(k):
                try:
                    r = await _run_once(
                        i, diversity_index=(i if self._consensus_diversity else None)
                    )
                    results.append(r)
                    total_latency += r[2]
                except Exception:
                    continue  # a failed sub-run is excluded from the vote
            if not results:
                return await _run_once(0)  # all failed → let run_trial's handler see it
            llm_fn = self._verify_llm_fn()
            idx, low = await choose_modal([r[0] for r in results], question=question, llm_fn=llm_fn)
            modal_index = idx
            low_confidence = low
            consensus_answers = [r[0] for r in results]
            primary = results[idx]
            chosen_subdir_i = idx
        else:
            primary = await _run_once(0)
            total_latency += primary[2]
            if verification_active:  # reverify-only path
                chosen_subdir_i = 0

        # ── Argumentation: bounded rounds on a split vote ────────────────
        # Only fires when the K-run vote above came back low_confidence AND the
        # caller opted in via --agent-argue-rounds. Each round re-dispatches every
        # sub-run with the OTHER sub-runs' answers appended ("Other analysts
        # concluded: ..."), then re-votes. Stops early once a majority forms.
        # Fail-open: any dispatch/judge error mid-round keeps the current best
        # (modal) answer from before that round and stops arguing.
        argue_rounds_used = 0
        if k > 1 and low_confidence and self._argue_rounds > 0:
            for round_num in range(self._argue_rounds):
                try:
                    argued: list[tuple[str, int, float]] = []
                    for i in range(len(results)):
                        block_lines = ["Other analysts concluded:"]
                        for j, r in enumerate(results):
                            if j == i:
                                continue
                            block_lines.append(f"- {r[0][:1500]}")
                        block_lines.append(
                            "Reconsider your answer in light of the above. If you still "
                            "believe your answer is correct, restate it; otherwise revise. "
                            "Give the single final answer clearly."
                        )
                        argue_extra = "\n".join(block_lines)
                        r = await _run_once(
                            i,
                            extra=argue_extra,
                            diversity_index=(i if self._consensus_diversity else None),
                        )
                        argued.append(r)
                    total_latency += sum(r[2] for r in argued)
                    results = argued
                    argue_rounds_used = round_num + 1
                    llm_fn = self._verify_llm_fn()
                    idx, low = await choose_modal(
                        [r[0] for r in results], question=question, llm_fn=llm_fn
                    )
                    modal_index = idx
                    low_confidence = low
                    consensus_answers = [r[0] for r in results]
                    primary = results[idx]
                    chosen_subdir_i = idx
                    if not low_confidence:
                        break
                except Exception:
                    break  # fail-open: keep the current best (modal) answer

        # Track re-derive metadata for persistence
        rederived_answer: str | None = None
        agreed: bool | None = None
        reconcile_used = False
        final_answer = primary

        # ── Re-derive: one independent run + reconcile on mismatch ──────
        if self._reverify:
            try:
                rederived = await _run_once(900)  # distinct sub-scratch
                total_latency += rederived[2]
                rederived_answer = rederived[0]
                llm_fn = self._verify_llm_fn()
                agreed = await answers_agree(
                    primary[0], rederived[0], question=question, llm_fn=llm_fn
                )
                if not agreed:
                    reconcile = await _run_once(
                        901,
                        extra=(
                            "An independent recomputation produced a DIFFERENT result:\n"
                            f"  your answer: {primary[0]}\n  independent: {rederived[0]}\n"
                            "Recompute carefully and give the single correct final answer "
                            "on the last line."
                        ),
                    )
                    total_latency += reconcile[2]
                    reconcile_used = True
                    final_answer = reconcile
                    chosen_subdir_i = 901
            except Exception:
                pass  # fail-open: keep the primary answer

        # ── Post-verify: deterministic constraint check + one bounded revise ─
        # Runs on the FINAL answer (post-consensus, post-argue, post-reverify).
        # Independent of consensus_k/reverify — works standalone.
        postverify_violations: list[str] = []
        postverify_revised = False
        if self._postverify:
            try:
                from labrat.agent.verification.constraints import check_answer_constraints

                postverify_violations = check_answer_constraints(question, final_answer[0])
                if postverify_violations:
                    violation_lines = ["Your answer does not satisfy the question's constraints:"]
                    violation_lines.extend(f"- {v}" for v in postverify_violations)
                    violation_lines.append(
                        "Revise your answer to satisfy these constraints. Give the single "
                        "final answer clearly."
                    )
                    revised = await _run_once(
                        902, extra="\n".join(violation_lines), diversity_index=None
                    )
                    total_latency += revised[2]
                    final_answer = revised
                    postverify_revised = True
                    chosen_subdir_i = 902
            except Exception:
                pass  # fail-open: keep the original final answer

        # ── Persist verification traces (spec §6) ───────────────────────
        if verification_active:
            try:
                vdata: dict[str, Any] = {
                    "consensus_k": self._consensus_k,
                    "reverify": self._reverify,
                    "consensus_diversity": self._consensus_diversity,
                    "consensus_answers": consensus_answers,
                    "modal_index": modal_index,
                    "low_confidence": low_confidence,
                    "argue_rounds": self._argue_rounds,
                    "argue_rounds_used": argue_rounds_used,
                    "rederived_answer": rederived_answer,
                    "agreed": agreed,
                    "reconcile_used": reconcile_used,
                    "postverify": self._postverify,
                    "postverify_violations": postverify_violations,
                    "postverify_revised": postverify_revised,
                    "chosen_answer": final_answer[0],
                }
                (scratch_dir / "verification.json").write_text(json.dumps(vdata, indent=2))
            except Exception:
                pass  # fail-open: a write error must never trap the trial

            # Best-effort: promote chosen sub-run's trace file to scratch root
            if chosen_subdir_i is not None:
                try:
                    import shutil

                    chosen_sub = scratch_dir / f"subrun{chosen_subdir_i}"
                    for src in chosen_sub.glob("*_tool_calls.jsonl"):
                        dst = scratch_dir / src.name
                        if not dst.exists():
                            shutil.copy2(src, dst)
                        break  # only one trace file expected per sub-run
                except Exception:
                    pass  # fail-open

        return (final_answer[0], final_answer[1], total_latency)

    async def _dispatch_driver_once(
        self,
        task: BenchmarkTask,
        db_config_path: Path,
        scratch_dir: Path,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        if self._driver == "labrat-agent":
            return await self._run_trial_labrat_agent(
                task,
                db_config_path,
                scratch_dir,
                extra_instructions=extra_instructions,
                diversity_index=diversity_index,
            )
        if self._driver == "claude-mcp":
            return await self._run_trial_claude_mcp(
                task,
                db_config_path,
                scratch_dir,
                extra_instructions=extra_instructions,
                diversity_index=diversity_index,
            )
        return await self._run_trial_raw_bash(task, db_config_path)

    # ── raw-bash driver (Phase 1b baseline) ──────────────────────────────────

    async def _run_trial_raw_bash(
        self, task: BenchmarkTask, db_config_path: Path
    ) -> tuple[str, int, float]:
        import yaml

        dataset_dir = db_config_path.parent
        config = yaml.safe_load(db_config_path.read_text())
        clients: dict[str, Any] = config.get("db_clients") or {}
        db_lines: list[str] = []
        duckdb_clients: dict[str, Path] = {}
        sqlite_clients: dict[str, Path] = {}
        for name, spec in clients.items():
            db_type = str(spec.get("db_type", "")).lower()
            db_path = dataset_dir / str(spec.get("db_path", ""))
            if db_type == "duckdb":
                duckdb_clients[name] = db_path
                db_lines.append(
                    f'  {name} (DuckDB): python3 -c "import duckdb; '
                    f"conn = duckdb.connect('{db_path}'); "
                    "print(conn.execute('SELECT ...').fetchall())\""
                )
            elif db_type == "sqlite":
                sqlite_clients[name] = db_path
                db_lines.append(
                    f'  {name} (SQLite): python3 -c "import sqlite3; '
                    f"conn = sqlite3.connect('{db_path}'); "
                    "print(conn.execute('SELECT ...').fetchall())\""
                )

        if duckdb_clients and sqlite_clients:
            _duck_name, duck_path = next(iter(duckdb_clients.items()))
            attach_lines = [
                "\nCross-database JOINs — attach SQLite into DuckDB"
                " (use this for queries spanning both databases):"
            ]
            for sql_name, sql_path in sqlite_clients.items():
                attach_lines.append(
                    f'  python3 -c "\nimport duckdb\n'
                    f"conn = duckdb.connect('{duck_path}')\n"
                    f"conn.execute(\\\"ATTACH '{sql_path}' AS {sql_name} (TYPE SQLITE)\\\")\n"
                    f"# query: SELECT ... FROM duck_table"
                    f" JOIN {sql_name}.sqlite_table ON ...\n"
                    f"print(conn.execute('SELECT ...').fetchall())\n\""
                )
            db_lines.extend(attach_lines)

        db_preamble = "You can query these databases via Bash (Python is available):\n" + "\n".join(
            db_lines
        )
        enriched_prompt = f"{db_preamble}\n\n{task.prompt}"

        # Raw-bash defaults to max_turns=15 for Phase 1b reproducibility, but
        # honour an explicit override so the user can rerun the baseline with
        # tighter / looser budgets when comparing to other drivers.
        raw_bash_max_turns = self._agent_max_turns if self._agent_max_turns is not None else 15
        t0 = time.monotonic()
        agent_out = await _invoke_agent(
            prompt=enriched_prompt, ctx=None, max_turns=raw_bash_max_turns
        )
        latency = time.monotonic() - t0
        return agent_out["final_text"], int(agent_out["tool_calls"]), latency

    # ── claude-mcp driver (Phase 4 on Max-plan billing) ──────────────────────

    async def _run_trial_claude_mcp(
        self,
        task: BenchmarkTask,
        db_config_path: Path,
        scratch_dir: Path,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        """Phase 4 driver that uses the LabRat MCP server inside `claude --print`.

        Generates a per-trial mcp-config.json pointing at ``labrat.mcp.server``
        with the task's DuckDB primary, then shells the claude CLI with
        ``--model <agent_model>`` (so the subprocess never falls through to
        whatever model the parent claude session is using). SQLite secondaries
        are surfaced in the prompt; the model uses the ``attach_database`` MCP
        tool to bring them in.
        """
        import shutil
        import subprocess

        from labrat.db.duckdb_engine import DuckDBConnection
        from labrat.eval.benchmarks.dab.env import build_dab_task_env

        # Resolve to absolute up front: the subprocess runs with cwd=scratch_dir
        # (filesystem isolation), so any relative --mcp-config / log path would be
        # re-resolved by the claude CLI against the new cwd and double. The harness
        # passes a repo-relative scratch dir, so this matters in practice.
        scratch_dir = scratch_dir.resolve()

        if not shutil.which("claude"):
            raise RuntimeError(
                "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
            )

        env_spec = build_dab_task_env(db_config_path)
        primary_name = env_spec.ctx.primary
        primary_conn = env_spec.ctx.connections[primary_name]
        if not isinstance(primary_conn, DuckDBConnection):
            raise RuntimeError(
                f"claude-mcp driver requires a DuckDB primary; got "
                f"{type(primary_conn).__name__} for {primary_name!r}."
            )

        maze_root: Path | None = None
        if self._cartograph:
            dataset = task.id.split(":")[0]
            maze_root = await _run_cartographer(
                env_spec,
                dataset,
                self._scent_cache_root,
                with_semantics=self._cartograph_semantics,
                llm_fn=self._cartograph_llm_fn() if self._cartograph_semantics else None,
                variant_seed=diversity_index or 0,
            )
            (maze_root / "_home").mkdir(parents=True, exist_ok=True)

        framing = _framing_for(diversity_index)
        if framing:
            extra_instructions = (extra_instructions + "\n" + framing).strip()

        mcp_config = {
            "mcpServers": {
                "labrat": {
                    "command": "uv",
                    "args": [
                        "--directory",
                        str(_LABRAT_ROOT),
                        "run",
                        "python",
                        "-m",
                        "labrat.mcp.server",
                    ],
                    "env": {
                        "LABRAT_MCP_CONNECTIONS": json.dumps(
                            {
                                primary_name: {
                                    "db_type": "duckdb",
                                    "db_path": primary_conn.path,
                                }
                            }
                        ),
                        "LABRAT_MCP_PRIMARY": primary_name,
                        # Audit-grade per-call traces land in the trial scratch dir
                        # (one mcp_tool_calls.jsonl line per dispatch) — first-class
                        # traces instead of reconstructing from ~/.claude after the fact.
                        "LABRAT_MCP_LOG_DIR": str(scratch_dir),
                        **(
                            {
                                "LABRAT_MAZE_DIR": str(maze_root),
                                # hermetic: the user Scent layer (~/.labrat) must not
                                # contribute on the benchmark — point HOME at an empty dir.
                                "HOME": str(maze_root / "_home"),
                            }
                            if maze_root is not None
                            else {}
                        ),
                    },
                }
            }
        }
        mcp_config_path = scratch_dir / "mcp-config.json"
        mcp_config_path.write_text(json.dumps(mcp_config))

        prompt = _build_claude_mcp_prompt(
            primary_name,
            env_spec,
            task,
            include_cartographer_line=maze_root is not None,
            max_tool_calls=self._agent_max_tool_calls,
        )
        if extra_instructions:
            prompt = f"{prompt}\n\n{extra_instructions}"

        # max_turns under claude-mcp maps to claude CLI's --max-turns. If
        # unbounded (None), pass a high ceiling (200) so the CLI doesn't apply
        # its own short default.
        effective_max_turns = self._agent_max_turns if self._agent_max_turns is not None else 200

        # Sandbox gate: restrict the agent's tools to the LabRat MCP server and
        # explicitly block every native Claude Code tool. Without this the agent
        # keeps Bash/WebFetch/Task even under bypassPermissions and can read the
        # benchmark's answer keys off disk or fetch external labels (the 2026-06-03
        # contamination). --disallowedTools takes precedence and is the hard block;
        # --allowedTools scopes the rest to the MCP server.
        cmd = [
            "claude",
            "--print",
            "--strict-mcp-config",
            "--mcp-config",
            str(mcp_config_path),
            "--allowedTools",
            "mcp__labrat",
            "--disallowedTools",
            _BLOCKED_NATIVE_TOOLS,
            "--model",
            self._agent_model,
            "--permission-mode",
            "bypassPermissions",
            "--max-turns",
            str(effective_max_turns),
            "--output-format",
            "json",
        ]

        # Max-plan billing: strip ANTHROPIC_API_KEY so the CLI falls through to
        # OAuth credentials. Drop CLAUDECODE / CLAUDE_CODE_* so a nested call
        # under a Claude Code session doesn't try to phone home to the parent.
        env_vars = {
            k: v
            for k, v in os.environ.items()
            if k != "ANTHROPIC_API_KEY" and k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE")
        }

        # Per-trial wall-clock: --agent-timeout override, else the 1200s default.
        # Hard classification queries (e.g. agnews) need headroom beyond 600s.
        effective_timeout = self._agent_timeout if self._agent_timeout is not None else _DAB_TIMEOUT

        t0 = time.monotonic()
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                input=prompt.encode(),
                capture_output=True,
                timeout=effective_timeout,
                env=env_vars,
                # Filesystem isolation: run in the per-trial scratch dir so the
                # benchmark checkout (validate.py / ground_truth.csv) is not under
                # the agent's cwd. DB paths reach the MCP server via env, not cwd.
                cwd=str(scratch_dir),
            )
        except subprocess.TimeoutExpired:
            # Record the timeout as a trial-level failure (the validator will mark
            # passed=False) rather than crashing the whole run.
            latency = time.monotonic() - t0
            return f"[trial exceeded {effective_timeout}s timeout]", 0, latency
        latency = time.monotonic() - t0

        raw = result.stdout.decode(errors="replace").strip()
        final_text = raw
        num_turns = 0
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                if "result" in data:
                    final_text = str(data["result"])  # type: ignore[arg-type]
                turns_val = data.get("num_turns", 0)  # type: ignore[arg-type]
                if isinstance(turns_val, int):
                    num_turns = turns_val
        except json.JSONDecodeError:
            pass

        if result.returncode != 0 and not final_text:
            err = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"claude --print error (exit {result.returncode}): "
                f"stderr={err[:200] or '(empty)'} stdout={raw[:200] or '(empty)'}"
            )

        # num_turns counts assistant rounds; tool calls = rounds beyond the final answer.
        tool_calls = max(0, num_turns - 1)
        return final_text, tool_calls, latency

    # ── labrat-agent driver (Phase 4 measurement) ────────────────────────────

    async def _run_trial_labrat_agent(
        self,
        task: BenchmarkTask,
        db_config_path: Path,
        scratch_dir: Path,
        *,
        extra_instructions: str = "",
        diversity_index: int | None = None,
    ) -> tuple[str, int, float]:
        # P3 (sandbox note): this path is in-process. The registry exposes no
        # file-read/shell tool and the submission provider (codex/GPT-5.5) has no
        # native Bash, so the agent cannot read answer keys. Only carry providers
        # WITHOUT native filesystem/shell access here; claude-mcp is the path for Claude.
        from labrat.agent.data_tools import build_data_tools_registry
        from labrat.agent.providers import build_provider
        from labrat.agent.runner import run_agent_task
        from labrat.agent.tool_trace import append_tool_trace
        from labrat.eval.benchmarks.dab.env import (
            build_dab_task_env,
            introspect_env_catalogs,
        )

        framing = _framing_for(diversity_index)
        if framing:
            extra_instructions = (extra_instructions + "\n" + framing).strip()

        env = build_dab_task_env(db_config_path)
        for conn in env.ctx.connections.values():
            connect = getattr(conn, "connect", None)
            if callable(connect):
                connect()
        # Connections aren't connect()-ed in build_dab_task_env, so the catalogs it
        # builds are empty; introspect now (post-connect) so the catalog-backed tools
        # (list_tables / describe_table / column_stats / search_columns) actually work.
        introspect_env_catalogs(env.ctx)

        cartograph_root: Path | None = None
        if self._cartograph:
            from labrat.eval.benchmarks.dab.env import build_dab_task_env as _build_fresh_env

            dataset = task.id.split(":")[0]
            cartograph_root = await _run_cartographer(
                _build_fresh_env(db_config_path),
                dataset,
                self._scent_cache_root,
                with_semantics=self._cartograph_semantics,
                llm_fn=self._cartograph_llm_fn() if self._cartograph_semantics else None,
                variant_seed=diversity_index or 0,
            )

        try:
            registry = build_data_tools_registry()
            provider = build_provider(
                self._agent_provider,
                self._agent_model,
                timeout=self._agent_timeout,
                reasoning=self._agent_reasoning,
                # Per-task cache key: the provider is rebuilt per trial, so a stable
                # per-task key lets a task's repeated trials (identical prompt prefix)
                # reuse the same warm prompt cache instead of routing to fresh
                # machines each time. Only the codex provider uses it.
                cache_key=task.id,
            )
            system_prompt = _build_labrat_agent_system_prompt(env)
            if cartograph_root is not None:
                system_prompt = system_prompt + "\n" + _cartographer_prompt_line()
            if extra_instructions:
                system_prompt = f"{system_prompt}\n\n{extra_instructions}"

            def _trace(
                tool: str,
                tool_input: dict[str, Any],
                ok: bool,
                output: str,
                latency_ms: float,
            ) -> None:
                append_tool_trace(
                    scratch_dir,
                    "agent_tool_calls.jsonl",
                    tool=tool,
                    input=tool_input,
                    ok=ok,
                    output=output,
                    latency_ms=latency_ms,
                )

            effective_timeout = (
                self._agent_timeout if self._agent_timeout is not None else _DAB_TIMEOUT
            )
            run_kwargs: dict[str, Any] = dict(
                prompt=task.prompt,
                ctx=env.ctx,
                registry=registry,
                provider=provider,
                system_prompt=system_prompt,
                max_turns=self._agent_max_turns,
                max_tool_calls=self._agent_max_tool_calls,
                verify=self._agent_verify,
                on_tool_call=_trace,
            )
            if cartograph_root is not None:
                (cartograph_root / "_home").mkdir(parents=True, exist_ok=True)
                saved = {k: os.environ.get(k) for k in ("LABRAT_MAZE_DIR", "HOME")}
                os.environ["LABRAT_MAZE_DIR"] = str(cartograph_root)
                os.environ["HOME"] = str(cartograph_root / "_home")
                try:
                    result = await asyncio.wait_for(
                        run_agent_task(**run_kwargs), timeout=effective_timeout
                    )
                finally:
                    for k, v in saved.items():
                        if v is None:
                            os.environ.pop(k, None)
                        else:
                            os.environ[k] = v
            else:
                result = await asyncio.wait_for(
                    run_agent_task(**run_kwargs), timeout=effective_timeout
                )
            # Capture per-trial token usage if the provider tracks it (codex does);
            # run_trial folds it into TrialResult.meta so trials.jsonl records real
            # tokens + cache hit rate.
            provider_usage = getattr(provider, "usage", None)
            self._last_usage = (
                cast("dict[str, int]", dict(cast("dict[str, Any]", provider_usage)))
                if isinstance(provider_usage, dict)
                else None
            )
        finally:
            for conn in env.ctx.connections.values():
                disconnect = getattr(conn, "disconnect", None)
                if callable(disconnect):
                    disconnect()
        return result.final_text, result.tool_calls, result.latency_seconds

    def aggregate(self, results: list[TrialResult]) -> AggregateScore:
        if not results:
            return AggregateScore(overall=0.0, per_task={}, n_tasks=0, n_trials=0, n_passes=0)

        # Drop infra failures so they don't depress the pass rate on queries that
        # never got a fair shot (Max-plan session limit, API credit, timeout), and
        # contaminated trials (data-leakage backstop) which are withdrawn entirely.
        _withdrawn = ("infra:", "contaminated:")
        semantic_results = [r for r in results if not (r.reason or "").startswith(_withdrawn)]
        if not semantic_results:
            return AggregateScore(overall=0.0, per_task={}, n_tasks=0, n_trials=0, n_passes=0)

        per_task: dict[str, list[bool]] = {}
        for r in semantic_results:
            per_task.setdefault(r.task_id, []).append(r.passed)
        per_task_pass_rate = {tid: sum(passes) / len(passes) for tid, passes in per_task.items()}

        by_dataset: dict[str, list[float]] = {}
        for tid, pr in per_task_pass_rate.items():
            dataset = tid.split(":", 1)[0]
            by_dataset.setdefault(dataset, []).append(pr)
        dataset_means = {ds: sum(prs) / len(prs) for ds, prs in by_dataset.items()}

        return AggregateScore(
            overall=sum(dataset_means.values()) / len(dataset_means),
            per_task=per_task_pass_rate,
            by_dimension={"dataset": dataset_means},
            n_tasks=len(per_task),
            n_trials=len(results),
            n_passes=sum(1 for r in results if r.passed),
        )

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None:
        from labrat.eval.benchmarks.dab.reporter import write_submission_json

        write_submission_json(report, output_dir)
