# ADE-bench Stubborn Failures: Root Cause Analysis & Improvement Roadmap

> Written 2026-05-26. Based on three full 3-attempt runs of the 20 previously-failing tasks
> (experiment `2026-05-25__23-06-27__none`). Six tasks hit usage limits and couldn't run;
> this covers the 10 that ran but still failed every attempt.

---

## Executive summary

After adding mandatory pre-submit verification, dbt anti-patterns, and N=3 retries, **5 of 20
tasks newly passed** (f1005, f1005.medium, f1009, helixops_saas009, airbnb011.hint). The
remaining 10 are not flakiness or carelessness — they represent **five distinct capability
gaps** that no amount of retry or instruction-hedging will close. Each gap has a clear
engineering fix, and three of the five gaps are already solved by competitors (Altimate Code,
Databao, Hex). The roadmap below maps each failure to its root cause, its fix, and the
competitive precedent.

**New score if all 10 were solved: 50/60 = 83%.** That puts LabRat past Altimate's published
74.4% (on their 43-task subset) and into territory no open-source agent currently occupies.

---

## Failure catalog

### analytics_engineering004 — Wrong column ordering and missing alias

**Task:** Create `obt_product_inventory` joining products onto inventory items.

**What happened:** Agent joined `fact_inventory` to `dim_products` correctly (LEFT JOIN on
product_id), but selected inventory columns first and product columns second — the reverse of
the expected schema. It also failed to alias `i.product_id AS ipd` to avoid the duplicate
column name that results from the join.

**The test that fails:** `AUTO_obt_product_inventory_equality` — row-level data comparison
where column ordering matters.

**Root cause: output-schema blindness.** The agent has no way to know the expected column
order before writing. It guesses a reasonable order (primary table first) but guesses wrong.

---

### analytics_engineering006 — Multi-step transformation chain with three missing pieces

**Task:** Create `dim_products`, `fact_inventory`, and `obt_product_inventory`; both fact
and OBT must be unique on `inventory_id`.

**What happened:** Three separate errors, each in a different model:
1. `dim_products` uses raw `supplier_id` (integer FK) instead of joining `stg_suppliers` to
   get `supplier_company` (string). The agent didn't discover this join requirement.
2. `fact_inventory` doesn't parse `transaction_created_date` from its raw format
   (`%m/%d/%Y %H:%M:%S`) to DATE — kept as string.
3. Neither model adds a `ROW_NUMBER() OVER(PARTITION BY ... ORDER BY ...)` dedup step to
   enforce the uniqueness constraint.

**Root cause: incomplete dependency discovery.** Each missing piece requires the agent to
infer an undocumented requirement from the staging layer (supplier join), the raw data format
(date string format), or the task description ("unique on inventory_id" → must deduplicate).

---

### analytics_engineering007.medium — Type migration misunderstood

**Task:** The project is broken because product IDs changed from integers to strings. Fix it.

**What happened:** Agent correctly identified that `id` in `stg_products` is now a string, but
then added `TRY_CAST(id AS INTEGER) IS NOT NULL` as a filter — silently dropping 5 products
with non-numeric IDs. Expected behavior: keep all products, add `CAST(product_id AS VARCHAR)`
in downstream fact tables to handle the type mismatch.

**Result:** Agent produced 40 rows; gold expects 45.

**Root cause: wrong mental model for type migrations.** The "filter the bad rows" approach
vs. the "cast downstream to match" approach produce identical dbt compile results but very
different data. Without knowing which is intended, the agent defaults to the wrong one.

---

### asana004 — Wrong JOIN grain (missing rows for projects with zero users)

**Task:** Extract `agg_project_users` and `count_project_users` CTEs from `asana__project`
into a new `int_asana__project_user_agg` model with `project_id`, `users`, `number_of_users_involved`.

**What happened:** Agent joined the two aggregation CTEs directly with a FULL OUTER JOIN
(`agg_project_users FULL OUTER JOIN count_project_users ON project_id`). This returns only
13 rows — it drops 3 projects that have no user activity (which should appear with NULL
values for `users` and `number_of_users_involved`).

**Expected:** Start from the full `project` table, LEFT JOIN both CTEs. Preserves all 16
projects including those with no user data.

**Root cause: fanout / NULL-row blindness.** Agent doesn't know that the gold expects NULL
rows for projects with no users. It naturally picks FULL OUTER JOIN (which it thinks is
"safe"), but the correct anchor is the project table.

---

### asana005 — Same as asana004

Identical root cause, identical fix. The base task adds an additional requirement (fix the
underlying project failure), but the JOIN grain error is the blocking failure in all 3 trials.

---

### asana005.hard — Upstream compilation error the agent isn't told to fix

**Task:** Create the intermediate model (same as asana004, without any mention of the
compilation error).

**What happened:** The asana project has a type mismatch in `stg_asana__task.sql` from the
Fivetran `asana_source` package v0.8.x: a COALESCE mixes TIMESTAMP and INTEGER types. dbt
compilation fails before any of the agent's models can be built.

The base variant says "fix whatever is causing the project to fail"; the hard variant omits
this sentence. Without that sentence, none of the 3 trials discovered and fixed the upstream
package bug, so the project never compiled.

**Root cause: error-first exploration absent.** The agent doesn't run `dbt compile` first to
discover pre-existing errors before planning. It assumes the project is healthy.

---

### airbnb010 — Interval vs. integer type confusion for date arithmetic

**Task:** Create SCD2 snapshot `snap__hosts` and `dim_superhost_evolution` model with
`acct_age_before_achieving_superhost`, `status_change_count`, `last_status_change_at`.

**What happened:** The agent's `acct_age_before_achieving_superhost` column returns an
INTERVAL or TIME value instead of an INTEGER (number of days). DuckDB's `DATE_DIFF('day',
start, end)` returns an integer, but if the agent used subtraction or DATEDIFF with different
syntax, it may produce an interval.

**The equality test fails** because the column type doesn't match what DuckDB expects for
integer comparison.

**Root cause: dialect-specific date arithmetic uncertainty.** DuckDB's date arithmetic is
subtly different from Postgres/Snowflake. Without running `dbt show` and inspecting the
actual column type, the agent can't verify it produced an integer.

---

### airbnb012 — Agent doesn't know dbt unit test syntax

**Task:** Add dbt unit tests to verify NPS calculation logic in `listing_agg_nps_reviews`
and `daily_agg_nps_reviews`. Tests must catch intentionally broken model variants.

**What happened:** The agent ran dbt successfully and the models pass their data tests — but
it wrote zero dbt unit tests. The harness checks `unit_tests_exist` (expects ≥1 unit test
in the manifest) and `broken_models_caught` (expects unit tests to catch 7 injected logic
bugs). Both fail because no unit tests were created.

**Root cause: dbt unit test syntax gap.** dbt unit tests (introduced in dbt-core 1.8,
February 2024) use a YAML `unit_tests:` block under a model's schema, with `given:` and
`expect:` sections. This is different from data tests (which the agent knows well). The agent
either doesn't know the syntax exists or doesn't recognize the task as requiring it.

---

### f1002 — Extra columns in output model

**Task:** Create four stat models from `__stats.yml`: `most_podiums`, `most_pole_positions`,
`most_fastest_laps`, `finishes_by_driver`.

**What happened:** The agent's `most_podiums` model selects `rank`, `driver_full_name`,
`podiums`, AND ALSO `p1`, `p2`, `p3` (podium breakdown by position 1/2/3). The gold expects
only `rank`, `driver_full_name`, `podiums`.

**Root cause: output-schema blindness.** Same pattern as analytics_engineering004. The
agent added "helpful" breakdown columns that seem reasonable given the task context but
aren't in the spec. The equality test rejects them because the column list doesn't match.

---

### helixops_saas007.no_location_hint — Agent adds geo_segment to marts but not the intermediate

**Task:** Add a `geo_segment` column (format `APAC-enterprise`) to the account 360 and
account health marts. The `no_location_hint` variant does not say "add it to
int_account_billing_snapshot."

**What happened:** The agent added `geo_segment` to `mart_account_360` and/or
`mart_account_health` but not to `int_account_billing_snapshot`. The test fails because the
intermediate model's column list doesn't include `geo_segment`.

**Root cause: lineage tracing absent.** Without the hint pointing to the intermediate
layer, the agent added the column to the final marts but didn't trace back up the DAG to
find where the column actually needs to originate. The correct approach is to start from
`stg_accounts` (where region + segment fields live), compute geo_segment in the intermediate
snapshot, then pass it downstream.

---

## Root cause taxonomy

Mapping all 10 failures to five distinct gaps:

| Gap | Failures | Description |
|---|---|---|
| **G1: Output-schema blindness** | 004, 006, f1002 | Agent can't know the gold's expected column list/order before writing |
| **G2: Dependency discovery** | 006, helixops007 | Agent doesn't trace the full DAG to find where changes originate |
| **G3: NULL-row / grain awareness** | asana004, asana005 | Agent picks the wrong JOIN anchor, dropping NULLable rows |
| **G4: Type/pattern misidentification** | 007.medium, airbnb010 | Agent picks the wrong pattern (filter vs. cast; interval vs. integer) |
| **G5: Missing dbt knowledge** | airbnb012, asana005.hard | Agent lacks specific dbt 1.8 syntax (unit tests) or doesn't run compile-first |

---

## Competitive context: how others solve these gaps

### Altimate Code (MIT, ADE-bench leader at 74.4%)
- **100+ deterministic tools** that each do one thing (explain column, show lineage, generate
  unit test, run data_diff). This is the key architectural difference from LabRat's 12-tool
  general-purpose loop. Narrower, purpose-built tools mean fewer hallucinations per step.
- **`data_diff` integration across 12 warehouses with 5 algorithms**: agent can compare two
  tables row-by-row after writing a model. This directly closes G1 (output-schema blindness)
  and G4 (type misidentification) — the agent runs data_diff against the existing table,
  sees the diff, and fixes it.
- **dbt unit test generation**: Altimate explicitly generates `unit_tests:` YAML blocks.
  Directly closes G5 (airbnb012).
- **Lineage-aware tools**: "explain column" traverses the DAG and tells the agent where a
  column originates. Directly closes G2 (dependency discovery, helixops007).

### Databao Agent (Apache 2.0, Spider 2.0-DBT #1 at 60.29)
- **Databao Context Engine** (separate repo, also Apache 2.0): auto-generates governed
  semantic context from dbt, PDFs, and warehouse metadata. The agent enters each task with
  a pre-built semantic layer that knows column types, relationships, and grain — closing G3
  and parts of G1.
- **Plan-then-execute structure** (visible in their Spider 2.0-DBT results): the agent
  produces an explicit plan with expected output schema before writing any SQL. This closes G1
  at the planning stage rather than the verification stage.

### Hex (SaaS, active 2026)
- **Context Studio + Automatic User Memory** (April 14, 2026): learns column naming
  conventions, date format patterns, and aggregation styles from prior notebook runs. If the
  user's warehouse consistently uses `CAST(STRPTIME(...) AS DATE)` for date parsing, it
  learns that pattern. This closes G4 (type/pattern misidentification) by building a
  project-specific pattern library.

### Wren AI (AGPL + Apache SDK, 14.8k stars)
- **"Open context layer"** (post May 7, 2026 restructure): governs SQL generation by
  pre-computing a semantic model of relationships, grains, and metrics. The agent queries the
  context layer instead of raw schema. Closes G3 (grain awareness) because the semantic model
  encodes the correct JOIN anchor per entity.

---

## Improvement roadmap

Organized into four tiers by engineering effort. Tiers are independent — Tier 1 can ship
without Tier 2, etc.

---

### Tier 1 — Prompt and tool-call improvements (2–5 days, ≈ +3 tasks)

Low-code changes to `_DOCKER_PREAMBLE` and task-family hints. No new tool infrastructure.

**T1.1 — Compile-first mandate** (closes G5 for asana005.hard)

Add to `_DOCKER_PREAMBLE` BEFORE WRITING ANY SQL section:

```
ALWAYS start by running dbt compile to check for pre-existing errors:
  docker exec {container_name} bash -c "cd /app && dbt compile 2>&1 | tail -30"
If compile fails, fix the error BEFORE writing any new models.
```

Expected lift: asana005.hard passes when agent discovers and fixes the stg_asana__task
COALESCE error before attempting the intermediate model.

**T1.2 — Column introspection before writing** (closes G1 partially, G4)

Add to verification section:

```
After building your model, inspect its actual column types:
  docker exec {container_name} bash -c "cd /app && dbt show --select <model> --limit 1"
Check that: (a) column names match what the task describes, (b) date columns are DATE not
TIMESTAMP, (c) numeric columns are INTEGER/BIGINT not INTERVAL.
If a column type looks wrong, check the gold by reading the task description carefully.
```

Expected lift: airbnb010 (agent discovers `acct_age_before_achieving_superhost` is INTERVAL,
casts to INTEGER). Possibly f1002 on rerun.

**T1.3 — dbt unit test syntax added to _DOCKER_PREAMBLE** (closes G5 for airbnb012)

When task description contains "unit test" or "test" + "model" or "verify":

```
dbt UNIT TEST SYNTAX (dbt-core 1.8+):
In the model's schema.yml, add:

  unit_tests:
    - name: test_nps_formula_promoters
      model: listing_agg_nps_reviews
      given:
        - input: ref('reviews')
          rows:
            - {score: 9, review_id: 1}
            - {score: 5, review_id: 2}
      expect:
        rows:
          - {promoters: 1, detractors: 1, nps: -50}

Run: docker exec {container_name} bash -c "cd /app && dbt test --select <model>"
```

Expected lift: airbnb012 (agent now knows the syntax and produces valid unit tests).

**T1.4 — Analytics Engineering family hint expansion** (closes G3 partially)

Expand the `analytics_engineering` hint in `_FAMILY_HINTS` to say:

```
- The grain of a JOIN anchor matters: always start from the most complete table (usually
  the dim/project table) and LEFT JOIN aggregations. Never FULL OUTER JOIN two aggregation
  CTEs if you need NULL rows for entities with zero activity.
- When a task says "unique on X_id", add: ROW_NUMBER() OVER(PARTITION BY x_id ORDER BY
  updated_at DESC) as rn, then WHERE rn = 1 in a final CTE.
- Check staging models for FK columns that might need joining to get human-readable names
  (e.g., supplier_id → JOIN stg_suppliers to get supplier_company).
```

Expected lift: asana004, asana005 (grain awareness); analytics_engineering006 (supplier join
discovery).

---

### Tier 2 — New tools for schema comparison and lineage (1–2 weeks, ≈ +4 tasks)

New agent tools that expose information the model can't currently get.

**T2.1 — `compare_schema` tool** (closes G1, G4)

A new docker-exec-based tool that:
1. Lists the columns and types of the agent's model output
2. Compares against any "hint" schema available (from YAML tests, schema.yml, or task description)
3. Reports mismatches: "column `ipd` expected but not found; column `attachments` present
   but not expected"

Implementation: read schema.yml test definitions to infer expected column names; run
`DESCRIBE SELECT * FROM <model>` via dbt show. Report the diff in the MANDATORY VERIFICATION step.

This is LabRat's version of **Altimate's `data_diff`** — a lightweight internal diff
rather than cross-warehouse parity. Takes ~3 days to implement.

**T2.2 — `trace_column_lineage` tool** (closes G2)

Given a column name + target model, trace back through `ref()` chains to find where it
originates. Implementation: parse the dbt manifest's `node_deps` graph, follow `ref()`
references, and report the chain. When the agent needs to add `geo_segment` to mart_account_360,
it calls this tool to discover that `geo_segment` doesn't exist anywhere in the DAG yet and
must be created at `int_account_billing_snapshot`.

Inspired by **Databao Context Engine**'s lineage awareness and **Altimate's lineage tools**.

**T2.3 — `check_project_compile` tool** (closes G5 for compile-first, more reliable than prompt)

A dedicated tool that runs `dbt compile` and returns structured error output (model name,
line number, error message). Unlike the prompt-based T1.1, this is a first-class tool the
agent can call at any time and get clean JSON back. Makes compile-first a reliable habit
rather than a suggested step.

**T2.4 — `generate_unit_test` tool** (closes G5 for airbnb012 reliably)

Given a model name + a description of what to test, generate a valid dbt 1.8 unit test YAML
block. The tool reads the model's SQL, identifies input refs, and scaffolds the `given:`/
`expect:` structure. The agent fills in the test values.

Directly inspired by **Altimate Code's dbt unit test generation** feature.

---

### Tier 3 — Plan-then-execute architecture (2–4 weeks, ≈ +6–8 tasks, systemic)

A fundamental loop change that closes G1, G2, G3 at the planning stage. Inspired by
**Databao's explicit plan step** and the prior Spider2 work (now deleted from this repo).

**T3.1 — Mandatory planning phase in `_DOCKER_PREAMBLE`**

Before writing any SQL, the agent must produce a structured plan:

```
REQUIRED PLANNING PHASE (do this before any file edits):

## Plan
Models to create/modify: [list]
For each model:
  - Grain: [what is one row]
  - Input refs: [what models/sources it reads from]
  - Join strategy: [how to join; which is the LEFT anchor]
  - Output columns (with types): [list]
  - Uniqueness guarantee: [how to ensure unique rows if required]

Only after writing this plan, proceed to implement.
```

This closes:
- **G1** (output-schema blindness): the plan forces the agent to commit to a column list
  before writing, catching ordering/aliasing errors at plan time.
- **G2** (dependency discovery): the agent must list input refs, forcing it to inspect
  what models exist.
- **G3** (grain awareness): the plan forces explicit JOIN anchor declaration.

**T3.2 — Verifier/critic pass** (closes G4, catches all residual errors)

After the agent says "done," run a second, cheaper prompt that:
1. Reads the agent's model files
2. Reads the task description
3. Reports: "The plan said X, but the model does Y. Potential issues: [list]"

The main agent then decides whether to fix. This is **Hex's pattern** (Notebook Agent +
Modeling Agent as separate roles).

Implementation: add a second `claude -p` invocation inside `LabratLocalAgent.perform_task`
that runs after the main loop completes, using a short system prompt. Cost: ~$0.05 extra per
task.

**T3.3 — Schema YAML parsing to infer gold columns**

ADE-bench tasks often have schema.yml files in the project that define column tests (not null,
unique, accepted values). These implicitly describe the expected column list. Parse these
before the agent starts and inject a "known schema" section into the prompt:

```
KNOWN SCHEMA (from schema.yml — use these column names exactly):
  obt_product_inventory: [inventory_id, product_id AS ipd, product_name, quantity, ...]
```

This closes G1 precisely, without needing a gold table comparison.

---

### Tier 4 — Semantic layer + memory (4–8 weeks, systemic, closes G4 for all future tasks)

Longer-term investments that pay dividends across all benchmarks, not just these 10 failures.

**T4.1 — Project-specific pattern memory** (closes G4 broadly)

Inspired by **Hex's Automatic User Memory** (April 2026). A JSONL file per dbt project that
records:
- Date parsing pattern used (e.g., `STRPTIME(col, '%m/%d/%Y %H:%M:%S')`)
- Column naming conventions (e.g., snake_case, `_id` suffix for FKs)
- Whether dedup is typically done with ROW_NUMBER or QUALIFY
- Common supplier/dimension join patterns

On each task, inject the relevant project memories. This means analytics_engineering006
would know "this project parses dates with STRPTIME and uses ROW_NUMBER for dedup" from
prior runs.

**T4.2 — dbt semantic layer integration** (closes G3 broadly)

Parse the dbt manifest's `semantics/` layer (metrics, entities, dimensions) to build a grain
map: "one row in `int_asana__project_user_agg` = one project." When the agent writes a JOIN
that would change the grain, the tool warns it.

Inspired by **Databao Context Engine** and **Wren AI's semantic model** approach.

**T4.3 — data_diff integration** (closes G1 definitively)

Implement LabRat's own lightweight data diff: given two DuckDB tables (agent output vs. a
pre-built reference), report row-level differences. For ADE-bench, the "reference" can be
the setup dbt run output. For production, it's the user's current table.

**Altimate does this across 12 warehouses with 5 algorithms.** LabRat's DuckDB-native version
takes ~1 week and opens up a class of validation that currently doesn't exist.

---

## Summary table

| Gap | Tier 1 (days) | Tier 2 (weeks) | Tier 3 (weeks) | Tier 4 (months) | Tasks closed |
|---|---|---|---|---|---|
| G1: Output-schema blindness | T1.2 (partial) | T2.1 `compare_schema` | T3.1 planning phase, T3.3 schema parsing | T4.3 data_diff | 004, f1002 |
| G2: Dependency discovery | — | T2.2 `trace_column_lineage` | T3.1 planning phase | T4.2 semantic layer | helixops007, 006 |
| G3: NULL-row / grain awareness | T1.4 hint expansion | — | T3.1 planning phase | T4.2 semantic layer | asana004, asana005 |
| G4: Type/pattern misidentification | T1.2 column introspection | T2.1 schema compare | T3.2 verifier pass | T4.1 project memory | 007.medium, airbnb010 |
| G5: Missing dbt knowledge | T1.1 compile-first, T1.3 unit test syntax | T2.3 compile tool, T2.4 unit test generator | — | — | airbnb012, asana005.hard |

**Projected score progression:**

| After | Estimated score | Notes |
|---|---|---|
| Baseline (post-Tier 1 prompt changes) | 75% (45/60) | Already achieved with the 5 new passes |
| + Tier 1 additions (T1.1–T1.4) | ~78% (47/60) | Closes asana005.hard, airbnb012, asana004 |
| + Tier 2 tools | ~82% (49/60) | Closes helixops007, 006 partially |
| + Tier 3 planning | ~85%+ (51/60) | Closes most G1/G2/G3 failures |
| + Tier 4 semantic layer | 87%+ | Closes G4 systematically |

**Recommended next action:** Ship Tier 1 additions (T1.1–T1.4) in one PR to the ade-bench
repo. They're all prompt changes, 1–2 days of work, and should push the score from 75% to
~78% with no architecture changes. Then measure, and use the new cast file data to decide
whether Tier 2 tools (compare_schema, lineage tracing) are worth the investment.

---

## What LabRat can claim competitors can't

After Tier 2 is complete:

1. **AGPL-3.0 + terminal-native + dbt-catalog-native** — still unique in the top 7 of both
   benchmarks. Databao is Apache, SignalPilot is Apache, Altimate is MIT. None are terminal-native.

2. **Audit log + provenance export** — unique among OSS data agents.

3. **Lineage-aware column tracing** (T2.2) — Databao has this via a separate context engine
   repo; LabRat would have it natively in the agent loop.

4. **data_diff natively in the TUI** (T4.3) — Altimate has CLI data_diff; LabRat can be the
   first to surface it in a TUI workflow, tied to the SQL editor panel.
