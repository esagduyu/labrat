# DAB integration — technical reference

How the DataAgentBench harness (`src/labrat/eval/benchmarks/dab/`) works. This is the deep reference; `CLAUDE.md` keeps only the day-to-day facts and points here. For results/history/conclusions see [`dab-progress-report.md`](dab-progress-report.md).

[DataAgentBench](https://ucbepic.github.io/DataAgentBench/) — 12 official datasets, 54 queries, multi-DB (DuckDB, SQLite, PostgreSQL, MongoDB). Local repo (`~/repos/DataAgentBench`) has 17 directories — 5 are unofficial extras (civic_unstructured, cve, imdb, krama, usaspending) not in the official benchmark.

## Files
- `suite.py` — `DabSuite` implements `BenchmarkSuite`. Three drivers (below). `Driver = Literal["raw-bash", "labrat-agent", "claude-mcp"]`
- `env.py` — `build_dab_task_env(db_config_path) → DabTaskEnv(ctx, attachable)`. DuckDB clients become real `DuckDBConnection`s in `ctx.connections`; SQLite clients become `AttachSpec(alias, path, db_type)` entries the agent uses via the `attach_database` tool. Connections are not pre-`connect()`ed — the driver does that at trial start. Because connections aren't connected at build time, the catalogs it builds are empty (`Catalog(schemas=[])`); `introspect_env_catalogs(ctx)` repopulates them post-connect and is called by `_run_trial_labrat_agent` after it connects, so the catalog-backed tools (`list_tables` / `describe_table` / `column_stats` / `search_columns` / `profile_dataset`) actually work under the `labrat-agent` driver. The `claude-mcp` driver is unaffected (it introspects via the MCP server).
- `scorer.py` — imports each query's `validate.py`, adds DAB repo root to `sys.path` for `common_scaffold`
- `reporter.py` — writes `submission.json` in DAB leaderboard format
- `taint.py` — fail-closed submission audit over answer artifacts and the configured driver's canonical per-attempt tool trace
- `scripts/build_dab_trace_bundle.py` — packages a completed run, selected semantic-attempt traces, taint verdicts, and a hash/count manifest; `--strict-official` enforces the exact 54×5 matrix

## Three drivers (selected via `--driver` on `scripts/eval_dab.py`)

| Driver | Loop owner | Billing | Reliability | Use for |
|---|---|---|---|---|
| `raw-bash` (default) | claude CLI native Bash | Max plan | high | Reproducing Phase 1b 48.5% baseline |
| `labrat-agent` | `AgentLoop` + LabRat tools | depends on `--agent-provider` | high w/ anthropic; **fragile** w/ claude-code | metered API; cross-provider matrix |
| `claude-mcp` | claude CLI + LabRat MCP server | Max plan | high | **Recommended full-benchmark path** — LabRat tools, free per run |

```bash
uv run python scripts/eval_dab.py --driver raw-bash         # Phase 1b baseline (48.5% reproducible)
uv run python scripts/eval_dab.py --driver labrat-agent --agent-provider anthropic --agent-model claude-sonnet-4-6
uv run python scripts/eval_dab.py --driver claude-mcp       # claude --print + LabRat MCP server (Max-plan)
uv run python scripts/eval_dab.py --driver labrat-agent --agent-provider codex  # GPT-5.6 Luna Max default
uv run python scripts/eval_dab.py --driver labrat-agent --agent-provider codex --agent-model gpt-5.6-terra --agent-reasoning high
uv run python scripts/eval_dab.py --driver labrat-agent --agent-provider codex --agent-model gpt-5.6-sol --agent-reasoning ultra
uv run python scripts/eval_dab.py --driver labrat-agent --agent-provider codex --agent-model gpt-5.5 --agent-reasoning medium  # compatibility
uv run python scripts/eval_dab.py --driver labrat-agent --max-turns 10 --max-tool-calls 30   # bound the loop
uv run python scripts/eval_dab.py --driver labrat-agent --agent-verify   # opt-in LLM-as-judge verifier (default off)
uv run python scripts/eval_dab.py --driver claude-mcp --agent-cartograph   # Cartographer pre-pass (+8pp Sonnet, ablated)
```

Before every scoring run, update and prepare the benchmark checkout; ground truth changes can alter historical scores:

```bash
git -C ~/repos/DataAgentBench pull
~/repos/DataAgentBench/download.sh  # when required large files are absent
uv run python scripts/dab_setup.py --dab-dir ~/repos/DataAgentBench
```

MongoDB must be running for `agnews` and `yelp`.

The `labrat-agent` driver builds the `DabTaskEnv`, registers `data_tools` (`profile_dataset`, `list_tables`, `describe_table`, `search_columns`, `link_schema`, `sample_rows`, `column_stats`, `run_sql`, `explain_sql`, `verify_join`, `attach_database`, `load_file`, `load_mongo_collection`, `search_reference_docs`, `workflow`), and routes through `run_agent_task` in-process. Its system prompt (`_build_labrat_agent_system_prompt`) surfaces `profile_dataset`/`link_schema`/`verify_join`/`load_file` and a profile→link→plan→verify-joins→verify-answer discipline. Opt-in `--agent-verify` enables the LLM-as-judge verifier loop on this driver only (default off); `--agent-timeout` overrides the per-trial subprocess timeout (claude-mcp wall-clock; claude-mcp default 1200s); the labrat-agent driver enforces a per-trial wall-clock timeout via `asyncio.wait_for`, recording a timeout as `reason="infra:timeout"` (excluded by `aggregate()` and auto-retried on resume). The `claude-mcp` driver writes a per-trial `mcp-config.json` to the scratch dir and shells `claude --print --strict-mcp-config --mcp-config <file> --allowedTools mcp__labrat --disallowedTools Bash,WebFetch,WebSearch,Task,Read,Write,Edit,NotebookEdit,Glob,Grep --model <agent_model> --permission-mode bypassPermissions`.

**`--agent-cartograph` (off by default):** runs the Cartographer pre-pass (`maze/cartographer.py::cartograph_prepass`) before each trial's agent loop. The Cartographer is a deterministic, GT-firewalled first-contact pass that explores the dataset's databases and writes **Scent** docs (table grain, columns, `verify_join`-confirmed joins, observed dimension values) to the trial's hermetic scratch HOME. The agent then calls `search_reference_docs` (FEATURE_ROADMAP #26a) to retrieve relevant sections during its reasoning. The pre-pass is wired into both `labrat-agent` and `claude-mcp` drivers; it is deterministic-only on the benchmark (structure-only Scent; no LLM semantics pass) and GT-firewalled by construction (reads only DB metadata and sampled rows, never answer-key files). Ablation: **+8pp on Sonnet/claude-mcp** (21%→29% on the tuning subset; deps_dev_v1 0→33%, music_brainz_20k 0→11%; stockindex 56→44% noise). Precedent: Altimate's AutoContext (PR #53) achieved a similar +8pp and was accepted on the leaderboard. **Without `--agent-cartograph`**, a `claude-mcp` trial's Scent tools still resolve against the real (non-hermetic) `~/.labrat/maze/<profile>/` user layer on the machine running the eval — `search_reference_docs` reads the `scent/` docs and `search_trails` reads the `trail/` docs — the same exposure surface as any other MCP tool call; the submission path stays clean because it runs cartograph-hermetic.

**Grounding/context ablation controls:** `--hints/--no-hints`, `--agent-levers/--no-agent-levers`, and `--agent-ledger/--no-agent-ledger` are tri-state at the CLI: an omitted flag inherits the stored value on resume. New runs default to hints **off**, benchmark-safe prompt levers **on**, and ContextLedger **on**. The levers gate force-query, SQL-repair diagnostics, SQL-side aggregation, and tie handling. The ledger persists its artifacts in trial scratch and may summarize large tool results for the model, while the canonical trace still receives the full tool output. Resuming with a conflicting toggle is rejected; use a separate output directory for every ablation arm. Cartographer remains a separate default-off control.

**`--agent-mcp-ledger` (off by default, `claude-mcp` only):** enables the server-side Context Ledger *inside the MCP server subprocess* the `claude-mcp` driver mounts — sets `LABRAT_MCP_LEDGER=1` + `LABRAT_MCP_RESULT_STORE_DIR=<scratch>/results` in the generated `mcp-config.json`'s `env` block, so oversized tool payloads are bounded to a preview + `get_artifact` ref instead of blowing up the CLI's context. `get_artifact` is covered by the existing `--allowedTools mcp__labrat` server-level prefix — no allowlist change needed when the flag is on. This is **distinct from `--agent-ledger`**, which is the in-process `ContextLedger` on the `labrat-agent` driver only (default **on**); `--agent-mcp-ledger` is the `claude-mcp`-side equivalent, added later and defaulting **off** pending an ablation, hence the different flag name rather than reusing `--agent-ledger` (which has no meaning on `claude-mcp` — there is no in-process loop there to attach a ledger to).

**`--informed-shape/-validator/-conventions/-datasets` (all off by default):** the four benchmark-informed prompt rule packs (`informed_packs.py`), form-only rules guarded by a blocking token contamination gate (no pack token may appear in any ground_truth*/validate.py) plus a reverse GT-value audit. Wired into BOTH drivers' prompt builders; the off-path is byte-identical (frozen golden hash). v2.2 (2026-08-03) carries the three delivery-drift fixes from the Opus packs campaign — adjacency non-interposition, stored-structure completeness, first-mention value delivery — and is what PR #84 ran. History: one-pack-per-arm on Sonnet measured an underpowered null; all-four on Opus v1 was a wash; v2.2 on Opus measured +5.9pp over packs-off with zero dataset regressions.

**Per-call traces (`agent_tool_calls.jsonl`):** the `labrat-agent` driver writes one `{tool, input, ok, output, latency_ms}` line per tool call to `<scratch>/agent_tool_calls.jsonl` via the shared `append_tool_trace` writer — schema-identical to `claude-mcp`'s `mcp_tool_calls.jsonl`. The file is created even for a zero-tool attempt and is truncated at the start of every attempt, including an infrastructure retry, so the canonical file describes only the currently selected semantic attempt. Subagent calls are forwarded through the active hook as `subagent:<name>`. The taint audit requires the configured driver's trace, validates its schema, classifies a missing/malformed trace as `audit-error`, and rejects every non-clean verdict. Feature-by-feature driver parity matrix: **`docs/dab-driver-parity.md`**.

**Cross-DB ATTACH:** under `raw-bash` the ATTACH idiom is injected into the prompt (text). Under `labrat-agent`/`claude-mcp` the model uses `attach_database` against the primary DuckDB (Postgres via DuckDB's `postgres` extension; Mongo via `load_mongo_collection` → TEMP table); SQLite/Postgres paths come from `DabTaskEnv.attachable`, Mongo from `MongoSpec`. A second (or later) DuckDB client in a dataset is also routed through `attachable` as `AttachSpec(db_type="duckdb")` — DuckDB's `ATTACH ... (TYPE DUCKDB)` mounts it the same as SQLite/Postgres, so it's no longer silently dropped on either driver. On a successful `attach_database` call, the tool now also introspects the attached database's tables/columns into `ctx.catalogs[alias]` (`DuckDBConnection.introspect_attached_catalog`) and registers `ctx.connections[alias]` against the same primary connection, so `list_tables`/`describe_table`/`column_stats`/`search_columns` and the column-disambiguation hint resolve attached tables too (`describe_table` also strips a leading `"<alias>."` prefix so the dotted form the agent naturally passes resolves). If adding a dataset, check `db_config.yaml` `db_clients` keys and ensure all db types are handled in `env.py` (DuckDB → `ctx.connections` for the first client, `attachable` as `AttachSpec(db_type="duckdb")` for any later one; SQLite/Postgres → `attachable`; Mongo → `MongoSpec`).

**Caps:** `--max-turns` and `--max-tool-calls`, both default `None` (unbounded). Under `labrat-agent` they hard-cap `AgentLoop`. Under `claude-mcp`, `max-turns` maps to `claude --max-turns` (default 200 when `None`); `max-tool-calls` is **advisory only** (surfaced in the prompt). Under `raw-bash`, `max-turns` defaults to 15; `max-tool-calls` ignored.

**Resume safety:** `config.json` records the driver/provider/model/effort, caps and verifier settings, Cartographer/consensus settings, resolved `hints`, `agent_levers`, and `agent_ledger` values, plus `trace_attempt_policy: "reset_on_attempt"`. Resuming via `--output-dir` restores omitted tri-state values; any CLI override that conflicts with the existing config is rejected to prevent mixed-driver or mixed-feature runs from corrupting the aggregate. **`task_filter` is NOT resume-guarded** (so a `--datasets` filter can be added on resume without conflict).

**Per-trial isolation:** `DabSuite.run_trial` wraps the driver dispatch in try/except — a provider/agent exception (e.g. claude-code's per-call `TimeoutError`) is recorded as `reason="infra:timeout"` / `"infra:agent_error"` and skipped by `aggregate()` (and auto-retried on resume), so a single failure can't crash a long run.

**Scoring:** stratified — mean of per-dataset pass rates. Each dataset contributes equally regardless of query count. Per-query rate = `passes / n_trials` (not binary pass@5), so a query with 1/5 passes scores 0.2. The leaderboard's `Pass@1` column IS this stratified mean (`DabSuite.aggregate().overall`), **not** "did any of 5 attempts pass?" — don't reconcile our `report.md` against the leaderboard expecting different metrics.

**Leaderboard standing (current, 2026-08-06):** LabRat holds **four accepted rows**. **#6 — "Claude-Opus-5 + Cartographer" 0.8018** (PR #84, accepted 2026-08-06; Tuned-prompt ✓; board footnote: 2 agnews:4 infra timeouts counted as non-passes, 0.8102 excluding them; first fully taint-clean submission, 268-trace bundle verified in review). #9 — "GPT-5.6-Luna-Max + Cartographer" 74.18% (PR #72; **#2 among untuned entries**). #18 — Sonnet-4.6 + Cartographer 60.88% (PR #65). #23 — Sonnet-4.6 51.38% (PR #54, historical). Never cite the contaminated 58.0% figure. DAB scores are versioned against the **current ground truth** (the board re-scores when GTs/validators change — patents PR #59, tightened crmarenapro:9). Results/history/conclusions: `dab-progress-report.md`.

## Sandbox gate — architectural invariant of the `claude-mcp` driver (must not regress)
The Phase 5 contamination was an unsandboxed agent reading answer-key files / loading HF labels via native Bash. The fix is now load-bearing and permanent (commit `6b4d3bf`): (1) **tool allowlist** — `--allowedTools mcp__labrat` + `--disallowedTools` for every native tool, so the LabRat MCP server is the sole interface (`--permission-mode bypassPermissions` alone is NOT a sandbox — it keeps the full Claude Code toolset live); (2) **filesystem isolation** — `cwd=<absolute scratch dir>`, DAB checkout off-path; (3) **contamination backstop** — `_detect_contamination()` scans each trial's output for answer-key/external-dataset markers and withdraws any hit as `reason="contaminated:<tag>"`, excluded by `aggregate()` alongside `infra:`; (4) **audit traces** — `LABRAT_MCP_LOG_DIR` logs every dispatch. `ANTHROPIC_API_KEY`/`CLAUDECODE`/`CLAUDE_CODE_*` are stripped from the subprocess env so the CLI falls through to Max-plan OAuth. Network egress isolation (container / `unshare -n`) stays an environment step. **Caveat that survives the sandbox:** agnews leaks via model *parametric memory* (Sonnet recalls the public AG News id→label mapping and applies it via SQL) — `_detect_contamination` only catches trials that *name* the dataset, so agnews is intrinsically unreliable for a pretraining-exposed model; same leak hit DAB PR #53 (Altimate, GPT-5.5), so it's model-agnostic.

## GPT-5.5/5.6 via the Codex subscription provider

`--agent-provider codex` uses the private ChatGPT Codex Responses endpoint and `~/.codex/auth.json`; it is subscription-backed, rate-limited, and not the distributable public `openai` provider. New Codex DAB runs default to **GPT-5.6 Luna Max**. Explicit supported combinations are:

| Model | Reasoning values |
|---|---|
| `gpt-5.6-luna` | `low`, `medium`, `high`, `xhigh`, `max` |
| `gpt-5.6-terra` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| `gpt-5.6-sol` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| `gpt-5.5` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh` |

Ultra is supported only for Sol/Terra. It records the requested effort as `ultra`, sends wire effort `max`, and activates proactive multi-agent delegation in the DAB prompt. Luna Ultra and other invalid combinations fail before making a request.

The provider deliberately uses `store: false` and exact full output-item replay, not `previous_response_id`. This is the official stateless Responses conversation pattern; response-ID chaining is state management and does not make prior input free. GPT-5.6 uses the current private Responses Lite request shape. A stable per-task `prompt_cache_key`, a process-local four-second logical-call start interval per model/key (approximately 15 RPM), an explicit-breakpoint probe with targeted fallback, encrypted-reasoning replay, and aggregate plus per-request telemetry are wired. A rejected compatibility probe may retry immediately within that logical call, and independent runner processes do not share the pacing gate. Completed-call usage survives a later timeout/429, but an HTTP failure without terminal usage has no token count.

**Do not claim the cache work eliminates subscription limits.** OpenAI's public prompt-caching documentation says caching does not affect API rate limits, and the private ChatGPT endpoint may have different quota rules. In the bounded Luna Low smoke, two paced trials reached a combined 52.0% cache-read ratio and averaged 42.5% less noncached input than one baseline trial, while adding about 12.4 seconds of pacing wait per trial. That small, capped, stochastic comparison verifies the transport and motivates further validation; it is not a completed cache-effect or DAB-accuracy experiment. Full rationale, the measurement table, exact private/public boundaries, and telemetry caveats: [`codex-caching-investigation.md`](codex-caching-investigation.md). Historical GPT-5.5 conclusions remain in `dab-progress-report.md` §Phase 6.

Self-healing watchdog: `scripts/dab_codex_{tick,loop,finish}.sh` (probe→resume→retry on 429; opt-in `MAX_TOOL_CALLS` cap, resume-safe). Treat `prompt_cache_key` as routing hygiene and validate every new private-endpoint parameter live; `prompt_cache_retention` remains intentionally absent because this endpoint has rejected it.

The trace-complete Luna Max submission command is explicit about every official dataset and winning feature toggle (resume by rerunning the same command/output directory; do not restart it):

```bash
uv run python scripts/eval_dab.py \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-luna --agent-reasoning max \
  --agent-cartograph --hints --agent-levers --agent-ledger \
  --datasets agnews,bookreview,crmarenapro,deps_dev_v1,github_repos,googlelocal,music_brainz_20k,pancancer_atlas,patents,stockindex,stockmarket,yelp \
  --n-trials 5 --output-dir runs/dab/solultra-luna-max
```

(The completed 2026-07 campaign actually executed this as per-dataset shards under
`runs/dab/submission-gpt56-luna-max-ledger-shards/` via `scripts/dab_shards.py`,
then assembled `runs/dab/submission-gpt56-luna-max-ledger-final-270` with
`dab_shards.py recover`; the single-directory command above is the equivalent
unsharded form.)

Launch that arm only with the feature configuration selected by the Luna Max subset ablation; if the winning arm differs, start a fresh output directory with the exact selected toggles. The hard-tail model study must reuse that grounding configuration and compare Luna Max, Terra High, Sol High, and Sol Ultra in separate `runs/dab/tier-<model>-<effort>` directories on the same task filter. Subscription telemetry is appropriate for relative token/latency comparisons; public API list prices are not the cost of subscription-backed calls.

**Provider-agnostic finding (Cartographer):** the Cartographer mechanism is provider-agnostic — GPT-5.5 does consult `search_reference_docs` (confirmed via `agent_tool_calls.jsonl` traces). But the **effect is Sonnet-favoring**: **+8pp on Sonnet, +0pp (neutral) on GPT-5.5** (n=2). GPT-5.5 already self-grounds exhaustively (~32 `run_sql` calls + full schema exploration per trial), making structure-only Scent redundant for it; the leaner-exploring Sonnet benefits. Leaderboard path: Sonnet/claude-mcp + Cartographer + prompt levers.

## Operational notes
- A 270-trial Max-plan run spans multiple session windows — `reason="infra:session_limit"` detection + aggregate-skip + resume auto-retry (commits `404af15`/`9c46c1c`) make it practical; the harness still fast-fails the queue once a limit hits (sub-2s trials with the error as final_text), so resume after reset (open enhancement: sleep-until-reset for unattended completion).
- `claude-mcp` per-trial traces: `--output-format json` gives one bundled result (passed/tool_calls/latency/final_text), but the MCP server logs every dispatch to `<LABRAT_MCP_LOG_DIR>/mcp_tool_calls.jsonl` for audit-grade per-call traces (open enhancement: `--output-format stream-json` for the LLM message stream).
- After a completed official run, package and verify it with `uv run python scripts/build_dab_trace_bundle.py --run-dir <run-dir> --strict-official`. The bundler requires exactly one non-infrastructure semantic attempt per `(task, trial)`, permits retained infrastructure-attempt rows, matches the submission keys to the selected attempts, includes the clean taint verdicts and per-trial traces, and writes a hash/count/attempt manifest under `<run-dir>/trace_bundle/`.
- **`ClaudeCodeProvider` fragility:** the text protocol works on simple queries; harder queries push the model to native `{"type":"tool_use",...}` blocks and the CLI returns `error_max_turns`. Don't use `--agent-provider=claude-code`; use `--driver=claude-mcp`.

## Gotchas
- **Always `--datasets` filter for a leaderboard run.** `DabSuite.tasks()` enumerates all 17 local dirs = **104 queries / 520 trials** (incl. 5 unofficial extras). The official benchmark is **12 datasets / 54 queries / 270 trials**: `--datasets agnews,bookreview,crmarenapro,deps_dev_v1,github_repos,googlelocal,music_brainz_20k,pancancer_atlas,patents,stockindex,stockmarket,yelp`.
- **Default `n_trials` is 5** — `--n-trials 1` for a quick check. Resume: `--output-dir runs/dab/dab-<id>` skips completed `(task_id, trial_num)` pairs.
- **Probe Max-plan before resuming** after an `infra:session_limit` hit: `env -u ANTHROPIC_API_KEY -u CLAUDECODE claude --print --model claude-sonnet-4-6 --max-turns 1 -p "ping"`. Real response → safe to resume; session-limit text → wait. Without the probe, a premature resume blasts ~170 fast-fail infra trials in ~4 min.
- **Dataset directory casing is mixed** — `DEPS_DEV_V1`/`GITHUB_REPOS`/`PANCANCER_ATLAS`/`PATENTS` are uppercase; the rest lowercase. `DabSuite` lowercases all task_ids; **submission JSON entries must be re-cased to match directory names** or DAB's scorer won't find them (see the build script in `runs/dab/dab-1780210698/`).
- **Leaderboard submission files go in `leaderboard_submissions/`** (not `submissions/`, which holds older runs). Naming: `<agent>_<model>_n5.json`. PR commits only the JSON. **Maintainer process (PR #54, PR #65):** they re-validate all answers, then **close the PR and merge the entry on their end** — a closed PR is not a rejection; check the live leaderboard. Current standing: 60.88% / #8 (PR #65, Cartographer entry); prior standing: 51.38% / #13 (PR #54).
- **`claude-mcp` scratch paths must be absolute** — the sandboxed driver runs `claude` with `cwd=scratch_dir`, so a relative `--mcp-config`/`LABRAT_MCP_LOG_DIR` gets re-resolved and doubles (`Invalid MCP configuration: ... not found`). `_run_trial_claude_mcp` calls `scratch_dir.resolve()` up front.
- **MCP server `:memory:` primary must be writable** — `_build_context_from_env` forces `read_only=False` when `db_path == ":memory:"` (DuckDB can't open `:memory:` read-only; the federation primary is the agent's writable workspace). Bit federation datasets (agnews, yelp synthesize a `:memory:` `__federation`); masked in Phase 5 because the agent used Bash, exposed by the sandbox, now fixed + regression-tested. File-backed primaries default `read_only=True`.
- **`labrat-agent` needs `introspect_env_catalogs` post-connect** — `build_dab_task_env` builds `Catalog(schemas=[])`; `_run_trial_labrat_agent` connects then calls `introspect_env_catalogs(env.ctx)` to populate the catalogs before running (else `list_tables`/`describe_table`/`column_stats`/`search_columns`/`profile_dataset` are dead). `claude-mcp` introspects via the MCP server.
- **MCP server connections are JSON env vars** — `LABRAT_MCP_CONNECTIONS` + optional `LABRAT_MCP_PRIMARY`/`LABRAT_MCP_LOG_DIR`. Only `db_type=duckdb` in the spec today; SQLite/Postgres/MySQL/a second DuckDB reached via the `attach_database` tool, which now also introspects the attached DB into `ctx.catalogs` so the catalog tools work against it over MCP too (previously they errored on any attached secondary). To add Postgres/Mongo MCP support, extend `_build_context_from_env` in `src/labrat/mcp/server.py`.
- **music_brainz_20k answer-from-context** — returns in 7-10s with wrong answers (model answers from prompt without querying). A force-query prompt rule is the candidate fix (untried). Distinct from a slow-but-wrong failure like deps_dev_v1:1 (60-170s real queries, still wrong).
- **Self-healing Sonnet re-run loop** (`scripts/dab_rerun_{tick,loop}.sh`) — probes Max-plan, then starts/resumes `eval_dab.py --output-dir runs/dab/dab-rerun-clean` (official-scoped, n=5, 1200s); 30-min poll. Must run locally (Max-plan OAuth + mongod + DAB checkout). mongod must be up (agnews/yelp need it).

## Lever Pack v2 additions (2026-07-18)

- `--agent-taxonomy` (default off, resume-safe): appends the benchmark-agnostic
  "Answer discipline" section to the labrat-agent system prompt (shape/grain
  pinning, literal delivery, verify-before-commit, deterministic-rule bulk
  categorization). Spec: `superpowers/specs/2026-07-18-lever-pack-v2-design.md`.
- `--llm-classify-backend llm|local-embed`: `local-embed` classifies rows with
  the `semantic` extra's local embedder — zero LLM tokens, provider-quota-immune;
  fail-closed self-error when the extra is absent.
- **Hard-tail timeout recipe** (no new config surface): run slow datasets as
  their own shard with a longer wall clock, e.g.
  `uv run python scripts/eval_dab.py --output-dir runs/dab/<run>/agnews --datasets agnews --agent-timeout 2400 ...`
  — shard-level `--agent-timeout` composes with `dab_shards.py merge`'s
  permitted per-shard config deltas.
- Trace bundles now carry per-trial `opening_prompt.txt` (written by the
  labrat-agent driver at dispatch time) and a per-trial `usage` summary in
  `manifest.json` — both additive.
