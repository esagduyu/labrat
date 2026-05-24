# LabRat — Competitive Intelligence

> Research conducted 2026-05-24. Sources: direct GitHub repo analysis (Databao, SignalPilot), arxiv
> papers, engineering blogs. See section 5 for full source list.

---

## TL;DR

The top two Spider2-DBT agents score **60%** and **51%** vs. our **8%**. Neither uses DSPy or any
optimizer. The gap is entirely architectural:

1. **Context**: They parse `manifest.json`/`catalog.json` into a semantic index and inject only
   relevant models on demand. We dump raw schema.yml as a flat string.
2. **Agent loop**: They run a proper ReAct loop with 5–32 purpose-built tools. We make a single
   prompt call.
3. **Verification**: Both run a validation pass after building — comparing schemas, row counts, and
   sampled values against a pre-captured reference snapshot.
4. **Discipline**: Narrow tool sets with hard guards (can't overwrite existing files, can't run
   `ATTACH`, can't background `dbt run`). The constraints prevent the most common failure modes.

Our MIPROv2 autoresearch loop was optimizing the wrong variable — prompt wording — when the real
gap is agent architecture and context engineering.

---

## 1. Databao (JetBrains) — Spider2-DBT #1, 60.29%

**Repos**: `github.com/JetBrains/databao-agent` · `github.com/JetBrains/databao-context-engine`
**Stack**: LangGraph + LangChain, Jinja2 prompts, GPT-5 (probable for benchmark), Apache 2.0

### 1.1 Agent Architecture

A two-node LangGraph ReAct loop — nothing exotic:

```
START → llm_node ⇄ tool_executor → END
```

The `should_continue` edge is purely: does the last message have tool calls? No separate planning
phase, no tree-of-thought, no critic agent. Straightforward Reason+Act with a 50-turn recursion
limit (soft: the prompt budgets 25 tool calls before submission).

### 1.2 The Nine Tools (Deliberately Narrow)

| Tool | Purpose |
|------|---------|
| `run_sql(sql, sample_rows=5)` | Query DuckDB; returns schema + row_count + sample |
| `run_dbt(project_dir, timeout)` | Runs `dbt run`; returns returncode + log tail |
| `dbt_deps(project_dir)` | Runs `dbt deps` |
| `read_tool(path)` | Read file (truncated at 20k chars) |
| `write_tool(path, content)` | Write NEW files only — **blocked if file already exists** |
| `edit_tool(path, original, replacement)` | Regex-replace in existing files |
| `grep_tool(table_name)` | Project-wide grep, capped at 500 matches |
| `submit_answer(sql, description)` | Executes final SQL, marks completion |
| `search_context(query, datasource, type)` | Vector+keyword search over DCE semantic index |

They removed "general scope tools" including unrestricted terminal access. The `write_tool` guard
(`if path in pre_existing_files: return ERROR`) is load-bearing — it prevents the agent from
overwriting the existing project models. The `ATTACH` guard in `run_sql` prevents DB corruption.

A `dbt_dirty` flag skips redundant `dbt run` calls when nothing changed since the last build.

### 1.3 Context Engine (The Critical Piece)

`databao-context-engine` parses `manifest.json` (always required) and `catalog.json` (optional)
into a typed `DbtContext` object:

- All dbt models with descriptions, column definitions, constraints, tests
- Semantic models, metrics, dimensions from the dbt Semantic Layer
- Column types enriched from `catalog.json` (actual warehouse types, not just schema.yml)

This is indexed into a **hybrid vector + keyword search** (`HYBRID_SEARCH` / `KEYWORD_SEARCH` /
`VECTOR_SEARCH` modes). Retrieval uses **Reciprocal Rank Fusion (RRF, k=60)** with optional LLM
**query expansion** (3 diverse retrieval queries adapted to dbt naming conventions → merged via RRF).

In the system prompt, the agent gets a **compact file tree** (filename + size, excluding `target/`,
`dbt_packages/`, `logs/`). It reads specific files on demand via `read_tool`. No dump of full
project content upfront.

### 1.4 Prompt Engineering

Hand-written Jinja2 templates — no DSPy, no MIPROv2. They rewrote their prompts from scratch,
explicitly avoiding the "prompt onion" problem (overlapping, conflicting rules). The system prompt
(`system_prompt.jinja`) has numbered rules, hard invariants marked `MOST IMPORTANT`, and a
separate `task_instruction.jinja` prepended as a `HumanMessage` with a numbered workflow checklist:
Discover → Fix → Answer → Capture → Submit → Validate.

Hard rules baked in:
- Never run `ATTACH` in SQL
- Never modify pre-existing tables in the database
- Never declare success without `run_dbt` returning 0 errors
- Always create YAML documentation alongside new SQL model files
- Prompt caching enabled for Anthropic (cache breakpoint on system message)

### 1.5 Product Angle

- Python SDK on PyPI + MCP server + forthcoming team SaaS
- Model-agnostic: OpenAI, Anthropic, Ollama, vLLM, Google, Groq
- 9 databases: Postgres, MySQL, SQLite, DuckDB, BigQuery, Snowflake, ClickHouse, MSSQL, Athena
- Target: analytics leads and data engineers, not business users

---

## 2. SignalPilot — Spider2-DBT #2, 51.56%

**Repo**: `github.com/SignalPilot-Labs/SignalPilot` (Apache 2.0, 463 stars)
**Stack**: Claude Sonnet 4.6 (main + verifier), Claude Opus 4.6 (value verify), Claude Code SDK,
MCP tools, Docker sandboxing

### 2.1 Two-Agent Architecture

**Main agent** (Claude Sonnet 4.6, up to 200 turns):
- Extended thinking: `budget_tokens: 20_000` per turn
- Skills pre-installed: `dbt-workflow`, `dbt-debugging`, `duckdb-sql`, `notion-context`
- MCP server via stdio transport

**Verifier subagent** (Claude Sonnet 4.6, max 80 turns):
- Discovers required models from YML/SQL globs independently — does NOT trust main agent
- Runs the 7-check protocol (see §2.3)

**Specialist fix agents** (triggered conditionally):
- `run_quick_fix_agent` — after failed dbt run (max 200 turns)
- `run_value_verify_agent` — Claude Opus 4.6 for deep value spot-checks
- `run_name_fix_agent` — fixes table name aliasing issues

### 2.2 The Reference Snapshot (Key Innovation)

Before the main agent runs, the system queries the task's DuckDB to capture for each stub model:
- Row count
- Column names and types
- 3 sample rows

Written to `reference_snapshot.md` in the working directory. Both the main agent and the verifier
use this as ground truth. Without it, neither agent has a baseline to verify against.

### 2.3 The 7-Check Verifier Protocol

1. **Model Existence** — discovers required models independently from YML; builds missing ones
   (up to 3 retries)
2. **Column Schema** — diffs against `reference_snapshot.md`; checks column TYPES via
   `information_schema.columns` — type mismatches (VARCHAR vs INTEGER) cause eval failure
3. **Row Count** — compares against snapshot; diffs with `EXCEPT` on mismatch; triggers JOIN
   analysis for fan-outs or over-restriction
4. **Fan-Out Detection** — groups by join key when row count >> expected
5. **Cardinality Audit** — `audit_model_sources` MCP call: fan-out, over-filter, constant
   columns, NULL columns across all output
6. **Value Spot-Check** (CRITICAL) — takes first sample row's unique key from snapshot, queries
   `SELECT * FROM <model> WHERE <key> = '<val>'`, compares every column against snapshot
7. **Table Names** — verifies exact materialized names (dbt aliases can differ from model names)

### 2.4 libfaketime for Date Determinism

dbt models using `current_date`/`current_timestamp` produce different outputs depending on when
`dbt run` executes. SignalPilot reverse-engineered each gold database's build date by scanning
calendar spines, age columns, and date boundaries in the gold DB. Stored in `gold_build_dates.json`,
applied per-task via `libfaketime` wrapping the `dbt` binary.

This is a correctness issue most competitors missed: a gold DB built on 2024-03-15 will fail if
your agent runs on 2026-05-24.

### 2.5 Skill-Based Step-Gating

The `dbt-workflow` skill enforces a 6-step protocol:
1. Call `dbt_project_map` → load skill
2. Call `dbt_project_validate`, fix parse errors
3. Per-model: focus-map for column contract, check `reference_snapshot.md`, estimate grain,
   read sibling SQL
4. Load `dbt-write` skill → write SQL → `dbt run --select <model>`
5. Verify DB queryable → spawn verifier subagent
6. Optional traceability report

The `dbt-write` skill mandates: column alias names must match YML exactly (case-sensitive);
read a sibling model before writing; default to LEFT JOIN + `COALESCE(col, 0)` for metrics.

### 2.6 Product Angle

- SaaS + self-hosted (`docker compose up -d`, 60-second setup)
- Entrypoint: Claude Code (skills require Claude Code; other MCP clients get 32 tools but not skills)
- 7-layer governance gateway (DDL blocking, PII redaction, audit logs)
- 11 database connectors
- **AutoFyn** (`github.com/SignalPilot-Labs/AutoFyn`): separate meta-optimization tool —
  runs Claude in round-loop Docker containers to iteratively improve agent architectures against
  measurable goals. Each round gets a clean context window; cross-round knowledge in `run_state.md`.
  Used to build and optimize the benchmark submission itself.

---

## 3. Context Engineering — State of the Art (2026)

### 3.1 Best Schema Serialization Format: M-Schema

The research converges on **M-Schema** (from XiYan-SQL, BIRD leaderboard #1 at 75.63% EX):

```
[DB_ID] database_name

# Table model_name
(column_name, data_type, description, is_primary_key, [sample_val_1, sample_val_2])
...

[Foreign Keys]
table_a.col → table_b.col
```

M-Schema outperforms raw DDL (`CREATE TABLE`) by an average of **+2.03% EX** across multiple LLMs.
The key additions over DDL: column descriptions, per-column sample values (2-5 for categoricals),
explicit PK/FK markup.

**Information density sweet spot**: name + type + description (1-2 sentences) + 2-5 sample values
for categorical columns. The survey (arxiv 2406.08426v4) explicitly warns: "including more database
content can harm overall accuracy." Column-level statistics (cardinality, histograms) are not proven
to help and consume context budget. The Databricks guide shows `SELECT * LIMIT 3` sample rows
alone lift a 60.9% baseline to 67%.

### 3.2 Graph vs. Vector DB vs. Flat Files

**The state of the art is hybrid — graph for structure, vectors for semantics.**

| Approach | Best For | Key Systems |
|----------|---------|-------------|
| **FK graph + BFS** | Finding join paths, sub-5-minute setup, 5-30 table projects | SchemaGraphSQL (arxiv 2505.18363): zero-shot, training-free SOTA on BIRD |
| **Column vector embeddings** | Disambiguating semantically similar columns, large schemas | LitE-SQL (arxiv 2510.09014), RASL (Amazon Science) |
| **Hybrid (graph + vector)** | Production systems at scale | AutoLink (AAAI 2026): 97.4% recall, 87.7% token reduction |
| **Full context dump** | ≤50 columns with a frontier model (Gemini 1.5 Pro) | "Death of Schema Linking?" (arxiv 2408.07702) — loses 4-5% with filtering |

**Practical recommendation for LabRat (5-30 dbt models per project)**:

1. Build an **in-memory networkx graph** from FK/ref relationships extracted from `catalog.json`
2. At query time: 3-layer entity resolution →
   (a) exact name match against model names,
   (b) business entity map (synonym glossary),
   (c) column name scan
3. **BFS from seed models through the ref graph** (depth 1-2)
4. Within candidate models, rank columns by semantic similarity (lightweight embedding model)
5. Serialize candidate models only as M-Schema → inject into prompt

SchemaGraphSQL (the simplest graph approach) achieves SOTA on BIRD with just: LLM extracts source
and target tables from the question → classical BFS through FK graph → join path returned.

For the Spider2-DBT scale, the nirmalya.net 3-layer entity resolver + BFS achieves 93% token
reduction, 100% recall, 93% precision on 35-table schemas with zero LLM calls during pruning.
The critical piece is Layer 2: an `ENTITY_MAP` that maps business terms to physical names
(`"revenue" → "net_revenue_usd"`). Without it, recall drops from 1.00 to 0.57.

### 3.3 The Planning Span (Thinkquel Pattern)

**Thinkquel** (arxiv 2510.00186, the only dedicated text-to-dbt model) forces a YAML planning
block before SQL generation:

```xml
<think>
source_tables:
  - stg_orders
  - dim_customers
columns_to_use:
  - stg_orders.order_id
  - stg_orders.customer_id
  - dim_customers.email
join_path: stg_orders → dim_customers via customer_id
</think>
<answer>
SELECT ...
</answer>
```

This externalizes schema grounding — the model commits to which tables and columns it will use
before writing SQL, reducing hallucination of nonexistent columns. Results: 92.2% execution
accuracy, 72.3% match on BIRD-dbt. The forced planning step is the critical innovation.

### 3.4 Evidence Injection (SEED Pattern)

**SEED** (arxiv 2506.07423) shows that **automatically generated evidence** can match human-curated
evidence from BIRD:
- **Synonym maps**: `F = female`, `status 1 = active`
- **Domain rules**: normal value ranges, units of measure
- **Value illustrations**: concrete examples of how a column is used

Human BIRD evidence boosts accuracy by +17.73%; SEED's auto-generated evidence matches this.
Evidence can be pre-generated from the model's compiled SQL + sample values and cached —
regenerate only when the catalog changes.

### 3.5 Semantic Layer Formats

| Format | Used By | Structure |
|--------|---------|-----------|
| **MetricFlow / dbt Semantic Layer** | dbt, Lightdash | `semantic_manifest.json`: metric definitions, time grains, dimensions |
| **dbt MCP server** | dbt Cloud, self-hosted | `get_model_details`, `get_model_parents`, `list_metrics`, `query_metrics` |
| **Wren AI MDL** | Wren AI | YAML → `mdl.json`: entities, metrics, relationships, calculated fields, cubes |
| **Cortex Analyst YAML** | Snowflake | Logical tables, synonyms, measures, dimensions |
| **LakehouseIQ Metric Views** | Databricks | YAML metrics learned from notebooks + dashboards + SQL history |

The dbt MCP server (GA at Coalesce 2025; PyPI: `dbt-mcp`) is the emerging standard interface
for AI agents. Exposes `manifest.json` / `catalog.json` / `semantic_manifest.json` as callable
tools. For LabRat, this is the right integration path.

### 3.6 Keeping Context Fresh as the Database Evolves

**Lightest-weight approach for a local-first tool** (our case):

1. On each `dbt run` completion: re-parse `catalog.json` + `manifest.json` (always regenerated)
2. Diff changed models by compiled SQL hash (`manifest.json` → `compiled_code` field)
3. Re-index only changed models — no full re-embedding
4. Use **DuckDB VSS extension** (approximate nearest neighbors) as the vector store —
   zero external infrastructure, stays in-process

For real-time freshness: a `dbt` post-hook on `on-run-end` can trigger re-indexing.
Event-driven approaches (Azure Change Tracking, CDC + Kafka) are for enterprise scale, not needed here.

---

## 4. LabRat Gap Analysis

### 4.1 Current State vs. Competitors

| Dimension | LabRat (current) | Databao (#1) | SignalPilot (#2) |
|-----------|-----------------|--------------|------------------|
| **Schema source** | Raw schema.yml dump | manifest.json + catalog.json → semantic index | manifest.json + catalog.json + reference snapshot |
| **Context injection** | Full project dump in one string | Compact file tree upfront + on-demand reads | Project map tool + skill-gated focus calls |
| **Agent architecture** | Single DSPy ChainOfThought call | LangGraph ReAct, 9 tools, 50 turns | Two-agent (main 200t + verifier 80t) + 3 specialist agents |
| **Tool set** | None (single prompt) | 9 narrow tools with hard guards | 32 MCP tools, skill-gated workflow |
| **Verification** | None | Emergent via ReAct + `run_dbt` return code | 7-check verifier: schema, types, row count, values |
| **Write safety** | N/A | write_tool blocked on pre-existing files | Skill enforces no pre-existing file modification |
| **Date determinism** | None | Unknown | libfaketime per gold build date |
| **Prompt structure** | DSPy signature docstring | Jinja2 system prompt with numbered rules | Skill YAML files (6-step workflow) |
| **Self-correction** | None | ReAct loop on dbt errors | Quick-fix agent + verifier rebuild |
| **Schema linking** | None (dump everything) | Hybrid search (vector + keyword + RRF) | Tool-driven focus calls, sibling model reads |
| **Prompt optimization** | MIPROv2 autoresearch | None — hand-crafted | None — hand-crafted (AutoFyn for architecture, not prompts) |

### 4.2 Root Cause of the 8% Score

Our DSPy setup is not the right abstraction for Spider2-DBT. The task requires:
- Reading multiple files selectively
- Running `dbt` and observing errors
- Iterating on failures
- Verifying output matches expected shape

A single-shot prompt call cannot do any of these. The 8% we get (1/12 tasks passing) is exactly
what you'd expect from a model that gets lucky on the one task where the full context fits and
the SQL is simple enough to get right in one shot.

### 4.3 Why MIPROv2 Kept Scoring 0%

MIPROv2 optimizes prompt wording. But our failures are:
- **Wrong model completed**: not a wording problem; the agent needs to read the target file and
  verify it's completing the right one
- **DuckDB dialect errors**: partially fixable by prompt; v2 prompt helped here
- **Missing verification**: the model has no feedback loop to know it generated wrong SQL

MIPROv2 cannot discover that we need to add an agent loop — it can only tune the instruction text.
The autoresearch direction is not wrong in principle, but it's the wrong intervention until the
agent architecture is in place.

---

## 5. Prioritized Recommendations for LabRat

### P0: Architectural Shift — Add an Agent Loop

The single biggest change. Move from single-shot DSPy to a proper agent loop:
- **Read**, **write** (guarded), **run_dbt**, **run_sql**, **search_context** as tools
- `write_tool` must be blocked on pre-existing files from the start
- `dbt run` must always run synchronously (DB lock)
- Emergent self-correction falls out of the loop naturally

Estimated impact: the architectural gap alone explains most of the 8% → 40%+ jump. Every
competitor with a real agent loop clears 35% even with modest prompts.

### P1: Parse manifest.json + catalog.json (Not Raw YAML)

Replace the current raw `project_files` dump with:
1. Parse `manifest.json` → model descriptions, compiled SQL, column contracts, ref graph
2. Parse `catalog.json` → actual column types from the warehouse
3. Build M-Schema representation per model
4. Inject a compact project map (file tree) upfront; let the agent read specific models on demand

This is what Databao's Context Engine does. It's ~300 lines of Python using stdlib `json`.

### P2: Reference Snapshot Before Agent Runs

Before the agent touches anything, query the DuckDB to capture per-stub-model:
- Row count (expected output size)
- Column names and types
- 3 sample rows

Write to `reference_snapshot.md`. The agent works toward matching this. The verifier checks
against it. Without this, neither the agent nor the verifier has a baseline.

### P3: Verifier Pass After Build

After the main build completes, run a targeted verification:
1. Column schema check (names + types match reference snapshot)
2. Row count check (within tolerance)
3. Value spot-check on one sample row's unique key

This doesn't need a second agent — a structured tool call sequence in the same loop works.
SignalPilot's verifier catches 20-30% of tasks that the main agent thought it completed.

### P4: Planning Span Before SQL Generation

Force a YAML planning block before generating SQL for each model:
```
target_model: models/fct_orders.sql
source_models: [stg_orders, dim_customers, dim_dates]
join_path: stg_orders → dim_customers via customer_id, stg_orders → dim_dates via created_at
grain: one row per order
```

Reduces wrong-model completions and column hallucinations. The model commits to a plan
before writing, making errors easier to catch.

### P5: Sibling Model Reading (Before Writing)

Before completing any model, mandate that the agent read:
- The target file stub (what's already there)
- At least one complete sibling SQL file in the same directory

The sibling enforces CTE style, JOIN type conventions, and column naming patterns. SignalPilot's
`dbt-write` skill does exactly this and it significantly reduces structural errors.

### P6: Date Determinism for Eval

For Spider2-DBT evaluation specifically: derive each gold database's build date by scanning
`current_date` / date spine boundaries in the gold DuckDB, store in a JSON file, and apply
via `libfaketime` (or DuckDB's `SET current_date = '...'` if available). Without this, any
model using `current_timestamp` or a rolling window will produce mismatched results.

### P7: M-Schema for Context Engine (M29/M30 Retrofit)

The existing `ContextEngine` (M29) and external catalog (M30) should serialize context as
M-Schema rather than plain text. Add:
- Column descriptions (from `schema.yml` → `manifest.json`, or auto-generated)
- 2-5 sample values per categorical column (from a `TABLESAMPLE` or `LIMIT 5`)
- FK/ref relationships in a `[Foreign Keys]` section
- Evidence blocks (synonym maps, domain rules) pre-generated per model and cached

### P8: FK Graph + BFS for Schema Linking (Longer Term)

Build an in-memory networkx graph from the dbt `ref()` lineage:
- Nodes: models + source tables
- Edges: `ref()` relationships
- At query time: extract entity names from the question, resolve to seed models, BFS expand,
  select only candidate models for context

This produces the 87-93% token reduction that makes large-project tasks tractable.

---

## 6. What We Should NOT Do

- **Continue MIPROv2 autoresearch on the current architecture**: optimizing prompt wording on a
  single-shot call cannot fix agent-loop failures. Resume autoresearch after P0-P2 are implemented.
- **Use a vector DB immediately**: for 5-30 model dbt projects, networkx + BFS is simpler, faster,
  and equally accurate. Add vectors only when projects exceed ~50 models.
- **Try to match Databao's 9-tool philosophy directly**: LabRat is a general SQL agent, not
  dbt-only. The tool set should be broader, but retain the same discipline (hard guards, no general
  terminal access for eval).
- **Fine-tune a model**: Thinkquel is the only fine-tuned text-to-dbt model (92% EX) but requires
  a training pipeline. The top agents are all zero-shot with architecture discipline.

---

## 7. Sources

### Competitor Repositories
- [JetBrains/databao-agent](https://github.com/JetBrains/databao-agent) — Apache 2.0
- [JetBrains/databao-context-engine](https://github.com/JetBrains/databao-context-engine) — Apache 2.0
- [SignalPilot-Labs/SignalPilot](https://github.com/SignalPilot-Labs/SignalPilot) — Apache 2.0
- [SignalPilot-Labs/AutoFyn](https://github.com/SignalPilot-Labs/AutoFyn)

### Competitor Blog Posts
- [Databao: How We Ranked #1 on Spider2-DBT](https://blog.jetbrains.com/databao/2026/02/how-databao-agent-ranked-1-spider-2-0-dbt/)
- [Introducing Databao](https://blog.jetbrains.com/databao/2026/02/introducing-databao/)
- [SignalPilot Benchmark Page](https://www.signalpilot.ai/benchmark)
- [ExitStack: SignalPilot Cracks Data Engineering's Toughest Benchmark](https://www.exitstack.co/posts/signalpilot-tops-every-major-ai-lab-on-the-hardest-data-engineering-benchmark)

### Papers: Schema Representation
- [XiYan-SQL + M-Schema](https://arxiv.org/abs/2411.08599) — BIRD #1 (75.63% EX)
- [DAIL-SQL (VLDB 2024)](https://bolinding.github.io/papers/vldb24dailsql.pdf) — 86.6% Spider
- [C3: Clear Prompting + Calibration](https://arxiv.org/abs/2307.07306) — 82.3% zero-shot
- [SEED: Automatic Evidence Generation](https://arxiv.org/abs/2506.07423) — +17.73% with evidence
- [Thinkquel: Text-to-dbt](https://arxiv.org/abs/2510.00186) — only dedicated dbt model
- [Solid-SQL: Robust Schema Linking](https://arxiv.org/abs/2412.12522)

### Papers: Schema Linking + Retrieval
- [AutoLink (AAAI 2026)](https://arxiv.org/abs/2511.17190) — 97.4% recall, agent-driven
- [SchemaGraphSQL](https://arxiv.org/abs/2505.18363) — FK graph + BFS, zero-shot SOTA
- [DCG-SQL (ACL 2025)](https://arxiv.org/abs/2505.19956) — deep contextual schema graph
- [LitE-SQL (EACL 2026)](https://arxiv.org/abs/2510.09014) — pure vector schema linking
- [RASL (Amazon Science)](https://www.amazon.science/publications/rasl-retrieval-augmented-schema-linking-for-massive-database-text-to-sql) — enterprise scale
- [PSM-SQL](https://arxiv.org/abs/2502.05237) — progressive multi-granularity
- [ReFoRCE](https://arxiv.org/abs/2502.00675) — 35.8 on Spider2-Snow
- [The Death of Schema Linking?](https://arxiv.org/abs/2408.07702) — frontier models with full context
- [Schema Pruning: 93% Less Context](https://www.nirmalya.net/posts/2026/02/text-to-sql-schema-pruning/)
- [LLM Text-to-SQL Survey](https://arxiv.org/html/2406.08426v4)
- [Companion Agents](https://arxiv.org/abs/2601.08838) — +14.13% EX recovery

### Production Systems + Semantic Layers
- [dbt Semantic Layer vs Text-to-SQL 2026 Benchmark](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026)
- [dbt MCP Server Introduction](https://docs.getdbt.com/blog/introducing-dbt-mcp-server)
- [Wren AI MDL](https://docs.getwren.ai/oss/concepts/what_is_mdl)
- [Uber QueryGPT](https://www.uber.com/us/en/blog/query-gpt/)
- [Pinterest Text-to-SQL Embeddings](https://medium.com/pinterest-engineering/unified-context-intent-embeddings-for-scalable-text-to-sql-793635e60aac)
- [Databricks Text2SQL Improvement Guide](https://www.databricks.com/blog/improving-text2sql-performance-ease-databricks)
- [Snowflake Cortex Analyst](https://docs.snowflake.cn/en/user-guide/snowflake-cortex/cortex-analyst)
- [Select Star: Three Types of Context](https://www.selectstar.com/resources/text-to-sql-llm)
- [Atlan: Context Engineering for AI Analysts](https://atlan.com/know/context-engineering-for-ai-analyst/)
- [AWS Bedrock Dynamic Text-to-SQL](https://aws.amazon.com/blogs/machine-learning/dynamic-text-to-sql-for-enterprise-workloads-with-amazon-bedrock-agents/)
- [Azure SQL: Keeping Embeddings Updated](https://devblogs.microsoft.com/azure-sql/database-and-ai-solutions-for-keeping-embeddings-updated/)
- [M-Schema GitHub](https://github.com/XGenerationLab/M-Schema)
- [Contextual AI: Best Local Text-to-SQL](https://contextual.ai/blog/open-sourcing-the-best-local-text-to-sql-system)

### Leaderboard
- [Spider 2.0 Leaderboard](https://spider2-sql.github.io/)
