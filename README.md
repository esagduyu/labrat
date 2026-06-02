# 🐀 LabRat

> Find the cheese in your maze.

LabRat is a terminal-native AI data agent. Connect to your warehouse, ask a question in plain English, and watch the agent explore your schema, write dialect-correct SQL in real time, and surface the answer — all without leaving your terminal.

> [!NOTE]
> Status: feature-complete v0 alpha. 542 tests passing. End-to-end demo operational against DuckDB. Evaluated on [ADE-bench](https://github.com/dbt-labs/ade-bench) (dbt Labs): **100% easy · 80% medium · 60% hard · 80% overall** (48/60 tasks, DuckDB+dbt, claude-sonnet-4-6, best-of-3) — **82% on the 39-task subset that overlaps with Altimate Code's published DuckDB results (vs. their 77%)**. Evaluated on the **full [DataAgentBench](https://ucbepic.github.io/DataAgentBench/) (UC Berkeley)** 54-query / 12-dataset benchmark: **58.0%** (pass@5, claude-sonnet-4-6, LabRat tools via MCP) — **above Spacedock (57.7%)** on the public leaderboard, behind Altimate Code (60.4%) and MinusX (63.1%). Strongest single-dataset signal: **crmarenapro 82%** on a 6-database hybrid query set. Full write-up: [docs/dab-progress-report.md](docs/dab-progress-report.md).

<!-- TODO: replace with a real screenshot or recorded demo -->
<!-- ![LabRat demo](docs/demo.gif) -->

---

## What makes LabRat different

- **The SQL editor is the agent's whiteboard.** Watch SQL stream into the editor character-by-character in your warehouse's dialect as the agent thinks. Edit it. Run it. The agent learns from your edits.
- **Learns from you.** Per [Meta's research](https://medium.com/@AnalyticsAtMeta/inside-metas-home-grown-ai-analytics-agent-4ea6779acfb3), 88% of data scientists' queries hit tables they've used before. LabRat captures your query history, infers your domain, and applies your past corrections automatically. Day-30 LabRat is meaningfully better than day-1 LabRat.
- **Audit-ready by default.** Every interaction is event-sourced and logged. Pin findings and export polished HTML reports with full provenance — query, results, chart, timestamps, lineage.
- **Safe by default.** Read-only roles enforced at connection. Mutations and multi-statement injection refused (sqlglot AST-checked). Queries gated by EXPLAIN-estimated cost. Spend tracked per session. Destructive mistakes are physically impossible.
- **Catalog-native.** Reads your dbt project's `schema.yml`, `manifest.json`, lineage, and tags. Connects to DataHub, OpenMetadata, or any MCP-compatible data catalog. Surfaces the canonical models in your warehouse, not just whatever the LLM guesses.
- **Mouse-native, keyboard-first.** Composes with your shell, your SSH sessions, your tmux setup. Every feature works without the mouse.

## Status

LabRat is feature-complete for v0 alpha. Below is the full capability inventory.

| Layer | Status | Details |
|---|---|---|
| 7 warehouse adapters | ✅ | DuckDB, Postgres, Snowflake, BigQuery, Redshift, Trino, MySQL |
| 3 LLM providers | ✅ | Anthropic API, Claude Code CLI (Mac OAuth), OpenAI-compatible |
| Agent tool loop | ✅ | one-call `profile_dataset` grounding, schema exploration, SQL execution, safety gates (mutation + statement-stacking refusal), multi-DB routing, `attach_database` for cross-DB JOINs, `load_file` (CSV/TSV/JSON/Parquet), opt-in LLM-as-judge verifier loop, configurable `max_turns` / `max_tool_calls` caps |
| MCP server | ✅ | `python -m labrat.mcp.server` mounts the LabRat tool registry over MCP stdio — drop into Claude Code, Codex, Cursor, OpenCode, or any MCP-supporting host. Reads connection spec from `LABRAT_MCP_CONNECTIONS` env var. |
| Query history | ✅ | always-on, PII-redacted JSONL per profile |
| Personal context engine | ✅ | table relevance scoring, LLM-generated descriptions |
| dbt catalog integration | ✅ | manifest.json + schema.yml + catalog.json + lineage |
| MCP catalog integration | ✅ | generic async client for any MCP-compatible catalog |
| Self-healing memory | ✅ | edit-derived + chat-correction memories, retrieval |
| Custom validations | ✅ | natural-language rules, warn/block severity |
| Eval framework | ✅ | [ADE-bench](https://github.com/dbt-labs/ade-bench) 60 tasks: **80% overall** (100% easy · 80% medium · 60% hard, DuckDB+dbt, Sonnet 4.6, best-of-3). [DataAgentBench](https://ucbepic.github.io/DataAgentBench/) Phase 1b: **48.5%** (17 queries, 5 DuckDB+SQLite datasets, pass@5, raw Claude + prompt engineering baseline). |
| 3-pane TUI | ✅ | chat + SQL whiteboard + schema browser |
| Charts | ✅ | unicode (plotext) + image protocol (matplotlib/kitty) |
| HTML export | ✅ | findings with full provenance |
| Audit log | ✅ | JSONL event sourcing |

**Test coverage:** 542 passing, 10 skipped (gated by `ANTHROPIC_API_KEY` / `LABRAT_RUN_LLM_TESTS`).

**[ADE-bench](https://github.com/dbt-labs/ade-bench) (dbt Labs, DuckDB+dbt, claude-sonnet-4-6, best-of-3, via `LabratLocalAgent`):**

| Tier | Tasks | Score |
|------|-------|-------|
| Easy | 15 | **100%** (15/15) |
| Medium | 30 | **80%** (24/30) |
| Hard | 15 | **60%** (9/15) |
| **Overall** | **60** | **80% (48/60)** |

On the 39 tasks shared with Altimate Code's published DuckDB results: **LabRat 82% (32/39) vs. Altimate 77% (30/39)**, same model (Sonnet 4.6), same best-of-3 methodology. Full write-up: [docs/ade-bench-progress-report.md](docs/ade-bench-progress-report.md).

**[DataAgentBench](https://ucbepic.github.io/DataAgentBench/) (UC Berkeley, multi-DB query answering, claude-sonnet-4-6):**

| Phase | Datasets | Tasks | Score | Notes |
|-------|----------|-------|-------|-------|
| Phase 1a baseline | 5 (DuckDB+SQLite) | 17 | **43%** | n_trials=1, raw-bash driver |
| Phase 1b | 5 (DuckDB+SQLite) | 17 | **48.5%** | pass@5, ATTACH preamble, raw-bash driver — raw-Claude floor |
| Phase 4 | 5 (DuckDB+SQLite) | 17 | **54.0%** | pass@5, claude-mcp driver. **+5.5pp over Phase 1b** = measured tool-layer value |
| **Phase 5 (full DAB)** | **12 (all official)** | **54** | **58.0%** | **pass@5, claude-mcp driver, full Phase 2+3 substrate. Above Spacedock (57.7%); behind Altimate 60.4% and MinusX 63.1%** |

**Phase 5 per-dataset highlights:** agnews **95%** and bookreview **93%** (Phase 2/3 substrate at scale); crmarenapro **82%** on 6 databases (substrate's strongest single signal); stockindex **100%**, stockmarket 80%; pancancer_atlas 67%; github_repos 50%, googlelocal 50%; deps_dev_v1 10% (stochastic regression from Phase 4); music_brainz_20k 7% and patents 0% (Sonnet ceiling, not infra). 25 of 54 queries scored a perfect 5/5. Three drivers coexist (`raw-bash` for baseline reproducibility, `labrat-agent` for multi-provider standalone use, `claude-mcp` for the Max-plan tool path). Full write-up: [docs/dab-progress-report.md](docs/dab-progress-report.md).

## Install

```bash
# Coming soon
uv tool install labrat
```

Until then, build from source:

```bash
git clone https://github.com/esagduyu/labrat
cd labrat
uv sync
uv run labrat
```

Requires Python 3.12+.

## Quickstart

```bash
labrat
```

On first run, an onboarding wizard walks you through:

1. Picking a database dialect (DuckDB / PostgreSQL / Snowflake / BigQuery / Redshift / Trino / MySQL)
2. Entering credentials (stored encrypted in OS keyring)
3. Testing the connection
4. Optionally linking a dbt project or data catalog

Then you're in. Ask a question:

```
> show me Q4 revenue by region
```

LabRat will:
- Explore your schema (tables, columns, relationships)
- Sample data to understand value distributions
- Consult your query history and any applicable memories
- Write dialect-correct SQL in the editor pane as it thinks
- Run the query (with safety gates)
- Render the results
- Offer to chart or pin the finding

Press `?` at any time for the in-app keyboard reference.

## Supported warehouses

DuckDB, PostgreSQL, Snowflake, BigQuery, Redshift, Trino/Presto, MySQL.

Pull requests for additional warehouses welcome — the `Connection` abstract base class makes new adapters straightforward.

## Supported LLM providers

- **Anthropic API** — direct SDK access; recommended when you have API credits
- **Claude Code CLI** — shells out to the local `claude` binary, authenticated via Mac OAuth (Claude Max subscription). Used by `LabratLocalAgent` for ADE-bench runs where the API key has no credits
- **OpenAI-compatible endpoints** — Azure OpenAI, LiteLLM gateways, vLLM, Together, Fireworks, Ollama for local

Configure per profile. The default model is `claude-sonnet-4-6`; switch in settings.

## Why "LabRat"?

We started flirting with [ratatui](https://github.com/ratatui/ratatui) and Rust. We landed on Python + [Textual](https://textual.textualize.io/) because the agent is the point of the product and Python's iteration speed on prompts and tools dominates. The name stuck. The rat got a lab coat.

## License

AGPL-3.0. You can use, modify, and redistribute LabRat for any purpose, including commercial. If you distribute a modified version, or run it as a service, you must release your modifications under the same license.

If you want LabRat under a more permissive license for proprietary use, get in touch.

## Acknowledgments

LabRat stands on shoulders:

- **[Textual](https://textual.textualize.io/)** by Will McGugan — the framework that made mouse-native, async-native TUIs a real option in Python
- **[rich-pyfiglet](https://github.com/edward-jazzhands/rich-pyfiglet)** for the placeholder banner that ships with v0
- **[Harlequin](https://harlequin.sh/)** by Ted Conbeer — proved a terminal SQL editor can feel professional and shipped real adapter abstractions
- **[DuckDB](https://duckdb.org/)** — the universal SQL engine that makes "just connect to anything" actually work
- **[SQLGlot](https://sqlglot.com/)** by Toby Mao — the dialect handling we could never have built ourselves
- **[Polars](https://pola.rs/)** — fast Arrow-backed DataFrames
- **[uv](https://docs.astral.sh/uv/)** by Astral — finally, a Python package manager that doesn't make you sigh
- **[dbt Labs](https://github.com/dbt-labs/ade-bench)** ADE-bench team — the most serious public benchmark for data-engineering agents, Docker-sandboxed, execution-based, no LLM judges
- **[Meta's Analytics at Meta team](https://medium.com/@AnalyticsAtMeta)** — their writeup of the home-grown analytics agent is the architectural foundation for LabRat's personal context layer
- **The folks at [SignalPilot](https://signalpilot.ai/) and [Databao](https://databao.app/)** — competitors in adjacent categories. They've validated the space and set a high bar. We watch their work closely.

## Contributing

Issues, discussions, and PRs welcome. See `CONTRIBUTING.md` (coming soon) for details. Until then, the simplest contribution is to use LabRat, hit a wall, and open an issue describing what broke.

## Development

```bash
# Run the full test suite
uv run pytest

# Lint and type-check
uv run ruff check . && uv run ruff format --check . && uv run pyright

# Evaluate against the sample DuckDB
# Schema/SQL tests run with no auth; the agent NL→SQL step uses ANTHROPIC_API_KEY
# if set, otherwise falls back to the local `claude` CLI (Mac OAuth)
uv run python scripts/eval_duckdb.py

# ADE-bench evaluation (requires Docker + ade-bench repo at ~/repos/ade-bench)
# Uses local Claude Code via Mac OAuth — no API credits needed
cd ~/repos/ade-bench && uv run ade run helixops_saas001 airbnb001 --db duckdb --project-type dbt --agent labrat_local --no-diffs

# DataAgentBench evaluation (requires DataAgentBench repo at ~/repos/DataAgentBench)
# Phase 1b: pass@5 by default; --n-trials 1 for a quick single-trial run
uv run python scripts/eval_dab.py --datasets deps_dev_v1,github_repos,music_brainz_20k,stockindex,stockmarket
# Phase 4 measurement via the LabRat MCP server inside claude --print (Max-plan billing)
uv run python scripts/eval_dab.py --driver claude-mcp --n-trials 5
# Opt-in LLM-as-judge verifier loop on the labrat-agent driver (default off; extra LLM call/answer).
# --agent-timeout raises the claude-code per-call subprocess timeout to absorb the extra round-trips:
uv run python scripts/eval_dab.py --driver labrat-agent --agent-verify --agent-timeout 300
# Resume a crashed run:
uv run python scripts/eval_dab.py --output-dir runs/dab/dab-<id>

# Standalone LabRat agent against an arbitrary prompt (any provider, any DuckDB connection)
uv run python scripts/run_task.py \
    --prompt "How many rows in orders?" \
    --connections '{"main":{"db_type":"duckdb","db_path":"/path.duckdb"}}' \
    --provider anthropic --model claude-sonnet-4-6

# Mount the LabRat MCP server inside Claude Code / Codex / Cursor / OpenCode
LABRAT_MCP_CONNECTIONS='{"main":{"db_type":"duckdb","db_path":"/path.duckdb"}}' \
    uv run python -m labrat.mcp.server

# Generate UI screenshots (no API key needed)
uv run python scripts/take_screenshots.py
```

## Roadmap

v0 alpha is feature-complete. Post-v0 priorities:

- **ADE-bench improvements**: 80% overall — next target is `compare_schema` and `trace_column_lineage` tools (Tier 2) to close the remaining output-schema and dependency-discovery gaps
- **DataAgentBench Phase 6** (closing the gap to MinusX 63.1%): force-query prompt rule to recover music_brainz_20k (7% → likely ≥40% — the agent has tools and currently chooses not to use them); investigate patents 0% (Sonnet ceiling vs. prompt structure); raise pass@5 to pass@10 to tighten dataset-mean variance (deps_dev_v1 regressed from Phase 4's 40% to 10% — likely n=5 noise)
- **DAB harness ergonomics**: per-trial exception isolation now records provider failures as `infra:timeout`/`infra:agent_error` (so one error can't crash a run); remaining work is detecting session-limit text and sleep-until-reset rather than fast-failing the rest of the queue (today's runs still need a few manual `--output-dir` resume cycles for a 270-trial sweep)
- **testcontainers integration tests**: full Postgres/MySQL/Trino adapters against live containers
- **v1 GA**: dogfooded for one week, P0 bug-free, README demo gif

---

*A small rat in a big maze. Finding cheese, one query at a time.*
