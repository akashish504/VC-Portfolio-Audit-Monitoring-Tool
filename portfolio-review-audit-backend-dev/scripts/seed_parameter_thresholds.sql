-- Seed parameter_thresholds for financial discrepancy variance checks.
--
-- Table (see models / alembic):
--   id              SERIAL PK (omit on insert)
--   key             VARCHAR(128) UNIQUE — must match the LABEL used in portfolio.py
--                   (e.g. "Revenue", "EBITDA"); comparison code indexes thresholds by this string.
--   value           JSON — percent_threshold = ratio (0.005 == 0.5%), absolute_threshold optional
--   description     TEXT — human note / full name
--   created_at      TIMESTAMPTZ DEFAULT now()
--   updated_at      TIMESTAMPTZ DEFAULT now()
--
-- Run against your DB, schema defaults to portfolioauditreview (APP_SCHEMA).
-- Example:  psql "$DATABASE_URL" -f scripts/seed_parameter_thresholds.sql

\set ON_ERROR_STOP on

SET search_path TO portfolioauditreview;

INSERT INTO parameter_thresholds AS pt (key, value, description)
VALUES
  (
    'Revenue',
    '{"metric_key":"revenue","display_name":"Revenue","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for Revenue (internal metric key: revenue). Default 0.5% relative; optional absolute cap in value JSON.'
  ),
  (
    'EBITDA',
    '{"metric_key":"ebitda","display_name":"EBITDA","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for EBITDA (internal metric key: ebitda).'
  ),
  (
    'PBT',
    '{"metric_key":"pbt","display_name":"PBT","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for PBT / profit before tax (internal metric key: pbt).'
  ),
  (
    'PAT',
    '{"metric_key":"pat","display_name":"PAT","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for PAT / profit after tax (internal metric key: pat).'
  ),
  (
    'Gross Margin',
    '{"metric_key":"gross_margin","display_name":"Gross Margin","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for Gross Margin (internal metric key: gross_margin).'
  ),
  (
    'Net Margin',
    '{"metric_key":"net_margin","display_name":"Net Margin","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for Net Margin (internal metric key: net_margin).'
  ),
  (
    'Operating Expenses',
    '{"metric_key":"operating_expenses","display_name":"Operating Expenses","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for Operating Expenses (internal metric key: operating_expenses).'
  ),
  (
    'Cash Burn',
    '{"metric_key":"cash_burn","display_name":"Cash Burn","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for Cash Burn (internal metric key: cash_burn).'
  ),
  (
    'Runway (months)',
    '{"metric_key":"runway_months","display_name":"Runway (months)","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for Runway in months (internal metric key: runway_months).'
  ),
  (
    'ARR',
    '{"metric_key":"arr","display_name":"ARR","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for ARR (internal metric key: arr).'
  ),
  (
    'MRR',
    '{"metric_key":"mrr","display_name":"MRR","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for MRR (internal metric key: mrr).'
  ),
  (
    'Headcount',
    '{"metric_key":"headcount","display_name":"Headcount","percent_threshold":0.005,"absolute_threshold":null}'::json,
    'Variance threshold for Headcount (internal metric key: headcount).'
  )
ON CONFLICT (key) DO UPDATE SET
  value       = EXCLUDED.value,
  description = EXCLUDED.description,
  updated_at  = now();

-- Verify
SELECT id, key, value, description, created_at, updated_at
FROM parameter_thresholds
ORDER BY id;
