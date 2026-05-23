# LabRat

> Find the cheese in your maze.

LabRat is a terminal-native AI data agent. Connect to your warehouse, ask a question in plain English, and watch the agent explore your schema, write dialect-correct SQL in real time, and surface the answer — all without leaving your terminal.

> [!NOTE]
> Status: alpha. Active development. The splash banner currently renders via `rich-pyfiglet` as a placeholder; custom art coming.

<!-- TODO: replace with a real screenshot or recorded demo -->
<!-- ![LabRat demo](docs/demo.gif) -->

---

## What makes LabRat different

- **The SQL editor is the agent's whiteboard.** Watch SQL stream into the editor character-by-character in your warehouse's dialect as the agent thinks. Edit it. Run it. The agent learns from your edits.
- **Learns from you.** Per [Meta's research](https://medium.com/@AnalyticsAtMeta/inside-metas-home-grown-ai-analytics-agent-4ea6779acfb3), 88% of data scientists' queries hit tables they've used before. LabRat captures your query history, infers your domain, and applies your past corrections automatically. Day-30 LabRat is meaningfully better than day-1 LabRat.
- **Audit-ready by default.** Every interaction is event-sourced and logged. Pin findings and export polished HTML reports with full provenance — query, results, chart, timestamps, lineage.
- **Safe by default.** Read-only roles enforced at connection. Mutations refused. Queries gated by EXPLAIN-estimated cost. Spend tracked per session. Destructive mistakes are physically impossible.
- **Catalog-native.** Reads your dbt project's `schema.yml`, `manifest.json`, lineage, and tags. Connects to DataHub, OpenMetadata, or any MCP-compatible data catalog. Surfaces the canonical models in your warehouse, not just whatever the LLM guesses.
- **Mouse-native, keyboard-first.** Composes with your shell, your SSH sessions, your tmux setup. Every feature works without the mouse.

## Status

LabRat is being built in public, milestone by milestone. The build plan is detailed and the agent loop is the heart of the product. Stars and feedback welcome.

## Install

```bash
# Coming soon
uv tool install labrat
```

Until then, build from source:

```bash
git clone https://github.com/{username}/labrat
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

- **Anthropic Claude** (default, recommended) — best tool use, native streaming
- **OpenAI-compatible endpoints** — Azure OpenAI, LiteLLM gateways, vLLM, Together, Fireworks, Ollama for local
- **AWS Bedrock** — IAM-authenticated, for AWS shops
- **Google Vertex AI** — for GCP shops

Configure per profile. The default is `claude-sonnet-4-6`; switch in settings.

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
- **[Spider 2.0](https://spider2-sql.github.io/)** team at XLang AI — the benchmark that defines the bar for enterprise text-to-SQL
- **[Meta's Analytics at Meta team](https://medium.com/@AnalyticsAtMeta)** — their writeup of the home-grown analytics agent is the architectural foundation for LabRat's personal context layer
- **The folks at [SignalPilot](https://signalpilot.ai/) and [Databao](https://databao.app/)** — competitors in adjacent categories. They've validated the space and set a high bar. We watch their work closely.

## Contributing

Issues, discussions, and PRs welcome. See `CONTRIBUTING.md` (coming soon) for details. Until then, the simplest contribution is to use LabRat, hit a wall, and open an issue describing what broke.

## Roadmap

See the project tracker. The biggest near-term items:

- v0 alpha: all milestones complete, end-to-end demo working
- v0 beta: catalog integration (M30) and memory loop (M31) shipped
- v1 GA: full benchmark suite passing, all warehouse adapters, dogfooded

---

*A small rat in a big maze. Finding cheese, one query at a time.*
