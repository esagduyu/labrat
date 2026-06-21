# Data-analysis workflow skill + SQL self-repair (FEATURE_ROADMAP #30)

> **Status:** Design approved 2026-06-21. The **workflow/process-skill** half of the Anthropic
> article's two-layer pattern (the knowledge half is #26a/#26b, shipped). Promotes LabRat's
> prescriptive system prompt into a first-class, ordered, **inspectable** data-analysis SOP that the
> agent walks and tracks, plus the highest-evidence reliability add-on from a June-2026 SOTA review:
> deterministic, repair-oriented SQL error diagnostics.
>
> **Branch:** `feat/workflow-skill`. **Process:** superpowers — this spec → `writing-plans` → TDD →
> verification → review. Full gate every commit: `ruff format` → `ruff check` → `pyright` → `pytest`.

## 1. Why

The article credits *procedural* knowledge (the senior-analyst SOP / "Unbook" skill) as much as the
reference docs for the 21%→95% accuracy jump. #30 is that procedural layer for LabRat: the
prescriptive prompt becomes a named, ordered, **checkable** workflow the agent tracks step-by-step,
so the sequence is inspectable (UI/audit) rather than advisory prose.

A June-2026 SOTA review (research subagent, anchored on verified/evaluated big-company sources —
Anthropic self-serve analytics, Google DS-STAR, Spider 2.0, DataAgentBench, SQL self-healing
literature) found the proposed flow **substantially SOTA-aligned** and in places more rigorous
(verify-joins is differentiated). It surfaced one high-evidence gap we adopt here: **execution-driven
SQL repair** (feeding rich error diagnostics back for a bounded retry) — the strongest single add-on
in the literature (**+4.6pp on BIRD**, deterministic, execution-gated, no LLM-judge reliability risk).
It also confirmed two of our decisions: **fail-open enforcement** (hard gates cost throughput without
accuracy gains; DS-STAR uses an advisory loop) and **opt-in-only LLM-judge verifier** (pure
sufficiency judges show ~no benefit; the +6% adversarial-review gain requires a *tool-enabled*
reviewer — tracked as a #13 follow-up, not built here).

## 2. Scope

**In scope (#30):**
- A canonical **data-analysis SOP** (9 ordered steps) as structured prompt guidance (all surfaces).
- A **`workflow` tool** that records the agent's progress through the steps and returns the checklist
  — fail-open, record-and-inspect (no runtime blocking).
- **Deterministic SQL self-repair**: enrich `run_sql`'s error output with a structured error
  category + the executed SQL + a remediation hint, plus a bounded anti-thrash attempt counter in
  the workflow state.
- Register the `workflow` tool in `build_data_tools_registry()` (reaches agent + MCP + TUI).
- Promote `system_base.md`'s Workflow section into the 9-step SOP + a "track each step with
  `workflow`" instruction.

**Explicitly out of scope (follow-ups, noted not built):**
- Upgrading the #13 verifier to a **tool-enabled** adversarial reviewer (where Anthropic's +6% lives).
- Reference-doc **drift/staleness detection** (already #26b cycle-B MAINTAIN).
- **Multiple/named** workflows / a Trail recipe registry (one canonical SOP now).
- A **TUI checklist widget** (we expose the state; the widget is later).
- Hard or soft *enforcement* gates (chose record-and-inspect, research-confirmed).
- A separate generate-execute-repair subsystem (redundant with the AgentLoop — see §5).

## 3. The SOP (one canonical workflow)

Ordered steps, reconciling the existing `system_base.md` Workflow with the SOTA review's additions
(marked ★ where new vs. today's prompt):

| # | key | step |
|---|---|---|
| 1 | `clarify` | Restate the question + assumptions; ★ decompose a complex multi-part question into sub-questions. |
| 2 | `consult_scent` | Call `search_reference_docs` for curated grounding. |
| 3 | `ground` | `profile_dataset` / `link_schema`; ★ column-value grounding (map NL value mentions to real DB values via `search_columns` / `column_stats`). |
| 4 | `plan` | State a short numbered plan. |
| 5 | `query` | Execute one step at a time, reading each result; prefer pushing aggregation into SQL. |
| 6 | `repair` | ★ On a SQL error, use the structured diagnostics to fix and retry; bounded (see §5). |
| 7 | `verify_joins` | `verify_join` before trusting any join (match-rate + fan-out). |
| 8 | `verify_answer` | Confirm the result answers *that* question — magnitudes, units, row fan-out. |
| 9 | `review` | (opt-in) adversarial review (the #13 verifier). |

Defined once as a code constant (`DATA_ANALYSIS_WORKFLOW`), mirrored in the prompt. The step `key`s
are the stable identifiers the `workflow` tool accepts.

## 4. Workflow tracking (the "checkable" mechanism)

**`src/labrat/agent/workflow.py`:**
- `STEP_KEYS: tuple[str, ...]` and `DATA_ANALYSIS_WORKFLOW: list[WorkflowStep]` (key + human label).
- `class WorkflowState(BaseModel)`: ordered per-step status `pending`/`doing`/`done`, an optional
  `note` per step, and a `repair_attempts: int` counter. Methods: `mark(key, status, note=None)`,
  `render() -> str` (a checklist like `[x] clarify  [~] query  [ ] verify_joins …`), and
  `note_repair_failure() -> int` (increments + returns the count).

**`src/labrat/agent/tools/workflow.py`** — `WorkflowTool` (name `workflow`):
- Input: `step: str | None = None` (a step key), `status: "doing" | "done" = "done"`,
  `note: str | None = None`.
- No `step` → returns the current checklist (status query). With `step` → validates it against
  `STEP_KEYS` (unknown key → error), records the status, returns the updated checklist.
- Holds `dict[str, WorkflowState]` keyed by `ctx.profile_name` (mirrors the profile-keyed tool
  pattern); lazily creates a fresh state per profile. State persists across calls within a session.
- Registered in `build_data_tools_registry()`.

**Inspectability:** the tool's return value shows the full checklist (the agent sees its own
progress); the `WorkflowState` is queryable so a future TUI widget can render it live (widget out of
scope); and `run_sql` already logs every execution — including failures, with the error message — to
the query-history log, so repair attempts are recorded there without new audit wiring.

## 5. Deterministic SQL self-repair

LabRat's AgentLoop **already** provides execution-feedback repair: `run_sql` returns an error, the
loop feeds it back as a tool result, and the model retries. The SOTA review's +4.6pp comes from
making that feedback *rich and bounded*, not from a parallel loop (regeneration needs the model,
which is the loop). So #30's deterministic repair is two concrete, non-redundant pieces:

**(a) Repair-oriented `run_sql` error output** (`agent/tools/run_sql.py`, modify the error path):
- Add fields to `_Output`: `error_category: str | None`, `executed_sql: str | None`,
  `hint: str | None` (keep `error` for back-compat).
- On the `except` path, classify the exception message deterministically into a category and emit a
  targeted hint:
  | category | trigger (case-insensitive substring in the exception) | hint |
  |---|---|---|
  | `unknown_table` | "table" + ("does not exist" / "not found") | "Table not found — call `list_tables` / `profile_dataset` to confirm the name." |
  | `missing_column` | "column" + ("not found" / "does not have a column" / "does not exist") | "Column not found — call `describe_table` / `search_columns` to confirm the name." |
  | `syntax` | "parser error" / "syntax error" | "Syntax error — re-check the SQL against the active dialect." |
  | `type_mismatch` | "conversion" / "type" / "cast" | "Type mismatch — check column types with `describe_table`; cast explicitly." |
  | `other` | (fallback) | "Inspect the error; verify table/column names and types before retrying." |
- Always set `executed_sql` to the actually-run SQL (post auto-limit) so the model repairs what ran.
- Classification is a small pure helper (`_classify_sql_error(msg) -> tuple[category, hint]`),
  unit-tested directly; dialect-agnostic enough for the DuckDB primary (categories generalize).

**(b) Bounded anti-thrash guard** (fail-open): the `workflow` tool's state carries
`repair_attempts`. The tool increments it via `note_repair_failure()` each time the `repair` step is
marked `doing` (i.e. each new repair attempt the agent starts, which it does per the prompt after a
failed query). Once the count exceeds a small cap (default **3**) the checklist render flags it
(`repair: 3 failed attempts — rethink the approach`). This *surfaces*; it never blocks. The existing
`max_tool_calls` remains the hard backstop.

## 6. Prompt changes (`agent/prompts/system_base.md`)

Replace the Workflow section with the 9-step SOP (above), and add to it: *"Track your progress by
calling `workflow` to mark each step `doing` when you start it and `done` when you finish — walk the
steps in order. If a query errors, read the returned `hint` and `error_category`, fix the SQL, and
retry; after a few failed attempts, stop and rethink rather than retrying blindly."* Add a Tool
Usage bullet for `workflow`. Keep the existing reference-docs (#26a) router step as step 2.

## 7. Enforcement & benchmark safety

- **Fail-open, record-and-inspect** — no runtime blocking; discipline = prompt + visible checklist +
  review. Research-confirmed (DS-STAR advisory loop; hard gates hurt easy-task throughput).
- **Benchmark-safe by construction:** the `workflow` tool is mechanism-only (no content, no GT
  access); the `run_sql` diagnostics are generic error guidance (no dataset-specific or answer-shaped
  content). Inert when unused. No leakage surface.

## 8. Testing plan (TDD)

- **WorkflowState:** all steps `pending` on init; `mark` transitions; `render` shows the checklist
  in order; `note_repair_failure` increments; unknown key handling.
- **workflow tool:** no-arg returns the full checklist; marking a step advances it; unknown step key
  → validation error; state persists across calls for one profile; two profiles are isolated.
- **run_sql repair diagnostics:** `_classify_sql_error` maps representative messages to the right
  category+hint; an integration test runs a deliberately-bad query against the `ecommerce_db`
  fixture (a missing column) and asserts `ok=False` with `error_category="missing_column"`, a
  non-empty `hint`, and `executed_sql` set; a valid query still returns `ok=True` unchanged
  (back-compat).
- **registry:** `workflow` present in `build_data_tools_registry().to_anthropic_schemas()`.
- **prompt:** `system_base.md` names the `workflow` tool, includes the repair guidance
  (`error_category` / `hint`), and covers the SOP steps (clarify → review).
- Gate every commit: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.

## 9. Decisions settled during brainstorming (+ SOTA review)

- **Form:** structured prompt SOP (all surfaces) + a checkable `workflow` tool (not prompt-only, not
  a host-specific SKILL.md).
- **Enforcement:** fail-open record-and-inspect (research-confirmed; not soft/hard gating).
- **Repair:** built into #30 as repair-oriented `run_sql` diagnostics + a bounded anti-thrash counter
  (not a redundant generate-execute-repair subsystem).
- **SOP additions from the review:** question decomposition (into `clarify`), column-value grounding
  (into `ground`), explicit `repair` step.
- **Verifier:** stays opt-in/off; the tool-enabled-reviewer upgrade is a #13 follow-up.
- **One canonical workflow** now; named/multiple workflows are a later Trail-registry concern.
- Tool name `workflow`; state keyed by `profile_name`; package `agent/workflow.py` (Rat Core).

## 10. Research provenance

Full report archived in the session; key verified anchors: Anthropic self-serve analytics
(21%→95%; adversarial review +6% only when tool-enabled), Google DS-STAR (Analyzer ablation −18pp on
hard tasks; advisory loop), Spider 2.0 (enterprise schema-linking failures), DataAgentBench (failure
mix: 45% implementation / 40% plan / 15% data-selection), SQL self-healing (+4.6pp BIRD with bounded
execution-feedback repair), DEA-SQL (decomposition +5pp). Honest caveat: no study isolates
"checkable checklist artifact vs prose prompt" — we keep it for inspectability/auditability, not as
an accuracy claim.
