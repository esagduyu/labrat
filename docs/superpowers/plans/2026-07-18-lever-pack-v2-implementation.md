# Lever Pack v2 — implementation plan

Spec: `../specs/2026-07-18-lever-pack-v2-design.md` (locked, incl. preregistered
ablation + decision rules). Branch `feat/lever-pack-v2` off master `4a91260`.
Gates after every task: `uv run ruff format . && uv run ruff check . && uv run
pyright && uv run pytest tests/unit -q`. One commit per task.

## T1 — `agent_taxonomy` lever end-to-end
Files: `src/labrat/eval/benchmarks/dab/suite.py` (`_taxonomy_lines()`, DabSuite
kwarg `agent_taxonomy: bool = False`, prompt append in
`_build_labrat_agent_system_prompt(..., include_taxonomy=...)` + both call sites),
`scripts/eval_dab.py` (BooleanOptionalAction `--agent-taxonomy` default None,
resume-conflict tuple, effective_ default False, suite kwarg, config dict),
`scripts/dab_shards.py` (`_RECOVERY_COMPAT_KEYS` += `agent_taxonomy`).
Tests: `tests/unit/test_dab_prompt_levers.py` (on/off/golden-off-identity),
`test_eval_dab_runner.py` (persist + resume conflict), `test_dab_shards.py`
(compat mismatch).

## T2 — `local-embed` classify backend
Files: `src/labrat/agent/tools/base.py` (`ToolContext.llm_classify_backend`),
new `src/labrat/agent/tools/local_classify.py`, `llm_classify.py` routing,
`llm_primitives.py` (extract a reusable `select_classify_rows(...)` without
changing `extract_rows` semantics), `scripts/eval_dab.py` + `suite.py` plumbing
(kwarg/CLI/config/resume), `dab_shards.py` compat key.
Tests: new `tests/unit/test_local_classify_backend.py` (stub embedder; result
table + ledger shape parity, budget consumption/exhaustion, absent-embedder
self-error), plumbing tests alongside T1's.

## T3 — disclosure upgrades
Files: `suite.py` (`opening_prompt.txt` write in `_run_trial_labrat_agent` at
dispatch time), `scripts/build_dab_trace_bundle.py` (copy prompt file per trial
when present + per-trial `usage` in manifest entries).
Tests: `test_dab_suite_run_trial.py` (prompt file exists incl. turn-budget
terminal trials), `test_dab_trace_bundle.py` (manifest usage keys; prompt file
copied; all existing checks green).

## T4 — self-review + docs
Adversarial whole-branch review; fix findings; update `docs/dab-integration.md`
(new flags + hard-tail timeout recipe).

## T5 — ablation (per spec §Preregistered)
Arm L1 (45-key taxonomy) → merge → score; Arm L2 (agnews smoke) if quota.
Report per-dataset rates + decision-rule verdicts. No merge/push of the branch.
