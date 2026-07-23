# Terra vs Luna DAB Diff — Group A (crmarenapro, deps_dev_v1, github_repos, stockindex, patents, music_brainz_20k)

**Method:** script-assembled pass/fail tallies from `trials.jsonl` on both sides for all 28 queries (140 trials) in this group, then read `artifact.payload` for every trial on every query that differed or where both sides failed, cross-referenced against `validate.py`/`ground_truth.csv`, and opened `agent_tool_calls.jsonl` traces where the answer text alone didn't explain the gap.

**Confound reminder:** TERRA = gpt-5.6-terra@high + taxonomy lever ON. LUNA = gpt-5.6-luna-max, taxonomy OFF (our submitted #5 entry, 74.18%). Different model **and** different lever changed together — attribution to "the taxonomy" is only claimed where trace/payload evidence specifically implicates the taxonomy's instructions, never by default.

**Group totals (excl. infra):** Terra 108/140 (77.1%) vs Luna 107/140 (76.4%) — **statistically a wash**. The two configs reach nearly the same aggregate by very different paths: several large systematic swings mostly cancel out.

**Harness data-quality note (affects any re-scoring of the Terra run):** Terra's `music_brainz_20k/trials.jsonl` has 6 rows for `music_brainz_20k:3` — `trial_num=3` appears twice (one `infra:timeout`, one later `passed=True` from a resume). The Terra writer *appends* a resumed trial instead of overwriting the infra row for the same `trial_num`, so any scorer must dedupe by `(task_id, trial_num)` keeping the non-infra row or it will inflate that query's denominator. All tallies here already dedupe this.

---

## crmarenapro (13 queries, 65 trials/side)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| 1 | 5/5 | 5/5 | — |
| 2 | 2/5 | 1/5 | free-text-grounding (shared) |
| 3 | 4/5 | 5/5 | noise/variance |
| 4 | 5/5 | 5/5 | — |
| 5 | 5/5 | 5/5 | — |
| 6 | 5/5 | 5/5 | — |
| 7 | 3/5 | 4/5 | free-text-grounding (shared) |
| 8 | **0/5** | **3/5** | wrong-computation / delivery-format (tie-band over-enumeration) |
| 9 | 5/5 | 5/5 | — |
| 10 | 5/5 | 5/5 | — |
| 11 | 5/5 | 5/5 | — |
| 12 | **3/5** | **0/5** | wrong-computation (ambiguity resolution) |
| 13 | 5/5 | 5/5 | — |

**crmarenapro:2** ("which knowledge article does this quote violate") — both sides converge on the *same wrong ID* `ka0Wt000000Ens5IAC` in most failing trials (Terra 3/5 fail, Luna 4/5 fail), vs expected `ka0Wt000000Eq0MIAS`. Terra trial4 payload: `'ka0Wt000000Ens5IAC\nka0Wt000000Eq0MIAS'` — actually contains the correct ID too but validator's `expected in llm_output_clean` substring check still passed it (lucky double-emission). This is a judgment task (read multiple candidate knowledge articles, pick the one that applies) — both models systematically favor the same plausible-but-wrong article. Not a Terra/Luna tool difference; a shared retrieval/reading weakness.

**crmarenapro:7** — same pattern: both sides land on the identical wrong ID `ka0Wt000000EpSUIA0` on their respective trial2, and Terra additionally has one trial (4) that gives up entirely (`'None'`, "no policy violation"). Same free-text-grounding judgment weakness as query 2.

**crmarenapro:8** ("agent with fewest transfer counts... return only the Id") — **Terra 0/5, catastrophic and systematic**: all 5 Terra trials return ~30 agent IDs (the *entire* qualifying roster), e.g. trial0's full payload is a 31-line dump of IDs. The trace (`crmarenapro_8__trial0/agent_tool_calls.jsonl`) shows why: `{'step': 'query', 'note': 'Final query found 32 qualifying agents; 31 tied at the minimum of zero outgoing transfers.'}` followed by `{'step': 'verify_answer', 'note': 'Verified... all 31 tied IDs are returned rather than arbitrarily truncating the tie.'}`. Terra's agent applied the tie-inclusion lever ("if the Nth value can repeat, rank with ties... rather than LIMIT alone") to a **31-of-32-way tie**, treating a near-universal zero as a legitimate tie band instead of a red flag that it mis-modeled "transfer count." The question itself says "identify **the** agent" (singular) — the taxonomy's own shape-pinning rule ("a superlative... picks the best... not one global winner" / "if genuinely ambiguous, return all qualifying rows") was over-applied here: a 31/32 tie is not "genuinely ambiguous," it's a signal the query is wrong. Luna (no taxonomy, no strong tie-lever pull) answers with a single ID 3/5 times (correct `005Wt000003NIliIAG`) and a single different wrong ID (`005Wt000003NGjuIAG`) the other 2/5 — Luna never dumps the full roster.

**crmarenapro:12** (opposite direction: Terra 3/5, Luna **0/5**) — mirror image of query 2/7's shared-wrong-answer pattern, but here Terra breaks the tie correctly more often. Both sides' failures converge on the identical wrong ID `005Wt000003NJgAIAW`. Terra's one passing-trial payload states its reasoning explicitly: *"I'll treat 'in April 2023' as the policy-defined closing event—the contract's CompanySignedDate—and compare each opportunity owner's average days..."* — this is the taxonomy's ambiguity-commitment line ("when several readings are defensible... commit to the most defensible one... state your choice in one short clause") working as designed and landing on the right interpretation, while Luna's model consistently picks a different (wrong) definition of "closing event" in all 5 trials.

**Failure-mode tally (crmarenapro):** free-text-grounding (shared, both sides) ×2 queries; wrong-computation/tie-overreach (Terra-specific) ×1; wrong-computation/ambiguity (Luna-specific) ×1; noise ×1.

---

## deps_dev_v1 (2 queries, 10 trials/side)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| 1 | 3/5 | **0/5** | delivery-format (validator adjacency window) + enumeration-gap |
| 2 | 5/5 | 5/5 | — |

**deps_dev_v1:1** ("top-5 packages by GitHub stars, name-version pairs") — this is the cleanest, most mechanically-proven finding in the whole group. `validate.py` checks `version in llm_lower[idx+len(name) : idx+len(name)+10]` — the version string must appear in the **10 raw characters immediately following the name**. Terra's taxonomy line *"keep each item and its value adjacent as plain tokens"* produces compact answers like `` @dylanvann/svelte — 3.25.4 — 73499 `` (target 5 chars after name → well within window) → **passes 3/5**. Luna's default markdown-table style produces `` | \`@dylanvann/svelte\` | \`3.25.4\` | `` — after the name comes `` ` | ` `` (5 chars) before `3.25.4` even starts, so only `3.25.` (5 of the 6 needed chars) fits in the 10-char window → **fails all 5/5**, every single trial with the identical reason `"Version '3.25.4' not found after name '@dylanvann/svelte'"`. This is a textbook delivery-format regression driven precisely by markdown-table/backtick formatting vs bare-token formatting, and directly attributable to the taxonomy's adjacency instruction. Terra's own 2 failures are a *different* mode: `enumeration-gap` on a 95–99-way star-count tie (trial3 drops `@dylanvann/svelte` entirely, trial4 computes a completely different top-5 without the tie cluster at all) — inconsistent tie-band handling on the *legitimate* large-tie case (contrast with crmarenapro:8's *illegitimate* tie).

---

## github_repos (4 queries, 20 trials/side)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| 1 | **0/5** | **0/5** | BOTH FAIL — likely dedup/grain issue, not a lever gap |
| 2 | **0/5** | 1/5 | wrong-computation / column-disambiguation (repo-language join) |
| 3 | 5/5 | 5/5 | — |
| 4 | 5/5 | 5/5 | — |

**github_repos:1** ("proportion of non-Python repos' README.md files with copyright") — **both sides fail every single trial**, and by a huge margin: GT is `total_readmes=3, copyright_readmes=1, proportion=0.333`, but every Terra/Luna trial computes a proportion around **0.12–0.15 over 101–138 README rows** — roughly 35–46× too many READMEs. Terra's trace shows a careful, `verify_join`-checked pipeline (joins `contents.sample_repo_name` → `languages.repo_name`, confirms "join cannot fan out... right-side key unique") that still lands on 105–138 rows. The consistent inflation on both sides, using the correctly-named `contents`/`sample_*` tables, strongly suggests the true GT-generating query dedupes at a level neither agent checks: `verify_join` validates *join* cardinality (foreign-key fan-out) but never checks whether the **source table itself has one row per logical README file** — if `contents` stores multiple sampled chunks/duplicate rows per actual file, counting raw rows inflates the denominator ~40×. Not a taxonomy/model difference — genuinely hard, and not obviously fixable by either lever pack; flagging for follow-up data-quality investigation rather than attributing to either config.

**github_repos:2** ("repo in Swift language with the most-copied non-binary Swift file") — Terra 0/5, Luna 1/5. GT is `SwiftAndroid/swift` (max copy count 23), but Terra/Luna mostly answer `uacaps/PageMenu` (38 copies) — a higher raw copy count, but from a repo that isn't actually a Swift-language repo. The one Luna pass (trial2) explicitly narrates the missing step: *"filtering for non-binary `.swift` files, and matching repositories whose language metadata includes Swift..."* — the fix is joining files→repo-language metadata and filtering by the repo's *declared* primary language before taking the max, not just filtering by file extension. Both sides mostly skip that join. Tag: **column-disambiguation** (file extension vs. repo-level language-metadata column) — same category as the autopsy's lever D.

---

## stockindex (3 queries, 15 trials/side)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| 1 | 5/5 | 4/5 | delivery-format (boundary artifact) |
| 2 | 4/5 | 5/5 | wrong-computation (single-trial) |
| 3 | 4/5 | 5/5 | noise/variance |

All three deltas are exactly ±1/5 — **this dataset is dominated by noise/variance**, but one delta is mechanically explained:

**stockindex:1** — validator requires the target ticker within the **first 200 characters**. Luna's one failure (trial4) has the target at character index **193**, but the target string is 9 chars long (193+9=202 > 200) — it's cut off by **2 characters**, a pure boundary artifact of preamble length, not a real error. Checked all idx offsets: Terra's passing trials are bare-token-first (idx 0 in 4/5, idx 115 in 1/5); Luna's answers routinely lead with a ~100–190 char framing sentence before the ticker. This is the *general* pattern behind the delivery-format tag — Terra's terser default (again plausibly taxonomy-driven) has more margin against this specific 200-char heuristic, but at n=5 with a 2-char margin this one flip reads as noise, not a systematic win.

**stockindex:2** Terra trial3 answers `NYA` instead of `IXIC` outright (target string absent, idx=-1) — a genuine wrong-computation, isolated to one trial, no pattern across the other 4.

**stockindex:3** Terra trial4 drops `NSEI` from its ranking (uses `N225` instead) under the "regular monthly investment" ambiguity — an isolated interpretation flip; other Terra trials use the NSEI reading.

---

## patents (3 queries, 15 trials/side)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| 1 | 3/5 | 5/5 | free-text-grounding (date parsing) |
| 2 | 3/5 | 3/5 | enumeration-gap (shared) |
| 3 | 5/5 | 2/5 | column-disambiguation (CPC hierarchy level) |

**patents:1** ("level-5 CPC codes whose best EMA year is 2022") — validator requires **all 71** listed GT codes present as substrings. Terra's 2 failing trials both drop exactly `A22B` (alphabetically first in the GT list) while also including several codes *not* in GT — i.e., each failing trial computed a materially different code set, not just a truncation. The trace (`crmarenapro`-style deterministic recursive-CTE EMA query, confirmed via `agent_tool_calls.jsonl`) shows the underlying computation *is* SQL/deterministic, but depends on messy free-text `filing_date` parsing (`regexp_extract` on inconsistent date strings) — small parsing differences shift which codes' "best year" tie-breaks land on 2022 vs an adjacent year. Tag: free-text-grounding (date-string parsing robustness), not a taxonomy/model issue — Luna happened to get a clean parse in all 5 trials.

**patents:2** (CPC groups tied at peak EMA, fuzzy-matched against `BAKING; EDIBLE DOUGHS`) — same rate both sides (Terra 3/5, Luna 3/5, different failing trials). Every failure has the reason `"Name fuzzy match failed for 'BAKING; EDIBLE DOUGHS'"` — the `A21` group (also alphabetically/numerically first in the results) is the one that gets dropped from the large (~20-group) EMA-tie enumeration in roughly 40% of trials on both sides. This is the same enumeration-gap-on-large-ties pattern as `deps_dev_v1:1`'s Terra failures — a shared weakness independent of the taxonomy lever, present on both configs equally.

**patents:3** ("citing assignees + CPC title, exact `UNIV CALIFORNIA` match") — Terra 5/5, Luna 2/5, a real and systematic split. `validate.py`/GT expects the CPC **subclass**-level title (4-char code, e.g. `H01M` → "PROCESSES OR MEANS... BATTERIES..."). Terra's traces/answers consistently truncate to the 4-char subclass and join `cpc_definition` at that level in all 5 trials. Luna's 3 failing trials instead report the full CPC **subgroup** symbol (`H01M4/9066`) with its narrower subgroup-level title ("Metals or alloys specially used in fuel cell operating..."), which doesn't fuzzy-match the GT subclass title (`"No match for: BLOOM ENERGY CORP + PROCESSES OR MEANS..."`). Tag: **column-disambiguation** — which level of `cpc_definition.symbol`/`level` to join against. Terra gets this right 5/5; Luna gets it right only when it separately decides to truncate to subclass (2/5).

---

## music_brainz_20k (3 queries, 16 trials Terra / 15 Luna)

| Query | Terra | Luna | Tag |
|---|---|---|---|
| 1 | 5/5 | 4/5 | entity-resolution |
| 2 | 5/5 | 5/5 | — |
| 3 | 4/5 (+1 infra retry, excluded) | 5/5 | entity-resolution |

**music_brainz_20k:1** (revenue of a song with 5 duplicate title/artist track records) — Luna trial3 fails: *"After resolving **four** duplicate records for Beyoncé's 'Get Me Bodied,' the two Canada/Apple Music sales totaled $377.62 + $223.82... $601.44"* vs correct $1,059.46 (needs all 5 duplicates / 3 matching sales rows). A clean, strict-numeric-check failure: the agent's dedup step missed one of the five duplicate track records. Classic entity-resolution gap (autopsy lever C).

**music_brainz_20k:3** (highest-revenue song, dedup across duplicate track records) — Terra trial2 (of 5) resolves to a **different top song entirely** ("Emerge (Dexter remix)" instead of "Zo gaat het leven aan je voor," fuzzy score 0.32) — same entity-resolution failure class, this time on Terra's side. Note: this query's validator (`validate.py`) only fuzzy-matches the **song name string**, not the dollar figure — Luna trial1 also under-resolves duplicates (misses a 5th record, computes $7,536.31 instead of $9,013.69) but still **passes** because the song name stays correct; this is a validator leniency, not evidence the agent got it right — flagged here so it isn't mistaken for a genuine pass. (Terra also has one extra infra-timeout-then-retry row on trial3, a rerun artifact, excluded from the tally as instructed.)

Both sides show the identical underlying weakness (inconsistent duplicate-record resolution, sometimes 4/5 or missing the right cluster entirely) hitting different specific trials — genuinely systematic across the dataset, not attributable to either config specifically.

---

## Failure-mode tally (Group A, 28 queries)

| Tag | Queries | Systematic? |
|---|---|---|
| entity-resolution | music_brainz_20k:1, :3 | Yes — shared, both sides |
| free-text-grounding | crmarenapro:2, :7 (judgment/retrieval); patents:1 (date parsing) | Yes — shared, both sides |
| enumeration-gap (large legitimate ties) | deps_dev_v1:1 (Terra), patents:2 (both) | Yes — shared weakness on big tie clusters |
| delivery-format (validator adjacency/char-window) | deps_dev_v1:1 (Luna 0/5!), stockindex:1 (boundary) | Yes for deps_dev_v1; noise-level for stockindex |
| column-disambiguation | github_repos:2, patents:3 | Yes — CPC hierarchy level & language-metadata join, both real |
| wrong-computation (tie-band overreach) | crmarenapro:8 (Terra 0/5) | Yes — one query, but total (0/5) and mechanically explained |
| wrong-computation (ambiguity resolution) | crmarenapro:12 (Luna 0/5) | Yes — mirror case, taxonomy's ambiguity-commit line plausibly responsible for Terra's edge |
| both-fail / data-quality quirk | github_repos:1 | Unclear — likely a source-table dedup gap, not a lever gap |
| noise/variance | crmarenapro:3, stockindex:2, :3 | Confirmed noise — single-trial flips, no shared cause |

---

## Prioritized fixes (existing LabRat tools/knobs)

1. **Validator-adjacency-window formatting (`deps_dev_v1:1`, highest-confidence finding).** The taxonomy line *"keep each item and its value adjacent as plain tokens"* (`_taxonomy_lines()` in `suite.py`) should be promoted from the taxonomy-only addendum into the **base `_dab_lever_lines()`** (which Luna's config already has enabled) — Luna's markdown-table default cost it all 5/5 trials on this query purely from `` ` | ` `` characters between name and value. Rescues: `deps_dev_v1:1` outright (and plausibly the marginal `stockindex:1` boundary case too).

2. **Tie-band sanity check before enumerating (`crmarenapro:8`, 0/5 total, mechanically traced).** The tie-inclusion lever ("if the Nth value can repeat, rank with ties... rather than LIMIT alone") needs a guard: if the tie band covers a large fraction of the qualifying set (e.g. >50%, here 31/32), that's a signal the underlying computation is wrong, not a real tie — the agent should re-derive/sanity-check rather than dump the full set. Also reinforce the taxonomy's own shape-pinning rule to give priority to singular question phrasing ("identify **the** agent") over the tie-lever. This is a `_dab_lever_lines()` / taxonomy interaction bug, not a Luna-vs-Terra issue — it would recur on either config given the same lever combination.

3. **`verify_join` doesn't catch source-table row inflation (`github_repos:1`, both sides 0/5).** `verify_join` currently validates join-key fan-out only; it has no check for whether the table being counted (e.g. `contents`) has one row per logical entity before aggregation. Both sides ran `verify_join`-style checks and still got a ~40× inflated denominator. Worth a `verify_join`/`profile_dataset` extension that flags "N distinct rows vs M distinct logical keys" on the *base* table, not just the join.

4. **Column/level disambiguation on hierarchical code tables (`patents:3`, `github_repos:2`).** Two different instances of the same root cause: picking the wrong granularity/level of a lookup table (`cpc_definition.level`/`symbol` truncation; repo-level `languages` metadata vs per-file extension). This is the autopsy's lever D (`column-disambiguation`) still unaddressed — `link_schema`/`describe_table` grounding could surface "this table has a level/hierarchy column — confirm which level the question needs" for tables with an explicit level/hierarchy column.

5. **Entity-resolution for duplicate-record dedup (`music_brainz_20k:1`, `:3`).** Confirms the autopsy's lever C is still open — no fuzzy/duplicate-record-resolution primitive exists; the agent's ad hoc dedup (via SQL grouping or reasoning) misses one of N known-duplicate records roughly 20% of the time on both configs.

**Noise vs. signal, honestly:** `crmarenapro:3`, `stockindex:2`, `stockindex:3` are pure single-trial flips with no shared mechanism — don't act on them. `crmarenapro:8` and `deps_dev_v1:1` are the opposite: total (0/5 or 5/5-vs-0/5) and mechanically traced to a specific lever/validator interaction — high-confidence, not noise, even though the run is model-confounded.

---

_Regenerated 2026-07-23 from the agent transcript after an accidental deletion of the original file._
