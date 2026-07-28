import apiClient from '@/api/axios';
import type { ApiConfigRow, Page } from '@/api/reviewCycleAdjustments';
import { listAllEmailTemplateVersions } from '@/api/emailTemplates';

/**
 * Reminder 1 / Reminder 2 sending.
 *
 * The backend reminder engine already exists (`POST /api/v1/email/reminders/send`,
 * see backend `services/reminder_send.py`): it threads the reminder into the
 * original discrepancy email, blocks duplicates, and advances the affected
 * entities to "Query response reminder sent 1/2". It does NOT render the reminder
 * body — the caller supplies `subject` + `body_html`. So here we resolve the
 * configured Reminder template (same ConfigTable source the Settings → Email
 * Template Assignment tab writes) and fill its placeholders client-side, then let
 * the user review/edit before sending.
 */

const REMINDER_CONFIG_KEY: Record<1 | 2, string> = {
  1: 'email_templates.reminder_1',
  2: 'email_templates.reminder_2',
};

export interface ReminderSendResult {
  success: boolean;
  thread_id: string;
  message_id: string;
  affected_entity_ids: number[];
}

export interface ReminderThreadCandidate {
  thread_id: string;
  subject: string | null;
  latest_sent_at: string | null;
  affected_entity_ids: number[];
}

export interface ReminderSelectionRequired {
  selection_required: true;
  candidates: ReminderThreadCandidate[];
}

export type ReminderSendResponse = ReminderSendResult | ReminderSelectionRequired;

export function isReminderSelectionRequired(r: ReminderSendResponse): r is ReminderSelectionRequired {
  return (r as ReminderSelectionRequired).selection_required === true;
}

export interface ResolvedReminderTemplate {
  templateId: string;
  templateName: string | null;
  subject: string;
  body: string;
}

/**
 * Resolve which email template is assigned to Reminder 1 / Reminder 2 via the
 * ConfigTable (`email_templates.reminder_1` / `_2`) and return its subject/body.
 * Returns null when no template is assigned (the caller should prompt the user to
 * configure one under Settings → Email Templates).
 */
export async function resolveReminderTemplate(
  reminderNumber: 1 | 2,
): Promise<ResolvedReminderTemplate | null> {
  const [configRes, versions] = await Promise.all([
    apiClient.get<Page<ApiConfigRow>>('/api/v1/config', { params: { limit: 500, offset: 0 } }),
    listAllEmailTemplateVersions(),
  ]);
  const rows = configRes.data.items ?? [];
  const row = rows.find((r) => r.key === REMINDER_CONFIG_KEY[reminderNumber]);
  const stored = row?.value as Record<string, unknown> | string | undefined;
  const templateId =
    typeof stored === 'object' && stored !== null
      ? String(stored.value ?? stored.template_id ?? stored.id ?? '')
      : typeof stored === 'string'
      ? stored
      : '';
  if (!templateId) return null;
  const tpl = versions.find((t) => t.id === templateId);
  if (!tpl) return null;
  return {
    templateId,
    templateName: tpl.template_name,
    subject: tpl.subject ?? '',
    body: tpl.body ?? '',
  };
}

// Mirrors the backend placeholder grammar (draft_email._PLACEHOLDER_RE).
const PLACEHOLDER_SOURCE = '\\{\\{\\s*([a-zA-Z_][a-zA-Z0-9_]*)\\s*\\}\\}';

/** Substitute `{{placeholders}}`; unknown placeholders are left intact on purpose. */
export function renderReminderTemplate(text: string, variables: Record<string, string>): string {
  return (text || '').replace(new RegExp(PLACEHOLDER_SOURCE, 'g'), (whole, key: string) =>
    Object.prototype.hasOwnProperty.call(variables, key) ? variables[key] : whole,
  );
}

/** True if any `{{placeholder}}` remains unresolved (e.g. a `{{financials}}` table the browser can't build). */
export function hasUnresolvedPlaceholders(text: string): boolean {
  return new RegExp(PLACEHOLDER_SOURCE).test(text || '');
}

/** Extract a 4-digit FY year (20xx) from the company's FY fields — mirrors backend `_fy_year_from_text`. */
export function deriveFyYear(...candidates: (string | null | undefined)[]): string {
  for (const c of candidates) {
    const m = /\b(20\d{2})\b/.exec(c || '');
    if (m) return m[1];
  }
  return '';
}

export async function sendReminder(payload: {
  portfolio_company_id: number;
  reminder_number: 1 | 2;
  to_addrs: string[];
  cc_addrs: string[];
  subject: string;
  body_html: string;
  thread_id?: string;
  attachment_keys?: string[];
  attachments_id?: string;
}): Promise<ReminderSendResponse> {
  const res = await apiClient.post('/api/v1/email/reminders/send', {
    portfolio_company_id: payload.portfolio_company_id,
    reminder_number: payload.reminder_number,
    to_addrs: payload.to_addrs,
    cc_addrs: payload.cc_addrs,
    subject: payload.subject,
    body_html: payload.body_html,
    thread_id: payload.thread_id,
    attachment_keys: payload.attachment_keys ?? [],
    attachments_id: payload.attachments_id,
  });
  return res.data as ReminderSendResponse;
}
