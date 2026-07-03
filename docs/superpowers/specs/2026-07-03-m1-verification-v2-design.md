# M1 — Verification-v2 — Design

**Date:** 2026-07-03
**Status:** Design — awaiting user review before writing-plans
**Branch:** `feat/verification-v2` (proposed)
**Source:** Milestone M1 of `docs/superpowers/plans/2026-07-03-competitive-build-milestones.md`; competitive analysis `docs/competitive-analysis-2026-07-03.md` §4 Tier 0 (0.1 input-diversity consensus + argumentation, 0.3 deterministic post-step verifiers).

## Motivation

Our shipped K-of-N consensus (T1a) ablated to **null** (within noise at n=24). The competitive deep-dive diagnosed exactly why: the K sub-runs get **identical prompts**, so their errors are correlated and the modal vote just reconfirms the same mistake. **MinusX (#6) ships the working version at our exact benchmark**: the K analysts get **different catalog sample views** (decorrelating errors at the input, not temperature) and, on disagreement, run **argumentation rounds**. Separately, SCRIBE (#1) uses **zero-LLM deterministic post-step verifiers** that catch the single most damaging data-agent bug (silent filter/join failure) for free.

M1 rebuilds verification as the version competitors above us prove works. It is the roadmap's highest-confidence benchmark lever, and we already own most of the scaffolding (`agent/verification/`, `DabSuite._run_trial_verified`).

**Non-negotiables:** default-off, independently flag-gated per unit (so each is separately ablatable), fail-open everywhere (an unparseable judge verdict / a failed sub-run never traps the loop), GT-firewalled (the Scent variants sample DB rows only), and the equivalence judge routes to the `claude-code` provider on the claude-mcp path (Max-plan OAuth strips `ANTHROPIC_API_KEY` — the shipped `_verify_llm_fn` lesson).

## Current-state anchors (code-verified 2026-07-03)

- `agent/verification/consensus.py::choose_modal(answers, *, question, llm_fn) -> (idx, low_confidence)`; `agreement.py::answers_agree(a, b, *, question, llm_fn) -> bool`. Both fail-open.
- `DabSuite._run_trial_verified` (suite.py:613): runs `_run_once(i)` → `subrun{i}/`; K sub-runs → `choose_modal`; `--agent-reverify` does one fresh re-run (`_run_once(900)`) + `answers_agree` + one reconcile round. `_verify_llm_fn` (594) routes the judge to claude-code on claude-mcp. Metadata persisted to `verification.json`.
- `_run_once(i, extra="")` calls `_dispatch_driver_once(task, db_config, sub, extra_instructions=extra)` (732). `extra_instructions` is appended to the opening prompt — the **framing-rotation hook already exists**.
- The claude-mcp maze/Scent dir is set inside `_run_trial_claude_mcp` (via `LABRAT_MAZE_DIR`, hermetic `HOME`); the Cartographer pre-pass runs in `_run_cartographer` → a per-dataset Scent store.
- Cartographer sampling: `build_dimensions` (cartographer.py:118) uses `SELECT DISTINCT col ... LIMIT cap` (deterministic); example-row/format sampling added in M0. To vary per variant, sampling must take a **seed**.
- **Separated-context re-derive is already present:** the reverify path re-dispatches a *fresh* `_run_once(900)` that never sees the primary's transcript. M1 hardens/tests this rather than rebuilds it.

## The three units

### Unit 1 — Input-diversity consensus *(the headline fix)*

Make the K consensus sub-runs differ two ways so their errors decorrelate; keep `choose_modal` for the vote.

**(a) Sample-view diversity — K Scent variants.**
- `generate_scent`/`build_dimensions` (and the M0 value/format sampling) take a `variant_seed: int` (default `0` = current deterministic behavior). Seeded sampling: replace the plain `LIMIT cap` distinct/example scans with a reproducible seeded sample — DuckDB `... ORDER BY hash(CAST(col AS VARCHAR) || '<seed>') LIMIT cap` (deterministic per seed, different per seed). Low-cardinality dimension columns naturally yield identical values across variants (nothing to diversify there); higher-cardinality example/format rows diverge. GT-firewall unchanged (samples DB rows only; the doc still runs `audit_scent_doc`).
- `cartograph_prepass` gains a `variants: int = 1` param: when `>1`, it generates `<scent_dir>/variant0/ … variant{K-1}/` (variant `i` uses `variant_seed=i`). `_run_cartographer` generates K variants when consensus + diversity are on.
- Sub-run `i` uses `LABRAT_MAZE_DIR = <maze_root>/variant{i}`.

**(b) Framing rotation.**
- A fixed neutral list `_CONSENSUS_FRAMINGS: list[str]` (e.g. "Pay extra attention to filters and null-handling.", "Double-check join grain and whether the top value ties.", "Confirm units, magnitudes, and that aggregates aren't double-counting."). Sub-run `i` gets `framings[i % len]` appended via the existing `extra_instructions` hook. Content is process-only (benchmark-safe).

**Wiring.** `_run_once(i, *, diversity_index=i)` → `_dispatch_driver_once(..., diversity_index=i)` → `_run_trial_claude_mcp` selects the variant maze dir + appends the framing line. `diversity_index=None` (default) → byte-identical to today. Gated so diversity is on only for diverse-consensus arms.

### Unit 2 — Argumentation rounds *(MinusX's second mechanism)*

After the K diverse sub-runs and `choose_modal`, if the vote has **no clear majority** (`low_confidence` True, i.e. a tie/split), run bounded cross-examination:
- For up to `argue_rounds` (default 2), re-dispatch each sub-run with the *other* sub-runs' `(answer, justification)` appended: "Other analysts concluded: … Defend your answer with evidence, or revise it." (`justification` = the sub-run's `final_text`, which carries its reasoning.)
- Re-run `choose_modal` after each round; stop early once a majority emerges.
- Fail-open: after `argue_rounds` with no majority, return the primary sub-run's current answer flagged `low_confidence`. All rounds recorded to `verification.json`.
- New flag `--agent-argue-rounds N` (default 0 = off); only meaningful with `--agent-consensus K`.

### Unit 3 — Deterministic post-step verifiers *(zero-LLM, orthogonal, SCRIBE)*

**(a) `run_sql` success enrichment** (`agent/tools/run_sql.py`). On a successful SELECT, add to the output (deterministic, no LLM):
- `result_shape`: `(rows, cols)`.
- `warnings: list[str]`: fire on the single-query-computable danger signals — **empty result** when the SQL has a WHERE/JOIN ("0 rows after filter — check the predicate/join keys"), and an **all-NULL column** in the result ("column X is entirely NULL — likely a bad join or wrong column"). (Row-blowup/groupby-grew need a before/after comparison → that's `verify_join`'s job pre-join; not duplicated here.) Errors already return `error_category`/`hint`; this covers the success path. Fields default empty (back-compat).

**(b) Question-constraint checker** (`agent/verification/constraints.py`). `check_answer_constraints(question: str, answer: str) -> list[str]` — deterministic extraction of `top_k`/`count`/`percentage`/`distinct`/`scalar` expectations from the question text and a shape check against the candidate answer (e.g. "question asks for the top 5 but the answer lists 3 items"; "question asks for a percentage but the answer has no % / no value in [0,100]"). No LLM. Wired into `_run_trial_verified` finalization: if the chosen answer violates a constraint, do **one bounded revise** dispatch (re-run with the violation noted), fail-open. Gated by `--agent-postverify`.

## Ablation & flags

Each unit is separately flag-gated → separately ablatable (the discipline that made the T1a ablation interpretable):
- `--agent-consensus K` (existing) — now runs **diverse** consensus (Unit 1) by default when K>1; `--no-consensus-diversity` disables diversity for the null-baseline A/B (proving diversity is what matters, not just K).
- `--agent-argue-rounds N` (Unit 2).
- `--agent-postverify` (Unit 3a run_sql enrichment is harmless-always-on; 3b constraint-revise flag-gated).
- `--agent-reverify` (existing separated re-derive) — unchanged.

**Ablation matrix** (expanded 6-dataset subset from M0's run, claude-mcp; and given the M0 ablation's model finding, run on **Sonnet 5** where the levers actually helped, plus a 4.6 control): off / diverse-consensus-3 / +argue / +postverify / (and no-diversity-consensus-3 as the "prove diversity matters" control). Keep only net-positive units. This is real Sonnet Max-plan budget — sequence after the code lands.

## Testing (mostly no live LLM)

- **Unit 1:** `generate_scent(variant_seed=0)` vs `variant_seed=1` on a fixture with a high-cardinality column → assert the sampled example values differ, both are GT-firewalled (`audit_scent_doc` clean), and low-cardinality dimensions are identical. `cartograph_prepass(variants=3)` → assert 3 variant dirs. `_dispatch_driver_once(diversity_index=i)` (stub the driver) → assert framing[i] + variant-i maze dir selected; `diversity_index=None` → identical to today.
- **Unit 2:** stub `choose_modal`/dispatch → a low-confidence first vote then a majority after one argue round → assert the argued answer is returned and rounds recorded; fail-open after max rounds. (Judge/dispatch stubbed — no live LLM.)
- **Unit 3a:** run_sql over a fixture → assert `result_shape` + empty-after-filter warning + all-NULL-column warning; clean query → no warnings. **3b:** `check_answer_constraints("top 5 …", "A, B, C")` → flags the count mismatch; a matching answer → no flags. All deterministic.
- Existing verification tests (`test_dab_verification.py`) stay green; default-off path byte-identical.

## Decomposition into plan phases

- **Phase 1 — Unit 3 (deterministic verifiers).** Zero-LLM, self-contained, cheapest, no ablation-budget dependency. Ship first.
- **Phase 2 — Unit 1a (K Scent variants).** Cartographer `variant_seed` + `cartograph_prepass(variants=)`.
- **Phase 3 — Unit 1b + wiring (framing rotation + diverse-consensus dispatch).** `diversity_index` through `_dispatch_driver_once`/`_run_trial_claude_mcp`; `--no-consensus-diversity`.
- **Phase 4 — Unit 2 (argumentation rounds).** `--agent-argue-rounds`; the cross-examination orchestration.

Each phase is independently testable and mergeable; the ablation runs after the phases land.

## Open questions for the plan

1. Seeded-sample SQL form (`ORDER BY hash(col || seed)` vs DuckDB `USING SAMPLE ... (bernoulli, seed)`) — pick the one that's reproducible across DuckDB versions + cheap; confirm on a fixture.
2. `_CONSENSUS_FRAMINGS` exact wording + count (≥ K rotation coverage).
3. Argumentation justification source — full `final_text` vs a truncated tail (token cost); truncate to a bounded length.
4. Constraint-checker patterns (regexes for top_k/count/percentage) — concrete list; keep conservative (only flag high-confidence mismatches to avoid false-positive reviser churn).
5. Whether variant generation is skipped when `--agent-cartograph` is off (no Scent → diversity degrades to framing-only; acceptable, documented).

## Non-goals (M1)

- Interpretation-rotation as a distinct planner mechanism (framing rotation covers the cheap version; full multi-interpretation surfacing is deferred).
- Root-cause verdict cascade (SCRIBE) — a later refinement, not in M1.
- Changing the shipped separated-context re-derive beyond a hardening test.
- Any product (`run_agent_task`) verification params — DAB-path first (mirrors the shipped T1a deferral).
