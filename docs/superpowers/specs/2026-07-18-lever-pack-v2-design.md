# Lever Pack v2 — answer-discipline taxonomy lever + classification tiering

**Status:** locked before implementation. **Branch:** `feat/lever-pack-v2`.
**Prior art (cited, not copied):** SCRIBE PR #67 spec rules and Alkera's published
`system-prompt-addendum.md` (github.com/AlkeraAI/Alkera-DAB-July) demonstrate that an
answer-shape/grain/delivery discipline prompt is the highest-leverage benchmark-safe
lever on DAB; AgenDA PR #68 shows per-step source contracts; our own failure autopsy
(2026-07-02) pre-identified the same classes as levers B (verbatim emission) and
D (column disambiguation). All wording below is authored fresh for LabRat as
general data-analysis discipline. It intentionally contains no dataset names, no
benchmark references, no validator/output-convention knowledge derived from DAB
specifics — the goal is to remain an **untuned prompt** under DAB's definition.

## Goals
1. **A. `agent_taxonomy` lever** (default OFF): appends an "Answer discipline"
   section to the labrat-agent system prompt, covering shape/grain pinning,
   delivery contract, literal reading, verify-before-commit, interpretation
   enumeration, legitimate not-supported answers, and deterministic-rule bulk
   categorization.
2. **B. Classification tiering**: (i) the deterministic-rule guidance ships inside
   the taxonomy text; (ii) cheap-tier nested classification is already configurable
   (`--llm-classify-model/--llm-classify-reasoning` — verified present, no work);
   (iii) a **local classification backend** `llm_classify_backend: "llm" | "local-embed"`
   that classifies rows with zero LLM tokens.
3. **C. Disclosure upgrades**: per-trial `opening_prompt.txt` written into trial
   scratch by the labrat-agent driver; per-trial usage/cost summary surfaced in the
   trace-bundle manifest.

## Non-goals / decisions taken
- **D1 — no post-hoc answer rewriting.** A difflib nearest-name normalizer that
  edits the agent's final answer post-hoc is rejected: it is answer-content
  transformation performed by the harness, which (a) risks crossing from process
  lever into answer manipulation, and (b) is exactly the kind of output-convention
  fitting the "Tuned prompt" column exists to mark. The taxonomy text instead
  instructs the agent to state entity names verbatim as stored in the data.
- **D2 — local backend = embedding zero-shot on the existing `semantic` extra,**
  not transformers/deberta NLI. Trade-off: deberta-v3 zero-shot is stronger on
  nuanced labels but costs a ~2GB torch dependency chain for an evaluator-path
  feature; model2vec (~30MB, already an optional extra for hybrid-RRF) gives
  deterministic, token-free classification via cosine similarity between row text
  and label prototypes (label + optional description). The backend flag is a seam:
  a heavier `local-nli` value can be added later without interface change.
- **D3 — no new per-task timeout surface.** `--agent-timeout` exists; the
  hard-tail recipe is documentation (run hard datasets in their own shard with a
  longer `--agent-timeout`), not code.
- **D4 — taxonomy is a separate lever, not levers-v1 mutation.** `agent_levers`
  stays byte-identical so prior arms remain comparable; `agent_taxonomy` composes
  with it and is independently ablatable/resume-safe.

## A. Taxonomy text (authored for LabRat)

Appended to `_build_labrat_agent_system_prompt` when `agent_taxonomy=True`, after
the Approach block, as a titled section ("Answer discipline"). Content summary
(exact wording lives in `_taxonomy_lines()` in suite.py; reviewed against the
untuned-prompt bar):

1. **Shape before compute** — decide one-value vs one-row-per-group from the
   question's own wording; "per X"/"for each X" means every qualifying X; a
   superlative inside a per-group question selects within the group, not a single
   global winner; when genuinely ambiguous, return all qualifying rows; after
   computing, compare row count to the number of qualifying groups.
2. **Grain before compute** — prefer the column storing the asked-about thing at
   its native granularity; when both an identifier-code column and a display-name
   column exist and the question is ambiguous, prefer the finer/coded one and
   check whether distinct codes share a name; if a stated qualifier filters out
   zero rows while such values exist elsewhere, suspect the wrong column.
3. **Delivery** — final line carries the complete literal answer; enumerate every
   qualifying item (no truncation or "and N more"); item and its value adjacent as
   plain tokens; numbers at query precision, fractions as decimals unless percent
   is requested; entity names exactly as stored in the data; no preamble.
4. **Literal reading** — strict vs inclusive comparatives; time filters keyed to
   the event the question names; "not X" defaults to strict absence unless the
   question says primary/main; a loose metric gets one stated concrete definition.
5. **Verify before commit** — re-run the exact final query and check stated items/
   counts/values against it; after each significant filter, sanity-check cohort
   size and a sample before building on it.
6. **Interpretations** — when several readings are defensible, note the candidates
   briefly, commit to the most defensible, and state the choice in one clause.
7. **Not supported** — after checking every provided database, "the data does not
   contain this" is a legitimate final answer.
8. **Bulk text categorization** — derive one deterministic rule from sampled data,
   state it, apply it uniformly to all rows; do not sample-and-guess or change the
   rule midway.

## B(iii). `local-embed` backend

- `ToolContext.llm_classify_backend: str = "llm"` (+ suite kwarg, CLI flag
  `--llm-classify-backend`, config persistence, resume-conflict entry,
  `_RECOVERY_COMPAT_KEYS` entry — same plumbing走 as other classify kwargs).
- In `LlmClassifyTool.execute`, backend `"local-embed"` routes to
  `classify_rows_local_embed(...)` (new `tools/local_classify.py`): same SELECT/
  identifier-safety/cap path as the LLM route (reuse the engine's row-selection
  helper — refactor a `select_rows(...)` out of `extract_rows` if needed, without
  changing `extract_rows` semantics), embeds row texts and label prototypes via
  `labrat.maze.embedding.get_default_embedder()`, assigns argmax cosine, writes
  the identical result temp table + ledger artifact shape, consumes the same
  cumulative row budget.
- Absent extra/model → structured self-error (fail-closed, same style as
  `llm_fn is None`). Unit tests use a stub embedder; no downloads.

## C. Disclosure upgrades

- `_run_trial_labrat_agent` writes `opening_prompt.txt` (system prompt + blank
  line + opening user message) into the trial scratch dir at dispatch time —
  before the loop runs, so even failed/terminal trials carry it. taint/bundle
  treat it as an extra scratch file (verify the bundle copies or ignores it
  harmlessly; add it to the bundle's per-trial copied files + manifest).
- Trace-bundle manifest per-trial entries gain `usage` (input/cached/output
  tokens, requests) read from the trial record's meta — additive keys only.

## Preregistered ablation (locked before any arm runs)

Reference baseline: existing ledger arm `runs/dab/ablation-gpt56-luna-max-ledger`
(read-only in the main repo): **39/45 raw, 83.9286% stratified**, per-dataset
deps_dev_v1 3/6, music_brainz_20k 9/9, stockindex 9/9, yelp 18/21.

- **Arm L1** (`runs/dab/ablation-gpt56-luna-max-taxonomy` in this worktree):
  ledger-arm config (cartograph+levers+hints+ledger, gpt-5.6-luna @ max, codex,
  n=3, same 15-task filter) **+ `agent_taxonomy: true`**. Sharded per dataset,
  sequential, ≤3 attempts per shard with 900s backoff, then merge.
- **Arm L2** (only if quota remains): agnews:1 + agnews:2 × 3 trials, taxonomy ON,
  `--llm-classify-reasoning low` as the cheap tier (codex provider exposes only
  gpt-5.6-luna/terra/sol + gpt-5.5; no cheaper model tier — recorded), default
  backend (the local-embed backend ships untested-on-benchmark; L2 measures the
  taxonomy's deterministic-rule guidance + cheap-tier effort, not the local
  backend).
- **Decision rules (preregistered):**
  - Adopt `agent_taxonomy` as default-recommended for GPT configs iff L1
    stratified ≥ 81.13 (baseline − 2.8pp one-task noise band) AND raw ≥ 39.
  - Declare resubmission-worthy iff L1 stratified ≥ 87.0.
  - L2 is directional only (n=6); no promotion decisions from it.
  - All numbers reported with per-dataset rates; infra rows excluded per standard
    aggregation; no post-hoc arm exclusion.

## Test plan (red-first per behavior)
- Lever plumbing: taxonomy off by default; on→prompt contains the section; off→
  byte-identical prompt vs base (golden assertion); resume conflict on toggle
  mismatch; config persisted; dab_shards compat rejects taxonomy-mismatched
  shards.
- local-embed backend: stub-embedder classification lands in result table +
  budget consumed; absent embedder → structured self-error; budget exhaustion
  respected; backend flag plumbed through CLI/config/resume.
- opening_prompt.txt: written per trial (incl. terminal trials); bundle manifest
  carries usage + prompt file with existing checks green.
