import apiClient from './axios';

export interface SyncAlert {
  id: number;
  company_id: string;
  company_name: string;
  investment_stage: string | null;
  message: string;
  is_read: boolean;
  read_at: string | null;
  acknowledged_by_user_email: string | null;
  created_at: string;
}

export interface SyncAlertPage {
  items: SyncAlert[];
  total: number;
}

export interface SyncAlertUnreadCount {
  count: number;
}

export interface SyncAlertAcknowledgeAllResult {
  acknowledged_count: number;
}

export async function listSyncAlerts(params: {
  status?: 'all' | 'unread' | 'read';
  limit?: number;
  offset?: number;
}): Promise<SyncAlertPage> {
  const { data } = await apiClient.get<SyncAlertPage>('/api/v1/sync-alerts', { params });
  return data;
}

export async function getSyncAlertsUnreadCount(): Promise<SyncAlertUnreadCount> {
  const { data } = await apiClient.get<SyncAlertUnreadCount>('/api/v1/sync-alerts/unread-count');
  return data;
}

export async function acknowledgeSyncAlert(id: number): Promise<SyncAlert> {
  const { data } = await apiClient.post<SyncAlert>(`/api/v1/sync-alerts/${id}/acknowledge`);
  return data;
}

export async function acknowledgeAllSyncAlerts(): Promise<SyncAlertAcknowledgeAllResult> {
  const { data } = await apiClient.post<SyncAlertAcknowledgeAllResult>(
    '/api/v1/sync-alerts/acknowledge-all'
  );
  return data;
}
