"""Spider2-DBT agent tools: read_file, write_file_new, edit_file, run_dbt, run_sql, grep_project, submit.

Each tool follows the same interface as labrat.agent.tools.base.Tool but uses
Spider2Context instead of ToolContext.  They are registered in Spider2ToolRegistry
and dispatched by Spider2AgentLoop.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel

from labrat.dspy_opt.spider2_context import Spider2Context

_MAX_FILE_CHARS = 20_000
_MAX_GREP_MATCHES = 200
_MAX_DBT_LOG_CHARS = 3_000
_MAX_SQL_ROWS = 20


# ── base ──────────────────────────────────────────────────────────────────────


class Spider2Tool(ABC):
    """Abstract base for Spider2-specific agent tools."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def input_model(self) -> type[BaseModel]: ...

    @abstractmethod
    async def execute(self, ctx: Spider2Context, args: BaseModel) -> str: ...

    def anthropic_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }


class Spider2ToolRegistry:
    def __init__(self, tools: list[Spider2Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def to_anthropic_schemas(self) -> list[dict[str, Any]]:
        return [t.anthropic_schema() for t in self._tools.values()]

    async def dispatch(self, name: str, args: dict[str, Any], ctx: Spider2Context) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'. Available: {list(self._tools)}"
        try:
            parsed = tool.input_model.model_validate(args)
            return await tool.execute(ctx, parsed)
        except Exception as exc:
            return f"Error: {exc}"


# ── 1. read_file ──────────────────────────────────────────────────────────────


class _ReadFileInput(BaseModel):
    path: str  # relative to project_dir


class ReadFileTool(Spider2Tool):
    """Read a file from the dbt project directory (truncated at 20 000 chars)."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file in the dbt project. "
            "path is relative to the project root. "
            "Returns the file contents (truncated at 20 000 characters)."
        )

    @property
    def input_model(self) -> type[_ReadFileInput]:
        return _ReadFileInput

    async def execute(self, ctx: Spider2Context, args: _ReadFileInput) -> str:
        target = (ctx.project_dir / args.path).resolve()
        # Prevent path traversal outside project_dir
        try:
            target.relative_to(ctx.project_dir.resolve())
        except ValueError:
            return f"Error: path '{args.path}' is outside the project directory."
        if not target.exists():
            return f"Error: file '{args.path}' does not exist."
        if not target.is_file():
            return f"Error: '{args.path}' is not a file."
        text = target.read_text(errors="replace")
        if len(text) > _MAX_FILE_CHARS:
            return text[:_MAX_FILE_CHARS] + f"\n\n[... truncated at {_MAX_FILE_CHARS} chars ...]"
        return text


# ── 2. write_file_new ─────────────────────────────────────────────────────────


class _WriteFileNewInput(BaseModel):
    path: str
    content: str


class WriteFileNewTool(Spider2Tool):
    """Write content to a NEW file. Refuses to overwrite pre-existing SQL models."""

    @property
    def name(self) -> str:
        return "write_file_new"

    @property
    def description(self) -> str:
        return (
            "Write content to a file in the dbt project. "
            "Will REFUSE if the file is a pre-existing SQL model (use edit_file instead). "
            "path is relative to the project root."
        )

    @property
    def input_model(self) -> type[_WriteFileNewInput]:
        return _WriteFileNewInput

    async def execute(self, ctx: Spider2Context, args: _WriteFileNewInput) -> str:
        target = (ctx.project_dir / args.path).resolve()
        try:
            target.relative_to(ctx.project_dir.resolve())
        except ValueError:
            return f"Error: path '{args.path}' is outside the project directory."

        rel = str(Path(args.path))
        if rel in ctx.pre_existing_files:
            return (
                f"Error: '{args.path}' is a pre-existing SQL model and cannot be overwritten. "
                "Use edit_file to modify it instead."
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args.content)
        ctx.dbt_dirty = True
        return f"Written {len(args.content)} chars to '{args.path}'."


# ── 3. edit_file ──────────────────────────────────────────────────────────────


class _EditFileInput(BaseModel):
    path: str
    old_str: str
    new_str: str


class EditFileTool(Spider2Tool):
    """Replace old_str with new_str in an existing file (exact string match)."""

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Replace old_str with new_str in an existing file. "
            "old_str must match exactly (including whitespace). "
            "Returns an error if old_str is not found or is ambiguous."
        )

    @property
    def input_model(self) -> type[_EditFileInput]:
        return _EditFileInput

    async def execute(self, ctx: Spider2Context, args: _EditFileInput) -> str:
        target = (ctx.project_dir / args.path).resolve()
        try:
            target.relative_to(ctx.project_dir.resolve())
        except ValueError:
            return f"Error: path '{args.path}' is outside the project directory."
        if not target.exists():
            return f"Error: file '{args.path}' does not exist."

        original = target.read_text(errors="replace")
        count = original.count(args.old_str)
        if count == 0:
            return f"Error: old_str not found in '{args.path}'. Check the exact text."
        if count > 1:
            return (
                f"Error: old_str appears {count} times in '{args.path}'. "
                "Provide more context to make it unique."
            )

        updated = original.replace(args.old_str, args.new_str, 1)
        target.write_text(updated)
        ctx.dbt_dirty = True
        return f"Replaced 1 occurrence in '{args.path}'."


# ── 4. run_dbt ────────────────────────────────────────────────────────────────


class _RunDbtInput(BaseModel):
    select: str | None = None  # dbt --select expression; None = run all


class RunDbtTool(Spider2Tool):
    """Run `dbt run` in the project directory. Returns returncode and log tail."""

    @property
    def name(self) -> str:
        return "run_dbt"

    @property
    def description(self) -> str:
        return (
            "Run `dbt run` in the project directory. "
            "Optionally pass a `select` expression (e.g. 'my_model') to run only that model. "
            "Returns the exit code and last portion of dbt output. "
            "returncode=0 means success. Only call submit after returncode=0."
        )

    @property
    def input_model(self) -> type[_RunDbtInput]:
        return _RunDbtInput

    async def execute(self, ctx: Spider2Context, args: _RunDbtInput) -> str:
        if not ctx.dbt_dirty:
            return "Skipped (no files changed since last successful run). returncode=0."

        dbt = _find_dbt()
        cmd = [dbt, "run", "--project-dir", str(ctx.project_dir), "--profiles-dir", str(ctx.project_dir)]
        if args.select:
            cmd += ["--select", args.select]

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
                cwd=ctx.project_dir,
            )
        except subprocess.TimeoutExpired:
            return "Error: dbt run timed out after 180s."

        ctx.last_dbt_returncode = result.returncode
        if result.returncode == 0:
            ctx.dbt_dirty = False

        combined = (result.stdout + result.stderr).strip()
        tail = combined[-_MAX_DBT_LOG_CHARS:] if len(combined) > _MAX_DBT_LOG_CHARS else combined
        output = f"returncode={result.returncode}\n\n{tail}"

        # Phase 3: run deterministic verifier after a successful build
        if result.returncode == 0 and ctx.eval_tables and ctx.db_path:
            try:
                from labrat.dspy_opt.verifier import verify_build
                verify_result = verify_build(ctx.db_path, ctx.eval_tables, ctx.snapshot)
                output += f"\n\n--- Verifier Report ---\n{verify_result.format_report()}"
            except Exception as exc:
                output += f"\n\n[Verifier error: {exc}]"

        return output


def _find_dbt() -> str:
    venv_dbt = Path(sys.executable).parent / "dbt"
    if venv_dbt.exists():
        return str(venv_dbt)
    import shutil
    found = shutil.which("dbt")
    if found:
        return found
    raise RuntimeError("dbt not found — install with: uv add dbt-duckdb")


# ── 5. run_sql ────────────────────────────────────────────────────────────────


class _RunSqlInput(BaseModel):
    sql: str
    limit: int = 10


class RunSqlTool(Spider2Tool):
    """Execute a SQL query against the task's DuckDB and return schema + rows."""

    @property
    def name(self) -> str:
        return "run_sql"

    @property
    def description(self) -> str:
        return (
            "Execute a SQL query against the task's DuckDB database. "
            "Returns column names, types, and up to `limit` rows (default 10). "
            "Do NOT use ATTACH statements — they will fail in the sandbox."
        )

    @property
    def input_model(self) -> type[_RunSqlInput]:
        return _RunSqlInput

    async def execute(self, ctx: Spider2Context, args: _RunSqlInput) -> str:
        if "attach" in args.sql.lower():
            return "Error: ATTACH statements are not allowed in the sandbox."

        db = ctx.db_path
        if db is None or not db.exists():
            return "Error: DuckDB file not found. Run dbt first to build the models."

        try:
            result = await asyncio.to_thread(_exec_sql, str(db), args.sql, args.limit)
            return result
        except Exception as exc:
            return f"SQL error: {exc}"


def _exec_sql(db_path: str, sql: str, limit: int) -> str:
    conn = duckdb.connect(db_path, read_only=True)
    try:
        rel = conn.execute(sql)
        columns = [d[0] for d in rel.description]
        types = [d[1] for d in rel.description]
        rows = rel.fetchmany(limit)
        header = " | ".join(f"{c} ({t})" for c, t in zip(columns, types))
        divider = "-" * len(header)
        lines = [header, divider]
        for row in rows:
            lines.append(" | ".join(str(v) for v in row))
        if not rows:
            lines.append("(no rows)")
        return "\n".join(lines)
    finally:
        conn.close()


# ── 6. grep_project ───────────────────────────────────────────────────────────


class _GrepProjectInput(BaseModel):
    pattern: str  # regex pattern
    file_glob: str = "**/*"


class GrepProjectTool(Spider2Tool):
    """Search the project files for a regex pattern (capped at 200 matches)."""

    @property
    def name(self) -> str:
        return "grep_project"

    @property
    def description(self) -> str:
        return (
            "Search all files in the dbt project for a regex pattern. "
            "file_glob filters which files to search (default: all files). "
            "Returns up to 200 matches as 'path:line: text'."
        )

    @property
    def input_model(self) -> type[_GrepProjectInput]:
        return _GrepProjectInput

    async def execute(self, ctx: Spider2Context, args: _GrepProjectInput) -> str:
        try:
            pattern = re.compile(args.pattern, re.IGNORECASE)
        except re.error as exc:
            return f"Error: invalid regex pattern: {exc}"

        matches: list[str] = []
        for path in sorted(ctx.project_dir.glob(args.file_glob)):
            if not path.is_file():
                continue
            try:
                for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                    if pattern.search(line):
                        rel = path.relative_to(ctx.project_dir)
                        matches.append(f"{rel}:{lineno}: {line.rstrip()}")
                        if len(matches) >= _MAX_GREP_MATCHES:
                            matches.append(f"[... truncated at {_MAX_GREP_MATCHES} matches ...]")
                            return "\n".join(matches)
            except OSError:
                continue

        return "\n".join(matches) if matches else "No matches found."


# ── 7. submit ─────────────────────────────────────────────────────────────────


class _SubmitInput(BaseModel):
    description: str


class SubmitTool(Spider2Tool):
    """Mark the task complete. Only call after run_dbt returns returncode=0."""

    @property
    def name(self) -> str:
        return "submit"

    @property
    def description(self) -> str:
        return (
            "Mark the task as complete and stop the agent. "
            "Only call this after run_dbt has returned returncode=0. "
            "description: brief summary of what was built and how."
        )

    @property
    def input_model(self) -> type[_SubmitInput]:
        return _SubmitInput

    async def execute(self, ctx: Spider2Context, args: _SubmitInput) -> str:
        if ctx.last_dbt_returncode != 0:
            return (
                "Error: cannot submit — last run_dbt did not return returncode=0. "
                "Fix all dbt errors first."
            )
        ctx.submitted = True
        ctx.submit_description = args.description
        return f"Task submitted. Description: {args.description}"


# ── factory ───────────────────────────────────────────────────────────────────


def default_registry() -> Spider2ToolRegistry:
    return Spider2ToolRegistry([
        ReadFileTool(),
        WriteFileNewTool(),
        EditFileTool(),
        RunDbtTool(),
        RunSqlTool(),
        GrepProjectTool(),
        SubmitTool(),
    ])
