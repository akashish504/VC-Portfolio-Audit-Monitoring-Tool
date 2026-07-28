-- Read-only. Lists every FK pointing at review_cycles.id, plus any other
-- column whose name suggests it holds a review_cycle id, so we know exactly
-- what the rename migration must touch.

\set ON_ERROR_STOP on
SET search_path TO portfolioauditreview, public;

\echo '=== 1. FK constraints targeting review_cycles ==='
SELECT tc.table_schema, tc.table_name, kcu.column_name,
       tc.constraint_name,
       rc.update_rule, rc.delete_rule
FROM   information_schema.table_constraints tc
JOIN   information_schema.key_column_usage kcu
  ON   kcu.constraint_name   = tc.constraint_name
 AND   kcu.table_schema      = tc.table_schema
JOIN   information_schema.referential_constraints rc
  ON   rc.constraint_name    = tc.constraint_name
 AND   rc.constraint_schema  = tc.table_schema
JOIN   information_schema.constraint_column_usage ccu
  ON   ccu.constraint_name   = rc.unique_constraint_name
 AND   ccu.constraint_schema = rc.unique_constraint_schema
WHERE  tc.constraint_type = 'FOREIGN KEY'
AND    ccu.table_schema   = 'portfolioauditreview'
AND    ccu.table_name     = 'review_cycles'
ORDER  BY tc.table_schema, tc.table_name;

\echo
\echo '=== 2. Columns named like a review_cycle id (any schema) ==='
SELECT table_schema, table_name, column_name, data_type, character_maximum_length
FROM   information_schema.columns
WHERE  column_name ILIKE '%review_cycle%'
   OR  column_name = 'review_cycle_id'
ORDER  BY table_schema, table_name, column_name;

\echo
\echo '=== 3. Row counts for tables we may need to touch ==='
SELECT 'review_cycles'             AS tbl, count(*) FROM review_cycles
UNION ALL SELECT 'portfolio_companies',     count(*) FROM portfolio_companies
UNION ALL SELECT 'entities',                count(*) FROM entities
UNION ALL SELECT 'files',                   count(*) FROM files
UNION ALL SELECT 'review_cycle_view_audit', count(*) FROM review_cycle_view_audit
UNION ALL SELECT 'export_jobs',             count(*) FROM export_jobs
UNION ALL SELECT 'financial_metric_reconciliation', count(*) FROM financial_metric_reconciliation
UNION ALL SELECT 'financial_data_snowflake',count(*) FROM financial_data_snowflake;

\echo
\echo '=== 4. Current review_cycles rows (full) ==='
SELECT id, name, status, starts_at::date, ends_at::date, meta
FROM   review_cycles
ORDER  BY starts_at;

\echo
\echo '=== 5. Preview: what the new ids would be ==='
-- Parses name "CY 24 - FY 25" -> "CY24-FY25"
SELECT id AS old_id,
       name,
       regexp_replace(name, '\s+', '', 'g') AS new_id_candidate,
       CASE
         WHEN regexp_replace(name, '\s+', '', 'g') ~ '^CY[0-9]{2}-FY[0-9]{2}$'
           THEN 'OK'
         ELSE 'CANNOT PARSE — needs manual decision'
       END AS parse_status
FROM   review_cycles
ORDER  BY starts_at;

\echo
\echo '=== 6. Any review_cycle_view_audit rows that reference cycles? ==='
SELECT review_cycle_id, count(*) AS n
FROM   review_cycle_view_audit
WHERE  review_cycle_id IS NOT NULL
GROUP  BY review_cycle_id
ORDER  BY n DESC
LIMIT  10;

\echo
\echo '=== 7. Any export_jobs rows referencing cycles? ==='
SELECT review_cycle_id, count(*) AS n
FROM   export_jobs
WHERE  review_cycle_id IS NOT NULL
GROUP  BY review_cycle_id
ORDER  BY n DESC
LIMIT  10;

\echo
\echo '=== 8. Empty-string review_cycle entities (already a hygiene issue) ==='
SELECT count(*) AS empty_review_cycle_entities
FROM   entities
WHERE  review_cycle = '' OR review_cycle IS NULL;


