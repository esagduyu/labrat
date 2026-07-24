# Fable 5 Usage / Cost Forensics — Session `0cf50364-b3cb-4e63-8d53-1d71ba3512ca`

**Scope:** the single Claude Code session spanning 2026-07-16 12:19 → 2026-07-19 19:11 (3 days), covering the GPT-5.6/DAB audit campaign, code reviews, fixes, feature builds, and competitive intel work. Data sources: the main session transcript (`~/.claude/projects/-Users-ege-repos-labrat/0cf50364-b3cb-4e63-8d53-1d71ba3512ca.jsonl`, 4.5MB / 2213 lines) and 37 subagent transcripts under that session's `subagents/` directory (11MB total). All parsing was done by streaming `jsonl` line-by-line in Python — no full transcript was loaded into context. Other sibling session directories under `/private/tmp/claude-501/-Users-ege-repos-labrat/` (`04a6d194…`, `0fb4f9f5…`, `680356db…`, `74d18798…`, `c17f660c…`, `fa04713f…`) were checked and are empty/inactive (no subagent tasks, no scratchpad content) — negligible contribution to spend.

**Pricing model used:** current Claude API list prices from the `claude-api` skill's cached model table (2026-06-24): Claude Fable 5 $10/$50 per MTok (input/output); Claude Opus 4.8 $5/$25; Claude Sonnet 5 $2/$10 (introductory, through 2026-08-31). Cache write (5-minute ephemeral) = 1.25× the input price; cache read = 0.1× the input price (standard Anthropic cache economics). These are **list-price approximations of the real bill** — actual invoiced amounts may differ with any org-level discounts, but the shape and relative proportions below are accurate.

---

## 1. Totals

Two independent surfaces both burned tokens: the **main/coordinator session** (interactive, driven by you) and the **37 subagent transcripts** it spawned (`Agent` tool calls — 21 `fork`-type, 16 fresh `general-purpose`, all `spawnDepth: 1`; one of the 37, `af237aba82220c064`, is this very audit task and is reported separately below but included in all subagent totals).

| Surface | Requests | Input (noncached) | Output | Cache write | Cache read | Est. cost |
|---|---:|---:|---:|---:|---:|---:|
| Main session | 741 | 1,447 | 981,102 | 9,625,683 | 260,039,418 | **$405.30** |
| All 37 subagents (incl. self-audit) | 1,676 | 30,597 | 757,413 | 13,954,891 | 415,435,072 | **$617.75** |
| **Grand total** | **2,417** | **32,044** | **1,738,515** | **23,580,574** | **675,474,490** | **≈ $1,023** |

**Model split** (by request count, all files):
- `claude-fable-5`: 2,325 requests — 706 main + 1,619 subagent — cost ≈ $381.17 (main) + $172.35+ (subagent cache/output/input) → the overwhelming majority of spend
- `claude-opus-4-8`: 70 requests — 33 main + 37 subagent (`intel:Spacedock` 30 calls, `intel:Altimate` 7 calls) — cost ≈ $24.13 (main) + ~$16.60 (subagent)
- `claude-sonnet-5`: 26 requests, all inside this audit task itself (`af237aba82220c064`) — cost ≈ $1.21

**Billable shape — where the money actually goes:**

| Token kind | Tokens | Weighted $ | Share of total $ |
|---|---:|---:|---:|
| Cache **read** (0.1× input price — cheapest per-token, but 675M of them) | 675,474,490 | ≈ $659 | **64%** |
| Cache **write** (1.25× input price) | 23,580,574 | ≈ $280 | **27%** |
| Output (5× input price, priciest per-token) | 1,738,515 | ≈ $84 | **8%** |
| Input, noncached | 32,044 | ≈ $0.32 | ~0% |

The spend is **not** driven by expensive per-token output — it's driven by the sheer *volume* of cache-read traffic. 675M cache-read tokens is what happens when a session's accumulated context grows into the hundreds of thousands of tokens and gets paid for again (at the cheap 0.1× rate) on every one of 2,417 requests across 3 days and 37 forked subagents. See §4 for the mechanism.

---

## 2. Per-agent table (subagents only, ranked by cost)

Cost column uses the Claude Fable 5 rate for all rows except the two Opus-mixed intel agents (Spacedock, Altimate), which are a slight overestimate there since ~80–25% of their calls ran on the cheaper Opus 4.8 (already reflected correctly in the model-split totals above).

| Agent ID | Purpose | Model(s) | Requests | Output tok | Cache-write tok | Cache-read tok | Duration | Est. $ |
|---|---|---|---:|---:|---:|---:|---:|---:|
| a53e93494327522f7 | track1:lever-pack-campaign | fable-5 | 115 | 47,674 | 8,604,008 | 65,491,598 | **821 min (13.7h)** | $175.43 |
| ad6ee39d5e2a0f2d3 | build:hybrid-RRF | fable-5 | 103 | 41,227 | 142,005 | 48,433,317 | 17.0 min | $52.27 |
| afb8d5215f290ebfa | build:taint-gate-v2 | fable-5 | 83 | 47,357 | 154,359 | 39,365,817 | 16.0 min | $43.66 |
| ac99e2ee2d05a38f8 | intel:SCRIBE | fable-5 | 52 | 24,557 | 72,837 | 26,875,855 | 8.8 min | $29.02 |
| a649bb411fb4782ac | intel:MinusX | fable-5 | 41 | 17,195 | 47,199 | 20,948,626 | 7.4 min | $22.40 |
| acd3afeb44806a4ae | intel:AgenDA | fable-5 | 37 | 18,363 | 36,072 | 18,880,073 | 7.7 min | $20.25 |
| aad8e34630ee32991 | track2:moat-planning | fable-5 | 28 | 29,496 | 78,603 | 17,364,452 | 6.6 min | $19.82 |
| a4708e84fec9e5b7d | intel:Alkera | fable-5 | 36 | 14,363 | 67,144 | 16,679,252 | 5.9 min | $18.24 |
| a9d8f25dbc0957059 | fix:terminal-flag | fable-5 | 100 | 31,996 | 425,663 | 10,156,025 | 10.3 min | $17.08 |
| ab027b305b7ae1d61 | intel:Spacedock | fable-5(3)+opus(30) | 33 | 10,488 | 34,469 | 14,489,808 | 4.6 min | $15.45* |
| a240c1feb2d9297cd | fix:dispatch-429 | fable-5 | 106 | 28,364 | 319,209 | 9,644,160 | 9.1 min | $15.05 |
| aeffc6b11531215fe | intel:Altimate | fable-5(21)+opus(7) | 28 | 5,152 | 37,114 | 13,735,230 | 5.3 min | $14.46* |
| aeb60563cff1dcf8b | audit:ablations/tiers | fable-5 | 75 | 24,484 | 181,510 | 9,677,597 | 9.6 min | $13.17 |
| a7caf2212b29b078f | fix:rate-limit-seams | fable-5 | 83 | 33,436 | 278,720 | 7,648,977 | 8.8 min | $12.81 |
| aef1f4427cb81d70b | fix:suite-verification | fable-5 | 72 | 36,540 | 292,473 | 7,218,023 | 10.0 min | $12.70 |
| a4349becea73ef71f | audit:process/rollout | fable-5 | 56 | 33,137 | 246,541 | 7,810,969 | 8.2 min | $12.67 |
| abd2b644d9b7f9fd3 | research:competitive-refresh | fable-5 | 25 | 10,829 | 69,543 | 10,885,156 | 3.2 min | $12.45 |
| ab36bb3c80f709180 | fix:taint-findings | fable-5 | 74 | 31,274 | 310,045 | 6,264,524 | 11.3 min | $11.71 |
| a225b08bfe50328a2 | audit:dab-suite+rebuild | fable-5 | 56 | 21,337 | 219,964 | 7,732,651 | 7.8 min | $11.55 |
| a16ed6dd533dab068 | review:taint-branch | fable-5 | 17 | 11,540 | 47,397 | 8,365,565 | 3.7 min | $9.54 |
| acef13be8a56d42c2 | audit:regrade+PR65 | fable-5 | 45 | 16,462 | 170,744 | 5,475,776 | 6.9 min | $8.43 |
| ae3b6360c86d10261 | design:customer-evals | fable-5 | 16 | 14,200 | 35,752 | 6,954,327 | 3.1 min | $8.11 |
| a386dc8b08c5a4978 | review:RRF-branch | fable-5 | 15 | 7,178 | 27,322 | 7,323,790 | 2.9 min | $8.02 |
| af6567263a938b12b | audit:codex-provider | fable-5 | 34 | 19,840 | 197,740 | 4,427,426 | 6.0 min | $7.89 |
| a4566b82412d06581 | audit:270-trace-integrity | fable-5 | 52 | 16,575 | 99,592 | 5,756,601 | 6.7 min | $7.83 |
| a6fa972a2fe5e66ef | review:angleB | fable-5 | 35 | 37,906 | 208,932 | 2,362,567 | 8.1 min | $6.87 |
| adc4d1df8eaa24500 | review:angleC | fable-5 | 43 | 22,879 | 230,030 | 2,593,774 | 7.0 min | $6.61 |
| aa335529a75184825 | review:angleA | fable-5 | 26 | 17,874 | 220,655 | 1,631,313 | 6.1 min | $5.28 |
| af62041bb9c8a94da | review:altitude | fable-5 | 31 | 13,547 | 171,244 | 1,847,210 | 4.9 min | $4.67 |
| a93083015b95a0bf7 | fix:shards-compat-helper | fable-5 | 28 | 11,368 | 156,766 | 1,488,347 | 3.3 min | $4.02 |
| a04e44f7f7afff82f | fix:base-shard-compat | fable-5 | 24 | 6,598 | 164,341 | 1,154,248 | 3.0 min | $3.54 |
| ab1c005d2742ab4bf | audit:cache-warm-state | fable-5 | 20 | 6,821 | 30,909 | 2,631,012 | 2.8 min | $3.36 |
| a324686f1cbc74dcb | review:reuse | fable-5 | 22 | 15,026 | 118,824 | 1,080,924 | 3.4 min | $3.32 |
| acdb91c6f24990820 | review:conventions | fable-5 | 16 | 4,649 | 162,827 | 686,519 | 1.5 min | $2.95 |
| a59b7131bfa9a2618 | review:simplification | fable-5 | 18 | 12,316 | 84,582 | 867,844 | 3.4 min | $2.54 |
| a0542eb2e25a9597b | review:efficiency | fable-5 | 12 | 8,495 | 76,137 | 510,590 | 1.7 min | $1.89 |
| af237aba82220c064 | **this audit task** (not a workstream) | sonnet-5 | 19–26† | 6,870 | 133,619 | 975,129 | (ongoing) | $1.21 |

\* Spacedock/Altimate costs shown are computed at the exact model split (fable-5 vs opus-4-8), not a flat rate.
† assistant-message count differs slightly (19 vs 26) between two counting passes; immaterial to the total.

"Duration" is wall-clock (last timestamp − first timestamp in the transcript), not active compute time — see §4 for why track1's 821 minutes matters disproportionately.

---

## 3. Grouped by workstream

| Group | Agents | Requests | Est. $ | Share |
|---|---:|---:|---:|---:|
| **TRACK1-CAMPAIGN** (autonomous DAB tier campaign) | 1 | 115 | $175.43 | **28.1%** |
| **COMPETITIVE-INTEL** (6 deep-dives + refresh + customer-evals design) | 8 | 268 | $140.36 | **22.5%** |
| **FEATURE-BUILDS** (hybrid-RRF + taint-gate-v2 + their branch reviews) | 4 | 218 | $113.50 | **18.2%** |
| **FIXES** (7 targeted TDD fixes) | 7 | 487 | $76.90 | **12.3%** |
| **DAB-AUDIT** (7 audit angles on the GPT-5.6 submission) | 7 | 338 | $64.91 | **10.4%** |
| **CODE-REVIEW** (8-angle whole-branch review) | 8 | 203 | $34.13 | **5.5%** |
| **TRACK2-PLANNING** (moat planning, autonomous) | 1 | 28 | $19.82 | **3.2%** |
| **Subagent total** | 36 | 1,657 | **$625.06** | 100%* |

\*This table's total ($625.06) differs slightly from §1's exact model-split total ($617.75, excl. self-audit) because it approximates Spacedock/Altimate at flat fable-5 rate rather than their true fable-5/opus-4-8 split — a ~$1.20 overestimate, immaterial to the ranking.

**Reading it:** a single overnight autonomous campaign (TRACK1) is larger than any other whole *group* of work — bigger than all 8 code-review angles combined, bigger than all 7 targeted fixes combined. Competitive intel (8 parallel-ish deep-dive forks) is the second-largest group, essentially by headcount — 8 agents each paying to inherit the same large parent context.

---

## 4. Root-cause analysis — why the burn was this high

### 4.1 The dominant mechanism: one long-lived session, repeatedly re-billed for its own accumulated context

The main coordinator session ran **continuously across 3 days** (2026-07-16 → 2026-07-19) without ever being cleared/compacted, accumulating **741 of its own requests** and a growing conversation history that reached **~500,000–620,000 tokens** by day 3. Two consequences follow directly from Claude's prompt-caching mechanics (5-minute ephemeral TTL, prefix-match):

**(a) Cache-read volume compounds with turn count.** Every one of those 741 requests re-sends the entire prior conversation as a cached prefix. Cache reads are the cheapest per-token (0.1× input price), but at ~500K+ tokens per request × hundreds of requests, the *volume* alone produces 260M cache-read tokens in the main session and 415M more across the 37 subagents — 675M total, 64% of the entire session's dollar cost.

**(b) Idle gaps longer than 5 minutes force a full, expensive cache rewrite.** We found **15 events in the main session alone** where `cache_read_input_tokens` drops to 0 and `cache_creation_input_tokens` jumps to the full context size (500K–620K tokens) in a single request — i.e., the 5-minute ephemeral cache had expired since the last turn (typically an overnight gap or a multi-hour gap between work sessions), so the *entire* accumulated history had to be re-written to cache at the pricier 1.25× rate rather than read at 0.1×. Example: at 2026-07-19 07:27 and again at 19:04, a 616K–525K-token context was rewritten from scratch. Each such event costs roughly $6–8 in cache-write alone; 15 of them in the main session plus a matching pattern inside subagents account for a meaningful share of the $280 (27%) spent on cache writes session-wide.

**(c) This same idle-gap pattern is the single largest individual line item.** The `track1:lever-pack-campaign` fork (`a53e93494327522f7`, $175.43, 28% of ALL subagent spend) ran as an overnight autonomous monitor for a background DAB benchmark run. Its transcript shows exactly **13 cache-write events >50K tokens** clustered into two bursts: one short burst around 19:55 on 2026-07-18, then — after an **11.5-hour gap** — a burst of 8 more from 07:28 to 08:20 on 2026-07-19, each ~10 minutes apart, each paying a **full ~650,000-token cache rewrite** because the poll interval (~10 min) exceeds the 5-minute cache TTL. Tool outputs in this transcript were tiny (mean 757 bytes, max 7.3KB) — the cost had nothing to do with big files; it came entirely from re-paying for a large accumulated context on every poll because the poll cadence never fell inside the cache's live window.

### 4.2 Fork fan-out multiplies the same large context

21 of the 37 subagents are `fork`-type — by design they inherit the parent's full conversation history so they can share its prompt cache (`fork` boilerplate literally states this). This is the *intended* efficient design (cache reads are cheap), and it worked as designed: forks launched in the same batch (e.g. the 5 competitive-intel deep-dives launched within 35 seconds of each other on 2026-07-17 01:10, all sharing the identical ~497,314-token baseline) correctly shared one cache write and paid only cheap reads afterward.

The cost driver here isn't a bug — it's that **the parent context had already grown to ~500K tokens by the time these forks were launched**, so *every* fork, no matter how small its own task, pays to inherit and then repeatedly re-read that base. `intel:SCRIBE` (52 requests) had 26.9M cache-read tokens; `intel:MinusX` (41 requests) had 20.9M — both several-times more read volume than their own work would justify, because most of each request's "input" is the inherited parent history, re-read every turn.

### 4.3 Two agents used Opus 4.8 instead of Fable 5

`intel:Spacedock` (30 of 33 calls) and `intel:Altimate` (7 of 28 calls) ran on `claude-opus-4-8` rather than `claude-fable-5`. Opus 4.8 is *cheaper* per token (5×/25× vs Fable 5's 10×/50×), so this wasn't a waste in itself — but it's evidence the model choice for these forked deep-dives wasn't deliberately pinned, and 6 of the 8 sibling intel agents ran the identical task shape entirely on the pricier Fable 5. There's no indication the harder model bought better output on a "read a GitHub PR and summarize" task.

### 4.4 Low-value verdict: the CODE-REVIEW group

8 separate agents (`review:angleA` through `review:conventions`, plus 4 more folded into FEATURE-BUILDS as branch reviews) were spawned to review the same branch from 8 different angles. Individually they're cheap ($1.89–$6.87 each), but in aggregate ($34–43 depending on grouping) this is 12 separate agents independently re-reading the same code and the same ~500K-token parent context. Whether 8 angles were more useful than, say, 3–4 consolidated ones is a judgment call the campaign owner should revisit — the marginal-agent cost here (each new fork ≈ $2–7 minimum just to exist) is cheap per-agent but adds up linearly with agent count.

### 4.5 What was NOT a driver

- **No evidence of pathologically large individual tool outputs.** Sampled tool-result sizes across the biggest agents (track1, SCRIBE, MinusX) were all small (hundreds of bytes to a few KB); the `>50,000-char tool result` counter was 0 for every sampled agent.
- **No runaway tool-call loops.** Tool-call counts per agent are modest (7–67), consistent with focused, bounded tasks — not agents stuck in retry loops.
- **DAB GPT-5.6 API costs are NOT in this accounting.** The DAB benchmark itself runs against the ChatGPT/codex subscription (GPT-5.6), a separate billing account from Anthropic — this report covers only the Claude/Fable-5 orchestration and audit overhead around that campaign, not the benchmark's own model calls.

---

## 5. Recommendations, ranked by expected savings

1. **Stop letting one coordinator session run for days without a reset/compaction (largest lever, likely $200–300+/month).** The single biggest cost driver is a ~600K-token session that never got cleared across a 3-day, multi-workstream campaign. Concretely: use `/clear` (or start a fresh session) at natural workstream boundaries — e.g. when moving from "DAB audit" to "competitive intel" to "feature build" — rather than carrying one unbroken history across unrelated work. Anthropic's own compaction feature (beta, `compact-2026-01-12`) is designed for exactly this and would cap the re-paid prefix size automatically if the harness supports it.

2. **Never run an LLM-driven polling/monitoring loop at an interval close to or above the 5-minute cache TTL (single biggest concrete waste — ~$100–150 in this session alone).** The overnight `track1:lever-pack-campaign` agent polled a background job every ~10 minutes and paid a full ~650K-token cache rewrite each time because the gap exceeded the cache's live window. Fix: (a) poll with a **plain bash/shell watchdog script that makes zero LLM calls** and only invokes Claude once there's a real decision to make (job finished, error, or a genuine judgment call needed) — this is a script problem, not an agent problem; (b) if an LLM must poll, poll *inside* the 5-minute TTL (e.g. every 2–3 minutes) so each check is a cheap cache read, not a rewrite; (c) for any wait longer than the TTL, let the agent's turn end and re-invoke it fresh only when there's something to react to, rather than keeping a stateful loop alive across the gap.

3. **Cap fan-out on "N-angle" review and intel campaigns.** 8 separate fork agents reviewed one branch from 8 angles; 8 more did competitive-intel deep-dives. Each fork's *minimum* cost (inheriting and reading the parent's ~500K-token context once) is $2–8 regardless of how small its actual task is. Before spawning N parallel agents, ask whether 3–4 consolidated agents (or even one agent covering multiple angles sequentially) would find materially the same issues — the marginal fork cost is currently being paid 1.5–2× more often than may be needed. This is a process change, not a code change: default to a smaller N and only add agents when a specific angle needs isolation.

4. **Pin cheaper models explicitly for research/summarization forks.** Intel/audit forks that read a GitHub PR, run `gh` commands, and write a summary do not obviously need Fable 5's frontier reasoning. 6 of 8 intel deep-dives ran on Fable 5 by default; the 2 that used Opus 4.8 (cheaper) show it's a viable substitute for this task shape. Consider defaulting this class of "read + summarize + compare" fork to Sonnet 5 (5× cheaper than Fable 5 on output, similar-or-better for well-scoped extraction tasks) and reserving Fable 5 for the genuinely hard reasoning (the DAB audit trace-integrity work, the hybrid-RRF build). This alone could plausibly cut the $140 competitive-intel line by half or more without losing coverage.

5. **The DAB-AUDIT, FEATURE-BUILDS, and FIXES spend (a combined ~$255, 41% of subagent cost) look largely justified** — these produced concrete deliverables (shipped fixes, a built feature, closed audit findings) via the standard subagent-driven-development workflow this project already uses deliberately (per project memory: "ALWAYS subagent-driven-development for builds"). The waste here isn't *that* subagents were used, it's that every one of them paid the large-inherited-context tax described in §4.1–4.2. Fixing the session-lifecycle issue (recommendation 1) would shrink this bucket's cost without changing the workflow at all.

6. **Track spend live, not after the fact.** Nothing in this session surfaced the ~$1,023 running total until this forensic pass. If there's a way to check per-session or per-day cumulative cost (Console usage dashboard, or a lightweight local counter summing `usage` fields as agents report back), checking it at the start of a new multi-hour campaign would have flagged the cost trajectory well before the monthly limit was hit.

---

## Schema / parsing notes and limitations

- Transcript lines are JSON objects with `type` ∈ `{user, assistant, ...}`; usage lives at `message.usage` on `assistant`-type lines (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, plus a `cache_creation` breakdown by TTL bucket — only `ephemeral_5m` was ever populated in this data, no `ephemeral_1h` entries found).
- `model` is on `message.model`; some lines (echoed tool-result-only turns) have no `usage` and were skipped.
- Duration figures are wall-clock (first-to-last timestamp), not active-compute time — several agents show long wall-clock spans with sparse requests, indicating idle/waiting periods rather than continuous work (most visible in track1's 821-minute span for 115 requests).
- Per-agent dollar figures for the two mixed-model agents (Spacedock, Altimate) in §2 are computed correctly using the true per-model split; the group table in §3 uses a flat fable-5-rate approximation for simplicity, overstating those two rows (and the subagent grand total) by roughly $1.20 total — immaterial to any ranking or conclusion in this report.
- Pricing is current list price from the bundled `claude-api` skill reference (cached 2026-06-24); it does not reflect any organization-specific negotiated rate, and Sonnet 5's introductory discount (through 2026-08-31) was applied where relevant (only the ~$1.21 self-audit line).
- This report's own token usage (the `af237aba82220c064` audit task) is excluded from the workstream analysis in §3 since it is overhead of producing this report, not part of the original campaign — it's listed separately in §2 for completeness (≈$1.21, negligible).

_Regenerated 2026-07-23 from transcript after accidental deletion._
