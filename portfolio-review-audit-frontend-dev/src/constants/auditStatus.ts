import type { AuditStatus, CompanyInReviewStage } from '@/types/domain';
import {
  COMPANY_IN_REVIEW_STATUS_OPTIONS,
  COMPANY_REVIEW_STAGE_OPTIONS,
  ENTITY_AUDIT_STATUS_OPTIONS,
  ENTITY_REVIEW_STATUS_OPTIONS,
  ENTITY_RESOLVED_COMPANY_IN_REVIEW_OPTIONS,
  RECONCILIATION_STATUS_OPTIONS,
  type CompanyInReviewStatus,
  type CompanyReviewStage,
  type EntityAuditStatus,
  type EntityResolvedCompanyInReviewStatus,
  type EntityReviewStatusValue,
  type ReconciliationStatus,
  isClosedInReviewStatus,
} from '@/constants/statusEnums';

/** @deprecated use ENTITY_AUDIT_STATUS_OPTIONS from statusEnums */
export const AUDIT_STATUS_OPTIONS: readonly AuditStatus[] = ENTITY_AUDIT_STATUS_OPTIONS as readonly AuditStatus[];

/** @deprecated use ENTITY_RESOLVED_COMPANY_IN_REVIEW_OPTIONS */
export const ENTITY_RESOLVED_COMPANY_STAGE_OPTIONS: readonly EntityResolvedCompanyInReviewStatus[] =
  ENTITY_RESOLVED_COMPANY_IN_REVIEW_OPTIONS;

/** Company review stage options (audit workflow). */
export const COMPANY_STAGE_OPTIONS: readonly CompanyInReviewStage[] =
  COMPANY_REVIEW_STAGE_OPTIONS as readonly CompanyInReviewStage[];

export { RECONCILIATION_STATUS_OPTIONS, COMPANY_REVIEW_STAGE_OPTIONS };
export type { ReconciliationStatus, EntityAuditStatus, CompanyInReviewStatus, CompanyReviewStage };

export const DEFAULT_COMPANY_STAGE: CompanyInReviewStage = 'Not applicable';

const LEGACY_COMPANY_STAGE_MAP: Record<string, CompanyInReviewStage> = {
  // Old in_review_status vocabulary → nearest new review_stage value
  'Review Started': 'In review',
  'In Progress': 'In review',
  'In Progress - Partial answered': 'Response received - Partially answered',
  'In progress - Call to be scheduled': 'Response received - Call to be scheduled',
  'Discrepancy Identified': 'Discrepancy identified',
  'Not Comparable': 'Not comparable',
  'No Discrepancy Identified': 'No discrepancy identified',
  'Query sent': 'Query sent',
  'Query response reminder sent 1': 'Query response reminder sent 1',
  'Query response reminder sent 2': 'Query response reminder sent 2',
  Closed: 'Approved',
  'Closed - Flagged': 'Approved - Flagged',
  Completed: 'Approved',
  // Old review_stage vocabulary (pre-migration 0049)
  Created: 'Not applicable',
  'Scoped In': 'Not applicable',
  'Scoped Out': 'Not applicable',
  Overdue: 'Not applicable',
  'In Review': 'In review',
};

const LEGACY_ENTITY_STATUS_MAP: Record<string, AuditStatus> = {
  Completed: 'Resolved',
};

export const auditStatusBadgeClass: Record<string, string> = {
  'Scoped In': 'bg-emerald-100 text-emerald-800 border-emerald-200',
  'Scoped Out': 'bg-orange-100 text-orange-800 border-orange-200',
  'Pending Review': 'bg-yellow-100 text-yellow-800 border-yellow-200',
  'Discrepancy Identified': 'bg-red-100 text-red-800 border-red-200',
  'Clarification Requested': 'bg-blue-100 text-blue-800 border-blue-200',
  Resolved: 'bg-green-100 text-green-800 border-green-200',
  'Archive Entity': 'bg-gray-100 text-gray-600 border-gray-200',
};

export const auditStatusDotClass: Record<string, string> = {
  'Scoped In': 'bg-emerald-500',
  'Scoped Out': 'bg-orange-400',
  'Pending Review': 'bg-yellow-400',
  'Discrepancy Identified': 'bg-red-400',
  'Clarification Requested': 'bg-blue-400',
  Resolved: 'bg-green-400',
  'Archive Entity': 'bg-gray-400',
};

export const companyStageBadgeClass: Record<string, string> = {
  'Not applicable': 'bg-slate-100 text-slate-500',
  'Financials to be received': 'bg-sky-100 text-sky-800',
  'In review': 'bg-yellow-100 text-yellow-800',
  'Discrepancy identified': 'bg-red-100 text-red-800',
  'No discrepancy identified': 'bg-emerald-100 text-emerald-800',
  'Not comparable': 'bg-gray-100 text-gray-700',
  'Query sent': 'bg-blue-100 text-blue-800',
  'Query response reminder sent 1': 'bg-indigo-100 text-indigo-800',
  'Query response reminder sent 2': 'bg-indigo-100 text-indigo-900',
  'Response received': 'bg-teal-100 text-teal-800',
  'Response received - Partially answered': 'bg-amber-100 text-amber-900',
  'Response received - Call to be scheduled': 'bg-orange-100 text-orange-900',
  Approved: 'bg-green-100 text-green-800',
  'Approved - Flagged': 'bg-lime-100 text-lime-900',
  'Not Approved': 'bg-rose-100 text-rose-800',
  'Not Approved - Flagged': 'bg-rose-200 text-rose-900',
};

export const companyStageDotClass: Record<string, string> = {
  'Not applicable': 'bg-slate-300',
  'Financials to be received': 'bg-sky-400',
  'In review': 'bg-yellow-400',
  'Discrepancy identified': 'bg-red-400',
  'No discrepancy identified': 'bg-emerald-500',
  'Not comparable': 'bg-gray-400',
  'Query sent': 'bg-blue-400',
  'Query response reminder sent 1': 'bg-indigo-400',
  'Query response reminder sent 2': 'bg-indigo-600',
  'Response received': 'bg-teal-400',
  'Response received - Partially answered': 'bg-amber-500',
  'Response received - Call to be scheduled': 'bg-orange-500',
  Approved: 'bg-green-500',
  'Approved - Flagged': 'bg-lime-500',
  'Not Approved': 'bg-rose-500',
  'Not Approved - Flagged': 'bg-rose-700',
};

/**
 * Review-stage vocabulary — the canonical `review_stage` column shared by the Review Cycle
 * Adjustments page, the In Review Tracker (home), and the company detail header. Render the stage
 * with these helpers everywhere so the three views stay in sync. Keep colors aligned with the
 * adjustments page (`AuditTrackerPage` stageBadge).
 */
export const DEFAULT_REVIEW_STAGE: CompanyReviewStage = 'Not applicable';

export const reviewStageBadgeClass: Record<string, string> = {
  'Not applicable': 'bg-slate-100 text-slate-500',
  'Financials to be received': 'bg-sky-100 text-sky-800',
  'In review': 'bg-yellow-100 text-yellow-800',
  'Discrepancy identified': 'bg-red-100 text-red-800',
  'No discrepancy identified': 'bg-emerald-100 text-emerald-800',
  'Not comparable': 'bg-gray-100 text-gray-700',
  'Query sent': 'bg-blue-100 text-blue-800',
  'Query response reminder sent 1': 'bg-indigo-100 text-indigo-800',
  'Query response reminder sent 2': 'bg-indigo-100 text-indigo-900',
  'Response received': 'bg-teal-100 text-teal-800',
  'Response received - Partially answered': 'bg-amber-100 text-amber-900',
  'Response received - Call to be scheduled': 'bg-orange-100 text-orange-900',
  Approved: 'bg-green-100 text-green-800',
  'Approved - Flagged': 'bg-lime-100 text-lime-900',
  'Not Approved': 'bg-rose-100 text-rose-800',
  'Not Approved - Flagged': 'bg-rose-200 text-rose-900',
};

export const reviewStageDotClass: Record<string, string> = {
  'Not applicable': 'bg-slate-400',
  'Financials to be received': 'bg-sky-500',
  'In review': 'bg-yellow-400',
  'Discrepancy identified': 'bg-red-400',
  'No discrepancy identified': 'bg-emerald-500',
  'Not comparable': 'bg-gray-400',
  'Query sent': 'bg-blue-400',
  'Query response reminder sent 1': 'bg-indigo-400',
  'Query response reminder sent 2': 'bg-indigo-600',
  'Response received': 'bg-teal-500',
  'Response received - Partially answered': 'bg-amber-500',
  'Response received - Call to be scheduled': 'bg-orange-500',
  Approved: 'bg-green-500',
  'Approved - Flagged': 'bg-lime-500',
  'Not Approved': 'bg-rose-500',
  'Not Approved - Flagged': 'bg-rose-600',
};

export function normalizeReviewStage(raw: string | null | undefined): CompanyReviewStage {
  if (!raw) return DEFAULT_REVIEW_STAGE;
  // Case-insensitive match so stored casing variants (e.g. "In Review") resolve to the canonical
  // option ("In review") instead of falling through to the default.
  const trimmed = String(raw).trim().toLowerCase();
  return COMPANY_REVIEW_STAGE_OPTIONS.find((o) => o.toLowerCase() === trimmed) ?? DEFAULT_REVIEW_STAGE;
}

/** Display label for a review stage (the stored value is already the display value). */
export function reviewStageDisplayLabel(raw: string | null | undefined): string {
  return normalizeReviewStage(raw);
}

export function normalizeAuditStatus(raw: string | null | undefined): AuditStatus {
  const trimmed = (raw ?? '').trim();
  if (trimmed && AUDIT_STATUS_OPTIONS.includes(trimmed as AuditStatus)) {
    return trimmed as AuditStatus;
  }
  if (trimmed && LEGACY_ENTITY_STATUS_MAP[trimmed]) {
    return LEGACY_ENTITY_STATUS_MAP[trimmed];
  }
  return 'Pending Review';
}

export function normalizeCompanyStage(raw: string | null | undefined): CompanyInReviewStage {
  if (!raw) return DEFAULT_COMPANY_STAGE;
  const trimmed = String(raw).trim();
  if (COMPANY_STAGE_OPTIONS.includes(trimmed as CompanyInReviewStage)) {
    return trimmed as CompanyInReviewStage;
  }
  return LEGACY_COMPANY_STAGE_MAP[trimmed] ?? DEFAULT_COMPANY_STAGE;
}

/** Display label for entity audit status (stored value shown as-is). */
export function inReviewStatusDisplayLabel(raw: string | null | undefined): string {
  return inReviewStatusDisplayLabelFromNormalized(normalizeAuditStatus(raw));
}

export function inReviewStatusDisplayLabelFromNormalized(status: AuditStatus): string {
  return status;
}

export function companyStageDisplayLabel(raw: string | null | undefined): string {
  return normalizeCompanyStage(raw);
}

/**
 * Per-entity status vocabulary (entities.status). The full `EntityReviewStatus` list:
 * the review-stage values plus the three scoping/archival states. Use these helpers for
 * the org-chart per-entity status dropdown so every possible entity status is selectable.
 */
export const ENTITY_STATUS_OPTIONS: readonly EntityReviewStatusValue[] = ENTITY_REVIEW_STATUS_OPTIONS;

export const DEFAULT_ENTITY_STATUS: EntityReviewStatusValue = 'Not applicable';

export const entityStatusBadgeClass: Record<string, string> = {
  'Scoped In': 'bg-emerald-100 text-emerald-800 border-emerald-200',
  'Scoped Out': 'bg-orange-100 text-orange-800 border-orange-200',
  'Archive Entity': 'bg-gray-100 text-gray-600 border-gray-200',
  ...reviewStageBadgeClass,
};

export function normalizeEntityStatus(raw: string | null | undefined): EntityReviewStatusValue {
  if (!raw) return DEFAULT_ENTITY_STATUS;
  // Case-insensitive match so stored casing variants resolve to the canonical option.
  const trimmed = String(raw).trim().toLowerCase();
  return ENTITY_REVIEW_STATUS_OPTIONS.find((o) => o.toLowerCase() === trimmed) ?? DEFAULT_ENTITY_STATUS;
}

/** Display label for an entity status (stored value is already the display value). */
export function entityStatusDisplayLabel(raw: string | null | undefined): string {
  return normalizeEntityStatus(raw);
}

export { isClosedInReviewStatus };
