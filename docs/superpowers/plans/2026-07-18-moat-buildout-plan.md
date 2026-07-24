# Moat Build-Out — Master Plan (Track 2)

**Date:** 2026-07-18 · **Status:** ready to execute (subagent-driven; no user in loop
during planning — decisions recorded in
[`../specs/2026-07-18-moat-decisions.md`](../specs/2026-07-18-moat-decisions.md), read
that first) · **Specs:** customer evals
[`2026-07-16-customer-evals-design.md`](../specs/2026-07-16-customer-evals-design.md)
(D1–D6 now ratified), provider defaults
[`2026-07-18-provider-conditional-defaults-design.md`](../specs/2026-07-18-provider-conditional-defaults-design.md),
operational rules
[`2026-07-18-scent-operational-rules-design.md`](../specs/2026-07-18-scent-operational-rules-design.md),
clustering
[`2026-07-18-correction-clustering-design.md`](../specs/2026-07-18-correction-clustering-design.md).

**Milestone order (SEQ1):** M0 hygiene → M1 operational rules → M2 provider defaults
→ M3 customer evals v1 → M4 evals v1.1 → M5 clustering. Rationale: two small
features land the brand promise + retrieval quality before the eval keystone
baselines against them; hygiene first because two RRF items embarrass any demo.
No hard dependencies between M1/M2/M3 — they may run on parallel branches if
capacity allows; M4 requires M3; M5 is independent but sequenced last (SEQ3).

**Standing rules for every milestone:** feature branch per milestone
(`feat/moat-m<N>-<slug>`), worktree isolation if anything else is running from the
main tree; TDD red-first per behavior; gates at every task boundary
(`uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest
tests/unit -q`) + full `pytest -q` once per milestone; whole-branch adversarial
review before merge; benchmark-safety proof obligation of the relevant spec
discharged by test, not assertion; commit per task with the standard trailer.

---

## M0 — Hygiene pass (~1d, branch `feat/moat-m0-hygiene`)

| # | Task | Files | Acceptance |
|---|---|---|---|
| 0.1 | Memoize embedder + sidecar cache per `(model_id, cache_path)` at module level | `maze/hybrid.py`, `maze/embedding.py` | counter-stub test: two flag-on searches → one model load, one sidecar parse |
| 0.2 | Provision-at-toggle: flipping the Settings switch triggers background model provisioning with a status note; retrieval never downloads in-call | `screens/settings.py`, `maze/embedding.py` | test: flag on + model unfetched → tool call returns lexical + no network attempt; toggle handler invokes provisioning hook |
| 0.3 | Slug-validate `profile_name` before any sidecar path construction (reuse the `Profile.name` validator rule) | `maze/hybrid.py` | traversal-shaped name → `ValueError`, no path touched |
| 0.4 | Sidecar `mkdir(parents=True)` on first write | `maze/embedding.py` | fresh profile dir → sidecar persists (today: silent in-memory only) |
| 0.5 | Runtime dispatch gate for the DAB labrat-agent driver (SEQ2): before executing `load_file`/`attach_database`/`run_sql`-with-file-source, classify path args via `taint_structural.classify_file_source`; deny non-sanctioned sources with a structured error + trace event | `eval/benchmarks/dab/suite.py` (driver wiring), small helper in `eval/benchmarks/dab/taint_structural.py` | test: DAB env dispatch of `load_file('/x/query_agnews/query3/gt.csv')` → blocked result + `runtime_gate` trace event; product paths (no gate) unaffected; the 273-trace corpus replayed through the gate → 0 blocks |
| 0.6 | `TESTING.md` manual-gate line for the hybrid toggle + provisioning note | `TESTING.md` | doc line present |

## M1 — Scent Operational Rules (~2d, branch `feat/moat-m1-oprules`)

| # | Task | Files | Acceptance |
|---|---|---|---|
| 1.1 | Sentinel-string probe (bounded top-K frequent values over `_STRINGY` columns vs fixed sentinel list, with counts) | `maze/cartographer.py`, fixture builder in `tests/fixtures/` | red-first: sentinel table flagged with count evidence; probe row-bounded |
| 1.2 | Mixed-format date-in-text probe (sample N stringy values; flag ≥2 date shapes) | same | fixture with two formats flagged; single-format not flagged |
| 1.3 | Section assembly: promote join-transform + shared-structure facts, add probe results; OR2 ranking (category weight → table count → alpha), cap 8; `source: "verified"` + freshness meta; emitted first | `maze/cartographer.py` | ordering deterministic; cap enforced; section first in doc |
| 1.4 | `operational_rules: bool = False` flag through `generate_scent`/`cartograph_prepass`; TUI first-connect passes True | `maze/cartographer.py`, `screens/main.py` | **byte-identity golden test with flag off**; grep-test: no `src/labrat/eval/` path passes True |
| 1.5 | Docs: CLAUDE.md maze row note; ablation-candidate note in the DAB integration doc ("declared lever once ablated", OR3) | docs | present |

## M2 — Provider-conditional defaults (~2d, branch `feat/moat-m2-defaults`)

| # | Task | Files | Acceptance |
|---|---|---|---|
| 2.1 | `agent/defaults.py`: `Receipt`/`RecommendedDefaults` models, seeded table (sonnet/opus/gpt-5 families transcribed from the ablation docs), `resolve_recommended` glob resolver | new file + tests | unknown model → None; every seeded field carries ≥1 receipt; glob precedence tested |
| 2.2 | Settings chips + "Apply recommended" (writes explicit profile fields via `ProfileManager`; PD2 — never auto) + receipts expander | `screens/settings.py` | chip shows iff current ≠ recommended; apply idempotent; TUI test in `tests/tui/` |
| 2.3 | `labrat defaults show|apply` CLI | `cli.py` | snapshot test of `show`; `apply` parity with TUI path |
| 2.4 | First-connect nudge (once per screen instance, never auto-runs) | `screens/main.py` | nudge appears for covered-family profile with no applied choice; absent otherwise |
| 2.5 | Import-graph isolation test: `labrat.agent.defaults` unreachable from `labrat.eval.benchmarks.*` and `run_agent_task` | tests | red if imported |

## M3 — Customer evals v1 (~5d, branch `feat/moat-m3-evals`)

Build the ratified spec's §6 v1 column exactly; D3b scoring semantics.

| # | Task | Files | Acceptance |
|---|---|---|---|
| 3.1 | `src/labrat/evals/` models (`EvalCheck`/`EvalCase`/`EvalSuiteFile`) + YAML loader with fail-loud validation (unique ids, ≥1 check, SELECT-only golden_sql via the run_sql statement guard, tolerance⇒numeric) | new package | red-first per validation rule |
| 3.2 | `CustomerEvalSuite` (BenchmarkSuite #4): `tasks`/`run_trial` (ProfileManager → `build_agent_session` → `run_agent_task`; checks vs `final_text`; golden_sql re-executed, ≤20-row cap; `raise_rate_limits` stays False) /`aggregate` (by-tag dims)/`write_submission` (report.md/json + `evals-history.jsonl` with maze git SHA + model id) | `evals/suite.py` | protocol-conformance test (SubsetSuite shape); infra/semantic split (`infra:` never counts as fail) |
| 3.3 | D3b scoring: case score = rate; exit strict (1 if any rate<1); report separates failing (0) from flaky (0<r<1) | `evals/suite.py`, CLI | exit-code matrix test |
| 3.4 | CLI group `labrat evals init|validate|run|list` (validate = offline, no warehouse/LLM) | `cli.py` | scaffold round-trips through validate; exit codes 0/1/2 |
| 3.5 | Cheese export: `--export-cheese` → `kind: "eval-report"` template (per-case pass/flaky/fail, per-tag rollup, provenance block) | `cheese/` template + `evals/` | rendered HTML self-contained; provenance block carries SHA+model |
| 3.6 | Isolation + safety tests: no import between `labrat.evals` and `labrat.eval.benchmarks`; registry count unchanged; one LLM-gated end-to-end (2-case ecommerce suite, one pass one fail) | tests | as stated |
| 3.7 | Docs: `docs/customer-evals.md` user guide; CLAUDE.md subsystem row | docs | present |

## M4 — Evals v1.1: golden capture + harvest wiring (~3d, branch `feat/moat-m4-evals11`, requires M3)

| # | Task | Files | Acceptance |
|---|---|---|---|
| 4.1 | TUI mark-as-golden: keybinding on a chat turn/Finding drafts an `EvalCase` (question verbatim, checks prefilled from answer numerics, `created_from` set) into a review modal before write | `screens/` (RecordDecisionScreen pattern) | draft lands only on accept; TUI test |
| 4.2 | Harvest→eval candidates: `SessionHarvester` additionally drafts an eval candidate per correction (`source: "harvested"`), surfaced through HarvestReviewScreen, behind `harvest_opt_in` | `memory/harvest.py`, `screens/harvest_controller.py` | default-off; candidate carries corrected answer as expected |
| 4.3 | Eval-failure → harvest signal: failing case with `created_from` surfaces a "regression on a saved golden" review item with one-tap Scent-gotcha routing | `evals/`, harvest surface | fires only for `created_from` cases |
| 4.4 | `llm_judge` check kind via `ValidationChecker` shape (never on validate/CI path) | `evals/` | judge gated behind explicit flag; deterministic paths untouched |

## M5 — Correction clustering (~3d, branch `feat/moat-m5-clustering`)

| # | Task | Files | Acceptance |
|---|---|---|---|
| 5.1 | `_semantic_subclusters` (greedy agglomerative, θ=0.80, centroid running-mean, CC3 ordering) beneath scope clustering | `maze/harvest.py` | θ-boundary tests (0.79/0.80); determinism under shuffle |
| 5.2 | Near-dup collapse (0.95, keep earliest) + merged-note rendering in drafts | same | note shows N+M counts; audit still fail-louds |
| 5.3 | Fail-open: embedder None/exception ⇒ stage 2 skipped, drafts byte-identical | same | byte-identity test |
| 5.4 | Embedding reuse via the shared cache; integration test (6-correction fixture → 2 clusters + loner) through `draft_harvested_sections` | same | embed called once per distinct text |

## Dependency graph

```
M0 ──┬─ M1 ─┐
     ├─ M2 ─┼─ (soft: better before M3 baselines) ─ M3 ── M4
     └──────┴──────────────────────────────────────────── M5 (independent, last by SEQ3)
```

## Effort roll-up

M0 1d · M1 2d · M2 2d · M3 5d · M4 3d · M5 3d ≈ **16 build-days** serial; M1/M2/M3
parallelizable to ~11 with three implementer lanes (review capacity is the limit).

## Standing proof obligations (per merge)

- DAB/eval behavior byte-identical (each spec's stated test).
- Tool-registry count unchanged (existing test).
- No new hard deps (optional extras only; none needed beyond the shipped `[semantic]`).
- Fail-closed/opt-in for every Scent-writing surface (harvest gates unchanged).

_Regenerated 2026-07-23 from transcript after accidental deletion._
