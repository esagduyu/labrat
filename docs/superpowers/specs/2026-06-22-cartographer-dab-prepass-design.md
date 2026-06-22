# Cartographer pre-pass for DAB — GT-firewalled grounding (FEATURE_ROADMAP #26b → first-contact)

> **Status:** Design approved 2026-06-22. Wires the (already-shipped, #26b) Scent **cartographer**
> into the DAB benchmark as a deterministic, GT-firewalled **first-contact pre-pass** — the honest,
> precedent-validated form of grounding (Altimate's AutoContext, PR #53, was accepted on the
> leaderboard and drove their 0.604 → 0.689). Built as a **reusable seam** so the same pass later
> serves any warehouse on first contact (the north-star "no cold start"); DAB is the first consumer.
>
> **Branch:** `feat/cartographer-dab-prepass`. **Process:** superpowers — this spec → `writing-plans`
> → TDD → verification → review. Full gate every commit: `ruff format` → `ruff check` → `pyright` → `pytest`.

## 1. Why

We shipped the cartographer (#26b `generate_scent`) but left the Scent store **empty on DAB** — a
belt-and-suspenders over-correction after the Phase 5 contamination. That conflated two different
things: **hand-authored / answer-shaped docs** (leakage → forbidden) versus **mechanically-generated,
GT-firewalled structure** (grain, columns, `verify_join`-confirmed joins, observed sample values →
*legitimate grounding*). The roadmap already permits DAB grounding that "stays schema/grain/join
**structure** only" — exactly what the deterministic cartographer produces. The gap was **integration,
not capability**: we never connected the cartographer to the DAB pipeline.

The precedent is decisive: **Altimate's AutoContext (PR #53)** — a one-shot-per-dataset,
GT-firewalled schema-orientation doc — was **accepted on the leaderboard** and was the single biggest
delta in their run (~+8pp). Our cartographer is the same shape. Running it as a first-contact pre-pass
attacks our worst clean DAB datasets (deps_dev_v1, music_brainz, patents), which are **grounding**
failures, not tooling gaps.

## 2. Scope

**In scope:**
- A reusable **`cartograph_prepass(...)`** function with first-contact caching (generate once per DB,
  reuse thereafter), deterministic by default.
- Wiring it into **both** DAB drivers (`claude-mcp` — the full-run path — and `labrat-agent` — for
  ablation), running it before the agent and pointing the agent's `search_reference_docs` at the
  generated Scent.
- A one-line DAB-prompt addition telling the agent to consult `search_reference_docs` first.
- Disclosure note (for the submission) + the guardrails below.

**Explicitly out of scope (later):**
- The **general first-run auto-trigger** from the agent/TUI on connect — the seam is built here; the
  second caller is the follow-up.
- **Schema-hash drift / regeneration** (the #26b MAINTAIN cycle) — the pre-pass cache here is simple
  first-contact (no Scent yet → generate); drift detection is separate.
- The **LLM semantics pass on DAB** — deliberately OFF (see guardrails).

## 3. The reusable seam (the generalization point)

```python
def cartograph_prepass(
    connections: dict[str, object],
    catalogs: dict[str, object],
    primary: str,
    store_dir: Path,
    *,
    with_semantics: bool = False,
    llm_fn: LLMFn | None = None,
) -> list[Path]:
    """First-contact Scent pre-pass: for each connection, if a Scent doc already
    exists in store_dir, reuse it; otherwise generate_scent(...) and write it.
    Idempotent. Deterministic by default (with_semantics=False)."""
```

- **First-contact cache:** if a Scent doc already exists in `store_dir` for a connection's `domain`
  → skip generation; else `generate_scent(...)` → `write_docs(store_dir)`. Returns the doc paths
  (existing + newly written). Idempotent — a populated `store_dir` is a no-op.
- **Caller owns isolation:** the caller passes a `store_dir` scoped so distinct sources don't collide
  (see §4 — DAB uses a per-dataset store, since DAB connection keys like `main` repeat across datasets
  and a shared store would otherwise have dataset B reuse dataset A's `main.md`).
- **DAB calls it deterministic-only** (`with_semantics=False`, `llm_fn=None`).
- **Generalization:** the agent's first-connect path (run_agent_task / TUI) will later call the *same*
  function — with `with_semantics=True` and the #26a dual store (`./labrat_maze/scent` or
  `~/.labrat/maze/<profile>/scent`). No rework; just a second caller.
- Lives in `src/labrat/maze/cartographer.py`, next to `generate_scent`.

## 4. DAB wiring

In `_run_trial_claude_mcp` and `_run_trial_labrat_agent` (`src/labrat/eval/benchmarks/dab/suite.py`),
**before launching the agent**:

1. Build the task's DB `connections` and introspect `catalogs` (the labrat-agent path already does this
   via `build_dab_task_env` + `introspect_env_catalogs`; the claude-mcp path will build the same
   `Connection` objects from the task's db spec, run the pre-pass, then disconnect before launching the
   subprocess).
2. `cartograph_prepass(connections, catalogs, primary, store_dir=<ds_root>/labrat_maze/scent, with_semantics=False)`.
   - **Per-dataset store, persisted across trials:** `<ds_root> = <run_dir>/scent/<dataset>`. The
     pre-pass writes into `<ds_root>/labrat_maze/scent/` (the `MazeStore` layout). A dataset's **first**
     trial generates; its later trials see the populated store and reuse it (the first-contact cache).
     Per-dataset isolation prevents domain collisions across datasets that share a connection key
     (e.g. `main`). (Persisted under the run dir — not the per-trial scratch dir.)
3. Launch the agent with **`LABRAT_MAZE_DIR=<ds_root>`** (absolute) in its environment, so the MCP
   server's `search_reference_docs` (`MazeStore.from_env` → `<LABRAT_MAZE_DIR>/labrat_maze/scent/`)
   resolves *this dataset's* Scent. (The claude-mcp subprocess already forwards env; add this var. The
   agent runs in its isolated scratch cwd and reads the store via the env var, not cwd.)
4. Add to the DAB driver prompt(s): *"Call `search_reference_docs` first for curated grounding
   (grain, join keys, data-quality notes) before profiling or writing SQL."* (The prompts already
   instruct `link_schema`/`verify_join`; this adds the Scent consult.)

## 5. Guardrails (the sandboxing / "peeking" concerns, head-on)

- **Deterministic-only on DAB.** `with_semantics=False` — no LLM pass — so the Scent is *only*
  mechanically-derived structure (grain, columns, verified joins, observed sample values). No
  model-authored, hint-shaped prose. (Asserted by test: zero LLM calls.)
- **GT-firewalled by construction.** The pre-pass is *controller code* operating on `Connection`
  objects (`profile_dataset` + `verify_join` + bounded distinct probes). It has **no filesystem path**
  to `validate.py` / `ground_truth.csv` — it cannot read the answer key. It samples rows, which the
  agent could already do via `sample_rows` (legitimate, as in Altimate).
- **Existing sandbox unchanged.** The agent still runs MCP-only (`--allowedTools mcp__labrat`,
  `--disallowedTools` for Bash/Read/Web/…), isolated cwd, with the `_detect_contamination` post-trial
  backstop. The Scent store is read-only structure; it contains no answers.
- **Disclose it** in the submission, exactly as Altimate disclosed AutoContext — transparency is the
  accepted playbook.
- **agnews caveat unchanged.** The pre-pass adds no answers; agnews's model-*memory* leak is
  orthogonal and still caveated.

## 6. Ablation (non-negotiable)

Run the tuning subset (e.g. deps_dev_v1, music_brainz_20k, stockindex) **with vs without** the pre-pass
on the same driver/model; keep it only if net-positive. Altimate got ~+8pp; confirm on ours. Also run
the 9-task ADE smoke set as a regression check. Measure deltas — don't assume (the article's "three
net-negative iterations" lesson).

## 7. Testing plan (TDD)

- **prepass idempotency:** first call generates `<domain>.md`; a second call with the doc present
  **skips** (no regeneration, no LLM) and returns the existing path.
- **deterministic-only:** `with_semantics=False` performs **zero** LLM calls (spy asserts 0) and the
  produced doc's sections are all `Source: verified`.
- **end-to-end (ecommerce_db fixture):** `cartograph_prepass` → store → set `LABRAT_MAZE_DIR` →
  `SearchReferenceDocsTool` retrieves the generated doc for a relevant question.
- **driver wiring:** the DAB driver calls `cartograph_prepass` and sets `LABRAT_MAZE_DIR` to the store
  (unit-test the seam without launching a real agent; assert the store is populated + env is set).
- **GT-firewall (structural):** the pre-pass signature/impl only takes `connections`/`store_dir` and
  never receives or opens benchmark answer-key paths — verified by construction + a test that it writes
  only into `store_dir`.
- Gate every commit: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.

## 8. Decisions settled during brainstorming

- **Name:** keep **Cartographer** (`cartograph_prepass`) — clear, zero rename churn, and consistent
  with the shipped `cartographer.py` (rat-native alternatives Scout/Sniffer/Forager considered, declined).
- **Trigger model:** lazy **first-contact, cached per DB** — the seam that generalizes.
- **Scope:** DAB now via the reusable seam; the general first-run auto-trigger is a later second caller.
- **Safety:** deterministic-only on DAB; GT-firewalled by construction; sandbox unchanged; disclosed; ablated.
- **Placement:** `maze/cartographer.py`; **per-dataset** store at
  `<run_dir>/scent/<dataset>/labrat_maze/scent` with `LABRAT_MAZE_DIR=<run_dir>/scent/<dataset>`
  (isolation prevents connection-key collisions like `main`); wired into both DAB drivers
  (claude-mcp = full-run path, labrat-agent = ablation).

## 9. Provenance

Altimate AutoContext / DAB PR #53 (memory `reference_dab_pr53_altimate_precedent`): GT-firewalled,
accepted, ~+8pp. Anthropic self-service-analytics article (the reference-doc grounding thesis). The
June-2026 SOTA review (DS-STAR Analyzer ablation −18pp on hard tasks → grounding is the top lever).
Our cartographer (#26b) is the LabRat instance of this pattern; this spec connects it to the benchmark.
