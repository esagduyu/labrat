# MySQL Dialect

Active connection: **MySQL**

## Key Functions

### Date / Time
- Current timestamp: `NOW()` or `CURRENT_TIMESTAMP`
- Date truncation: no native `DATE_TRUNC` — use: `DATE_FORMAT(ts, '%Y-%m-01')` for month start
- Date diff: `DATEDIFF(end_date, start_date)` (days, end minus start)
- Date add: `DATE_ADD(date_col, INTERVAL 7 DAY)` or `date_col + INTERVAL 7 DAY`
- Extract parts: `YEAR(ts)`, `MONTH(ts)`, `DAY(ts)`, `HOUR(ts)`, `EXTRACT(YEAR FROM ts)`
- To string: `DATE_FORMAT(ts, '%Y-%m-%d')`, `DATE_FORMAT(ts, '%Y-%m-%d %H:%i:%s')`
- Parse: `STR_TO_DATE('2024-01-15', '%Y-%m-%d')`

### Window Functions
- Full support for window functions added in MySQL 8.0+.
- No QUALIFY — wrap in a subquery.

### JSON
- JSON type supported from MySQL 5.7.8+.
- Extract: `col->'$.key'` → JSON (quoted); `col->>'$.key'` → unquoted string
- Equivalent: `JSON_EXTRACT(col, '$.key')` → JSON; `JSON_UNQUOTE(JSON_EXTRACT(col, '$.key'))` → string
- Check type: `JSON_TYPE(col)`
- Contains: `JSON_CONTAINS(col, '"value"', '$.key')`

### Type Casting
- Cast syntax: `CAST(val AS UNSIGNED)`, `CAST(val AS CHAR)`, `CONVERT(val, UNSIGNED)`
- MySQL types: `TINYINT`, `SMALLINT`, `INT`, `BIGINT`, `FLOAT`, `DOUBLE`, `DECIMAL(p,s)`, `CHAR(n)`, `VARCHAR(n)`, `TEXT`, `DATE`, `DATETIME`, `TIMESTAMP`
- `IFNULL(col, default)` — MySQL alias for `COALESCE` with two args

### Aggregates
- No approximate distinct — use `COUNT(DISTINCT col)` (exact).
- Median: no built-in; use `PERCENTILE_CONT` (MySQL 8.0+) or a variable-based approach.
- String aggregation: `GROUP_CONCAT(col ORDER BY col SEPARATOR ', ')` — note 1024-byte limit by default; increase with `group_concat_max_len`.

## Common Pitfalls
- **No FULL OUTER JOIN** — simulate with `LEFT JOIN UNION ALL RIGHT JOIN WHERE left_col IS NULL`.
- String comparison is case-insensitive by default (depends on collation); use `BINARY` for case-sensitive: `WHERE BINARY col = 'Value'`.
- MySQL does not support `EXCEPT` or `INTERSECT` before MySQL 8.0.31 — use `NOT IN` / `JOIN` instead.
- `LIMIT` on subqueries: MySQL allows `LIMIT` in subqueries but not in `IN`/`ALL`/`ANY`/`SOME` subqueries.
- Auto-increment gaps are normal after rollbacks; do not treat `AUTO_INCREMENT` values as contiguous.
- `ONLY_FULL_GROUP_BY` mode (default in MySQL 5.7.5+): all non-aggregated SELECT columns must appear in GROUP BY.

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

### Truncate date to month
```sql
SELECT DATE_FORMAT(created_at, '%Y-%m-01') AS month_start,
       COUNT(*)                             AS event_count
FROM   my_table
GROUP BY month_start
ORDER BY month_start;
```

### String aggregation with limit awareness
```sql
SELECT group_col,
       GROUP_CONCAT(name ORDER BY name SEPARATOR ', ') AS names
FROM   my_table
GROUP BY group_col;
```
