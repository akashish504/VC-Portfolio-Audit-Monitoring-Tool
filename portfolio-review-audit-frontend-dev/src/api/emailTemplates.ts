import apiClient from '@/api/axios';

export interface ApiEmailTemplate {
  id: string;
  template_name: string | null;
  subject: string | null;
  body: string | null;
  version_id: string | null;
  version_name: string | null;
  is_active: boolean | null;
  created_at: string;
  updated_at: string | null;
}

export async function listActiveEmailTemplates() {
  const res = await apiClient.get('/api/v1/email/templates');
  return res.data as ApiEmailTemplate[];
}

export async function listAllEmailTemplateVersions() {
  const res = await apiClient.get('/api/v1/email/templates/list');
  return res.data as ApiEmailTemplate[];
}

export async function createEmailTemplate(payload: {
  template_name: string;
  subject: string;
  body: string;
  version_name?: string;
}) {
  const res = await apiClient.post('/api/v1/email/templates', payload);
  return res.data as ApiEmailTemplate;
}

export async function activateEmailTemplate(payload: { template_name: string; version_id: string }) {
  const res = await apiClient.post('/api/v1/email/templates/activate', payload);
  return res.data as { success: boolean };
}

export async function updateEmailTemplateVersion(
  templateId: string,
  payload: {
    subject: string;
    body: string;
    version_name?: string;
  },
) {
  const res = await apiClient.put(`/api/v1/email/templates/${templateId}`, payload);
  return res.data as ApiEmailTemplate;
}

export interface ApiEmailTemplateHistory {
  id: number;
  template_id: string | null;
  event: string;
  meta: Record<string, unknown>;
  created_at: string;
}

export async function listEmailTemplateHistory(templateId: string) {
  const res = await apiClient.get(`/api/v1/email/templates/${templateId}/history`);
  return res.data as ApiEmailTemplateHistory[];
}

