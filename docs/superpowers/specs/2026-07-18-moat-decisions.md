# Moat Build-Out — Decision Doc (Track 2, autonomous planning)

**Status:** Decided (2026-07-18) — recorded as trade-offs per Ege's delegation ("make
your decisions and record them as trade-offs"); flagged items deserve a later look.
**Author:** Claude Fable (Track-2 planning fork)
**Companion docs:** [`2026-07-18-moat-buildout-plan.md`](../plans/2026-07-18-moat-buildout-plan.md)
(the master plan), the three mini-specs dated 2026-07-18, and the pre-existing
[`2026-07-16-customer-evals-design.md`](2026-07-16-customer-evals-design.md).

Read this doc first: every decision made during Track-2 planning is here, each with
options, trade-offs, rationale, reversal cost, and a **ratify?** flag (🔴 = please
double-check; 🟡 = worth a glance; 🟢 = low risk, informational).

---

## Part A — Customer-evals D1–D6 ratifications

### D1 — Naming: CLI `labrat evals`, package `src/labrat/evals/` — **RATIFIED as recommended** 🟢
- **Options:** `evals` (recommended) vs `goldens`.
- **Trade-off:** `evals` is what users will type and search for; the cost is one-letter
  adjacency with `src/labrat/eval/` (benchmarks). `goldens` is unambiguous but
  undiscoverable and doesn't cover non-golden check kinds (`text`, `value`).
- **Why:** discoverability beats internal ambiguity; the ambiguity is ours, not the
  user's. Mitigation baked into the plan: module docstrings in both packages
  cross-reference each other, and the plan's M3 includes a lint-style test asserting
  no import from `labrat.evals` into `labrat.eval.benchmarks` (or vice versa).
- **Reversal cost:** trivial pre-ship; a breaking CLI rename post-ship (medium).

### D2 — Free/paid split: local loop FREE, team-scale PAID — **RATIFIED, amends the commercial memo** 🔴
- **Options:** (a) evals fully paid (the #24 memo's original slotting); (b) local loop
  free / CI + trends + governance + distribution paid (the spec's recommendation, adopted).
- **Trade-off:** (b) gives away single-user value forever — a paywall ratchet you
  cannot re-tighten without community damage. In exchange: the PLG wedge (an analyst
  can prove LabRat's accuracy on their warehouse before any purchase), consistency
  with the team-Scent-free / dbt-CI-paid precedent, and enforcement simplicity — the
  paid pieces (CI wiring, history/trends UI, suite governance, Slack/report
  distribution) are naturally workflow/server-side, so the Apache-2.0 core needs no
  license gates.
- **Why:** the moat thesis needs eval *adoption* to close the harvest↔eval flywheel;
  a paid gate on the loop starves the flywheel precisely where it compounds.
- **Reversal cost:** HIGH — effectively irreversible once shipped free. **This is the
  decision most worth Ege's explicit confirmation before M3 ships.**

### D3 — `n_trials` default 3 — **RATIFIED**, plus new sub-decision D3b 🟡
- **D3b (new): per-case scoring semantics.** A case's score = pass **rate** over its
  trials; the suite **exit code is strict** (exit 1 if any case rate < 1.0), and the
  report distinguishes **failing** (rate 0) from **flaky** (0 < rate < 1).
- **Options:** strict all-trials-pass as the case verdict (brittle for stochastic
  agents, but honest for a regression gate) vs rate-only (hides flakiness) vs this
  hybrid (adopted).
- **Why:** a regression gate must surface flakiness rather than average it away
  (the smoke-baseline lesson), but labeling a 2/3 case simply "failed" destroys the
  diagnostic signal users need to decide whether to fix the eval or the agent.
- **Reversal cost:** low (display/exit-code logic only).

### D4 — No LLM-judge in v1 — **RATIFIED** 🟢
Deterministic-only keeps `validate` and any future CI path free and drift-proof.
v1.1 adds an `llm_judge` check kind through the existing `validations.ValidationChecker`
shape, never on the free-CI path. Reversal cost: none (purely additive later).

### D5 — Harvest→eval-candidate wiring in v1.1, not v1 — **RATIFIED** 🟢
The standalone loop must not be gated on the harvest surface; the wiring rides the
existing `HarvestReviewScreen` + `harvest_opt_in` gate and lands one milestone later
(plan M4). Reversal cost: none — v1 ships `created_from`/`source` fields ready.

### D6 — Store at `labrat_maze/evals/` — **RATIFIED** 🟢
One directory = "the team's LabRat knowledge **and** its acceptance tests," versioned
together — which is what makes the (paid) CI story coherent later. The alternative
top-level `labrat_evals/` keeps the Maze purely knowledge but splits the colocate
story and doubles the git-pairing surface. Reversal cost: low pre-adoption (a move +
loader path change), medium after teams commit suites.

---

## Part B — Provider-conditional defaults (new decisions)

### PD1 — Defaults live in a shipped code table, not in Profile — 🟡
`src/labrat/agent/defaults.py`: `RECOMMENDED_DEFAULTS` keyed by model-family glob
(`claude-sonnet-*`, `claude-opus-*`, `gpt-5.*`, …) → a frozen `RecommendedDefaults`
model (effort, verify, hybrid_retrieval, ledger, classify-tier hint) + `receipts`
(doc-path + one-line claim) + `measured_on`. **Options:** per-profile persisted
defaults (drifts stale, invisible to updates); a remote-fetched table (violates
local-first); shipped code table (adopted — versioned with the code that was
measured, updated by the same PR that lands an ablation).
**Reversal cost:** low.

### PD2 — Recommendations are surfaced and one-tap applied, never auto-applied — 🔴
- **Options:** (a) auto-apply when a profile field is "unset"; (b) surface as
  recommendation chips in Settings (+ `labrat defaults show` CLI) and only write
  profile fields when the user taps apply (adopted).
- **Trade-off:** (b) means some users run un-recommended configs; (a) means agent
  behavior silently differs per provider — a support nightmare, a benchmark-safety
  hazard (any code path that resolves defaults could leak into eval flows), and
  `Profile`'s explicitly-defaulted booleans have no "unset" state anyway (a schema
  change we don't want on a frozen model).
- **Why:** the feature's value is the *receipts* (measured, cited claims) — trust
  comes from showing your work, not from silently steering. This also keeps the
  brand claim honest: "we publish per-backbone measurements and let you apply them."
- **Reversal cost:** (b)→(a) is easy later if demanded; (a)→(b) after complaints is
  reputation-costly. **Flagged because it shapes product feel.**

### PD3 — Receipt format: repo-relative doc path + claim string + date — 🟢
Rendered as an expandable "why" in Settings and printed by the CLI. No URLs (docs
ship with the repo; no network). New ablations update the table + receipts in one PR.

---

## Part C — Scent "Operational Rules" header (new decisions)

### OR1 — Deterministic-only content; harvested/LLM content stays OUT — 🟢
The header is populated exclusively from facts the Cartographer already computes or
can compute with bounded probes (join-normalization transforms with exact SQL,
sentinel-string detection in stringy columns, mixed-format date probes,
shared-structure warnings). Harvested corrections keep flowing to `## Gotchas` via
the human-gated harvest path; no LLM authoring. **Why:** Altimate's own-goal
(an LLM-authored unconditional rule breaking 2 queries) is the documented failure
mode, and our own T1c ablation measured LLM-authored semantics net-negative. The
*format* is what's proven; determinism is our differentiator.
**Reversal cost:** none (adding sources later is additive).

### OR2 — Ranking: fixed category weights, then blast radius, then alpha — 🟢
`join-correctness > data-dirtiness (sentinels/mixed formats) > structure warnings`,
within category by affected-table count desc, then alphabetical; hard cap 8 bullets.
Deterministic and explainable; no scoring model. Reversal cost: none.

### OR3 — Default ON in the TUI first-connect pre-pass, OFF on DAB until ablated — 🔴
- This is the one Track-2 feature that *would* touch the leaderboard path if enabled
  there: the DAB `--agent-cartograph` pre-pass generates Scent, so a new section
  changes retrieval content on the benchmark. Therefore: `cartograph_prepass(...,
  operational_rules: bool)` — TUI passes True, the DAB driver passes False until a
  subset ablation clears it (at which point it becomes a *declared lever*, candidate
  for the Track-1 campaign's next arm).
- **Proof obligation:** with the flag False, generated Scent bytes are identical to
  today's (test), and no eval path sets it True (grep-test like `active_maps`').
- **Why flagged:** the alternative (ship it into DAB immediately) might gain score
  but would put an unablated change on the leaderboard path — exactly what our
  process forbids. Flagged so Ege knows score is deliberately left on the table
  pending ablation.

---

## Part D — Correction clustering (new decisions)

### CC1 — Two-stage clustering: existing scope buckets, then semantic sub-clusters — 🟢
Keep `cluster_corrections`' scope clustering (table/thread) as stage 1; stage 2
sub-clusters within a scope by greedy agglomerative cosine similarity over
`maze/embedding.py` vectors. **Options:** replace scope clustering wholesale
(loses the domain-routing semantics HarvestReview depends on) vs layer beneath it
(adopted). Reversal cost: none.

### CC2 — Threshold θ=0.80 join, 0.95 near-duplicate collapse; fail-open — 🟡
Corrections with cosine ≥ 0.80 merge into one draft section; ≥ 0.95 collapse to the
earliest text. Embedder unavailable (extra not installed, model missing) → stage 2
skipped entirely, current behavior byte-identical — same fail-open contract as
hybrid retrieval. θ values are config constants with tests pinning behavior at the
boundaries; tuning them later is cheap. **Flagged 🟡** only because thresholds are
judgment: if clusters feel too greedy/too shy in real use, θ is the dial.

### CC3 — Deterministic cluster identity — 🟢
Cluster order = earliest member timestamp then id; static embeddings are
deterministic, so the same corrections always produce the same drafts (review-surface
stability + testability). Reversal cost: none.

---

## Part E — Sequencing & scope decisions

### SEQ1 — Milestone order: hygiene → ops-rules → defaults → evals v1 → evals v1.1 → clustering — 🟡
- **Options:** evals-first (it's the keystone) vs small-wins-first (adopted).
- **Trade-off:** evals-first delivers the flagship 4–6 days sooner; small-wins-first
  lands the two ~2-day features that (a) improve the retrieval the eval suite will
  then measure and (b) make the provider-aware brand claim shippable *this week*,
  while the hygiene pass fixes the two RRF items any demo would trip over
  (per-call model load, first-use download inside a tool call).
- **Why:** momentum + the attribution story — when the first eval baselines land,
  Scent already carries Operational Rules, so early history lines measure the
  system users will actually run. Reordering later costs nothing (no hard deps).

### SEQ2 — PreToolUse-style runtime gate goes in the hygiene milestone, DAB-driver-only — 🟢
Execution-time blocking (Spacedock's architecturally-stronger pattern) reusing
`taint_structural.classify_file_source` — deny at dispatch on the DAB labrat-agent
driver only; product paths untouched. Small, high-value, reuses shipped code.

### SEQ3 — Clustering waits for correction volume — 🟢
M5 is last not because it's hard but because its value scales with harvested-
correction volume that only real usage accrues; nothing blocks pulling it forward.

---

## The five most consequential decisions (summary)

1. **D2** — free local eval loop (amends the commercial memo; irreversible ratchet) 🔴
2. **PD2** — recommendations never auto-apply 🔴
3. **OR3** — Operational Rules off on DAB until ablated (score left on table by process) 🔴
4. **SEQ1** — small-wins-first ordering 🟡
5. **D3b** — flaky-vs-failing eval semantics with strict exit codes 🟡

_Regenerated 2026-07-23 from transcript after accidental deletion._
