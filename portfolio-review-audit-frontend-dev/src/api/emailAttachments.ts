import apiClient from '@/api/axios';

export async function uploadEmailAttachments(payload: { files: File[]; attachments_id?: string }) {
  const form = new FormData();
  if (payload.attachments_id) form.append('attachments_id', payload.attachments_id);
  for (const f of payload.files) form.append('attachments', f);
  const res = await apiClient.post('/api/v1/email/attachments/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data as { attachments_id: string; attachment_keys: string[]; attachment_urls: string[] };
}

export async function presignEmailAttachment(object_path: string) {
  const res = await apiClient.get('/api/v1/email/attachments/presign', { params: { object_path } });
  return res.data as { url: string };
}

