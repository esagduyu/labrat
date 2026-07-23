# Scent "Operational Rules" Header — Design (Track 2, item 3)

**Status:** Decided 2026-07-18 (autonomous Track-2 planning; decisions in
[`2026-07-18-moat-decisions.md`](2026-07-18-moat-decisions.md) §Part C)
**Author:** Claude Fable (Track-2 planning fork)
**Related:** `src/labrat/maze/cartographer.py` (the deterministic fact generators
this reads), `maze/document.py::Section`, `maze/provenance.py::SOURCE_TIERS`,
`docs/competitive-deepdives-2026-07-16.md` (Altimate digest — the format precedent
and its LLM-authoring failure mode; local-only doc).

## One-sentence pitch

Give every Cartographer-generated Scent doc a short, ranked, **deterministic**
"read this first" section — the highest-salience correctness facts (join
transforms, dirty-data sentinels, trap structures) promoted above the descriptive
schema prose — because ordering and framing of grounding is itself a measured lever.

## Why

Altimate's AutoContext opens with a ranked "Operational Rules" block and it is the
one thing that separated their #44 from their #53 entry; their failure mode
(LLM-authored unconditional rules that broke two PANCANCER queries) is exactly what
our T1c ablation measured net-negative. The adoptable piece is the **format**; the
content must stay deterministic — facts we can verify by probe, not prose a model
believes (OR1).

## Design

### 1. Section shape

A `## Operational Rules` section emitted as the **first** section of each
Cartographer-generated domain doc, `source: "verified"` (every bullet is
probe-verified), standard freshness meta (`generated_at`/`schema_hash`). Bullets are
imperative one-liners with the evidence inline, e.g.:

- `Join orders.customer_id → customers.id via regexp_replace(CAST(customer_id AS VARCHAR), '^[0-9]+[-.]', '') — raw join matches 3.1%, transformed 99.4%.`
- `stays.discharge_note contains the sentinel '[Not Applicable]' (218/5,000 sampled rows) — filter before casting or aggregating.`
- `listing_date is TEXT with ≥2 date formats in sample ('2021-03-04', '04/03/2021') — parse explicitly; naive CAST drops rows silently.`
- `4 tables share one column structure (events_2021…events_2024) — a per-year split; union before whole-history aggregates.`

### 2. Content sources (v1 — all deterministic, mostly already computed)

| Rule category | Source | Status |
|---|---|---|
| Join-normalization transforms (exact SQL + match rates) | `cartographer._candidate_joins` + transform detection (`:195-222`) | exists |
| Shared-structure table groups | the `⚠ N tables share this structure` generator (`:101`) | exists (promote + rephrase) |
| Sentinel strings in stringy columns | new bounded probe: top-K frequent values of `_STRINGY` columns matched against a fixed sentinel list (`[Not Applicable]`, `N/A`, `NULL`, `-`, `Unknown`, …) with count evidence | new, small |
| Mixed-format date-in-text columns | new bounded probe: sample N values of stringy columns whose name/values look date-ish; flag when ≥2 regex date shapes match | new, small |

Explicitly **not** sources (OR1): harvested corrections (stay in human-gated
`## Gotchas`), LLM-authored semantics (T1c), anything not probe-verifiable.

### 3. Ranking + cap (OR2)

Deterministic sort: category weight (`join > sentinel/mixed-format > structure`),
then affected-table count descending, then alphabetical. Hard cap **8 bullets**;
overflow is dropped, never summarized (the cap keeps the header a header).

### 4. Retrieval interaction

None by construction: the section is ordinary Scent content retrieved by the
existing lexical/hybrid scorers — no scorer changes, no new tool, no prompt changes.
Its lift comes from being present, first, and dense with the terms agents actually
query (join/column/format words).

### 5. The gating decision (OR3) — TUI on, DAB off until ablated

`cartograph_prepass(..., operational_rules: bool = False)`:
- TUI first-connect pre-pass (M2 path in `screens/main.py`) passes `True`.
- The DAB driver keeps `False` — on the leaderboard path this section would change
  retrieval content, making it a *lever* that must clear a subset ablation before
  it ships to a submission (then declared, like every other lever).

## Benchmark-safety proof obligation

With `operational_rules=False`, `generate_scent`/`cartograph_prepass` output is
**byte-identical** to today (golden test against a fixture DB); a grep-style test
asserts no `src/labrat/eval/` path passes `operational_rules=True` (the
`active_maps` precedent).

## Test strategy

Unit: each probe against fixture DBs (sentinel table, mixed-date table, transform
join — extend `tests/fixtures/sample_dbs/` builders); ranking determinism + cap;
byte-identity with flag off; flag-on doc has the section first with `source:
"verified"` and populated meta; probes are bounded (row-limit assertions).

## Effort

S: probes (1d), assembly/ranking + flag plumbing (½d), tests/docs (½d).

_Regenerated 2026-07-23 from transcript after accidental deletion._
