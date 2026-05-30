# Spider2-DBT Benchmark (stub)

Implementation is deferred to a follow-on spec. This README captures design intent
so the unified-suite decisions don't get forgotten.

## When implemented

- Tasks come from `~/repos/Spider2/spider2-dbt/examples/spider2-dbt.jsonl` (67 entries).
- `Spider2DbtSuite.run_trial()` will copy the dbt project to `scratch_dir`, build a
  `ToolContext` over the starter DuckDB, invoke the agent via `LabRatAgentDriver`
  (extracted in Phase 4), then table-match against
  `~/repos/Spider2/spider2-dbt/evaluation_suite/gold/<task_id>/<db>.duckdb` using
  the `duckdb_match` / `tables_match` logic ported from Spider2's `evaluate.py`.
- `artifact = {"type": "duckdb_state", "payload": {"db_path": "..."}}`.
- Dataset triage (Fivetran `_tmp` unsolvability, allowlist for "fair score") is a
  Spider2-spec concern, not architectural.

## See also

- `docs/superpowers/specs/2026-05-28-unified-benchmark-suite-design.md` — protocol
- Memory: `project_spider2_revisit.md`, `project_spider2_autoresearch.md`
