-- Read-only. Run in deployment to confirm cycle-id shape so the rollover
-- service can be adapted.
\set ON_ERROR_STOP on
SET search_path TO portfolioauditreview, public;

\echo '=== A. review_cycles column types ==='
SELECT column_name, data_type, udt_name, character_maximum_length, is_nullable
FROM   information_schema.columns
WHERE  table_schema = 'portfolioauditreview'
AND    table_name   = 'review_cycles'
ORDER  BY ordinal_position;

\echo
\echo '=== B. portfolio_companies.review_cycle_id type ==='
SELECT column_name, data_type, udt_name, character_maximum_length
FROM   information_schema.columns
WHERE  table_schema = 'portfolioauditreview'
AND    table_name   = 'portfolio_companies'
AND    column_name  = 'review_cycle_id';

\echo
\echo '=== C. entities.review_cycle type ==='
SELECT column_name, data_type, udt_name, character_maximum_length
FROM   information_schema.columns
WHERE  table_schema = 'portfolioauditreview'
AND    table_name   = 'entities'
AND    column_name  = 'review_cycle';

\echo
\echo '=== D. files.review_cycle_id type (if column exists) ==='
SELECT column_name, data_type, udt_name, character_maximum_length
FROM   information_schema.columns
WHERE  table_schema = 'portfolioauditreview'
AND    table_name   = 'files'
AND    column_name  = 'review_cycle_id';

\echo
\echo '=== E. Distinct review_cycle_id values actually used in portfolio_companies ==='
SELECT review_cycle_id, count(*) AS n
FROM   portfolio_companies
GROUP  BY review_cycle_id
ORDER  BY n DESC
LIMIT  20;

\echo
\echo '=== F. Distinct review_cycle values actually used in entities ==='
SELECT review_cycle, count(*) AS n
FROM   entities
GROUP  BY review_cycle
ORDER  BY n DESC
LIMIT  20;

\echo
\echo '=== G. Cross-check: do portfolio_companies.review_cycle_id values match review_cycles.id (UUID) or review_cycles.name (label)? ==='
SELECT 'match_by_id'   AS kind, count(*) AS n
FROM   portfolio_companies pc
JOIN   review_cycles rc ON rc.id::text = pc.review_cycle_id
UNION ALL
SELECT 'match_by_name' AS kind, count(*) AS n
FROM   portfolio_companies pc
JOIN   review_cycles rc ON rc.name = pc.review_cycle_id
UNION ALL
SELECT 'no_match'      AS kind, count(*) AS n
FROM   portfolio_companies pc
WHERE  NOT EXISTS (SELECT 1 FROM review_cycles rc WHERE rc.id::text = pc.review_cycle_id OR rc.name = pc.review_cycle_id);

\echo
\echo '=== H. Same cross-check for entities.review_cycle ==='
SELECT 'match_by_id'   AS kind, count(*) AS n
FROM   entities e
JOIN   review_cycles rc ON rc.id::text = e.review_cycle
UNION ALL
SELECT 'match_by_name' AS kind, count(*) AS n
FROM   entities e
JOIN   review_cycles rc ON rc.name = e.review_cycle
UNION ALL
SELECT 'no_match'      AS kind, count(*) AS n
FROM   entities e
WHERE  NOT EXISTS (SELECT 1 FROM review_cycles rc WHERE rc.id::text = e.review_cycle OR rc.name = e.review_cycle);

\echo
\echo '=== I. Sample portfolio_companies rows (one per cycle) ==='
SELECT DISTINCT ON (review_cycle_id)
       review_cycle_id, company_id, name, review_stage, org_chart_file_id
FROM   portfolio_companies
ORDER  BY review_cycle_id, id;

doppler run -- psql "$DATABASE_URL" -f scripts/hygiene_script.sql

doppler run -- psql "$POSTGRES_HOST" -f scripts/hygiene_script.sql