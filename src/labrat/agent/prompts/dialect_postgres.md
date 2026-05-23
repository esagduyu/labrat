# PostgreSQL Dialect

Active connection: **PostgreSQL**

## Key Functions

### Date / Time
- Current timestamp: `NOW()` or `CURRENT_TIMESTAMP`
- Date truncation: `DATE_TRUNC('month', ts)` — returns a `TIMESTAMPTZ`
- Date diff: `EXTRACT(epoch FROM (end_ts - start_ts)) / 86400` (no native `DATEDIFF`)
- Date arithmetic: `date_col + INTERVAL '7 days'`
- Extract parts: `EXTRACT(year FROM ts)`, `EXTRACT(month FROM ts)`
- To string: `TO_CHAR(ts, 'YYYY-MM-DD')`

### Window Functions
- Full support for all standard window functions.
- `FILTER (WHERE ...)` clause on aggregates: `COUNT(*) FILTER (WHERE status = 'ok')`

### Array / JSON
- Array literal: `ARRAY[1, 2, 3]`
- Array indexing: `arr[1]` (1-based)
- Array contains: `val = ANY(arr)` or `arr @> ARRAY[val]`
- JSON (unstructured): `col->'key'` → JSON; `col->>'key'` → TEXT
- JSONB (indexed): `col @> '{"key": "value"}'::jsonb`
- Extract nested: `col#>'{a,b}'` → JSON; `col#>>'{a,b}'` → TEXT

### Type Casting
- Cast syntax: `val::INTEGER`, `val::TEXT`, `val::TIMESTAMPTZ`
- Alternative: `CAST(val AS INTEGER)`
- Null-safe equality: `IS NOT DISTINCT FROM`

### Aggregates
- Approximate distinct: `COUNT(DISTINCT col)` (no built-in approx; use `pg_catalog.pg_stats` for estimates)
- Median: `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY col)`
- String aggregation: `STRING_AGG(col, ', ' ORDER BY sort_col)`

## Common Pitfalls
- String comparison is case-sensitive; use `LOWER(col) = LOWER(val)` or `ILIKE` for case-insensitive search.
- No `LIMIT` on `DELETE`/`UPDATE` — use a CTE with `RETURNING` or a subquery with `ctid`.
- `SERIAL` / `BIGSERIAL` pseudo-types for auto-increment; prefer `GENERATED ALWAYS AS IDENTITY` in Postgres 10+.
- `EXPLAIN (ANALYZE, BUFFERS)` gives the most detail for query tuning.
- Schema search path matters: `SET search_path TO myschema, public;`

## Analytical Patterns

### Top-N per group
```sql
SELECT *
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY group_col ORDER BY metric DESC) AS rn
    FROM   my_table
) t
WHERE rn = 1;
```

### Running total
```sql
SELECT date_col,
       amount,
       SUM(amount) OVER (ORDER BY date_col) AS running_total
FROM   my_table;
```

### Conditional aggregation
```sql
SELECT SUM(amount) FILTER (WHERE status = 'completed') AS completed_total,
       COUNT(*)    FILTER (WHERE status = 'failed')    AS failed_count
FROM   orders;
```
