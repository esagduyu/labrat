# Hybrid RRF Retrieval — Design (SPEC-ONLY / build deferred)

**Date:** 2026-07-10 · **Status:** design artifact — **build deliberately deferred** (see §0). Two decisions are surfaced for the user: (D-A) whether to add an embedding dependency at all, and (D-B) which embedding source.
**Thesis:** upgrade `search_reference_docs` + `search_trails` from pure-lexical to **hybrid** retrieval — fuse the existing deterministic lexical ranker with a semantic (embedding) ranker via Reciprocal Rank Fusion — so a query phrased differently from the Scent/Trail wording still retrieves the right sections. This is north-star §9's "Hybrid RRF retrieval — ADOPT" borrow.

## 0. Why this is spec-only (the decisive constraints)

1. **Retrieval scoring changes are NOT benchmark-neutral.** Both tools feed the agent's grounding on the DAB `claude-mcp` leaderboard path (Cartographer → `search_reference_docs`). Any change to ranking (BM25, vectors, or RRF fusion) changes *what the agent retrieves*, which can shift DAB scores up or down. **The benchmark track is parked** (post-Fable, user-explicit), so a scoring change cannot be validated now. Shipping it default-on unvalidated risks a silent regression; default-off delivers no value until post-Fable.
2. **The embedding-dependency decision is the user's** (D-A/D-B below): it is product-shaping (package weight / install UX for a terminal tool) and, for an API source, breaks the local-first "nothing leaves the machine" ethos.
3. **The validated insight cuts against heavy investment here:** north-star §8:189 — "the bottleneck was structure," and the Anthropic self-serve-analytics result showed curated reference *docs* (not better retrieval) drove <21%→>95%. Retrieval sophistication is lower-leverage than curation (harvest, Trails, semantic ingest — all shipped/shipping).

Given 1–3, the correct move is to **lock this design** and let the user greenlight the build (and the dependency) when the benchmark track reopens for validation. The Fable window's build energy goes to Q4 (decision-trail harvesting) instead — it extends the validated curation loop, is opt-in/default-off, and needs no dependency.

## 1. What already holds

- `search_reference_docs` (`kind="scent"`) + `search_trails` (`kind="trail"`) share the pattern: `maze/_lexical.py` tokenize/stem, score `2*name_hits + body_hits` (set-intersection), sort `(-score, domain, order)`, top-k, prepend Quick-Reference/When-to-use. Deterministic, offline, **no LLM**, empty store → `[]` (benchmark-safety guarantees).
- **Zero embedding infrastructure:** no numpy, no sentence-transformers, no vector store. `Memory.embedding: list[float] | None` exists but is unused (T2b v2 also wants it — Q5).

## 2. Decisions surfaced for the user

- **D-A — add an embedding dependency at all?** Options: (i) **yes, optional extra** (`pip install labrat[semantic]`; base install + benchmark path unchanged; opt-in for TUI users) — RECOMMENDED if built; (ii) no — keep pure-lexical, close this as "won't do" (retrieval is lower-leverage than curation per §0.3).
- **D-B — embedding source (if D-A=yes):**
  - **Local static embeddings** (e.g. `model2vec` — numpy-only, ~tens of MB, no torch, fast, deterministic, offline) — RECOMMENDED: preserves local-first + offline + determinism; lightweight; benchmark path stays lexical (model not installed on the runner). Modest quality vs transformer embeddings, but real.
  - **Local transformer** (`sentence-transformers`) — best quality, but ~2GB torch dep; heavy for a terminal tool.
  - **API** (Voyage/OpenAI embeddings) — tiny dep, top quality, but breaks offline/no-network + local-first ethos and adds per-query cost — DISFAVORED (contradicts the just-reinforced Cheese "nothing leaves the machine" posture).

## 3. Design (the build, when greenlit)

- **`maze/embedding.py` (new):** an `Embedder` protocol (`embed(texts: list[str]) -> list[list[float]]`) + a null default (returns nothing → pure-lexical). One optional backend behind the `[semantic]` extra implementing D-B's choice. Deterministic; offline; no network on the local paths.
- **Section-embedding cache:** embed each Scent/Trail section body at write time (or lazily on first retrieval), keyed by `sha256(body)`, stored in a sidecar (`.embeddings.jsonl` beside the docs, like `.manifest_fingerprint`) so query time is one query-embed + dot products. Reuses `Memory.embedding`'s vector shape.
- **RRF fusion in the shared retrieval:** compute the lexical ranking (unchanged) and the cosine-similarity ranking; fuse by `score = Σ 1/(k + rank_i)` (standard RRF, `k=60`); the fused order replaces the sort. **Gated behind a flag (`Profile.hybrid_retrieval`, default False)** so the default + benchmark path is byte-identical to today until validated.
- **Graceful fallback:** no embedder / no cache / flag off → exactly today's lexical behavior. Empty store → `[]` preserved.

## 4. Non-negotiables (for the eventual build)

1. **Benchmark path unchanged until validated:** default-off; with the flag off (and on any benchmark run), retrieval is byte-identical to the current lexical tool. Enabling on a submission requires a DAB A/B first.
2. **Offline + deterministic on the local backends:** no network for local static/transformer embeddings; same input → same vectors → same ranking.
3. **Empty/absent store → `[]`** preserved (the benchmark-safety guarantee).
4. **No hard base dependency:** the embedder is an optional extra; base install and `pyright`/tests pass without it.
5. **Reuse:** the RRF fusion wraps the existing `_lexical` ranker; `Memory.embedding` vector shape reused; one embedding path shared by both search tools and (Q5) `cluster_corrections`.

## 5. Testing (for the eventual build)

- RRF fusion unit tests with a stub embedder (deterministic fake vectors): a query semantically-but-not-lexically matching a section ranks it via the semantic arm; flag-off → identical to lexical; empty store → `[]`.
- Cache: section-body-hash keying; stale body → re-embed; sidecar round-trip.
- Fallback: no embedder installed → pure lexical, no error.
- Determinism: same corpus + query → identical fused order across runs.

## 6. Out of scope

- Enabling on the benchmark/default path without a DAB A/B (the whole §0.1 point).
- The API embedding source unless the user explicitly accepts the offline-ethos tradeoff (D-B).
- Re-ranking beyond RRF (cross-encoders, LLM re-rank) — heavier, later.
