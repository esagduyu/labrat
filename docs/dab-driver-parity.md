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
| 11 | Informed packs (`--informed-*` ×4) | opening user message | system prompt | ✅ parity (both builders, verified by prompt-builder assertion 2026-08-02) |
| 11 | Submission artifacts | config/trials/submission/report + traces | same + traces | ✅ parity |
| 12 | Variant-Scent diversity (consensus sub-run decorrelation) | `variant_seed=diversity_index or 0` into `_run_cartographer` | same (closed P5) | ✅ parity |
| 13 | Framing rotation (`_framing_for`) on diversified sub-runs | yes | yes | ✅ parity |
| 14 | Argumentation rounds on a split consensus vote (`--agent-argue-rounds`) | shared `_run_trial_verified`, driver-agnostic | same | ✅ parity |
| 15 | Postverify (constraint check + bounded revise, `--agent-postverify`) | shared `_run_trial_verified`, driver-agnostic | same | ✅ parity |

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
- **P5 — variant-Scent diversity was labrat-agent-only-missing (M1 final-review fix, closed):**
  `_run_trial_claude_mcp` passed `variant_seed=diversity_index or 0` into `_run_cartographer`, but
  `_run_trial_labrat_agent`'s call site omitted it, so labrat-agent consensus sub-runs all shared
  seed-0 Scent — the decorrelation mechanism was absent on that driver. Fixed by threading
  `variant_seed=diversity_index or 0` through the labrat-agent call site too (`diversity_index` was
  already a parameter on `_run_trial_labrat_agent`, just not forwarded to `_run_cartographer`).

## Conclusion

With P1–P5 closed and P3 documented, the labrat-agent/Codex path is **submission-equivalent** to
claude-mcp: identical tool set, identical trace schema, identical scoring/sandbox/resume semantics,
identical M1 consensus-decorrelation/argumentation/postverify behavior, and a full trace package per
trial.
