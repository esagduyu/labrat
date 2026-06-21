# Scent auto-cartographer — GENERATE half (FEATURE_ROADMAP #26b, cycle A)

> **Status:** Design approved 2026-06-21. This is the **generation** half of the Scent layer
> (Pillar 3 / the Rat Maze) — the cold-start on-ramp that writes the curated reference docs
> the #26a `search_reference_docs` tool consumes. The **MAINTAIN** half (drift loop) is a
> separate fast-follow spec (cycle B), explicitly out of scope here.
>
> **Branch:** `feat/scent-cartographer`. **Process:** superpowers — this spec → `writing-plans`
> → TDD → verification → review. Full gate every commit: `ruff format` → `ruff check` → `pyright` → `pytest`.
>
> **Builds on:** #26a (shipped) — the `src/labrat/maze/` package, the `ScentDoc`/`Section` model +
> `parse_document`, the `labrat_maze/scent/` store layout, and the reference-doc template.

## 1. Why

The reference-doc layer assumes a doc already exists; something has to author the first one.
Anthropic's article ("Claude drafts, human owns") and the #1 DAB agent (Altimate AutoContext,
GT-firewalled, new in their leaderboard-topping run) both validate an automated first-draft pass.
For LabRat's ICP — a data scientist pointing at a warehouse they may not own — this is the
cold-start that makes the whole knowledge-layer thesis usable on day one.

The generator pairs a **deterministic structure pass** (mechanically-verified facts) with an
**opt-in single LLM deep pass** (the business semantics a senior analyst would write), keeping
the two visibly distinguished so a human can trust the facts and own the interpretation.

## 2. Scope

**In scope (cycle A — GENERATE):**
- A deterministic structure pass that profiles a database and mechanically verifies joins, writing
  a curated `ScentDoc` skeleton (Quick Reference / Key Tables / Dimensions) at `Source: verified`.
- An opt-in single LLM deep pass that fills the remaining sections (Gotchas / Best Practices /
  business context / metric hints) at `Source: draft`, given the enforced template + the verified
  skeleton + the profile evidence as fixed context.
- A `ScentDoc → markdown` serializer (`render_document`) and a `**Source:**` provenance marker that
  round-trips through `parse_document`.
- A `generate_scent(...)` orchestrator in `src/labrat/maze/cartographer.py` + a `scripts/cartograph.py` CLI.
- Output written into the #26a store (`labrat_maze/scent/<connection>.md`), one doc per connection.

**Explicitly out of scope (cycle B / later):**
- The drift/MAINTAIN loop (schema-hash stamp, kickoff diff, regen-changed-only, re-verify affected
  joins, flag-not-overwrite human/edited sections).
- First-run auto-trigger (this cycle ships an explicit command).
- Per-domain chunking for very large warehouses (cycle A caps + reports; no chunking).
- dbt-semantic-layer ingestion.

## 3. Provenance marker (the seam between cycle A and cycle B)

Under each `## Heading`, a single bold tag line — human-visible, markdown-native, no emoji:

```markdown
## Key Tables
**Source:** verified

- orders — grain: one row per order ...
```

- **`verified`** — produced by the deterministic pass (profiled + join-probed). Trustworthy facts.
- **`draft`** — produced by the LLM pass. Review before trusting.
- **`human`** — authored or reviewed by a person.

Rules:
- The marker is **structured, not cosmetic.** `parse_document` lifts a leading `**Source:** <token>`
  line out of a section's body into `Section.source`; `render_document` re-emits it. So it round-trips,
  stays **out of retrieval scoring** (#26a scores `Section.body`, which no longer contains the marker),
  and is the field cycle-B MAINTAIN keys on (regenerate `verified`/`draft`; flag-never-overwrite `human`).
- **Unmarked sections default to `human`.** Hand-authored #26a docs have no marker → treated as human →
  MAINTAIN will never clobber them. This is the safe default.
- The recognized tokens are exactly `verified` / `draft` / `human`. An unrecognized token also defaults
  to `human` (conservative). The token is the first whitespace-delimited word after `**Source:**`.

## 4. Architecture / components

| File | Responsibility |
|---|---|
| `src/labrat/maze/document.py` (extend) | Add `Section.source: str` (default `"human"`); `render_document(doc: ScentDoc) -> str` (inverse of `parse_document`); teach `parse_document` to lift/round-trip the `**Source:**` marker. |
| `src/labrat/maze/cartographer.py` (new) | The orchestrator: `generate_scent(...)` — deterministic skeleton, join discovery+verification, optional LLM pass, merge, return `list[ScentDoc]`. Plus the write helper. |
| `scripts/cartograph.py` (new) | Thin CLI wrapper (mirrors `scripts/run_task.py`): build connections+catalogs, call `generate_scent`, write docs to `--out`. |

The cartographer **reuses existing deterministic blocks** rather than re-implementing them:
`ProfileDatasetTool` (structure + samples) and `VerifyJoinTool` (join match-rate / fan-out / validity).
It calls their `execute(ctx, input)` directly (they are pure, no LLM). Table prioritization reuses
`context_engine.relevance.score_table_relevance` when query history is available.

## 5. Data flow

`generate_scent(connections, catalogs, *, primary, with_semantics=False, llm_fn=None, table_budget=40, profile_name="default")`:

1. **For each connection** (one `ScentDoc`, `domain = connection key`):
2. **Profile** — call `ProfileDatasetTool.execute` (a `ToolContext` over that connection) →
   per-table columns, types, row counts, declared FKs, sample rows.
3. **Prioritize / budget** — if more than `table_budget` tables, rank by `context_engine` relevance
   (when history exists) else by row count; keep the top `table_budget`; record the omitted count.
4. **Build the verified skeleton** (`Source: verified`):
   - `## Quick Reference` — table count, per-table grain (row counts), and any uniqueness observed.
   - `## Key Tables` — per kept table: columns (name + type), grain, and join keys (next step).
   - `## Dimensions` — for each string/categorical column, a **bounded distinct probe**
     (`SELECT DISTINCT <col> ... LIMIT cap+1`, `cap` default 25). If `≤ cap` distinct values, list
     them as a dimension (surfaces status enums, currency codes, regions, etc.); if `> cap`, skip the
     column (it is not a dimension). This is a deterministic SQL probe — more reliable than the 3-row
     sample for enum discovery — so it is `Source: verified`.
5. **Discover + verify joins** — candidate pairs from (a) declared FKs in the profile and
   (b) a name heuristic: for a column named `<base>_id` on table L, candidate right tables are those
   whose name is `<base>` or `<base>s` (singular/plural), with candidate right columns `<base>_id` or
   `id`. (This covers the fixture's `orders.customer_id = customers.customer_id`.) For each candidate,
   call `VerifyJoinTool.execute`; keep those with `likely_valid` true; write each into `Key Tables`
   as `left.col = right.col (verified: <match_rate>, <fanout note>)`.
6. **Optional LLM deep pass** (only if `with_semantics` and `llm_fn` provided) — ONE call:
   - Input: the #26a template (enforced section list) + the rendered verified skeleton +
     a compact form of the profile evidence.
   - Instruction: *the verified facts are GROUND TRUTH — do not alter them; fill the remaining
     sections (Gotchas, Best Practices, business context, metric hints) as concise, retrieval-oriented
     bullets; if unsure, say so rather than invent.*
   - Output parsed into sections tagged `Source: draft` and merged (LLM sections never replace a
     `verified` section).
7. **Assemble** one `ScentDoc` (frontmatter `kind: scent`, `confidence: draft`) and `render_document`.
8. **Write** to `<out>/<domain>.md` (default `<out> = $LABRAT_MAZE_DIR or cwd /labrat_maze/scent`).

`llm_fn` is an injected `async (prompt: str) -> str` (same idiom as `context_engine` / `verifier`),
so the orchestrator is provider-agnostic and unit-testable with a stub. The CLI builds a real one via
`build_provider`.

## 6. Key design points

- **Deterministic skeleton is always produced; the LLM pass is opt-in and single-shot.** The LLM only
  ever sees the assembled profile + template (no filesystem, no tools) — the GT-firewall is structural,
  not just disciplinary.
- **Verified facts are immutable to the LLM** — enforced template + explicit ground-truth instruction;
  merge logic never lets a draft section overwrite a verified one.
- **Table budget, reported.** Default 40; the doc states how many tables were covered and how many
  omitted — no silent truncation.
- **Born `confidence: draft`.** A human flips the doc-level frontmatter to `verified` after review;
  per-section `**Source:**` is the finer-grained provenance.
- **One doc per connection** (`domain = connection key`) for cycle A. Splitting a connection into
  multiple domain docs is a later refinement.

## 7. Benchmark safety

- Deterministic pass reads schema + sample rows only (via the existing tools); the LLM pass sees only
  the assembled profile blob. Neither touches `validate.py` / `ground_truth.csv`.
- **Usage rule (unchanged from #26a):** never author + commit Scent docs for a held-out benchmark —
  the store stays empty there, so `search_reference_docs` remains a no-op and there is zero leakage.
- A unit test asserts that `with_semantics=False` performs **zero** LLM calls (the stub is never invoked).

## 8. Testing plan (TDD)

- **document (extend):** `render_document` ↔ `parse_document` round-trip including `Source` markers;
  a marker line is lifted into `Section.source` and removed from `Section.body`; unmarked → `human`;
  unrecognized token → `human`.
- **deterministic skeleton:** on the `ecommerce` fixture — table grain/row counts in Quick Reference;
  columns in Key Tables; `orders.status` distinct values in Dimensions via the bounded distinct probe;
  a high-cardinality column (e.g. `email`) is skipped in Dimensions; all `Source: verified`.
- **join discovery + verify:** the `orders.customer_id = customers.customer_id` join is discovered and
  written as verified; a non-join candidate is rejected (not written).
- **LLM pass (stub `llm_fn`):** draft sections are tagged `Source: draft`; the verified sections are
  byte-identical before and after the pass (immutability).
- **end-to-end:** `generate_scent` (stub LLM) writes a doc into a temp `labrat_maze/scent/`; then
  #26a `search_reference_docs` retrieves it for a relevant question (closes generate→consume).
- **benchmark-safety:** `with_semantics=False` makes zero LLM calls (assert stub not called).
- Gate every commit: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.

## 9. Decisions settled during brainstorming

- **Scope:** GENERATE only this cycle; MAINTAIN is a separate fast-follow spec.
- **Engine:** deterministic core (always) + opt-in single LLM deep pass (`--with-semantics`).
- **Provenance:** human-visible `**Source:** verified|draft|human` tag line per section, structured
  (round-trips via `Section.source`), out of retrieval scoring; unmarked → `human`.
- **Run surface:** core in `maze/cartographer.py`, CLI in `scripts/cartograph.py` (mirrors `run_task.py`).
- **Granularity:** one doc per connection (`domain = connection key`).
- **LLM injection:** `llm_fn: async (str) -> str` so the orchestrator is provider-agnostic + stub-testable.
