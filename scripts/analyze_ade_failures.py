#!/usr/bin/env python3
"""Analyze ADE-bench experiment failures from cast + results files.

Usage:
    uv run scripts/analyze_ade_failures.py ~/repos/ade-bench/experiments/2026-05-24__23-15-04__none

Reads all trials from a run directory, filters to failures, and prints:
- task_id, turns, cost, which tests failed
- The dbt commands the agent ran (extracted from cast file)
- Any ERROR or FAIL lines seen in the cast output
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def _read_cast_commands(cast_path: Path) -> list[str]:
    """Extract bash commands run by the agent from an asciinema cast file."""
    commands: list[str] = []
    try:
        lines = cast_path.read_text(errors="replace").splitlines()
    except OSError:
        return commands

    output_chunks: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, list) or len(event) < 3:
            continue
        if event[1] == "o":
            output_chunks.append(event[2])

    full_output = _strip_ansi("".join(output_chunks))

    # Heuristic: extract lines that look like shell prompts followed by a command
    # The pattern is: "root@<hash>:/app# <command>"
    prompt_re = re.compile(r"root@[0-9a-f]+:/app#\s+(.+)")
    for line in full_output.splitlines():
        m = prompt_re.match(line.strip())
        if m:
            cmd = m.group(1).strip()
            if cmd and not cmd.startswith("echo"):
                commands.append(cmd)
    return commands


def _find_error_lines(cast_path: Path) -> list[str]:
    """Extract ERROR/FAIL lines from cast output."""
    error_lines: list[str] = []
    try:
        lines = cast_path.read_text(errors="replace").splitlines()
    except OSError:
        return error_lines

    output_chunks: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, list) and len(event) >= 3 and event[1] == "o":
            output_chunks.append(event[2])

    full_output = _strip_ansi("".join(output_chunks))
    for line in full_output.splitlines():
        stripped = line.strip()
        if re.search(r"\b(ERROR|FAIL|Error:|Exception:)\b", stripped) and len(stripped) < 300:
            error_lines.append(stripped)
    return error_lines[:10]  # Cap at 10 lines per task


def analyze_run(run_dir: Path) -> None:
    results_file = run_dir / "results.json"
    if not results_file.exists():
        sys.exit(f"No results.json in {run_dir}")

    data = json.loads(results_file.read_text())
    all_results = data.get("results", [])

    failures = [r for r in all_results if not r.get("is_resolved")]
    passes = [r for r in all_results if r.get("is_resolved")]

    print(f"Run: {run_dir.name}")
    print(f"Total: {len(all_results)}  Passed: {len(passes)}  Failed: {len(failures)}")
    print(f"Pass rate: {len(passes)/len(all_results)*100:.1f}%\n")
    print("=" * 70)

    for result in sorted(failures, key=lambda r: r["task_id"]):
        task_id = result["task_id"]
        turns = result.get("num_turns", "?")
        cost = result.get("cost_usd", 0.0)
        parser = result.get("parser_results", {})
        failed_tests = [k for k, v in parser.items() if v == "failed"]

        print(f"\n{'─'*60}")
        print(f"FAIL  {task_id}  ({turns} turns, ${cost:.3f})")
        print(f"      Failed tests: {', '.join(failed_tests) or 'none'}")

        # Find cast file
        cast_path: Path | None = None
        recording = result.get("recording_path", "")
        if recording:
            # recording_path is relative to the experiments/ parent
            experiments_dir = run_dir.parent
            candidate = experiments_dir / recording
            if candidate.exists():
                cast_path = candidate

        if cast_path:
            commands = _read_cast_commands(cast_path)
            dbt_cmds = [c for c in commands if "dbt" in c]
            print(f"      dbt commands ({len(dbt_cmds)}):")
            for cmd in dbt_cmds[:8]:
                print(f"        $ {cmd}")
            if len(dbt_cmds) > 8:
                print(f"        ... ({len(dbt_cmds) - 8} more)")

            errors = _find_error_lines(cast_path)
            if errors:
                print(f"      Error lines:")
                for e in errors:
                    print(f"        {e[:120]}")
        else:
            print(f"      (no cast file found at {recording})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    analyze_run(Path(sys.argv[1]).expanduser())
