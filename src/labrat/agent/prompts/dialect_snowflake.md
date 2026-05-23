# Snowflake Dialect

Active connection: **Snowflake**

## Key Functions

### Date / Time
- Current timestamp: `CURRENT_TIMESTAMP()` or `GETDATE()`
- Date truncation: `DATE_TRUNC('MONTH', ts)` — unit is a quoted string
- Date diff: `DATEDIFF('day', start_date, end_date)` (end minus start)
- Date add: `DATEADD('day', 7, date_col)` or `date_col + 7` (integers add days for DATE)
- Extract parts: `EXTRACT(year FROM ts)`, `DATE_PART('month', ts)`, `YEAR(ts)`, `MONTH(ts)`
- To string: `TO_CHAR(ts, 'YYYY-MM-DD')` or `TO_VARCHAR(ts, 'YYYY-MM-DD')`
- Parse: `TO_DATE('2024-01-15', 'YYYY-MM-DD')`, `TO_TIMESTAMP_NTZ('2024-01-15 00:00:00')`

### Window Functions
- Full standard window function support.
- QUALIFY is supported: `QUALIFY ROW_NUMBER() OVER (...) = 1`
- `RATIO_TO_REPORT(col) OVER (PARTITION BY ...)` — fraction of total within partition

### Semi-Structured / Variant
- VARIANT type: stores JSON/XML/Avro natively.
- Extract: `col:key` (colon notation) → VARIANT; `col:key::STRING` → cast result
- Array access: `col[0]` (0-based)
- `PARSE_JSON('{"key": "val"}')` → VARIANT
- `OBJECT_CONSTRUCT('k1', v1, 'k2', v2)` → VARIANT object
- `FLATTEN(col)` — table function to expand nested arrays

### Type Casting
- Cast syntax: `val::INTEGER`, `val::STRING`, `CAST(val AS NUMBER)`
- Snowflake types: `NUMBER(p,s)`, `FLOAT`, `VARCHAR`, `STRING`, `TEXT`, `BOOLEAN`, `DATE`, `TIME`, `TIMESTAMP_NTZ`, `TIMESTAMP_TZ`, `VARIANT`, `ARRAY`, `OBJECT`
- `TRY_CAST(val AS INTEGER)` — returns NULL on failure

### Aggregates
- Approximate distinct: `APPROX_COUNT_DISTINCT(col)` (HyperLogLog)
- Median: `MEDIAN(col)` or `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY col)`
- String aggregation: `LISTAGG(col, ', ') WITHIN GROUP (ORDER BY col)`
- Array aggregation: `ARRAY_AGG(col)`

## Common Pitfalls
- **Identifiers default to UPPERCASE** — unquoted identifiers are folded to upper case. Use double-quotes to preserve case: `"myColumn"`.
- Virtual warehouses (compute) and storage are separate — warehouse size affects query speed.
- `TIME_TRAVEL` retention (default 1 day for transient tables, 90 days for permanent) — use `AT(TIMESTAMP => ...)` to query historical data.
- `CLUSTERING KEY` improves performance on large tables with selective predicates.
- `FLATTEN` + lateral join to unnest VARIANT arrays — different syntax from standard SQL.

## Analytical Patterns

### Top-N per group (with QUALIFY)
```sql
SELECT *
FROM   my_table
QUALIFY ROW_NUMBER() OVER (PARTITION BY group_col ORDER BY metric DESC) = 1;
```

### Query semi-structured JSON
```sql
SELECT src:customer_id::STRING  AS customer_id,
       src:order:total::FLOAT   AS order_total
FROM   raw_events;
```

### Time travel query
```sql
SELECT * FROM my_table AT(TIMESTAMP => '2024-01-01 00:00:00'::TIMESTAMP);
```
