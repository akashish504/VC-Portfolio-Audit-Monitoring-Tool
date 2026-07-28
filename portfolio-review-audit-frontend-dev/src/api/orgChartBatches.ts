import apiClient from '@/api/axios';

export interface OrgChartUploadRecord {
  id: number;
  batch_id: number;
  review_cycle_id: string;
  file_id: number | null;
  file_name: string;
  storage_uri: string | null;
  company_id: string | null;
  name: string | null;
  portfolio_company_id: number | null;
  extracted_org_chart: unknown | null;
  extraction_status: string;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrgChartUploadBatch {
  id: number;
  review_cycle_id: string;
  original_zip_filename: string;
  status: string;
  file_count: number;
  mapping_uploaded_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface OrgChartUploadBatchDetail extends OrgChartUploadBatch {
  records: OrgChartUploadRecord[];
}

export interface BatchUploadResponse {
  batch: OrgChartUploadBatch;
  records: OrgChartUploadRecord[];
}

export interface BatchListResponse {
  items: OrgChartUploadBatch[];
  total: number;
}

export interface MappingUploadResponse {
  rows_processed: number;
  rows_mapped: number;
  rows_skipped: number;
  errors: Array<{ row: number; file_name: string; error: string }>;
  extraction_queued: number;
}

export async function uploadOrgChartZip(
  zipFile: File,
): Promise<BatchUploadResponse> {
  const form = new FormData();
  form.append('file', zipFile);
  const { data } = await apiClient.post<BatchUploadResponse>(
    '/api/v1/org-chart-batches/upload',
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return data;
}

export async function listOrgChartBatches(params?: {
  review_cycle_id?: string;
  limit?: number;
  offset?: number;
  skipGlobalLoading?: boolean;
}): Promise<BatchListResponse> {
  const { data } = await apiClient.get<BatchListResponse>('/api/v1/org-chart-batches', {
    params: {
      review_cycle_id: params?.review_cycle_id,
      limit: params?.limit ?? 50,
      offset: params?.offset ?? 0,
    },
    ...(params?.skipGlobalLoading ? { skipGlobalLoading: true } : {}),
  });
  return data;
}

export async function getOrgChartBatch(batchId: number, options?: { skipGlobalLoading?: boolean }): Promise<OrgChartUploadBatchDetail> {
  const { data } = await apiClient.get<OrgChartUploadBatchDetail>(
    `/api/v1/org-chart-batches/${batchId}`,
    options?.skipGlobalLoading ? { skipGlobalLoading: true } : undefined,
  );
  return data;
}

export async function downloadMappingTemplate(batchId: number): Promise<void> {
  const response = await apiClient.get(
    `/api/v1/org-chart-batches/${batchId}/mapping-template`,
    { responseType: 'blob', skipGlobalLoading: true },
  );
  const url = URL.createObjectURL(new Blob([response.data]));
  const a = document.createElement('a');
  a.href = url;
  a.download = `org_chart_mapping_batch_${batchId}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

export async function downloadStatusReport(batchId: number): Promise<void> {
  const response = await apiClient.get(
    `/api/v1/org-chart-batches/${batchId}/status-report`,
    { responseType: 'blob', skipGlobalLoading: true },
  );
  const url = URL.createObjectURL(new Blob([response.data]));
  const a = document.createElement('a');
  a.href = url;
  a.download = `org_chart_status_report_batch_${batchId}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

export async function uploadMappingXlsx(
  batchId: number,
  xlsxFile: File,
  reviewCycleId?: string,
): Promise<MappingUploadResponse> {
  const form = new FormData();
  form.append('file', xlsxFile);
  const params = reviewCycleId ? `?review_cycle_id=${encodeURIComponent(reviewCycleId)}` : '';
  const { data } = await apiClient.post<MappingUploadResponse>(
    `/api/v1/org-chart-batches/${batchId}/mapping-upload${params}`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return data;
}
