import apiClient from '@/api/axios';

export interface DashboardUploadResult {
  rows_processed: number;
  rows_updated: number;
  rows_skipped: number;
  error_count: number;
  errors: { row: number; reason: string }[];
}

export async function downloadDashboardData(
  reviewCycleId: string,
  scopedIn: boolean,
): Promise<void> {
  const res = await apiClient.get('/api/v1/dashboard-data/download', {
    params: { review_cycle_id: reviewCycleId, scoped_in: scopedIn },
    responseType: 'blob',
  });

  const today = new Date().toISOString().slice(0, 10);
  const safeId = reviewCycleId.replace(/\//g, '-').replace(/\s+/g, '_');
  const scopingSuffix = scopedIn ? '_scoped_in' : '_scoped_out';
  const filename = `dashboard_data_${safeId}${scopingSuffix}_${today}.xlsx`;

  const url = URL.createObjectURL(res.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function uploadDashboardData(
  file: File,
  reviewCycleId: string,
): Promise<DashboardUploadResult> {
  const form = new FormData();
  form.append('file', file);
  const res = await apiClient.post<DashboardUploadResult>(
    '/api/v1/dashboard-data/upload',
    form,
    {
      params: { review_cycle_id: reviewCycleId },
      headers: { 'Content-Type': 'multipart/form-data' },
    },
  );
  return res.data;
}
