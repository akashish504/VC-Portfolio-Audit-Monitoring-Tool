import apiClient from '@/api/axios';
import type { Entity, EntityType, FileData, PortfolioCompany } from '@/types/domain';

export type FinancialMetricKey =
  | 'revenue'
  | 'ebitda'
  | 'pbt'
  | 'pat'
  | 'cash'
  | 'debt';

import type { ReconciliationStatus } from '@/constants/statusEnums';

export type { ReconciliationStatus };

/** Per-term contribution captured by the backend financial-extraction evaluator. */
export type MappingBreakdownTermSource = 'ocr' | 'default_zero';

export interface MappingBreakdownTerm {
  path: string;
  sign: '+' | '-';
  raw_value: number | null;
  contribution: number;
  source: MappingBreakdownTermSource;
}

export interface MappingBreakdownDerivedComponent {
  path: string | null;
  value: number | null;
}

export interface MappingBreakdownDerived {
  computed: boolean;
  formula: string;
  components: Record<string, MappingBreakdownDerivedComponent>;
}

/**
 * Per-metric breakdown persisted on `FinancialMetricReconciliation.extra_data.mapping_breakdown[metric_key]`
 * by the backend extraction sync and surfaced on the read API as a typed top-level field.
 */
export interface MappingBreakdown {
  computed_at?: string | null;
  config_signature?: string | null;
  currency?: string | null;
  total: number;
  terms: MappingBreakdownTerm[];
  missing_paths: string[];
  derived_components?: Record<string, MappingBreakdownDerived> | null;
}

/**
 * Latest manual-edit marker for one field. Mirrors the backend ``manual_edit_marker`` shape
 * (services/manual_edit_marker.py). Surfaced under ``meta.manual_edits[path]`` (extraction tree)
 * and ``row.manual_edits[column]`` (reconciliation) so the UI can flag manually edited values.
 */
export interface ManualEditMarker {
  edited?: boolean;
  action?: string | null;
  edited_by?: string | null;
  edited_at?: string | null;
  reason?: string | null;
  previous_value?: number | string | boolean | null;
  new_value?: number | string | boolean | null;
}

/** Tall reconciliation row: one per (company, entity, review_cycle, metric_key). */
export interface ApiFinancialMetricReconciliationRow {
  id: number;
  portfolio_company_id: number;
  entity_id: number;
  entity_name?: string | null;
  review_cycle: string;
  metric_key: FinancialMetricKey;
  mis_amount: number | null;
  afs_amount: number | null;
  mis_currency: string | null;
  afs_currency: string | null;
  frequency: string | null;
  category: string;
  type: string | null;
  discrepency_text: string;
  status: ReconciliationStatus | null;
  enable: boolean;
  company_response: string | null;
  reviewer_remarks: string | null;
  variance_category: string | null;
  flagged: boolean;
  extra_data: Record<string, unknown>;
  mapping_breakdown?: MappingBreakdown | null;
  /** Per-field manual-edit markers (keyed by column, e.g. "afs_amount"); null when nothing edited. */
  manual_edits?: Record<string, ManualEditMarker> | null;
  created_at: string;
  updated_at: string;
}

/** Free-form manual query row (below financial table in email). */
export interface ApiManualReconciliationQueryRow {
  id: number;
  portfolio_company_id: number;
  entity_id: number | null;
  entity_name?: string | null;
  discrepency_text: string;
  category: string;
  type: string | null;
  status: ReconciliationStatus | null;
  enable: boolean;
  company_response: string | null;
  reviewer_remarks: string | null;
  flagged: boolean;
  variance_category: string | null;
  created_at: string;
  updated_at: string;
}

/** Legacy wide-row types kept for Snowflake panel only. */
export interface ApiFinancialDataRow {
  id: number;
  portfolio_company_id: number;
  entity_id: number | null;
  period_start?: string | null;
  period_end?: string | null;
  review_cycle: string | null;
  frequency: string | null;
  currency: string | null;
  revenue: number | null;
  ebitda: number | null;
  pbt: number | null;
  pat: number | null;
  cash: number | null;
  debt: number | null;
  extra_data: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ApiFinancialDataSnowflakeRow extends ApiFinancialDataRow {
  source_ref: string | null;
  /** Per-metric manual-edit markers (keyed by metric, e.g. "ebitda"); null when nothing edited. */
  manual_edits?: Record<string, ManualEditMarker> | null;
}

/** Snowflake row with resolved entity name (company-scoped list / attachment APIs). */
export interface ApiFinancialDataSnowflakeAttachmentRow extends ApiFinancialDataSnowflakeRow {
  entity_name?: string | null;
}

export interface InReviewTrackerRow {
  portfolio_company_id: number;
  company_name: string;
  entity_id: number | null;
  entity_name: string | null;
  entity_status: string | null;
  review_cycle_id: string | null;
  contact_name: string | null;
  has_files_without_entity: boolean;
  has_pending_org_chart_reconciliation?: boolean;
}

export async function listInReviewTracker(params?: {
  q?: string;
  review_cycle_id?: string;
  entity_status?: string;
  limit?: number;
  offset?: number;
}) {
  const res = await apiClient.get('/api/v1/in-review-tracker', { params });
  return res.data as { items: InReviewTrackerRow[]; total: number };
}

export async function searchPortfolioCompanies(q: string, reviewCycleId?: string) {
  const res = await apiClient.get('/api/v1/portfolio-companies', {
    params: { q: q || undefined, review_cycle_id: reviewCycleId || undefined, limit: 20, offset: 0 },
  });
  return (res.data as { items: PortfolioCompany[]; total: number }).items;
}

export async function listPortfolioCompanies(params?: {
  q?: string;
  review_cycle_id?: string;
  review_stage?: string;
  in_review_status?: string;
  has_discrepancy_type?: string;
  has_discrepancy_category?: string;
  limit?: number;
  offset?: number;
}) {
  const res = await apiClient.get('/api/v1/portfolio-companies', { params });
  return res.data as { items: PortfolioCompany[]; total: number };
}

/**
 * One Audit Tracker row = one entity in the review cycle, carrying its parent company's
 * fields (so company-level columns + company-field edits keep working) plus the entity's
 * own `entity_status` — the audit state of record (replaces the old company `review_stage`).
 */
export interface CycleEntityRow extends PortfolioCompany {
  entity_id: number | null;
  entity_name: string | null;
  entity_status: string | null;
  entity_type: string | null;
  parent_entity_id: number | null;
  entity_is_parent: boolean;
  entity_review_cycle: string | null;
  /** False when the company has no entities yet — row is a placeholder. */
  has_entities: boolean;
  /** True when the company already has an org-chart file on record. */
  has_org_chart: boolean;
  entity_comments: string | null;
  entity_one_desk_email_status: string | null;
  entity_geolocation: string | null;
}

export async function listCycleEntities(params?: {
  q?: string;
  review_cycle_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
}) {
  const res = await apiClient.get('/api/v1/cycle-entities', { params });
  return res.data as { items: CycleEntityRow[]; total: number };
}

/**
 * Fetch the set of deal_ids (= company_ids) that are scoped in for audit
 * for the given review cycle. A company is "scoped in" if ANY metadata record
 * for (review_cycle_id, deal_id) has scoping_for_audit = true.
 * Returns a Set<string> of scoped-in company_ids. If no cycle is provided,
 * returns an empty set (all companies fall into "Scoped Out" by default).
 */
export async function fetchScopedInDealIds(reviewCycleId: string): Promise<Set<string>> {
  if (!reviewCycleId) return new Set();
  const res = await apiClient.get('/api/v1/master-scoping', {
    params: { review_cycle_id: reviewCycleId },
  });
  const records = res.data as Array<{ deal_id: string; scoping_for_audit: boolean | null }>;
  const scopedIn = new Set<string>();
  for (const r of records) {
    if (r.scoping_for_audit === true) {
      scopedIn.add(r.deal_id);
    }
  }
  return scopedIn;
}

export async function getPortfolioCompany(portfolioCompanyId: number) {
  const res = await apiClient.get(`/api/v1/portfolio-companies/${portfolioCompanyId}`);
  return res.data as PortfolioCompany;
}

export async function getPortfolioCompanyByCompanyId(companyId: string, reviewCycleId?: string) {
  const res = await apiClient.get(`/api/v1/portfolio-companies/by-company-id/${encodeURIComponent(companyId)}`, {
    params: { review_cycle_id: reviewCycleId || undefined },
  });
  return res.data as PortfolioCompany;
}

export async function createPortfolioCompany(payload: {
  company_id: string;
  name: string;
  contact_name?: string | null;
  contact_email_id?: string | null;
  extra_data?: Record<string, unknown>;
}) {
  const res = await apiClient.post('/api/v1/portfolio-companies', payload);
  return res.data as PortfolioCompany;
}

const CYCLE_AUDIT_HEADER = { 'X-Audit-Context': 'review-cycle-adjustments' };

export async function bulkUpsertPortfolioCompanies(payload: {
  items: Array<{ name: string; company_id?: string | null } & Partial<Omit<PortfolioCompany, 'id'>>>;
}) {
  const res = await apiClient.post('/api/v1/portfolio-companies/bulk-upsert', payload, {
    headers: CYCLE_AUDIT_HEADER,
  });
  return res.data as { items: PortfolioCompany[] };
}

export async function patchPortfolioCompany(
  portfolioCompanyId: number,
  payload: Partial<PortfolioCompany> & { extra_data?: Record<string, unknown>; edit_reason?: string },
  options?: { auditContext?: 'review-cycle-adjustments' | 'company' },
) {
  const headers =
    options?.auditContext === 'review-cycle-adjustments' ? CYCLE_AUDIT_HEADER : undefined;
  const res = await apiClient.patch(`/api/v1/portfolio-companies/${portfolioCompanyId}`, payload, {
    headers,
  });
  return res.data as PortfolioCompany;
}

/** Deletes the org-chart file (if any), clears `org_chart_file_id`, and removes all entities for the company. */
export async function clearOrgChartAndEntities(portfolioCompanyId: number) {
  const res = await apiClient.delete(`/api/v1/portfolio-companies/${portfolioCompanyId}/org-chart`);
  return res.data as { deleted_entities: number; deleted_file_id: number | null };
}

export async function listEntities(params?: { portfolio_company_id?: number; review_cycle?: string; limit?: number; offset?: number }) {
  const res = await apiClient.get('/api/v1/entities', { params });
  return res.data as { items: Entity[]; total: number };
}

export async function patchEntity(
  entityId: number,
  payload: Partial<Pick<Entity, 'name' | 'geolocation' | 'entity_type' | 'review_cycle' | 'status' | 'parent_entity_id' | 'region' | 'is_parent' | 'extra_data' | 'comments' | 'one_desk_email_status'>>,
) {
  const res = await apiClient.patch(`/api/v1/entities/${entityId}`, payload);
  return res.data as Entity;
}

export async function createEntity(payload: {
  portfolio_company_id: number;
  name: string;
  geolocation?: string | null;
  entity_type?: EntityType | null;
  parent_entity_id?: number | null;
  is_parent?: boolean;
  review_cycle?: string | null;
  status?: string | null;
}) {
  const res = await apiClient.post('/api/v1/entities', payload);
  return res.data as Entity;
}

export async function deleteEntity(entityId: number) {
  await apiClient.delete(`/api/v1/entities/${entityId}`);
}

export async function listFiles(params?: { portfolio_company_id?: number; entity_id?: number; status?: string; unattached_only?: boolean; limit?: number; offset?: number }) {
  const res = await apiClient.get('/api/v1/files', { params });
  return res.data as { items: FileData[]; total: number };
}

// ---- Financial Metric Reconciliation APIs ----

export async function listFinancialMetricReconciliation(params?: {
  portfolio_company_id?: number;
  entity_id?: number;
  limit?: number;
  offset?: number;
}) {
  const res = await apiClient.get('/api/v1/financial-metric-reconciliation', { params });
  return res.data as { items: ApiFinancialMetricReconciliationRow[]; total: number };
}

export async function getFinancialMetricReconciliation(id: number) {
  const res = await apiClient.get(`/api/v1/financial-metric-reconciliation/${id}`);
  return res.data as ApiFinancialMetricReconciliationRow;
}

export async function patchFinancialMetricReconciliation(
  id: number,
  payload: Partial<Pick<
    ApiFinancialMetricReconciliationRow,
    | 'afs_amount'
    | 'afs_currency'
    | 'discrepency_text'
    | 'status'
    | 'enable'
    | 'company_response'
    | 'reviewer_remarks'
    | 'variance_category'
    | 'flagged'
    | 'type'
  >> & { edit_reason?: string },
) {
  const res = await apiClient.patch(`/api/v1/financial-metric-reconciliation/${id}`, payload);
  return res.data as ApiFinancialMetricReconciliationRow;
}

export async function convertAfsCurrencyForEntityCycle(params: {
  entity_id: number;
  review_cycle: string;
  target_currency: string;
}) {
  const res = await apiClient.post('/api/v1/financial-metric-reconciliation/convert-afs-currency', {
    entity_id: params.entity_id,
    review_cycle: params.review_cycle,
    target_currency: params.target_currency.toUpperCase(),
  });
  return res.data as { updated: number };
}

// ---- Manual Reconciliation Queries APIs ----

export async function listManualReconciliationQueries(params?: {
  portfolio_company_id?: number;
  entity_id?: number;
  limit?: number;
  offset?: number;
}) {
  const res = await apiClient.get('/api/v1/manual-reconciliation-queries', { params });
  return res.data as { items: ApiManualReconciliationQueryRow[]; total: number };
}

export async function createManualReconciliationQuery(payload: {
  portfolio_company_id: number;
  entity_id?: number | null;
  discrepency_text: string;
  type?: string | null;
  status?: string | null;
  enable?: boolean;
  company_response?: string | null;
  reviewer_remarks?: string | null;
  flagged?: boolean;
  variance_category?: string | null;
}) {
  const res = await apiClient.post('/api/v1/manual-reconciliation-queries', payload);
  return res.data as ApiManualReconciliationQueryRow;
}

export async function patchManualReconciliationQuery(
  id: number,
  payload: Partial<Pick<
    ApiManualReconciliationQueryRow,
    | 'discrepency_text'
    | 'status'
    | 'enable'
    | 'company_response'
    | 'reviewer_remarks'
    | 'flagged'
    | 'variance_category'
    | 'type'
  >>,
) {
  const res = await apiClient.patch(`/api/v1/manual-reconciliation-queries/${id}`, payload);
  return res.data as ApiManualReconciliationQueryRow;
}

export async function deleteManualReconciliationQuery(id: number) {
  await apiClient.delete(`/api/v1/manual-reconciliation-queries/${id}`);
}

// ---- Snowflake MIS data (kept) ----

export async function createFinancialDataSnowflake(payload: {
  portfolio_company_id: number;
  entity_id?: number | null;
  review_cycle?: string | null;
  frequency?: string | null;
  currency?: string | null;
  metric_key: string;
  metric_value: number;
  edit_reason: string;
}) {
  const res = await apiClient.post('/api/v1/financial-data-snowflake', payload);
  return res.data as ApiFinancialDataSnowflakeRow;
}

export async function listFinancialDataSnowflake(params?: { portfolio_company_id?: number; entity_id?: number; limit?: number; offset?: number }) {
  const res = await apiClient.get('/api/v1/financial-data-snowflake', { params });
  return res.data as { items: ApiFinancialDataSnowflakeRow[]; total: number };
}

export async function patchFinancialDataSnowflake(
  id: number,
  payload: Partial<ApiFinancialDataSnowflakeRow> & { edit_reason?: string },
) {
  const res = await apiClient.patch(`/api/v1/financial-data-snowflake/${id}`, payload);
  return res.data as ApiFinancialDataSnowflakeRow;
}

export async function convertFinancialDataSnowflakeCurrency(rowId: number, targetCurrency: string) {
  const res = await apiClient.post(`/api/v1/financial-data-snowflake/${rowId}/convert-currency`, {
    target_currency: targetCurrency.toUpperCase(),
  });
  return res.data as ApiFinancialDataSnowflakeAttachmentRow;
}

/** List Snowflake financial rows for a company (includes entity_name when attached). */
export async function listCompanySnowflakeData(
  portfolioCompanyId: number,
  params?: { limit?: number; offset?: number },
) {
  const res = await apiClient.get(`/api/v1/portfolio-companies/${portfolioCompanyId}/financial-data-snowflake`, {
    params: { limit: params?.limit ?? 500, offset: params?.offset ?? 0 },
  });
  return res.data as { items: ApiFinancialDataSnowflakeAttachmentRow[]; total: number };
}

/** Attach or reassign a Snowflake row to an org-chart entity. Pass null to detach. */
export async function setSnowflakeEntityAttachment(rowId: number, entityId: number | null) {
  const res = await apiClient.patch(`/api/v1/financial-data-snowflake/${rowId}/entity-attachment`, {
    entity_id: entityId,
  });
  return res.data as ApiFinancialDataSnowflakeAttachmentRow;
}


export async function getFileDownloadUrl(fileId: number) {
  const res = await apiClient.get(`/api/v1/files/${fileId}/download-url`);
  return res.data as { file_id: number; download_url: string; file_name: string; content_type: string | null; expires_in: number };
}

/** Backend-proxied URL for streaming the file — avoids S3 CORS issues in pdfjs. */
export function getFileStreamUrl(fileId: number): string {
  return `/api/v1/files/${fileId}/stream`;
}

/**
 * Fetch file bytes through the authenticated axios client and return a blob URL.
 * Use this instead of getFileStreamUrl() when the URL is passed to an iframe or img tag,
 * since those make unauthenticated browser requests that will fail the JWT middleware.
 */
export async function fetchFileAsBlobUrl(fileId: number): Promise<{ blobUrl: string; contentType: string }> {
  const res = await apiClient.get(`/api/v1/files/${fileId}/stream`, { responseType: 'blob' });
  const blob = res.data as Blob;
  return { blobUrl: URL.createObjectURL(blob), contentType: blob.type || 'application/octet-stream' };
}

/** Per-path source reference (page + verbatim text snippet) for the PDF source viewer. */
export interface AfsSourceRef {
  page: number;
  text_snippet: string;
  /**
   * Confidence of the page, set by the backend's deterministic verify pass:
   *  - "verified"   — the snippet was located in the PDF text; page is exact.
   *  - "inherited"  — page taken from a verified sibling/section line; approximate (±1).
   *  - "unverified" — could not be located → the UI shows no link.
   * Absent on records extracted before the verify pass (treated as clickable/exact for back-compat).
   */
  source?: 'verified' | 'inherited' | 'unverified';
  /** Bounding box of the located value as page fractions [x0, y0, x1, y1] (top-left origin). */
  bbox?: [number, number, number, number];
}

/**
 * Resolve an entity's audited-financials source file + its source-ref map.
 * Lets the Discrepancy Dashboard open the PDF source viewer for a dotted metric path.
 * `file_id` is null when the entity has no audited-financials file.
 */
export interface AfsCandidateFile {
  file_id: number;
  filename: string;
  review_cycle_id: string | null;
  ocr_status: string;
  is_primary: boolean;
  is_reconciliation_source: boolean;
}

export interface EntityAfsSource {
  entity_id: number;
  file_id: number | null;
  source_refs: Record<string, AfsSourceRef>;
  status: string;
  files: AfsCandidateFile[];
}

export async function getEntityAfsSource(entityId: number) {
  const res = await apiClient.get(`/api/v1/entities/${entityId}/afs-source`);
  return res.data as EntityAfsSource;
}

/**
 * Choose which audited-financials file is the reconciliation source for an entity. The backend
 * marks it primary, re-syncs, and the dashboard values / breakdowns / source links / query-email
 * numbers all then come from this file. Returns the refreshed `getEntityAfsSource` payload.
 */
export async function setEntityPrimaryAfsFile(entityId: number, fileId: number) {
  const res = await apiClient.put(`/api/v1/entities/${entityId}/afs-primary-file`, { file_id: fileId });
  return res.data as EntityAfsSource;
}

export interface ApiFileRead {
  id: number;
  portfolio_company_id: number | null;
  entity_id?: number | null;
  review_cycle_id?: string | null;
  filename: string;
  content_type?: string | null;
  status?: string | null;
  tags: string[];
  portfolio_company_name?: string | null;
  portfolio_company_review_cycle_id?: string | null;
  entity_name?: string | null;
  entity_geolocation?: string | null;
  entity_detached_acknowledged?: boolean;
  size_bytes?: number | null;
  created_at: string;
  updated_at: string;
  processed_at?: string | null;
  duplicate_warning?: boolean;
  message?: string;
  existing_files?: Array<{ file_id: number; filename: string; status: string }>;
}

export async function getFile(fileId: number) {
  const res = await apiClient.get<ApiFileRead>(`/api/v1/files/${fileId}`);
  return res.data;
}

export async function patchFile(
  fileId: number,
  payload: { portfolio_company_id?: number | null; entity_id?: number | null; fy_end?: string; entity_detached_acknowledged?: boolean },
) {
  const res = await apiClient.patch<ApiFileRead>(`/api/v1/files/${fileId}`, payload);
  return res.data;
}

export async function deleteFile(fileId: number) {
  await apiClient.delete(`/api/v1/files/${fileId}`);
}

export async function downloadInReviewDiscrepanciesXlsx(reviewCycleId?: string) {
  const params: Record<string, string> = {};
  if (reviewCycleId && reviewCycleId !== '__all__') params.review_cycle_id = reviewCycleId;
  const res = await apiClient.get('/api/v1/discrepancies/export/in-review', {
    params,
    responseType: 'blob',
  });
  const today = new Date().toISOString().slice(0, 10);
  const filename = reviewCycleId && reviewCycleId !== '__all__'
    ? `in-review-discrepancies-${reviewCycleId.replace(/\//g, '-')}-${today}.xlsx`
    : `in-review-discrepancies-${today}.xlsx`;
  const url = URL.createObjectURL(res.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

