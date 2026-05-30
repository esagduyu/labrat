# DAB Phase 1a Results (2026-05-29)

## Score

- **Overall: 43.3%** (stratified: mean of dataset means)
- Tasks: 17 queries across 5 DuckDB+SQLite datasets
- Trials: 1 trial per query (n_trials=1)
- Passes: 9/17

## Scores by Dataset

| Dataset | Score | Queries |
|---------|-------|---------|
| stockmarket | 100% (5/5) | 5 queries |
| github_repos | 50% (2/4) | 4 queries |
| music_brainz_20k | 33% (1/3) | 3 queries |
| stockindex | 33% (1/3) | 3 queries |
| deps_dev_v1 | 0% (0/2) | 2 queries |

## Failure Analysis

### deps_dev_v1 (0/2) — Cross-DB join limitation

Both queries require joining the SQLite `package_database` with the DuckDB `project_database`. The agent sees separate connection examples for each DB but doesn't know how to join them in a single query. In Phase 1b, DuckDB ATTACH will federate the SQLite DB and enable cross-DB joins.

- `deps_dev_v1:1`: Missing name: @dmrvos/infrajs>0.0.6>typescript
- `deps_dev_v1:2`: Missing project name: mui-org/material-ui

### github_repos (2/4) — Stochastic accuracy

- `github_repos:1`: Output "0.33" not found (numeric precision / rounding format)
- `github_repos:2`: "swiftandroid/swift" not found within 3-char edit distance (agent found "swiftlang/swift" instead — plausible but wrong)
- `github_repos:3`: PASS
- `github_repos:4`: PASS

### music_brainz_20k (1/3) — SQLite accuracy

- `music_brainz_20k:1`: Agent computed $601.44, expected $1059.46. Likely incorrect filter or currency conversion in the SQLite query.
- `music_brainz_20k:3`: Agent found "Systemisch bled" (Stüngö), expected "Zo gaat het leven aan je voor". Wrong aggregation.
- `music_brainz_20k:2`: PASS

### stockindex (1/3) — Answer format + missing indices

- `stockindex:2`: IXIC answer buried in prose, not stated as primary answer in first 200 chars. Prompt engineering opportunity.
- `stockindex:3`: NSEI not found — agent missed the India NSE index (possibly not in data or requires different query).
- `stockindex:1`: PASS

## Agent Architecture

DAB Phase 1a uses `claude --print --disable-slash-commands --dangerously-skip-permissions --max-turns 15` with the model having Bash access to run Python+DuckDB/SQLite queries directly. This avoids `ClaudeCodeProvider` text-protocol conflicts and superpowers plugin overhead (~10s per invocation).

System prompt: "You are a data analyst. Query the databases using Python+DuckDB/SQLite via Bash. Return your final answer as plain text once you are confident."

Each `run_trial` call injects a `db_preamble` with exact Python snippets showing how to open each database by name, then appends the description + question.

## Phase 1b Priorities

Based on failures:
1. **DuckDB ATTACH for cross-DB federation** — fixes all `deps_dev_v1` failures (adds ~2 pts to overall)
2. **pass@5 + best-of-k scoring** — reduces stochastic variance on github_repos, stockindex
3. **Prompt iteration for SQLite accuracy** — music_brainz_20k joins/aggregations need tighter schema guidance
4. **Answer format guidance** — stockindex:2 pattern (answer should be stated first, not buried)
