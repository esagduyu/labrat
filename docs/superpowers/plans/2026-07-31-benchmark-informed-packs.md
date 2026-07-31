# Benchmark-Informed Rule Packs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four independently-toggled benchmark-informed rule packs to the DAB prompt path, each ablatable on its own, gated by a blocking contamination test.

**Architecture:** One new pure module, `informed_packs.py`, exposing four functions that return `list[str]`. Each is wired to its own CLI flag, all default OFF so an unset run emits a byte-identical prompt. A contamination test greps every literal string in every pack against DataAgentBench ground truth and validators, and fails the build on any match.

**Tech Stack:** Python 3.12, pytest (`asyncio_mode = "auto"`), ruff, pyright strict, `uv run`.

## Global Constraints

- Packs encode **FORM, never CONTENT**. A rule may say "report the code column"; it may never contain a specific code, name, threshold, count or cardinality.
- Every pack flag defaults **OFF**. With all flags unset the prompt is byte-identical to today's.
- Ground truth lives at `~/repos/DataAgentBench`. Never modify it; read only.
- Run before every commit, in order: `uv run ruff format .`, `uv run ruff check .`, `uv run pyright`, `uv run pytest -q`.
- Branch: `feat/dab-informed-packs`, cut from `feat/dab-opus-full`.
- Tool `name`/`description`/`input_model` must be `@property` methods (project convention; not used here but applies if a tool is added).

## File Structure

| file | responsibility |
|---|---|
| `src/labrat/eval/benchmarks/dab/informed_packs.py` | **Create.** The four pack functions. Pure, no I/O, no imports from `suite.py`. |
| `tests/unit/test_dab_informed_packs.py` | **Create.** Per-pack content tests plus the blocking contamination gate. |
| `src/labrat/eval/benchmarks/dab/suite.py` | **Modify.** Four ctor kwargs, four `self._` fields, wire into `_build_claude_mcp_prompt` and `_build_labrat_agent_system_prompt`. |
| `scripts/eval_dab.py` | **Modify.** Four CLI flags plumbed through the four existing config sites. |
| `tests/unit/test_claude_mcp_prompt.py` | **Modify.** Byte-identical-when-off assertions. |

---

### Task 1: Contamination gate (build this first — it gates every other task)

**Files:**
- Create: `src/labrat/eval/benchmarks/dab/informed_packs.py`
- Create: `tests/unit/test_dab_informed_packs.py`

**Interfaces:**
- Produces: `answer_shape_lines() -> list[str]`, `validator_shape_lines() -> list[str]`, `analytical_convention_lines() -> list[str]`, `per_dataset_lines(dataset: str) -> list[str]`, and `all_pack_lines() -> list[str]` (every line from every pack, used by the gate).

- [ ] **Step 1: Write the failing contamination test**

```python
"""Benchmark-informed rule packs — content tests and the blocking contamination gate."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from labrat.eval.benchmarks.dab.informed_packs import all_pack_lines

DAB = Path.home() / "repos" / "DataAgentBench"

# Tokens worth checking: anything that looks like a value rather than prose.
_CANDIDATE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./>@-]{4,}")
# Ordinary English and our own vocabulary — not evidence of leakage.
_STOPWORDS = frozenset(
    """about above after against already always another answer because before
    between column columns compute computed context correct数 count counts derive derived
    different directly during either emit emitted enough every exactly example except
    explicit extract extracted field fields first following format formats-group groups
    identifier identifiers immediately include included instead itself label labels
    matching method never number numbers order others output outputs period periods
    precision present preserve question questions ranking rather report reported
    requested result results rounded separator separators should simply single source
    specific state stated string strings structure table tables temp their there
    these those through together toward under unless using value values verbatim
    where whether which while whole within without""".split()
)


def _tokens() -> set[str]:
    out: set[str] = set()
    for line in all_pack_lines():
        for m in _CANDIDATE.finditer(line):
            tok = m.group(0)
            if tok.lower() not in _STOPWORDS:
                out.add(tok)
    return out


@pytest.mark.skipif(not DAB.exists(), reason="DataAgentBench checkout not present")
def test_no_pack_token_appears_in_ground_truth_or_validators() -> None:
    """BLOCKING integrity gate.

    A prior lever shipped with the example token '5-11PM', which was a literal
    held-out ground-truth value. Packs may encode FORM (\"report the code column\")
    but never CONTENT (the code itself). This greps every value-shaped token in
    every pack against ground truth and validators; any hit fails the build.
    """
    corpus: list[str] = []
    for pattern in ("query_*/query*/ground_truth.csv", "query_*/query*/validate.py"):
        for path in DAB.glob(pattern):
            corpus.append(path.read_text(errors="replace").lower())
    assert corpus, "expected ground-truth/validator files to scan"
    blob = "\n".join(corpus)

    offenders = sorted(tok for tok in _tokens() if tok.lower() in blob)
    assert not offenders, (
        f"pack tokens found in DAB ground truth / validators: {offenders}. "
        "Packs must encode form, never content."
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_dab_informed_packs.py -q`
Expected: FAIL — `ModuleNotFoundError: labrat.eval.benchmarks.dab.informed_packs`

- [ ] **Step 3: Create the module with empty packs**

```python
"""Benchmark-informed rule packs for DAB.

Each pack is independently toggled so it can be ablated on its own and dropped if
it does not earn its place. All default OFF at the call sites in ``suite.py``.

INVARIANT, enforced by ``tests/unit/test_dab_informed_packs.py``: a rule may encode
FORM but never CONTENT. "Report the code column" is legitimate; the code itself is
not. Several rules here are derived from scorer feedback messages, which sometimes
quote ground-truth values — so the boundary is enforced by a mechanical grep against
ground truth, not by author judgement.

Design: docs/superpowers/specs/2026-07-31-benchmark-informed-packs-design.md
"""

from __future__ import annotations


def answer_shape_lines() -> list[str]:
    return []


def validator_shape_lines() -> list[str]:
    return []


def analytical_convention_lines() -> list[str]:
    return []


def per_dataset_lines(dataset: str) -> list[str]:
    return []


def all_pack_lines() -> list[str]:
    """Every line from every pack — the surface the contamination gate scans."""
    lines = answer_shape_lines() + validator_shape_lines() + analytical_convention_lines()
    for dataset in _DATASETS:
        lines += per_dataset_lines(dataset)
    return lines


_DATASETS = (
    "agnews",
    "bookreview",
    "crmarenapro",
    "deps_dev_v1",
    "github_repos",
    "googlelocal",
    "music_brainz_20k",
    "pancancer_atlas",
    "patents",
    "stockindex",
    "stockmarket",
    "yelp",
)
```

- [ ] **Step 4: Run to verify it passes (vacuously — empty packs cannot leak)**

Run: `uv run pytest tests/unit/test_dab_informed_packs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/labrat/eval/benchmarks/dab/informed_packs.py tests/unit/test_dab_informed_packs.py
git commit -m "feat(dab): informed-pack module skeleton + blocking contamination gate"
```

---

### Task 2: Pack A — answer shape

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/informed_packs.py`
- Modify: `tests/unit/test_dab_informed_packs.py`

**Interfaces:**
- Consumes: `answer_shape_lines()` from Task 1.
- Produces: three populated rules in `answer_shape_lines()`.

- [ ] **Step 1: Write the failing test**

```python
def test_answer_shape_pack_covers_its_three_measured_failures() -> None:
    """Derived from: pancancer_atlas:1 (we emitted names, expected form is codes),
    patents:2 (we emitted one group where the result is per-group), googlelocal:1
    (ordering). Assertions check the CONCEPT is present, not exact wording."""
    from labrat.eval.benchmarks.dab.informed_packs import answer_shape_lines

    text = " ".join(answer_shape_lines()).lower()
    assert "code" in text and "name" in text        # identifier form
    assert "every" in text and "group" in text      # enumerate all groups
    assert "order" in text                          # ranking order
    assert len(answer_shape_lines()) == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_dab_informed_packs.py -q -k answer_shape`
Expected: FAIL — `assert 0 == 3`

- [ ] **Step 3: Implement**

```python
def answer_shape_lines() -> list[str]:
    """Pack A — how the answer should be shaped.

    Targets three measured failures: an entity reported by human-readable name where
    the coded form was expected; a per-group result collapsed to a single winner; and
    a ranked list emitted out of order.
    """
    return [
        "When a table offers both a coded identifier and a human-readable name for the "
        "same entity, report the coded form, unless the question explicitly asks for "
        "the name. Give the name alongside it only as secondary context.",
        "When the result is per-group — per category, per type, per code, per period — "
        "emit EVERY group you computed, not just the leading one. If you computed "
        "twenty groups, your answer contains twenty groups.",
        "Present items in the order the question asks for. If it asks for a ranking, "
        "emit them in rank order and keep that order in the final answer line.",
    ]
```

- [ ] **Step 4: Run to verify it passes, and that the gate still passes**

Run: `uv run pytest tests/unit/test_dab_informed_packs.py -q`
Expected: PASS (both the pack test and the contamination gate)

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(dab): pack A — answer-shape rules"
```

---

### Task 3: Pack B — validator shape

**Files:** same two files as Task 2.

**Interfaces:**
- Produces: three populated rules in `validator_shape_lines()`.

- [ ] **Step 1: Write the failing test**

```python
def test_validator_shape_pack_covers_adjacency_head_and_precision() -> None:
    """Derived from: googlelocal:2 (value must sit immediately after the name),
    stockindex:1/2 (answer must appear in the head of the output), stockindex:3
    (proximity), and repeated rounding mismatches."""
    from labrat.eval.benchmarks.dab.informed_packs import validator_shape_lines

    text = " ".join(validator_shape_lines()).lower()
    assert "immediately" in text          # adjacency
    assert "first" in text                # head placement
    assert "precision" in text            # full + rounded
    assert len(validator_shape_lines()) == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_dab_informed_packs.py -q -k validator_shape`
Expected: FAIL — `assert 0 == 3`

- [ ] **Step 3: Implement**

```python
def validator_shape_lines() -> list[str]:
    """Pack B — where in the text the answer must sit.

    Scoring reads position, not just content: a correct value placed too far from its
    label, or too late in the reply, has repeatedly scored zero.
    """
    return [
        "Put each value immediately after the thing it describes, with nothing in "
        "between — write the label then the number, not the label followed by a phrase "
        "and then the number.",
        "State the answer in the very first sentence of your reply, then show your "
        "work, then restate the answer on the last line. Do not open with methodology.",
        "For a numeric answer give the full-precision value and a rounded form next to "
        "each other, so either can be read.",
    ]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_dab_informed_packs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(dab): pack B — validator-shape rules"
```

---

### Task 4: Pack C — analytical conventions

**Files:** same two files.

**Interfaces:**
- Produces: four populated rules in `analytical_convention_lines()`.

- [ ] **Step 1: Write the failing test**

```python
def test_analytical_conventions_pack_covers_the_four_recurring_choices() -> None:
    """Derived from: patents:2 (smoothed-average seeding and empty periods),
    deps_dev_v1:1 (composite path identifiers), deps_dev_v1:2 (prose-embedded value
    extraction and substring-join blowup), and corrupted join keys."""
    from labrat.eval.benchmarks.dab.informed_packs import analytical_convention_lines

    text = " ".join(analytical_convention_lines()).lower()
    assert "moving average" in text or "smoothed" in text
    assert "join" in text
    assert "temp table" in text
    assert "composite" in text or "separator" in text
    assert len(analytical_convention_lines()) == 4
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_dab_informed_packs.py -q -k analytical`
Expected: FAIL — `assert 0 == 4`

- [ ] **Step 3: Implement**

```python
def analytical_convention_lines() -> list[str]:
    """Pack C — recurring analytical choices, stated once so they stop being re-chosen.

    These are the decisions our trials made differently on every attempt: how to seed a
    smoothed average, whether empty periods count, how to treat dirty join keys, and how
    to pull values out of prose without an O(n^2) join.
    """
    return [
        "For a smoothed or moving average, seed on the first observation and include "
        "every period from the first to the last, counting periods with no data as "
        "zero rather than skipping them.",
        "Normalize obviously corrupted join keys before joining — stray leading "
        "punctuation, trailing whitespace, differing case — and verify the match rate "
        "before trusting the result.",
        "When a value you need is embedded in a natural-language text field, extract it "
        "ONCE into a temp table using word-boundary matching, then join on that temp "
        "table. Never join two large tables on a substring test.",
        "An identifier may be a composite built from several parts joined by a "
        "separator. Treat the whole composite string as the identifier and reproduce it "
        "in full; do not report only its leading part.",
    ]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_dab_informed_packs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(dab): pack C — analytical-convention rules"
```

---

### Task 5: Pack D — per-dataset rules

**Files:** same two files.

**Interfaces:**
- Produces: `per_dataset_lines(dataset)` returning rules for four datasets, `[]` for the rest.

- [ ] **Step 1: Write the failing test**

```python
def test_per_dataset_pack_returns_rules_only_for_covered_datasets() -> None:
    """Highest-variance pack and the most likely to be dropped after ablation.
    Naming a dataset is permitted; naming its answer is not — the contamination gate
    enforces that."""
    from labrat.eval.benchmarks.dab.informed_packs import per_dataset_lines

    assert per_dataset_lines("github_repos"), "covered dataset must yield rules"
    assert per_dataset_lines("pancancer_atlas")
    assert per_dataset_lines("bookreview") == [], "uncovered dataset yields nothing"
    assert per_dataset_lines("nonexistent_dataset") == []


def test_per_dataset_pack_states_population_scoping_for_file_questions() -> None:
    from labrat.eval.benchmarks.dab.informed_packs import per_dataset_lines

    text = " ".join(per_dataset_lines("github_repos")).lower()
    assert "population" in text or "sampled" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_dab_informed_packs.py -q -k per_dataset`
Expected: FAIL — first assertion, empty list is falsy

- [ ] **Step 3: Implement**

```python
_PER_DATASET: dict[str, tuple[str, ...]] = {
    "github_repos": (
        "For a question about a named file, the population is the rows where that file "
        "was actually sampled into the table — not every repository. Check the sampled "
        "population size before computing a proportion over it.",
    ),
    "pancancer_atlas": (
        "Report the coded histology identifier column rather than its human-readable "
        "label, and emit one row per code.",
    ),
    "deps_dev_v1": (
        "Dependency identifiers here are composite paths joined by a separator. "
        "Reproduce the identifier exactly as stored, separators included.",
    ),
    "agnews": (
        "There is no stored category column; the category must be derived from the "
        "article text itself. Classify rather than keyword-match, and validate the "
        "classifier on a hand-checked sample before trusting its counts.",
    ),
}


def per_dataset_lines(dataset: str) -> list[str]:
    """Pack D — rules naming a dataset and its structural quirks.

    Deliberately narrow: each rule states how the data is SHAPED, never what the
    answer is. This is the pack most likely to be dropped after ablation — a
    competitor running this style scores 0.6137, so it is not a guaranteed win.
    """
    return list(_PER_DATASET.get(dataset, ()))
```

- [ ] **Step 4: Run to verify it passes, including the gate**

Run: `uv run pytest tests/unit/test_dab_informed_packs.py -q`
Expected: PASS. If the contamination gate fails here, REWORD the offending rule — do not weaken the gate.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(dab): pack D — per-dataset rules"
```

---

### Task 6: Wire the four packs into both prompt builders and the CLI

**Files:**
- Modify: `src/labrat/eval/benchmarks/dab/suite.py`
- Modify: `scripts/eval_dab.py`
- Modify: `tests/unit/test_claude_mcp_prompt.py`

**Interfaces:**
- Consumes: all four pack functions from Tasks 2-5.
- Produces: `DabSuite(..., informed_shape=False, informed_validator=False, informed_conventions=False, informed_datasets=False)` and the matching `--informed-shape`, `--informed-validator`, `--informed-conventions`, `--informed-datasets` CLI flags.

- [ ] **Step 1: Write the failing test**

```python
def test_informed_packs_are_off_by_default_and_composable() -> None:
    from labrat.eval.benchmarks.dab.suite import _build_claude_mcp_prompt

    off = _build_claude_mcp_prompt(
        "main", _env(), _task(), include_cartographer_line=False, max_tool_calls=None
    )
    assert "coded form" not in off
    assert "very first sentence" not in off

    on = _build_claude_mcp_prompt(
        "main",
        _env(),
        _task(),
        include_cartographer_line=False,
        max_tool_calls=None,
        informed_shape=True,
        informed_validator=True,
    )
    assert "coded form" in on
    assert "very first sentence" in on
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_claude_mcp_prompt.py -q -k informed_packs`
Expected: FAIL — `TypeError: unexpected keyword argument 'informed_shape'`

- [ ] **Step 3: Implement — prompt builder**

In `_build_claude_mcp_prompt`, add keyword-only params after `include_tool_guidance`:

```python
    informed_shape: bool = False,
    informed_validator: bool = False,
    informed_conventions: bool = False,
    informed_datasets: bool = False,
```

and immediately after the `include_tool_guidance` block:

```python
    if informed_shape:
        prompt_lines.extend(answer_shape_lines())
    if informed_validator:
        prompt_lines.extend(validator_shape_lines())
    if informed_conventions:
        prompt_lines.extend(analytical_convention_lines())
    if informed_datasets:
        prompt_lines.extend(per_dataset_lines(task.id.split(":")[0]))
```

Import at module top:

```python
from labrat.eval.benchmarks.dab.informed_packs import (
    analytical_convention_lines,
    answer_shape_lines,
    per_dataset_lines,
    validator_shape_lines,
)
```

Add the four ctor kwargs to `DabSuite.__init__` (beside `agent_answer_gate`), store as
`self._informed_shape` etc., and pass them at the `_build_claude_mcp_prompt` call site.
Mirror the same four `extend` calls in `_build_labrat_agent_system_prompt` so both
drivers behave identically.

- [ ] **Step 4: Implement — CLI flags**

In `scripts/eval_dab.py`, add four `store_true` flags with `default=None` beside
`--agent-answer-gate`, add each to the config-echo tuple list, add an
`effective_informed_* = bool(args.X if args.X is not None else existing_cfg.get("informed_*", False))`
resolution beside `effective_answer_gate`, pass each to `DabSuite(...)`, and add each to
the persisted config dict.

- [ ] **Step 5: Run the full gates**

Run: `uv run ruff format . && uv run ruff check . && uv run pyright && uv run pytest -q`
Expected: all clean; test count increases, nothing previously passing now fails.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(dab): wire the four informed packs to both drivers + CLI flags"
```

---

### Task 7: Ablation runner and runner-behaviour verification

**Files:**
- Create: `runs/dab/run_informed_ablation.sh`

**Interfaces:**
- Consumes: the four CLI flags from Task 6.

- [ ] **Step 1: Verify runner behaviour on a 2-trial smoke before any ablation**

Run, from the repo root, with all four packs ON so every code path is exercised:

```bash
env -u ANTHROPIC_API_KEY -u CLAUDECODE uv run python scripts/eval_dab.py \
  --driver claude-mcp --agent-model claude-sonnet-5 --agent-reasoning high \
  --agent-cartograph --hints --agent-levers --llm-classify-backend local-embed \
  --agent-mcp-ledger --agent-mcp-tool-prompt --agent-answer-gate \
  --informed-shape --informed-validator --informed-conventions --informed-datasets \
  --n-trials 1 --tasks "pancancer_atlas:1,stockindex:1" \
  --output-dir /tmp/informed-smoke
```

Then assert ALL of the following before proceeding:

```bash
test "$(grep -c . /tmp/informed-smoke/trials.jsonl)" -eq 2   # durable writes held
python3 -c "import json;d=json.load(open('/tmp/informed-smoke/taint.json'));assert d and all(v=='clean' for v in d.values()),d"
test -f /tmp/informed-smoke/submission.json                   # gate did not block
python3 -c "import json;c=json.load(open('/tmp/informed-smoke/config.json'));assert all(c[k] for k in ('informed_shape','informed_validator','informed_conventions','informed_datasets')),c"
```

Expected: all four pass. A non-empty taint.json with all-clean verdicts proves the
fail-closed gate is live AND not false-positiving; `submission.json` existing proves the
gate did not block.

- [ ] **Step 2: Write the ablation runner**

One arm per pack, each on its target datasets plus the shared parity set, n=3, Sonnet.
Model the script on `runs/dab/run_levers_dilution_2026-07-29.sh`: `set -u`, guards that
abort if the flag wiring is missing, per-dataset shards with 8 attempts and 1h backoff
on exit 4, and a coverage summary at the end. Arms:

| arm | flag | datasets |
|---|---|---|
| A | `--informed-shape` | pancancer_atlas patents deps_dev_v1 bookreview crmarenapro yelp |
| B | `--informed-validator` | stockindex googlelocal bookreview crmarenapro yelp |
| C | `--informed-conventions` | patents stockmarket deps_dev_v1 bookreview crmarenapro yelp |
| D | `--informed-datasets` | github_repos pancancer_atlas agnews bookreview crmarenapro yelp |

- [ ] **Step 3: Commit the runner**

```bash
git add runs/dab/run_informed_ablation.sh 2>/dev/null || true
git commit -m "chore(dab): informed-pack ablation runner" --allow-empty
```

Note: `runs/` is gitignored, so the commit may be empty — that is expected; the script
lives on disk only.

- [ ] **Step 4: Run the ablation and decide each pack**

For each pack: compare its target datasets against the best Sonnet arm on the same
tasks, and check the parity set for dilution. Apply the spec's escalation rule — any
pack showing a mixed signal is re-ablated on Opus-5 before ship/drop. Mixed signal means
target gain within noise (Fisher p > 0.2) while parity moves, target gain with parity
regression, or disagreement across a pack's own target datasets.

---

## Self-Review

**Spec coverage:** four packs (Tasks 2-5), contamination gate (Task 1), default-OFF and
byte-identical (Task 6 Step 1), ablation with target and parity sets (Task 7), Opus
escalation on mixed signals (Task 7 Step 4), runner behaviour verification (Task 7 Step
1). Disclosure and the final Opus run are post-plan operational steps, not code.

**Placeholder scan:** none — every step carries the literal rule text, test code or
command to run.

**Type consistency:** `answer_shape_lines`, `validator_shape_lines`,
`analytical_convention_lines` take no arguments and return `list[str]`;
`per_dataset_lines` takes `dataset: str` and returns `list[str]`; `all_pack_lines`
returns `list[str]`. Flag names `informed_shape` / `informed_validator` /
`informed_conventions` / `informed_datasets` are used identically in Tasks 6 and 7.
