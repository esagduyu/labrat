# Overnight Autonomous Run — 2026-07-10 — Decision Log

> User directive (2026-07-10, before sign-off): continue the Fable-window work autonomously overnight; run brainstorms as if the user were in the loop but self-answer along the recommended path; record every decision here for morning review; Fable reviews per task + per branch as usual; heartbeat every ~30-60 min against usage limits; NO regression validation (deferred post-Fable, memory-noted).

## Work queue (decided up front)

**Q1 — Ticket bundle first** (small, de-risks the backlog): one plan covering
(a) tagged `on_tool_call` propagation into dispatch_subagent sub-loops (trace-validity; prerequisite for any dispatching labrat-agent DAB submission);
(b) `_last_draft_sql` snapshot/restore around dispatch (M3 correction-baseline hazard);
(c) footer repr-parser full-tuple forgery hole + mixed-shape `+N` undercount (both small widget fixes).
**Excluded deliberately:** DAB-driver → host_configs migration — it touches the sandbox-load-bearing leaderboard path; wrong thing to do unattended overnight. Stays ticketed.

**Q2 — Cartographer attached-DB C1+C2** (the committed, code-verified 2026-07-04 plan, product half only): C1 attached-DB profiling coverage + C2 deterministic code/name detector. **C3/C4 (grounded-semantics + prune, i.e. the re-ablation enablers) stay tabled** with the benchmark track per the user's standing call. The plan predates RMv2/T1b — anchors will be re-verified before execution and the byte-identity rider honored.

**Q3 — If window remains:** moat-extras (2.3–2.5 + T2b v2 deferrals) scoping SPEC only (design artifact for morning review; no build unattended).

## Heartbeat policy

Primary driver = background-task notifications (as all session). Fallback = ScheduleWakeup ~1800s so the loop survives a hang. On any subagent dispatch failing with a usage-limit error: probe (`env -u ANTHROPIC_API_KEY -u CLAUDECODE claude --print -p ping`, per feedback_max_plan_probe — note: subagents here run on the session's own plan, so the probe is advisory), back off with 1800s wakeups until dispatches succeed again, then resume where the ledger says we stopped.

## Decisions taken overnight

(appended as they happen — each entry: what was decided, the options weighed, why)

- **D-01 (queue order):** tickets → C1/C2 → moat-spec. Rationale: the ticket bundle is 1-2h and closes real hazards (b) and submission-blockers (a) before the bigger build; C1/C2 has the highest product value but also the highest anchor-drift risk, so it gets the freshest review attention; the moat spec is safe to cut if limits bite.
- **D-02 (DAB-driver migration excluded):** see Q1 — leaderboard-path change unattended = bad idea. Recorded as remaining ticket.
- **D-03 (C3/C4 stay tabled):** they exist to make the semantics re-ablation valid; the user tabled ablations. Only C1/C2 execute.

- **D-04 (Q1a design — sub-loop trace propagation):** Options weighed: (i) thread a callback through the SubagentRunner protocol (rejected — the tool never holds the parent's hook); (ii) new AgentLoop parameter (rejected — core churn); (iii) **CHOSEN:** AgentLoop exposes its active `on_tool_call` during `run()` (one attribute); the session runner-closure captures the parent LOOP and forwards each sub-loop tool call to the parent's active hook with the name prefixed `subagent:`. Audit consumers (DAB `agent_tool_calls.jsonl`) get complete traces with provenance; schema unchanged.
- **D-05 (Q1a TUI consequence):** propagated `subagent:*` events would pollute the chat transcript (violating the DS-review adjudication "transcript = one dispatch line"). **CHOSEN:** ChatPanel's on_tool_call filters `subagent:`-prefixed names from transcript rendering (audit/message-post unaffected); TurnProvenance naturally ignores them (name mismatch) — parent footer stays parent-scoped. Recorded as deliberate.
- **D-06 (Q1b design — draft-baseline protection):** Options: core-level dispatch flag (rejected — UI concern in core); callback-splitting (rejected — fragile). **CHOSEN:** UI-owned wrapper — MainScreen wraps `ctx.subagent_runner` after `build_agent_session` installs it (caller-wins seam used as designed): snapshot `_last_draft_sql`/`_last_sql`, await the real runner, restore in `finally`. Sub-agent pane transparency preserved (adjudicated M4/DS behavior); capture baseline can no longer be poisoned by sub-agent drafts.
- **D-07 (Q1c design — footer hardening):** (c1) full-tuple forgery closed by POSITIONAL alignment: each domain/best_source/stale match must fall between DocResult-occurrence i and i+1 (finditer spans), else count-fallback. (c2) `+N` uses `self._scent_hits - 1` (all matched docs, tiered or not) instead of `len(_scent_docs) - 1`.

## Progress log

- **~23:30 — Q1 COMPLETE, merged (63270ee) + pushed.** 4 tasks, every Fable task-review clean (one routed test gap closed in-branch), whole-branch review APPROVED with all minors deferred; loop.py neutrality proven by byte-level equivalence; the footer forgery fix was independently re-mutation-checked by the final reviewer (old parser really did render a fabricated `verified` tier). No manual gate — reviewer concurred the textual pins are stronger than a visual check. Tickets D1/D2 + both footer items from the 2026-07-09 reviews are now closed; remaining carried ticket: DAB-driver → host_configs migration (deliberately excluded, D-02).
- **Next: Q2 — Cartographer attached-DB C1+C2** (re-verify the 2026-07-04 plan's anchors against post-T1b cartographer.py first, per D-03/byte-identity rider).
- **D-08 (Q2 resolved as already-shipped):** anchor re-verification revealed the 2026-07-04 attached-DB plan was FULLY EXECUTED in a prior session (its own commit messages in history: 6c0739d, 5c8d796, env/suite wiring, and even C3/C4 at 04a970e) — only the plan document was dormant/untracked until 2026-07-09. Verified byte-for-byte against current source; no post-T1b conflicts (per-catalog fingerprint stamping composes). Q2 = NO BUILD; plan doc annotated with status; the C3/C4 re-ablation RUN stays tabled (benchmark track). My earlier roadmap breakdown listing this as "execution-ready" was wrong in the useful direction.

- **~23:55 — proceeding to Q3:** moat-extras scoping SPEC (design-only per D-01; no unattended build).

- **D-09 (Q3 scoping — the six moat-extra candidates, assessed):**
  - **2.3 git-versioned team memory — RECOMMENDED NEXT, and building it tonight.** Smallest step with the largest strategic multiplier: turns the moat from personal to team (the Figma-GTM posture). Everything it needs already shipped — project-layer Scent lives as files in the analyst's repo, rendering is deterministic, apply/ingest are idempotent, RMv2 dedup absorbs merge dupes, and the unused `Section.git_sha` meta field is sitting there. v1 scope: (a) a pure `maze/status.py` report + module CLI (per-domain inventory, tier summary, freshness vs live catalog, manifest-drift state); (b) `git_sha` provenance stamping on the two derived write paths (harvest apply + semantic ingest; NOT the regenerable user-layer Cartographer); (c) `docs/team-scent.md` workflow doc (commit `labrat_maze/`, PR-review harvested sections, merge semantics). No LLM, no clock — overnight-safe, which is why building (not just speccing) is within the user's "continue executing" mandate; the earlier "spec-only" note in D-01 was my conservatism and is superseded for this low-risk scope.
  - **Embedding-based clustering (T2b v2) — second.** Self-contained quality win for `cluster_corrections`; `Memory.embedding` unused. Needs an embedding source decision (local vs API) → not unattended work.
  - **2.5 decision-trail harvesting — third.** Extends T2b to Findings/threads; wants product judgment on what a "decision" is → daytime brainstorm.
  - **Scheduled autonomous harvesting (T2b v2) — parked.** The human review gate is the moat's integrity mechanism; autonomous scheduling fights it. Revisit only with a batch-review UX.
  - **dbt-CI at-source pairing (T2b v2) — parked.** Write-back into the user's dbt repo = highest-trust surface; needs its own careful spec.
  - **2.4 customer-facing evals — parked.** Product-shaped and big; belongs with the platform track (post-T2a runtime modes).
