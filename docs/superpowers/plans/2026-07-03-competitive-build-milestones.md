# Competitive Build Milestones (2026-07-03)

> A sequenced, dependency-aware milestone roadmap turning the ranked feature list in `docs/competitive-analysis-2026-07-03.md` into buildable work. Each milestone gets its **own spec + TDD plan at kickoff** (brainstorming → writing-plans → subagent-driven-development), not a monolithic up-front task list. Ordered by ROI × confidence × dependency. Feature IDs (0.1, 1.2, …) reference the report's §4 tiers.

**Kickoff trigger:** the 4-arm DAB ablation (`runs/dab/abl2/`, Sonnet 5 vs 4.6 × parse-levers off/on) must finish first — it gates M0's two decisions. Build starts at **M0 immediately on completion.**

---

## ▶ Progress (updated 2026-07-06)

| Milestone | Status | Merge |
|-----------|--------|-------|
| **M0** — deterministic levers | ✅ SHIPPED | `798c501` |
| **M1** — verification-v2 | ✅ SHIPPED | `912b644` |
| **M2** — verified semantic Scent | ✅ SHIPPED | `5a615a6` |
| **M3** — column-level lineage + read-only Analyst mode | ✅ SHIPPED | `ad125e0` |
| **M4** — execution white space (Context Ledger + llm_extract/classify + program mode) | ✅ SHIPPED | `ebf3bd0` · `493b3db` · `4a08a4c` |
| **M5** — compounding memory moat (Plan 0 foundation + T2b harvesting v1) | ▶ IN PROGRESS | — |

M4 detail: Context Ledger Ph1 (`ebf3bd0`, M4 foundation), llm_extract/llm_classify per-row primitives (`493b3db`, M4 2.1), program mode `run_program` (`4a08a4c`, M4 2.2). None are claude-mcp leaderboard levers — all are AgentLoop/labrat-agent-path product wins. Ledger Phase 2 (`dispatch_subagent`) remains deferred.

M5 is scoped to the **moat-roadmap Plan 0 (shared Scent section-provenance foundation) + Plan 1 (T2b correction-harvesting v1)** — the shippable first increment (spec `docs/superpowers/specs/2026-07-02-moat-roadmap-design.md`, adopted+refreshed at kickoff). The milestones-doc extras below (2.3 git-team memory, 2.4 customer-facing evals, 2.5 decision-trail harvesting) and moat Increments 2/3 (T1b lineage — note `explain_lineage` already shipped in M3; T3c provenance footer) are M5 follow-ons, each getting its own spec+plan when reached.

---

## Guiding sequence logic

1. **Process the run, then bank the cheap deterministic wins** (M0) — lowest risk, mostly extends tools we own, benchmark-safe.
2. **Rebuild the two highest-confidence benchmark levers that we already tried and mis-implemented** (M1 verification, M2 semantic Scent) — competitors *above us* prove the correct versions.
3. **Ship the deterministic data-intelligence moat** (M3 lineage) — Altimate parity + our T1b.
4. **Take the execution white space** (M4 program mode + per-row primitives) — differentiated, attacks the category the whole board fails.
5. **Build the compounding memory moat** (M5) — the durable game.
6. **Strategic track runs in parallel** (backbone experiment, ADE publish) — non-blocking.

Rationale from the analysis: the top of the board wins with *bigger models + benchmark fitting*; our durable edge is *verification done right + deterministic tooling + the program/primitive white space + open compounding memory*.

---

## M0 — Process the run + cheap deterministic levers  ·  branch `feat/deterministic-levers`

**Goal:** act on the ablation, then ship the S-effort, deterministic, GT-firewalled levers that need no new machinery.

**Gate / first actions (ablation-dependent):**
- **Decide Plan A (parse-robustness levers).** If net-positive on the ablation's stratified per-dataset deltas (esp. deps_dev_v1/music_brainz/pancancer/yelp/googlelocal) → merge `feat/dab-parse-robustness`. If within noise → keep default-off, document, do not merge default-on. Run the ADE 9-task smoke gate first (needs Docker) to clear the `system_base.md` product-path change.
- **Read the Sonnet-5 vs 4.6 delta** (arms s5-off vs s46-off, s5-on vs s46-on). If Sonnet 5 alone is a large lift, promote the **backbone-swap experiment** (Strategic track) ahead of M1 — it may be the single biggest lever and reorders everything.

**Features (all Tier 1/3, deterministic, S each):**
- **1.2 Pre-execution SQL fuzzy-check** (`check_sql`) — validate table/column refs from parsed SQL against `ctx.catalogs`, Levenshtein-suggest closest names before execution. New tool in the `verify_join`/`link_schema` family.
- **1.3 Join-key transform detection** in the Cartographer — sample values, detect `12345 ↔ "CUST-0012345"`, emit the exact normalization SQL into the Scent doc (extends `verify_join` from diagnose→prescribe).
- **1.4 Value-ranges + stratified format-sampling** in `profile_dataset`/`cartograph_prepass` — per-column min/max/cardinality/top-values + deliberately surface rows with unusual value structure (embedded separators, delimiter paths, JSON-in-text).
- **3.1 `normalize_text()`** registered as a SQL function/macro on every connection (diacritic/whitespace/case-insensitive).
- **3.4 Wide-DB schema compaction** — "N tables share structure → one representative + name list" (stockmarket/stockindex).
- **3.5 `top_n_with_ties` lever** — one-line system-prompt/`_dab_lever_lines` addition ("LIMIT N silently truncates ties").
- **3.3 Mechanized taint-audit as a scoring gate** — upgrade `_detect_contamination` from detect → gate+classify (exit-code contract, `taint.json`) in `eval_dab.py`.

**Deps:** Cartographer (`maze/cartographer.py`), `data_tools.py` registry, `eval_dab.py`. All shipped surfaces.
**Effort:** M (bundle of S items). **Exit:** all merged to master; each Cartographer/tool change ablated neutral-or-positive on the tuning subset + ADE smoke green; taint-gate blocks an unaudited submission by construction.
**Why first:** immediate, low-risk, corroborated by DataBridge's (overfit) prompt hardcoding these same fixes — we do them as general deterministic tooling.

---

## M1 — Verification, done right (the #1 benchmark lever)  ·  branch `feat/verification-v2`

**Goal:** rebuild T1a as the version two teams *above us* prove, converting our within-noise consensus into a real lift.

**Features (Tier 0 + 3):**
- **0.1 Input-diversity consensus + argumentation** — run K analysts on *different catalog sample views* (decorrelate errors at the input, not temperature); on disagreement, ≤2 rounds where each sees the other's answer + justification and must defend/revise; then modal. [MinusX — working at our exact benchmark]
- **Separated-context re-derive** — the verifier is a *fresh `run_agent_task`* receiving only (question, candidate answer, connections) — **not** the maker's transcript — instructed to re-derive, not judge. [Spacedock]
- **0.3 Deterministic post-step verifiers** — auto-append result shape/dtypes/head; zero-LLM `WARN/FAIL` on empty-after-filter / >2× row-blowup-after-join / groupby-grew-rows. **+ question-derived constraint checker** (extract top_k/distinct/count/percentage from the question, validate the answer). [SCRIBE]
- **3.6 Multi-interpretation rotation** — planner enumerates 2–4 readings; when split, rotate the chosen reading across the K trials (diversifies consensus). [SCRIBE]
- **3.7 Root-cause verdict cascade** — typed taxonomy (spec_wrong/executor_wrong/blind_spot/NA/answer) routing recovery, replacing the binary sufficiency judge. [SCRIBE]

**Deps:** `agent/verification/{agreement,consensus}.py` + `DabSuite._run_trial_verified` (shipped, `feat/verification-layer` merged). This is a redo/upgrade of that scaffolding.
**Effort:** M–L. **Exit:** 4-arm ablation (off / consensus-v2 / +postverify / both) on the expanded 6-dataset subset shows a **clear** (beyond-noise) lift; if so, enable on a submission. Judge auth routed to claude-code on claude-mcp (the shipped fix).
**Why #1 build lever:** the analysis' highest-confidence item — Spacedock (#2) and MinusX (#6) both beat us primarily on verification, and we already own the plumbing.

---

## M2 — Semantic Scent, verified (T1c redo)  ·  branch `feat/scent-verified-semantics`

**Goal:** make the `with_semantics` Cartographer pass net-positive by verifying every claim, reversing the −3.7pp result.

**Features (Tier 0.2):**
- **Join-verified authoring** — no semantic/join annotation is persisted unless it survives a live `verify_join`/`COUNT(*)` probe. [MinusX]
- **Conditional, not prescriptive, rules** — author gotchas as "when the question asks for coded values use X; for labels use Y," never unconditional "use X not Y" (which broke music_brainz for us and pancancer for Altimate). [Altimate]
- **ID-tagged, few-high-signal annotations** — render catalog with short IDs; self-check drops anything restating name+type. [MinusX]

**Deps:** `cartograph_prepass(with_semantics=True)` + `scent_audit.py` (shipped, `feat/llm-semantic-scent` merged); composes with `verify_join`.
**Effort:** M. **Exit:** re-ablate semantics vs structure-only on the tuning subset; keep only if it clears the prior −3.7pp and lands net-positive (target: recover music_brainz). Stays default-off until proven.
**Why after M1:** shares the "verify-before-trust" primitive M1 hardens; lower confidence than M1 so sequenced second.

---

## M3 — Column-level lineage + data-intelligence (T1b)  ·  branch `feat/column-lineage`

**Goal:** close Altimate's headline gap and our known T1b hole; deepen grounding + trust.

**Features (Tier 1 + 3):**
- **1.1 Column-level lineage tool** (`explain_lineage`/`trace_column`) — parse SQL live against the `Catalog` via `sqlglot.lineage`, **not** dbt manifests (staleness argument). Registered in `build_data_tools_registry()`; write `lineage`-tagged sections into Scent. [Altimate — their #1 differentiator]
- **3.2 Engine-enforced read-only "Analyst" mode** — `ToolContext.read_only` enforced in the registry/dispatch layer (not prompt); "safe to point at prod." Generalizes the shipped `run_sql` statement-stacking guard. [Altimate]

**Deps:** `catalog/dbt/` (`DbtLoader`/`LineageGraph`), `ToolContext`, moat-roadmap **Plan 2** (this milestone *is* T1b, upgraded with the live-parse insight). Uses the shared Scent-provenance foundation (`lineage` token) from the moat roadmap.
**Effort:** M. **Exit:** `explain_lineage` reachable by the agent + surfaced in Scent; read-only mode enforced with a test that a mutation is blocked at the registry.
**Why here:** moat + parity; independent of M1/M2; can run in parallel with them if capacity allows.

---

## M4 — Execution white space (T1d extensions)  ·  branch `feat/context-ledger` (extend)

**Goal:** take the differentiated ground the whole board lacks — deterministic program execution + per-row LLM primitives.

**Features (Tier 2.1, 2.2):**
- **2.1 Per-row LLM primitives** — `llm_extract(rows, json_schema)` / `llm_classify` fanning out per-row mini-calls from a deterministic loop, results bound outside model context. **Attacks bulk unstructured extraction (patents) — the one category the entire leaderboard fails, PromptQL included at 0%.** [PromptQL — unique]
- **2.2 Plan-then-execute "program mode" + handle-based results** — opt-in mode: model emits one script composing existing tools, executed in a sandbox, results stored as fetchable handles **and live DuckDB temp tables** (cross-connection joins for free), not flowed back through context. [PromptQL + MinusX + Pi, convergent]

**Deps:** T1d Context Ledger (spec'd on `feat/context-ledger`; ResultStore/ledger). Program mode extends the ledger from *bounding* re-entry to *preventing* it; handle-tables reuse `load_file` TEMP-table machinery. **Sandbox by construction** (contamination — reuse the claude-mcp gate pattern).
**Effort:** L. **Exit:** `llm_extract` moves patents off 0% on a dev run; program mode produces byte-identical answers with lower peak context; sandbox verified (no answer-key/network reach).
**Why after M3:** biggest build; builds on the (unbuilt) Context Ledger; highest differentiation but not the fastest ROI.

---

## M5 — Compounding memory moat (T2b+)  ·  branch `feat/correction-harvesting`

**Goal:** ship the durable, defensible moat the funded incumbents (Altimate Decision Graph, PromptQL "team skills") are also racing toward — on the open/terminal wedge they can't match.

**Features (moat-roadmap Plan 0/1 + Tier 2.3–2.5):**
- Moat-roadmap **foundation + T2b v1** — wire the dormant extractors → human-gated promotion into Scent + staleness (already spec'd in `docs/superpowers/plans/2026-07-02-moat-foundation-and-t2b-harvesting.md`).
- **2.3 Git-versioned, team-inherited memory** — memory blocks in `./labrat_maze/memory/`, size-capped (Oracle Forge cautionary tale: unbounded log = 70× tokens), tagged, propagating on `git pull`. [Altimate]
- **2.5 Decision-trail harvesting** — `decisions.jsonl` beside `agent_tool_calls.jsonl`; harvest recorded verdicts, not just edits. [Spacedock]
- **2.4 Customer-facing evals** — `labrat evals` (question → assertion → expected, incl. "cannot answer") to make context quality measurable — closes the flywheel. [MinusX] *(v2 — larger; can split out.)*

**Deps:** `memory/` (extractors, `MemoryStore`), moat-roadmap shared Scent-provenance foundation, `history/`.
**Effort:** L. **Exit:** corrections harvest → human-approved → appear in Scent; team memory round-trips through git; evals run against a user DB. Size-budgeting enforced.
**Why last:** highest moat value but least benchmark-urgent; benefits from M0–M3 grounding/verification being in place to harvest against.

---

## Strategic track (parallel, non-blocking)

- **Backbone-swap DAB experiment** — the one winners' lever we've never tried. Gated on M0's read of the ablation's Sonnet-5 arm; if Sonnet 5 (or Opus/GPT-5.5 via the wired codex provider) is a large lift, this could outrank several code milestones. Experiment, then decide the leaderboard backbone.
- **Publish ADE-bench 80% methodology loudly** — before Spacedock's razorback posts an ADE number and takes the "verification-harness beats tool-rich agent" narrative on *our* benchmark. Zero code.
- **Marketing** — "only fully-auditable top-10 entry / single mid-tier model / beat DAB's co-creator PromptQL." Fold into the next submission writeup.

---

## Dependency & branch map

```
ablation (running) ─┬─► M0 deterministic-levers ─► master
                    └─► [Sonnet-5 delta] ─► Strategic: backbone experiment
feat/verification-layer (merged) ─► M1 verification-v2
feat/llm-semantic-scent (merged) ─► M2 scent-verified-semantics  (uses M1's verify primitive)
moat-roadmap Plan 2 ─► M3 column-lineage  (uses shared Scent-provenance foundation)
feat/context-ledger (T1d spec) ─► M4 program-mode + primitives
moat-roadmap Plan 0/1 ─► M5 correction-harvesting
```

**Parallelizable:** M3 is independent of M1/M2 (different subsystem) — run concurrently if capacity allows. M4 depends on T1d landing. M5 is last but its foundation (Scent-provenance schema) is shared with M3 → **do that foundation once, before M3**, per the moat-roadmap spec.

## Kickoff order after the run
1. **M0** immediately (process ablation → merge/decide Plan A → ship deterministic levers). 
2. **M1** next (highest-confidence lever; own spec+plan at kickoff).
3. Then **M2 / M3** (M3 parallelizable), **M4**, **M5** — each brainstormed → spec'd → TDD-planned → subagent-built at its turn.

Each milestone above is a *pointer to a build*, not the build itself: at kickoff I run the superpowers workflow (brainstorming → writing-plans → subagent-driven-development) to produce its detailed spec + task-level TDD plan, exactly as done for DAB Plan A.
