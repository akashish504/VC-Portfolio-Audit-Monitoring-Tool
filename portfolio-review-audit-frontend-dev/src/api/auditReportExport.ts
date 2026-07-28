/**
 * Audit-report XLSX async export API client.
 *
 * POST   /api/v1/export/audit-report          → start job
 * GET    /api/v1/export/audit-report/{job_id} → poll status
 * GET    /api/v1/export/audit-report/{job_id}/download → download XLSX
 */
import apiClient from './axios';

export type ExportJobStatus = 'pending' | 'running' | 'done' | 'failed';
export type OutputCurrency = 'USD' | 'INR';
export const SUPPORTED_OUTPUT_CURRENCIES: readonly OutputCurrency[] = ['USD', 'INR'];

export type ReportType = 'reconciliation' | 'extracted_financials';
export const SUPPORTED_REPORT_TYPES: readonly ReportType[] = [
  'reconciliation',
  'extracted_financials',
];

export interface ExportJobResponse {
  job_id: string;
  status: ExportJobStatus;
  report_type: ReportType | null;
  output_currency: OutputCurrency | null;
  filename: string | null;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export async function startAuditReportExport(
  reviewCycleId: string | null,
  reportType: ReportType,
  outputCurrency?: OutputCurrency,
): Promise<ExportJobResponse> {
  const body: Record<string, unknown> = {
    review_cycle_id: reviewCycleId,
    report_type: reportType,
  };
  if (outputCurrency) {
    body.output_currency = outputCurrency;
  }
  const { data } = await apiClient.post<ExportJobResponse>('/api/v1/export/audit-report', body);
  return data;
}

export async function pollAuditReportExport(jobId: string): Promise<ExportJobResponse> {
  const { data } = await apiClient.get<ExportJobResponse>(
    `/api/v1/export/audit-report/${encodeURIComponent(jobId)}`,
  );
  return data;
}

export async function downloadAuditReportXlsx(jobId: string, filename: string): Promise<void> {
  const res = await apiClient.get(
    `/api/v1/export/audit-report/${encodeURIComponent(jobId)}/download`,
    { responseType: 'blob' },
  );
  const url = URL.createObjectURL(res.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
