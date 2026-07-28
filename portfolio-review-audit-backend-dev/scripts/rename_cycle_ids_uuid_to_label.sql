-- =====================================================================
-- One-shot migration: rename review_cycles.id from UUID to "CYxx-FYyy".
--
-- Convention chosen: id = regexp_replace(name, '\s+', '', 'g')
--   "CY 24 - FY 25"  ->  "CY24-FY25"
--
-- Runs inside one transaction.  Order:
--   1. Update every child table (string columns + the real FK in `files`).
--   2. Update parent `review_cycles.id` last.
--   3. Verify FK still resolves and no row references an orphan UUID.
--
-- Performs writes only when --set live=1 is passed.  Default is DRY-RUN
-- (BEGIN ... ROLLBACK) so you can read the row-count output, confirm, then
-- re-run with `-v live=1` to commit.
--
-- Usage:
--   doppler run -- bash -c 'psql "host=$POSTGRES_HOST ..." -f scripts/rename_cycle_ids_uuid_to_label.sql'         # DRY RUN
--   doppler run -- bash -c 'psql "host=$POSTGRES_HOST ..." -v live=1 -f scripts/rename_cycle_ids_uuid_to_label.sql' # COMMIT
-- =====================================================================

\set ON_ERROR_STOP on
\timing on
SET search_path TO portfolioauditreview, public;

\if :{?live}
  \echo '*** LIVE MODE — changes will be COMMITTED ***'
\else
  \set live 0
  \echo '*** DRY-RUN — final ROLLBACK; pass -v live=1 to commit ***'
\endif

BEGIN;

-- ---------------------------------------------------------------------
-- 0. Build the (old_id -> new_id) mapping table for this transaction.
--    Fails loudly if any name cannot be parsed, or if two cycles would
--    map to the same new id.
-- ---------------------------------------------------------------------
CREATE TEMP TABLE _cycle_id_map (
  old_id text PRIMARY KEY,
  new_id text NOT NULL
) ON COMMIT DROP;

INSERT INTO _cycle_id_map (old_id, new_id)
SELECT id,
       regexp_replace(name, '\s+', '', 'g') AS new_id
FROM   review_cycles;

\echo
\echo '--- Mapping: old UUID -> new label ---'
SELECT * FROM _cycle_id_map ORDER BY new_id;

-- Hard fail if any name failed to parse cleanly.
DO $$
DECLARE bad_count int;
BEGIN
  SELECT count(*) INTO bad_count
  FROM   _cycle_id_map
  WHERE  new_id !~ '^CY[0-9]{2}-FY[0-9]{2}$';
  IF bad_count > 0 THEN
    RAISE EXCEPTION 'Found % review_cycles whose name does not parse to CYxx-FYyy; aborting', bad_count;
  END IF;
END $$;

-- Hard fail if the mapping is not 1:1 (two old ids mapping to the same new id).
DO $$
DECLARE dup_count int;
BEGIN
  SELECT count(*) INTO dup_count
  FROM (SELECT new_id FROM _cycle_id_map GROUP BY new_id HAVING count(*) > 1) d;
  IF dup_count > 0 THEN
    RAISE EXCEPTION 'Mapping is not 1:1 — % new_ids would collide; aborting', dup_count;
  END IF;
END $$;

-- Hard fail if any new_id already exists as a different review_cycles.id row.
DO $$
DECLARE collide_count int;
BEGIN
  SELECT count(*) INTO collide_count
  FROM   review_cycles rc
  JOIN   _cycle_id_map m ON m.new_id = rc.id AND m.old_id <> rc.id;
  IF collide_count > 0 THEN
    RAISE EXCEPTION 'Some target ids already exist in review_cycles (collision); aborting';
  END IF;
END $$;

-- ---------------------------------------------------------------------
-- 0b. Drop the only real FK targeting review_cycles for the duration of
--     the transaction.  We recreate it (with the SAME definition) at the
--     end after parent + child rows are aligned.  Atomic — if anything
--     below fails the FK is restored by ROLLBACK.
-- ---------------------------------------------------------------------
ALTER TABLE files DROP CONSTRAINT files_review_cycle_id_fkey;

-- ---------------------------------------------------------------------
-- 1. Update all child tables FIRST so the FK from files re-resolves
--    once we change the parent.
-- ---------------------------------------------------------------------

\echo
\echo '--- portfolio_companies.review_cycle_id ---'
WITH upd AS (
  UPDATE portfolio_companies pc
  SET    review_cycle_id = m.new_id
  FROM   _cycle_id_map m
  WHERE  pc.review_cycle_id = m.old_id
  RETURNING pc.id
)
SELECT count(*) AS rows_updated FROM upd;

\echo '--- entities.review_cycle (non-empty only) ---'
WITH upd AS (
  UPDATE entities e
  SET    review_cycle = m.new_id
  FROM   _cycle_id_map m
  WHERE  e.review_cycle = m.old_id
  RETURNING e.id
)
SELECT count(*) AS rows_updated FROM upd;

\echo '--- financial_metric_reconciliation.review_cycle ---'
WITH upd AS (
  UPDATE financial_metric_reconciliation f
  SET    review_cycle = m.new_id
  FROM   _cycle_id_map m
  WHERE  f.review_cycle = m.old_id
  RETURNING f.id
)
SELECT count(*) AS rows_updated FROM upd;

\echo '--- financial_data_snowflake.review_cycle ---'
WITH upd AS (
  UPDATE financial_data_snowflake f
  SET    review_cycle = m.new_id
  FROM   _cycle_id_map m
  WHERE  f.review_cycle = m.old_id
  RETURNING f.id
)
SELECT count(*) AS rows_updated FROM upd;

\echo '--- export_jobs.review_cycle_id ---'
WITH upd AS (
  UPDATE export_jobs ej
  SET    review_cycle_id = m.new_id
  FROM   _cycle_id_map m
  WHERE  ej.review_cycle_id = m.old_id
  RETURNING ej.id
)
SELECT count(*) AS rows_updated FROM upd;

\echo '--- review_cycle_view_audit.review_cycle_id (portfolioauditreview) ---'
WITH upd AS (
  UPDATE review_cycle_view_audit a
  SET    review_cycle_id = m.new_id
  FROM   _cycle_id_map m
  WHERE  a.review_cycle_id = m.old_id
  RETURNING a.id
)
SELECT count(*) AS rows_updated FROM upd;

\echo '--- portfolioreview.review_cycle_view_audit.review_cycle_id (legacy schema) ---'
-- Comment out the block below if you do NOT want to touch the legacy schema.
WITH upd AS (
  UPDATE portfolioreview.review_cycle_view_audit a
  SET    review_cycle_id = m.new_id
  FROM   _cycle_id_map m
  WHERE  a.review_cycle_id = m.old_id
  RETURNING a.id
)
SELECT count(*) AS rows_updated FROM upd;

\echo '--- files.review_cycle_id (REAL FK; updated before parent) ---'
WITH upd AS (
  UPDATE files f
  SET    review_cycle_id = m.new_id
  FROM   _cycle_id_map m
  WHERE  f.review_cycle_id = m.old_id
  RETURNING f.id
)
SELECT count(*) AS rows_updated FROM upd;

-- ---------------------------------------------------------------------
-- 2. Update the parent.
-- ---------------------------------------------------------------------
\echo
\echo '--- review_cycles.id ---'
WITH upd AS (
  UPDATE review_cycles rc
  SET    id = m.new_id
  FROM   _cycle_id_map m
  WHERE  rc.id = m.old_id
  RETURNING rc.id
)
SELECT count(*) AS rows_updated FROM upd;

-- ---------------------------------------------------------------------
-- 2b. Recreate the FK with the original definition.
--     (review_cycle_id -> review_cycles.id, ON DELETE SET NULL, ON UPDATE NO ACTION)
-- ---------------------------------------------------------------------
ALTER TABLE files
  ADD CONSTRAINT files_review_cycle_id_fkey
  FOREIGN KEY (review_cycle_id)
  REFERENCES review_cycles(id)
  ON UPDATE NO ACTION
  ON DELETE SET NULL;

-- ---------------------------------------------------------------------
-- 3. Verification.  Anything > 0 here is a problem.
-- ---------------------------------------------------------------------
\echo
\echo '--- Verification: review_cycles rows still using UUID ids ---'
SELECT count(*) AS uuid_left_in_review_cycles
FROM   review_cycles
WHERE  id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';

\echo '--- Verification: any FK in files that no longer resolves ---'
SELECT count(*) AS dangling_files
FROM   files f
WHERE  f.review_cycle_id IS NOT NULL
AND    NOT EXISTS (SELECT 1 FROM review_cycles rc WHERE rc.id = f.review_cycle_id);

\echo '--- Verification: portfolio_companies still pointing at a UUID ---'
SELECT count(*) AS pc_uuid_left
FROM   portfolio_companies
WHERE  review_cycle_id ~ '^[0-9a-f-]{36}$';

\echo '--- Verification: entities still pointing at a UUID ---'
SELECT count(*) AS entities_uuid_left
FROM   entities
WHERE  review_cycle ~ '^[0-9a-f-]{36}$';

\echo '--- Verification: final review_cycles ---'
SELECT id, name, status, starts_at::date, ends_at::date FROM review_cycles ORDER BY starts_at;

-- ---------------------------------------------------------------------
-- 4. Commit or rollback based on --set live.
-- ---------------------------------------------------------------------
\if :live
  COMMIT;
  \echo '*** COMMITTED. ***'
\else
  ROLLBACK;
  \echo '*** ROLLED BACK (dry run). Re-run with -v live=1 to commit. ***'
\endif
