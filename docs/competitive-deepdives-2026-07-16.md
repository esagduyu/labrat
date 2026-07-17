# DAB top-competitor deep dives — 2026-07-16

Six trace-level submission audits (one agent each, full PR + trace + product research):
Alkera (pending PRs #69/#70), SCRIBE #67, Spacedock #63, Altimate #53, AgenDA #68,
MinusX #50. Companion to `competitive-analysis-2026-07-16.md`. Every claim below is
grounded in primary artifacts (PR bodies/threads, shipped trace archives parsed in
full, public source where AGPL/Apache, vendor sites); source URLs in each digest.

---

## Synthesis — what the whole top table teaches

**1. The #1 lever on this benchmark is a wrong-answer taxonomy, and it now has three
independent proofs.** SCRIBE's RULES 1–10 (spec-first, answer-shape/grain defaults,
`min_rows` hard floor), Alkera's v11 system addendum (shape/grain/delivery contract,
"FINAL ANSWER:" discipline), and AgenDA's per-leaf "source contracts" (table/columns/
grain/filter/selection pinned per plan node) are three drafts of the same document —
held by leaderboard positions #1, pending-#1, and #4. This is exactly our failure-
autopsy levers B (verbatim emission) and D (column disambiguation), pre-identified
on 2026-07-02 and never built. **Highest-priority adoption, one coherent lever pack:**
shape/grain addendum + harness-enforced answer-shape floor + interpretation
enumeration before execution + mandatory filter-then-verify + an explicit
"Not Applicable is a legitimate answer" path. All benchmark-safe (process, not
content); ablatable on the 45-key subset harness on both backbones.

**2. Nobody burns LLM tokens on bulk classification — except us.** SCRIBE: regex
alternation. Alkera: agent-derived deterministic keyword rule ("derive one rule,
state it, apply uniformly, never sample"). Spacedock: Python token-frequency scorer.
AgenDA: a local zero-shot NLI model (deberta-v3, weights-only, maintainer-accepted).
Their agnews scores: 0.35–0.55. Ours: 0.25 with two queries quota-dead at 0/10.
**Adoption path (three tiers):** (a) prompt-lever the derive-a-deterministic-rule
pattern; (b) point our existing `llm_classify_model` plumbing at a cheap tier
(MinusX proves Haiku-for-rows); (c) add a local-NLI backend to the `llm_classify`
engine (AgenDA precedent makes it disclosure-safe) — deletes the quota failure mode.

**3. Verification: the marketing exceeds the measurement everywhere except MinusX.**
Spacedock's adversarial rejection loop fired **zero times in 60 sessions** (all
corrections inline). Altimate's K=3 consensus nets **+3 trials of 270** (+1.1pp) —
empirically why our T1a ablation was noise. The one working design is MinusX's
debate: K=2 with **data-view diversity** (different catalog samples per analyst, not
prompt phrasing), mandatory justification on submit, disagreement → cross-feedback
re-derivation with full history, independent cheap judge. Round stats prove rescue
(72% pass when round-1 agree, 53% on round-3 trials). AGPL source is ~250 readable
lines (`double-check-benchmark.ts`). **Port as `consensus_mode="debate"` in
`_run_trial_verified`** — their Sonnet entry beats our Sonnet entry +4.3pp with no
knowledge layer, so their lever and our grounding lever are orthogonal and plausibly
stack.

**4. Our no-shell registry is now a proven differentiator, not a handicap.** Every
competitor ships a shell/Python escape hatch; two of the top three had leakage
episodes through exactly that hatch (Altimate: subprocess inheriting the HF cache +
a grep matcher bug; SCRIBE v1: HF cache read + gold values in prompts). Both were
caught by the maintainers' trace audit, whose method is now known: **flag only leaks
that were obtained AND drove the passing answer.** Our MCP-only/no-shell path closes
these vectors by construction — say it plainly in PR #72 and positioning. Worth
adopting from Spacedock as defense-in-depth: a runtime PreToolUse-style hard gate
(block at execution) complementing our audit-time taint gate v2.

**5. Per-backbone asymmetry is universal, and only we measure it.** Spacedock same-
harness: GPT-5.5 74.33 vs Opus 4.8 67.21 (GPT-favoring, −7.1pp on Opus — the mirror
image of our Cartographer-regresses-on-GPT finding). Alkera controlled pair: Opus
80.44 → Fable 5 83.28 (+2.84). Altimate confounded model with AutoContext and can't
attribute. "Provider-aware grounding, measured per backbone" is uncontested
positioning.

**6. The hard tail is shared misery and the remaining frontier.** deps_dev_v1 ~0.50
and github_repos 0.45–0.60 for everyone including the #1; agnews only yields to
deterministic classification. Whoever cracks the idiosyncratic-GT tail owns the
board. Our autopsy already names the levers (A: free-text grounding; C: entity
resolution — MinusX's lexical-vs-semantic decision table targets exactly C).

**7. Grounding beats planning.** AgenDA's flat-in-practice HTN (3.4 nodes, 7 replans
/270, 4.8 LLM calls/trial) loses to us by 5pp precisely on entity-heavy datasets
(music_brainz 0.53 vs our 0.93; yelp, googlelocal, crm all ours) — plan-once/
code-once leaves no room for mid-course discovery. Our iterative explore+Scent loop
is the right architecture; their one win (stockmarket 0.96 vs 0.80) is a
**difflib-style name normalization** on answer emission we should copy (it is
output formatting, validator-safe, and explains our stockmarket:4 five-trial miss).

### Ranked adoption backlog (across all six dives)

| # | Item | Source proof | Fits |
|---|---|---|---|
| 1 | Shape/grain/delivery lever pack (addendum + min_rows floor + source contracts + filter-then-verify + NA path + interpretation enumeration) | SCRIBE #1, Alkera pending-#1, AgenDA | autopsy levers B+D; `_dab_lever_lines` |
| 2 | Bulk-classification tiering: deterministic-rule prompt → cheap-model `llm_classify_model` → local-NLI backend | Alkera/Spacedock/SCRIBE/AgenDA/MinusX | deletes agnews quota deaths |
| 3 | Debate consensus (`consensus_mode="debate"`) with data-view diversity + justifications | MinusX AGPL source | `_run_trial_verified` scaffolding exists |
| 4 | Deterministic "Operational Rules" salience header atop Scent docs | Altimate (format proven; their LLM-authored content own-goaled) | T1c-risk-free; Cartographer computes the inputs already |
| 5 | difflib nearest-name normalization on answer emission | AgenDA stockmarket 0.96 | stockmarket:4 fix |
| 6 | Runtime hard gate (PreToolUse-style) + per-trial `_prompt.md` + cost telemetry in trace bundle | Spacedock, Alkera, AgenDA | taint v2 complement; disclosure upgrades |
| 7 | Longer per-trial timeouts for hard-tail tasks | Alkera 2h, MinusX 26-min medians | our 20-min cap is the outlier |

### Positioning synthesis

Ours to own: **provider-aware grounding measured per backbone** + **integrity by
construction** (no-shell registry, byte-rebuildable packages, independent audit,
zero benchmark-fitted rules — contrast SCRIBE's footnote ⁶) + **cost discipline**
(Spacedock ties us at ~10× the tool/token cost; AgenDA pays $216 full-freight for
−5pp; our 71% cache on a subscription). Threat ranking: **Alkera #1** (wedge overlap
+ distilled addendum + real product), MinusX #2 strategically (AGPL, "Claude Code
for data" tagline collision, benchmark-extension lobbying with maintainers),
Spacedock/SCRIBE benchmark-strong but product-orthogonal, AgenDA academic.

### PR #72 implications

Maintainer audit = trace reading focused on "obtained AND drove the passing answer";
our disclosure set exceeds every accepted entry's; precedent is footnotes not
rejections. The SCRIBE q4-regrade curiosity (their accepted agnews row disagrees
with a current-GT regrade) is a reminder that accepted numbers and current-GT
regrades can diverge per-query — do not panic if our re-validated number moves a
task or two.

---

## Per-competitor digests

### SCRIBE (Actioneer) — #1, 81.85%, PR #67 (Opus 4.7 xhigh)
Spec Agent → Executor (Python REPL, 16.6 calls/trial — leanest top-tier) → mid-run
planner; backreview verdict cascade incl. the quietly-lethal `NA_CONFIRMED`.
"Overfit + oracle-selected" hypothesis **partially refuted**: system prompt
byte-identical across 270 runs, single 9-hour campaign, no spec editing, only 4/270
`interpretation_confidence: split` specs — but organic per-run spec variance spreads
interpretations across trials (yelp:1 = five readings in five runs), legally farming
pass@1 partial credit. History: original 83.87% caught with gold-values-in-prompts +
HF-cache leakage (PR #57), corrected transparently to 71.99%, rebuilt clean to
81.85% (+7.25pp of which is the shared patents GT resync). Footnote ⁶ = DAB-fitted
domain rules in the planner prompt (the board's only methodology caveat). One
observation: their executor can *list* sibling harness workspaces (incl. other
queries' `answer.txt`) — zero reads observed; exposure-not-obtained. Company:
Actioneer = enterprise AI-transformation platform (Bitkraft/Sony-backed); benchmark
harness ≠ product; buyer isn't ours. **Verdict: fragile ranking, real engineering —
mine the rules, wedge the footnote.**

### Alkera (YC S26) — pending #1, 83.28%, PR #70 (Fable 5 + Opus 4.8 fallback)
See the 2026-07-16 standalone dive. Headlines: model tier only ~3pp (their own
controlled pair); ~6pp is the v11 addendum + deterministic agnews classifier + 2h
timeouts; KB is per-trial-ephemeral on DAB (verified empty at every run start);
Fable 5 refuses PANCANCER (cancer-genomics) wholesale → disclosed dataset-level Opus
fallback; `python3` is their top bash command (860 calls) despite METHODS saying
shell is Mongo-only; review subagents: 4 uses in 270. Closest wedge overlap of any
competitor ("Claude Code for data engineers"): lineage + team KB + governance.

### Spacedock (Recce) — #2, 74.33%, PR #63 (GPT-5.5 via Codex CLI)
First-officer/ensign skills architecture on stock Codex (dual runtime adapters:
Claude Code AND Codex — market converging on our embeddable-core thesis). The
advertised adversarial rejection gate **never fired** (0 cycle-2 reports / 60
sessions); verify is substantive but inline. Heaviest harness profiled: ~480 shell
calls and 200–240k first-officer tokens per dataset-session — ties us at ~10× cost.
agnews = deterministic Python scorer (0.45). We beat them on PATENTS (0.67 vs 0.40,
their worst), crm (0.82 vs 0.74). Runtime PreToolUse hook blocks answer-key reads /
HF / pip at execution time — clean integrity, architecture worth copying as
defense-in-depth. Same-harness Opus entry −7.1pp = GPT-favoring asymmetry, free data
for our positioning. Apache-2.0 source public. Expect drift up with model upgrades,
not method leaps.

### Altimate Code — #3 on board at 71.71% (validator-relaxation re-score; honest run = 63.18%), PR #53 (GPT-5.5 + Sonnet AutoContext)
K=3 consensus measured across all 810 sub-trials: 185 unanimous / 46 majority / 38
none; net **+6/−3 = +3 trials (~1.1pp)** — our null T1a result explained. AutoContext
= Sonnet-authored per-dataset doc whose ranked **"Operational Rules"** preamble is
the real innovation (adopt the *format*, populate deterministically — their
LLM-authored unconditional histology rule own-goaled PANCANCER q2/q3, the exact T1c
failure mode we measured). `bash`(python) is their #1 tool; 263 uses of a
`validate_shape` CLI = another answer-shape gate. Their PR thread = the maintainer
audit playbook we'll face (and their two leak vectors are impossible in our
registry). PATENTS 0/15 honest (refused per-dataset tuning). TypeScript runtime,
opencode lineage.

### AgenDA — #4, 69.11%, PR #68 (Opus 4.8; anonymous pre-publication research group)
Richest trace format on the board (plan trees, per-agent histories, formalizer
logs, per-trial cost: $216.40 total, 4.8 LLM calls/trial, **zero caching**). HTN is
flat in practice (3.4 nodes, depth 1, 7 replans/270). Code-over-dataframes: leaves
emit pandas against pre-materialized frames; injected `agenda_assert_nonempty`
(617 uses) = fail-loud empty-result guards. Local deberta NLI for bulk
classification (agnews 0.35, zero tokens — maintainer-accepted precedent for a
local-model backend). We +5pp on a far cheaper model, winning exactly the
grounding-heavy datasets; they win stockmarket 0.96 via difflib name normalization
(copy it). Their profiler is prompt-stuffed Cartographer-lite: no verified joins,
no observed values, no retrieval.

### MinusX — #7, 65.18%, PR #50 (Sonnet 4.6 + GPT-5.5-mini judge + Haiku rows)
The debate-consensus blueprint (see synthesis §3) in AGPL source; 383M tokens
(~1.4M/trial), 26-minute median trials. `ExploreDataset` = our llm primitives on
Haiku with a lexical-vs-semantic decision table in-prompt (targets our lever C).
AutoContext advertised but vestigial in the submitted run — their score is
consensus + cheap-model rows, no knowledge layer → orthogonal to our grounding.
Found a real GT bug (music_brainz q2), volunteered the correction that *lowered*
their score — credibility + cozy maintainer relationship; lobbying for
dashboard-building benchmark extensions (their roadmap telegraphed). Repo tagline:
**"It's Claude Code for data"** — direct positioning collision. Watch their
report-agent/Viz V2 (Cheese-adjacent).

---

*Method note: one agent per competitor, each parsing the full shipped trace archive
(60–270 sessions each), PR threads, and public source/sites. Alkera's dive is in the
session record of 2026-07-16; this doc carries its digest. Nothing in any dive
involved non-public data.*
