# LabRat Competitive Analysis — DataAgentBench Field (2026-07-03)

> Deep-dive intelligence on every product competitor on the [DataAgentBench leaderboard](https://ucbepic.github.io/DataAgentBench/) (leaderboard JSON updated 2026-07-02), analyzed against LabRat's north-star vision. Sources: each competitor's product site, GitHub repo, and DAB submission PR; researched by seven parallel Fable-model agents reading actual code, not marketing. Companion to `FEATURE_ROADMAP.md`, `competitive_intelligence.md`, and memory `project_competitive_position`.

---

## 0. TL;DR

- **We slipped #8 → #9 of 23**, but only because a **new #1 (SCRIBE/Actioneer, 81.85%) entered above the field** — and that entry is **DAB-overfit and oracle-selected**, not a capability jump. Our 60.88% is unchanged and remains **the only top-10 result on a single mid-tier model, fully traced, honestly scored.**
- **The leaderboard's top is increasingly benchmark-fitted.** SCRIBE hardcodes 11 DAB-specific domain rules + selects trials with the official validator; DataBridge (#7) hardcodes fixes for named queries; even Altimate/MinusX/PromptQL fold `--hints` + dataset-tuned answer-formatting in. Our honest, structure-only, traced posture is a *different and more defensible claim* — keep saying so.
- **Two of our "failed" ablations were implementation misses, not dead ends.** MinusX (#6) and Spacedock (#2) ship the *working* versions of consensus (T1a) and semantic grounding (T1c) that we ablated to null/negative. We now know exactly why ours didn't move and how to fix them.
- **The moat pattern is commoditizing.** Cartographer-style grounding + a corrections log is now the *default* playbook — even a training-bootcamp reference architecture (Oracle Forge) ships "AGENT.md + domain KB + corrections log." Our differentiation must be **execution quality** (deterministic, GT-firewalled, verified) and **the honest AGPL/terminal wedge**, not the mere existence of memory/grounding.
- **Our closest competitors are MinusX and Altimate.** MinusX shares our exact positioning ("Claude Code for data," AGPL, open-source, single-operator). Altimate is the best-funded validation of our thesis with ~10× our distribution — but their OSS is a funnel to a *closed* Rust core + SaaS.
- **Genuine white space nobody (including us) has shipped:** **per-row LLM primitives inside deterministic programs** (attacks the bulk-unstructured category — patents — that the *entire* leaderboard fails), and **verification done as separated-context re-derivation with input diversity.**

---

## 1. The leaderboard as of 2026-07-02

| # | Entry | Pass@1 | Real competitor? |
|---|---|---|---|
| 1 | **SCRIBE (Actioneer)** + Opus 4.7 | 81.85% | Research harness, solo builder — **DAB-overfit + oracle-selected** |
| 2 | **Spacedock (Recce)** + GPT-5.5 | 74.33% | Yes — adversarial-review engine (Recce, $4M seed) |
| 3 | **Altimate Code** + GPT-5.5 + Sonnet 4.6 | 71.71% | Yes — closest funded competitor (~$2M, Sierra) |
| 4 | Altimate Code + Sonnet 4.6 | 68.22% | " |
| 5 | Spacedock + Opus 4.8 | 67.21% | " |
| 6 | **MinusX** + Sonnet 4.6 + GPT-5.5-mini + Haiku 4.5 | 65.18% | Yes — **most direct positioning competitor** (YC S24, AGPL) |
| 7 | **DataBridge** + GLM-5.2 | 61.37% | Barely — 1-person, 1-week benchmark prototype, overfit |
| 8 | **Pi Coding Agent** + Opus 4.6 | 61.03% | No — MIT harness (Earendil) + indie methodology demo |
| **9** | **LabRat (Sonnet 4.6 + Cartographer)** | **60.88%** | — us — |
| 10 | **PromptQL (Hasura)** + Gemini 3.1 Pro | 60.00% | Yes — **co-created DAB**; enterprise; we outrank them |
| 11 | PromptQL + Opus 4.6 | 59.33% | " |
| 12 | Spacedock + Opus 4.6 | 58.28% | " |
| 13 | Claude Opus 4.6 ReAct | 54.68% | No — EPIC baseline |
| 14 | LabRat (Sonnet 4.6) | 51.38% | our prior entry |
| 15/18/22 | Oracle Forge (PaLM / Tenacious / Cohere) | 47.2 / 44.6 / 16.7% | No — FDE-training capstone |
| 16 | TOT-SQL Safeguard | 45.47% | No — technique; contamination saga |
| 17,19–21,23 | Gemini/GPT/Kimi ReAct | 45.5–10.4% | No — EPIC baselines |

**Reading it honestly:** of the 8 entries above us, three run **larger models than our Sonnet 4.6** (Opus 4.7/4.8, GPT-5.5), and the two closest (DataBridge #7 at +0.49pp, Pi #8 at +0.15pp) are a benchmark prototype and an indie demo. The "real product, general technique, comparable model" set above us is effectively **Altimate and MinusX** — and both beat us with techniques we can now copy.

---

## 2. Competitor dossiers (distilled)

### SCRIBE / Actioneer — #1 (81.85%) — *not the threat the rank implies*
A solo-built (`Suraj-gameramp`) research harness for an ICDM 2026 paper; "Actioneer" affiliation is **unverified**. Architecture is genuinely interesting — a three-role spec→executor→planner pipeline with a **6-verdict root-cause cascade** and **deterministic post-step verifiers** (auto-inspect + VERIFY tags catching empty-after-filter / row-blowup with no LLM call). But the 81.85% rests on: (a) a spec prompt hardcoding **11 numbered DAB-specific rules** (won't transfer to a new dataset), (b) a **prior leakage incident** (an 83.87% entry withdrawn; honest number fell to ~72%), and (c) an **oracle best-of-N submission builder** that validates every candidate against DAB's `validate.py` and packs the passing ones into the 5 "Pass@1" slots. **Verdict:** legitimately achieved but heavily fitted; no product, no users, nothing compounds.

### Spacedock (Recce) — #2 (74.33%) — *verification as organizational structure*
Recce is a funded ($4M seed, Heavybit) dbt data-diff / PR-review company; **Spacedock** is founder CL Kao's separate open-source (Apache-2.0, Go) multi-agent orchestrator — "nothing ships without a decision." The load-bearing idea: a **validation stage run by a *separate agent with a fresh context that cannot see the maker's reasoning*, which re-derives the answer from the databases**, plus a second adversarial tier that tries to *refute* the result (mutation-testing-as-review), with `feedback-to:` routing over 3 rounds and a full decision trail written to disk. Their DAB score decomposes as harness + model-bump + hints + a DAB-tuned "batch mode" (intra-dataset schema reuse across queries). **Watch item: razorback (their bench CLI) already has ADE-bench specs — they are coming for our primary benchmark.**

### Altimate Code — #3/#4 (71.71/68.22%) — *the best-funded validation of our thesis*
SF company (~$2M, Sierra), grew out of the 750k-install dbt-power-user VS Code extension. **Altimate Code** is an MIT TypeScript harness (OpenCode fork, 694★) with **~104 tools** — but the deterministic brain is a **closed-source Rust core** (`@altimateai/altimate-core`, prebuilt binaries, private source) doing sub-ms **column-level lineage** (parsed *live from SQL + catalog, not stale dbt manifests*), 2ms schema validation, and 19-rule SQL anti-pattern analysis. SaaS layer adds a **Context Graph + Decision Memory** (auto-extracts ADRs from merged PRs — *aimed squarely at our correction-harvesting moat*), FinOps, PII/governance, `data_diff` (12 warehouses). Their DAB technique = GPT-5.5 backbone + K=3 consensus + Sonnet-authored **AutoContext** doc — **but the three levers were never publicly ablated**, so the "AutoContext = +8pp" number our T1c chased **is not actually isolable from their PRs**. Same contamination episode as us (agnews HF-cache leak + a grep-permission bug), same disclose→sandbox→re-run→GT-rescore recovery. **Their column-level lineage + IDE-everywhere distribution are the real gaps vs us; their OSS-funnel-to-closed-core is the strategic difference.**

### MinusX — #6 (65.18%) — *our most direct competitor, and it hands us our ablation fixes*
YC S24, pre-seed, 2–5 people, **AGPL (same as us)**, tagline literally **"Claude Code for data."** Pivoted Feb 2026 from a Chrome-extension overlay to a full open-source agent-first BI platform. Their DAB technique is **production-general** (not overfit) and includes the two mechanisms that fix our null ablations:
- **Input-diversity consensus (DoubleCheck):** two Sonnet analysts get *different catalog sample views* (decorrelating errors at the input, not temperature); disagreement triggers up to 2 **argumentation rounds** where each sees the other's answer + justification. This is a **working existence proof at our exact benchmark** of why our identical-prompt consensus (T1a) came back within noise.
- **Join-verified, ID-tagged AutoContext:** every join annotation must survive a **live `COUNT(*)` probe** before it's persisted, with a "fewer, higher-signal annotations" self-check. This is why *their* LLM-authored context works where *our* T1c semantics pass ablated −3.7pp (we wrote unverified prose).
- Plus **handle-based results registered as live DuckDB tables** (cross-connection joins for free; feeds our T1d), lighter-model "+prompt" passes, and a **customer-facing Evals** product that makes context quality measurable per-customer ("context without measurement is hopium").

### DataBridge — #7 (61.37%) — *overfit prototype, two clean steals*
1-week-old, 1-person benchmark harness by an automation agency (Gavi Ventures). ~30–40% of its system prompt is **DAB-overfit** (literally hardcodes `'GetMe Bodied'`, an actual yelp question + its pipeline, the pancancer code-vs-name column) — notably hardcoding fixes for **the exact queries our own autopsy flagged**, which validates that our general parse-robustness levers target the right failures. Worth stealing, both deterministic and general: **join-key transform detection** (samples values, detects `12345 ↔ "CUST-0012345"`, and emits the *exact normalization SQL* — our `verify_join` probes match-rate but doesn't propose the fix) and **`normalize_text()` registered as a SQL function on every connection**.

### Pi Coding Agent — #8 (61.03%) — *not a company; a thesis*
The DAB entry is an indie dev's methodology demo on Mario Zechner's `pi` (MIT, 67k★, now owned by Earendil, which builds an *email* product — not a data agent). The transferable idea: a **richer pre-index** (schemas + sample rows + **value ranges + join-key analysis**) loaded before the first query, plus a "write one complete script, iterate on the traceback" loop. Nobody defends this #8 slot.

### PromptQL (Hasura) — #10/#11 (60.00/59.33%) — *the benchmark's co-author, and we outrank them*
Hasura **co-created DataAgentBench** (the benchmark is shaped around PromptQL's enterprise customers' problems) — so beating them on it is a marketing line *and* a calibration warning. Their real architectural idea is general and **unshipped by anyone else on the board**: separate **plan from execution** — the LLM writes a **Python program executed in a sandbox outside its context**, with **LLM "primitives" (`classify`/`extract`/`summarize`) callable per-row inside that deterministic code**, and typed **artifacts** for intermediate results. This directly attacks **bulk unstructured extraction (patents) — the one category the *entire* leaderboard fails, including PromptQL at 0%**. Their 2026 pivot ("corrections become reusable team skills") targets our memory moat from enterprise distribution; our counter is the open-source/terminal/single-operator wedge they don't reach.

### Oracle Forge & TOT-SQL — *not competitors, but instructive*
Oracle Forge = a Forward-Deployed-Engineer training capstone (three trainee teams). Instructive only as **convergent evolution**: even a bootcamp reference architecture ships AGENT.md + domain KB + a corrections log + a self-correction loop — the moat pattern is now table stakes. Cautionary tale: one team was re-scored 28%→12.8% for submitting raw tool-preview strings as answers and an **unbounded corrections log inflating tokens ~70×** — validating our strict-answer-extraction and the need to **size-budget any self-growing memory** (carry into T1d). TOT-SQL's "Safeguard" is just its backbone model (OpenAI's open-weight `gpt-oss-safeguard-120b`); the reusable kernel is a **policy-conditioned open-weight judge** for cheap, auditable verification.

---

## 3. Where LabRat wins, and where we're exposed

**We win on:**
- **Honesty/integrity posture** — fully-traced, single-model, structure-only-grounded, 0-contamination. Uniquely clean vs the overfit/oracle-selected top ranks.
- **End-to-end open (AGPL)** — vs Altimate's closed Rust core, PromptQL's proprietary engine, SCRIBE/DataBridge's non-products. Only MinusX matches our license.
- **Deterministic, GT-firewalled Cartographer** — a real, audited grounding artifact.
- **ADE-bench 80%** — still edges Altimate's claimed 74–78% and no one else has a public number. *Currently un-leveraged.*
- **The embeddable MCP/tool core** — a genuine product surface Pi's thesis explicitly rejects.

**We're exposed on:**
- **Verification** — Spacedock (#2) *is* a verification engine and MinusX (#6) ships working consensus; ours ablated to null. This is the single clearest capability gap, and we now know the fix.
- **Column-level lineage** — Altimate's headline differentiator; our T1b is still table-level with no `explain_lineage` tool.
- **Memory-as-moat is contested** — Altimate's Decision Memory and PromptQL's "team skills" are the *same bet with distribution*. Our extractors are still dormant (T2b unbuilt).
- **Distribution** — Altimate has 750k installed extensions + IDE-everywhere; MinusX has a viral blog + YC; we have a leaderboard entry.
- **Bulk unstructured extraction** — we (and everyone) fail patents; PromptQL's per-row primitives are the only credible answer and we don't have them.

---

## 4. Ranked feature recommendations (by impact)

Impact = benchmark lift × moat/product value × (1/effort). Each item names the competitor that **proves it works** and maps to our existing roadmap where applicable. Effort: S ≤ 2d · M ≈ days–1wk · L ≈ 1–2wk+.

### Tier 0 — Convert our failed ablations into wins *(highest ROI — existence proofs at our exact benchmark)*

| # | Feature | Proven by | Why it's the fix | Effort | Roadmap |
|---|---|---|---|---|---|
| **0.1** | **Input-diversity consensus + argumentation rounds** — run the K analysts on *different catalog sample views*, and on disagreement run ≤2 rounds where each sees the other's answer + justification and must defend/revise (not just modal vote) | **MinusX #6** | Our T1a consensus was within noise because trials were *identical prompts* → correlated errors. MinusX decorrelates at the input and adds cross-examination. Working at our benchmark, our exact model. | M (we own the judge + N-run plumbing on `feat/verification-layer`) | **T1a redo** |
| **0.2** | **Join-verified, conditional semantic Scent** — before persisting any LLM semantic annotation, require it to survive a live `verify_join`/`COUNT(*)` probe; author gotchas as *conditional* rules ("when the question asks for coded values use X, for labels use Y"), never unconditional | **MinusX #6 + Altimate #3** | Our T1c semantics ablated −3.7pp because we wrote *unverified, prescriptive* prose (music_brainz 2/9→0/9). MinusX verifies each claim; Altimate's identical unconditional-rule failure (pancancer 5/15) → their fix is conditional rules. | M (compose existing `verify_join` into `cartograph_prepass(with_semantics=True)`) | **T1c redo** |
| **0.3** | **Deterministic post-step verifiers** — auto-append result shape/dtypes/head after each query and fire zero-LLM `WARN/FAIL` tags on empty-after-filter, >2× row-blowup-after-join, groupby-that-grew-rows; plus a **question-derived constraint checker** (extract top_k / distinct / count / percentage from the question, validate the candidate answer) | **SCRIBE #1** | Catches the single most damaging data-agent bug (silent filter/join failure) with **no LLM cost**; generalizes our `verify_join`. Directly lifted SCRIBE's weak-domain scores. | S–M | new (Pillar 1) |

### Tier 1 — High-impact, roadmap-aligned

| # | Feature | Proven by | Why | Effort | Roadmap |
|---|---|---|---|---|---|
| **1.1** | **Column-level lineage as a deterministic `explain_lineage`/`trace_column` tool** — parse SQL live against the `Catalog` (via `sqlglot.lineage`), **not** dbt manifests (staleness argument is correct for agentic use) | **Altimate #3** (their #1 differentiator) | Our acknowledged T1b gap; `sqlglot` gives cross-dialect column lineage nearly free; we already hold `Catalog` in `ToolContext`. Highest-leverage roadmap item per the Altimate agent. | M | **T1b** (upgrades it) |
| **1.2** | **Pre-execution SQL validation with fuzzy-fix (`check_sql`)** — validate table/column refs from parsed SQL against `ctx.catalogs` and suggest Levenshtein-closest names *before* running | **Altimate #3** | Catches wrong-table/column in ~ms instead of after a failed warehouse round-trip; it's our repair-via-error_category lever moved *before* execution. | S | new (Pillar 1) |
| **1.3** | **Join-key transform detection in the Cartographer** — sample values, detect `12345 ↔ "CUST-0012345"`, emit the exact normalization SQL into the Scent doc (not just a match-rate) | **DataBridge #7** | Deterministic, GT-firewalled, attacks our cross-DB join failures (deps_dev_v1, crmarenapro); extends `verify_join` from *diagnose* to *prescribe*. | S–M | extends #25/Cartographer |
| **1.4** | **Value-ranges + stratified format-sampling in `profile_dataset`/Cartographer** — emit per-column min/max/cardinality/top-values, and deliberately surface rows with *unusual value structures* (embedded separators, delimiter-encoded paths, JSON-in-text) up front | **Pi #8 + SCRIBE #1 + DataBridge #7** | Cheaply exposes parsing traps (the github_repos text-count / deps `>`-path class our autopsy flagged) before the first query. Their entire pre-index edge minus the Opus tier. | S–M | extends Cartographer |

### Tier 2 — White space & moat *(differentiated; nobody or almost nobody on the board has these)*

| # | Feature | Proven by | Why | Effort | Roadmap |
|---|---|---|---|---|---|
| **2.1** | **Per-row LLM primitives** — an `llm_extract(rows, schema)` / `llm_classify` tool that fans out per-row mini-calls from within a deterministic loop, results bound outside model context | **PromptQL #10** (only) | Attacks **bulk unstructured extraction (patents) — the one category the *entire* leaderboard fails, PromptQL included at 0%.** Genuinely differentiated + general. Dovetails with T1d. | M | new (dovetails T1d) |
| **2.2** | **Plan-then-execute "program mode" + handle-based results** — opt-in mode where the model emits one script composing the existing 20 tools, executed in a sandbox, with results stored as fetchable handles **and live DuckDB temp tables** (enabling cross-connection joins), not flowed back through context | **PromptQL #10 + MinusX #6 + Pi #8** (convergent) | Determinism/repeatability + constant context on large working sets; the whole board is converging on "write one script." Extends the Context Ledger from *bounding* re-entry to *preventing* it. | M–L | **T1d** (extends) |
| **2.3** | **Git-versioned, team-inherited memory** — memory blocks live in `./labrat_maze/memory/`, size-capped, tagged, propagating on `git pull` | **Altimate #3** | Turns memory from personal state into a **team compounding asset** — our exact moat thesis, at team scale. We already dual-store Scent this way. | S–M | **T2b** (upgrades) |
| **2.4** | **Customer-facing evals** — `labrat evals`: question → assertion → expected value, run against the user's own DB (incl. "cannot answer" assertions) | **MinusX #6** | Makes context quality *measurable* per user — closes the correction-harvesting flywheel (we have ingestion, lack measurement) and is a wedge into teams. | L | **T2b v2** |
| **2.5** | **Decision-trail harvesting** — write every verdict + evidence + reason next to the work item (`decisions.jsonl` beside `agent_tool_calls.jsonl`); harvest *recorded verdicts*, not just edit-corrections | **Spacedock #2** | Auditability as artifact; feeds T2b with a second, richer correction source. | S | extends T2b |

### Tier 3 — Cheap wins & credibility

| # | Feature | Proven by | Effort |
|---|---|---|---|
| **3.1** | **`normalize_text()` registered as a SQL function on every connection** (diacritic/whitespace/case-insensitive) — general entity-matching lever | DataBridge #7 | S |
| **3.2** | **Engine-enforced read-only "Analyst" mode** — SELECT-only in the registry/dispatch layer (not prompt); "safe to point at prod" checkbox | Altimate #3 | S |
| **3.3** | **Mechanized taint-audit as a scoring gate** — upgrade `_detect_contamination` from *detect* to *gate + classify* (exit-code contract, `taint.json`), run between run and score in `eval_dab.py` | Spacedock #2 | S |
| **3.4** | **Schema compaction for many-identical-table DBs** — "N tables share structure → one representative + name list" (stockmarket/stockindex 1000s of per-ticker tables) | DataBridge #7 | S |
| **3.5** | **`top_n_with_ties` awareness** — a one-line lever: "LIMIT N silently truncates ties" | DataBridge #7 | XS |
| **3.6** | **Multi-interpretation surfacing + per-trial interpretation rotation** — planner enumerates 2–4 readings; when genuinely split, rotate the chosen reading across the N consensus trials | SCRIBE #1 | M (composes with 0.1) |
| **3.7** | **Root-cause verdict cascade** — replace the binary sufficient/insufficient judge with a typed taxonomy (spec_wrong / executor_wrong / blind_spot / NA / answer) routing recovery | SCRIBE #1 | M |

### Strategic (non-code)

- **Untried DAB lever = backbone swap.** Our own ablations show consensus/semantics are noise-to-negative *on Sonnet*; the one lever from the winners' playbook we haven't tried is a **bigger/different backbone** (Opus/GPT-5.5) — we have the codex provider wired, and the in-flight ablation already includes a **Sonnet 5 vs 4.6 arm** that will quantify the model-only delta.
- **Publish the ADE-bench methodology loudly, now.** Spacedock's razorback already has ADE specs; if they post an ADE number, "verification-harness beats tool-rich agent" becomes their story on *our* 80% benchmark. Pre-empt it.
- **Marketing lines that write themselves:** "#9 on a single mid-tier model, fully traced, 0 contamination — above every honest single-model entry"; "we beat DataAgentBench's own co-creator (PromptQL) on their own benchmark"; "the only top-10 entry you can fully audit."
- **Watch triggers:** Spacedock→ADE-bench; Altimate's Context Graph / MinusX's Evals maturing (both target our memory moat); "DataBridge Cloud" or MinusX enterprise traction.

---

## 5. How this reshapes the roadmap

1. **Verification (T1a) is not dead — it was mis-implemented.** Re-open it with input-diversity + argumentation (0.1) and separated-context re-derivation (Spacedock). This is now the **highest-confidence benchmark lever** because two teams above us prove it, and we have the scaffolding.
2. **Semantic Scent (T1c) is not dead — it was un-verified.** Re-test with join-verified + conditional authoring (0.2). Altimate and MinusX both make LLM context work; the difference is per-claim verification.
3. **The parse-robustness levers currently ablating are corroborated.** DataBridge's overfit prompt hardcodes fixes for the same queries — our general levers target real, competitor-confirmed failures. (Await the running ablation's stratified result before merging.)
4. **T1b (lineage) is validated as high-value** and should adopt the *live-parse-not-manifest* insight (1.1).
5. **T1d (Context Ledger) gains two extensions:** handle-based DuckDB-table results (2.2) and per-row LLM primitives (2.1) — the latter is genuine white space against the whole field.
6. **T2b (correction-harvesting) is both validated and contested** — ship it, and add git-versioned team memory (2.3) + evals (2.4) to out-execute Altimate's Decision Graph and PromptQL's "team skills" on the open/terminal wedge they don't serve.

**One-line strategy:** the top of the board is winning with *bigger models + benchmark fitting*; the durable game for a single-mid-tier, fully-open agent is **verification done right (Tier 0) + deterministic data-intelligence tools (Tier 1) + the per-row-primitive/program white space (Tier 2)** — then the compounding, measurable, team-shared memory moat (T2b+) that the funded incumbents are also racing toward but can't match on openness.
