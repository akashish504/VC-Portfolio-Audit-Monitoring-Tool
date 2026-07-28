import apiClient from '@/api/axios';

export interface ApiEmailMessage {
  id: string;
  thread_id: string | null;
  portfolio_company_id: number | null;
  sender: string | null;
  recipients: string[] | null;
  cc: string[] | null;
  subject: string | null;
  body: string | null;
  attachments?: string[] | null;
  sent_at: string | null;
  is_inbound: boolean | null;
}

export interface ApiEmailThreadSummary {
  thread_id: string;
  subject: string | null;
  latest_sent_at: string | null;
  email_count: number;
  sender: string | null;
}

export interface ApiEmailThread {
  thread_id: string;
  latest_sent_at: string | null;
  subject: string | null;
  email_count: number;
  emails: ApiEmailMessage[];
}

export async function listEmailThreads(portfolioCompanyId: number) {
  const res = await apiClient.get('/api/v1/email/threads', { params: { portfolio_company_id: portfolioCompanyId } });
  return res.data as ApiEmailThread[];
}

export async function getCompanyContacts(portfolioCompanyId: number) {
  const res = await apiClient.get('/api/v1/email/company-contacts', {
    params: { portfolio_company_id: portfolioCompanyId },
  });
  return res.data as { poc_email_ids: string[]; poc_cc_email_ids: string[] };
}

export async function getSuggestedEmails() {
  const res = await apiClient.get('/api/v1/email/suggested-emails');
  return res.data as { emails: string[] };
}

export async function putSuggestedEmails(emails: string[]) {
  const res = await apiClient.put('/api/v1/email/suggested-emails', { emails });
  return res.data as { emails: string[] };
}

export async function listUntaggedThreads() {
  const res = await apiClient.get('/api/v1/email/threads/untagged');
  return res.data as ApiEmailThreadSummary[];
}

export async function getUntaggedThreadDetail(threadId: string) {
  const res = await apiClient.get(`/api/v1/email/threads/untagged/${encodeURIComponent(threadId)}`);
  return res.data as ApiEmailThread;
}

export interface AuditedFinancialsFile {
  id: number;
  filename: string | null;
  status: string | null;
  content_type: string | null;
  created_at: string | null;
  source_attachment_key: string | null;
}

export interface AttachmentIngestResult {
  s3_key: string;
  filename: string;
  status: 'success' | 'skipped' | 'failed';
  file_id: number | null;
  error: string | null;
  skip_reason: string | null;
}

export interface AuditedFinancialsEmail {
  id: string;
  thread_id: string | null;
  portfolio_company_id: number | null;
  portfolio_company_name: string | null;
  sender: string | null;
  subject: string | null;
  email_type: string | null;
  sent_at: string | null;
  is_inbound: boolean | null;
  attachments: string[] | null;
  classified_by: 'system' | 'user';
  files: AuditedFinancialsFile[];
}

export interface TagWithEntityPayload {
  email_id: string;
  portfolio_company_id: number;
  entity_id?: number | null;
  review_cycle_id?: string | null;
  force_reprocess?: boolean;
}

export interface TagWithEntityResult {
  success: boolean;
  email_id: string;
  portfolio_company_id: number;
  entity_id: number | null;
  review_cycle_id: string | null;
  attachments_processed: number;
  attachment_results: AttachmentIngestResult[];
  message: string | null;
}

export async function tagEmailWithEntity(payload: TagWithEntityPayload) {
  const res = await apiClient.post('/api/v1/email/tag-with-entity', payload);
  return res.data as TagWithEntityResult;
}

export async function listAuditedFinancialsEmails() {
  const res = await apiClient.get('/api/v1/email/audited-financials');
  return res.data as AuditedFinancialsEmail[];
}

export async function tagThread(payload: {
  portfolio_company_id: number;
  thread_id?: string;
  message_id?: string;
}) {
  const res = await apiClient.post('/api/v1/email/threads/tag', payload);
  return res.data as {
    success: boolean;
    thread_id: string | null;
    portfolio_company_id: number;
    affected_rows: number;
    message: string | null;
  };
}

export async function sendEmail(payload: {
  portfolio_company_id: number;
  to_addrs: string[];
  cc_addrs: string[];
  subject: string;
  body_html: string;
  thread_id?: string;
  reply_to_message_id?: string;
  attachment_keys?: string[];
  attachments_id?: string;
  /** New files to upload inline (sent as multipart/form-data). */
  attachments?: File[];
}) {
  const form = new FormData();
  form.append('portfolio_company_id', String(payload.portfolio_company_id));
  form.append('to_addrs', payload.to_addrs.join(',,'));
  form.append('cc_addrs', (payload.cc_addrs || []).join(',,'));
  form.append('subject', payload.subject);
  form.append('body_html', payload.body_html);
  if (payload.thread_id) form.append('thread_id', payload.thread_id);
  if (payload.reply_to_message_id) form.append('reply_to_message_id', payload.reply_to_message_id);
  if (payload.attachment_keys?.length) form.append('attachment_keys', JSON.stringify(payload.attachment_keys));
  if (payload.attachments_id) form.append('attachments_id', payload.attachments_id);
  for (const file of payload.attachments || []) {
    form.append('attachments', file);
  }
  const res = await apiClient.post('/api/v1/email/send', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data as { success: boolean; thread_id: string; message_id: string };
}

