# 🐀 LabRat

> Find the cheese in your maze.

**LabRat is a terminal-native AI data agent — and the start of "Claude Code for data scientists."** Connect to your warehouse, ask a question in plain English, and watch the agent explore your schema, write dialect-correct SQL in real time, and surface the answer — all without leaving your terminal.

> [!NOTE]
> **Status: feature-complete v0 alpha.** 577 tests passing, end-to-end against DuckDB. On the public [DataAgentBench](https://ucbepic.github.io/DataAgentBench/) leaderboard at **51.4%** (rank #10/18) and **80%** on dbt Labs' [ADE-bench](https://github.com/dbt-labs/ade-bench) — see [Benchmark records](#benchmark-records).

<!-- TODO: replace with a real screenshot or recorded demo -->
<!-- ![LabRat demo](docs/demo.gif) -->

---

## Where this is going

The DuckDB demo and the benchmark scores are proof-of-life for the reasoning engine. The product they serve is bigger: **an agent _and_ a TUI that capture end-to-end data workflows the way Claude Code captures coding workflows.** Three ideas drive the design.

**The Rat, the Maze, and the Cheese.** The vocabulary is the product, the way Claude Code has its own.
- **The Rat** is the agent kernel — the reasoning loop, tools, providers, and warehouse adapters. It finds the cheese.
- **The Cheese** is the deliverable — an answer that carries its own SQL and reasoning, reviewable like a colleague's work, saveable and shareable.
- **The Rat Maze** is an *optional, additive* knowledge layer the colony builds over time: metric definitions, naming, known gotchas, and reusable analysis recipes distilled from real usage. A mapped maze means the next rat — or the next teammate — never cold-starts. ([Anthropic's own data team](https://claude.com/blog/how-anthropic-enables-self-service-data-analytics-with-claude) measured <21% accuracy without this kind of reference layer, >95% with it.)

**A Lego-modular, embeddable core.** Everything is a self-contained library that composes. The **Rat Core** (agent loop + tools + adapters + providers) is harness-agnostic — it runs in LabRat's TUI *or* embedded in your own harness via the [MCP server](#architecture). The TUI and the Rat Maze are optional layers that snap on top; the bare-bones product (Core + TUI) must be excellent on its own, with zero knowledge layer required.

**Bottoms-up, like Figma.** An individual data scientist adopts the Core + TUI on their laptop against their own warehouse — no procurement, no platform commitment. Teams adopt the shared Rat Maze later, once the value is already proven.

The three pillars, in order: **(1) find the cheese reliably → (2) spread the cheese → (3) map the maze.** Pillar 1 is what the benchmarks measure and it's largely built; Pillars 2 and 3 are the road ahead (see [Roadmap](#roadmap)).

## What makes LabRat different

- **The SQL editor is the agent's whiteboard.** Watch SQL stream into the editor character-by-character in your warehouse's dialect as the agent thinks. Edit it. Run it. The agent learns from your edits.
- **Learns from you.** Per [Meta's research](https://medium.com/@AnalyticsAtMeta/inside-metas-home-grown-ai-analytics-agent-4ea6779acfb3), 88% of data scientists' queries hit tables they've used before. LabRat captures your query history, infers your domain, and applies your past corrections automatically. Day-30 LabRat is meaningfully better than day-1 LabRat.
- **Grounded before it guesses.** A one-call `profile_dataset` reads the real schema, row counts, and sample values before planning; `link_schema` narrows wide schemas to the relevant tables; `verify_join` mechanically probes a join's match-rate and fan-out *before* the agent trusts it. An opt-in LLM-as-judge verifier gates the final answer.
- **Audit-ready by default.** Every interaction is event-sourced and logged. Pin findings and export polished HTML reports with full provenance — query, results, chart, timestamps, lineage.
- **Safe by default.** Read-only roles enforced at connection. Mutations and multi-statement injection refused (sqlglot AST-checked). Queries gated by EXPLAIN-estimated cost. Spend tracked per session. Destructive mistakes are physically impossible.
- **Embeddable, not walled.** The Rat Core mounts over MCP into Claude Code, Codex, Cursor, OpenCode, or any MCP host — fully open (AGPL-3.0), with swappable LLM backends. You ride the tools you already use instead of adopting a new app.
- **Benchmarked, and honest about it.** Real public scores, with the methodology and a self-disclosed contamination story documented in full ([below](#benchmark-records)).

## Benchmark records

LabRat is measured on the two most serious public **agentic** data benchmarks — both execution-based (real databases, real validators, no LLM judges). All runs use `claude-sonnet-4-6` unless noted.

### DataAgentBench — UC Berkeley EPIC ([leaderboard](https://ucbepic.github.io/DataAgentBench/))

Natural-language query answering across 12 datasets / 54 queries / 4 database systems (DuckDB, SQLite, PostgreSQL, MongoDB), many requiring cross-database joins. **LabRat is on the public leaderboard at a stratified Pass@1 of 51.4% (rank #10 of 18)**, independently re-validated by the maintainers.

| Phase | Scope | Score | What it measures |
|-------|-------|-------|------------------|
| 1b | 5 datasets · 17 queries | **48.5%** | raw-Claude + prompt-engineering floor (no LabRat tools) |
| 4 | 5 datasets · 17 queries | **54.0%** | same subset *with* LabRat's tool layer — a **+5.5pp** measured tool-layer lift |
| 5 (full) | 12 datasets · 54 queries | **51.4%** | full benchmark, on the leaderboard |

Strongest single signal: **crmarenapro 82%** on a 6-database hybrid query set. The headline number has an honest history: our initial submission scored 58.0%, but we found and **self-disclosed** a harness flaw that let the agent read benchmark answer-key files; the maintainers independently re-validated all 270 answers and added LabRat at the corrected **51.4%**. The sandbox that prevents this is now permanent. The full story — leak, disclosure, re-validation, sandbox gate, and per-dataset breakdown — is in **[docs/dab-progress-report.md](docs/dab-progress-report.md)**.

### ADE-bench — dbt Labs ([repo](https://github.com/dbt-labs/ade-bench))

Analytics-engineering tasks in Docker-sandboxed dbt+DuckDB projects. **80% overall (48/60).**

| Tier | Tasks | Score |
|------|-------|-------|
| Easy | 15 | **100%** (15/15) |
| Medium | 30 | **80%** (24/30) |
| Hard | 15 | **60%** (9/15) |
| **Overall** | **60** | **80% (48/60)** |

On the 39 tasks shared with Altimate Code's published DuckDB results: **LabRat 82% (32/39) vs. Altimate 77% (30/39)**, same model, same best-of-3 methodology. Full write-up and remaining-failure analysis: **[docs/ade-bench-progress-report.md](docs/ade-bench-progress-report.md)**.

## Status — what's built

LabRat is feature-complete for v0 alpha.

| Layer | Status | Details |
|---|---|---|
| 7 warehouse adapters | ✅ | DuckDB, Postgres, Snowflake, BigQuery, Redshift, Trino, MySQL |
| 4 LLM providers | ✅ | Anthropic API · Claude Code CLI (Mac OAuth, Max plan) · OpenAI-compatible · GPT-5.5 via ChatGPT subscription (Codex Responses API — personal/dev path) |
| Agent tool loop | ✅ | 18 tools: `profile_dataset` grounding, `link_schema` / `verify_join`, schema exploration, SQL execution, safety gates (mutation + statement-stacking refusal), multi-DB routing, `attach_database` (cross-DB JOINs), `load_file` (CSV/TSV/JSON/Parquet), `load_mongo_collection`, opt-in LLM-as-judge verifier, configurable turn/tool-call caps |
| MCP server | ✅ | `python -m labrat.mcp.server` mounts the tool registry over MCP stdio — drop into Claude Code, Codex, Cursor, OpenCode, or any MCP host |
| Query history | ✅ | always-on, PII-redacted JSONL per profile |
| Personal context engine | ✅ | table relevance scoring (frequency × recency), LLM-generated descriptions |
| dbt catalog integration | ✅ | manifest.json + schema.yml + catalog.json + lineage |
| MCP catalog integration | ✅ | generic async client for any MCP-compatible catalog |
| Self-healing memory | ✅ | edit-derived + chat-correction memories, retrieval |
| Custom validations | ✅ | natural-language rules, warn/block severity |
| Benchmark harness | ✅ | unified suite protocol; ADE-bench + DataAgentBench integrations |
| 3-pane TUI | ✅ | chat + SQL whiteboard + schema browser |
| Charts · HTML export · Audit log | ✅ | unicode + image-protocol charts; provenance-rich HTML findings; JSONL event sourcing |

**Test coverage:** 577 tests (LLM-gated tests skipped without `ANTHROPIC_API_KEY` / `LABRAT_RUN_LLM_TESTS`).

## Architecture

LabRat is built as composable layers — the kernel works without the layers above it.

- **Rat Core** (`src/labrat/agent/`, `src/labrat/db/`) — `AgentLoop` drives tool-use round-trips against a `ToolRegistry` and a swappable `ModelProvider`. Tools subclass a common `Tool[InputT]` base with Pydantic-validated inputs. Warehouse adapters share a `Connection` ABC and return Polars DataFrames. `run_agent_task()` turns a one-shot prompt into a result in-process.
- **MCP server** (`src/labrat/mcp/`) — exposes the same tool registry over MCP stdio, so the Core runs inside any MCP host. This is the embeddable seam: the DataAgentBench `claude-mcp` driver is living proof of LabRat's tools running inside a third-party harness today.
- **TUI** (`src/labrat/screens/`, `src/labrat/widgets/`) — Textual 3-pane layout: chat, a streaming SQL editor (the agent's whiteboard), and a schema browser.
- **Knowledge subsystems** (`memory/`, `validations/`, `context_engine/`, `catalog/`, `history/`, `audit/`) — the seeds of the Rat Maze: self-healing memory, NL validations, personal table-relevance scoring, dbt/MCP catalog loaders, query history, event sourcing.

Contributor-facing detail lives in [CLAUDE.md](CLAUDE.md); architectural decisions in [decisions.md](decisions.md).

## Install

```bash
# Coming soon
uv tool install labrat
```

Until then, build from source (requires Python 3.12+):

```bash
git clone https://github.com/esagduyu/labrat
cd labrat
uv sync
uv run labrat
```

## Quickstart

```bash
labrat
```

On first run, an onboarding wizard walks you through picking a dialect, entering credentials (stored encrypted in the OS keyring), testing the connection, and optionally linking a dbt project or data catalog. Then ask a question:

```
> show me Q4 revenue by region
```

LabRat explores your schema, samples data, consults your history and memories, streams dialect-correct SQL into the editor, runs it behind safety gates, renders the results, and offers to chart or pin the finding. Press `?` for the in-app keyboard reference.

**Supported warehouses:** DuckDB, PostgreSQL, Snowflake, BigQuery, Redshift, Trino/Presto, MySQL. New adapters are straightforward via the `Connection` base class — PRs welcome.

**Supported providers:** Anthropic API · Claude Code CLI (Mac OAuth / Max plan) · OpenAI-compatible (Azure, LiteLLM, vLLM, Together, Fireworks, Ollama) · GPT-5.5 via ChatGPT subscription. Configure per profile; default model `claude-sonnet-4-6`.

## Roadmap

v0 alpha is feature-complete. The path forward follows the three pillars.

**Pillar 1 — find the cheese reliably (within-task reasoning).** Largely shipped: grounding profiler, schema-linking, mechanically-verified joins, the verifier loop. In flight:
- **GPT-5.5 and cross-model measurement** — a native provider now runs LabRat's full loop (verifier included) on GPT-5.5; we're measuring the verifier's contribution and benchmarking GPT-5.5 against the Sonnet baseline.
- **Closing the DAB gap to the leaders** (Altimate 71.7%, Spacedock 67.2%, MinusX 65.2%): force-query prompting to recover the answer-from-memory failures (music_brainz), the patents ceiling, and a self-improving tool-iteration loop that adds and ablates one tool at a time against a held-out subset.
- **ADE-bench:** `compare_schema` and `trace_column_lineage` tools to close the output-schema and dependency gaps.

**Pillar 2 — spread the cheese (the workflow product).** The **Cheese** share artifact: every answer carries its SQL + reasoning, saveable and exportable as a reviewable unit; a provenance footer on every result; notebook (marimo) integration.

**Pillar 3 — map the Rat Maze (the knowledge moat).** Promote the existing `memory/` + `validations/` + `context_engine/` + `catalog/` seeds into a scoped, optional knowledge layer — reference docs written for LLM retrieval, reusable analysis recipes, a correction-harvesting loop that keeps the maze fresh, scoped per-user and per-team. This is the [21%→95% lever](https://claude.com/blog/how-anthropic-enables-self-service-data-analytics-with-claude) and the long-term differentiator.

**Foundations:** extract `labrat-core` as an installable embeddable package; testcontainers integration tests for the live warehouse adapters; v1 GA after a week of dogfooding.

## Why "LabRat"?

We started flirting with [ratatui](https://github.com/ratatui/ratatui) and Rust. We landed on Python + [Textual](https://textual.textualize.io/) because the agent is the point of the product and Python's iteration speed on prompts and tools dominates. The name stuck. The rat got a lab coat.

## License

AGPL-3.0. Use, modify, and redistribute LabRat for any purpose, including commercial. If you distribute a modified version or run it as a service, you must release your modifications under the same license. Want LabRat under a more permissive license for proprietary use? Get in touch.

## Development

```bash
uv run pytest                        # full test suite (577 tests)
uv run ruff check . && uv run ruff format --check . && uv run pyright   # lint + types

# Evals (see CLAUDE.md for the full matrix)
uv run python scripts/eval_duckdb.py                          # schema/SQL eval, no API key needed
uv run python scripts/eval_dab.py --driver claude-mcp --n-trials 5   # DataAgentBench (LabRat tools via MCP)
cd ~/repos/ade-bench && uv run ade run helixops_saas001 --db duckdb --project-type dbt --agent labrat_local --no-diffs

# Run the standalone agent on any prompt / any provider
uv run python scripts/run_task.py --prompt "How many rows in orders?" \
    --connections '{"main":{"db_type":"duckdb","db_path":"/path.duckdb"}}' --provider anthropic

# Mount the Rat Core over MCP in any host
LABRAT_MCP_CONNECTIONS='{"main":{"db_type":"duckdb","db_path":"/path.duckdb"}}' \
    uv run python -m labrat.mcp.server
```

## Acknowledgments

LabRat stands on shoulders: [Textual](https://textual.textualize.io/) (mouse-native async TUIs in Python), [Harlequin](https://harlequin.sh/) (a terminal SQL editor that feels professional), [DuckDB](https://duckdb.org/), [SQLGlot](https://sqlglot.com/), [Polars](https://pola.rs/), and [uv](https://docs.astral.sh/uv/). The [dbt Labs ADE-bench](https://github.com/dbt-labs/ade-bench) and [UC Berkeley EPIC DataAgentBench](https://ucbepic.github.io/DataAgentBench/) teams build the kind of execution-based benchmarks that actually mean something. [Meta's Analytics team](https://medium.com/@AnalyticsAtMeta) and [Anthropic's self-service-analytics writeup](https://claude.com/blog/how-anthropic-enables-self-service-data-analytics-with-claude) are the architectural foundation for the Rat Maze. And the folks at [SignalPilot](https://signalpilot.ai/), [Databao](https://databao.app/), and Altimate Code set a high bar in adjacent categories — we watch their work closely.

## Contributing

Issues, discussions, and PRs welcome. The simplest contribution: use LabRat, hit a wall, and open an issue describing what broke.

---

*A small rat in a big maze. Finding cheese, one query at a time.*
