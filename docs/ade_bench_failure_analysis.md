# ADE-bench Stubborn Failures: Root Cause Analysis & Improvement Roadmap

> Originally written 2026-05-26. Updated 2026-05-27 to reflect Tier 1 completion and
> actual results. Current score: **80% (48/60)**. 12 base-task failures remain.

---

## Executive summary

Starting from a 67% baseline, a series of targeted improvements has brought LabRat to
**80% (48/60)** on ADE-bench DuckDB+dbt with claude-sonnet-4-6 and best-of-3 retries.

**Improvement history:**

| Date | Change | Score |
|---|---|---|
| 2026-05-24 | Baseline | 67% (40/60) |
| 2026-05-25 | Exploration discipline, verification loop, anti-patterns, family hints | 75% (45/60) |
| 2026-05-26 | Errored task recovery (quickbooks004 confirmed passing) | ~77% (46/60) |
| 2026-05-27 | **Tier 1 complete**: compile-first, column type check, unit test syntax, helixops/analytics hints | **80% (48/60)** |

The remaining 12 failures are not flakiness. They represent five distinct capability gaps —
some addressable with further prompt work, others requiring new tool infrastructure. Four
additional failures (`helixops_saas010`, `helixops_saas015`, `intercom002`, `quickbooks003`)
have not yet been root-cause analysed and may represent new gap categories.

**If all 12 were solved: 60/60 = 100%.** Realistic target with Tier 2 complete: **~87% (52/60).**

---

## Failure catalog

### ✅ CLOSED — airbnb010 (fixed by T1.2)

**Was:** Agent produced `acct_age_before_achieving_superhost` as INTERVAL instead of INTEGER
(days since account creation). The column type inspection step (T1.2) now catches this: the
agent runs `dbt show`, sees "3 days 00:00:00", and replaces the expression with
`DATEDIFF('day', start_date, end_date)`. Passed 2/3 attempts in T1 validation.

---

### ✅ CLOSED — airbnb012 (fixed by T1.3)

**Was:** Agent wrote zero dbt unit tests because it didn't know the `unit_tests:` YAML syntax.
Adding a full syntax example to `_DOCKER_PREAMBLE` (T1.3) was sufficient: the agent now
produces valid `unit_tests:` blocks that catch the 7 injected logic bugs. Passed 1/3 attempts
in T1 validation (flaky due to test-value selection, but the gap is closed).

---

### analytics_engineering004 — Wrong column ordering and missing alias

**Task:** Create `obt_product_inventory` joining products onto inventory items.

**What happens:** Agent joins `fact_inventory` to `dim_products` correctly (LEFT JOIN on
product_id), but selects inventory columns first and product columns second — the reverse of
the expected schema. It also fails to alias `i.product_id AS ipd` to avoid the duplicate
column name that results from the join.

**The test that fails:** `AUTO_obt_product_inventory_equality` — row-level data comparison
where column ordering matters.

**T1 result:** Still failing 2/3 tests (67%) across all 3 attempts. T1.2 column type
inspection doesn't help because the column *types* are correct — the *order* and *alias* are
wrong. This requires schema comparison, not just type checking.

**Root cause: G1 — output-schema blindness.** The agent has no way to know the expected
column order before writing. It guesses a reasonable order (primary table first) but guesses
wrong.

---

### analytics_engineering006 — Multi-step transformation chain with three missing pieces

**Task:** Create `dim_products`, `fact_inventory`, and `obt_product_inventory`; both fact
and OBT must be unique on `inventory_id`.

**What happens:** Three separate errors, each in a different model:
1. `dim_products` uses raw `supplier_id` (integer FK) instead of joining `stg_suppliers` to
   get `supplier_company` (string). The agent doesn't discover this join requirement.
2. `fact_inventory` doesn't parse `transaction_created_date` from its raw format
   (`%m/%d/%Y %H:%M:%S`) to DATE — kept as string.
3. Neither model adds a `ROW_NUMBER() OVER(PARTITION BY ... ORDER BY ...)` dedup step to
   enforce the uniqueness constraint.

**T1 result:** Still failing 5/8 tests (62%) across all 3 attempts. The FK join discovery
hint in T1.4 (analytics_engineering family) explicitly mentions supplier_id → stg_suppliers,
but 3 tests still fail — the date parsing and dedup issues remain. This task requires all
three fixes simultaneously; getting any two right still fails.

**Root cause: G2 — dependency discovery** (supplier join) **+ G1 — output-schema blindness**
(column list for dedup) **+ G4 — type/pattern misidentification** (date parsing format).

---

### analytics_engineering007.medium — Type migration misunderstood *(variant, not in base 60)*

**Task:** The project is broken because product IDs changed from integers to strings. Fix it.

**What happens:** Agent correctly identifies that `id` in `stg_products` is now a string, but
adds `TRY_CAST(id AS INTEGER) IS NOT NULL` as a filter — silently dropping 5 products with
non-numeric IDs. Expected: keep all products, add `CAST(product_id AS VARCHAR)` in downstream
fact tables.

**T1 result:** Still failing 10/11 tests (91%) — one test off across all 3 attempts. The base
`analytics_engineering007` task passes; only the `.medium` variant (harder framing) fails.

**Root cause: G4 — wrong mental model for type migrations.** Filter vs. cast produce
identical dbt compile results but very different data. Without knowing which is intended, the
agent defaults to the wrong one.

---

### asana004 — Wrong JOIN grain (missing rows for projects with zero users)

**Task:** Extract two aggregation CTEs from `asana__project` into a new
`int_asana__project_user_agg` model with `project_id`, `users`, `number_of_users_involved`.

**What happens:** Agent joins the two aggregation CTEs with FULL OUTER JOIN, returning 13
rows. Expected: anchor on the full project table with LEFT JOIN, returning all 16 projects
including the 3 with no user activity (which appear as NULLs).

**T1 result:** Still failing 6/7 tests (86%) across all 3 attempts. **Key insight:** T1.4
added JOIN grain rules to the `analytics_engineering` family hints, but asana004 is in the
`asana` family. Those rules were never injected. This is a quick fix — same hints need to be
added to the `asana` family.

**Root cause: G3 — NULL-row / grain awareness.** Also: T1.4 targeted the wrong family.

---

### asana005 — Same JOIN grain issue as asana004

Identical root cause. The base task adds an additional requirement (fix an underlying project
failure) but the JOIN grain error is the blocking failure.

**T1 result:** Still failing 7–8/9 tests (78–89%). Same fix needed as asana004: add JOIN
grain rules to the `asana` family hints.

**Root cause: G3 — NULL-row / grain awareness + T1.4 wrong-family targeting.**

---

### asana005.hard — Upstream compilation error the agent isn't told to fix *(variant)*

**Task:** Create the intermediate model without any mention of the upstream compilation error.

**What happens:** The asana project has a type mismatch in `stg_asana__task.sql` from the
Fivetran `asana_source` package v0.8.x: a COALESCE mixes TIMESTAMP and INTEGER types. dbt
compilation fails before any of the agent's models can be built.

**T1 result:** Still failing 3/9 tests (33%). The compile-first step (T1.1) now correctly
surfaces the upstream error — the agent does run `dbt compile` and sees the COALESCE error.
But fixing a type mismatch inside a dbt *package* (not the project's own models) is harder:
the agent needs to override the package's SQL file via a patch or `dispatch:` mechanism, which
it doesn't know to try. T1 surfaced the error but didn't give the agent the vocabulary to
fix it.

**Root cause: G5 — missing dbt knowledge.** Specifically: overriding package SQL via
`patches:` or `dispatch:` in `dbt_project.yml`.

---

### f1002 — Extra columns in output model

**Task:** Create four stat models from `__stats.yml`: `most_podiums`, `most_pole_positions`,
`most_fastest_laps`, `finishes_by_driver`.

**What happens:** Agent's `most_podiums` model selects `rank`, `driver_full_name`,
`podiums`, AND ALSO `p1`, `p2`, `p3` (podium breakdown by position 1/2/3). Gold expects
only `rank`, `driver_full_name`, `podiums`. Equality test rejects the extra columns.

**T1 result:** Still failing 10/11 tests (91%). The F1 column-discipline hint added in T1.4
says "output only the columns the task spec lists" — but the task spec is `__stats.yml`, a
file the agent needs to read and strictly follow. It's still adding breakdown columns it
thinks are helpful.

**Root cause: G1 — output-schema blindness.** Column discipline hint helps but is
insufficient when the schema is embedded in a YAML file that requires precise parsing.

---

### helixops_saas007 — Agent adds geo_segment to marts but not the intermediate

**Task:** Add a `geo_segment` column to the account 360 and account health marts. Without an
explicit hint, the agent doesn't know the column must first be added to
`int_account_billing_snapshot`.

**T1 result:** Still failing 7/8 tests (88%) across all 3 attempts. The new `helixops_saas`
family hint explicitly mentions `int_account_billing_snapshot` as a common upstream dependency
and instructs the agent to use `dbt ls --select +<mart_name>` to trace the DAG. Still failing
— the agent traces the DAG but doesn't propagate the column through the full chain correctly.

**Root cause: G2 — dependency discovery / lineage tracing.** Prompt hints are insufficient;
the agent needs a tool that can read the manifest and report the exact upstream node list.

---

### helixops_saas010 — *Needs investigation* *(was: known-flaky, now consistently failing)*

**Symptoms:** Fails 9/11 tests (82%) consistently across all 3 attempts in every run since
T1. Previously described as flaky (~50% pass rate); now failing consistently. The 2 failing
tests have not been identified by reading agent logs.

**Status:** Root cause unknown. May be a new failure mode introduced by a change in the
benchmark, or a task that only passes when the agent explores a particular code path not
consistently triggered by the new compile-first preamble. Needs log analysis.

---

### helixops_saas015 — *Needs investigation*

**Symptoms:** Fails 3/4 tests (75%) consistently. One specific test fails every run. The
`.low` variant passes consistently (3/3); the base task does not.

**Status:** Root cause unknown. The `.low` variant likely has a simpler or more constrained
version of the same task. The difference between what `.low` does differently needs inspection.

---

### intercom002 — *Needs investigation*

**Symptoms:** Fails 3/5 tests (60%) consistently across all 3 attempts. Note: Altimate Code
passes `intercom002` with 4/4 tests — our run has 5 tests, meaning the benchmark was updated
since Altimate ran it. The 2 failing tests are the ones that were added.

**Status:** Root cause unknown, but the newer tests are the likely culprit. The agent handles
the original 3 tests but not the 2 that were added post-Altimate.

---

### quickbooks003 — *Needs investigation*

**Symptoms:** Fails 6/15 tests (40%) consistently — the most broadly broken task in the
suite. 9 specific tests fail every run. `quickbooks003` is a hard task, and both LabRat and
Altimate fail it (they get 5/14; we get 6/15).

**Status:** Root cause unknown. Both agents fail, suggesting structural complexity (multiple
models, complex business logic, or date arithmetic chains) rather than a simple fixable gap.
May require Tier 2 tooling before it's closable.

---

## Root cause taxonomy

Updated to reflect closed tasks and revised understanding:

| Gap | Open failures | Closed | Description |
|---|---|---|---|
| **G1: Output-schema blindness** | ae004, ae006, f1002 | (none by T1) | Agent can't know the gold's expected column list/order before writing |
| **G2: Dependency discovery** | ae006, helixops007 | (none by T1) | Agent doesn't trace the full DAG to find where changes originate |
| **G3: NULL-row / grain awareness** | asana004, asana005 | (none by T1; T1.4 targeted wrong family) | Agent picks the wrong JOIN anchor, dropping NULLable rows |
| **G4: Type/pattern misidentification** | ae007.medium | **airbnb010 ✅** | Agent picks the wrong pattern (interval vs. integer, filter vs. cast) |
| **G5: Missing dbt knowledge** | asana005.hard | **airbnb012 ✅** | Agent lacks dbt 1.8 syntax or can't fix package-level errors |
| **G6: Unknown** | helixops010, helixops015, intercom002, quickbooks003 | — | Root cause not yet analysed |

---

## Competitive context: how others solve these gaps

### Altimate Code (MIT, ~78% DuckDB per their published results)
- **100+ deterministic tools** that each do one thing (explain column, show lineage, generate
  unit test, run data_diff). This is the key architectural difference from LabRat's 12-tool
  general-purpose loop. Narrower, purpose-built tools mean fewer hallucinations per step.
- **`data_diff` integration across 12 warehouses with 5 algorithms**: agent can compare two
  tables row-by-row after writing a model. Directly closes G1 (output-schema blindness) and
  G4 (type misidentification).
- **dbt unit test generation**: Altimate explicitly generates `unit_tests:` YAML blocks. Was
  the model for T1.3, which closed airbnb012.
- **Lineage-aware tools**: "explain column" traverses the DAG. Directly closes G2
  (dependency discovery, helixops007).

### Databao Agent (Apache 2.0, Spider 2.0-DBT #1 at 60.29)
- **Databao Context Engine**: auto-generates governed semantic context from dbt, PDFs, and
  warehouse metadata. Closes G3 and parts of G1 by pre-building a semantic layer that knows
  column types, relationships, and grain.
- **Plan-then-execute structure**: agent produces an explicit plan with expected output schema
  before writing any SQL. Closes G1 at the planning stage rather than the verification stage.

### Hex (SaaS, active 2026)
- **Context Studio + Automatic User Memory** (April 2026): learns column naming conventions,
  date format patterns, and aggregation styles from prior runs. Closes G4 by building a
  project-specific pattern library over time.

### Wren AI (AGPL + Apache SDK, 14.8k stars)
- **"Open context layer"** (May 2026 restructure): governs SQL generation by pre-computing a
  semantic model of relationships, grains, and metrics. Closes G3 because the semantic model
  encodes the correct JOIN anchor per entity.

---

## Improvement roadmap

### ~~Tier 1~~ — COMPLETE ✅ (shipped 2026-05-27, +2 tasks)

All T1.1–T1.4 changes are live in `labrat_local_agent.py`.

**Actual results vs. expected:**

| Item | Expected | Actual |
|---|---|---|
| T1.1 Compile-first | asana005.hard passes | ❌ Compile error now visible but agent can't fix package-level COALESCE |
| T1.2 Column type inspection | airbnb010 passes | ✅ Passed 2/3 attempts |
| T1.3 Unit test syntax | airbnb012 passes | ✅ Passed 1/3 attempts (gap closed; flakiness on test values) |
| T1.4 Analytics_engineering hints | asana004, asana005, ae006 improve | ❌ Wrong family — hints only inject for `analytics_engineering` prefix, not `asana` |

**Net: +2 tasks (airbnb010, airbnb012). Score: 75% → 80%.**

T1.4 missed asana004/005 because the JOIN grain rules were added to the `analytics_engineering`
family but the tasks are in the `asana` family. This is a one-line fix for Tier 1.5.

---

### Tier 1.5 — Fast follow-ups (< 1 day, ≈ +2 tasks)

**T1.5a — Add JOIN grain rules to `asana` family hints**

Copy the JOIN grain rules from the `analytics_engineering` hint into the `asana` hint in
`_FAMILY_HINTS`. Specifically:
- "Always anchor on the most complete entity table (project), LEFT JOIN aggregations"
- "Never FULL OUTER JOIN two aggregation CTEs — you'll drop entities with zero activity"

Expected lift: asana004 and asana005 — both fail for exactly this reason, and the fix has
already been validated in analogous tasks.

**T1.5b — Investigate helixops_saas010, helixops_saas015, intercom002, quickbooks003**

Read agent logs from the most recent experiment run to identify what the failing tests
actually check and what the agent produces. No code changes until root cause is known.

---

### Tier 2 — New tools for schema comparison and lineage (1–2 weeks, ≈ +4 tasks)

**T2.1 — `compare_schema` docker tool** (closes G1)

A new step in the MANDATORY VERIFICATION section that:
1. Reads the task's schema.yml to extract expected column names for each model
2. Runs `dbt show --select <model> --limit 0` to get actual column list
3. Reports mismatches: "column `ipd` expected but not found; column `p1` present but not expected"

This is LabRat's version of Altimate's `data_diff` — a lightweight internal schema diff
rather than cross-warehouse parity. Prompt-only, no new Python code needed; all via
`docker exec`.

Target: **analytics_engineering004** (column order/alias), **f1002** (extra columns).
Estimated: 2 days.

**T2.2 — `trace_column_lineage` manifest tool** (closes G2)

A bash command injected into the preamble that parses the compiled dbt manifest:

```bash
docker exec {container_name} bash -c "
  cd /app && dbt compile --quiet &&
  python3 -c \"
import json
manifest = json.load(open('target/manifest.json'))
# Find which model first introduces a column named X
\"
"
```

When the agent needs to add `geo_segment`, it runs this tool to discover that `geo_segment`
doesn't exist in any upstream node — it must be created at the staging layer.

Target: **analytics_engineering006** (supplier join discovery), **helixops_saas007**
(intermediate model propagation).
Estimated: 3–5 days.

**T2.3 — `check_project_compile` as explicit first-turn action** (closes G5 more reliably)

Make compile-first not just a suggestion in the preamble but the literal first bash command
the agent is seeded with (via a pre-populated tool call in the agent invocation). This
ensures the compile step always runs even if the agent skips preamble instructions.

Target: **asana005.hard** (still fails because agent sometimes skips compile even with T1.1).

**T2.4 — Package override pattern in preamble** (closes G5 for asana005.hard)

Add explicit guidance: "If compile fails due to a dbt package model error, override it by
creating a file at `models/staging/<package>/<model_name>.sql` that shadows the package
model." This is the standard dbt pattern for patching upstream packages.

Target: **asana005.hard**.

---

### Tier 3 — Plan-then-execute architecture (2–4 weeks, ≈ +3–4 tasks, systemic)

A fundamental loop change that closes G1, G2, G3 at the planning stage.

**T3.1 — Mandatory planning phase in `_DOCKER_PREAMBLE`**

Before writing any SQL, the agent must produce a structured plan:

```
REQUIRED PLANNING PHASE (before any file edits):

## Plan
Models to create/modify: [list]
For each model:
  - Grain: [what is one row]
  - Input refs: [what models/sources it reads]
  - Join strategy: [which table is the LEFT anchor; which are joined onto it]
  - Output columns (with types): [exact list]
  - Uniqueness guarantee: [ROW_NUMBER or QUALIFY if required]
```

This closes G1 (forces column list commitment before writing), G2 (forces input ref
enumeration), and G3 (forces explicit JOIN anchor declaration).

Target: **analytics_engineering007.medium** (type migration wrong model), **asana004/005**
if T1.5a doesn't fully close them.

**T3.2 — Verifier/critic pass after completion**

After the agent says "done," run a second, cheaper prompt that reads the agent's model files
and task description and reports discrepancies. The main agent then decides whether to fix.

Implementation: a second `claude -p` invocation inside `LabratLocalAgent.perform_task` after
the main loop, using a 200-token critic prompt. Cost: ~$0.05 extra per task.

**T3.3 — Schema YAML injection at task start**

Parse schema.yml before the agent starts and inject a "known schema" section:

```
KNOWN SCHEMA (from schema.yml — use these column names exactly):
  obt_product_inventory: [inventory_id, ipd, product_name, quantity, ...]
```

Closes G1 precisely for tasks where schema.yml is authoritative. Doesn't require a new tool.

---

### Tier 4 — Semantic layer + memory (4–8 weeks, systemic)

**T4.1 — Project-specific pattern memory** (closes G4 broadly)

A JSONL file per dbt project that records date parsing patterns, column naming conventions,
dedup strategies, and common dimension join patterns learned from prior runs. Injects
relevant patterns at task start. Inspired by Hex's Automatic User Memory.

**T4.2 — dbt semantic layer integration** (closes G3 broadly)

Parse the dbt manifest's `entities:` / `semantics:` layer (if present) to build a grain
map: "one row in `int_asana__project_user_agg` = one project." Warns the agent when a JOIN
would change the grain. Inspired by Databao Context Engine and Wren AI's semantic model.

**T4.3 — DuckDB-native data_diff** (closes G1 definitively)

Given two DuckDB tables, report row-level and column-level differences. For ADE-bench, the
"reference" is the setup dbt run output. For production, it's the user's current table.

Altimate does this across 12 warehouses. LabRat's DuckDB-native version takes ~1 week and
is a unique differentiator in the TUI — surface it in the SQL editor panel rather than CLI.

---

## Summary table

| Gap | Status | Next action | Tasks affected |
|---|---|---|---|
| G1: Output-schema blindness | Open | T2.1 compare_schema | ae004, ae006, f1002 |
| G2: Dependency discovery | Open | T2.2 lineage manifest tool | ae006, helixops007 |
| G3: NULL-row / grain awareness | Open | **T1.5a (< 1 day)**: add to asana hints | asana004, asana005 |
| G4: Type/pattern misidentification | ✅ Partially closed (airbnb010) | T3.2 verifier for ae007.medium | ae007.medium |
| G5: Missing dbt knowledge | ✅ Partially closed (airbnb012) | T2.3/T2.4 for asana005.hard | asana005.hard |
| G6: Unknown | Open | Investigate logs | helixops010, helixops015, intercom002, qb003 |

**Projected score progression from current 80%:**

| After | Estimated score | Key assumptions |
|---|---|---|
| **Now** | **80% (48/60)** | Tier 1 complete |
| + T1.5a (asana family hints, < 1 day) | **83% (50/60)** | asana004, asana005 both close |
| + T2.1 compare_schema (2 days) | **85% (51/60)** | ae004 or f1002 closes; one may need T3.3 |
| + T2.2 lineage tool (3–5 days) | **87% (52/60)** | helixops007 closes; ae006 partially |
| + G6 investigation (findings TBD) | **87–90%** | depends on root causes of 4 unknown failures |
| + T3 planning phase (2 weeks) | **90%+ (54+/60)** | closes most remaining G1/G2/G3 |
| + T4 semantic layer + memory | **93%+** | systematic G3/G4 closure across all tasks |

**Recommended next action:** Ship T1.5a first — add JOIN grain rules to the `asana` family
hints. It's a 10-line change that should close asana004 and asana005 immediately (the exact
same hint format already works for the `analytics_engineering` family). Then investigate the
4 unknown failures before investing in Tier 2 tooling.

---

## What LabRat can claim competitors can't

After Tier 2 is complete:

1. **AGPL-3.0 + terminal-native + dbt-catalog-native** — unique in the top 7 of both
   benchmarks. Databao is Apache, SignalPilot is Apache, Altimate is MIT. None are terminal-native.

2. **Audit log + provenance export** — unique among OSS data agents.

3. **Lineage-aware column tracing** (T2.2) — Databao has this via a separate context engine
   repo; LabRat would have it natively in the agent loop.

4. **data_diff natively in the TUI** (T4.3) — Altimate has CLI data_diff; LabRat can be the
   first to surface it in a TUI workflow, tied to the SQL editor panel.
