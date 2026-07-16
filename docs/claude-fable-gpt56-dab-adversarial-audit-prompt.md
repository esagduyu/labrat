# Claude Fable prompt: adversarial audit of the GPT-5.6 DAB campaign

Copy everything below the horizontal rule into a fresh Claude Fable task with
read-only access to the local filesystem and repositories named below.

---

You are Claude Fable acting as an independent, adversarial senior reviewer.
Audit a long-running LabRat GPT-5.6/DataAgentBench implementation and evaluation
campaign. Assume every existing report, generated audit, score, and conclusion
may be wrong until you reproduce it from primary artifacts. Do not optimize for
agreement with the author. Look for evidence that would invalidate the code,
the experimental conclusions, the prompt-caching claims, the trace-integrity
claims, or the proposed 270-row submission.

## Operating rules

1. Work read-only. Do not edit source files, run artifacts, traces, Git history,
   the DataAgentBench checkout, or Codex memory/session files. You may write only
   your final report and temporary files under `/tmp` if the environment requires
   it. Do not run any command that can overwrite a run directory.
2. Start from primary evidence: Git objects, source code, raw `trials.jsonl`,
   `submission.json`, `config.json`, `taint.json`, every selected
   `agent_tool_calls.jsonl`, pinned DAB validators/ground truth, and the PR #65
   artifacts. Treat Markdown reports and precomputed JSON audits as claims to
   test, not sources of truth.
3. Do not silently repair, normalize, exclude, or reinterpret any row. Report
   malformed, missing, duplicate, ambiguous, or inconsistent evidence exactly
   as found.
4. Use exact `(task_id, trial_num)` keys. Separate semantic failures from
   `infra:*` rows, historical attempts, partial attempts, cancelled attempts,
   and the ten bounded recovery failures.
5. Findings come first. Assign severity:
   - P0: invalidates or contaminates the submission/results.
   - P1: likely correctness, integrity, or reproducibility failure.
   - P2: material limitation, misleading conclusion, or unsafe edge case.
   - P3: maintainability/documentation issue with limited result impact.
6. Every finding must cite exact evidence: absolute file path plus line number
   for code/docs, or task/trial plus trace event number/JSON pointer for run
   artifacts. Distinguish confirmed fact, strong inference, and unresolved risk.
7. If an audit area is clean, state what you inspected and the coverage achieved;
   do not merely say “no issues found.”
8. Do not use the benchmark's web-search prohibition as a restriction on your
   review. You may read the public PR and authoritative documentation, but record
   every network source used. Prefer the pinned local Git objects where possible.

## Repositories, branches, and immutable anchors

- LabRat repository: `/Users/ege/repos/labrat`
- Branch under audit: `feat/codex-caching-gpt56`
- LabRat baseline: `origin/master` at
  `945534f1acac38a2cac2bdbd27e3df254e3681ec`
- Expected audited LabRat head at prompt creation:
  `28be10c` (`docs: finalize GPT-5.6 DAB evidence`)
- Branch span: 48 commits after the baseline. Verify this yourself; if HEAD has
  moved, record the new HEAD and review the additional commits separately.
- DataAgentBench repository: `/Users/ege/repos/DataAgentBench`
- Pinned DAB regrade/current checkout:
  `ca45478a102792c8acbe5d19c8bcb2fb58827557`
- PR #65 base:
  `6b86ca7089d05f0e0b3f09acf8cdd181bdff9225`
- PR #65 head:
  `8f3cdaeed37cb335b5314dcaf7e9ce057235c327`
- Official comparison target:
  `https://github.com/ucbepic/DataAgentBench/pull/65`
- Original pasted campaign specification:
  `/Users/ege/.codex/attachments/188852e8-2f76-4ce7-8ae7-09ed6e8e365d/pasted-text-1.txt`

The DAB checkout uses Git LFS. A normal `git status` may fail while trying to
write `.git/lfs/tmp` in a restricted environment. Do not mistake that for a
dirty or clean verdict. If necessary, inspect Git objects and use read-only
filter overrides, then explain exactly how you assessed worktree identity.

## Codex memory and full-session audit trail

These are context and process evidence, not authoritative technical truth:

- `/Users/ege/.codex/memories/memory_summary.md`
- `/Users/ege/.codex/memories/MEMORY.md`
- `/Users/ege/.codex/memories/raw_memories.md`
- `/Users/ege/.codex/memories/extensions/ad_hoc/instructions.md`
- `/Users/ege/.codex/memories/rollout_summaries/2026-06-23T03-52-03-9ELQ-labrat_full_stack_agent_runtime_plan_with_positioning.md`
- Full 57 MB Codex task rollout for this campaign:
  `/Users/ege/.codex/sessions/2026/07/10/rollout-2026-07-10T23-42-49-019f4fea-18c9-7f90-bab6-577e559a7ac3.jsonl`

Use the full rollout to audit process claims: what was run, stopped, resumed,
retried, delegated, overwritten, reverted, or represented to the user. Search it
selectively by command, commit SHA, run directory, task ID, `rate_limit`,
`timeout`, `terminal:turn_budget`, `Luna Low`, and `Luna Max`; do not load all 57
MB into context at once. Identify discrepancies between the conversation/process
record and persisted artifacts.

Subagents have separate rollout JSONL files rather than being fully embedded in
the parent file. Discover every rollout that references this task's parent thread
ID, then inspect the first JSON object in each file to distinguish actual
`source.subagent.thread_spawn` children from copied/forked parent context:

```bash
rg -l '019f4fea-18c9-7f90-bab6-577e559a7ac3' \
  /Users/ege/.codex/sessions/2026/07 --glob '*.jsonl'

# For each returned file:
head -1 <rollout.jsonl> | jq -c \
  '.payload | {id, source, cwd, originator}'
```

Review the true child rollouts as primary process evidence, including their tool
calls and terminal status. Do not infer that a subagent's final summary captures
all actions it took.

## Complete Git scope

First reproduce these inventories rather than trusting the lists:

```bash
cd /Users/ege/repos/labrat
git status --short --branch
git rev-parse HEAD origin/master
git rev-list --count origin/master..HEAD
git log --reverse --format='%h %s' origin/master..HEAD
git diff --stat origin/master...HEAD
git diff --name-status origin/master...HEAD
git log --format= --name-only origin/master..HEAD | sed '/^$/d' | sort -u
```

### Files remaining in the net branch diff

Review all 34 files, not just the final recovery commits:

```text
.gitignore
docs/codex-caching-investigation.md
docs/dab-integration.md
docs/dab-solultra-ablation.md
docs/superpowers/plans/2026-07-11-gpt56-dab-experiments.md
scripts/build_dab_trace_bundle.py
scripts/dab_shards.py
scripts/eval_dab.py
src/labrat/agent/loop.py
src/labrat/agent/providers/__init__.py
src/labrat/agent/providers/base.py
src/labrat/agent/providers/codex_subscription.py
src/labrat/agent/runner.py
src/labrat/agent/session.py
src/labrat/agent/tools/base.py
src/labrat/agent/tools/llm_classify.py
src/labrat/agent/tools/llm_extract.py
src/labrat/agent/tools/llm_primitives.py
src/labrat/agent/verifier.py
src/labrat/eval/benchmarks/dab/suite.py
src/labrat/eval/benchmarks/dab/taint.py
tests/unit/test_agent_runner.py
tests/unit/test_agent_runner_llm_fn.py
tests/unit/test_claude_mcp_prompt.py
tests/unit/test_codex_subscription_provider.py
tests/unit/test_dab_infra_patterns.py
tests/unit/test_dab_prompt_levers.py
tests/unit/test_dab_shards.py
tests/unit/test_dab_suite_run_trial.py
tests/unit/test_dab_taint.py
tests/unit/test_dab_trace_bundle.py
tests/unit/test_eval_dab_runner.py
tests/unit/test_llm_classify_tool.py
tests/unit/test_llm_primitives_engine.py
```

### Historically touched files that were later reverted or removed

The 48-commit session touched 47 unique paths. The following additional paths
do not remain in the net diff but must still be reviewed as process/history
evidence, especially for residual behavior or incorrect conclusions after the
native-host detour was removed:

```text
docs/superpowers/plans/2026-07-11-native-codex-mcp-isolation.md
docs/superpowers/plans/2026-07-11-native-codex-mcp-runtime.md
docs/superpowers/specs/2026-07-11-native-codex-mcp-dab-design.md
scripts/diagnose_codex_host_cache.py
src/labrat/eval/benchmarks/dab/codex_host.py
src/labrat/eval/benchmarks/dab/tool_profiles.py
src/labrat/mcp/policy.py
src/labrat/mcp/server.py
tests/unit/test_dab_codex_host.py
tests/unit/test_dab_tool_profiles.py
tests/unit/test_diagnose_codex_host_cache.py
tests/unit/test_mcp_policy.py
tests/unit/test_mcp_server.py
```

Use `git log --all -- <path>` and `git show <commit>:<path>` to inspect removed
versions. Confirm that commits `9af09bf`, `20cd8f1`, and `6bf32c8` actually
remove the intended diagnostic scope and leave no production/runtime residue.
Also inspect the stash refs shown by `git log --all` only as historical evidence;
do not apply them.

## High-risk implementation areas

Review the complete branch diff and commit evolution, with special attention to:

1. **Codex subscription and prompt caching**
   - cache-key stability and isolation across task, trial, retry, model, and host;
   - exact-replay versus initial-full request construction;
   - reasoning-item passback, response IDs, conversation IDs, fallback behavior,
     compatibility retries, and whether any fallback silently destroys cacheability;
   - token accounting: input, cached, cache-write presence, noncached, output,
     reasoning, requests, HTTP attempts, and partial/incomplete requests;
   - pacing, retry, rate-limit classification, subscription-limit behavior, and
     whether reported cache ratios can be inflated, double counted, or selectively
     omitted;
   - secrets or authentication material in persisted metadata/traces.

2. **DAB runner, trace, and failure semantics**
   - semantic versus `infra:*` classification;
   - retry preservation and `trace_attempt_policy="reset_on_attempt"` behavior;
   - whether trace resets overwrite evidence that reports imply is preserved;
   - timeout cancellation and missing in-flight events;
   - the `runner_timeout` and `runner_turn_budget` terminal events;
   - exact equality between submitted terminal artifacts and final trace outputs;
   - whether max-turn exhaustion is reliably exposed through AgentLoop, runner,
     DAB suite, JSON serialization, report generation, and regrade paths;
   - fail-fast behavior for 429/rate limit/exit 4 and sibling-worker safety.

3. **Nested LLM primitives and bounded classification**
   - model-tier and effort propagation for main agent, verifier, extractor, and
     classifier paths;
   - cumulative 200-row classifier budget across multiple calls, off-by-one and
     concurrency races, empty inputs, batch sizes, retries, and partial failures;
   - the batched-classification implementation and whether it changes semantics;
   - whether any path can silently fall back to Luna Low or another model;
   - whether the final ten recovery trials actually used Luna Max and why the
     main-turn cap bound before `llm_classify` was called.

4. **Sharding, merging, and bounded recovery assembly**
   - disjoint task guarantees and duplicate-key rejection;
   - config compatibility and the meaning of permitted config deltas;
   - refusal to overwrite an existing semantic key;
   - exact 54-task × five-trial coverage and deterministic ordering;
   - safe trace copying, symlink/path traversal/collision handling, temporary
     directories, atomic replacement, cleanup, and behavior on partial failure;
   - taint gate coverage and whether a clean top-level verdict can mask malformed
     or missing per-trial evidence;
   - whether `merge_bounded_recovery` creates a truthful, DAB-compatible config
     rather than post-hoc normalization;
   - whether source infra rows remain recoverable and whether the final package's
     omission of them is represented accurately.

5. **Trace bundle and taint policy**
   - completeness, schema validation, path safety, answer/trace consistency,
     manifest integrity, archive reproducibility, and accidental leakage;
   - false negatives/positives in web, filesystem, validator, answer-key,
     benchmark-repository, and ground-truth detection;
   - whether `search_reference_docs`, `search_trails`, shell-like strings, URLs,
     SQL functions, or tool aliases can bypass the checks.

6. **Tests and documentation**
   - look for tests that prove only mocked happy paths, mirror implementation
     bugs, or omit cancellation/concurrency/error cases;
   - identify material behavior not covered by tests;
   - check every numerical and causal statement in the final docs against raw
     artifacts;
   - distinguish descriptive evidence from model-only, feature-only, or causal
     claims.

## Generated run-artifact scope

Run artifacts are intentionally Git-ignored, so `git diff` cannot audit them.
Recursively inventory and checksum every regular file in the following
directories. Do not follow symlinks. Report missing files, symlinks, unexpected
file types, duplicate bytes where uniqueness was expected, and mutable files
whose hashes differ after your read-only audit.

Also inspect these top-level campaign launch configurations and the entire audit
and rendered-report trees; several generators and intermediate/partial reports
are ignored by Git and otherwise easy to miss:

```text
/Users/ege/repos/labrat/runs/dab/ablation-gpt56-luna-max-hints-config.json
/Users/ege/repos/labrat/runs/dab/ablation-gpt56-luna-max-ledger-config.json
/Users/ege/repos/labrat/runs/dab/ablation-gpt56-luna-max-levers-config.json
/Users/ege/repos/labrat/runs/dab/submission-gpt56-luna-max-ledger-config.json
/Users/ege/repos/labrat/runs/dab/tier-gpt-5.6-luna-max-config.json
/Users/ege/repos/labrat/runs/dab/tier-gpt-5.6-terra-high-config.json
/Users/ege/repos/labrat/runs/dab/tier-gpt-5.6-sol-high-config.json
/Users/ege/repos/labrat/runs/dab/tier-gpt-5.6-sol-ultra-config.json
/Users/ege/repos/labrat/runs/dab/audits
/Users/ege/repos/labrat/runs/dab/reports/gpt56-campaign
```

### Prompt-cache and host diagnostics

```text
/Users/ege/repos/labrat/runs/codex-host-cache/20260712-luna-low-ab
/Users/ege/repos/labrat/runs/codex-host-cache/20260712-luna-low-ab-retry1
/Users/ege/repos/labrat/runs/codex-host-cache/20260712-luna-low-ab-retry2
/Users/ege/repos/labrat/runs/codex-host-cache/20260712-luna-low-ab-retry3
/Users/ege/repos/labrat/runs/dab/cache-baseline-gpt56-luna-low
/Users/ege/repos/labrat/runs/dab/cache-fixed-gpt56-luna-low
/Users/ege/repos/labrat/runs/dab/cache-fixed-paced-gpt56-luna-low
/Users/ege/repos/labrat/runs/dab/cache-fixed-warm-gpt56-luna-low
```

Determine whether the native-vs-responses diagnostic was actually complete
enough to support its decision rule, and whether later documentation accurately
describes the failed/removed native arm.

### Five grounding-feature arms

```text
/Users/ege/repos/labrat/runs/dab/ablation-gpt56-luna-max-baseline
/Users/ege/repos/labrat/runs/dab/ablation-gpt56-luna-max-cartograph
/Users/ege/repos/labrat/runs/dab/ablation-gpt56-luna-max-levers-shards
/Users/ege/repos/labrat/runs/dab/ablation-gpt56-luna-max-levers
/Users/ege/repos/labrat/runs/dab/ablation-gpt56-luna-max-hints-shards
/Users/ege/repos/labrat/runs/dab/ablation-gpt56-luna-max-hints
/Users/ege/repos/labrat/runs/dab/ablation-gpt56-luna-max-ledger-shards
/Users/ege/repos/labrat/runs/dab/ablation-gpt56-luna-max-ledger
```

Independently verify the matched 45-row denominators, task/trial identity,
feature flags, model/effort, score calculations, retry handling, trace coverage,
and usage aggregates. Claims to challenge:

- Bare baseline: 35/45, 74.4% stratified.
- Cartographer: 33/45, 65.7% stratified.
- Levers: 33/45, 65.6746% stratified.
- Hints: 40/45, 83.5317% stratified and best raw accuracy.
- Ledger: 39/45, 83.9286% stratified, 10,708,211 noncached input, promoted by
  the preregistered stratified-score-plus-noncached-input rule despite one fewer
  raw pass and higher requests/latency than Hints.
- Ledger allegedly reduces noncached input by 750,581 tokens / 6.5503% versus
  Hints. Verify both numerator and denominator and whether retry overhead changes
  the conclusion.

### Hard-tail tier study

```text
/Users/ege/repos/labrat/runs/dab/tier-gpt-5.6-luna-max-shards
/Users/ege/repos/labrat/runs/dab/tier-gpt-5.6-luna-max
/Users/ege/repos/labrat/runs/dab/tier-gpt-5.6-terra-high-shards
/Users/ege/repos/labrat/runs/dab/tier-gpt-5.6-sol-high-shards
/Users/ege/repos/labrat/runs/dab/tier-gpt-5.6-sol-ultra-shards
```

Verify that tasks and flags are identical where evidence exists. Do not compare
survivor-only accuracy as though arms were complete. Challenge the claims that
Luna Max is the only complete tier result (18/18, 9 passes), Terra has 14/18
semantic rows, Sol High 9/18, and Sol Ultra 6/18 before a true rate-limit stop.
Determine whether any higher-tier evidence supports a cost/performance promotion.

### Full campaign, excluded attempts, recovery, and final package

```text
/Users/ege/repos/labrat/runs/dab/submission-gpt56-luna-max-ledger-shards
/Users/ege/repos/labrat/runs/dab/recovery-gpt56-luna-max-agnews-bounded
/Users/ege/repos/labrat/runs/dab/recovery-gpt56-luna-max-agnews-bounded-v2
/Users/ege/repos/labrat/runs/dab/smoke-gpt56-luna-max-batched-agnews3
/Users/ege/repos/labrat/runs/dab/smoke-gpt56-luna-max-classifier-low-agnews3
/Users/ege/repos/labrat/runs/dab/submission-gpt56-luna-max-ledger-final-270
```

The first recovery directory is excluded v1 evidence and must remain excluded:
it contains two committed rows plus an interrupted third trial without the
required terminal trace event. Verify that no v1 row entered the final package.

The accepted v2 recovery must contain exactly `agnews:3` and `agnews:4`, trials
0-4, with Luna Max main and classifier config, ten main turns, 200 cumulative
classification rows, normal 1,200-second timeout, no Luna Low, no 429, and no
relabeling of historical timeout attempts. The stated result is 0/10, all
`terminal:turn_budget`, each artifact exactly
`[trial exhausted 10-turn budget without a final answer]`, each trace nonempty
and ending in matching `runner_turn_budget`. The row budget allegedly was never
consumed because no trial called `llm_classify`. Verify this from raw traces and
usage metadata.

## Final 270-row submission validation

Treat this directory as the proposed handoff package:

`/Users/ege/repos/labrat/runs/dab/submission-gpt56-luna-max-ledger-final-270`

At prompt creation it is about 71 MB and should contain:

- `config.json`
- `trials.jsonl`
- `submission.json`
- `report.md`
- `taint.json`
- `scratch/<task-trial>/agent_tool_calls.jsonl` for exactly 270 task/trial keys

Expected SHA-256 values at prompt creation:

```text
fea3b3533f14f000f95b55124b37e623940e71b5672633bfc91806367772ec66  config.json
540999c5695d42d6847af51a688be3127e86d86cce9a06d0f7f8465309f27ea9  trials.jsonl
9b93e29d3edfcbc8a9cd8b6bbfc0994e2714f6ea87d7940179387ab5d0073ea5  submission.json
125e2d19254cab9150d0454913719b13236f81405806372be9f299d5da2e3a43  taint.json
```

Independently validate all of the following:

1. Exactly 270 trial rows, 270 submission rows, 270 unique exact keys, 54 tasks,
   trials 0-4 for every task, no extras, no duplicates, no selected `infra:*`
   rows, and no missing/empty selected trace.
2. Exactly 206 official validator passes; micro rate 76.296296%; full
   dataset-stratified score 0.7417582417582418 across 12 datasets. Recompute from
   the pinned DAB validators, not stored `passed` fields.
3. Usage totals: 229,857,456 input; 163,544,576 cached; 66,312,880 noncached;
   3,240,606 output; 13,174 completed requests; 71.150433% aggregate cache-read
   ratio. Check request-level sums, missing fields, cache-write presence, partial
   requests, and arithmetic consistency.
4. Exactly ten selected `terminal:turn_budget` rows, all in the intended AG News
   slots, and no other task-specific config mutation.
5. Each submission answer exactly corresponds to its trial artifact and trace;
   for the ten terminal rows, final trace output exactly equals the terminal
   artifact. Check whether DAB's public submission schema and trace contract
   allow this representation.
6. `config.json` truthfully records base shard deltas, recovery run paths,
   task-specific overrides, model/effort, row cap, turn cap, timeout behavior,
   and preserved-source infra policy. Flag absolute local paths or nonportable
   metadata that should not ship upstream.
7. All trace paths are safe, regular, non-symlink files. Parse every JSONL event;
   report schema failures, duplicate/colliding traces, impossible ordering,
   missing terminal usage, and artifact/trace mismatch.
8. Rebuild the package independently into `/tmp` from the source shards and v2
   recovery using `scripts/dab_shards.py recover`; byte-compare all deterministic
   outputs and semantically compare any intentionally nondeterministic fields.
   Never target the existing final directory.
9. Determine whether task-specific evaluator caps are permitted by DAB's actual
   submission rules. Do not accept the local report's “ready for maintainer
   review” wording without checking. State whether the package is:
   - technically trace complete,
   - schema valid,
   - reproducible,
   - ethically disclosed,
   - and eligible for an official PR/leaderboard,
   as five separate verdicts.

## Full trace-integrity and anti-cheating audit

Parse all 270 selected traces event-by-event and separately inspect excluded v1,
source retry/infra traces that still exist, and the PR #65 trace archive. Search
inputs and outputs, not merely tool names. At minimum test for:

- web/browser/search/fetch/HTTP/network access or URLs;
- shell or subprocess escape;
- arbitrary filesystem reads outside sanctioned benchmark data;
- direct reads of `ground_truth.csv`, `validate.py`, validators, expected answers,
  submission files, prior run answers, reports, trace archives, Git history, or
  benchmark repository source that reveals answers;
- `search_trails` returning nonempty historical answers;
- benchmark contamination or suspicious “expected answer,” “target,” or
  “validator” language;
- unsupported direct-answer behavior without local-data evidence;
- tools whose aliases or free-form arguments evade the current taint policy;
- answer fabrication, trace/answer divergence, post-hoc answer substitution, or
  a passing stored flag that fails official regrade;
- secrets, auth headers, conversation IDs, or sensitive user paths in artifacts.

For every questionable trial, provide task/trial, pass/fail, exact event number,
tool, relevant input/output excerpt or hash, why it is suspicious, and your final
classification: clean, suspicious-but-not-proven, or contaminated. Do not silently
exclude questionable failures or passes.

Claims to challenge:

- 270/270 automated taint clean.
- 254 human-classified clean, 16 suspicious-but-not-proven CRM trials, zero
  contaminated.
- No confirmed web/external result lookup, validator/ground-truth/answer-file
  access, prohibited filesystem access, or nonempty trail result.
- All 206 passing rows have persisted local-data evidence.
- Raw model prompt/response bodies are absent; 25 current-run infrastructure
  traces and 110 broader ablation/tier traces were overwritten under
  `reset_on_attempt`; cancelled timeouts may omit the final in-flight event.
  Determine how much these gaps weaken the verdict.

Existing audits to distrust and reproduce:

```text
/Users/ege/repos/labrat/runs/dab/audits/full-luna-trace-integrity-final.md
/Users/ege/repos/labrat/runs/dab/audits/full-luna-trace-integrity-final.json
/Users/ege/repos/labrat/runs/dab/audits/full-luna-trace-integrity-final-270.md
/Users/ege/repos/labrat/runs/dab/audits/full-luna-trace-integrity-final-270.json
```

Expected SHA-256 of the final supplement JSON at prompt creation:
`e0cca61436d86add02dc2164ddf2167b84a289db4b72152a9a93d2bf59d03d66`.

## Exact comparison with official PR #65

Audit these artifacts and their generators:

```text
/Users/ege/repos/labrat/runs/dab/audits/pr65-source/build_full_luna_pr65_comparison.py
/Users/ege/repos/labrat/runs/dab/audits/pr65-source/revalidate_pr65.py
/Users/ege/repos/labrat/runs/dab/audits/pr65-source/pr65-revalidation.json
/Users/ege/repos/labrat/runs/dab/audits/pr65-source/pr65-revalidation.csv
/Users/ege/repos/labrat/runs/dab/audits/pr65-source/labrat_traces.tar.gz
/Users/ege/repos/labrat/runs/dab/audits/full-luna-pr65-comparison-final.md
/Users/ege/repos/labrat/runs/dab/audits/full-luna-pr65-comparison-final.json
/Users/ege/repos/labrat/runs/dab/audits/full-luna-pr65-comparison-final.csv
```

Expected immutable hashes at prompt creation:

```text
99d3e7bd7d095bf16059850b4db499ae168ea53a8ed2951dccec662759285eb1  full-luna-pr65-comparison-final.json
08e3c54fe8e53b989ecffe626db27f0921adc5ea0d82d7e6d5dd8f4dff58477c  labrat_traces.tar.gz
```

Independently establish PR #65's exact 270 answers/traces from the pinned Git
objects/archive, regrade both answer sets under the same pinned validators, and
verify validator plus `ground_truth.csv` byte identity across relevant commits.
Check archive extraction for path traversal and do not trust filenames alone.

Claims to challenge:

- Current: 206/270 = 76.2963%.
- PR #65: 176/270 = 65.1852%.
- Delta: +30 passes, +11.1111 percentage points.
- Full dataset-stratified: 74.1758% versus 60.8822%, +13.2937pp.
- Task comparison: 23 gains, 8 regressions, 23 ties.
- Exact transition matrix: 154 both pass, 52 current-only, 22 PR-only, 42 both
  fail.
- Newly cleared: `github_repos:2`, `googlelocal:3`, `music_brainz_20k:1`,
  `music_brainz_20k:3`, `patents:2`, `yelp:2`, `yelp:4`.
- Lost: `agnews:2`, `agnews:4`, `github_repos:1`, `stockmarket:4`.
- Zero validator errors and zero current stored-pass/regrade mismatches.
- 54 validators plus 54 ground-truth files are 108/108 byte-identical to PR #65
  base; verify the claimed manifest hash from the JSON audit.
- The comparison is descriptive, not causal: PR #65 used Claude Sonnet 4.6 with
  Cartographer+Hints, while current uses GPT-5.6 Luna Max plus Cartographer,
  Levers, Hints, ContextLedger, a different host/runtime, and the disclosed AG
  News cap.
- The existing audit reports three dirty input BSON files despite a clean
  validator/ground-truth manifest. Independently resolve whether they are truly
  dirty, LFS artifacts, or a status-checking error, and assess result impact.

## Report and campaign-summary artifacts

Review the generators as code and recompute their claims:

```text
/Users/ege/repos/labrat/runs/dab/audits/summarize_campaign.mjs
/Users/ege/repos/labrat/runs/dab/audits/build_campaign_report.mjs
/Users/ege/repos/labrat/runs/dab/audits/campaign-summary.json
/Users/ege/repos/labrat/runs/dab/reports/gpt56-campaign/artifact.json
/Users/ege/repos/labrat/runs/dab/reports/gpt56-campaign/source-notes.md
/Users/ege/repos/labrat/runs/dab/reports/gpt56-campaign/report.html
/Users/ege/repos/labrat/docs/dab-solultra-ablation.md
```

Check aggregation formulas, denominators, cache ratios, public-API-equivalent
pricing, handling of cache-write telemetry, reasoning-token double counting,
infrastructure overhead, retry ceilings, and report/render drift. Do not let the
polish of the HTML/Markdown substitute for evidence.

## Suggested safe validation commands

Run commands only after reading their code and ensuring outputs go to `/tmp` or
are read-only:

```bash
cd /Users/ege/repos/labrat
uv run pytest tests/unit -q
uv run ruff check scripts src tests/unit
uv run pyright scripts src tests/unit
git diff --check origin/master...HEAD

# Read-only arithmetic checks
jq -s '{
  rows:length,
  unique_keys:(map(.task_id+":"+(.trial_num|tostring))|unique|length),
  passes:(map(select(.passed==true))|length),
  infra:(map(select((.reason//"")|startswith("infra:")))|length),
  terminal_turn_budget:(map(select(.reason=="terminal:turn_budget"))|length),
  input:(map(.meta.usage.input_tokens)|add),
  cached:(map(.meta.usage.cached_tokens)|add),
  output:(map(.meta.usage.output_tokens)|add),
  requests:(map(.meta.usage.requests)|add)
}' runs/dab/submission-gpt56-luna-max-ledger-final-270/trials.jsonl

find runs/dab/submission-gpt56-luna-max-ledger-final-270/scratch \
  -name agent_tool_calls.jsonl -type f -size +0c | wc -l
```

The previous reviewer run reported `1383 passed, 7 skipped`, Ruff clean, and
Pyright clean. Reproduce this; then assess whether test coverage is adequate.

## Required final deliverable

Return a standalone audit report with this structure:

1. **Executive verdict**
   - Ship / ship only with corrections / do not ship.
   - Separate verdicts for code safety, numerical correctness, trace integrity,
     experimental claims, PR #65 comparison, and DAB submission eligibility.
2. **Findings, ordered P0 to P3**
   - Evidence, impact, reproduction, and minimal remediation for each.
3. **Final submission validation table**
   - Expected versus independently observed for coverage, scores, usage, configs,
     traces, taint, terminal rows, and hashes.
4. **Trace-integrity table**
   - Counts by classification and every questionable task/trial with evidence.
5. **PR #65 exact-key comparison**
   - Per-dataset and per-task reconciliation, transition matrix, version/GT
     checks, and caveats.
6. **Feature and tier ablation verdict**
   - Which conclusions survive, which are descriptive only, and whether Ledger
     and Luna Max are defensible promotions.
7. **Prompt-caching verdict**
   - Whether caching is implemented correctly, whether 71.15% is reproducible,
     why it differs from 90–95% host-native observations, and whether the quota
     claims are supported.
8. **Process/history audit**
   - Reverted detours, stale or misleading docs, overwritten evidence, user-facing
     claim discrepancies, and memory/session assumptions that affected decisions.
9. **Coverage and limitations**
   - Exact files, commits, traces, rows, validators, and archive entries inspected;
     anything inaccessible or unverifiable.
10. **Pre-PR checklist**
    - Blocking fixes, required disclosures, regenerated artifacts, and exact
      maintainer questions about the AG News cap.

Include a compact machine-readable appendix (JSON in a fenced block) containing
the final counts, hashes, verdicts, and finding IDs. End with a clear statement
of whether you independently reproduced each headline number, not merely whether
it looked plausible.

Do not open, update, or comment on any PR during this audit. Do not commit fixes.
The user wants an independent adversarial report first.
