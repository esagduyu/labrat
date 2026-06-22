# Codex ⇄ Claude DAB submission-equivalence — driver parity

> **Status:** Design approved 2026-06-22. Bring the **labrat-agent** DAB driver (Codex/GPT‑5.5, any
> provider) to submission-equivalence with the **claude-mcp** driver (Claude/Sonnet), proven by a living
> feature-by-feature parity matrix. Motivated by: DAB maintainers require **full per-call tool traces**
> in the submission package (the anti-contamination audit mechanism from PR #54), and the labrat-agent
> path currently produces none.
>
> **Branch:** `feat/codex-claude-parity`. **Process:** superpowers — this spec → `writing-plans` → TDD →
> review → finish. Full gate every commit: `ruff format` → `ruff check` → `pyright` → `pytest`.
> **Sequencing:** build on the branch; **merge after the in-flight codex ablation (`runs/dab/codex-cartograph-*`) finishes** — all changes are additive (no scoring impact), but this keeps the running trial clean.

## 1. Why

A 15-feature parity audit of the two DAB drivers found **11 features already at full parity** (tool set,
Cartographer pre-pass, prompt levers, `_detect_contamination` backstop, scoring, resume, infra
classification; labrat-agent is even *richer* on token metadata). **Three gaps remain:**

- **P1 (submission-blocking):** the labrat-agent path persists **no per-call tool traces**. claude-mcp
  writes `mcp_tool_calls.jsonl` per dispatch; labrat-agent keeps only an int count and discards
  `AgentLoop.history`. The trial `scratch_dir` is not even passed into `_run_trial_labrat_agent`. The DAB
  maintainers audit traces (PR #54 anti-contamination); a Codex submission without them is likely rejected.
- **P2 (operational):** the labrat-agent path has **no per-trial wall-clock timeout**. `run_agent_task`
  is awaited with no `asyncio.wait_for`; the Codex provider's 600s timeout covers only one HTTP call, not
  the whole trial. A stalled trial blocks the entire sequential run (a real risk for the multi-hour full run).
- **P3 (soft / document-only):** no subprocess-level filesystem isolation. In-process, the registry has no
  file-read/shell tool and Codex/GPT‑5.5 has no native Bash, so the agent *cannot* read `validate.py` /
  `ground_truth.csv` — but it is a guarantee-by-absence, not a positive barrier.

The deliverable that *is* the goal: a documented parity matrix proving Codex and Claude submissions are
equivalent.

## 2. Scope

**In scope:** shared trace writer; P1 (traces) + P2 (timeout) closed in code; P3 documented; the parity
matrix doc.

**Out of scope:** P4 (claude-mcp's `num_turns`-derived tool-call count is an approximation while
labrat-agent's is exact — labrat-agent is *more* accurate; documented, no change). No new tools, no scoring
changes, no change to the claude-mcp path beyond routing its existing logger through the shared writer.

## 3. Shared trace writer (DRY — schema can't drift between drivers)

New `src/labrat/agent/tool_trace.py`:

```python
TOOL_TRACE_FIELDS = ("tool", "input", "ok", "output", "latency_ms")

def append_tool_trace(
    log_dir: str | Path | None, *, tool: str, input: dict[str, Any],
    ok: bool, output: str, latency_ms: float,
) -> None:
    """Append one JSON line {tool, input, ok, output, latency_ms} to <log_dir>/<filename>.
    No-op when log_dir is falsy. The single source of the trace schema for BOTH drivers."""
```

(Writer takes the destination filename via a small wrapper or param so claude-mcp keeps
`mcp_tool_calls.jsonl` and labrat-agent uses `agent_tool_calls.jsonl`.) The MCP server's existing
`_log_tool_call` (`src/labrat/mcp/server.py:55`) is refactored into a thin call to `append_tool_trace`
(same `mcp_tool_calls.jsonl` output — no behavior change, verified by its existing trace format).

## 4. P1 — capture hook + persist traces (submission-blocking)

- **`AgentLoop.run(...)`** (`src/labrat/agent/loop.py`) gains an optional
  `on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None = None`. Around the
  dispatch (`loop.py:159`): record `t0`, call `dispatch = await self._registry.dispatch(...)`, compute
  `latency_ms`, derive `ok` and `output` (serialized result content) from the `DispatchResult`, and invoke
  `on_tool_call(tu.name, tu.input, ok, output, latency_ms)` when set. **Zero behavior change when `None`.**
- **`run_agent_task(...)`** (`src/labrat/agent/runner.py`) gains `on_tool_call=` and forwards it to
  `loop.run`.
- **`_run_trial_labrat_agent`** gains a `scratch_dir: Path` parameter (threaded from `run_trial`,
  `suite.py:441`). It passes a collector closure to `run_agent_task` that calls
  `append_tool_trace(scratch_dir, ...)` per call → `<scratch_dir>/agent_tool_calls.jsonl`.
- Submission packagers discover both drivers' traces via the glob `*tool_calls.jsonl`.

## 5. P2 — per-trial wall-clock timeout (operational parity)

In `_run_trial_labrat_agent`, wrap the agent call:
`await asyncio.wait_for(run_agent_task(...), timeout=self._agent_timeout or _DAB_TIMEOUT)`. On
`asyncio.TimeoutError`, return `final_text = f"[trial exceeded {effective_timeout}s timeout]"`,
`tool_calls=<count-so-far or 0>`, and the wall-clock latency — the **same marker string** claude-mcp emits,
so the shared `_detect_infra_failure` classifies it as `infra:` identically (and resume retries it). The
env-restore `finally` (LABRAT_MAZE_DIR/HOME) must still run on the timeout path.

## 6. P3 — document the sandbox asymmetry (document-only)

No code guard for the submission paths: claude-mcp = Claude (sandboxed subprocess); labrat-agent = Codex
(no native filesystem/shell tools → cannot read answer keys). Add (a) a comment at `_run_trial_labrat_agent`
stating the path must only carry providers without native file/shell access for a clean submission, and (b)
a row in the parity doc. Rationale: a hard in-process filesystem jail is disproportionate and unneeded for
Codex; the risk only arises with a filesystem-capable provider (e.g. `claude-code`), which is not a
submission path.

## 7. The deliverable: `docs/dab-driver-parity.md`

The living parity matrix — all 15 audited features (claude-mcp | labrat-agent | gap? | submission-blocking?),
the three gaps and how each is closed (P1/P2 in code, P3 documented), and P4 noted. This is what we point
the maintainers (and ourselves) at to assert Codex ⇄ Claude submission-equivalence. Updated as the gaps
close.

## 8. Testing plan (TDD)

- **`append_tool_trace`:** writes one line per call matching `TOOL_TRACE_FIELDS` exactly; round-trip parse;
  no-op on falsy `log_dir`.
- **MCP server refactor:** `_log_tool_call` still produces an identical `mcp_tool_calls.jsonl` line (schema
  unchanged) — guard against regression.
- **`AgentLoop` hook:** fires exactly once per dispatch with the correct `name` and `ok` (fake registry +
  provider returning one tool_use then text); not called when `on_tool_call=None`.
- **`run_agent_task` forwarding:** the hook reaches the loop (collector receives the call).
- **labrat-agent trace file:** the driver writes `<scratch_dir>/agent_tool_calls.jsonl` with the matching
  schema (test the collector seam; full driver run is integration, validated by the codex ablation/full run).
- **P2 timeout:** a fake slow `run_agent_task` → the driver returns the `"[trial exceeded …s timeout]"`
  marker and `_detect_infra_failure` classifies it `infra:`; env vars restored.
- Gate every commit: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.

## 9. Decisions

- **Shared writer** (not duplicated schema) so the two drivers' traces are byte-schema-identical for the
  package.
- **Filenames:** claude-mcp keeps `mcp_tool_calls.jsonl` (back-compat); labrat-agent uses
  `agent_tool_calls.jsonl`; packager globs `*tool_calls.jsonl`.
- **Capture via live hook** (not post-hoc `loop.history` reconstruction) — gives `ok`/`output`/`latency_ms`
  cleanly and mirrors claude-mcp's per-dispatch logging.
- **P2 reuses the exact claude-mcp infra marker string** so classification/resume stay shared.
- **P3 is documentation**, not a code jail (YAGNI for the Codex submission path).
- **Out of scope:** P4 (count semantics — labrat-agent already more accurate).
