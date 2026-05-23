# DuckDB Dialect

Active connection: **DuckDB**

## Key Functions

### Date / Time
- Current timestamp: `CURRENT_TIMESTAMP` or `NOW()`
- Date truncation: `DATE_TRUNC('month', ts)` — returns a `TIMESTAMP`
- Date diff: `DATEDIFF('day', start_date, end_date)` (integer result)
- Date arithmetic: `date_col + INTERVAL '7 days'`
- Extract parts: `EXTRACT(year FROM ts)` or `year(ts)`, `month(ts)`, `day(ts)`
- Strftime: `STRFTIME(ts, '%Y-%m-%d')`

### Window Functions
- Standard SQL window syntax; all major functions supported: `ROW_NUMBER()`, `RANK()`, `DENSE_RANK()`, `LAG()`, `LEAD()`, `FIRST_VALUE()`, `LAST_VALUE()`, `NTH_VALUE()`, `NTILE()`
- Frame specification: `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`

### Array / JSON
- Array literal: `[1, 2, 3]`
- Array indexing: `arr[1]` (1-based)
- Array functions: `array_length(arr)`, `list_contains(arr, val)`, `list_aggregate(arr, 'sum')`
- JSON extraction: `json_extract(col, '$.key')` → JSON; `json_extract_string(col, '$.key')` → VARCHAR
- JSON from string: `col::JSON`

### Type Casting
- Cast syntax: `val::INTEGER`, `val::VARCHAR`, `val::TIMESTAMP`
- Alternative: `CAST(val AS INTEGER)`
- Numeric: `INTEGER`, `BIGINT`, `DOUBLE`, `DECIMAL(p, s)`
- Null-safe equality: DuckDB uses `IS NOT DISTINCT FROM` (standard SQL)

### Aggregates
- Approximate distinct: `APPROX_COUNT_DISTINCT(col)`
- Median: `MEDIAN(col)` or `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY col)`
- String aggregation: `STRING_AGG(col, ', ')` or `LIST_AGG(col)`

## Common Pitfalls
- DuckDB column names are case-insensitive by default; double-quote to preserve case.
- `COUNT(*)` is fast; `COUNT(DISTINCT col)` on large columns is slower — use `APPROX_COUNT_DISTINCT` if exactness is not required.
- DuckDB reads Parquet, CSV, and JSON directly: `SELECT * FROM 'file.parquet'`.
- Use `QUALIFY` for post-window filtering instead of a subquery: `SELECT ..., ROW_NUMBER() OVER (...) AS rn ... QUALIFY rn = 1`.

## Analytical Patterns

### Top-N per group
```sql
SELECT *
FROM   my_table
QUALIFY ROW_NUMBER() OVER (PARTITION BY group_col ORDER BY metric DESC) = 1;
```

### Running total
```sql
SELECT date_col,
       amount,
       SUM(amount) OVER (ORDER BY date_col ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total
FROM   my_table;
```

### Pivot (wide from long)
```sql
PIVOT my_table ON category USING SUM(amount) GROUP BY date_col;
```
