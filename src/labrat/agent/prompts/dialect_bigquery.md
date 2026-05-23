# Google BigQuery Dialect

Active connection: **Google BigQuery**

## Key Functions

### Date / Time
- Current timestamp: `CURRENT_TIMESTAMP()` (with parentheses)
- Date truncation: `DATE_TRUNC(date_col, MONTH)` — unit is unquoted keyword
- Timestamp truncation: `TIMESTAMP_TRUNC(ts, MONTH)`
- Date diff: `DATE_DIFF(end_date, start_date, DAY)` (end minus start)
- Date arithmetic: `DATE_ADD(date_col, INTERVAL 7 DAY)`
- Extract parts: `EXTRACT(YEAR FROM ts)`, `EXTRACT(MONTH FROM ts)`
- Format: `FORMAT_DATE('%Y-%m-%d', date_col)`, `FORMAT_TIMESTAMP('%Y-%m-%d %H:%M', ts)`
- Parse: `PARSE_DATE('%Y%m%d', string_col)`, `PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S', string_col)`

### Window Functions
- Full standard window function support.
- QUALIFY is supported: `QUALIFY ROW_NUMBER() OVER (...) = 1`

### Array / JSON
- Array literal: `[1, 2, 3]`
- Array functions: `ARRAY_LENGTH(arr)`, `ARRAY_AGG(col)`, `UNNEST(arr)` in FROM clause
- JSON: BigQuery uses `JSON` type; extract with `JSON_VALUE(col, '$.key')` → STRING
- `JSON_QUERY(col, '$.key')` → JSON (returns JSON string)
- Struct/Record: `col.field` dot notation for nested STRUCT fields

### Type Casting
- Cast syntax: `CAST(val AS INT64)`, `CAST(val AS STRING)`, `CAST(val AS FLOAT64)`
- Safe cast (returns NULL on failure): `SAFE_CAST(val AS INT64)`
- BigQuery types: `INT64`, `FLOAT64`, `NUMERIC`, `BIGNUMERIC`, `STRING`, `BYTES`, `BOOL`, `TIMESTAMP`, `DATE`, `DATETIME`, `TIME`

### Aggregates
- Approximate distinct: `APPROX_COUNT_DISTINCT(col)`
- Median: `APPROX_QUANTILES(col, 100)[OFFSET(50)]`
- String aggregation: `STRING_AGG(col, ', ' ORDER BY col)`
- Array aggregation: `ARRAY_AGG(col IGNORE NULLS ORDER BY col)`

## Common Pitfalls
- **Identifiers are case-sensitive** for column/table names; use backticks to quote: `` `project.dataset.table` ``
- **Partitioned tables** — always include a partition filter on `_PARTITIONDATE` or the partition column to avoid full-table scans.
- BigQuery charges by bytes scanned — use `SELECT col1, col2` not `SELECT *`.
- No `LIMIT` on DML; BigQuery DML (`UPDATE`, `MERGE`) is transactional but expensive at scale.
- Timestamps are UTC; `DATETIME` is naive (no timezone). Prefer `TIMESTAMP`.
- `STRUCT` and `ARRAY` types are first-class; use `UNNEST` to flatten repeated fields.

## Analytical Patterns

### Top-N per group (with QUALIFY)
```sql
SELECT *
FROM   `project.dataset.my_table`
QUALIFY ROW_NUMBER() OVER (PARTITION BY group_col ORDER BY metric DESC) = 1;
```

### Date-range scan on partitioned table
```sql
SELECT *
FROM   `project.dataset.events`
WHERE  _PARTITIONDATE BETWEEN '2024-01-01' AND '2024-03-31';
```

### Flatten a repeated field
```sql
SELECT id, item
FROM   `project.dataset.orders`,
UNNEST(line_items) AS item;
```
