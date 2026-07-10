# Session Follow-ups Bundle — Design

**Date:** 2026-07-10 · **Status:** approved (overnight autonomous run; decisions D-04..D-07 in `docs/superpowers/overnight-2026-07-10-decisions.md`; user reviews in the morning)
**Closes tickets from the 2026-07-09 reviews:** D1 sub-loop trace propagation (dispatch_subagent whole-branch review), D2 draft-baseline overwrite (same review), footer repr full-tuple forgery + mixed-shape `+N` (RMv2/T4 reviews). **Excluded:** DAB-driver → host_configs migration (leaderboard path; not for unattended work).

## 1. Problems

- **P1:** sub-loop tool calls are invisible to the parent's `on_tool_call` — `agent_tool_calls.jsonl` on the labrat-agent path omits sub-agent activity, weakening trace-validity for any future dispatching submission.
- **P2:** the TUI's shared tool instances mean a sub-agent's `on_draft` overwrites `MainScreen._last_draft_sql` — the M3 correction-capture baseline can diff a user edit against a sub-agent's exploratory SQL (contained by the human harvest gate, but a Scent-quality hazard).
- **P3:** `TurnProvenance`'s repr parser can be misaligned by a doc body containing a complete forged `DocResult(...)` tuple (misattributes tier/freshness), and mixed enriched+tierless payloads under-report `+N`.

## 2. Decisions (D-04..D-07)

- **R1 (P1):** `AgentLoop` exposes its active per-run hook as `self.active_on_tool_call` (set at `run()` entry from the argument, cleared in a `finally`). The session runner-closure captures the parent loop and passes the sub-loop a forwarding hook: `parent.active_on_tool_call(f"subagent:{name}", input, ok, output, latency_ms)` when the parent hook is set. Trace schema unchanged; provenance via name prefix.
- **R2 (P1/TUI):** `ChatPanel`'s `on_tool_call` closure returns early for names starting with `"subagent:"` — no transcript line, no `AgentToolCall` message. `TurnProvenance` ignores them by construction (exact-name matching). The DS-review transcript adjudication is preserved.
- **R3 (P2):** `MainScreen` wraps `ctx.subagent_runner` immediately after `build_agent_session` returns (the caller-wins seam, used as designed): snapshot `_last_draft_sql` and `_last_sql`, `await` the wrapped runner, restore both in `finally`. Editor/pane updates during the sub-run remain live (adjudicated transparency); only the capture baseline is protected.
- **R4 (P3-forgery):** replace the count-equality alignment guard with **positional** alignment: using `finditer`, the i-th `domain=`/`best_source=`/`stale=` matches must each start within the span (start of `DocResult(` occurrence i, start of occurrence i+1 or end-of-string). Any field failing containment → count-fallback for the whole output (never partial attribution).
- **R5 (P3-undercount):** footer `+N` = `self._scent_hits - 1` (all matched docs), not `len(self._scent_docs) - 1`.

## 3. Non-negotiables

1. Depth-1 and scoped-seed invariants untouched (the forwarding hook adds observation only; sub-loop still receives no parent history and no runner).
2. Existing suites unmodified except where a pinned expectation legitimately changes (the mixed-shape `+N` test, if one pins the old value) — updates listed, never weakened.
3. TUI transcript behavior byte-identical for all existing flows (the DS spot-check contract: one dispatch line, zero sub chatter).
4. Never-raise contract of `TurnProvenance` preserved; positional parsing failure degrades to count-fallback.
5. `screens/` exempt / `agent/`+`widgets/` pyright-strict; repo gates per commit; known env flake `test_app_renders` non-signal.

## 4. Testing

- P1: e2e extension (dispatch_subagent e2e file): parent `on_tool_call` collects `subagent:`-prefixed events for the sub-loop's dispatches, parent's own events unprefixed; hook cleared after run (no leak between runs).
- P2: TUI test — a fake runner that fires the parent's `on_draft` mid-run: `_last_draft_sql` mutates during, restored after; a user edit post-dispatch diffs against the pre-dispatch draft.
- P3: forged-full-tuple body → count-fallback (no `verified` label); mixed enriched+tierless JSON → `+N` reflects all docs; existing fallback/format tests untouched.
