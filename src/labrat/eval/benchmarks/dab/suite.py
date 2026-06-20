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
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from labrat.eval.benchmarks.dab.env import DabTaskEnv
from labrat.eval.types import (
    AggregateScore,
    BenchmarkReport,
    BenchmarkTask,
    TrialResult,
)

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
    ("Credit balance is too low", "no_api_credit"),
    ("[trial exceeded ", "timeout"),
)


def _detect_infra_failure(final_text: str) -> str | None:
    """Return an infra reason tag (e.g. 'session_limit') if the trial output looks
    like an infrastructure failure rather than a real attempt; otherwise None."""
    for needle, tag in _INFRA_PATTERNS:
        if needle in final_text:
            return tag
    return None


# Substrings that mark a trial as contaminated by data leakage — the agent read
# the benchmark's answer key (validate.py / ground_truth.csv) off disk, or pulled
# external labels (HuggingFace `load_dataset`). With the claude-mcp driver properly
# sandboxed (MCP-only --allowedTools, isolated cwd) this is structurally impossible,
# so this is a loud backstop: any hit means the sandbox regressed. Contaminated
# trials are withdrawn from aggregate scoring (see aggregate()), never counted as
# a pass. Tags are checked in order; answer-key access is the more severe signal.
_CONTAMINATION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("validate.py", "answer_key"),
    ("ground_truth", "answer_key"),
    # Natural-language gold-answer assertions. The DAB maintainers (PR #54) caught
    # three leaks our filename-only scan missed: the access happened inside a Task
    # subagent (whose internal calls aren't in the transcript) and only its English
    # summary survives — e.g. "confirmed from the ground truth file", "matches the
    # ground truth answer 2020". In a DAB analysis trace these phrases only appear
    # when the agent reached the answer key, so they're high-signal markers.
    ("ground truth", "answer_key"),
    ("ground-truth", "answer_key"),
    ("answer key", "answer_key"),
    ("gold answer", "answer_key"),
    ("load_dataset", "external_dataset"),
    ("huggingface", "external_dataset"),
    ("fancyzhx/ag_news", "external_dataset"),
)


def _detect_contamination(text: str) -> str | None:
    """Return a contamination tag ('answer_key' / 'external_dataset') if the trace
    text shows the agent reached the answer key or an external labelled dataset;
    otherwise None. Case-insensitive."""
    low = text.lower()
    for needle, tag in _CONTAMINATION_PATTERNS:
        if needle in low:
            return tag
    return None


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

            desc_file = dataset_dir / (
                "db_description_withhint.txt" if self._hints else "db_description.txt"
            )
            description = desc_file.read_text().strip() if desc_file.exists() else ""

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

        try:
            if self._driver == "labrat-agent":
                final_text, tool_calls, latency = await self._run_trial_labrat_agent(
                    task, db_config_path
                )
            elif self._driver == "claude-mcp":
                final_text, tool_calls, latency = await self._run_trial_claude_mcp(
                    task, db_config_path, scratch_dir
                )
            else:
                final_text, tool_calls, latency = await self._run_trial_raw_bash(
                    task, db_config_path
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
        )

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
        self, task: BenchmarkTask, db_config_path: Path, scratch_dir: Path
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
                    },
                }
            }
        }
        mcp_config_path = scratch_dir / "mcp-config.json"
        mcp_config_path.write_text(json.dumps(mcp_config))

        prompt_lines = [
            "You have a labrat MCP server connected. It exposes data tools "
            "(link_schema, list_tables, describe_table, sample_rows, run_sql, "
            "verify_join, attach_database, load_mongo_collection, …) against "
            f"the primary DuckDB database '{primary_name}'.",
            "On a wide/unfamiliar schema call link_schema(question) first to find the "
            "relevant tables; before any multi-table JOIN call verify_join to confirm the "
            "keys match and won't fan out.",
        ]
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
        if self._agent_max_tool_calls is not None:
            prompt_lines.append(
                f"\nBudget: at most {self._agent_max_tool_calls} tool calls. Plan accordingly."
            )
        prompt = "\n".join(prompt_lines)

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
        self, task: BenchmarkTask, db_config_path: Path
    ) -> tuple[str, int, float]:
        from labrat.agent.data_tools import build_data_tools_registry
        from labrat.agent.providers import build_provider
        from labrat.agent.runner import run_agent_task
        from labrat.eval.benchmarks.dab.env import (
            build_dab_task_env,
            introspect_env_catalogs,
        )

        env = build_dab_task_env(db_config_path)
        for conn in env.ctx.connections.values():
            connect = getattr(conn, "connect", None)
            if callable(connect):
                connect()
        # Connections aren't connect()-ed in build_dab_task_env, so the catalogs it
        # builds are empty; introspect now (post-connect) so the catalog-backed tools
        # (list_tables / describe_table / column_stats / search_columns) actually work.
        introspect_env_catalogs(env.ctx)
        try:
            registry = build_data_tools_registry()
            provider = build_provider(
                self._agent_provider, self._agent_model, timeout=self._agent_timeout
            )
            system_prompt = _build_labrat_agent_system_prompt(env)
            result = await run_agent_task(
                prompt=task.prompt,
                ctx=env.ctx,
                registry=registry,
                provider=provider,
                system_prompt=system_prompt,
                max_turns=self._agent_max_turns,
                max_tool_calls=self._agent_max_tool_calls,
                verify=self._agent_verify,
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
