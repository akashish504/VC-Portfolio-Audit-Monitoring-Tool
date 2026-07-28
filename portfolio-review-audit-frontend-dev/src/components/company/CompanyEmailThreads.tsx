import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Bell, Loader2, Mail, Paperclip } from 'lucide-react';
import { toast } from 'sonner';
import DOMPurify from 'dompurify';

import { getCompanyContacts, getSuggestedEmails, listEmailThreads, type ApiEmailThread } from '@/api/emailThreads';
import { presignEmailAttachment } from '@/api/emailAttachments';
import { getPortfolioCompany } from '@/api/portfolio';
import {
  deriveFyYear,
  hasUnresolvedPlaceholders,
  renderReminderTemplate,
  resolveReminderTemplate,
} from '@/api/emailReminders';
import { EmailReplyForm } from './EmailReplyForm';
import { CompanyReminderForm } from './CompanyReminderForm';

export function CompanyEmailThreads({ companyId }: { companyId: string }) {
  const portfolioCompanyId = useMemo(() => {
    const raw = String(companyId);
    const m = raw.match(/^pc-(\d+)$/);
    if (m) return Number(m[1]);
    if (/^\d+$/.test(raw)) return Number(raw);
    return NaN;
  }, [companyId]);

  const [threads, setThreads] = useState<ApiEmailThread[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [pocEmails, setPocEmails] = useState<string[]>([]);
  const [pocCcEmails, setPocCcEmails] = useState<string[]>([]);
  const [suggestedEmails, setSuggestedEmails] = useState<string[]>([]);

  // which message has the inline reply form open
  const [replyToMessageId, setReplyToMessageId] = useState<string | null>(null);

  // Reminder 1 / 2 — a prefilled, editable draft for the selected thread.
  const [reminderForm, setReminderForm] = useState<{
    number: 1 | 2;
    to: string[];
    cc: string[];
    subject: string;
    body: string;
    hasUnresolved: boolean;
  } | null>(null);
  const [reminderLoading, setReminderLoading] = useState<1 | 2 | null>(null);
  // Bumped on every open so the form remounts with fresh prefill (e.g. switching 1 ↔ 2).
  const [reminderNonce, setReminderNonce] = useState(0);

  const refresh = async () => {
    if (!Number.isFinite(portfolioCompanyId)) return;
    setLoading(true);
    try {
      const rows = await listEmailThreads(portfolioCompanyId);
      setThreads(rows);
      if (!selectedThreadId && rows.length > 0) setSelectedThreadId(rows[0].thread_id);
    } catch {
      toast.error('Failed to load email threads');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [portfolioCompanyId]);

  useEffect(() => {
    if (!Number.isFinite(portfolioCompanyId)) return;
    getCompanyContacts(portfolioCompanyId)
      .then((data) => {
        setPocEmails(data.poc_email_ids);
        setPocCcEmails(data.poc_cc_email_ids);
      })
      .catch(() => {});
  }, [portfolioCompanyId]);

  useEffect(() => {
    getSuggestedEmails()
      .then((data) => setSuggestedEmails(data.emails))
      .catch(() => {});
  }, []);

  const selectedThread = threads.find((t) => t.thread_id === selectedThreadId) || null;

  const openAttachment = async (key: string) => {
    try {
      const res = await presignEmailAttachment(key);
      window.open(res.url, '_blank', 'noopener,noreferrer');
    } catch {
      toast.error('Failed to open attachment');
    }
  };

  // Prepare a Reminder 1/2 draft: resolve the configured template, fill in the
  // company details, prefill recipients from the original outbound email, and open
  // the editable form. The actual send (POST /email/reminders/send) threads it into
  // this discrepancy thread and advances the affected entities on the backend.
  const openReminder = async (n: 1 | 2) => {
    if (!Number.isFinite(portfolioCompanyId)) return;
    const thread = threads.find((t) => t.thread_id === selectedThreadId);
    if (!thread) return;
    setReminderLoading(n);
    try {
      const [tpl, company] = await Promise.all([
        resolveReminderTemplate(n),
        getPortfolioCompany(portfolioCompanyId).catch(() => null),
      ]);
      if (!tpl) {
        toast.error(`No Reminder ${n} template is assigned. Set one under Settings → Email Templates.`);
        return;
      }

      // Recipients default to whoever the first (outbound) discrepancy email went to —
      // "the same company that mail was sent to before" — falling back to the stored POC.
      const outbound = thread.emails.find((m) => m.is_inbound === false) || thread.emails[0];
      const to = (outbound?.recipients || []).filter(Boolean);
      const toList = to.length
        ? to
        : pocEmails.length
        ? pocEmails
        : company?.contact_email_id
        ? [company.contact_email_id]
        : [];
      const ccSet = new Set<string>([...(outbound?.cc || []).filter(Boolean), ...pocCcEmails]);
      ccSet.add('portfolioreview@peakxv.com');
      toList.forEach((e) => ccSet.delete(e));

      // Deliberately omit `financials` so a {{financials}} placeholder (which the
      // browser can't rebuild) stays visible and trips the unresolved-field warning.
      const variables: Record<string, string> = {
        company_name: company?.name || '',
        poc_name: company?.contact_name || '',
        fy_year: deriveFyYear(company?.fy_end, company?.fy_end_date),
      };
      const renderedBody = renderReminderTemplate(tpl.body, variables);
      const renderedSubject =
        renderReminderTemplate(tpl.subject, variables).trim() ||
        (thread.subject ? (thread.subject.startsWith('Re: ') ? thread.subject : `Re: ${thread.subject}`) : `Reminder ${n}`);

      setReplyToMessageId(null);
      setReminderNonce((x) => x + 1);
      setReminderForm({
        number: n,
        to: toList,
        cc: Array.from(ccSet),
        subject: renderedSubject,
        body: renderedBody,
        hasUnresolved: hasUnresolvedPlaceholders(renderedBody) || hasUnresolvedPlaceholders(renderedSubject),
      });
    } catch {
      toast.error('Failed to prepare reminder');
    } finally {
      setReminderLoading(null);
    }
  };

  if (!loading && threads.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2">
        <Mail className="h-8 w-8 text-gray-300" />
        <p className="text-sm text-gray-500">No email threads for this entity</p>
      </div>
    );
  }

  return (
    <div className="h-full flex">
      {/* Thread list sidebar */}
      <div className="w-72 border-r border-gray-200 bg-white flex flex-col shrink-0">
        <div className="px-3 py-2.5 border-b border-gray-200">
          <p className="text-xs text-gray-500">
            {threads.length} thread{threads.length !== 1 ? 's' : ''}
          </p>
        </div>
        <div className="flex-1 overflow-auto">
          {threads.map((t) => (
            <button
              key={t.thread_id}
              onClick={() => {
                setSelectedThreadId(t.thread_id);
                setReplyToMessageId(null);
              }}
              className={`w-full text-left px-3 py-3 border-b border-gray-100 hover:bg-gray-50 transition-all ${
                selectedThreadId === t.thread_id ? 'bg-gray-50' : ''
              }`}
            >
              <div className="text-sm font-medium text-gray-900 truncate">{t.subject || '(No subject)'}</div>
              <div className="text-xs text-gray-500 mt-0.5">
                {t.latest_sent_at ? new Date(t.latest_sent_at).toLocaleString() : '—'} • {t.email_count} message
                {t.email_count !== 1 ? 's' : ''}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Thread detail */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {selectedThread ? (
          <>
            <div className="px-4 py-3 border-b border-gray-200 bg-white shrink-0">
              <button
                onClick={() => { setSelectedThreadId(null); setReplyToMessageId(null); setReminderForm(null); }}
                className="text-xs text-gray-500 hover:text-gray-900 mb-2 flex items-center gap-1 transition-all"
              >
                <ArrowLeft className="h-3 w-3" /> Back
              </button>
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-bold text-gray-900 min-w-0 truncate">{selectedThread.subject || '(No subject)'}</h2>
                <div className="flex items-center gap-1.5 shrink-0">
                  {([1, 2] as const).map((n) => (
                    <button
                      key={n}
                      type="button"
                      onClick={() => openReminder(n)}
                      disabled={reminderLoading !== null}
                      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium border transition-all disabled:opacity-50 ${
                        reminderForm?.number === n
                          ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                          : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
                      }`}
                      title={`Send Reminder ${n} into this thread`}
                    >
                      {reminderLoading === n ? <Loader2 className="h-3 w-3 animate-spin" /> : <Bell className="h-3 w-3" />}
                      Reminder {n}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Messages + inline reply forms */}
            <div className="flex-1 overflow-auto p-4 bg-gray-50">
              <div className="space-y-3">
                {reminderForm && (
                  <CompanyReminderForm
                    key={`reminder-${reminderNonce}`}
                    reminderNumber={reminderForm.number}
                    portfolioCompanyId={portfolioCompanyId}
                    threadId={selectedThread.thread_id}
                    initialTo={reminderForm.to}
                    initialCc={reminderForm.cc}
                    initialSubject={reminderForm.subject}
                    initialBody={reminderForm.body}
                    hasUnresolved={reminderForm.hasUnresolved}
                    onClose={() => setReminderForm(null)}
                    onSuccess={() => { setReminderForm(null); refresh(); }}
                  />
                )}
                {selectedThread.emails.map((m) => (
                  <div key={m.id}>
                    <div className="bg-white border border-gray-200 rounded-lg p-3">
                      <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-xs text-gray-500">
                            From: <span className="font-mono text-gray-800">{m.sender || '—'}</span>
                          </div>
                          <div className="text-xs text-gray-500 mt-0.5">
                            To: <span className="font-mono text-gray-800">{(m.recipients || []).join(', ') || '—'}</span>
                          </div>
                          <div className="text-xs text-gray-500 mt-0.5">
                            Sent: <span className="font-mono text-gray-800">{m.sent_at ? new Date(m.sent_at).toLocaleString() : '—'}</span>
                          </div>
                        </div>
                        <button
                          onClick={() => setReplyToMessageId((prev) => (prev === m.id ? null : m.id))}
                          className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg font-medium text-xs border transition-all ${
                            replyToMessageId === m.id
                              ? 'border-blue-300 bg-blue-50 text-blue-700 hover:bg-blue-100'
                              : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
                          }`}
                          title="Reply to this message"
                        >
                          {replyToMessageId === m.id ? 'Cancel reply' : 'Reply'}
                        </button>
                      </div>

                      <div
                        className="text-sm text-gray-900 leading-relaxed mt-3"
                        dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(m.body || '') }}
                      />

                      {(m.attachments || []).length ? (
                        <div className="mt-3 border-t border-gray-100 pt-2">
                          <div className="text-[11px] font-medium text-gray-500 mb-1">Attachments</div>
                          <div className="flex flex-wrap gap-2">
                            {(m.attachments || []).map((k) => {
                              const name = String(k).split('/').pop() || k;
                              return (
                                <button
                                  key={k}
                                  type="button"
                                  onClick={() => openAttachment(k)}
                                  className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded-full border border-gray-200 bg-gray-50 text-blue-700 hover:text-blue-900"
                                  title={k}
                                >
                                  <Paperclip className="h-3 w-3" /> {name}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      ) : null}
                    </div>

                    {/* Inline reply form — opens directly below the message being replied to */}
                    {replyToMessageId === m.id && (
                      <EmailReplyForm
                        thread={selectedThread}
                        originalEmail={m}
                        portfolioCompanyId={portfolioCompanyId}
                        pocEmails={pocEmails}
                        pocCcEmails={pocCcEmails}
                        suggestedEmails={suggestedEmails}
                        onClose={() => setReplyToMessageId(null)}
                        onSuccess={() => {
                          setReplyToMessageId(null);
                          refresh();
                        }}
                      />
                    )}
                  </div>
                ))}
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center bg-gray-50">
            <div className="text-center">
              <Mail className="h-8 w-8 text-gray-300 mx-auto mb-2" />
              <p className="text-xs text-gray-500">Select a thread to view</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
