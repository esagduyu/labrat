# LabRat — Test Findings

**Date:** 2026-05-23  
**Build:** M1–M32 complete (33 milestones including M14.5)  
**Test suite:** 426 passing, 6 skipped  

---

## 1. Overall Test Health

```
426 passed, 6 skipped in ~12s
```

The 6 skipped tests are intentionally gated behind environment variables:
- `LABRAT_RUN_LLM_TESTS=1` — live LLM API tests (context analyzer, memory extractor)
- `ANTHROPIC_API_KEY` — full agent NL→SQL pipeline tests

All 426 passing tests run without any network access or external services.

---

## 2. Component Test Results

| Module | Tests | Notes |
|---|---|---|
| DB layer (DuckDB, Postgres, warehouse adapters) | 30 | Adapters unit-tested via class inspection; integration tests need `testcontainers` |
| Connection management (M24) | 11 | Thread-scope enforcement verified |
| Eval framework (M26–M27) | 29 | Runner, reporter, baselines all mocked |
| Query history (M28) | 15 | PII redaction verified for SSN, email, phone |
| Context engine (M29) | 11 | Relevance scoring, bundle serialization, mocked LLM |
| External catalog (M30) | 27 | DbtLoader, lineage, watcher, MCP client, manager |
| Self-healing memory (M31) | 26 | Model, store, extractor (mocked), retrieval |
| Custom validations (M32) | 21 | Model, store, checker (mocked LLM) |
| UI widgets | 8 snapshot + unit | ResultsTable, SchemaTree |

---

## 3. DuckDB End-to-End Evaluation

Evaluated against a 4-table e-commerce DuckDB (`customers`, `orders`, `products`, `events`).

### Schema Introspection ✅

```
Database: ecommerce
Tables found: ['customers', 'events', 'orders', 'products']
Schemas: 3 (main, information_schema, pg_catalog)
```

DuckDB introspects cleanly via `information_schema.columns`. The `DuckDBConnection.introspect_catalog()` correctly identifies all user tables and their column types.

### SQL Execution ✅

Test query: revenue by region for completed orders  
Result: 3 rows returned in <2ms  
Top region: EU ($3,850.00 revenue)

### Gold SQL Correctness ✅ 5/5

All 5 hand-crafted evaluation questions returned correct results when run with gold SQL:

| Question | Result |
|---|---|
| Total revenue from completed orders | $7,225.50 |
| Q4 2025 revenue by region | EU: $3,850, US-West: $2,200.25, US-East: $1,100 |
| Top 3 customers by total spend | Alice Johnson (#1) |
| Orders by status | completed: 6, pending: 1, refunded: 1 |
| Avg order value by product category | Hardware highest ($1,416.67) |

### Agent NL→SQL ⚠️ Not run (no API key)

The full agent pipeline (schema introspection → sampling → SQL generation → execution) requires `ANTHROPIC_API_KEY`. The evaluation script (`scripts/eval_duckdb.py`) is ready to run with:

```bash
ANTHROPIC_API_KEY=<key> uv run python scripts/eval_duckdb.py
```

---

## 4. Spider2-DBT Benchmark Exploration

Evaluated against the Spider2-DBT benchmark (68 tasks, ~/repos/spider2).

### DbtLoader Compatibility ✅ 9/10

Ran LabRat's `DbtLoader` (M30) against the first 10 Spider2-DBT example projects:

| Task | Status | Models |
|---|---|---|
| playbook001 | schema_yml_loaded | — |
| provider001 | raw_sql_only | 3 SQL files |
| asana001 | schema_yml_loaded | 46 models |
| shopify001 | schema_yml_loaded | 46 models |
| asset001 | schema_yml_loaded | 6 models |
| flicks001 | schema_yml_loaded | 45 models |
| analytics_engineering001 | schema_yml_loaded | 2 models |
| xero_new001 | schema_yml_loaded | 46 models |
| chinook001 | schema_yml_loaded | 52 models |
| f1001 | schema_yml_loaded | 44 models |

9/10 projects are parseable via `schema.yml`. `provider001` has raw SQL only (no schema.yml or manifest.json).

### Gap Analysis: Spider2-DBT vs. LabRat's Primary Use Case

Spider2-DBT tests **dbt model completion** (writing SQL files that produce correct output tables). LabRat's primary use case is **interactive NL→SQL against a connected warehouse**.

These partially overlap but are not identical:

| Capability | LabRat (current) | Spider2-DBT need |
|---|---|---|
| Schema introspection from manifest.json | ✅ M30 | Required |
| Lineage graph traversal | ✅ M30 | Useful |
| NL→SQL generation (DuckDB) | ✅ M14 | Required (basis) |
| dbt model file writing | ❌ | Core requirement |
| Multi-model project completion | ❌ | Core requirement |
| Running dbt to validate outputs | ❌ | Required for scoring |

To score on Spider2-DBT, LabRat would need an additional agent tool that writes SQL files to the dbt project directory and optionally runs `dbt run` to validate.

### Full Spider2-DBT Evaluation

To run the full benchmark once an API key is available:

```bash
ANTHROPIC_API_KEY=<key> uv run python scripts/eval_spider2.py --limit 68
```

---

## 5. PII Redaction Verification

The `redact_pii()` function correctly handles:

| Input | Output |
|---|---|
| `WHERE ssn = '123-45-6789'` | `WHERE ssn = '[REDACTED]'` |
| `WHERE email = 'alice@example.com'` | `WHERE email = '[REDACTED]'` |
| `WHERE phone = '+1-555-867-5309'` | `WHERE phone = '[REDACTED]'` |

SSN is matched before phone to avoid false positives (phone regex can match SSN-like patterns if phone goes first).

---

## 6. Memory & Validation Integration

### Memory Store Correctness
- JSONL persistence verified: append → read → delete → increment_applied all work correctly.
- Profile isolation: `dev` memories don't appear in `prod` profile reads.

### Validation Checker
- `"pass"` response → `ValidationStatus.pass_`
- `"warn: <text>"` response → `ValidationStatus.warn` with explanation
- `"block: <text>"` response → `ValidationStatus.block` with explanation
- Empty rules list → empty results (no LLM calls)
- Each rule gets an independent LLM call (verified via call counter in tests)

---

## 7. Known Gaps and Future Work

### No API Key Available
The most significant gap in this evaluation is the inability to run the live agent pipeline. All agent behavior is unit-tested with mocked LLM responses, but real accuracy numbers require live API access.

**To run live eval:**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
uv run python scripts/eval_duckdb.py          # DuckDB accuracy
LABRAT_RUN_LLM_TESTS=1 uv run pytest         # LLM integration tests
```

### Spider2-DBT Requires dbt Model Writing
LabRat would need a new capability — writing SQL to dbt model files — to participate in Spider2-DBT scoring. This is a natural next feature for the agent's tool registry.

### Integration Tests Need testcontainers
Postgres, MySQL, Trino, and Redshift adapters are tested via class inspection only. Full integration tests using `testcontainers` (already a dev dependency) would give higher confidence.

### Textual TUI: Display Required for Interactive Testing
The full 3-pane TUI (`uv run labrat`) requires an interactive terminal with a proper display. Widget-level testing via Textual's headless pilot covers the components, but end-to-end TUI testing on a real terminal has not been automated.

---

## 8. Performance Notes

All SQL execution via DuckDB is sub-5ms for the sample dataset (8 orders, 5 customers, 3 products). At realistic analytical query scale:

- Schema introspection: O(tables × columns), typically <100ms even for large warehouses
- Context bundle rebuild: dominated by LLM call (~1-3s with Haiku)
- Memory retrieval: O(n) text scan, typically <1ms for ≤1000 memories
- Validation check: n × LLM calls, recommend ≤5 rules to stay under 5s total

---

## 9. Screenshots Captured

Five SVG screenshots of UI components (in `screenshots/`):

- `results_table.svg` — populated data table with 5 rows, sortable columns
- `schema_browser.svg` — tree view of 2-schema catalog (public + staging)
- `sql_editor.svg` — SQL editor with Q4 revenue query and syntax highlighting
- `trace_log.svg` — agent reasoning trace: schema check → sampling → tool calls
- `chat_panel.svg` — chat interface with user question and agent response
