# Claude Fable adversarial audit — GPT-5.6 DAB campaign (`feat/codex-caching-gpt56`)

Audit date: 2026-07-16 · Audited HEAD: `28be10c` ("docs: finalize GPT-5.6 DAB evidence") · Baseline: `origin/master` `945534f` · Branch span: 48 commits (verified) · DAB checkout pinned: `ca45478` (verified)

Method: six parallel independent review passes (provider/caching code · DAB suite/shards/taint code + package rebuild · 270-trace integrity/anti-cheating · ablation/tier/report artifacts · dual regrade + PR #65 comparison · Codex-rollout process audit) plus a targeted follow-up on cache warm-state, with all cross-cutting numerical checks recomputed directly from primary artifacts (`trials.jsonl`, `submission.json`, traces, git objects, pinned validators). No source file, run artifact, or git object was modified; the only writes were this report, the session scratchpad, and an independent package rebuild in the scratchpad.

---

## 1. Executive verdict

**SHIP, with minor corrections (wording/disclosure only — no artifact regeneration required).**

| Area | Verdict |
|---|---|
| Code safety | **PASS** — no P0/P1 anywhere in the branch diff; P2s are product-side or defense-in-depth, none affect submission validity |
| Numerical correctness | **PASS** — every headline number independently reproduced from primary artifacts, including a full independent regrade |
| Trace integrity | **PASS** — no contamination; 270/270 structurally sound; disclosed gaps assessed and bounded |
| Experimental claims | **PASS with caveats** — all recompute exactly; n=3 ablation noise and the promotion-rule phrasing need the (mostly already-present) descriptive framing |
| PR #65 comparison | **PASS** — exact reproduction of both regrades, the transition matrix, and validator/GT byte identity |
| DAB submission eligibility | **ELIGIBLE** — meets SUBMISSION_RUBRIC.md and README coverage/trace/consistency requirements; per-task AG News caps are not prohibited, are score-conservative (they produced 0/10 failures), and should be disclosed in the PR description |

Five-part package verdict requested by the audit prompt: **technically trace complete: YES · schema valid: YES · reproducible: YES (byte-identical rebuild) · ethically disclosed: YES once the pre-PR checklist wording fixes land · eligible for official PR/leaderboard: YES.**

---

## 2. Findings (P0 → P3)

**P0: none. P1: none.**

### P2 (material limitations / unsafe edges — none invalidate the submission)

- **P2-1 — Tool-dispatch rate-limit re-raise leaks benchmark semantics into product hosts.** `ToolRegistry.dispatch`'s "always returns, never raises" contract now re-raises rate-limit errors (`src/labrat/agent/tools/base.py:229-254`), so a 429 inside a TUI tool call can abort a chat turn that previously degraded to a structured tool error. No non-benchmark-host test covers this. *Fix:* gate the re-raise on a ctx/loop flag or catch at the TUI seam. (Product bug, not submission-relevant.)
- **P2-2 — Automated taint gate is a thin backstop and an in-suite comment overstates the sandbox.** The gate is 9 case-insensitive substrings; `load_file`, `attach_database`, and DuckDB `read_csv_auto()` via `run_sql` (connections not read-only, external access enabled — `env.py:121`) can read arbitrary local paths, and literal-string checks are evadeable (e.g. SQL `concat('ground_','truth.csv')`). The suite comment claiming "the registry exposes no file-read/shell tool" is false as written. *Mitigation in fact:* the independent event-by-event human/trace audit (reproduced by this audit) is what carries the 270/270-clean verdict, and it found no such access. *Fix:* correct the comment; treat gate hardening as follow-up.
- **P2-3 — Terminal classification is an in-band string sentinel.** `terminal:turn_budget` is detected by `final_text.startswith("[trial exhausted ")` and short-circuits before the contamination backstop (`suite.py`). A model answer beginning with that literal would be misclassified. Unexploited here: all 10 terminal rows carry the genuine `runner_turn_budget` trace event and main shards ran unbounded turns. *Fix:* thread a structured flag from `_invoke_agent`.
- **P2-4 — 16 CRM trials with benchmark-targeting phrasing: suspicious-but-not-proven stands.** Independent re-scan flagged a 7-trial subset on the same signal; the prior audit's 16 is a conservative superset. What those searches returned is deterministic Cartographer Scent (structure + sample rows from the local sanctioned DB); Salesforce-style IDs in outputs are local sample data, not answer keys; the failing CRM rows fail with *wrong* IDs — evidence of no answer channel. Classification: 0 contaminated. *Action:* keep the disclosure in the PR.
- **P2-5 — No raw model prompt/response bodies in the trace contract.** Model-identity and no-fabrication conclusions rest on request metadata (all 13,174 records report `gpt-5.6-luna`) plus data-evidence inference (all 206 passing rows show ≥4 successful local data-tool calls; deep samples show 4–36 `run_sql` each). The known memory-leak-prone tasks (agnews:3/4) all failed 0/10. Inherent to the disclosed trace contract; *action:* keep disclosed.
- **P2-6 — Process lapse (mitigated, disclosed): unilateral Luna-Low classifier smoke.** The implementer launched a mixed-effort recovery smoke as an "automatic prerequisite" without surfacing the methodology change; the user challenged it in-session, the agent acknowledged and stopped (rollout lines 35573–35652). Zero impact on the package: smoke dirs excluded; the accepted v2 recovery config was explicitly user-specified (rollout line 36819-region) and matches `config.json` `task_overrides` exactly. Residual: drift-under-autonomy pattern to watch in unattended runs.
- **P2-7 — `merge_bounded_recovery` doesn't compat-gate base-shard config deltas.** `_RECOVERY_COMPAT_KEYS` is enforced only for recovery runs (`dab_shards.py:371-376`); any base-shard delta is recorded but not restricted (`:344-363`). Actual delta here is only the benign, truthfully-declared `agnews.agent_timeout: 2400`. *Fix:* enforce the allowlist on base shards too + a test for the disallowed case.

### P3 (documentation/robustness; no result impact)

1. **"Dirty BSON" claims are false in kind and count** (`docs/dab-solultra-ablation.md`, rendered report artifact): the 5 (not 3) `M`-flagged BSON files are byte-identical to their committed blobs (`git hash-object --no-filters` = index SHA-1); it's a Git-LFS clean-filter artifact (`filter: lfs` attribute over raw-committed blobs). Overcautious direction; reword before PR.
2. **"Preregistered stratified-plus-noncached promotion rule" phrasing mildly overstates.** The Winner rule (stratified primary; efficiency as tie-breaker) *was* preregistered byte-unchanged from `5dad0b5` (before all comparison arms completed), and Ledger won the primary outright (83.9286 > 83.5317) — but the compound phrase entered the docs post-hoc, and a 0.4pp stratified edge at 45 rows is noise. Soften to "preregistered headline metric plus registered efficiency comparator."
3. **Report builder hardcodes the five stratified scores** (`build_campaign_report.mjs:11-16`); values verified correct to 6 decimals, but hand-entered headline metrics are drift risk.
4. **Noncached definition drift**: `summarize_campaign.mjs:69` computes `input − cached − cache_write`; docs define `input − cached`. Identical today (all cache-writes 0 with 100% presence reporting); diverges if writes become nonzero.
5. **Nonportable metadata in the package**: `config.json` embeds absolute `/Users/ege/...` paths; `trials.jsonl` meta carries `conversation_id` (local uuid4) / `response_id` per request. No secrets (verified), but scrub/relativize before shipping upstream — the trace-bundle script's `_scrub_text` already does this for bundle output.
6. **Two stale stashes on the branch** ("paused exact-one-db codex-mcp findings fix", "wip submission-grade sql policy…") hold unreviewed deferred detour work; drop or convert to tickets.
7. **Doc drift**: `docs/dab-integration.md` shows a single-dir submission command; the campaign actually ran sharded + assembled. Provenance is truthfully in `config.json`; align the doc example.
8. **Pyright discrepancy vs prior reviewer**: canonical gate (`uv run pyright`, include=`["src"]`) is clean; the audit prompt's wider `pyright scripts src tests/unit` shows 62 errors — all in files untouched by this branch (out-of-gate paths). The prior "clean" claim for the wider command does not reproduce.
9. **Trace evidence for retried attempts is destroyed by design** (`reset_on_attempt`): 25 infra + ~110 ablation attempt traces overwritten; rows preserved append-only. Accurately disclosed; the surviving selected traces are complete.
10. Minor code robustness: `llm_classify_reasoning` resume asymmetry on explicit-null configs (never hit); pacing sleep while holding the route lock (latency only); sync token refresh inside the async stream path; rate-limit reclassification can flip a *passing* row to infra (score-deflating only).
11. **Reproduction gotchas for maintainers**: validators need the DAB repo root on `sys.path` (missing it *undercounts* — 45 `validator_error` rows, fail-closed); four dataset dirs are committed uppercase, a silent trap on case-insensitive macOS for hand-rolled scripts.
12. One `dispatch_subagent` prompt self-references "the expected answer" (crmarenapro:2:2 ev49) — agent's own phrasing, no access, trial failed anyway.

---

## 3. Final submission validation table (expected vs independently observed)

| Check | Expected | Observed | ✓ |
|---|---|---|---|
| Trial rows / unique keys / tasks | 270 / 270 / 54, trials 0–4 | 270 / 270 / 54, trials 0–4 | ✓ |
| Submission rows / schema | 270, DAB `{dataset,query,run,answer}` | 270, exact schema match | ✓ |
| Selected `infra:*` rows | 0 | 0 | ✓ |
| Traces | 270 non-empty, regular, non-symlink | 270 (0 empty, 0 symlinks, 0 parse errors, 10,591 events, pairwise-unique sha256) | ✓ |
| Official validator passes (regrade) | 206 | **206** (own harness, pinned validators; 0 stored-pass mismatches, 0 validator errors) | ✓ |
| Micro rate | 76.296296% | 76.296296% | ✓ |
| Dataset-stratified | 0.7417582417582418 | 0.7417582417582418 (formula = unweighted mean of 12 per-dataset rates; matches `common_scaffold`) | ✓ |
| Usage | 229,857,456 in / 163,544,576 cached / 66,312,880 noncached / 3,240,606 out / 13,174 requests / 71.150433% | all exact; all 13,174 rows `terminal_status=response.completed`; cache-write presence 100% | ✓ |
| Terminal rows | 10 × `terminal:turn_budget`, agnews:3+4 only | 10 exact; artifact = `[trial exhausted 10-turn budget without a final answer]`; trace ends `runner_turn_budget`, output byte-equal; 0 `llm_classify` calls; no Luna Low; no 429 | ✓ |
| SHA-256 config/trials/submission/taint | `fea3b353…` / `540999c5…` / `9b93e29d…` / `125e2d19…` | all match | ✓ |
| Independent rebuild (`dab_shards.py recover` → scratchpad) | byte-comparable | **byte-identical**: all 4 artifacts + all 1,789 scratch files; `report.md` differs only in run-id | ✓ |
| v1 recovery exclusion | no v1 row | proven two ways: byte-identity of deterministic rebuild + sha256 of final agnews traces = v2 traces | ✓ |
| Source infra recoverability | preserved | 35 infra rows in source shards (18 agnews); 260 shard semantic + 10 recovery = 270 | ✓ |
| taint.json | 270/270 clean | 270/270 clean; independent rescan concurs | ✓ |
| Audit-artifact hashes | comparison JSON `99d3e7bd…`, integrity JSON `e0cca614…`, traces tar `08e3c54f…` | all match; tar = 270 relative entries, no traversal | ✓ |

## 4. Trace-integrity table

| Classification | Prior audit | This audit |
|---|---|---|
| Clean | 254 | ≥254 (independent sweep flagged only a 7-trial subset of the prior 16) |
| Suspicious-but-not-proven | 16 (CRM) | ≤16, all CRM phrasing-only; returned content = deterministic local Scent; no answer channel |
| Contaminated | 0 | **0** |

Scan coverage: all 270 traces event-by-event (inputs *and* outputs) + excluded v1 + tool inventory. No shell/subprocess/web/fetch tools exist in the registry used; 179 URL hits all inside benchmark row content; 0 ground-truth/answer-key/HF hits; all 325 `/Users/ege` path hits resolve to sanctioned `query_*/query_dataset` stores; all 68 `search_trails` calls returned empty; 0 real secrets. All 206 passing rows verified to carry local-data evidence (100% coverage, upgraded from the prior audit's sampling).

Known-gap impact: `reset_on_attempt` overwrites and absent raw bodies weaken *process-history* auditability, not the selected rows — every selected trace is complete, self-consistent, and terminal-consistent; the leak-prone agnews tasks all failed.

## 5. PR #65 exact-key comparison — fully reproduced

Both sides regraded under pinned validators (verified byte-identical 108/108 across PR base `6b86ca7`, pinned `ca45478`, and worktree): current **206/270 (74.1758% stratified)** vs PR #65 **176/270 (60.8822%)** → **+30 passes, +11.1111pp micro, +13.2937pp stratified**. Transition matrix 154 both-pass / 52 current-only / 22 PR-only / 42 both-fail; 23 task gains / 8 regressions / 23 ties; newly-cleared and lost task lists match exactly. PR answers taken from the PR-head git object (`8f3cdaee`), not the working tree. The shipped comparison correctly frames itself as descriptive, not causal (different model + feature stack + AG News cap).

## 6. Feature & tier ablation verdict

- **All five arm scores reproduce exactly** (35/33/33/40/39 of 45; stratified 74.4048/65.6746/65.6746/83.5317/83.9286) on verified-identical 45-key denominators with correct cumulative flag stacks. The Ledger-over-Hints stratified inversion is correct arithmetic (equal dataset weights; stockindex 9/9 vs 8/9 outweighs yelp 20/21 vs 18/21).
- **Ledger promotion: defensible.** The Winner rule was preregistered before any comparison arm completed and Ledger won the primary criterion outright; the noncached-input edge (−750,581 / −6.5503%) reproduces exactly and survives a warm-state sensitivity test (asymmetry ≤1.9% of the delta; cross-arm cache reuse ruled out by 5–12h gaps vs ~≤1h TTL; retry asymmetry favored the *loser*). Ledger's worse requests (+5.08%) and latency (+11.37%) are accurately disclosed. Caveat stands: n=3 over 4 datasets — a one-task swing moves the headline ~2.8pp; treat as directional, which the docs already say.
- **Tier study: descriptive only, and the docs behave.** Luna Max is the only complete arm (18/18, 9 passes); Terra 14/18, Sol High 9/18, Sol Ultra 6/18 with a genuine rate-limit stop (reset timestamp matches to the second). No survivor-only comparison is misused; the doc explicitly refuses a tier promotion. No higher-tier evidence supports one.

## 7. Prompt-caching verdict

Implemented correctly: replay state commits only after complete streams; fallbacks are one-shot, observable in `request_mode` telemetry, and never silent; no reasoning-token double-count; cache-write presence tracked as a bit, not assumed zero. **71.15% is reproducible** and decomposes cleanly (10,448 exact-replay requests @ 71.45% vs 2,626 initial-full @ 44.20%). The 90–95% figure was a host-native *observation*, never claimed as locally measured; the one valid native-vs-responses A/B (retry3; retries 1–2 invalidated by a content-blind mechanical criterion) showed native **worse** on the registered decision metric (2.14× noncached input) despite a higher cache ratio, so the removal decision followed its preregistered rule — on n=1, which the artifacts disclose. Quota-exhaustion claims are correctly hedged in the doc ("not established").

## 8. Process/history audit

Substantially clean. The native-codex detour was user-initiated, honestly bounded, fully reverted (zero residue at HEAD; `src/labrat/mcp/` has no diff vs master), and its negative result recorded accurately. The Ledger pause, v1→v2 recovery switch (including the interrupted v1 third trial), and no-relabeling constraint were all disclosed in-session and match artifacts. Every number reported to the user at campaign end matches what this audit independently recomputed. One mitigated lapse (P2-6). Web searches in the rollout were all API-documentation lookups; no artifact tampering or hand-crafted rows; no secrets in cited excerpts. Memory-anchored assumptions were re-validated by fresh telemetry before being relied on.

## 9. Coverage and limitations

Inspected: full branch diff (34 files, verified list) + all 48 commits + 13 removed detour paths via git objects + stash list (read-only); provider/loop/runner/session/primitives/suite/shards/taint/bundle sources in full; ~146 test bodies/names across the touched test files; all 270 selected traces event-by-event + excluded v1 + PR #65 trace archive listing; 15 source-shard `trials.jsonl` (infra census); all 8 ablation dirs + 5 tier dirs + 8 cache-diagnostic dirs + all launch configs; both `.mjs` generators + campaign summary + rendered report artifacts; both audit-generator scripts; 540 answers regraded; 108 validator/GT blobs; the 57MB rollout (all 66 user messages, targeted segments, all web-search events) + 4 memory files + pasted spec. Gates re-run: pytest 1383 passed/7 skipped; ruff clean; canonical pyright clean.

Not fully verifiable: content of the 25+~110 overwritten attempt traces (rows survive, traces don't); the rollout's 827 inter-agent payloads (sampled) and 17 compaction elisions; raw model bodies (absent by trace contract); provider-side cache TTL (inferred ~≤1h from public behavior).

## 10. Pre-PR checklist

Blocking (minutes of work, wording only — no rerun needed):
1. Reword "dirty/modified BSON" in `docs/dab-solultra-ablation.md` + report artifact: 5 files, byte-identical to committed blobs, LFS clean-filter artifact.
2. Soften "preregistered stratified-plus-noncached promotion rule" to match what was registered.
3. Scrub absolute `/Users/ege/...` paths from the shipped `config.json` copy (or ship the bundle-script output, which already scrubs).

Required PR disclosures: hints=Yes; the AG News per-task caps (`agent_max_turns=10`, `terminalize_timeouts`, 200-row classifier cap — none consumed) with the fact they yielded 0/10 failures (score-conservative); the 16 suspicious-but-not-proven CRM trials; `reset_on_attempt` attempt-trace overwrites; the `sys.path` note for independent regrading.

Maintainer questions to ask on the PR: (a) is the literal terminal artifact `[trial exhausted …]` an acceptable representation for unanswered trials, or do they prefer empty answers? (b) are per-task budget caps acceptable as disclosed given they only bound cost on always-failing tasks?

Follow-ups (post-PR, non-blocking): P2-1 dispatch re-raise gating; P2-2 comment fix + taint-gate hardening; P2-3 structured terminal flag; P2-7 base-shard compat gate + test; drop/ticket the stashes; compute (don't hardcode) stratified scores in the report builder; align the noncached definition.

---

## Machine-readable appendix

```json
{
  "audit": {"date": "2026-07-16", "head": "28be10ce91dbe0be6644bfa9ddd08ea212838f16", "baseline": "945534f1acac38a2cac2bdbd27e3df254e3681ec", "branch_commits": 48, "dab_pinned": "ca45478a102792c8acbe5d19c8bcb2fb58827557"},
  "verdicts": {"overall": "ship_with_minor_corrections", "code_safety": "pass", "numerical_correctness": "pass", "trace_integrity": "pass", "experimental_claims": "pass_with_caveats", "pr65_comparison": "pass", "dab_eligibility": "eligible", "package": {"trace_complete": true, "schema_valid": true, "reproducible": true, "ethically_disclosed": "yes_after_checklist", "leaderboard_eligible": true}},
  "package": {"rows": 270, "unique_keys": 270, "tasks": 54, "passes_stored": 206, "passes_regraded": 206, "regrade_mismatches": 0, "validator_errors": 0, "micro": 0.762962962962963, "stratified": 0.7417582417582418, "infra_selected": 0, "terminal_turn_budget": 10, "traces": 270, "trace_events": 10591, "empty_traces": 0, "symlinks": 0},
  "usage": {"input": 229857456, "cached": 163544576, "noncached": 66312880, "output": 3240606, "requests": 13174, "all_requests_completed": true, "cache_ratio": 0.71150433},
  "hashes_match": {"config.json": true, "trials.jsonl": true, "submission.json": true, "taint.json": true, "pr65_comparison_json": true, "trace_integrity_270_json": true, "labrat_traces_tar_gz": true},
  "rebuild": {"byte_identical_artifacts": 4, "byte_identical_scratch_files": 1789, "v1_rows_in_package": 0},
  "taint": {"automated_clean": 270, "human_clean": 254, "suspicious_not_proven": 16, "contaminated": 0},
  "pr65": {"current": 206, "pr65": 176, "delta_passes": 30, "delta_micro_pp": 11.1111, "delta_stratified_pp": 13.2937, "matrix": {"both_pass": 154, "current_only": 52, "pr_only": 22, "both_fail": 42}, "gains": 23, "regressions": 8, "ties": 23, "validator_gt_identity": "108/108"},
  "ablation": {"raw": {"baseline": 35, "cartograph": 33, "levers": 33, "hints": 40, "ledger": 39}, "stratified": {"baseline": 74.4048, "cartograph": 65.6746, "levers": 65.6746, "hints": 83.5317, "ledger": 83.9286}, "ledger_noncached_delta": -750581, "ledger_noncached_delta_pct": -6.5503, "warm_state_share_of_delta": "<=1.9%", "promotion": "defensible_preregistered"},
  "tiers": {"luna_max": "18/18, 9 passes", "terra_high": "14/18, 7", "sol_high": "9/18, 5", "sol_ultra": "6/18, 3, true_rate_limit_stop", "promotion_supported": false},
  "gates": {"pytest": "1383 passed, 7 skipped", "ruff": "clean", "pyright_canonical": "clean", "pyright_wide_invocation_errors": 62, "wide_errors_in_branch_files": 0},
  "bson_status": {"flagged": 5, "actually_dirty": 0, "cause": "lfs_clean_filter_artifact"},
  "findings": {"P0": [], "P1": [], "P2": ["P2-1 dispatch re-raise product leak", "P2-2 taint gate thin + false sandbox comment", "P2-3 sentinel terminal classification", "P2-4 16 CRM suspicious-not-proven", "P2-5 no raw bodies (disclosed)", "P2-6 process lapse Luna-Low smoke (mitigated)", "P2-7 base-shard compat keys unenforced"], "P3_count": 12},
  "headline_reproduction": "all_independently_reproduced_none_plausibility_only"
}
```

**Reproduction statement:** every headline number in this report — 206/270, 76.296296%, 0.7417582417582418, the usage totals and 71.150433% cache ratio, the 10 terminal rows, all four package hashes and three audit-artifact hashes, the byte-identical rebuild, 176/270 and 60.8822% for PR #65, the +30/+11.11pp/+13.29pp deltas, the 154/52/22/42 matrix, all five ablation-arm scores and the −750,581 noncached delta, and the tier-arm counts — was **independently recomputed from primary artifacts** (raw trials/traces/git objects/pinned validators), not accepted on plausibility.
