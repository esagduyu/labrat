# Codex subscription caching investigation

Status: implemented and live-smoke-tested on 2026-07-11. The GPT-5.6 measurements below are transport diagnostics, not a completed DAB accuracy ablation or proof that ChatGPT-subscription limits are gone.

## Outcome

LabRat now follows the stateless Responses pattern deliberately: every request uses `store: false`, captures the complete model output (including encrypted reasoning items), and exactly replays those ordered output items with the next tool result. It also uses a stable per-task `prompt_cache_key`, spaces GPT-5.6 request starts by at least four seconds per model/key, probes an explicit cache breakpoint without disabling implicit caching, falls back safely when the private endpoint rejects that breakpoint, and records per-request cache telemetry.

The important boundary is that **prompt caching is not a rate-limit bypass**. OpenAI's [prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching) says cached prompts still count toward rate limits. The ChatGPT subscription endpoint is private and may meter differently from the public API, so neither a higher `cached_tokens` count nor lower uncached input proves that 429s have been eliminated. Full DAB runs must remain resumable and must preserve usage from completed calls before a 429 or timeout.

## What was actually wrong

The absence of `previous_response_id` was not the defect. OpenAI documents two distinct conversation-state patterns:

- stateful chaining with `previous_response_id`; and
- stateless operation with `store: false`, `include: ["reasoning.encrypted_content"]`, appending all response output items to local history, and resending that history on the next turn.

LabRat uses the second pattern. See the official [conversation state guide](https://developers.openai.com/api/docs/guides/conversation-state) and the [`store` request field](https://developers.openai.com/api/reference/resources/responses/methods/create). `previous_response_id` is a server-side state-management convenience, not a prompt-cache or billing primitive; the same guide notes that prior input tokens in a response chain are still billed as input. Switching to it would also abandon LabRat's explicit no-storage/exact-replay contract without establishing a rate-limit benefit.

The historical pressure came from three separate issues:

1. `AgentLoop` legitimately accumulated a very large multi-turn transcript: system prompt, tool schemas, reasoning/function-call items, and large tool outputs. A historical GPT-5.5 DAB trial was roughly 625K input tokens over about 18 turns. Caching can discount a stable prefix, but it does not make an oversized transcript disappear.
2. The older provider reconstructed translated history on every turn. For a stateless reasoning conversation, the cache-safe continuation is the exact ordered output sequence returned by the server, followed by only the new function outputs or feedback.
3. Cache routing and observability were incomplete. DAB originally allowed a fresh provider UUID to become the cache key, and aggregate usage could not show which request replayed exactly, which fallback fired, or whether a reported zero cache write was measured or merely absent.

The current provider fixes items 2 and 3. ContextLedger and the DAB feature levers address parts of item 1, but they are separate grounding/context ablations rather than evidence that prompt caching is solved.

## Request and replay contract

`CodexSubscriptionProvider` calls the private, unversioned ChatGPT Codex endpoint at `https://chatgpt.com/backend-api/codex/responses` using credentials from `~/.codex/auth.json`. This path is useful for personal subscription-backed evaluation, but it is not LabRat's distributable public OpenAI API path and must not be described as a stable public contract.

Every request has:

- `store: false` and no `previous_response_id`;
- `include: ["reasoning.encrypted_content"]`;
- one stable `prompt_cache_key` for all turns and trials of a DAB task (`task.id`);
- server-local top-level item IDs removed while function `call_id` values are preserved; and
- replay state committed only after a complete successful stream.

Replay has three observable modes:

| `request_mode` | Meaning |
|---|---|
| `initial_full` | First request in a bound conversation; translate the supplied history. |
| `exact_replay` | Replay the previously sent items plus the server's exact output items, then append only messages after the last assistant turn. |
| `reconstructed_full` | Safety fallback when a provider is reused with an unrelated history or its assistant cursor does not match. |

`bind_conversation()` isolates replay state for the main loop, verifier/reverify helpers, consensus rows, and subagents while sharing aggregate usage and feature-capability state. This prevents one helper conversation from corrupting another helper's prefix.

### GPT-5.6 Responses Lite

GPT-5.6 uses the private Codex “Responses Lite” transport currently expected by the ChatGPT backend:

- header `x-openai-internal-codex-responses-lite: true`;
- developer `additional_tools` and system-message items inside `input` on a fresh/reconstructed request;
- `reasoning: {"effort": <wire effort>, "context": "all_turns"}`;
- `parallel_tool_calls: false`; and
- no top-level `instructions` or `tools` fields.

GPT-5.5 retains the older experimental request shape: top-level `instructions` and `tools`, `parallel_tool_calls: true`, and `reasoning.summary: "auto"`. These differences describe the current private implementation only. Public Responses clients should follow the public API documentation instead of copying the Lite header or body shape.

## GPT-5.6 tier and effort matrix

These are the concrete IDs and accepted values enforced by LabRat's current Codex-subscription implementation. Unknown combinations fail before the first network request.

| Tier | Concrete model ID | Accepted `--agent-reasoning` values | DAB default when tier selected | Ultra behavior |
|---|---|---|---|---|
| GPT-5.6 Sol | `gpt-5.6-sol` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` | `max` | Sends wire effort `max` and enables proactive multi-agent delegation. |
| GPT-5.6 Terra | `gpt-5.6-terra` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` | `max` | Sends wire effort `max` and enables proactive multi-agent delegation. |
| GPT-5.6 Luna | `gpt-5.6-luna` | `low`, `medium`, `high`, `xhigh`, `max` | `max` | Unsupported; fails fast. |
| GPT-5.5 compatibility | `gpt-5.5` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh` | `medium` | Unsupported. |

For a new DAB run with `--agent-provider codex`, the default model is `gpt-5.6-luna` and the default effort is `max`. “Ultra” is a LabRat/Codex-subscription composite rather than a new wire effort: only Sol and Terra accept it, the provider records `reasoning_effort=ultra`, the request sends `reasoning.effort=max`, and the DAB system prompt activates proactive `dispatch_subagent` use. Subagent calls remain visible in the canonical trace as `subagent:<name>`.

The public model pages describe the relative tiers—[Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol), [Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra), and [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)—but the exact effort matrix and the Ultra composite above are the current Codex subscription metadata/implementation contract. Do not assume every public API account exposes the same IDs or combinations. Likewise, public API list prices are not ChatGPT-subscription charges and must not be used as the cost of these runs.

Examples:

```bash
# New Codex runs default to GPT-5.6 Luna Max.
uv run python scripts/eval_dab.py \
  --driver labrat-agent --agent-provider codex \
  --tasks stockindex:1 --n-trials 1

# Explicit tier/effort combinations for the hard-tail study.
uv run python scripts/eval_dab.py \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-terra --agent-reasoning high \
  --datasets patents,pancancer_atlas,music_brainz_20k,crmarenapro --n-trials 1

uv run python scripts/eval_dab.py \
  --driver labrat-agent --agent-provider codex \
  --agent-model gpt-5.6-sol --agent-reasoning ultra \
  --datasets patents,pancancer_atlas,music_brainz_20k,crmarenapro --n-trials 1
```

Use separate `--output-dir` values for each arm. Never change model, effort, or feature flags while resuming an existing directory; the runner rejects those conflicts.

## Cache-key, breakpoint, and reasoning fallback

OpenAI's [prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching) defines the behavior that is safe to rely on for the public API: caching is automatic for eligible prompts, hits require exact prefix matches, static content should precede dynamic content, `prompt_cache_key` improves routing, and GPT-5.6 supports explicit cache breakpoints. The guide also recommends keeping a given key at roughly 15 requests per minute; excess traffic can overflow that key onto additional machines and reduce hits.

LabRat applies those ideas conservatively to the private endpoint:

1. DAB passes `task.id` as the key, stable across every turn and repeated trial of the same task. Non-DAB callers get a per-provider UUID unless they provide a key.
2. GPT-5.6 requests pass through a process-local gate keyed by `(model, prompt_cache_key)`. Logical provider-call starts are at least four seconds apart, matching approximately 15 RPM/key. This covers DAB's sequential per-trial providers and bound helper conversations in one process; independent runner processes do not coordinate and can still overrun the same key. A rejected compatibility probe can retry immediately inside one logical call because it never began an accepted inference.
3. On GPT-5.6, the first input-text block is marked with `prompt_cache_breakpoint: {"mode": "explicit"}`. LabRat does **not** set a request-wide explicit-only mode, so default implicit latest-message caching remains available.
4. If and only if an HTTP 400 explicitly names `prompt_cache`, LabRat disables the breakpoint for that model in process, increments `cache_breakpoint_fallbacks`, and retries. The stable key and automatic/implicit cache remain. Unrelated 400s do not disable it.
5. Exact encrypted reasoning items are replayed before their matching function calls. If and only if a 400 explicitly rejects reasoning input items, LabRat strips them, increments `reasoning_passback_fallbacks`, and retries. This is a compatibility escape hatch, not the normal path.
6. `prompt_cache_retention` is intentionally absent because this private endpoint has historically rejected it. No retention duration is promised.

The 2026-07-11 live smoke observed the private endpoint reject the explicit breakpoint once. The provider retried successfully with the breakpoint off, retained the stable key, and remembered that capability result for the next trial in the process. This proves the fallback works; it does not prove an explicit breakpoint is available on the subscription endpoint.

## Telemetry contract and limitations

Each DAB `TrialResult.meta` can contain aggregate `usage` plus a safe-to-persist `request_usage` list. The latter contains no prompt, tool output, authorization data, or encrypted reasoning payload.

| Field | Interpretation |
|---|---|
| `input_tokens`, `output_tokens`, `reasoning_tokens` | Terminal usage reported by completed, incomplete, or failed Responses events. |
| `cached_tokens` | Cache reads reported by the endpoint. The aggregate hit ratio is `sum(cached_tokens) / sum(input_tokens)`. |
| `cache_write_tokens` | Cache writes when the endpoint reports the field. |
| `cache_write_tokens_reported` | Per-request presence bit. `true` with zero means a measured zero; absence in an older artifact means “unknown,” not necessarily zero. |
| `requests` | Requests with terminal usage. |
| `http_attempts` | Network attempts, including a rejected feature probe or a 429/400 with no terminal usage. It can exceed `requests`. |
| `cache_pacing_wait_seconds` / `cache_pacing_wait_ms` | Per-request wait and aggregate trial wait added by the GPT-5.6 per-key pacing gate. |
| `request_mode` | `initial_full`, `exact_replay`, or `reconstructed_full`. |
| `reasoning_effort`, `wire_reasoning_effort` | Records Ultra-to-Max mapping without losing the requested tier. |
| `cache_breakpoint`, fallback counters | Shows what was actually sent and whether a compatibility retry fired. |

The suite captures completed-turn usage in `finally`, so earlier successful calls survive a later timeout or 429. A failed HTTP attempt that never emits terminal usage has no token count, however. Therefore trial totals are a lower bound whenever `http_attempts > requests` for failures other than a known zero-token feature probe.

## Measurements: observed, not causal

The table separates historical evidence from current GPT-5.6 transport smokes. Cache rate is `cached / input`; noncached input is `input - cached`.

| Evidence | Scope | Input | Cached | Cache rate | Noncached input | Output | Wall time | Pacing wait | API-price equivalent* |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Historical GPT-5.5 full pass-1 | 54 semantic trials | 25,940,307 | 7,488,000 | 28.9% | 18,452,307 | 213,172 | 7,979s | n/a | n/a |
| GPT-5.6 Luna Low baseline | `stockindex:1`, n=1 | 42,680 | 8,192 | 19.2% | 34,488 | 783 | 25.37s | none | $0.04001 |
| Paced exact replay, trial 0 | same task/key, n=2 | 40,842 | 26,880 | 65.8% | 13,962 (−59.5%) | 393 | 30.47s | 11.639s | $0.01901 |
| Paced exact replay, trial 1 | same task/key, n=2 | 41,807 | 16,128 | 38.6% | 25,679 (−25.5%) | 312 | 36.94s | 13.115s | $0.02916 |
| Paced exact replay, combined/mean | two trials | 82,649 | 43,008 | 52.0% | 19,820.5 mean (−42.5%) | 352.5 mean | 33.70s mean | 12.377s mean | $0.02409 mean (−39.8%) |

\* “API-price equivalent” applies the public Luna prices on the [GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna) to reported uncached input, cached input, and output. It is an analytical normalization, **not** a claim about ChatGPT-subscription debiting. The paced requests explicitly reported zero cache-write tokens. The older baseline artifact lacks the write-field presence bit, so its equivalent could be understated if the private endpoint performed an unreported write.

Local evidence paths are `runs/dab/full-codex-pass1`, `runs/dab/cache-baseline-gpt56-luna-low`, and `runs/dab/cache-fixed-paced-gpt56-luna-low`. Run directories are operational artifacts rather than committed documentation, so preserve or bundle them before cleanup.

All Luna rows used an eight-turn/twelve-tool-call cap to bound subscription spend and produced the wrong DAB answer. They test request translation, exact replay, pacing, caching telemetry, and fallback behavior—not accuracy. The comparison has one baseline trial and two paced trials and is confounded by model nondeterminism, differing output/reasoning volume, and live cache state. The measured statement is: “the paced pair averaged 42.5% less noncached input than the one-trial baseline, while adding about 12.4 seconds of pacing wait per trial.” It is not yet a general cache-effect or accuracy estimate.

Every paced request after turn 1 used `exact_replay`; reasoning-item passback had zero fallbacks; both canonical traces passed the audit. Trial 0 made nine HTTP attempts for eight completed requests because the explicit breakpoint probe was rejected once. Trial 1 reused the process-local “breakpoint unsupported” result and completed eight attempts for eight requests.

### Uncapped Luna Max heavy-tail validation

| Evidence | Scope | Input | Cached | Cache rate | Noncached input | Output | Wall time | Pacing wait | API-price equivalent |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Uncapped Luna Max baseline | `deps_dev_v1:1`, trial 0 | 1,956,551 | 1,326,592 | 67.8% | 629,959 | 21,463 | 540.18s | 32.039s | n/a |

The first completed semantic row in `runs/dab/ablation-gpt56-luna-max-baseline` made 71 completed requests and 70 tool calls in one conversation. Request 1 was `initial_full`; requests 2–71 were all `exact_replay`, with no reconstruction or reasoning-passback fallback. Cache reads appeared on 54 requests and reached 97.79% on the final 63,612-token request, but intermittent full and partial misses still left about 630K noncached input. The pacing gate added 32 seconds, and the one extra HTTP attempt was the expected rejected-breakpoint retry. This validates exact replay through a genuinely heavy tail without exposing a provider regression, but it does **not** show that subscription quotas are eliminated: the row followed an earlier 429 attempt, and unbounded agent/tool churn still produced substantial uncached input.

A prior GPT-5.5 per-task-key A/B was also null on this subscription endpoint: approximately 36.3% with the old key versus 28.8% with the stable task key on `deps_dev_v1`, with no visible cross-trial reuse. That historical negative result is why the stable key is retained as correct routing hygiene, not advertised as a measured subscription saving.

The next defensible general result is a larger paced Luna comparison with an uncapped accuracy arm, multiple tasks, and enough replications to report distributions rather than one aggregate. Until then:

- exact replay is verified structurally and on live requests;
- breakpoint rejection/fallback is verified live;
- the small paced smoke reduced observed noncached input, but cache reads remained variable; and
- elimination of ChatGPT-subscription exhaustion is **not established**.

### Native-host diagnostic decision rule

Before spending more campaign time on a native Codex host, run one diagnostic-only
Luna-low pair over the same synthetic DuckDB and fixed nine-call tool sequence. Keep
the native path for a separate promotion decision only if both arms are valid and it
reduces aggregate noncached input by at least 30% versus the Responses adapter. Remove
the native detour if the reduction is below 20%; in the 20–30% band, run at most one
additional pair before deciding. This diagnostic does not score DAB and cannot make a
host submission-eligible.

## DAB grounding controls

The three benchmark-facing grounding/context controls use tri-state CLI parsing so omitted flags inherit an existing run's `config.json` value instead of silently changing it on resume.

| Control | New-run default | Off arm | What it changes |
|---|---:|---|---|
| `--hints` / `--no-hints` | off | `--no-hints` | Appends or omits DAB's benchmark-provided data hints. |
| `--agent-levers` / `--no-agent-levers` | on | `--no-agent-levers` | Gates force-query, SQL-repair diagnostics, SQL-side aggregation, and tie-handling prompt rules. |
| `--agent-ledger` / `--no-agent-ledger` | on | `--no-agent-ledger` | Enables ContextLedger summaries/artifacts in trial scratch. The canonical tool trace still receives the full tool output. |

`--agent-cartograph` remains a separate default-off pre-pass. Use separate output directories for baseline, Cartographer, levers, hints, verifier, and ledger arms. A resume conflict is an error, not a request to mutate the existing arm.

## Trace and resume guarantees

For each `labrat-agent` attempt, the suite creates or truncates `<scratch>/agent_tool_calls.jsonl` before the model starts. An infrastructure retry therefore replaces the canonical trace instead of appending calls from multiple attempts. An existing empty file is a valid zero-tool trace.

`config.json` records `trace_attempt_policy: "reset_on_attempt"`. The taint audit requires the trace for the configured driver and validates every non-empty line against the shared `{tool,input,ok,output,latency_ms}` schema. Missing or malformed traces become `audit-error`, and the submission gate rejects every non-clean verdict.

After a completed official 54-query × five-trial run, build the trace-complete artifact with:

```bash
uv run python scripts/build_dab_trace_bundle.py \
  --run-dir runs/dab/solultra-luna-max \
  --strict-official
```

The bundler requires `config.json`, `trials.jsonl`, `submission.json`, `report.md`, and one driver-appropriate trace per selected semantic attempt. It recomputes the taint audit and writes the clean verdicts into the bundle. It permits recorded infrastructure attempts but requires exactly one non-infrastructure semantic attempt for each `(task_id, trial_num)`, verifies submission keys, enforces the exact 12-dataset/54-query/five-trial matrix in strict mode, and writes a manifest with hashes, counts, selected attempt lines, and trace scope.

## Verification commands

```bash
uv run pytest -q \
  tests/unit/test_codex_subscription_provider.py \
  tests/unit/test_eval_dab_runner.py \
  tests/unit/test_dab_suite_run_trial.py \
  tests/unit/test_dab_taint.py \
  tests/unit/test_dab_trace_bundle.py

uv run ruff check \
  src/labrat/agent/providers/codex_subscription.py \
  src/labrat/eval/benchmarks/dab \
  scripts/eval_dab.py scripts/build_dab_trace_bundle.py \
  tests/unit/test_codex_subscription_provider.py \
  tests/unit/test_eval_dab_runner.py \
  tests/unit/test_dab_suite_run_trial.py \
  tests/unit/test_dab_taint.py tests/unit/test_dab_trace_bundle.py
```

Before any scoring run, sync and prepare the DAB checkout as described in [`dab-integration.md`](dab-integration.md). Do not put `~/.codex/auth.json`, bearer tokens, prompts, or encrypted reasoning payloads into run artifacts or trace bundles.
