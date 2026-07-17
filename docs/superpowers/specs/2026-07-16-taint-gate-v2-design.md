# Taint gate v2 — structural detection layers (design)

**Status:** approved for build 2026-07-16 (follow-up to audit finding P2-2 in
`docs/claude-fable-gpt56-dab-audit-report.md`) · **Branch:** `feat/taint-gate-v2`

## Problem

The automated DAB taint gate (`eval/benchmarks/dab/taint.py`) is a
case-insensitive substring scan (9 needles from `maze/scent_audit.py`) over the
concatenated artifact + trace text. The 2026-07-16 audit showed it is a thin
backstop: SQL obfuscation (`concat('ground_','truth.csv')`, `'ground_' ||
'truth.csv'`), answer-key access via *paths that never name the needle*
(`query3/gt.csv`), web-source file reads (`read_csv_auto('https://…')` — DuckDB
reads HTTP), and arbitrary local reads through `load_file`/`attach_database`
all pass the current gate. The in-process labrat-agent driver is **not** a
filesystem sandbox, so the gate is the only automated line before the human
trace audit.

## Threat model (what a leak looks like in a trace)

1. **Answer-key file access** — reading `ground_truth.csv`/`validate.py`, or
   anything under a benchmark *query* directory (`query_<ds>/query<N>/…` holds
   the keys; sanctioned stores live under `query_<ds>/query_dataset/…`).
2. **Web fetch of labels** — a file-source argument whose value is a URL
   (DuckDB `read_csv_auto('https://…')`, `load_file` of a URL), or external
   labelled datasets (HuggingFace — already a text needle).
3. **Prior-run/submission artifacts** — reading `trials.jsonl`,
   `submission.json`, `taint.json`, trace JSONLs, `results.json`,
   `benchmark.json`, `leaderboard_submissions/…`, `runs/dab/…`, `.git/…`.
4. **Escape hatches** — path traversal (`..`) in a file source; absolute paths
   outside the sanctioned layout entirely.

Channel asymmetry: **inputs are agent intent; outputs are dataset content.**
Benchmark outputs legitimately contain URLs, `validator`-ish strings, and
default-credential docs (the github_repos corpus is full of them), so v2's new
layers inspect *tool inputs* (and file-source arguments specifically). The
existing whole-text needle scan is retained unchanged as the last layer.

## Detection layers

- **(a) Structural file-source classification.** Extract every path-bearing
  argument: `load_file.path`, `attach_database.path`, and SQL sources inside
  `run_sql`/`check_sql`/`explain_sql` (`read_csv[_auto]`, `read_parquet`,
  `read_json[_auto]`, `read_ndjson`, `read_text`, `ATTACH '…'`,
  `COPY … FROM/TO '…'`, quoted-file `FROM '…'`). Classify each source,
  fail-closed in this order: URL scheme → `web_fetch`; sensitive
  basename/needle → `answer_key`; `query<N>` path segment → `answer_key_dir`;
  prior-run/VCS segments (`runs/dab`, `leaderboard_submissions`, `.git`) →
  `prior_artifact`; `..` traversal → `unsanctioned_path`; absolute path with no
  `query_dataset` segment and not under temp/scratch → `unsanctioned_path`.
  Relative paths and `query_dataset`/temp/scratch paths are allowed. The rule
  set is **layout-shape based, checkout-root independent** (the audit context
  has no task env: a standalone run dir doesn't record the DAB checkout path).
- **(b) SQL literal normalization.** Extract single-quoted literals from
  SQL-bearing inputs; fold `||` chains, `concat(…)`, and `chr(N)` calls into
  the literal stream; lowercase; then re-apply the shared contamination
  needles and layer-(a) source classification to the folded strings. Catches
  concatenation/char-code obfuscation of both needles and paths.
- **(c) Composite-tool recursion.** `run_program` steps (`{"steps": [{tool,
  args}…]}`) are expanded and each step scanned as its inner tool;
  `subagent:`-prefixed tool names are normalized before classification.
- **(d) Existing text backstop, unchanged.** `detect_contamination` over
  artifact + full trace text stays as-is (needle list still the single source
  of truth in `maze/scent_audit.py`).

## Integration contract (unchanged callers)

`audit_run(trials_jsonl, scratch_dir) -> dict[str, str]` keeps its exact
signature, verdict values (`clean` / `external-oracle-cheating` /
`audit-error`), and `taint.json` output — `dab_shards.py`, `eval_dab.py`, and
`build_dab_trace_bundle.py` are untouched. New module
`eval/benchmarks/dab/taint_structural.py` exposes
`scan_records(records) -> list[TaintFinding]` (frozen dataclass: `layer`,
`tag`, `event_index`, `tool`, `detail`); `audit_run` folds any finding into
the trial verdict as `external-oracle-cheating`. Structured findings are
available to tooling via the module API (no new artifact file — `taint.json`
stays the gate output; the trace-bundle/audit docs remain the evidence trail).

## Out of scope

- The **human trace audit remains the final authority**; the gate is a
  high-recall automated backstop, not proof of cleanliness.
- No runtime sandboxing (tool-side path enforcement is a separate concern —
  the suite comment now states this honestly).
- No output-channel URL/secret policing (dataset content; the trace-bundle
  secret scan owns credentials at packaging time).
- No semantic "did the answer come from the data" checking (verification
  layer's job).

## Acceptance / regression gates

1. All evasion cases above are caught (TDD red-first against the current gate).
2. Benign lookalikes stay clean: URL-rich github_repos outputs, `har-validator`
   package names, URLs as SQL *filter values* (not file sources), absolute
   `query_dataset` attaches, relative/temp file loads, `subagent:*` tools.
3. **The real 270-trace package (`submission-gpt56-luna-max-ledger-final-270`)
   re-audits 270/270 clean, and the excluded v1 recovery dir's committed rows
   keep their prior verdicts** — zero new flags on shipped evidence.
4. `ruff format`/`check`, `pyright`, full `pytest tests/unit` clean.

## Plan

1. Spec (this doc) → commit.
2. Red-first tests in `tests/unit/test_dab_taint_structural.py` + integration
   cases in `test_dab_taint.py`.
3. Implement `taint_structural.py`; wire into `audit_run`.
4. Corpus regression run (read-only) over the final-270 package + v1 dir.
5. Gates, adversarial self-review, commit.
