# Amazon Redshift Dialect

Active connection: **Amazon Redshift**

## Key Functions

### Date / Time
- Current timestamp: `GETDATE()` or `SYSDATE`
- Date truncation: `DATE_TRUNC('month', ts)`
- Date diff: `DATEDIFF(day, start_date, end_date)` (integer result)
- Date add: `DATEADD(day, 7, date_col)`
- Extract parts: `EXTRACT(year FROM ts)`, `DATE_PART('month', ts)`
- To string: `TO_CHAR(ts, 'YYYY-MM-DD')`

### Window Functions
- Full standard window function support.
- `MEDIAN(col)` — shorthand for `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY col)`
- `LISTAGG(col, ',') WITHIN GROUP (ORDER BY col)` for string aggregation (similar to `STRING_AGG`)

### Array / JSON (Redshift SUPER)
- SUPER type: `JSON_PARSE('{"key": "val"}')` → SUPER
- Object access: `col.key` (dot notation for SUPER columns)
- Array access: `col[0]` (0-based for SUPER arrays)
- Extract as text: `JSON_EXTRACT_PATH_TEXT(col, 'key')`

### Type Casting
- Cast syntax: `val::INTEGER`, `CAST(val AS INTEGER)`
- Redshift types: `INTEGER`, `BIGINT`, `FLOAT`, `DECIMAL(p,s)`, `VARCHAR(n)`, `TIMESTAMPTZ`
- `NVL(col, default)` — Redshift alias for `COALESCE`

### Aggregates
- Approximate distinct: `APPROXIMATE COUNT(DISTINCT col)` (HyperLogLog, very fast on large tables)
- Median: `MEDIAN(col)` or `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY col)`
- Percentiles: `PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY col)` — exact; `APPROXIMATE PERCENTILE_DISC(0.9) WITHIN GROUP (ORDER BY col)` — fast

## Common Pitfalls
- **No QUALIFY clause** — wrap in a subquery to filter window results.
- Redshift is a columnar store: avoid `SELECT *` on wide tables in production.
- Distribution style (`DISTSTYLE KEY`, `ALL`, `EVEN`) affects join performance; check `SVV_TABLE_INFO` for distribution info.
- `SORTKEY` columns filter efficiently; ensure range scans use sortkey-leading predicates.
- `VARCHAR` max length is 65535 bytes; use `VARCHAR(max)` cautiously.
- Redshift does not enforce primary key or foreign key constraints — they are metadata hints only.

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

### Approximate distinct count (large tables)
```sql
SELECT APPROXIMATE COUNT(DISTINCT user_id) AS approx_users
FROM   events
WHERE  event_date >= '2024-01-01';
```

### Unload to S3
```sql
UNLOAD ('SELECT * FROM my_table')
TO 's3://my-bucket/export/'
IAM_ROLE 'arn:aws:iam::...'
FORMAT PARQUET;
```
