import apiClient from '@/api/axios';

export interface ApiDraftEmail {
  id: string;
  portfolio_company_id: number | null;
  subject: string | null;
  to_add: string[] | null;
  cc: string[] | null;
  email_body: string | null;
  template_id: string | null;
  /** Logical template name (active version) used when the draft was generated. */
  template_name: string | null;
  attachments: string[] | null;
  attachments_id: string | null;
  created_at: string;
  updated_at: string | null;
}

export async function getCompanyDraftEmail(portfolioCompanyId: number) {
  const res = await apiClient.get('/api/v1/email/drafts', { params: { portfolio_company_id: portfolioCompanyId } });
  return res.data as ApiDraftEmail | null;
}

export async function generateCompanyDraftEmail(payload: {
  portfolio_company_id: number;
  template_name?: string;
  overwrite?: boolean;
}) {
  const res = await apiClient.post('/api/v1/email/drafts/generate', payload);
  return res.data as ApiDraftEmail;
}

export async function updateCompanyDraftEmail(
  draftId: string,
  payload: {
    subject?: string;
    to_add?: string[];
    cc?: string[];
    email_body?: string;
    attachments?: string[];
    attachments_id?: string;
  },
) {
  const res = await apiClient.put(`/api/v1/email/drafts/${draftId}`, payload);
  return res.data as ApiDraftEmail;
}

export async function sendCompanyDraftEmail(draftId: string) {
  const res = await apiClient.post(`/api/v1/email/drafts/${draftId}/send`);
  return res.data as { queued: boolean; draft_id: string };
}

