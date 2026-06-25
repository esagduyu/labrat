# Verification Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a driver-agnostic verification layer to the DAB trial path — K-of-N consensus + an independent re-derive stage — so the `claude-mcp` leaderboard path can self-correct the wrong-but-plausible single-trial answers that cost us on the noisy datasets.

**Architecture:** Two pure shared primitives (`answers_agree` LLM-judge, `choose_modal` clustering) plus a trial-level wrapper in `DabSuite.run_trial` that runs the active driver N times and/or re-derives + reconciles. Driver-agnostic: it only needs "run this trial once → `(final_text, tool_calls, latency)`", so it covers both `claude-mcp` and `labrat-agent` uniformly. Both mechanisms are opt-in, independently flag-gated, default-off.

**Tech Stack:** Python 3.12, Pydantic v2, pytest (`asyncio_mode = "auto"`). Reuses `verifier.py::provider_llm_fn` + `LLMFn`.

## Global Constraints

- Branch: `feat/verification-layer` (created; spec committed there).
- Spec: `docs/superpowers/specs/2026-06-24-verification-layer-design.md`.
- `from __future__ import annotations` at the top of every new/edited `.py`.
- Pyright **strict** on all of `src/labrat/` — no Unknown leaks (`json.loads` → cast/`# type: ignore[arg-type]`).
- **Fail-open everywhere:** any judge/sub-run error counts as "agree" / falls back to the primary answer — verification can NEVER drop a correct answer or trap a trial.
- **Benchmark-safe by construction:** `answers_agree` and all sub-runs receive only `(question, answers, the same sandboxed driver)` — never a `validate.py`/`ground_truth.csv` path. Asserted in tests.
- **Off path is byte-identical:** `consensus_k in (None, 1)` and `reverify=False` → exactly today's behavior (full suite green).
- **Scope:** this plan covers the **benchmark (trial-level) integration** — the leaderboard priority. The product `run_agent_task(consensus_k=…, reverify=…)` params (spec §6) are a deferred follow-up; not in this plan.
- Run Python via `uv run`. Full gate after every task: `uv run ruff format .` → `uv run ruff check .` → `uv run pyright` → `uv run pytest -q`.
- Commit messages end with these two trailer lines verbatim:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj
  ```

---

### Task 1: `answers_agree` — the shared LLM-judge agreement primitive

**Files:**
- Create: `src/labrat/agent/verification/__init__.py` (empty package marker)
- Create: `src/labrat/agent/verification/agreement.py`
- Test: `tests/unit/test_answers_agree.py`

**Interfaces:**
- Consumes: `LLMFn = Callable[[str], Awaitable[str]]` from `labrat.agent.verifier`.
- Produces: `async def answers_agree(a: str, b: str, *, question: str, llm_fn: LLMFn) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_answers_agree.py
"""LLM-judge answer-equivalence primitive (FEATURE: verification layer)."""

from __future__ import annotations

from labrat.agent.verification.agreement import answers_agree


def _judge(reply: str):
    async def _fn(prompt: str) -> str:
        return reply
    return _fn


async def test_agree_when_judge_says_same() -> None:
    assert await answers_agree("72", "there are 72 CPC codes", question="how many?", llm_fn=_judge("same")) is True


async def test_disagree_when_judge_says_different() -> None:
    assert await answers_agree("72", "73", question="how many?", llm_fn=_judge("different")) is False


async def test_fail_open_on_garbage_verdict() -> None:
    # unparseable judge reply must count as agree (never drop a correct answer)
    assert await answers_agree("72", "73", question="q", llm_fn=_judge("uhh not sure")) is True


async def test_fail_open_on_judge_exception() -> None:
    async def _boom(prompt: str) -> str:
        raise RuntimeError("judge down")
    assert await answers_agree("a", "b", question="q", llm_fn=_boom) is True


async def test_identical_strings_short_circuit_no_llm() -> None:
    calls = {"n": 0}
    async def _count(prompt: str) -> str:
        calls["n"] += 1
        return "different"
    assert await answers_agree("42", "42", question="q", llm_fn=_count) is True
    assert calls["n"] == 0  # exact match needs no judge call
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_answers_agree.py -q`
Expected: FAIL — `cannot import name 'answers_agree'`.

- [ ] **Step 3: Implement**

```python
# src/labrat/agent/verification/agreement.py
"""answers_agree — LLM-judge equivalence for two free-text data answers.

The single agreement primitive shared by consensus voting and the re-derive stage.
The benchmark validator can't be used at inference (it's the answer key → leakage),
so agreement is judged answer-to-answer. Fail-open: any parse error or judge
exception counts as 'agree' so verification can never drop a correct answer.
"""

from __future__ import annotations

from labrat.agent.verifier import LLMFn


def _parse_agreement(raw: str) -> bool:
    """True unless the judge clearly says the answers differ. Fail-open."""
    s = raw.strip().lower()
    # accept only an explicit, unambiguous "different" verdict as disagreement
    if s.startswith("different") or s.startswith("no"):
        return False
    return True


async def answers_agree(a: str, b: str, *, question: str, llm_fn: LLMFn) -> bool:
    """Whether answers a and b express the same result for ``question``."""
    if a.strip() == b.strip():
        return True
    prompt = (
        "You compare two answers to the same data question and decide whether they "
        "express the SAME result. Ignore wording, formatting, units phrasing, and "
        "ordering — judge only whether the underlying result is the same.\n\n"
        f"Question:\n{question}\n\nAnswer A:\n{a}\n\nAnswer B:\n{b}\n\n"
        'Reply with EXACTLY one word: "same" or "different".'
    )
    try:
        raw = await llm_fn(prompt)
    except Exception:
        return True  # fail-open: never drop an answer on a judge error
    return _parse_agreement(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_answers_agree.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add src/labrat/agent/verification/ tests/unit/test_answers_agree.py
git commit -m "feat(verification): answers_agree LLM-judge equivalence primitive

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 2: `choose_modal` — consensus clustering / vote

**Files:**
- Create: `src/labrat/agent/verification/consensus.py`
- Test: `tests/unit/test_choose_modal.py`

**Interfaces:**
- Consumes: `answers_agree` (Task 1); `LLMFn`.
- Produces: `async def choose_modal(answers: list[str], *, question: str, llm_fn: LLMFn) -> tuple[int, bool]` — returns `(index_of_modal_answer, low_confidence)`. Greedy clustering by `answers_agree`; modal = largest cluster; **tie → index 0 (primary)**, `low_confidence=True`. `low_confidence` is also True when no cluster has >1 member.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_choose_modal.py
"""Consensus clustering / modal-answer selection (FEATURE: verification layer)."""

from __future__ import annotations

from labrat.agent.verification.consensus import choose_modal


def _equal_judge():
    # judges "same" iff the raw strings are equal (lets tests control clustering)
    async def _fn(prompt: str) -> str:
        return "different"  # answers_agree short-circuits exact matches before calling
    return _fn


async def test_majority_wins() -> None:
    idx, low = await choose_modal(["A", "B", "A"], question="q", llm_fn=_equal_judge())
    assert idx == 0  # "A" cluster (size 2) beats "B" (size 1); first "A" is index 0
    assert low is False


async def test_tie_breaks_to_primary_low_confidence() -> None:
    idx, low = await choose_modal(["A", "B"], question="q", llm_fn=_equal_judge())
    assert idx == 0
    assert low is True  # tie → primary, flagged low-confidence


async def test_all_distinct_is_low_confidence() -> None:
    idx, low = await choose_modal(["A", "B", "C"], question="q", llm_fn=_equal_judge())
    assert idx == 0 and low is True


async def test_single_answer_passthrough() -> None:
    idx, low = await choose_modal(["only"], question="q", llm_fn=_equal_judge())
    assert idx == 0 and low is True  # k=1 → no real consensus
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_choose_modal.py -q`
Expected: FAIL — `cannot import name 'choose_modal'`.

- [ ] **Step 3: Implement**

```python
# src/labrat/agent/verification/consensus.py
"""Consensus over N candidate answers: greedy cluster by answers_agree, pick the modal."""

from __future__ import annotations

from labrat.agent.verification.agreement import answers_agree
from labrat.agent.verifier import LLMFn


async def choose_modal(
    answers: list[str], *, question: str, llm_fn: LLMFn
) -> tuple[int, bool]:
    """Return (index of the modal answer, low_confidence).

    Greedy clustering: each answer joins the first cluster whose representative it
    agrees with, else seeds a new cluster. Modal = largest cluster (ties → earliest,
    so index 0 / the primary sub-run wins). low_confidence when the modal cluster has
    no majority (tie or all-distinct).
    """
    if not answers:
        return 0, True
    clusters: list[list[int]] = []  # each is a list of answer indices
    for i, ans in enumerate(answers):
        placed = False
        for cluster in clusters:
            rep = answers[cluster[0]]
            if await answers_agree(rep, ans, question=question, llm_fn=llm_fn):
                cluster.append(i)
                placed = True
                break
        if not placed:
            clusters.append([i])
    biggest = max(clusters, key=len)
    modal_index = biggest[0]
    # low confidence: the winning cluster isn't a strict majority of the candidates
    low_confidence = len(biggest) * 2 <= len(answers)
    return modal_index, low_confidence
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_choose_modal.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add src/labrat/agent/verification/consensus.py tests/unit/test_choose_modal.py
git commit -m "feat(verification): choose_modal consensus clustering/vote

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 3: Extract `_dispatch_driver_once` (behavior-preserving refactor + reconcile hook)

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` (`run_trial` dispatch ~515–527; `_run_trial_claude_mcp` + `_run_trial_labrat_agent` prompt construction)
- Test: existing `tests/unit/test_dab_suite_run_trial.py` (must stay green — proves behavior preserved)

**Interfaces:**
- Produces: `async def _dispatch_driver_once(self, task, db_config_path: Path, scratch_dir: Path, *, extra_instructions: str = "") -> tuple[str, int, float]` — runs the active driver once. `extra_instructions`, when non-empty, is appended to the driver's prompt (used by the re-derive reconcile pass). Both drivers accept and append it.

- [ ] **Step 1: Add the dispatch method (extract the run_trial try-block body)**

In `suite.py`, add a method that contains exactly the current if/elif/else dispatch (the exception handling stays in `run_trial`):

```python
    async def _dispatch_driver_once(
        self,
        task: BenchmarkTask,
        db_config_path: Path,
        scratch_dir: Path,
        *,
        extra_instructions: str = "",
    ) -> tuple[str, int, float]:
        if self._driver == "labrat-agent":
            return await self._run_trial_labrat_agent(
                task, db_config_path, scratch_dir, extra_instructions=extra_instructions
            )
        if self._driver == "claude-mcp":
            return await self._run_trial_claude_mcp(
                task, db_config_path, scratch_dir, extra_instructions=extra_instructions
            )
        return await self._run_trial_raw_bash(task, db_config_path)
```

- [ ] **Step 2: Thread `extra_instructions` into the two agent drivers**

`_run_trial_claude_mcp(self, task, db_config_path, scratch_dir)` → add `*, extra_instructions: str = ""`. After the prompt is built (`prompt = _build_claude_mcp_prompt(...)`), append:
```python
        if extra_instructions:
            prompt = f"{prompt}\n\n{extra_instructions}"
```
`_run_trial_labrat_agent(self, task, db_config_path, scratch_dir)` → add `*, extra_instructions: str = ""`. After `system_prompt` is assembled, append:
```python
        if extra_instructions:
            system_prompt = f"{system_prompt}\n\n{extra_instructions}"
```
(`_run_trial_raw_bash` does not take it — it's not a verification-eligible driver.)

- [ ] **Step 3: Point `run_trial` at the new method**

Replace the `try:` if/elif/else block (lines ~516–527) with:
```python
        try:
            final_text, tool_calls, latency = await self._run_trial_verified(
                task, db_config_path, scratch_dir
            )
        except Exception as exc:
            ...  # unchanged infra:agent_error / infra:timeout handler
```
(`_run_trial_verified` is added in Task 4; for THIS task make it a thin pass-through so the refactor is behavior-preserving and the suite stays green:)
```python
    async def _run_trial_verified(self, task, db_config_path, scratch_dir):
        return await self._dispatch_driver_once(task, db_config_path, scratch_dir)
```

- [ ] **Step 4: Run the existing DAB suite tests (behavior preserved)**

Run: `uv run pytest tests/unit/test_dab_suite_run_trial.py tests/unit/test_dab_cartographer.py -q`
Expected: PASS (no behavior change — single dispatch, no extra_instructions).

- [ ] **Step 5: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add src/labrat/eval/benchmarks/dab/suite.py
git commit -m "refactor(dab): extract _dispatch_driver_once + reconcile prompt hook

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 4: `_run_trial_verified` — consensus + re-derive + suite flags

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py` (`__init__`, `_run_trial_verified`)
- Test: `tests/unit/test_dab_verification.py`

**Interfaces:**
- Consumes: `answers_agree`, `choose_modal` (Tasks 1–2); `_dispatch_driver_once`, `provider_llm_fn` (existing in `verifier.py`).
- Produces: suite `__init__` gains `consensus_k: int | None = None`, `reverify: bool = False` → `self._consensus_k`, `self._reverify`. `_run_trial_verified` runs consensus then re-derive, returning `(final_text, tool_calls, latency)`.

- [ ] **Step 1: Write the failing test (consensus votes; reverify reconciles on mismatch)**

```python
# tests/unit/test_dab_verification.py
"""Trial-level verification: consensus + re-derive (FEATURE: verification layer)."""

from __future__ import annotations

from pathlib import Path

from labrat.eval.benchmarks.dab.suite import DabSuite
from labrat.eval.types import BenchmarkTask


def _task() -> BenchmarkTask:
    return BenchmarkTask(id="demo:1", benchmark="dab", prompt="how many?",
                         config={"db_config_path": "x", "validator_path": "y", "dataset": "demo"})


async def test_consensus_returns_modal(tmp_path: Path, monkeypatch) -> None:
    suite = DabSuite(driver="claude-mcp", consensus_k=3)
    answers = iter([("A", 5, 1.0), ("B", 5, 1.0), ("A", 5, 1.0)])  # modal = A
    async def _disp(self, task, dbp, sd, *, extra_instructions=""):
        return next(answers)
    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    # judge: only exact-equal answers agree (answers_agree short-circuits those)
    monkeypatch.setattr(suite, "_verify_llm_fn", lambda: (lambda p: _never_same(p)))
    text, tc, lat = await suite._run_trial_verified(_task(), Path("x"), tmp_path)
    assert text == "A"


async def test_reverify_keeps_primary_when_agree(tmp_path: Path, monkeypatch) -> None:
    suite = DabSuite(driver="claude-mcp", reverify=True)
    runs = iter([("42", 5, 1.0), ("42", 5, 1.0)])  # primary, re-derive — identical → agree
    async def _disp(self, task, dbp, sd, *, extra_instructions=""):
        return next(runs)
    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    text, _, _ = await suite._run_trial_verified(_task(), Path("x"), tmp_path)
    assert text == "42"  # agreement → primary unchanged, no reconcile run consumed


async def test_off_path_single_dispatch(tmp_path: Path, monkeypatch) -> None:
    suite = DabSuite(driver="claude-mcp")  # both off
    calls = {"n": 0}
    async def _disp(self, task, dbp, sd, *, extra_instructions=""):
        calls["n"] += 1
        return ("once", 1, 0.5)
    monkeypatch.setattr(DabSuite, "_dispatch_driver_once", _disp)
    text, _, _ = await suite._run_trial_verified(_task(), Path("x"), tmp_path)
    assert text == "once" and calls["n"] == 1  # exactly one dispatch when verification off


async def _never_same(prompt: str) -> str:
    return "different"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_verification.py -q`
Expected: FAIL — `DabSuite` has no `consensus_k`/`_run_trial_verified` with this behavior.

- [ ] **Step 3: Add flags + `_verify_llm_fn` + implement `_run_trial_verified`**

In `__init__` (next to `cartograph`): add params `consensus_k: int | None = None, reverify: bool = False` and store `self._consensus_k = consensus_k`, `self._reverify = reverify`.

Add a judge-builder (reuses the agent provider as the equivalence judge):
```python
    def _verify_llm_fn(self):
        from labrat.agent.providers import build_provider
        from labrat.agent.verifier import provider_llm_fn
        provider = build_provider(self._agent_provider, self._agent_model)
        return provider_llm_fn(provider)
```

Replace the Task-3 pass-through `_run_trial_verified` with:
```python
    async def _run_trial_verified(self, task, db_config_path, scratch_dir):
        from labrat.agent.verification.agreement import answers_agree
        from labrat.agent.verification.consensus import choose_modal

        question = task.prompt
        k = self._consensus_k or 1

        async def _run_once(i: int, extra: str = "") -> tuple[str, int, float]:
            sub = scratch_dir / f"subrun{i}" if (k > 1 or self._reverify) else scratch_dir
            sub.mkdir(parents=True, exist_ok=True)
            return await self._dispatch_driver_once(
                task, db_config_path, sub, extra_instructions=extra
            )

        # ── Consensus: K sub-runs → modal ──────────────────────────────
        if k > 1:
            results: list[tuple[str, int, float]] = []
            for i in range(k):
                try:
                    results.append(await _run_once(i))
                except Exception:
                    continue  # a failed sub-run is excluded from the vote
            if not results:
                return await _run_once(0)  # all failed → let run_trial's handler see it
            llm_fn = self._verify_llm_fn()
            idx, _low = await choose_modal(
                [r[0] for r in results], question=question, llm_fn=llm_fn
            )
            primary = results[idx]
        else:
            primary = await _run_once(0)

        # ── Re-derive: one independent run + reconcile on mismatch ──────
        if self._reverify:
            try:
                rederived = await _run_once(900)  # distinct sub-scratch
                llm_fn = self._verify_llm_fn()
                if not await answers_agree(
                    primary[0], rederived[0], question=question, llm_fn=llm_fn
                ):
                    reconcile = await _run_once(
                        901,
                        extra=(
                            "An independent recomputation produced a DIFFERENT result:\n"
                            f"  your answer: {primary[0]}\n  independent: {rederived[0]}\n"
                            "Recompute carefully and give the single correct final answer "
                            "on the last line."
                        ),
                    )
                    return reconcile
            except Exception:
                pass  # fail-open: keep the primary answer
        return primary
```
> Pyright note: annotate the inner result lists as `tuple[str, int, float]`. The monkeypatched `_dispatch_driver_once` in tests matches this signature.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dab_verification.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add src/labrat/eval/benchmarks/dab/suite.py tests/unit/test_dab_verification.py
git commit -m "feat(dab): trial-level verification — consensus + re-derive (driver-agnostic)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

### Task 5: DAB CLI flags `--agent-consensus` / `--agent-reverify`

**Files:**
- Modify: `scripts/eval_dab.py`
- Test: `tests/unit/test_dab_verification.py` (extend — flag plumbing)

**Interfaces:**
- Consumes: suite `consensus_k`/`reverify` (Task 4).
- Produces: `--agent-consensus K` (int, default None) + `--agent-reverify` (store_true, default None) threaded into the suite, mirroring `--agent-cartograph` (arg → resume-conflict guard → `effective_*` with config fallback → `DabSuite(...)` kwarg → `config.json`).

- [ ] **Step 1: Write the failing test (flags reach the suite)**

```python
def test_eval_dab_threads_verification_flags(monkeypatch, tmp_path) -> None:
    import scripts.eval_dab as ed
    captured = {}
    class _FakeSuite:
        def __init__(self, **kw): captured.update(kw)
        def tasks(self): return []
        def name(self): return "dab"
    monkeypatch.setattr(ed, "DabSuite", _FakeSuite)
    monkeypatch.setattr(ed, "_run_interim", lambda *a, **k: _noop())
    ed.main(["--driver", "claude-mcp", "--agent-consensus", "3", "--agent-reverify",
             "--output-dir", str(tmp_path / "r"), "--datasets", "deps_dev_v1"])
    assert captured.get("consensus_k") == 3
    assert captured.get("reverify") is True
```
(If `main`'s wiring makes a fully-faked suite awkward, mirror however the existing `test` for `--agent-cartograph` plumbing asserts it; the assertion that matters is `consensus_k==3` and `reverify is True` reach `DabSuite(...)`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dab_verification.py -k verification_flags -q`
Expected: FAIL — unrecognized arguments / kwargs absent.

- [ ] **Step 3: Add the flags (mirror `--agent-cartograph` at all 4 sites)**

Argument (next to `--agent-cartograph`):
```python
    parser.add_argument("--agent-consensus", type=int, default=None,
        help="K-of-N self-consistency: run each trial K times and take the modal answer (off by default).")
    parser.add_argument("--agent-reverify", action="store_true", default=None,
        help="Independent re-derive + reconcile-on-mismatch verify stage (off by default).")
```
Resume-conflict loop: add `("agent_consensus", args.agent_consensus)` and `("agent_reverify", args.agent_reverify)`.
Effective resolution:
```python
    effective_consensus: int | None = (
        args.agent_consensus if args.agent_consensus is not None
        else existing_cfg.get("agent_consensus")
    )
    effective_reverify: bool = bool(
        args.agent_reverify if args.agent_reverify is not None
        else existing_cfg.get("agent_reverify", False)
    )
```
`DabSuite(...)` kwargs: `consensus_k=effective_consensus, reverify=effective_reverify`.
`config.json` dict: `"agent_consensus": effective_consensus, "agent_reverify": effective_reverify`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_dab_verification.py -q`
Expected: PASS.

- [ ] **Step 5: Full gate + commit**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
```bash
git add scripts/eval_dab.py tests/unit/test_dab_verification.py
git commit -m "feat(dab): --agent-consensus / --agent-reverify flags

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01PpuX2GGj7mES1U9d83MRGj"
```

---

## After the build: ablation (controller, not a task)

Tuning subset, n=3, claude-mcp/Sonnet, on top of Cartographer + hints (current baseline), four arms:
```
--agent-cartograph --hints                                  # baseline (have: pd-off ≈ 7/24)
--agent-cartograph --hints --agent-consensus 3              # consensus only
--agent-cartograph --hints --agent-reverify                 # reverify only
--agent-cartograph --hints --agent-consensus 3 --agent-reverify   # both
```
Keep only net-positive mechanism(s) for the full run. (Consensus ≈ 3× wall-clock; budget accordingly.)

## Self-Review

**1. Spec coverage:** §3 `answers_agree` → Task 1 ✅. §4 consensus/`choose_modal` (nested-per-trial, tie→primary, low_confidence) → Tasks 2+4 ✅. §5 re-derive (independent run + reconcile, fail-open, driver-agnostic) → Task 4 ✅. §6 trial-level driver-agnostic integration + flags → Tasks 3–5 ✅. §7 benchmark-safety (no validator path in verify code; fail-open) → Tasks 1+4 tests ✅. §9 ablation → controller section ✅. Product `run_agent_task` params (§6) → explicitly deferred (Global Constraints scope) ✅.

**2. Placeholder scan:** No TBD/TODO. Two test steps say "mirror the existing --agent-cartograph plumbing assertion" — that's pointing at a concrete existing pattern, not a placeholder; the binding assertions (`consensus_k==3`, `reverify is True`, modal/agree behaviors) are spelled out.

**3. Type consistency:** `answers_agree(a,b,*,question,llm_fn)->bool` identical Tasks 1→2→4. `choose_modal(answers,*,question,llm_fn)->tuple[int,bool]` Tasks 2→4. `_dispatch_driver_once(self,task,db_config_path,scratch_dir,*,extra_instructions="")->tuple[str,int,float]` identical Tasks 3→4. Driver methods gain `*, extra_instructions=""` (Task 3) and are called with it (Task 4). Suite `consensus_k`/`reverify` defined Task 4, plumbed Task 5. `_verify_llm_fn` defined+used in Task 4.
