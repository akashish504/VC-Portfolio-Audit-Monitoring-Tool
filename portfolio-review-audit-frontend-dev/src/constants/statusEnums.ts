/**
 * Canonical status/stage values — keep in sync with backend enums in schema/portfolio.py.
 */

export const RECONCILIATION_STATUS_OPTIONS = [
  'Open',
  'Sent',
  'Not Sent (if No)',
  'Partial',
  'Closed',
  'Sent - Flagged',
  'Partial - Flagged',
  'Closed - Flagged',
] as const;

export type ReconciliationStatus = (typeof RECONCILIATION_STATUS_OPTIONS)[number];

export const ENTITY_AUDIT_STATUS_OPTIONS = [
  'Scoped In',
  'Scoped Out',
  'Pending Review',
  'Discrepancy Identified',
  'Clarification Requested',
  'Resolved',
  'Archive Entity',
] as const;

export type EntityAuditStatus = (typeof ENTITY_AUDIT_STATUS_OPTIONS)[number];

export const COMPANY_IN_REVIEW_STATUS_OPTIONS = [
  'Review Started',
  'Discrepancy Identified',
  'Not Comparable',
  'No Discrepancy Identified',
  'Query sent',
  'Query response reminder sent 1',
  'Query response reminder sent 2',
  'In Progress',
  'In Progress - Partial answered',
  'In progress - Call to be scheduled',
  'Closed',
  'Closed - Flagged',
] as const;

export type CompanyInReviewStatus = (typeof COMPANY_IN_REVIEW_STATUS_OPTIONS)[number];

/** Subset offered when an entity is marked Resolved. */
export const ENTITY_RESOLVED_COMPANY_IN_REVIEW_OPTIONS = ['Closed', 'Closed - Flagged'] as const;

export type EntityResolvedCompanyInReviewStatus = (typeof ENTITY_RESOLVED_COMPANY_IN_REVIEW_OPTIONS)[number];

export const COMPANY_REVIEW_STAGE_OPTIONS = [
  'Not applicable',
  'Financials to be received',
  'In review',
  'Discrepancy identified',
  'No discrepancy identified',
  'Not comparable',
  'Query sent',
  'Query response reminder sent 1',
  'Query response reminder sent 2',
  'Response received',
  'Response received - Partially answered',
  'Response received - Call to be scheduled',
  'Approved',
  'Approved - Flagged',
  'Not Approved',
  'Not Approved - Flagged',
] as const;

export type CompanyReviewStage = (typeof COMPANY_REVIEW_STAGE_OPTIONS)[number];

/**
 * Full per-entity status vocabulary — mirrors the backend `EntityReviewStatus` enum
 * (entities.status). It is the review-stage vocabulary plus the three scoping/archival
 * states. This is the complete list shown in the org-chart per-entity status dropdown.
 */
export const ENTITY_REVIEW_STATUS_OPTIONS = [
  'Scoped In',
  'Scoped Out',
  'Archive Entity',
  ...COMPANY_REVIEW_STAGE_OPTIONS,
] as const;

export type EntityReviewStatusValue = (typeof ENTITY_REVIEW_STATUS_OPTIONS)[number];

export function isClosedInReviewStatus(status: string): boolean {
  return status === 'Closed' || status === 'Closed - Flagged';
}
