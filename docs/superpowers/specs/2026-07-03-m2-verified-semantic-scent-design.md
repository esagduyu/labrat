# M2 — Verified Semantic Scent — Design

**Date:** 2026-07-03
**Status:** Design — awaiting user review before writing-plans
**Branch:** `feat/verified-semantic-scent` (proposed)
**Source:** Milestone M2 of `docs/superpowers/plans/2026-07-03-competitive-build-milestones.md`; competitive analysis `docs/competitive-analysis-2026-07-03.md` §4 Tier 0.2. Redoes T1c (`docs/superpowers/specs/2026-06-25-llm-semantic-scent-design.md`), which ablated **−3.7pp** because it wrote *unverified, unconditional* prose.

## Motivation

The shipped `with_semantics` Cartographer pass (T1c) authors interpretive Scent (`## Gotchas`/`## Best Practices`) with an LLM. It ablated **net-negative (−3.7pp; music_brainz 2/9→0/9)** — it added misleading, unconditional claims the data didn't support. The competitive deep-dive found the fix, proven by two teams above us: **MinusX** verifies every join annotation with a live `COUNT(*)` probe before persisting it; **Altimate** learned that unconditional rules ("use column X not Y") backfire (their pancancer 5/15) and switched to conditional authoring. M2 rebuilds the pass around those two lessons: **every structured claim survives a live probe before it is persisted (unverified claims are dropped), and prose rules are authored conditionally.**

This is also the direct fix for two autopsy failures (memory `project_dab_failure_autopsy`): pancancer_atlas:1 (grouped by the name column instead of the code column) and music_brainz (entity-resolution gotchas that were wrong).

**Non-negotiables:** default-off (`--cartograph-semantics`, already exists); GT-firewalled (the LLM sees only the deterministic skeleton; verification probes read DB metadata/rows, never validator/answer-key files); every frozen doc still passes `audit_scent_doc` (fail-loud); the verification is deterministic (no LLM in the probe step); **unverified structured claims are dropped, not surfaced.**

## Current-state anchors (code-verified 2026-07-03)

- `maze/cartographer.py`: `_SEMANTICS_INSTRUCTION` + `_semantics_prompt(skeleton)` + `draft_semantics(skeleton, llm_fn) -> list[Section]` (parses LLM markdown into `## Gotchas`/`## Best Practices`/`## Cross-References` sections tagged `Source: draft`) + `merge_sections`. Invoked in `generate_scent(with_semantics=True, llm_fn)` → merge → `audit_scent_doc` (raise on hit) → freeze.
- Deterministic verified joins already exist: `discover_joins(ctx, profile, database)` → `verify_join`-confirmed `VerifiedJoin` list; `verify_join` (`agent/tools/verify_join.py`) returns match-rate/fan-out/`likely_valid`. **The claim verifier reuses these.**
- `scent_audit.py`: `detect_contamination`, `audit_scent_doc`, `ScentContaminationError`, `CONTAMINATION_PATTERNS`.
- `document.py`: `Section{heading, body, source, …}`; recognized sources incl. `verified`/`draft`/`human` (M0's moat-foundation spec also proposed `harvested`/`lineage`; M2 uses the existing `verified`/`draft`).
- DAB wiring: `_cartograph_llm_fn` routes semantics authoring to `claude-code` on claude-mcp (Max-plan). `--cartograph-semantics`/`--cartograph-semantics-model`/`--cartograph-scent-dir` in `eval_dab.py`.

## The three units

### Unit 1 — Structured claim emission

Extend the semantics authoring so the LLM emits, in addition to conditional prose, a **parseable `## Semantic Claims` block** of typed, one-per-line claims the code extracts. Line-based (not JSON — robust to LLM malformation). Two claim types:

- **JOIN claim:** `JOIN <left_table>.<left_col> = <right_table>.<right_col>` — a join the author believes is meaningful.
- **COLUMN-ROLE claim:** `ROLE <table>.<code_col> CODES <table>.<name_col>` — asserts `code_col` holds the coded/canonical values and `name_col` holds display names for the same concept (the pancancer code-vs-name distinction). (Single-table; both columns in the same table.)

A new `parse_semantic_claims(text) -> list[Claim]` extracts these (tolerant: unparseable lines ignored). Claims are the *input* to Unit 2; they are never persisted unverified.

### Unit 2 — Live claim verification (the load-bearing fix)

A deterministic (no-LLM) verifier probes each claim against the live DB; **only survivors are persisted.**

- **JOIN claim → `verify_join`.** Keep iff `likely_valid` (match-rate ≥ threshold). Render as a verified join line (same shape as the Key Tables joins).
- **COLUMN-ROLE claim → value-distribution probe.** Confirm the asserted roles from data: the `code_col` is the lower-cardinality / code-shaped column (short, enumerable, or bracketed-sentinel-bearing) and the `name_col` is the higher-cardinality display column, and they co-vary (each code maps to ≤ a small number of names). Keep iff the data supports the asserted direction; **drop if the roles are reversed or unsupported** (this is what prevents the pancancer-style "use the wrong column" failure from ever being written).
- Survivors render into a **`## Verified Semantics`** section, `Source: verified` (mechanically confirmed — same trust tier as Key Tables). Unverified claims are dropped silently (logged to the pre-pass run for debugging, not into the doc).

New: `verify_semantic_claims(claims, ctx, *, database) -> list[Section-lines]` (async for the verify_join calls; the value-distribution probe is plain SQL via the connection).

### Unit 3 — Conditional prose

Rewrite `_SEMANTICS_INSTRUCTION` so the free `## Gotchas`/`## Best Practices` are:
- **Conditional, never prescriptive:** "*When* the question asks for coded values, use the code column; for display labels, use the name column" — explicitly forbid unconditional "use X not Y."
- **Self-checked:** a final instruction to drop any bullet that merely restates a column name/type or the verified facts (few, high-signal).

Prose stays `Source: draft` (unverified interpretation), rendered *after* and clearly separated from `## Verified Semantics`. The LLM still only ever sees the deterministic skeleton (GT-firewall unchanged).

## Data flow (generate_scent with_semantics=True)

1. Build the deterministic skeleton (Quick Reference / Key Tables / Dimensions) — unchanged.
2. `draft_semantics(skeleton, llm_fn)` → now returns **(conditional prose sections, raw claims text)**.
3. `parse_semantic_claims(raw)` → `list[Claim]`.
4. `verify_semantic_claims(claims, ctx, database)` → verified `## Verified Semantics` section (dropped-unverified).
5. Merge: skeleton + `## Verified Semantics` (verified) + conditional prose (draft).
6. `audit_scent_doc(doc)` → raise on any contamination hit (unchanged fail-loud freeze guard).

## Flags / ablation

- Reuses `--cartograph-semantics` (default off). M2 changes *what it produces* (verified claims + conditional prose) — no new flag needed for the core.
- **Ablation** (the decisive test — T1c was −3.7pp): tuning subset + the M0 expanded subset, on **Sonnet 5** (where levers help) + a 4.6 control, arms: **structure-only (baseline) / verified-semantics-M2 / (optionally) old-T1c-prose** — to confirm M2 clears the −3.7pp and ideally recovers music_brainz + pancancer. Keep only if net-positive; stays default-off until proven. Needs Max-plan budget.

## Testing (fixture-based; the authoring LLM is stubbed)

- **Unit 1:** `parse_semantic_claims` over a fixture text with valid JOIN + ROLE lines + garbage lines → asserts the two typed claims parsed, garbage ignored.
- **Unit 2 (the crux):** fixture DB where (a) a claimed join is real → survives; (b) a claimed join is bogus (low match-rate) → dropped; (c) a ROLE claim with correct code/name direction → survives; (d) a ROLE claim with REVERSED direction → dropped. Assert the `## Verified Semantics` section contains only survivors, and `audit_scent_doc` is clean on the result.
- **Unit 3:** stub `llm_fn` returning conditional prose → `draft_semantics` tags it `draft`; assert the new `_SEMANTICS_INSTRUCTION` forbids unconditional phrasing (a golden-string check) and the prose renders after the verified section.
- **Integration:** `generate_scent(with_semantics=True, llm_fn=<stub emitting claims + prose>)` on a fixture → verified section has only probed-true claims, prose is draft, doc passes audit, and with a leaky claim the audit still fails-loud.
- Existing `test_dab_semantic_scent.py` / semantics tests stay green; `with_semantics=False` path byte-identical.

## Decomposition into plan phases

- **Phase 1 — claim model + parser** (Unit 1): `Claim` types + `parse_semantic_claims`. Pure, testable.
- **Phase 2 — claim verifier** (Unit 2): `verify_semantic_claims` (verify_join reuse + value-distribution probe). The load-bearing unit.
- **Phase 3 — conditional prose prompt** (Unit 3): rewrite `_SEMANTICS_INSTRUCTION`; `draft_semantics` returns prose + raw-claims.
- **Phase 4 — wire into `generate_scent`** (data flow above) + audit + the DAB path; regression + `with_semantics=False` byte-identity.

## Open questions for the plan

1. COLUMN-ROLE value-distribution probe: exact heuristics/thresholds (cardinality ratio, code-shape detection, code→name fan-out cap) — pick conservative constants; when in doubt, DROP (false-drop is safe; false-keep is the failure we're preventing).
2. Claim line grammar exact tokens (`JOIN`/`ROLE`/`CODES`) + how the prompt instructs the LLM to emit the block.
3. Whether `## Verified Semantics` join lines should be de-duplicated against the deterministic Key Tables joins (avoid restating the same verified join).
4. Provenance token: reuse `verified` vs a distinct `semantic-verified` (for the T3c footer later). Default: reuse `verified`.
5. Should JOIN-claim verification be skipped entirely when `--agent-cartograph` already ran `discover_joins` (dedup) — or kept for author-proposed joins the heuristic missed. Default: verify author claims not already in the deterministic set.

## Non-goals (M2)

- Surfacing dropped claims into the doc (they're dropped silently by design).
- LLM-based claim verification (verification is deterministic).
- New product/`run_agent_task` semantics params (DAB-path first).
- Column-level lineage / metric ingestion (that's M3/T1b).
