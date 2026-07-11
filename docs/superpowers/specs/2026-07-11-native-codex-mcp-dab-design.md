# Native Codex MCP DAB Runtime — Design

> **Status:** Design approved in conversation on 2026-07-11; written spec awaiting
> user review. This spec defines a native
> `codex-mcp` DataAgentBench driver, a submission-grade restricted MCP boundary,
> immutable experiment manifests, and the experiment sequence that decides whether
> native Codex and GPT-5.6 Luna Max earn the full 270-trial run.
>
> **Branch:** `feat/codex-caching-gpt56`.
> **Process:** Superpowers brainstorming → this spec → writing-plans →
> subagent-driven TDD → review → certification → experiments.

## 1. Executive decision

Build a native `codex-mcp` DAB driver in which Codex CLI owns the reasoning and
tool-use loop while LabRat owns task construction, the permitted data surface,
model and effort selection, traces, scoring, retries, and experiment manifests.

The driver has three explicit eligibility states:

```text
driver implemented
      ↓
diagnostic-only
      ↓ restricted-policy tests + trace reconciliation
policy-certified
      ↓ whole-host isolation + malicious-read canaries
submission-eligible
```

Native Codex may be used for cheap diagnostics after the first gate. It may host an
official DAB run only after all three gates pass. A native-tool configuration and a
post-hoc taint scan are necessary but are not, by themselves, a preventative data
boundary.

After certification, preserve and complete the existing GPT-5.6 Luna Max run,
perform a fresh same-model host-stack A/B, ablate the portable grounding features,
test the relevant AgentLoop-only features separately, and run the fixed hard-tail
model-tier study. Report those findings before launching the full 270-trial run.
Luna Max remains the default unless a larger tier satisfies the preregistered
hard-tail promotion rule.

## 2. Why this exists

LabRat currently has two materially different GPT-5.6 execution paths:

1. `labrat-agent` owns the loop and sends Responses requests through
   `CodexSubscriptionProvider`.
2. A proposed native path lets first-party Codex CLI own the loop and use LabRat's
   data tools over MCP.

The existing Responses path now achieves substantial cache reads, but its aggregate
cache-read rate remains around 69% on the paused Luna Max baseline and the run has
already exhausted ChatGPT subscription windows. The relevant cost is not cache
percentage alone. LabRat's in-process `AgentLoop` can still replay a large growing
history, while the first-party Codex host has its own prompt, compaction, cache, and
tool-loop policy.

A native driver is therefore the cleanest practical diagnostic for separating:

- model/provider effects;
- first-party-host effects;
- LabRat `AgentLoop` transcript growth;
- MCP versus in-process tool transport; and
- prompt-cache percentage versus absolute noncached input.

This is a **host-stack A/B**, not a pure API A/B. The native Codex system envelope is
an irreducible treatment difference and must be named in every report.

## 3. Goals

### 3.1 Runtime goals

- Add `codex-mcp` as a first-class DAB driver.
- Use ChatGPT/Codex subscription authentication without copying secrets into a run.
- Support GPT-5.6 Luna, Terra, and Sol with native reasoning effort selection.
- Capture native aggregate and request-index cache telemetry.
- Classify quota, auth, timeout, transport, policy, and trace failures correctly.
- Resume without mutating the model, task set, tool surface, benchmark version, or
  isolation contract.

### 3.2 Submission-integrity goals

- Publish only an explicit, task-scoped MCP tool surface.
- Authorize every MCP request server-side before normal LabRat dispatch.
- Reject SQL external scans, arbitrary attachments, arbitrary Mongo access, and
  identifier injection.
- Run the Codex host and MCP server inside the same submission isolation boundary.
- Make validators, answer keys, benchmark source, unrelated databases, user memory,
  and host files absent rather than merely forbidden by prompt.
- Require complete reconciled native and server traces for every semantic attempt.

### 3.3 Experiment goals

- Preserve the registered GPT-5.6 evidence already collected.
- Measure native Codex against the Responses adapter with a shared safe-core tool
  profile.
- Measure portable grounding features separately from AgentLoop-owned features.
- Determine whether Terra High, Sol High, or Sol Ultra clears hard tasks Luna misses.
- Select the cheapest configuration that meets the frozen quality and safety gates.

## 4. Non-goals

- Do not replace or rewrite `CodexSubscriptionProvider` as part of this driver.
- Do not claim that native Codex exposes the same hidden system prompt or context
  policy as LabRat's in-process loop.
- Do not use aggregate cache percentage as the sole efficiency decision metric.
- Do not make ContextLedger, `llm_extract`, `llm_classify`, or
  `dispatch_subagent` appear to work on an MCP host when their runtime dependencies
  are absent.
- Do not expose user Scent, Trails, Maps, harvested memory, or dbt state to DAB.
- Do not use `agnews` as a model-tier discriminator because its public label mapping
  is vulnerable to parametric-memory leakage. It remains in the official full run.
- Do not hand-edit, delete, or coerce retryable infrastructure rows into semantic
  failures.
- Do not call a diagnostic native run an official submission.

## 5. Architecture

The implementation is split into focused units so that the benchmark suite remains
an orchestrator rather than becoming another CLI/security monolith.

### 5.1 Native host adapter

A focused Codex host module owns:

- command construction;
- isolated `CODEX_HOME` selection;
- environment scrubbing;
- JSONL stdout capture;
- final-message extraction;
- aggregate usage parsing;
- request-index usage extraction from the matching private rollout;
- native-event validation; and
- auth, timeout, rate-limit, and transport classification.

`DabSuite` dispatches to this adapter and receives the same answer/tool-count/latency
shape used by the other drivers plus normalized usage metadata. The adapter does
not score answers or construct benchmark policies.

### 5.2 Generic MCP policy enforcement

The MCP server gains an optional fail-closed policy layer. When
`LABRAT_MCP_POLICY_PATH` is set, startup loads one immutable policy file, validates
its schema and digest, filters tool discovery, and authorizes each call before
registry dispatch. A missing, malformed, or digest-mismatched policy prevents server
startup.

The ordinary product MCP path remains unchanged when no benchmark policy is set.
The policy is additive and must not change `build_data_tools_registry()` semantics
or the legacy DAB tool surface.

### 5.3 DAB policy builder

A DAB-specific builder converts trusted `DabTaskEnv` state into a scrubbed policy:

- policy/schema version;
- trial identity;
- primary database alias;
- exact permitted tools;
- catalog-known relations;
- pre-attached source aliases;
- exact permitted Mongo sources;
- Cartographer availability;
- identifier, row, output, and SQL limits; and
- a canonical SHA-256 digest.

The policy is derived from benchmark configuration, never from model text. Paths and
credentials used to create isolated mounts or live connections are not copied into
the scrubbed policy artifact.

### 5.4 Whole-host isolation launcher

A dedicated launcher creates the submission environment for Codex CLI and the MCP
server together. The boundary is not an MCP-only container: the Codex host must also
be unable to inspect the DataAgentBench checkout or host filesystem.

### 5.5 Experiment manifest and resume guard

Every new run gets an immutable `experiment_manifest.json` containing:

- DAB commit;
- selected-task manifest hash, including prompts, DB configs, hints, and validators;
- LabRat commit and dirty-diff digest when applicable;
- Codex CLI version;
- model and native reasoning effort;
- driver and host eligibility state;
- tool profile, exact enabled-tool list, and tool-schema hash;
- policy version and policy-builder hash;
- prompt flags and prompt hash;
- isolation image/config digest;
- trace schema and attempt-reset policy; and
- task filter and expected semantic denominator.

Resume compares live state with this manifest before launching a model call. Any
drift fails with a precise conflict message. `config.json` remains the human-facing
run configuration, but the manifest is the immutable evidence contract.

Diagnostic runs may record a dirty-diff digest. Every registered comparison arm and
the full scoring run require a clean LabRat commit; a dirty worktree is a launch
error for those run classes.

### 5.6 Shared tool-profile resolver

One resolver maps a versioned profile name to exact tool names and canonical input
schemas. `labrat-agent` receives a registry filtered to that profile; the DAB MCP
policy builder, MCP discovery filter, and Codex `enabled_tools` override consume the
same resolved list. The manifest hashes the canonical schemas rather than assuming
that an MCP transport and an in-process provider serialize them identically.

This resolver is also the single compatibility seam for
`legacy-full-20260710`. A hidden/self-erroring tool is not silently retained in a
safe-core experiment merely because it exists in the global registry.

## 6. Codex CLI contract

The initial certified version is Codex CLI `0.144.1`. An upgrade requires rerunning
the native-event, policy, isolation, and malicious-read certification suite before
the new version becomes submission-eligible.

The command contract uses:

- `codex -a never exec --json`;
- `--ignore-user-config` and `--ignore-rules`;
- `--skip-git-repo-check`;
- an empty, absolute scratch working directory;
- a read-only Codex sandbox;
- an explicit GPT-5.6 model;
- an explicit native `model_reasoning_effort`;
- shell and unified execution disabled;
- web search disabled;
- browser, computer use, apps, plugins, image generation, workspace dependency
  installation, and multi-agent hosting disabled; and
- exactly one required LabRat MCP server with a second defense-in-depth
  `enabled_tools` list matching the server policy.

`OPENAI_API_KEY` and `CODEX_API_KEY` are removed from the child environment so the
run uses saved ChatGPT/Codex authentication. The driver never uses a
dangerous-bypass flag.

The diagnostic must **not** use `--ephemeral`. The emitted thread id is used to find
the one matching rollout inside the private `CODEX_HOME`, extract request-level
`token_count` events, and write a scrubbed `codex_token_usage.jsonl`. The raw private
rollout, config, history, and authentication files are never copied into `runs/` or
the trace bundle.

Native effort support is:

- Luna: `low`, `medium`, `high`, `xhigh`, `max`;
- Terra: `low`, `medium`, `high`, `xhigh`, `max`, `ultra`;
- Sol: `low`, `medium`, `high`, `xhigh`, `max`, `ultra`.

The tier study uses Luna Max, Terra High, Sol High, and Sol Ultra. Native Sol Ultra
means native `ultra` reasoning with Codex host subagents disabled. Controlled LabRat
delegation is evaluated separately so model effort and agent fan-out are not mixed.

## 7. Restricted DAB tool profile

### 7.1 `dab-core-v1`

The shared host-stack A/B and submission candidate expose only these policy-reviewed
tools:

- `profile_dataset`;
- `list_tables`;
- `describe_table`;
- `search_columns`;
- `link_schema`;
- `sample_rows`;
- `column_stats`;
- `run_sql`;
- `explain_sql`;
- `check_sql`;
- `explain_lineage`;
- `verify_join`; and
- `workflow`.

`search_reference_docs` is added only for a deterministic, hermetic Cartographer
arm. `load_mongo_collection` is added only when the task declares an exact permitted
Mongo database and collection and the policy supplies a bounded materialization
contract.

SQLite and PostgreSQL secondaries are attached server-side before model execution.
`attach_database` is not model-visible. This keeps paths, connection strings, and
credentials out of model arguments and traces.

Every listed helper must receive explicit policy coverage before the profile is
certified. A tool with incomplete argument validation fails certification; it is
never left available on the strength of a prompt instruction.

The name `dab-core-v1` is assigned only after every listed tool passes its policy
tests. Certification does not silently shrink or expand the profile. A changed list
requires a new versioned profile and a prospective experiment-manifest amendment;
both A/B hosts always receive the same final list and schema hash.

### 7.2 Explicit exclusions

The submission profile excludes:

- `load_file`;
- `attach_database`;
- `run_program`;
- `dispatch_subagent`;
- `llm_extract` and `llm_classify`;
- `search_trails`;
- user Scent, Map, Trail, harvested-memory, and dbt surfaces; and
- every native Codex command, patch, file, web, browser, computer, app, plugin,
  image, or subagent tool.

`run_program` is excluded even if its top-level MCP tool were allowlisted because it
constructs an internal registry and could otherwise launder access to a hidden
tool. It is evaluated only on the separately controlled `labrat-agent` feature arm.

### 7.3 SQL authorization

The policy parses DuckDB SQL and accepts exactly one query whose root is a `SELECT`,
CTE query, or set operation. It rejects:

- parse failures and multiple statements;
- `force=true`;
- DDL, DML, commands, `PRAGMA`, `COPY`, `ATTACH`, `INSTALL`, and `LOAD`;
- table sources whose AST target is not a plain identifier;
- `read_text`, CSV/JSON/Parquet readers, URLs, `glob`, `sqlite_scan`,
  `postgres_scan`, dynamic `query`, secrets/settings access, and equivalent external
  scan forms;
- relations absent from the authorized primary catalog, authorized pre-attached
  alias, or authorized Mongo temporary-table set; and
- limits or result requests above the policy ceiling.

Rejecting every non-identifier table source closes both named and obfuscated table
function paths. The same relation and identifier resolver protects `sample_rows`,
`column_stats`, `verify_join`, and other helpers that construct SQL from structured
arguments.

### 7.4 Mongo authorization

When Mongo is present, the policy permits only:

- the exact task database;
- exact declared collections;
- safe target identifiers;
- the exact trial primary;
- bounded row limits; and
- non-JavaScript query operators.

Other databases, `$where`, JavaScript-bearing operators, primary overrides, unsafe
target names, and undeclared collections are denied. The isolated environment uses
a benchmark-only Mongo principal scoped to the exact permitted database.

## 8. Isolation contract

The submission container runs as a non-root user and contains installed Codex CLI
and an installed LabRat package, not mounted source checkouts. It has:

- no DataAgentBench checkout;
- no validator, `ground_truth.csv`, expected answer, historical run, or user Maze;
- no host home directory;
- no Docker socket;
- one dedicated writable `CODEX_HOME` authentication/rollout volume;
- one writable per-attempt scratch directory;
- individual DuckDB/SQLite files mounted read-only rather than a whole
  `query_dataset` directory;
- a read-only scrubbed policy file;
- CPU, memory, process, and wall-clock limits; and
- only the network paths required for Codex inference and explicitly declared live
  DAB databases.

PostgreSQL uses a benchmark-only role with `CONNECT` and `SELECT` only on exact DAB
databases. Mongo uses a database-scoped benchmark principal. Port restriction alone
is not sufficient because an authorized server can contain unrelated databases.

Where reliable endpoint-level egress restriction for ChatGPT/Codex is not practical,
the MCP/data plane still receives no general internet access, and the event auditor
rejects any native web activity. The exact network policy and its digest are part of
the experiment manifest.

## 9. Trace and telemetry contract

Each attempt creates or truncates these artifacts before launch:

- `mcp_tool_calls.jsonl`: canonical server-side calls and denials;
- `codex_events.jsonl`: complete `codex exec --json` stdout;
- `codex_token_usage.jsonl`: scrubbed per-request usage extracted from the matching
  private rollout; and
- `mcp_policy.json`: scrubbed effective policy plus digest.

The host trace permits only the pinned lifecycle, reasoning, agent-message, and
LabRat MCP event schema. It rejects:

- command execution;
- file changes;
- web search;
- browser or computer activity;
- non-LabRat MCP calls;
- unknown tool-bearing items;
- malformed JSON or incomplete terminal state; and
- tool calls that do not reconcile one-for-one with the canonical MCP trace by
  order, name, normalized arguments, and terminal status.

Unknown events under a new CLI version are a certification failure, not something
the production auditor silently ignores.

The final answer is the last completed agent-message item. Aggregate usage comes
from `turn.completed.usage`. Request-level usage comes from deduplicated rollout
`token_count` events, using cumulative totals to remove exact duplicate events.
`input_tokens` already includes `cached_input_tokens`; therefore:

```text
cache_read_ratio = cached_input_tokens / input_tokens
noncached_input  = input_tokens - cached_input_tokens
```

There is no native cache-write token field, so reports label cache writes
unobserved rather than zero.

Trace bundling for `codex-mcp` requires all four artifacts, hashes each one, records
the policy and CLI version, and retains every infrastructure attempt alongside the
selected semantic attempt. Missing, malformed, mismatched, contaminated, or
unaudited artifacts make the trial `audit-error` and block a strict bundle.

An infrastructure failure before MCP startup or before the first native request may
leave one or more pre-created JSONL files empty; the attempt manifest records which
terminal state was never reached. Every selected semantic attempt requires valid
terminal native telemetry, a valid policy, and a valid canonical MCP trace. An empty
MCP trace is valid only when the clean semantic attempt genuinely made zero tool
calls.

## 10. Failure handling

### 10.1 Retryable infrastructure

Authentication expiry, transport interruption, timeout, process failure, and HTTP
429 are normalized into explicit `infra:*` reasons. The attempt row and its partial
telemetry remain append-only and are excluded from semantic denominators.

On the first new 429, the runner flushes a sanitized `infra:rate_limit` row, records
the reported reset when available, exits with the existing rate-limit code, and does
not launch another task. Resume retries that key in the same output directory.

### 10.2 Audit and policy failures

Missing policy, policy denial, forbidden native activity, malformed event streams,
trace disagreement, or isolation-canary failure is an `audit-error`. The runner
pauses immediately. It does not score the answer and does not automatically retry a
policy violation as though it were a stochastic model failure.

### 10.3 Semantic failures

Only a clean, trace-complete attempt reaching the normal DAB validator may become a
semantic pass or fail.

## 11. Existing Luna Max baseline

`runs/dab/ablation-gpt56-luna-max-baseline` remains append-only. Its current evidence
is 21 semantic attempts, 15 observed passes, and 14 retained infrastructure rows.
The semantic attempts report 13,521,875 input tokens and 9,349,632 cached input
tokens, a 69.15% aggregate cache-read ratio.

This run is not a minimal-tool control. Its traces used current full-runtime tools,
including `run_program`, `dispatch_subagent`, `search_trails`, `check_sql`, and
lineage. It is renamed analytically—not on disk—as:

```text
legacy-full-20260710
```

The compatibility profile freezes the exact 22-tool registry that existed when the
run began:

```text
profile_dataset, list_tables, describe_table, search_columns, link_schema,
sample_rows, column_stats, run_sql, explain_sql, check_sql, explain_lineage,
verify_join, attach_database, load_file, load_mongo_collection,
search_reference_docs, search_trails, workflow, llm_extract, llm_classify,
run_program, dispatch_subagent
```

Before resume, create a sidecar experiment manifest for this run without rewriting
its existing `config.json` or `trials.jsonl`. The manifest pins the original DAB
content and tool profile. Resume is allowed only if the live tool schema and selected
benchmark content match those fingerprints.

Synchronize upstream refs before scoring, but finish this partial arm from a clean
worktree pinned to its recorded DAB commit. Do not append results produced by a new
prompt, validator, data, or ground-truth snapshot. New arms and the full run use a
fresh, separately frozen upstream commit.

## 12. Experiment sequence

### 12.1 Certification probes

Run, in order:

1. no-model policy and MCP integration tests;
2. one cheap Luna-low native no-tool subscription probe;
3. one cheap Luna-low integrated `codex-mcp` canary; and
4. one cheap canary for each materially different database family after containment
   tests pass.

These are infrastructure evidence, not DAB score evidence.

### 12.2 Complete the legacy baseline

Resume the existing directory until all 45 registered keys have exactly one clean
non-infrastructure semantic attempt. Preserve all earlier infrastructure rows. Stop
on every new quota signal and resume after reset.

The completed legacy run is descriptive evidence about the current full-runtime
bundle. It is not reused as the safe-core host control.

### 12.3 Fresh host-stack A/B

Run Luna Max with three trials on this fixed six-task cohort:

```text
deps_dev_v1:1
deps_dev_v1:2
music_brainz_20k:1
music_brainz_20k:3
stockindex:3
yelp:1
```

Compare:

- `labrat-agent` + `dab-core-v1`; and
- `codex-mcp` + `dab-core-v1`.

Hold model, effort, prompt levers, hints, Cartographer, ledger, task order, timeout,
tool schemas, DAB commit, and scoring fixed. Alternate AB/BA host order across task
blocks and quota windows.

Native Codex wins the host gate only when:

1. every policy, isolation, and trace gate is clean;
2. its raw pass count is no more than one pass out of 18 below `labrat-agent`; and
3. its paired median noncached input per semantic trial is at least 25% lower.

If efficiency improves by less than 25%, native remains a useful diagnostic but does
not justify changing the official runtime. If safety or accuracy fails, the full run
uses `labrat-agent`.

### 12.4 Portable grounding ladder

On the winning host, run the existing 15-query, four-dataset cohort at `n=3` with
fresh immutable directories:

1. safe core;
2. `+Cartographer` and hermetic `search_reference_docs`;
3. `+` benchmark-safe prompt levers; and
4. `+` DAB hints.

The winner is the highest completed stratified Pass@1. Report dataset cuts and raw
passes. A tie prefers lower equivalent cost, then lower noncached input, then lower
latency.

ContextLedger is a separate `labrat-agent` H-versus-G comparison because MCP hosts
do not use LabRat's `AgentLoop`. A score tie plus a material noncached-input
reduction is positive; for this study, "material" means at least a 20% reduction in
paired median noncached input per semantic trial. An accuracy loss fails the ledger
guardrail.

### 12.5 Targeted last-week feature study

Do not treat all recently shipped features as one switch.

- Evaluate `run_program` and then `dispatch_subagent` as an incremental
  `labrat-agent` hard-tail ladder using explicit tool profiles.
- Evaluate `llm_extract`/`llm_classify` only when a static task audit identifies a
  legitimate row-level extraction/classification operation that is not dominated by
  public-label leakage. If no such DAB task exists, record the feature as not
  applicable rather than assigning it a zero.
- Evaluate the bounded verification-v2 composite on the hard tail after the base
  runtime winner is known; keep its multiplied model-call cost separate.
- Treat deterministic SQL checks and warnings as common substrate until a genuine
  null tool profile exists; do not claim a causal delta from their mere presence.
- Do not rerun semantic Scent in this sequence because it already measured
  net-negative.
- Keep TUI, harvested memory, dbt, Map, Trail, Cheese, and team-Scent surfaces out of
  DAB because they lack a fair benchmark corpus or are product-only paths.

### 12.6 Model-tier hard-tail study

Freeze the winning submission-eligible host, tool profile, grounding flags, DAB
commit, and task order. Run three trials for each model/effort arm on:

```text
crmarenapro:12
deps_dev_v1:1
music_brainz_20k:1
music_brainz_20k:3
pancancer_atlas:1
patents:2
```

Arms:

- GPT-5.6 Luna Max;
- GPT-5.6 Terra High;
- GPT-5.6 Sol High; and
- GPT-5.6 Sol Ultra.

A larger tier earns its cost only if it both improves the completed hard-tail
stratified score and obtains at least one pass on a query where Luna is 0/3. A tie or
swapped stochastic passes retains Luna. Choose the cheapest arm satisfying the rule.
Sol Ultra must add a clear beyond Sol High to justify its incremental effort.

### 12.7 Report before the full run

Publish a durable report with:

- stratified Pass@1, per-query results, raw passes, and zero-to-pass clears;
- aggregate and per-trial input, cached input, noncached input, output, and reasoning
  tokens;
- request-index cache curves;
- tool calls and completed model requests;
- latency and quota/reset incidence;
- public-API price equivalent, clearly labeled as an equivalent rather than a
  subscription invoice;
- trace/isolation eligibility; and
- the selected host, tool profile, grounding flags, model, and effort.

Cache comparison uses absolute noncached input and paired task results. Cache-read
percentage is supporting context, not the selection criterion.

### 12.8 Full trace-complete run

After the report freezes the winner, launch a fresh run on the 12 official datasets:

```text
agnews,bookreview,crmarenapro,deps_dev_v1,github_repos,googlelocal,
music_brainz_20k,pancancer_atlas,patents,stockindex,stockmarket,yelp
```

Use five trials for 54 queries: 270 semantic trials. Luna Max is the default if the
tier gate retains it. Stop on the first quota row and resume the same directory after
reset.

Completion requires:

- exactly 270 selected semantic attempts;
- all infrastructure attempts retained separately;
- 270 canonical tool traces;
- 270 valid native traces when `codex-mcp` is selected;
- clean policy, isolation, and taint results;
- a completed report and submission artifact; and
- a strict-official trace bundle with hashes.

Only then may the run be called trace-complete or submission-ready.

## 13. Certification and test design

### 13.1 Unit coverage

- Policy schema, digest, startup failure, tool filtering, and direct-call denial.
- Exact safe tool discovery under each task shape.
- SQL acceptance for safe joins, CTEs, windows, set operations, and authorized
  attached aliases.
- SQL denial for readers/scanners, URLs, `glob`, dynamic query functions,
  secrets/settings, multi-statements, `PRAGMA`, DDL/DML, and `force=true`.
- Identifier-injection denial for helper tools.
- Mongo denial for other databases, unsafe targets, primary overrides, `$where`, and
  excessive limits.
- Codex command construction, environment scrubbing, model/effort validation, and
  CLI-version pinning.
- Native final-message, aggregate usage, and request-usage parsing.
- Duplicate request-usage event removal.
- Native forbidden-event and unknown-event rejection.
- One-for-one host/server trace reconciliation.
- Retry trace truncation and retained infrastructure metadata.
- Experiment-manifest creation and every resume conflict.
- Diagnostic-only and strict-bundle eligibility gates.

### 13.2 Negative containment canaries

Create unique sentinels in:

- an unmounted filesystem path;
- an HTTP service on an unapproved endpoint;
- an unapproved PostgreSQL database; and
- an unapproved Mongo database.

Attempt access through native tools, SQL table functions, helper-argument injection,
attachment variants, Mongo variants, and symlink/path tricks. No sentinel may be
revealed and forbidden services must record zero successful access. Deliberately
enable one native tool in a test and prove the event auditor makes the attempt
ineligible.

### 13.3 Positive integration matrix

Run no-model MCP tests for:

- DuckDB plus SQLite;
- in-memory DuckDB plus PostgreSQL and SQLite; and
- in-memory DuckDB plus Mongo and SQLite.

Then run one cheap Codex canary for each materially different family. Require exact
tool discovery, successful approved queries, one-for-one traces, clean policy audit,
and no visible benchmark source or answer-key mount.

### 13.4 Regression gates

Before a certification commit or paid experiment:

```bash
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest -q
git diff --check
```

Known environment-only visual snapshot mismatches remain separately classified; no
new nonvisual failure is accepted.

## 14. Documentation and artifact updates

Implementation updates:

- `docs/dab-integration.md` for the new driver, policy, isolation, model/effort, and
  diagnostic/submission distinction;
- `docs/codex-caching-investigation.md` for native-versus-adapter cache evidence;
- `docs/dab-solultra-ablation.md` for the prospective amendment, legacy baseline
  correction, host A/B, feature results, tier results, and full-run decision; and
- trace-bundle documentation for the four-artifact native contract.

Reports must state which features are host-portable, AgentLoop-only, common
substrate, not applicable, or intentionally excluded. Historical Sonnet and GPT-5.5
deltas are priors, not causal controls under the current 22-tool runtime.

## 15. Key decisions

- **Restricted MCP plus whole-host isolation**, not either one alone.
- **Native driver starts diagnostic-only** and earns submission eligibility through
  executable canaries.
- **Server policy is authoritative**; Codex `enabled_tools` is defense in depth.
- **Pre-attach trusted SQLite/PostgreSQL sources** and hide attachment credentials
  from the model.
- **Freeze a shared safe-core profile** for host comparison.
- **Preserve the existing run as legacy full-runtime evidence**, not a bare control.
- **Separate portable grounding from AgentLoop-only features**.
- **Measure absolute noncached input**, not cache percentage alone.
- **Pass native Ultra directly but disable native subagents** so effort is not mixed
  with uncontrolled fan-out.
- **Report before launching the full run**.
- **Keep Luna Max unless a larger tier clears the preregistered promotion rule**.

## 16. Acceptance criteria

The design is successfully implemented when:

1. `codex-mcp` completes a safe, trace-complete Luna-low canary through native Codex
   CLI.
2. Every policy, SQL, Mongo, native-event, trace, manifest, and containment test is
   green.
3. A strict native bundle is impossible before submission eligibility and succeeds
   only after certification.
4. The legacy baseline resumes without tool, benchmark, or configuration drift.
5. The fresh host A/B produces paired accuracy and efficiency evidence.
6. Portable and AgentLoop-only feature results are reported without crossing runtime
   boundaries.
7. The fixed four-tier hard-tail table supports the Luna promotion decision.
8. The selected host/config is frozen before the full run.
9. The full run reaches 270 semantic attempts with complete traces and a clean strict
   bundle.
10. No secret, validator, ground truth, answer key, user memory, or unrelated
    database appears in a prompt, trace, manifest, bundle, or container mount.

## 17. References

Current Codex contracts used by this design:

- [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
- [Codex CLI reference](https://developers.openai.com/codex/cli/reference)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [Codex MCP configuration](https://developers.openai.com/codex/mcp)

Local evidence and operating contracts:

- `docs/dab-integration.md`
- `docs/dab-solultra-ablation.md`
- `docs/codex-caching-investigation.md`
- `docs/superpowers/specs/2026-06-22-codex-claude-parity-design.md`
- `docs/superpowers/plans/2026-06-22-full-stack-agent-runtime.md`
