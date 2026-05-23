# Trino / Presto Dialect

Active connection: **Trino** (compatible with Presto)

## Key Functions

### Date / Time
- Current timestamp: `CURRENT_TIMESTAMP` or `NOW()`
- Date truncation: `DATE_TRUNC('month', ts)`
- Date diff: `DATE_DIFF('day', start_date, end_date)`
- Date add: `DATE_ADD('day', 7, date_col)`
- Extract parts: `EXTRACT(YEAR FROM ts)`, `YEAR(ts)`, `MONTH(ts)`, `DAY(ts)`
- To string: `DATE_FORMAT(ts, '%Y-%m-%d')` (MySQL-style format)
- Parse: `DATE_PARSE('2024-01-15', '%Y-%m-%d')`
- Casting timestamp: `CAST('2024-01-15' AS DATE)`

### Window Functions
- Full standard window function support.
- No QUALIFY — wrap in a subquery to filter on window results.

### Array / JSON
- Array literal: `ARRAY[1, 2, 3]`
- Array functions: `cardinality(arr)`, `contains(arr, val)`, `array_agg(col)`, `flatten(arr_of_arrs)`
- JSON: `json_extract(col, '$.key')` → JSON; `json_extract_scalar(col, '$.key')` → VARCHAR
- `json_array_get(json_arr_string, 0)` — index into a JSON array string

### Type Casting
- Cast syntax: `CAST(val AS INTEGER)`, `CAST(val AS VARCHAR)`
- Trino types: `INTEGER`, `BIGINT`, `DOUBLE`, `DECIMAL(p,s)`, `VARCHAR`, `BOOLEAN`, `DATE`, `TIMESTAMP`, `TIMESTAMP WITH TIME ZONE`
- `TRY_CAST(val AS INTEGER)` — returns NULL on failure

### Aggregates
- Approximate distinct: `APPROX_DISTINCT(col)` (HyperLogLog)
- Median / percentiles: `APPROX_PERCENTILE(col, 0.5)`
- String aggregation: `ARRAY_JOIN(ARRAY_AGG(col), ', ')`

### Cross-Catalog Queries
- Trino can query multiple catalogs in one SQL statement:
  ```sql
  SELECT * FROM hive.sales.orders JOIN iceberg.warehouse.products USING (product_id)
  ```

## Common Pitfalls
- **No QUALIFY** — use a subquery or CTE for post-window filtering.
- Trino is case-insensitive for identifiers by default; exact case is preserved in results.
- `UNNEST` is supported: `SELECT * FROM my_table CROSS JOIN UNNEST(arr_col) AS t(item)`
- `LIMIT` and `OFFSET` are supported; use with `ORDER BY` for deterministic pagination.
- JSON functions use dollar-sign path syntax (`$.key`, not bare `key`).

## Analytical Patterns

### Top-N per group (subquery)
```sql
SELECT *
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY group_col ORDER BY metric DESC) AS rn
    FROM   my_table
) t
WHERE rn = 1;
```

### Approximate distinct count
```sql
SELECT APPROX_DISTINCT(user_id) AS approx_users
FROM   events
WHERE  event_date >= DATE '2024-01-01';
```

### Unnest an array column
```sql
SELECT id, item
FROM   my_table
CROSS JOIN UNNEST(tags) AS t(item);
```
