"""Spider2-DBT agent: ReAct loop that replaces the single-shot DSPy module.

Phases covered:
  Phase 1 — multi-turn agent loop with 7 guarded tools
  Phase 4 — mandatory planning span before SQL generation

The agent receives a task (project_dir, instruction, target_file), drives a
multi-turn loop via ClaudeCodeProvider, and returns a Spider2AgentResult.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from labrat.agent.loop import TextBlock, ToolUseBlock
from labrat.agent.providers.claude_code import ClaudeCodeProvider
from labrat.dspy_opt.spider2_context import Spider2Context, build_pre_existing
from labrat.dspy_opt.spider2_tools import Spider2ToolRegistry, default_registry

_MAX_TURNS = 40
_SYSTEM_PROMPT = """\
You are a dbt model completion agent for Spider2-DBT. Your task is to complete \
the target dbt model file so that `dbt run` succeeds and the output tables match \
the expected schema.

══ INVARIANTS — never violate these ══
1. Never run ATTACH in SQL — it breaks the DuckDB sandbox.
2. Never overwrite a pre-existing SQL model. Only write/edit stub files.
3. Never call submit without a prior run_dbt that returned returncode=0.
4. Always read the target file before writing it.
5. Always read at least one sibling SQL file before writing SQL.
6. If run_dbt fails, read the error carefully and fix only what it reports.

══ PLANNING — required before any SQL write ══
Before writing SQL for any model, output a planning block in this exact format \
(the agent loop validates it):

---plan
target_model: <relative path, e.g. models/fct_orders.sql>
source_tables_and_why:
  - <model_or_source_name>: <why it is needed>
key_joins:
  - <join condition, e.g. stg_orders.customer_id = dim_customers.customer_id>
grain: one row per <grain description>
---

If your plan's target_model does not match the task's required target file, \
you will receive an error and must revise before proceeding.

══ WORKFLOW ══
1. read_file the target file to understand what needs to be completed.
2. grep_project or read_file sibling models to learn the project's CTE style.
3. Output your ---plan ... --- block.
4. write_file_new (for new files) or edit_file (for stub files) with the SQL.
5. run_dbt to check for errors.
6. If errors: edit_file to fix, run_dbt again. Repeat until returncode=0.
7. submit with a brief description.

══ DuckDB dialect rules ══
- Date/time: current_timestamp (not get_current_timestamp()), strptime(),
  TRY_CAST(strptime(x, fmt) AS DATE) for safe date parsing.
- String agg: LIST_AGG(col, ', ') or STRING_AGG(col, ', ').
- Regex: regexp_replace(col, pattern, replacement) — no flags argument.
- Casting: CAST(x AS type) — avoid x::type shorthand.
- No ATTACH statements, ever.

══ dbt reference rules ══
- Use {{ source('schema', 'table') }} for raw source tables.
- Use {{ ref('model_name') }} for other dbt models.
- Only use macros already defined or imported in project files.
"""


@dataclass
class Spider2AgentResult:
    success: bool
    submit_description: str = ""
    turns_used: int = 0
    transcript: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    plan: Plan | None = None


@dataclass
class Plan:
    target_model: str
    source_tables: list[str]
    key_joins: list[str]
    grain: str
    raw: str


class Spider2Agent:
    """Drives a multi-turn ReAct loop for one Spider2-DBT task."""

    def __init__(
        self,
        project_dir: Path,
        instruction: str,
        target_file: str,
        db_path: Path | None = None,
        eval_tables: list[str] | None = None,
        registry: Spider2ToolRegistry | None = None,
        provider: ClaudeCodeProvider | None = None,
        max_turns: int = _MAX_TURNS,
    ) -> None:
        self._provider = provider or ClaudeCodeProvider()
        self._max_turns = max_turns
        self._registry = registry or default_registry()

        pre_existing = build_pre_existing(project_dir)
        self._ctx = Spider2Context(
            project_dir=project_dir,
            db_path=db_path,
            pre_existing_files=pre_existing,
            instruction=instruction,
            target_file=target_file,
            eval_tables=eval_tables or [],
        )
        self._plan: Plan | None = None

    async def run(self, on_text: Any = None) -> Spider2AgentResult:
        # Phase 3: capture reference snapshot before the agent touches anything
        if self._ctx.db_path and self._ctx.eval_tables:
            try:
                from labrat.dspy_opt.snapshot import capture_reference_snapshot, format_snapshot_markdown
                self._ctx.snapshot = capture_reference_snapshot(
                    self._ctx.db_path, self._ctx.eval_tables
                )
                snapshot_md = format_snapshot_markdown(self._ctx.snapshot)
            except Exception:
                snapshot_md = ""
        else:
            snapshot_md = ""

        snap_section = f"\n\n{snapshot_md}" if snapshot_md else ""
        user_msg = (
            f"Task instruction: {self._ctx.instruction}\n\n"
            f"Target file to complete: {self._ctx.target_file}{snap_section}\n\n"
            "Follow the workflow defined in the system prompt. "
            "Begin by reading the target file."
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_msg}]
        tool_schemas = self._registry.to_anthropic_schemas()
        turns = 0

        while turns < self._max_turns and not self._ctx.submitted:
            turns += 1
            text_parts: list[str] = []
            tool_uses: list[ToolUseBlock] = []

            stream = await self._provider.stream(
                messages=messages,
                tools=tool_schemas,
                system=_SYSTEM_PROMPT,
            )
            async for block in stream:
                if isinstance(block, TextBlock):
                    if on_text:
                        on_text(block.text)
                    text_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_uses.append(block)

            full_text = "".join(text_parts)

            # Phase 4: detect and validate plan blocks in text
            if not self._plan and "---plan" in full_text:
                plan_result = _parse_plan(full_text, self._ctx.target_file)
                if isinstance(plan_result, str):
                    # Validation error — inject as system note
                    tool_uses = []  # don't process tool calls until plan is valid
                    messages.append({"role": "assistant", "content": full_text})
                    messages.append({"role": "user", "content": f"Plan validation error: {plan_result}. Revise your plan before continuing."})
                    continue
                self._plan = plan_result

            # Record assistant turn
            content: list[dict[str, Any]] = []
            if full_text:
                content.append({"type": "text", "text": full_text})
            for tu in tool_uses:
                content.append({"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input})
            if content:
                messages.append({"role": "assistant", "content": content})

            if not tool_uses:
                break  # model produced final text response

            # Dispatch tools
            tool_results: list[dict[str, Any]] = []
            for tu in tool_uses:
                result_text = await self._registry.dispatch(tu.name, tu.input, self._ctx)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result_text,
                })
            messages.append({"role": "user", "content": tool_results})

            if self._ctx.submitted:
                break

        return Spider2AgentResult(
            success=self._ctx.submitted and self._ctx.last_dbt_returncode == 0,
            submit_description=self._ctx.submit_description,
            turns_used=turns,
            transcript=messages,
            plan=self._plan,
        )


# ── Phase 4: plan parsing ─────────────────────────────────────────────────────


_PLAN_RE = re.compile(r"---plan\s*\n(.*?)---", re.DOTALL)
_TARGET_RE = re.compile(r"target_model:\s*(.+)")
_GRAIN_RE = re.compile(r"grain:\s*(.+)")
_SOURCE_RE = re.compile(r"^\s+-\s+(\S+):", re.MULTILINE)
_JOIN_RE = re.compile(r"^\s+-\s+(.+=.+)", re.MULTILINE)


def _parse_plan(text: str, required_target: str) -> Plan | str:
    """Extract and validate a ---plan ... --- block.

    Returns a Plan on success, or an error string describing what's wrong.
    """
    match = _PLAN_RE.search(text)
    if not match:
        return "No ---plan ... --- block found."

    body = match.group(1)

    target_match = _TARGET_RE.search(body)
    if not target_match:
        return "Plan is missing 'target_model:' field."
    target_model = target_match.group(1).strip()

    # Normalise separators for comparison
    if _normalise_path(target_model) != _normalise_path(required_target):
        return (
            f"Plan targets '{target_model}' but the task requires '{required_target}'. "
            "Revise your plan to target the correct file."
        )

    grain_match = _GRAIN_RE.search(body)
    grain = grain_match.group(1).strip() if grain_match else ""

    sources = _SOURCE_RE.findall(body)
    joins = _JOIN_RE.findall(body)

    return Plan(
        target_model=target_model,
        source_tables=sources,
        key_joins=joins,
        grain=grain,
        raw=match.group(0),
    )


def _normalise_path(p: str) -> str:
    return p.strip().lstrip("./").replace("\\", "/")


# ── DSPy-compatible wrapper ───────────────────────────────────────────────────


class Spider2AgentModule:
    """Drop-in replacement for DBTModelCompletion that drives the full agent loop.

    Compatible with dspy.Evaluate: forward() is called synchronously,
    blocks until the agent finishes, and returns a dspy.Prediction
    carrying the output DuckDB path for the agent metric.
    """

    def __init__(
        self,
        spider2_dbt_dir: Path,
        output_base: Path,
        gold_eval: dict[str, Any] | None = None,
        provider: ClaudeCodeProvider | None = None,
        max_turns: int = _MAX_TURNS,
    ) -> None:
        self._spider2_dbt_dir = spider2_dbt_dir
        self._output_base = output_base
        self._gold_eval = gold_eval or {}
        self._provider = provider
        self._max_turns = max_turns
        output_base.mkdir(parents=True, exist_ok=True)

    def __call__(self, **kwargs: Any) -> Any:
        return self.forward(**kwargs)

    def forward(self, instance_id: str, instruction: str, target_file: str, **_: Any) -> Any:
        import asyncio
        import shutil
        import traceback as _tb

        import dspy
        import yaml

        try:
            return self._run_task(instance_id, instruction, target_file, asyncio, shutil, yaml, dspy)
        except Exception:
            _tb.print_exc()
            return dspy.Prediction(db_path=None, submitted=False, turns_used=0, submit_description="")

    def _run_task(
        self,
        instance_id: str,
        instruction: str,
        target_file: str,
        asyncio: Any,
        shutil: Any,
        yaml: Any,
        dspy: Any,
    ) -> Any:
        src = self._spider2_dbt_dir / "examples" / instance_id
        work_dir = self._output_base / instance_id

        if work_dir.exists():
            shutil.rmtree(work_dir)
        shutil.copytree(src, work_dir)

        # Resolve DuckDB path from profiles.yml (same as DBTExecutor._find_output_db)
        db_path: Path | None = None
        profiles_file = work_dir / "profiles.yml"
        if profiles_file.exists():
            try:
                profiles = yaml.safe_load(profiles_file.read_text())
                for _pname, profile in profiles.items():
                    for _tname, cfg in profile.get("outputs", {}).items():
                        if cfg.get("type") == "duckdb":
                            db_path = work_dir / cfg["path"]
                            break
            except Exception:
                pass

        # Phase 3: pass eval_tables so verifier can check after dbt run
        eval_tables: list[str] = []
        gold_entry = self._gold_eval.get(instance_id)
        if gold_entry:
            eval_tables = gold_entry.get("evaluation", {}).get("parameters", {}).get("condition_tabs", [])

        agent = Spider2Agent(
            project_dir=work_dir,
            instruction=instruction,
            target_file=target_file,
            db_path=db_path,
            eval_tables=eval_tables,
            provider=self._provider,
            max_turns=self._max_turns,
        )

        result = asyncio.run(agent.run())

        return dspy.Prediction(
            db_path=str(db_path) if (db_path and db_path.exists()) else None,
            submitted=result.success,
            turns_used=result.turns_used,
            submit_description=result.submit_description,
        )
