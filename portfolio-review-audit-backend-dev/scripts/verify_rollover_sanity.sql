-- =====================================================================
-- Read-only sanity check for the review-cycle rollover.
--
-- Usage in deployment shell (PGPASSWORD/POSTGRES_* assumed in env):
--
--   psql "$DATABASE_URL" \
--        -v source_id="'CY23-FY24'" -v target_id="'CY24-FY25'" \
--        -f scripts/verify_rollover_sanity.sql
--
-- Or interactively:
--   \set source_id 'CY23-FY24'
--   \set target_id 'CY24-FY25'
--   \i scripts/verify_rollover_sanity.sql
--
-- Performs NO writes. Each section is a SELECT — expected "good" results
-- are noted in the comment above the query.
-- =====================================================================

\set ON_ERROR_STOP on
\timing on
SET search_path TO portfolioauditreview, public;

\echo
\echo '================ 1. All review cycles + company counts ================'
SELECT rc.id,
       rc.name,
       rc.status,
       rc.starts_at::date AS starts,
       rc.ends_at::date   AS ends,
       (SELECT count(*) FROM portfolio_companies pc WHERE pc.review_cycle_id = rc.id) AS companies,
       rc.meta
FROM   review_cycles rc
ORDER  BY rc.starts_at NULLS LAST;

\echo
\echo '================ 2. Source / target cycle rows ================'
-- Both rows MUST exist.  target.meta should include cloned_from = source.
SELECT id, name, status, starts_at::date, ends_at::date, meta
FROM   review_cycles
WHERE  id IN (:source_id, :target_id)
ORDER  BY id;

\echo
\echo '================ 3. Source companies NOT present in target ================'
-- Expected: 0 rows.  Each source company_id must have a row in the target cycle.
SELECT src.company_id, src.name
FROM   portfolio_companies src
WHERE  src.review_cycle_id = :source_id
AND    src.company_id NOT LIKE 'sys-unassigned-%'
AND    NOT EXISTS (
         SELECT 1 FROM portfolio_companies tgt
         WHERE  tgt.review_cycle_id = :target_id
         AND    tgt.company_id      = src.company_id
       );

\echo
\echo '================ 4. Duplicate company_id rows inside target cycle ================'
-- Expected: 0 rows.  Idempotency must prevent multiple target rows per company.
SELECT company_id, count(*) AS n
FROM   portfolio_companies
WHERE  review_cycle_id = :target_id
GROUP  BY company_id
HAVING count(*) > 1;

\echo
\echo '================ 5. Target rows whose review_stage is NOT "Created" ================'
-- Expected: 0 rows.  Rollover must force review_stage = "Created".
SELECT company_id, name, review_stage
FROM   portfolio_companies
WHERE  review_cycle_id = :target_id
AND    (review_stage IS DISTINCT FROM 'Created');

\echo
\echo '================ 6. Metadata field mismatches (source vs target) ================'
-- Expected: 0 rows.  Each of the 7 copied fields must match the source row.
WITH mismatches AS (
  SELECT src.company_id,
         (src.name             IS DISTINCT FROM tgt.name)             AS name_diff,
         (src.contact_name     IS DISTINCT FROM tgt.contact_name)     AS contact_name_diff,
         (src.contact_email_id IS DISTINCT FROM tgt.contact_email_id) AS email_diff,
         (src.contact_contact_id IS DISTINCT FROM tgt.contact_contact_id) AS contact_id_diff,
         (src.partner_email    IS DISTINCT FROM tgt.partner_email)    AS partner_email_diff,
         (src.fund             IS DISTINCT FROM tgt.fund)             AS fund_diff,
         (src.investment_lead  IS DISTINCT FROM tgt.investment_lead)  AS lead_diff
  FROM   portfolio_companies src
  JOIN   portfolio_companies tgt
    ON   tgt.company_id      = src.company_id
   AND   tgt.review_cycle_id = :target_id
  WHERE  src.review_cycle_id = :source_id
)
SELECT *
FROM   mismatches
WHERE  name_diff OR contact_name_diff OR email_diff OR contact_id_diff
    OR partner_email_diff OR fund_diff OR lead_diff;

\echo
\echo '================ 7. Workflow-state leak on copied target companies ================'
-- Expected: 0 rows.  Prior-cycle workflow state must NOT have carried over.
SELECT company_id, audit_status, auditor, in_review_status, due_date
FROM   portfolio_companies
WHERE  review_cycle_id = :target_id
AND    (audit_status     IS NOT NULL
     OR auditor          IS NOT NULL
     OR in_review_status IS NOT NULL
     OR due_date         IS NOT NULL);

\echo
\echo '================ 8. Entity counts per cycle ================'
SELECT review_cycle, count(*) AS entities
FROM   entities
WHERE  review_cycle IN (:source_id, :target_id)
GROUP  BY review_cycle
ORDER  BY review_cycle;

\echo
\echo '================ 9. Target entities still carrying workflow status ================'
-- Expected: 0 rows.  Rollover resets entity.status, reminder/resolved timestamps.
SELECT id, name, status, reminder_1_sent_at, reminder_2_sent_at, resolved_at
FROM   entities
WHERE  review_cycle = :target_id
AND    (status              IS NOT NULL
     OR reminder_1_sent_at  IS NOT NULL
     OR reminder_2_sent_at  IS NOT NULL
     OR resolved_at         IS NOT NULL);

\echo
\echo '================ 10. Target entities with dangling parent_entity_id ================'
-- Expected: 0 rows.  parent_entity_id on a target entity must point to another
-- entity in the same target cycle.
SELECT e.id, e.name, e.parent_entity_id
FROM   entities e
WHERE  e.review_cycle = :target_id
AND    e.parent_entity_id IS NOT NULL
AND    NOT EXISTS (
         SELECT 1 FROM entities p
         WHERE  p.id = e.parent_entity_id
         AND    p.review_cycle = :target_id
       );

\echo
\echo '================ 11. Org chart — target rows reusing the SOURCE File row ================'
-- Expected: 0 rows.  Each cycle must own its own File row (storage_uri shared).
SELECT tgt.company_id, tgt.org_chart_file_id
FROM   portfolio_companies tgt
JOIN   portfolio_companies src
  ON   src.company_id      = tgt.company_id
 AND   src.review_cycle_id = :source_id
WHERE  tgt.review_cycle_id = :target_id
AND    tgt.org_chart_file_id IS NOT NULL
AND    tgt.org_chart_file_id = src.org_chart_file_id;

\echo
\echo '================ 12. Org chart — storage_uri mismatches ================'
-- Expected: 0 rows.  Cloned File row must preserve the source storage_uri.
SELECT src.company_id,
       sf.id AS src_file_id, sf.storage_uri AS src_uri,
       tf.id AS tgt_file_id, tf.storage_uri AS tgt_uri
FROM   portfolio_companies src
JOIN   portfolio_companies tgt
  ON   tgt.company_id      = src.company_id
 AND   tgt.review_cycle_id = :target_id
JOIN   files sf ON sf.id = src.org_chart_file_id
JOIN   files tf ON tf.id = tgt.org_chart_file_id
WHERE  src.review_cycle_id = :source_id
AND    sf.storage_uri IS DISTINCT FROM tf.storage_uri;

\echo
\echo '================ 13. Org chart — source has chart, target missing ================'
-- Expected: 0 rows.
SELECT src.company_id, src.org_chart_file_id AS src_file_id
FROM   portfolio_companies src
JOIN   portfolio_companies tgt
  ON   tgt.company_id      = src.company_id
 AND   tgt.review_cycle_id = :target_id
WHERE  src.review_cycle_id   = :source_id
AND    src.org_chart_file_id IS NOT NULL
AND    tgt.org_chart_file_id IS NULL;

\echo
\echo '================ 14. Most recent rollover audit row ================'
SELECT updated_at, action, meta
FROM   review_cycle_view_audit
WHERE  user_id = 'scheduler'
AND    review_cycle_id = :target_id
ORDER  BY updated_at DESC
LIMIT  1;

\echo
\echo '================ Done. ================'
\echo 'All numbered checks above should return ZERO rows except #1, #2, #8, #14.'
