import apiClient from '@/api/axios';

/** Pending file registered server-side; upload bytes only via `uploadFileViaBackend`. */
export interface InitUploadResponse {
  file_id: number;
  s3_key: string;
}

export interface BulkUploadResultItem {
  filename: string;
  status: 'success' | 'failed';
  file_id?: number;
  error?: string;
  duplicate_warning?: boolean;
  duplicate_warning_message?: string;
  existing_files?: Array<{ file_id: number; filename: string; status: string }>;
}

export interface BulkUploadResponse {
  total: number;
  succeeded: number;
  failed: number;
  results: BulkUploadResultItem[];
}

export const MAX_BULK_AUDIT_UPLOAD_FILES = 10;
export const MAX_AUDIT_UPLOAD_BYTES = 50 * 1024 * 1024;
export const ALLOWED_AUDIT_UPLOAD_EXTENSIONS = ['.pdf', '.xlsx', '.docx', '.pptx', '.ppt'] as const;

export async function bulkUploadAuditFiles(params: {
  fy_end: string;
  review_cycle_id?: string;
  portfolio_company_id?: number;
  entity_id?: number;
  kind?: string;
  files: File[];
}) {
  const form = new FormData();
  form.append('fy_end', params.fy_end);
  if (params.review_cycle_id) {
    form.append('review_cycle_id', params.review_cycle_id);
  }
  form.append('kind', params.kind ?? 'audit_report');
  if (params.portfolio_company_id != null) {
    form.append('portfolio_company_id', String(params.portfolio_company_id));
  }
  if (params.entity_id != null) {
    form.append('entity_id', String(params.entity_id));
  }
  for (const file of params.files) {
    form.append('uploads', file);
  }
  const { data } = await apiClient.post<BulkUploadResponse>('/api/v1/files/bulk-upload', form, {
    headers: { 'Content-Type': false as unknown as string },
    timeout: 600_000,
  });
  return data;
}

export async function initFileUpload(payload: {
  portfolio_company_id?: number | null;
  /** Required by the API when `portfolio_company_id` is omitted (upload without a company). */
  review_cycle_id?: string | null;
  entity_id?: number | null;
  filename: string;
  content_type: string;
  kind: string;
}) {
  const { data } = await apiClient.post<InitUploadResponse>('/api/v1/files/init-upload', payload);
  return data;
}

export async function uploadFileViaBackend(fileId: number, file: File) {
  const form = new FormData();
  form.append('upload', file);
  await apiClient.post(`/api/v1/files/${fileId}/upload`, form, {
    headers: { 'Content-Type': false as unknown as string },
  });
}

export async function uploadOrgChartAndExtract(portfolioCompanyId: number, file: File) {
  const form = new FormData();
  form.append('upload', file);
  const { data } = await apiClient.post(`/api/v1/portfolio-companies/${portfolioCompanyId}/org-chart/upload`, form, {
    headers: { 'Content-Type': false as unknown as string },
  });
  return data as {
    file_id: number;
    status?: string | null;
    updated_at?: string | null;
    filename?: string | null;
    content_type?: string | null;
    download_url?: string | null;
    download_expires_in?: number | null;
  };
}

export async function retriggerOrgChartExtraction(portfolioCompanyId: number, fileId?: number | null) {
  const { data } = await apiClient.post(
    `/api/v1/portfolio-companies/${portfolioCompanyId}/org-chart/retrigger`,
    null,
    { params: fileId ? { file_id: fileId } : undefined },
  );
  return data as {
    file_id: number;
    status?: string | null;
    updated_at?: string | null;
    filename?: string | null;
    content_type?: string | null;
    download_url?: string | null;
    download_expires_in?: number | null;
  };
}

export async function startFileExtraction(fileId: number, payload: { kind: string }) {
  const { data } = await apiClient.post(`/api/v1/files/${fileId}/extract`, payload);
  return data as { file_id: number; status?: string | null; error_message?: string | null; meta?: Record<string, unknown> };
}

export async function getFileExtractionStatus(fileId: number) {
  const { data } = await apiClient.get(`/api/v1/files/${fileId}/extract/status`);
  return data as { file_id: number; status?: string | null; error_message?: string | null; meta?: Record<string, unknown> };
}

export async function setFileExtractionCurrency(fileId: number, currency: string) {
  const { data } = await apiClient.patch(`/api/v1/files/${fileId}/extraction/currency`, { currency });
  return data as { file_id: number; status?: string | null; error_message?: string | null; meta?: Record<string, unknown> };
}

export async function applyFileExtractionConvertCurrency(fileId: number, target_currency: string) {
  const { data } = await apiClient.post(`/api/v1/files/${fileId}/extraction/convert-currency`, {
    target_currency: target_currency.toUpperCase(),
  });
  return data as { file_id: number; status?: string | null; error_message?: string | null; meta?: Record<string, unknown> };
}

export async function patchAuditFinancialExtractedValue(
  fileId: number,
  payload: { path: string; value: number | null; reason?: string | null },
) {
  const { data } = await apiClient.patch(`/api/v1/files/${fileId}/audit-financials/extracted-value`, {
    path: payload.path,
    value: payload.value,
    reason: payload.reason ?? null,
  });
  return data as { file_id: number; status?: string | null; error_message?: string | null; meta?: Record<string, unknown> };
}

export async function addAuditFinancialCompositeField(
  fileId: number,
  payload: { path: string; reason?: string | null },
) {
  const { data } = await apiClient.post(`/api/v1/files/${fileId}/audit-financials/add-composite-field`, {
    path: payload.path,
    reason: payload.reason ?? null,
  });
  return data as { file_id: number; status?: string | null; error_message?: string | null; meta?: Record<string, unknown> };
}

export async function getAuditFinancialParentPaths(fileId: number) {
  const { data } = await apiClient.get(`/api/v1/files/${fileId}/audit-financials/parent-paths`);
  return data as { parent_paths: string[]; leaf_paths: string[] };
}

/** One contributing line in a canonical field's signed roll-up (lead schedule). */
export interface FieldComponent {
  id?: string;
  label: string;
  value: number;
  sign: '+' | '-';
  source?: string;
}

export async function mapAuditFinancialUnmatched(
  fileId: number,
  payload: {
    unmatched_id: string;
    target_parent_path: string;
    target_key?: string | null;
    confirm_overwrite?: boolean;
    /** sum (default on confirm) | total | replace. Omit to get a 409 with the breakdown to resolve. */
    conflict_mode?: 'sum' | 'total' | 'replace' | null;
  },
) {
  const { data } = await apiClient.post(`/api/v1/files/${fileId}/audit-financials/map-unmatched`, {
    unmatched_id: payload.unmatched_id,
    target_parent_path: payload.target_parent_path,
    target_key: payload.target_key ?? null,
    confirm_overwrite: payload.confirm_overwrite ?? false,
    conflict_mode: payload.conflict_mode ?? null,
  });
  return data as {
    file_id: number;
    extracted: Record<string, unknown>;
    audit_financials_unmatched: Array<Record<string, unknown>>;
    audit_financials_field_components?: Record<string, FieldComponent[]>;
    conflict_mode?: string;
    intended_path?: string;
    effective_path?: string;
  };
}

/**
 * Remove a field from the extracted tree and move it to the unmatched panel (recoverable).
 * Inverse of {@link mapAuditFinancialUnmatched} — a manual escape hatch for a duplicate /
 * mis-mapped line the automatic dedupe missed. The line reappears in the unmatched list.
 */
export async function detachAuditFinancialField(fileId: number, payload: { path: string }) {
  const { data } = await apiClient.post(
    `/api/v1/files/${fileId}/audit-financials/detach-field`,
    { path: payload.path },
  );
  return data as {
    file_id: number;
    extracted: Record<string, unknown>;
    audit_financials_unmatched: Array<Record<string, unknown>>;
    audit_financials_field_components?: Record<string, FieldComponent[]>;
    source_refs?: Record<string, SourceRef>;
  };
}

/** Dismiss an unmatched row — sets dismissed:true, moves it to the dismissed section. Always recoverable. */
export async function dismissAuditFinancialUnmatched(fileId: number, unmatchedId: string) {
  const { data } = await apiClient.post(`/api/v1/files/${fileId}/audit-financials/dismiss-unmatched`, {
    unmatched_id: unmatchedId,
  });
  return data as { file_id: number; audit_financials_unmatched: Array<Record<string, unknown>> };
}

/** Restore a previously dismissed unmatched row back to the active list. */
export async function restoreAuditFinancialUnmatched(fileId: number, unmatchedId: string) {
  const { data } = await apiClient.post(`/api/v1/files/${fileId}/audit-financials/restore-unmatched`, {
    unmatched_id: unmatchedId,
  });
  return data as { file_id: number; audit_financials_unmatched: Array<Record<string, unknown>> };
}

/** Replace a canonical leaf's roll-up breakdown; the leaf becomes the signed sum. */
export async function setAuditFinancialFieldComponents(
  fileId: number,
  payload: { path: string; components: FieldComponent[] },
) {
  const { data } = await apiClient.post(
    `/api/v1/files/${fileId}/audit-financials/field-components`,
    {
      path: payload.path,
      components: payload.components.map((c) => ({ label: c.label, value: c.value, sign: c.sign })),
    },
  );
  return data as {
    file_id: number;
    extracted: Record<string, unknown>;
    audit_financials_field_components?: Record<string, FieldComponent[]>;
  };
}

export interface SourceRef {
  page: number;
  text_snippet: string;
  /** Backend verify-pass confidence: "verified" (exact page) | "unverified" (no link). */
  source?: 'verified' | 'inherited' | 'unverified';
  /** Bounding box of the located value as page fractions [x0, y0, x1, y1] (top-left origin). */
  bbox?: [number, number, number, number];
}

export interface FileSourceRefsResponse {
  file_id: number;
  /** Flat map: dotted field path → {page, text_snippet} */
  source_refs: Record<string, SourceRef>;
  /** "completed" | "empty" | "skipped" | "not_available" */
  status: string;
}

export async function getFileSourceRefs(fileId: number) {
  const { data } = await apiClient.get<FileSourceRefsResponse>(`/api/v1/files/${fileId}/source-refs`);
  return data;
}

// --- Spreadsheet preview ---

export interface SpreadsheetPreviewRow {
  row: number;
  cells: Array<string | null>;
}

export interface SpreadsheetPreviewSheet {
  name: string;
  rows: SpreadsheetPreviewRow[];
  truncated: boolean;
}

export interface SpreadsheetPreviewResponse {
  file_id: number;
  filename: string;
  sheets: SpreadsheetPreviewSheet[];
}

export async function getSpreadsheetPreview(fileId: number, maxRowsPerSheet = 500) {
  const { data } = await apiClient.get<SpreadsheetPreviewResponse>(
    `/api/v1/files/${fileId}/preview/spreadsheet`,
    { params: { max_rows_per_sheet: maxRowsPerSheet } },
  );
  return data;
}

export async function listOrgEntities(portfolioCompanyId: number) {
  const { data } = await apiClient.get(`/api/v1/portfolio-companies/${portfolioCompanyId}/org-entities`);
  return data as Array<{
    id: number;
    portfolio_company_id: number;
    name: string;
    geolocation?: string | null;
    entity_type?: import('@/types/domain').EntityType | null;
    review_cycle?: string | null;
    status?: string | null;
    parent_entity_id?: number | null;
    is_parent: boolean;
    extra_data: Record<string, unknown>;
  }>;
}

export async function reparentOrgEntity(payload: { portfolioCompanyId: number; childEntityId: number; newParentEntityId?: number | null }) {
  const { data } = await apiClient.post(`/api/v1/portfolio-companies/${payload.portfolioCompanyId}/org-entities/reparent`, {
    child_entity_id: payload.childEntityId,
    new_parent_entity_id: payload.newParentEntityId ?? null,
  });
  return data as Array<{
    id: number;
    portfolio_company_id: number;
    name: string;
    geolocation?: string | null;
    review_cycle?: string | null;
    status?: string | null;
    parent_entity_id?: number | null;
    is_parent: boolean;
    extra_data: Record<string, unknown>;
  }>;
}

