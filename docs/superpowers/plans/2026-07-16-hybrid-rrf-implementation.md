# Hybrid RRF Retrieval — Implementation Plan (T2b v2)

**Date:** 2026-07-16 · **Spec:** `docs/superpowers/specs/2026-07-10-hybrid-rrf-retrieval-design.md` (locked 2026-07-10) · **Branch:** `feat/hybrid-rrf-retrieval` (worktree, base master `a3d2878`)

**Resolved decisions (user, 2026-07-16):** D-A = **yes, optional extra** (`labrat[semantic]`); D-B = **local static embeddings** (`model2vec`). Both are the spec's recommendations. The spec's §0.1 deferral ground no longer holds: the GPT-5.6 subset-ablation harness gives us a validation path, and the feature ships **default-off** regardless.

## Contract (from spec §3–§5, non-negotiables §4)

1. **Default-off / additive:** flag off (`ToolContext.hybrid_retrieval = False`, never set by any eval/MCP path) ⇒ retrieval output **byte-identical** to today. Proven by test.
2. **Fail-open:** flag on but embedder unavailable (extra not installed, model not fetchable, corrupt cache) ⇒ exactly today's lexical behavior, no error.
3. **Empty/absent store ⇒ `[]`** preserved in all modes.
4. **Offline + deterministic:** model2vec inference is pure numpy, no network at retrieval; same corpus + query ⇒ identical fused order. (One-time model fetch is provisioning, not retrieval; offline + no model ⇒ fail-open lexical.)
5. **No hard base dep:** `model2vec` lives behind `[project.optional-dependencies] semantic`; base install, pyright, and the full test suite pass without it.
6. **Reuse:** RRF wraps the existing `_lexical` ranking; one shared hybrid path serves both `search_reference_docs` and `search_trails`.

## Tasks

**T1 — `maze/embedding.py`: Embedder protocol + model2vec backend + section cache.**
`Embedder` protocol (`model_id: str`, `embed(texts) -> list[list[float]]`); `get_default_embedder() -> Embedder | None` (import model2vec lazily; model = `LABRAT_EMBED_MODEL` env override or the pinned default id; any failure → `None`); `SectionEmbeddingCache` over a `.embeddings.jsonl` sidecar (keys: `model_id` + `sha256(body)`; corrupt/missing lines ignored; writes best-effort — `OSError` ⇒ in-memory only). Tests: stub embedder round-trip, cache hit avoids re-embed, stale body re-embeds, corrupt sidecar tolerated, `get_default_embedder()` returns None without the extra.

**T2 — `maze/hybrid.py`: pure fusion math + hybrid re-rank.**
`rrf_fuse(rankings, k=60) -> dict[key, float]`; `semantic_ranking(query_vec, candidates) -> list[key]` (cosine, stable `(domain, order)` tiebreak); `hybrid_rerank(query, candidates, lexical_ranked_keys, embedder, cache) -> list[key]` returning the fused order over ALL candidates (sections with zero lexical overlap are rankable via the semantic arm — the point of the feature). Tests: RRF arithmetic vs hand-computed values, determinism across runs, tie stability, lexical-only fallback when embedder is None.

**T3 — plumbing: `ToolContext.hybrid_retrieval` + `Profile.hybrid_retrieval` + propagation.**
Both default `False`; `_sub_ctx` propagates; TUI `screens/main.py` sets ctx flag from the profile; `screens/settings.py` gains the Switch (3-line pattern next to `verify-switch`). Tests: ctx default off, sub-ctx propagation.

**T4 — tool integration (both search tools).**
After the lexical `hits` list and before sort/top-k: `if ctx.hybrid_retrieval:` build the candidate list (same context-heading exclusions), load embedder+cache (user-layer sidecar `<home>/.labrat/maze/<profile>/<kind>/.embeddings.jsonl`), fused order replaces the sort; lexical `score`/`matched_terms` reported as today (0 / [] for semantic-only hits). Flag off ⇒ code path untouched. Tests: paraphrase query retrieves a lexically-disjoint section under a stub embedder; flag-off output equality vs baseline capture; flag-on + no embedder ⇒ lexical; empty store `[]`.

**T5 — packaging + docs.** `pyproject.toml` optional extra `semantic = ["model2vec>=0.5,<0.7"]` (numpy-only, MIT, ~30 MB model artifacts fetched on first use); CLAUDE.md one-liner; this plan updated with outcomes.

## Acceptance gates (every task + end)
`uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest tests/unit -q` — plus full `uv run pytest -q` once at the end. Commit per task boundary.

## Decisions taken (flag for ratification)
- **Cache location:** ONE sidecar per kind in the **user layer** (`~/.labrat/maze/<profile>/<kind>/.embeddings.jsonl`) rather than per-layer sidecars — bodies are content-hash-keyed so origin layer is irrelevant, and the user layer is always writable in the TUI flow. Benchmark path never writes (flag off).
- **Default model id:** `minishlab/potion-base-8M` (model2vec's standard small model), overridable via `LABRAT_EMBED_MODEL` (local path or HF id). First use may fetch the model (provisioning); offline-without-model fails open to lexical.
- **Semantic-only hits report `score=0.0, matched_terms=[]`** (lexical fields keep lexical semantics; fused order is the ranking, not the displayed score). Alternative (report fused score) rejected to avoid changing the meaning of an existing field.
- **Settings-screen toggle shipped now** (trivial pattern); the spec left UI exposure open.
