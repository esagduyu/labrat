# Correction Clustering (T2b v2, second half) — Design (Track 2, item 4)

**Status:** Decided 2026-07-18 (autonomous Track-2 planning; decisions in
[`2026-07-18-moat-decisions.md`](2026-07-18-moat-decisions.md) §Part D)
**Author:** Claude Fable (Track-2 planning fork)
**Related:** `src/labrat/maze/harvest.py` (`cluster_corrections`,
`draft_harvested_sections`), `maze/embedding.py` (the shipped Embedder + cache),
`screens/harvest_controller.py` + `HarvestReviewScreen` (the consuming surface),
[`2026-07-10-hybrid-rrf-retrieval-design.md`](2026-07-10-hybrid-rrf-retrieval-design.md)
(whose deferred "embedding-based clustering" half this completes).

## One-sentence pitch

When five sessions produce five phrasings of the same correction, the harvest
review should show **one** draft Gotcha with "5 similar corrections merged" — not
five near-duplicate drafts — using the already-shipped local embedding layer.

## Why

The compounding loop's bottleneck at volume is review fatigue: scope-only
clustering (`_cluster_by_scope`: table/thread buckets) puts every rephrasing of the
same mistake in front of the human as a separate draft. Semantic sub-clustering
collapses them while keeping the human gate — more knowledge landed per review
minute, which is the moat's actual growth rate.

## Design

### 1. Two-stage clustering (CC1)

Stage 1 unchanged: `cluster_corrections` buckets by scope (domain routing that
HarvestReview and Scent doc targeting depend on). Stage 2 (new,
`maze/harvest.py::_semantic_subclusters`): within each scope bucket, greedy
agglomerative clustering on cosine similarity of `Memory` text embeddings via
`get_default_embedder()` + the existing `SectionEmbeddingCache` machinery (vectors
cached under the profile's maze layer alongside retrieval embeddings, same
`(model_id, sha256(text))` keying).

### 2. Thresholds + merge semantics (CC2)

- **Join threshold θ = 0.80**: pairs ≥ θ to the same sub-cluster (greedy: each item
  joins the first cluster whose centroid similarity ≥ θ, else seeds a new one).
- **Near-duplicate collapse 0.95**: within a sub-cluster, texts ≥ 0.95 similar keep
  only the earliest (the later ones are counted, not rendered).
- One draft `Section` per sub-cluster: heading from the shared scope (today's
  behavior), body = bulleted union of distinct texts, plus a trailing
  `_N similar corrections merged (M near-duplicates collapsed)_` note when N > 1.
  `source: "harvested"` and the contamination audit apply unchanged
  (`draft_harvested_sections` keeps its fail-loud, draft-only contract).
- Constants live in `maze/harvest.py` with boundary-pinning tests; θ is the tuning
  dial if real clusters feel too greedy/shy.

### 3. Determinism (CC3)

Static embeddings are deterministic; iteration order = earliest memory timestamp
then id; centroid = running mean over that order. Same corrections in ⇒ same drafts
out, across runs and machines (review-surface stability, snapshot-testable).

### 4. Fail-open (the hybrid-retrieval contract)

`get_default_embedder()` returning `None` (extra not installed, model absent), an
embed exception, or a cache write failure ⇒ stage 2 skipped wholesale — output
byte-identical to today's scope-only clustering. No new hard dependency; reuses the
`labrat[semantic]` optional extra.

### 5. UX impact (HarvestReviewScreen)

No new screen: drafts arrive exactly as today, one per sub-cluster instead of one
per scope-bucket-of-everything; the merged-note line is the only visible change.
Accept/reject/edit semantics untouched; rejecting a merged draft rejects the
cluster's memories as a unit (today's per-draft behavior, now over a tighter draft).

## Benchmark-safety proof obligation

Harvest never runs on benchmark paths: `SessionHarvester.enabled` defaults False,
no eval/DAB path constructs a harvester, and this change is confined to
`maze/harvest.py` + tests (no retrieval, no registry, no prompts). Existing
grep/registry-count tests continue to prove the surface; a new test asserts
stage 2 is skipped (byte-identical drafts) when the embedder is unavailable.

## Test strategy

Stub-embedder unit tests: θ boundary (0.79 splits / 0.80 joins), near-dup collapse
at 0.95, merged-note rendering, determinism under shuffled input, fail-open
byte-identity, cache reuse (embed called once per distinct text), audit still
fail-louds on a poisoned draft. One integration test through
`draft_harvested_sections` with a mixed 6-correction fixture (2 clusters + 1 loner).

## Effort

M: sub-clustering + merge semantics (1–1½d), UX note + controller pass-through (½d),
tests (1d). Sequenced last (SEQ3) — value scales with correction volume.

_Regenerated 2026-07-23 from transcript after accidental deletion._
