# Benchmark-informed rule packs for DAB — design

**Date:** 2026-07-31 · **Status:** approved, pending implementation plan

## Why

The full Opus-5 270-trial run scored **0.7507 stratified** — already better than our
live entry (0.7418) and the best general-purpose entry on the board (Spacedock, 0.7433).
Simulation over its actual failures shows delivery fixes alone reach roughly **0.79–0.80**.

Crossing meaningfully past 0.80 requires the shared-hard wall: 30 of our 54 lost trials
sit on seven tasks where the accepted Luna run also scores ~0. Reading three of them
showed they are **comprehension and representation mismatches**, not capability gaps:

- `github_repos:1` — ground truth counts 3 READMEs; we find 128. A population-scoping
  disagreement.
- `pancancer_atlas:1` — we answer histology *names*; the expected form is *codes*, and
  five of them where we emit three.
- `deps_dev_v1:1` — three of five names correct; the two missed use a transitive
  dependency-path notation we never handle.

No tool fixes these. They are exactly the class of knowledge that `benchmark-informed`
entries encode, which is why all six entries above 0.80 carry that label and no
`general-purpose` entry has ever crossed it.

**Decision:** build a benchmark-informed rule pack, disclose it fully, and submit for a
maximum-score entry. Keep the existing untuned entry's history intact.

## What we are building

Four **independently-toggled** rule packs in a new module
`src/labrat/eval/benchmarks/dab/informed_packs.py`. Independence is the point: each is
ablated separately and only the ones that earn their place ship.

| pack | flag | encodes | derived from |
|---|---|---|---|
| A — answer shape | `--informed-shape` | report the code-form identifier when a table offers both code and name; emit every group when the result is per-group; preserve requested ranking order | `pancancer_atlas:1`, `patents:2`, `googlelocal:1` |
| B — validator shape | `--informed-validator` | place each value immediately adjacent to its label; state the answer in the head of the output as well as the last line; give full precision alongside a rounded form | `googlelocal:2`, `stockindex:1/2/3` |
| C — analytical conventions | `--informed-conventions` | smoothed-average seeding and zero-filled periods; corrupted join-key normalisation; extract prose-embedded values once into a temp table; handle path-style composite identifiers | `patents:2`, `deps_dev_v1:1/2` |
| D — per-dataset rules | `--informed-datasets` | rules naming datasets and their quirks directly | `github_repos:1` and the shared-hard set |

All default OFF. With every flag unset the emitted prompt is byte-identical to today's,
enforced by test.

## Integrity gate — blocking

Given the earlier retraction and the `5–11PM` near-miss (a lever example that was a
literal held-out ground-truth token), one test gates everything:

> Every literal string in every pack is grepped against ground-truth files and
> validators under `~/repos/DataAgentBench`. Any match fails the build.

The governing rule: **packs may encode FORM, never CONTENT.** "Report the code column"
is legitimate; the code itself is not. "Emit every group" is legitimate; the number of
groups is not.

This matters most for Pack D, where naming a dataset is acceptable but naming its answer
is disqualifying. Several rules are derived from scorer *feedback messages*, which
sometimes quote ground-truth values — so the grep is a mechanical boundary rather than a
matter of author judgement, and it is deliberately stricter than the existing
untuned-guard test, which only checks dataset names.

Per the DAB submission rubric, `benchmark-informed` is a **disclosure axis**, not a rubric
violation: Alkera, SCRIBE, Sarvam and DataBridge all carry the label in good standing.
What the rubric bars is a prompt that states a decisive value, label, threshold or
cardinality — which the grep gate is designed to make structurally impossible.

## Ablation

Run on **Sonnet-5, not Opus.** The packs are model-agnostic guidance and Sonnet is ~5×
cheaper on the Max plan, so we ablate properly and spend Opus budget only on the final
run.

Each pack is tested on the datasets whose failures it targets, plus a shared parity set
to detect dilution:

| arm | datasets |
|---|---|
| A | `pancancer_atlas`, `patents`, `deps_dev_v1` |
| B | `stockindex`, `googlelocal` |
| C | `patents`, `stockmarket`, `deps_dev_v1` |
| D | `github_repos`, `pancancer_atlas`, `agnews` |
| parity (every arm) | `bookreview`, `crmarenapro`, `yelp` |

Baseline for comparison is the completed Opus run for the same tasks *and* the best
Sonnet arm, so a pack is judged against a like-for-like Sonnet number.

### Escalation on mixed signals

A pack shows a **mixed signal** when any of these hold:

1. it improves its target datasets but regresses the parity set;
2. target-dataset improvement is within noise (Fisher p > 0.2) while parity moves at all;
3. its packs disagree across datasets — improving one target dataset while regressing
   another.

Any pack with a mixed signal is **re-ablated on Opus-5** before a ship/drop decision,
because the packs may interact with model verbosity and reasoning depth differently.
Packs that are cleanly positive or cleanly negative on Sonnet are decided there.

## Disclosure

Full disclosure. The PR declares the entry `benchmark-informed` and **publishes the exact
rule pack text**, as Alkera and Sarvam do. Maintainers assign the label from the PR and
traces regardless, so pre-empting costs nothing and protects credibility. The existing
untuned entry stays on the board as its own row; this is an additional entry, not a
replacement.

## Success criteria

- Every pack default-OFF and byte-identical when unset (tested).
- Contamination grep gate passes for every shipped pack.
- Each shipped pack has a measured, non-negative ablation result.
- Final Opus 270 run completes with a valid submission package: no orphaned
  `trials.jsonl`, no vacuous taint audit, log-derived and file-derived scores agreeing.

## Non-goals

- No tooling or orchestration work. Depth, grounding composition, consensus,
  `dispatch_subagent` and the full tool cross-reference have each been measured and each
  came back null; the failure data points at comprehension and delivery instead.
- No MCP-side subagent runner (separately scoped and rejected: expected gain zero).
- No change to the sandbox gate.
