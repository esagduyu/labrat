# Verification layer — consensus + re-derive (FEATURE_ROADMAP T1a)

> **Status:** Design approved 2026-06-24. The #1 competitor-proven lever: both top-2 DAB teams verify and LabRat doesn't. **Spacedock (#1)** is an adversarial-review engine (separate re-derive verify stage); **Altimate (#2)** uses K=3 self-consistency consensus. Our existing opt-in *sufficiency-judge* verifier measured **no benefit** (it judges "does the answer address the question," not whether the answer is *right*) — this layer adds the two mechanisms that actually work, done right.
>
> **Branch:** `feat/verification-layer`. **Process:** superpowers — this spec → `writing-plans` → TDD → review → finish. Full gate every commit: `ruff format` → `ruff check` → `pyright` → `pytest`.

## 1. Why

LabRat is #8/21 on DataAgentBench (60.88%) — the only top-10 entry on a single mid-tier model. The visible gap to the top cluster is partly model tier (they run GPT‑5.5/Opus/ensembles), but the **one technique every team above us uses and we don't is verification.** Our worst datasets — deps_dev_v1 (20%), music_brainz (27%), yelp (46%) — are exactly where *wrong-but-plausible* single-trial answers slip through. Self-consistency catches stochastic errors; an independent re-derivation catches logic errors. This is also a product story: **verification as a first-class analyst guarantee** (every answer re-derived + provenance-stamped → the Cheese artifact), and the durable form of the grounding moat.

## 2. Scope

**In scope:** a reusable verification layer with **two independent, composable mechanisms** (consensus, re-derive), both **opt-in / default-off**, sharing one **answer-agreement** primitive; wired into `run_agent_task` (general capability) and consumed by DAB via flags. Each mechanism independently toggleable → independently ablatable.

**Out of scope:** ripping out the existing sufficiency-judge verifier (`verifier.py` `LLMVerifier`) — it stays as-is (opt-in, default-off); we add alongside. Validator-based scoring at inference (that's the answer key — leakage). Multi-stage workflow orchestration beyond the two mechanisms.

## 3. The shared primitive — answer agreement

```python
# labrat/agent/verification/agreement.py
async def answers_agree(a: str, b: str, *, question: str, llm_fn: LLMFn) -> bool:
    """True if answers a and b express the same result for `question`.

    One LLM-judge call (same model as the agent — a cheaper judge loses the wins,
    per Anthropic's self-service-analytics finding). Handles 72 ≡ "there are 72 CPC
    codes", units, ordering, prose-vs-table. FAIL-OPEN: an unparseable verdict counts
    as 'agree' so the layer can never trap or silently drop a correct answer.
    """
```
- Pure answer-to-answer comparison — **never** sees `validate.py` / `ground_truth.csv`. Benchmark-safe by construction.
- Mirrors the existing `parse_verdict` / `provider_llm_fn` patterns in `verifier.py`.

## 4. Mechanism A — Consensus (self-consistency)

A wrapper around the task run. Runs **K sub-runs** (default `K=3`) of the same task, collects the K final answers, clusters them with `answers_agree`, returns the **modal** answer.

- **Granularity:** K sub-runs **nested inside each trial** — so for DAB pass@5, each of the 5 trials internally votes K sub-runs → one answer per trial → the 5-trial scoring structure is preserved (Altimate's shape).
- **Clustering:** greedy — first answer seeds cluster 0; each subsequent answer joins the first cluster whose representative it `answers_agree` with, else starts a new cluster. Modal = largest cluster; **tie → the agent's first (primary) sub-run answer**, marked `low_confidence` in the result.
- **Pure resampling** — no new tool surface, no leakage path.
- Returns the chosen answer + metadata (cluster sizes, K, low_confidence flag) for the trace.

## 5. Mechanism B — Re-derive verify stage

Extends the existing `AgentLoop` would-be-final-turn verifier hook — but the verifier is a **re-derivation**, not a sufficiency judge:

- At the would-be-final answer, spawn an **independent re-derivation**: a fresh agent run (fresh context / sub-task, same registry + tools + DB connections, same `ToolContext`) that recomputes the answer from scratch.
- `answers_agree(primary, rederived, question, llm_fn)`:
  - **agree** → accept the primary answer, done.
  - **mismatch** → inject the disagreement (both answers) as a new user turn — "an independent recomputation got X; reconcile and give your final answer" — and let the agent revise. **Bounded to 1 revise round** (then accept the agent's primary answer, marked `low_confidence`). Bound is also capped by the loop's remaining turn budget.
- Reads only the DB via the same tools — **no validator/answer-key path**. **Fail-open:** any error in the re-derivation or judge → accept the primary answer (never trap the loop).

## 6. Integration + flags

- `run_agent_task(..., consensus_k: int | None = None, reverify: bool = False, on_status=…)` — general capability; status (cluster sizes, re-derive verdicts) goes to `on_status`, never `on_text` (don't corrupt `final_text`).
- `AgentLoop` gains the re-derive verifier as a variant of the existing verifier seam (reuse `verifier.py` types where possible).
- DAB driver flags in `scripts/eval_dab.py`: `--agent-consensus K` and `--agent-reverify` (mirror the `--agent-cartograph` plumbing: `store_true`/int, resume-config, conflict-guard). Each independent.
- **Default off** both — they cost extra calls (consensus ≈ K× agent runs + (K−1) judge calls; re-derive ≈ +1 agent run + 1 judge call). Fine on flat-rate Max-plan for the benchmark; opt-in for product (latency/cost).
- Traces: consensus/re-derive decisions recorded via the existing trace writer (`append_tool_trace`-adjacent or the `on_tool_call`/status path) so a submission can show the verification happened.

## 7. Benchmark-safety (explicit)

- `answers_agree` and the re-derivation receive only `(question, answers, ToolContext)` — **never** a validator or ground-truth path (asserted structurally in tests).
- Re-derivation uses the same MCP-only sandboxed tools as the agent → no new leakage surface; the `_detect_contamination` backstop still runs on the final text.
- Consensus is resampling — no new surface at all.

## 8. Testing plan (TDD)

- **`answers_agree`:** with a fake `llm_fn` — `72` ≡ "there are 72 CPC codes" (agree), `72` vs `73` (disagree), unparseable verdict → fail-open agree. No real LLM.
- **Consensus:** fake provider returning controlled per-sub-run answers — modal selected; tie → primary + `low_confidence`; K=1 ≡ no consensus (passthrough).
- **Re-derive:** agree → accept primary unchanged; mismatch → exactly one revise round injected then accept; re-derivation error → fail-open accept. Assert no validator path reaches the verify code.
- **Off path:** `consensus_k=None`/`reverify=False` → byte-identical to today (full suite green).
- **DAB flags:** `--agent-consensus`/`--agent-reverify` plumb through to `run_agent_task` (resume-safe, conflict-guarded) like `--agent-cartograph`.
- Gate every commit: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.

## 9. Ablation (non-negotiable, before any full run)

Tuning subset (deps_dev_v1, music_brainz_20k, stockindex), n=3, four arms: **off / consensus-only (K=3) / reverify-only / both**. Keep only net-positive mechanism(s); measure each independently (the reason both ship behind separate flags). Cartographer + hints stay on (current baseline).

## 10. Decisions settled in brainstorming

- **Both mechanisms, one composable layer, each independently toggleable** (recovers ablatability).
- **Agreement = LLM-judge equivalence**, same model, fail-open (the only robust comparison across DAB's free-text/numeric/tabular answer shapes; validator is off-limits at inference).
- **Consensus = K sub-runs nested per trial** (preserves pass@5); default K=3 (Altimate's value).
- **Failure actions:** re-derive mismatch → 1 bounded revise round then primary; consensus tie → primary, both flagged `low_confidence`.
- **General capability** in `run_agent_task`, DAB-consumed via flags; default off.
- Existing sufficiency-judge verifier untouched (kept opt-in/off).

## 11. Provenance

Spacedock (#1, [spacedock.md](https://spacedock.md)) — adversarial review as a separate stage. Altimate (#2, PR #53/R27) — K=3 consensus + LLM-authored AutoContext. Anthropic self-service-analytics article — same-model reviewer beats a cheaper one. FEATURE_ROADMAP T1a; supersedes the no-benefit #13 sufficiency judge.
