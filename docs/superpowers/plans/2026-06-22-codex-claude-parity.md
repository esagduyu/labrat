# Codex ⇄ Claude DAB submission-equivalence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the labrat-agent (Codex) DAB driver to submission-equivalence with claude-mcp — per-call tool traces (P1), a per-trial wall-clock timeout (P2), a documented sandbox note (P3) — plus a living parity matrix doc.

**Architecture:** A shared trace writer (`tool_trace.py`) feeds both drivers an identical JSON-line schema. `AgentLoop` gains an `on_tool_call` hook (forwarded by `run_agent_task`); the labrat-agent driver passes a collector that writes `agent_tool_calls.jsonl` into the trial scratch dir, and wraps the agent run in `asyncio.wait_for`. The MCP server's existing logger is refactored onto the shared writer (no behavior change).

**Tech Stack:** Python 3.12, Pydantic v2, pytest (`asyncio_mode = "auto"`).

## Global Constraints

- Branch: `feat/codex-claude-parity` (already created; spec committed there).
- Spec: `docs/superpowers/specs/2026-06-22-codex-claude-parity-design.md`.
- `from __future__ import annotations` at the top of every new/edited `.py`.
- Pyright **strict** on all of `src/labrat/` — no Unknown leaks (`json.loads` → annotate/cast).
- **Trace schema is exactly** `{"tool": str, "input": dict, "ok": bool, "output": str, "latency_ms": float}` — identical for both drivers (the shared writer is the only source).
- **Filenames:** claude-mcp keeps `mcp_tool_calls.jsonl`; labrat-agent writes `agent_tool_calls.jsonl`.
- **Zero behavior change when `on_tool_call=None`** (the hook is opt-in).
- **P2:** wrap the agent run in `asyncio.wait_for(..., timeout=self._agent_timeout or _DAB_TIMEOUT)`; let `TimeoutError` propagate — `run_trial`'s existing handler (`suite.py:457`) maps it to `reason="infra:timeout"`. Do NOT add a new marker string.
- Run Python via `uv run`. Full gate after every task: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- Commit messages end with these two trailer lines verbatim:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj
  ```

---

### Task 1: Shared trace writer + refactor the MCP logger onto it

**Files:**
- Create: `src/labrat/agent/tool_trace.py`
- Modify: `src/labrat/mcp/server.py` (`_log_tool_call`, lines ~52–82)
- Test: `tests/unit/test_tool_trace.py`

**Interfaces:**
- Produces: `append_tool_trace(log_dir: str | Path | None, filename: str, *, tool: str, input: dict[str, Any], ok: bool, output: str, latency_ms: float) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_tool_trace.py
"""Shared tool-call trace writer (FEATURE: Codex⇄Claude parity)."""

from __future__ import annotations

import json
from pathlib import Path

from labrat.agent.tool_trace import append_tool_trace


def test_append_writes_exact_schema(tmp_path: Path) -> None:
    append_tool_trace(
        tmp_path, "agent_tool_calls.jsonl",
        tool="run_sql", input={"sql": "SELECT 1"}, ok=True, output="ok", latency_ms=12.5,
    )
    line = (tmp_path / "agent_tool_calls.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(line) == {
        "tool": "run_sql", "input": {"sql": "SELECT 1"},
        "ok": True, "output": "ok", "latency_ms": 12.5,
    }


def test_append_is_one_line_per_call(tmp_path: Path) -> None:
    for i in range(3):
        append_tool_trace(tmp_path, "t.jsonl", tool=f"t{i}", input={}, ok=True, output="", latency_ms=0.0)
    assert len((tmp_path / "t.jsonl").read_text(encoding="utf-8").strip().splitlines()) == 3


def test_append_noop_on_falsy_log_dir() -> None:
    append_tool_trace(None, "t.jsonl", tool="t", input={}, ok=True, output="", latency_ms=0.0)  # no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_tool_trace.py -q`
Expected: FAIL — `cannot import name 'append_tool_trace'`.

- [ ] **Step 3: Create `src/labrat/agent/tool_trace.py`**

```python
"""Shared per-call tool-trace writer — the single source of the trace schema
used by BOTH DAB drivers (claude-mcp's MCP server and the labrat-agent loop),
so the submission package's traces are schema-identical across providers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TOOL_TRACE_FIELDS = ("tool", "input", "ok", "output", "latency_ms")


def append_tool_trace(
    log_dir: str | Path | None,
    filename: str,
    *,
    tool: str,
    input: dict[str, Any],
    ok: bool,
    output: str,
    latency_ms: float,
) -> None:
    """Append one JSON line ``{tool, input, ok, output, latency_ms}`` to
    ``<log_dir>/<filename>``. No-op when ``log_dir`` is falsy."""
    if not log_dir:
        return
    record = {"tool": tool, "input": input, "ok": ok, "output": output, "latency_ms": latency_ms}
    dest = Path(log_dir)
    dest.mkdir(parents=True, exist_ok=True)
    with (dest / filename).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_tool_trace.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Refactor the MCP server logger onto the shared writer**

In `src/labrat/mcp/server.py`, keep `_TOOL_LOG_FILENAME = "mcp_tool_calls.jsonl"` and the `_log_tool_call(...)` signature, but replace its body to delegate (removes the duplicated record/write logic):

```python
from labrat.agent.tool_trace import append_tool_trace  # add to imports

def _log_tool_call(
    log_dir: str | None,
    *,
    name: str,
    arguments: dict[str, Any],
    ok: bool,
    output: str,
    latency_ms: float,
) -> None:
    """Append one audit line per tool dispatch to ``<log_dir>/mcp_tool_calls.jsonl``.
    No-op when ``log_dir`` is falsy (gated on ``LABRAT_MCP_LOG_DIR``)."""
    append_tool_trace(
        log_dir, _TOOL_LOG_FILENAME,
        tool=name, input=arguments, ok=ok, output=output, latency_ms=latency_ms,
    )
```

Remove the now-unused `json`/`Path` imports in server.py ONLY if nothing else uses them (grep first; `json.dumps`/`Path` are used elsewhere in server.py — leave them).

- [ ] **Step 6: Add an MCP-logger regression test**

Append to `tests/unit/test_tool_trace.py`:

```python
from labrat.mcp.server import _TOOL_LOG_FILENAME, _log_tool_call


def test_mcp_logger_still_writes_same_schema(tmp_path: Path) -> None:
    _log_tool_call(str(tmp_path), name="link_schema", arguments={"q": "x"}, ok=True, output="{}", latency_ms=3.0)
    rec = json.loads((tmp_path / _TOOL_LOG_FILENAME).read_text(encoding="utf-8").strip())
    assert rec == {"tool": "link_schema", "input": {"q": "x"}, "ok": True, "output": "{}", "latency_ms": 3.0}
```

- [ ] **Step 7: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add src/labrat/agent/tool_trace.py src/labrat/mcp/server.py tests/unit/test_tool_trace.py
git commit -m "feat(agent): shared tool-trace writer; MCP logger delegates to it

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 2: `AgentLoop` `on_tool_call` hook + `run_agent_task` forwarding

**Files:**
- Modify: `src/labrat/agent/loop.py` (`run` signature + the dispatch block, lines ~89–95 and ~159–169)
- Modify: `src/labrat/agent/runner.py` (`run_agent_task` signature + the `loop.run` call, lines ~30–75)
- Test: `tests/unit/test_agent_loop.py` (extend — reuse its existing fake provider/registry harness)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `AgentLoop.run(..., on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None = None)`
  - `run_agent_task(..., on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None = None)` — forwarded to `loop.run`.
  - Hook is invoked once per dispatch with `(tool_name, tool_input, ok, output_str, latency_ms)` where `output_str = str(dispatch.value) if dispatch.ok else f"Error: {dispatch.error}"` (the same string already used for the tool_result content).

- [ ] **Step 1: Write the failing test**

Open `tests/unit/test_agent_loop.py`, find the existing fake provider + fake registry/tool harness it uses to drive `AgentLoop` through one tool round-trip, and add (reusing that harness):

```python
async def test_on_tool_call_fires_once_per_dispatch() -> None:
    # Build a loop whose provider emits exactly one tool_use then a final text turn,
    # using the SAME fake harness the other tests in this file use.
    calls: list[tuple[str, bool]] = []

    def on_tool_call(name, tool_input, ok, output, latency_ms):
        calls.append((name, ok))

    loop = _make_loop_with_one_tool_call()  # <- use this file's existing helper/fixtures
    await loop.run("question", on_tool_call=on_tool_call)
    assert len(calls) == 1
    assert calls[0][0] == _EXPECTED_TOOL_NAME  # the tool the fake provider requested
    assert isinstance(calls[0][1], bool)


async def test_on_tool_call_optional() -> None:
    loop = _make_loop_with_one_tool_call()
    await loop.run("question")  # on_tool_call omitted → no error, behaves as before
```

> Implementer note: this file already constructs `AgentLoop` with fake providers that yield `ToolUseBlock`/`TextBlock`. Reuse that exact construction (don't invent a new provider). Name the helper/constants to match what's already there.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_agent_loop.py -k on_tool_call -q`
Expected: FAIL — `run()` got an unexpected keyword argument `on_tool_call`.

- [ ] **Step 3: Add the hook to `AgentLoop.run`**

In `src/labrat/agent/loop.py`: ensure `import time` is present at the top (add if missing). Add the parameter to `run`:

```python
    async def run(
        self,
        user_message: str,
        *,
        on_text: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None = None,
    ) -> None:
```

Replace the dispatch block (the `dispatch = await self._registry.dispatch(...)` section) with timing + the hook:

```python
                _t0 = time.monotonic()
                dispatch = await self._registry.dispatch(tu.name, tu.input, self._ctx)
                latency_ms = (time.monotonic() - _t0) * 1000.0
                output_str = (
                    str(dispatch.value) if dispatch.ok else f"Error: {dispatch.error}"
                )
                tool_result_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": output_str,
                    }
                )
                if on_tool_call is not None:
                    on_tool_call(tu.name, tu.input, dispatch.ok, output_str, latency_ms)
                self.tool_calls_used += 1
```

- [ ] **Step 4: Forward it through `run_agent_task`**

In `src/labrat/agent/runner.py`, add the param and pass it to `loop.run`:

```python
async def run_agent_task(
    *,
    prompt: str,
    ctx: ToolContext,
    registry: ToolRegistry,
    provider: ModelProvider,
    system_prompt: str,
    max_turns: int | None = None,
    max_tool_calls: int | None = None,
    verify: bool = False,
    max_verify_rounds: int = 2,
    on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None = None,
) -> AgentTaskResult:
```
(add `from collections.abc import Callable` and `from typing import Any` imports if not present) and change the run call:
```python
    await loop.run(prompt, on_text=on_text, on_tool_call=on_tool_call)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_agent_loop.py -k on_tool_call -q`
Expected: PASS (2 tests).

- [ ] **Step 6: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add src/labrat/agent/loop.py src/labrat/agent/runner.py tests/unit/test_agent_loop.py
git commit -m "feat(agent): AgentLoop on_tool_call hook, forwarded by run_agent_task

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 3: labrat-agent driver — write traces (P1) + per-trial timeout (P2) + sandbox note (P3)

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` (`run_trial` call site ~441–444; `_run_trial_labrat_agent` ~806–899)
- Test: `tests/unit/test_dab_cartographer.py` (extend — timeout classification)

**Interfaces:**
- Consumes: `append_tool_trace` (Task 1); `run_agent_task(..., on_tool_call=...)` (Task 2).
- Produces: `_run_trial_labrat_agent(self, task, db_config_path, scratch_dir: Path)` writes `<scratch_dir>/agent_tool_calls.jsonl`; a stalled trial raises `TimeoutError` → `run_trial` records `reason="infra:timeout"`.

- [ ] **Step 1: Write the failing test (timeout → infra:timeout)**

Append to `tests/unit/test_dab_cartographer.py`:

```python
import asyncio

import pytest

from labrat.eval.benchmarks.dab.suite import DabSuite


async def test_labrat_agent_timeout_is_classified_infra(tmp_path, monkeypatch) -> None:
    # run_trial must turn a TimeoutError from the driver into reason="infra:timeout"
    suite = DabSuite(driver="labrat-agent")

    async def _boom(*a, **k):
        raise TimeoutError("simulated stall")

    monkeypatch.setattr(suite, "_run_trial_labrat_agent", _boom)
    task = next(iter(suite.tasks()))  # any task; the driver is stubbed so no real run
    res = await suite.run_trial(task, 0, tmp_path / "scratch")
    assert res.reason == "infra:timeout"
    assert res.passed is False
```

(If `DabSuite(driver="labrat-agent")` requires more constructor args, mirror the construction used elsewhere in this test file / `test_dab_*` tests.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_cartographer.py -k timeout_is_classified_infra -q`
Expected: FAIL — current `run_trial` calls `_run_trial_labrat_agent(task, db_config_path)` (2 args); the stub signature/flow differs, or the reason isn't `infra:timeout` yet. (If it already passes because the existing `except` handler catches it, proceed — the test then pins the behavior we rely on for P2.)

- [ ] **Step 3: Thread `scratch_dir` into the driver call**

In `run_trial` (`suite.py:441–444`), pass `scratch_dir`:

```python
            if self._driver == "labrat-agent":
                final_text, tool_calls, latency = await self._run_trial_labrat_agent(
                    task, db_config_path, scratch_dir
                )
```

- [ ] **Step 4: Update `_run_trial_labrat_agent` — signature, trace collector, timeout, P3 note**

Change the signature and add `import asyncio` at the top of the method's imports block:

```python
    async def _run_trial_labrat_agent(
        self, task: BenchmarkTask, db_config_path: Path, scratch_dir: Path
    ) -> tuple[str, int, float]:
        # P3 (sandbox note): this path is in-process. The registry exposes no
        # file-read/shell tool and the submission provider (codex/GPT-5.5) has no
        # native Bash, so the agent cannot read answer keys. Only carry providers
        # WITHOUT native filesystem/shell access here; claude-mcp is the path for Claude.
        import asyncio
        from labrat.agent.data_tools import build_data_tools_registry
        from labrat.agent.providers import build_provider
        from labrat.agent.runner import run_agent_task
        from labrat.agent.tool_trace import append_tool_trace
        from labrat.eval.benchmarks.dab.env import (
            build_dab_task_env,
            introspect_env_catalogs,
        )
```

Define the collector after `registry = build_data_tools_registry()` and before the run:

```python
            def _trace(tool: str, tool_input: dict[str, Any], ok: bool, output: str, latency_ms: float) -> None:
                append_tool_trace(
                    scratch_dir, "agent_tool_calls.jsonl",
                    tool=tool, input=tool_input, ok=ok, output=output, latency_ms=latency_ms,
                )

            effective_timeout = self._agent_timeout if self._agent_timeout is not None else _DAB_TIMEOUT
```

Unify the two `run_agent_task` call sites into one `asyncio.wait_for`-wrapped call, preserving the conditional env mutation. Replace the whole `if cartograph_root is not None: ... else: ...` run block with:

```python
            run_kwargs = dict(
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
                    result = await asyncio.wait_for(run_agent_task(**run_kwargs), timeout=effective_timeout)
                finally:
                    for k, v in saved.items():
                        if v is None:
                            os.environ.pop(k, None)
                        else:
                            os.environ[k] = v
            else:
                result = await asyncio.wait_for(run_agent_task(**run_kwargs), timeout=effective_timeout)
```

Do NOT catch `TimeoutError` here — let it propagate. The connection-disconnect `finally` (existing, ~895) still runs, and `run_trial`'s `except Exception` handler (`suite.py:457`) maps `TimeoutError` → `reason="infra:timeout"`.

> Note: `run_kwargs` is a plain dict passed via `**`; under pyright strict you may need `run_kwargs: dict[str, Any] = {...}`. Use an explicit `dict[str, Any]` annotation to avoid an Unknown leak.

- [ ] **Step 5: Run the timeout test + a quick trace sanity check**

Run: `uv run pytest tests/unit/test_dab_cartographer.py -k "timeout_is_classified_infra or cartograph" -q`
Expected: PASS.

- [ ] **Step 6: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add src/labrat/eval/benchmarks/dab/suite.py tests/unit/test_dab_cartographer.py
git commit -m "feat(dab): labrat-agent writes per-call traces + per-trial wall-clock timeout

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 4: The parity matrix deliverable — `docs/dab-driver-parity.md`

**Files:**
- Create: `docs/dab-driver-parity.md`

**Interfaces:** none (documentation deliverable — the artifact that proves submission-equivalence).

- [ ] **Step 1: Write `docs/dab-driver-parity.md`**

Capture the full feature-by-feature matrix and the resolution of each gap. Use this content:

```markdown
# DAB driver parity: claude-mcp (Claude) ⇄ labrat-agent (Codex/GPT-5.5)

Proof that a submission produced via the labrat-agent driver (Codex) is as complete and valid as one
via claude-mcp (Claude). Both drivers funnel through the same `run_trial` seam (scoring, contamination
backstop, infra classification, resume) and the same `build_data_tools_registry()` tool set.

## Parity matrix

| # | Feature | claude-mcp | labrat-agent | Status |
|---|---------|------------|--------------|--------|
| 1 | Tool set (15 tools) | via MCP server | same registry, in-process | ✅ parity |
| 2 | Per-call tool traces | `mcp_tool_calls.jsonl` | `agent_tool_calls.jsonl` (shared writer) | ✅ closed (P1) |
| 3 | Cartographer pre-pass | `--agent-cartograph` | same | ✅ parity |
| 4 | Prompt levers + Scent-first line | yes | yes | ✅ parity |
| 5 | Sandbox / isolation | subprocess, tool allowlist | in-process; no file/shell tool + no native Bash on Codex | ✅ documented (P3) |
| 6 | Contamination backstop | `_detect_contamination` | same (shared `run_trial`) | ✅ parity |
| 7 | Per-trial metadata | latency, tool count | latency, tool count, **+ token usage** | ✅ parity (richer) |
| 8 | Per-trial wall-clock timeout | subprocess timeout | `asyncio.wait_for` → `infra:timeout` | ✅ closed (P2) |
| 9 | Answer extraction + scoring | shared `score_with_validator` | same | ✅ parity |
| 10 | Resume + infra classification | shared | shared | ✅ parity |
| 11 | Submission artifacts | config/trials/submission/report + traces | same + traces | ✅ parity |

## Gaps and resolution

- **P1 — per-call traces (was submission-blocking):** shared `append_tool_trace` writer + an
  `AgentLoop.on_tool_call` hook; the labrat-agent driver writes `<scratch>/agent_tool_calls.jsonl`
  with the identical `{tool, input, ok, output, latency_ms}` schema. Packagers glob `*tool_calls.jsonl`.
- **P2 — per-trial wall-clock timeout (operational):** the agent run is wrapped in
  `asyncio.wait_for(timeout=agent_timeout or 1200s)`; the resulting `TimeoutError` is mapped to
  `reason="infra:timeout"` by the shared handler (same outcome as claude-mcp's subprocess timeout).
- **P3 — sandbox asymmetry (documented):** the labrat-agent path is in-process and must only carry
  providers without native filesystem/shell access (Codex/GPT-5.5 qualifies). A hard in-process jail
  is unnecessary for the submission path; claude-mcp remains the path for Claude.
- **P4 — tool-call count (no action):** claude-mcp approximates from `num_turns`; labrat-agent reports
  the exact dispatch count — labrat-agent is more accurate. Not part of the submission format.

## Conclusion

With P1 and P2 closed and P3 documented, the labrat-agent/Codex path is **submission-equivalent** to
claude-mcp: identical tool set, identical trace schema, identical scoring/sandbox/resume semantics, and
a full trace package per trial.
```

- [ ] **Step 2: Commit**

```bash
git add docs/dab-driver-parity.md
git commit -m "docs(dab): driver parity matrix — Codex⇄Claude submission-equivalence

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

## Self-Review

**1. Spec coverage:**
- §3 shared writer → Task 1. ✅
- §4 P1 (hook + forward + scratch_dir + collector + `agent_tool_calls.jsonl`) → Tasks 2 + 3. ✅
- §5 P2 (timeout) → Task 3 (via `asyncio.wait_for`; the plan's Global Constraints note documents the simplification vs the spec's marker-string wording — same `infra:timeout` outcome). ✅
- §6 P3 (sandbox note) → Task 3 comment + Task 4 doc row. ✅
- §7 parity matrix doc → Task 4. ✅
- §8 testing (writer schema, MCP regression, hook fires/optional, forwarding, timeout classification) → Tasks 1–3. ✅
- §9 decisions (shared writer, filenames, live hook, P2 reuse, P3 doc-only, P4 no-action) → reflected. ✅

**2. Placeholder scan:** No TBD/TODO. Two reuse-the-existing-harness instructions (Task 2's loop fakes; Task 3's `DabSuite` construction) point to concrete existing files (`tests/unit/test_agent_loop.py`, `test_dab_*`) — not placeholders, but the implementer must read those harnesses to match names.

**3. Type consistency:** `append_tool_trace(log_dir, filename, *, tool, input, ok, output, latency_ms)` identical Tasks 1→3. `on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None` identical Tasks 2→3. `_run_trial_labrat_agent(self, task, db_config_path, scratch_dir)` — 3-arg call (Task 3 Step 3) matches the 3-arg signature (Step 4). Output string formula matches the existing tool_result content (`str(dispatch.value) if dispatch.ok else f"Error: {dispatch.error}"`).
