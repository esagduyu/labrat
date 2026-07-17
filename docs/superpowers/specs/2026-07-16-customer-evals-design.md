# Customer-Facing Evals Design (moat extra 2.4)

**Status:** Draft for ratification (2026-07-16)
**Author:** Claude Fable (design fork), for Ege
**Related:** [`2026-05-28-unified-benchmark-suite-design.md`](2026-05-28-unified-benchmark-suite-design.md) (the protocol this reuses), [`docs/dbt-ci-pairing.md`](../../dbt-ci-pairing.md) (the CI-gate precedent), [`docs/team-scent.md`](../../team-scent.md) (the colocate/git-version precedent), north-star §8a (the Anthropic eval-growth playbook), FEATURE_ROADMAP.md "Eval-set growth (process)" note.

## One-sentence pitch

Let a user turn *their own questions with known-good answers* into a versioned, runnable accuracy suite against *their own warehouse* — so "did LabRat get more accurate on MY data after that Scent change / model upgrade / dbt refactor?" becomes a command, not a feeling.

## Goals

1. **User-authored accuracy evals** — (question, expected outcome, connection) cases owned by the user, colocated and git-versioned exactly like team Scent (`labrat_maze/evals/`).
2. **Reuse, don't rebuild** — cases run through the existing `BenchmarkSuite` protocol (`src/labrat/eval/types.py`) and the real agent stack (`build_agent_session` → `run_agent_task`), so an eval run exercises precisely what a chat turn exercises: same tools, same ledger, same Scent retrieval.
3. **Close the moat loop** — eval cases are *born from* the moat (harvested corrections, Findings marked golden, dashboard-anchored drafts) and *gate* the moat (a Scent/Trail/Map change that regresses the suite is visible before it merges). This is the roadmap's standing instruction: "Wire correction-harvesting (T2b) to emit eval candidates, not just doc PRs."
4. **Shareable proof** — the run report exports as a provenance-stamped Cheese HTML artifact (Pillar 2): an accuracy report a data lead can send to a stakeholder with zero LabRat installed.
5. **Benchmark safety by construction** — a new package with zero changes to `eval/benchmarks/*`, retrieval scoring, the tool registry (no new agent tool), or any leaderboard path.

## Non-goals (v1)

- No LLM-judge scoring (deterministic checks only; see D4).
- No hosted/remote anything — local runs, local reports.
- No automatic eval generation (dashboard-anchored + long-tail drafting is v2; the Anthropic playbook's "calibrate count by offline→online correlation" needs usage data we don't have yet).
- No trend dashboards / history UI (paid-tier scope; the JSONL history the runner writes is the substrate).
- Not a data-quality tool (that's dbt tests' job) — this scores *the agent's answers*, not the warehouse.

## 1. What a case is (data model)

New package `src/labrat/evals/` (see D1 for naming), Pydantic throughout:

```python
class EvalCheck(BaseModel):          # discriminated union on `kind`
    kind: Literal["value", "text", "golden_sql"]
    # value: expected numeric value + tolerance (DAB-style "GT within tol in answer")
    expected: str | float | None = None
    tolerance: float | None = None
    # text: substring / regex the answer must contain
    pattern: str | None = None
    regex: bool = False
    # golden_sql: a SQL query whose (small) result set the answer must contain —
    # the "dashboard-anchored" form: the blessed query IS the expected answer,
    # re-executed at run time so it tracks the live warehouse
    sql: str | None = None
    database: str | None = None

class EvalCase(BaseModel):
    id: str                          # kebab-case slug, unique within the suite
    question: str                    # the NL prompt, verbatim
    checks: list[EvalCheck]          # ALL must pass (AND semantics)
    tags: list[str] = []             # domain routing, filtering (--tags revenue)
    source: str = "human"            # provenance tier, reuses maze SOURCE_TIERS
    created_from: str | None = None  # finding id / correction id / None
    enabled: bool = True
    notes: str | None = None

class EvalSuiteFile(BaseModel):      # one YAML file = one suite
    version: int = 1
    profile: str | None = None       # default connection profile; CLI can override
    n_trials: int = 3                # see D3
    cases: list[EvalCase]
```

**Storage:** `labrat_maze/evals/<suite-name>.yaml` — project layer, committed to git beside the dbt project and team Scent (D6). The user layer (`~/.labrat/`) deliberately has no eval store: evals are team artifacts by nature, and the colocate story is what makes the CI pairing possible later. Fingerprint sidecars are **not** needed (evals reference questions, not schema structure; staleness shows up as failures, which is the point).

**Validation rules on load:** unique ids, ≥1 check per case, `golden_sql` limited to `SELECT` (reuse `run_sql`'s statement guard), tolerance requires numeric expected. Fail-loud on any malformed case — an eval suite that silently skips cases is worse than none.

## 2. Authoring paths

1. **Hand-written YAML** (v1) — `labrat evals init` scaffolds a commented example suite; `labrat evals validate` lints it (same UX shape as `scent init-ci` / `scent check`).
2. **Mark-as-golden from the TUI** (v1.1) — a keybinding on a chat turn / Finding drafts an `EvalCase` (question = the user's prompt verbatim; checks pre-filled from the answer's key numeric values; `created_from` = finding id; `source` = "human") into a review screen before it's written. Mirrors the harvest-review gate: nothing lands without a human look. Reuses `RecordDecisionScreen`'s capture pattern.
3. **Harvest-emitted candidates** (v1.1, the T2b wiring) — when `SessionHarvester` captures a correction, it additionally drafts an eval candidate ("the corrected answer is the expected one") tagged `source: "harvested"`, surfaced in the same HarvestReviewScreen flow, default-off behind the existing `harvest_opt_in`. A correction that becomes both a Scent gotcha *and* a regression eval is the compounding loop closing on itself.

## 3. The runner: a fourth BenchmarkSuite

`CustomerEvalSuite` implements the existing protocol verbatim — the fourth consumer of `types.py` after DAB/ADE/smoke:

- `tasks()` — one `BenchmarkTask` per enabled case (`benchmark="customer-evals"`, `config` carries the checks + profile).
- `run_trial()` — resolves the profile via the keyring-backed `ProfileManager` → `build_agent_session` (full 22-tool registry, ledger on, Scent retrieval live, `verify` per profile setting) → `run_agent_task(prompt=case.question)`. Scoring: run each check against `final_text` (value/text) or against a fresh `run_sql` of the golden query (golden_sql; the agent's answer must contain each cell of the ≤N-row result, N capped ~20). `raise_rate_limits` stays False — a 429 mid-suite degrades that case, never kills the run.
- `aggregate()` — existing `AggregateScore`; `by_dimension` keyed by tag.
- `write_submission()` — `report.md` + `report.json` (BenchmarkReport) + `evals-history.jsonl` append (one line per run: git SHA of `labrat_maze/`, model id, scores — the substrate for paid trends later).

**Cheese export:** `labrat evals run --export-cheese` renders the report through `cheese/render.py` as a self-contained HTML artifact: per-case pass/fail, per-tag rollup, provenance block (model id, Scent git SHA, run timestamp, suite version). One new render template; no changes to the Cheese model beyond a `kind: "eval-report"` manifest field.

**CLI:**

```bash
labrat evals init                      # scaffold labrat_maze/evals/example.yaml
labrat evals validate [SUITE]          # lint, exit 0/1  (offline, no warehouse, no LLM)
labrat evals run [SUITE] [--tags t] [--n-trials N] [--profile P] [--export-cheese]
labrat evals list                      # suites + case counts + last-run summary
```

`run` exit codes: 0 all pass, 1 any fail, 2 infra (mirrors the smoke runner's infra/semantic split — `infra:` reasons never count as accuracy failures; the smoke-fail-fast lesson applies).

## 4. Moat integration

- **Eval failure → harvest signal** (v1.1): a failing case whose `created_from` is set surfaces in HarvestReview as "regression on a golden you saved — did the data change, or did LabRat regress?" with one-tap routing to a Scent gotcha draft.
- **CI pairing** (paid, v2): `labrat evals check` as the accuracy sibling of `scent check`. Full runs need a live warehouse + LLM spend, so the CI story is tiered: PR gate = `evals validate` (offline lint, free) + optionally a scheduled nightly `evals run` on a runner with credentials (paid). Never a synchronous PR-blocking LLM run.
- **Provenance stamping:** results carry the `labrat_maze/` git SHA (reusing team-Scent's `git_sha` stamping) and model id, so a score is always attributable to a (knowledge, model) pair — this is what makes "the Scent upgrade was worth +12pp on our suite" a defensible sentence.

## 5. Free/paid boundary (per commercial decision #24)

The commercial memo slots evals in the paid team layer. Recommended split (D2): **free** = the entire local loop (author, validate, run, Cheese export) for a single user — this is the PLG hook, and it keeps the "bare-bones tier depends on nothing server-side" rule; **paid** = the team-scale layer: CI wiring (`evals check` + scheduled runs), history/trends across runs, team-shared suite governance (review gates on suite changes), Slack/report distribution. Same shape as team Scent (free) vs dbt-CI pairing (paid complement).

## 6. v1 scope cut

| In v1 | Deferred |
|---|---|
| `src/labrat/evals/` models + YAML store + loader validation | TUI mark-as-golden (v1.1) |
| `CustomerEvalSuite` (BenchmarkSuite #4) over `build_agent_session` | harvest→candidate wiring (v1.1) |
| checks: `value`, `text`, `golden_sql` | `llm_judge` check kind (v1.1, D4) |
| CLI: `init` / `validate` / `run` / `list` | `evals check` CI gate + scheduled runs (v2, paid) |
| report.md/json + evals-history.jsonl | trends UI, team governance (v2, paid) |
| Cheese HTML export of a run | auto-generated dashboard-anchored/long-tail cases (v2) |

Estimated shape: ~4 source files + CLI wiring + 1 Cheese template; test-first throughout.

## 7. Test strategy

- Unit: model/loader validation (malformed YAML fail-loud), each check kind against fixture answers, tag filtering, exit codes, history append — all against `tests/fixtures/sample_dbs/ecommerce.duckdb` via the conftest fixture (no gitignored DB dependency).
- Suite-protocol conformance: same test shape as `SubsetSuite`'s (tasks/aggregate/write_submission round-trip).
- One end-to-end behind the LLM gate (`LABRAT_RUN_LLM_TESTS=1`): 2-case suite against ecommerce, one pass one fail.
- Benchmark-safety regression: assert `eval/benchmarks/` modules are byte-untouched (CI already covers via unchanged tests) and no new tool appears in `build_data_tools_registry()` (registry-count test exists).

## 8. Benchmark-safety statement

This feature adds a sibling consumer of the eval protocol. It does not modify `src/labrat/eval/benchmarks/**`, `maze/` retrieval scoring, the agent tool registry (count stays 27), prompts, or any DAB/ADE path. The only shared-code touchpoints are read-only reuse: `types.py` protocol, `build_agent_session`, `ProfileManager`, `cheese/render`. A DAB run before and after this feature merges must be byte-identical in behavior.

## Decisions for ratification

- **D1 — Naming (recommend: CLI `labrat evals`, package `src/labrat/evals/`).** Risk: one-letter confusion with `src/labrat/eval/` (benchmarks). Alternative: `goldens` everywhere ("golden questions") — unambiguous but less discoverable. The module docstrings disambiguate either way.
- **D2 — Free/paid split (recommend: local loop free, CI/trends/team paid).** Alternative: evals entirely paid per the original memo slotting — but a free local loop is the PLG wedge and matches the team-Scent-free precedent.
- **D3 — `n_trials` default 3 with per-case pass@1-strict display (recommend).** Stochastic agents need an envelope (smoke-baseline lesson); 1 trial reads as flaky, 5 is spendy for a default.
- **D4 — No LLM-judge in v1 (recommend defer).** Deterministic-first keeps `validate`/CI free and avoids judge-drift; the `ValidationChecker` shape is ready when wanted (v1.1).
- **D5 — Harvest→eval-candidate wiring in v1.1, not v1 (recommend).** It's the roadmap's explicit ask, but it rides the existing harvest review surface and shouldn't gate the standalone loop shipping.
- **D6 — Store at `labrat_maze/evals/` (recommend)** so one directory is "the team's LabRat knowledge + its acceptance tests," versioned together. Alternative `labrat_evals/` top-level keeps Maze purely knowledge — but splits the colocate story.
