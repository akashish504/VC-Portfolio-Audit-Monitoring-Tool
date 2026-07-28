import { useState } from 'react';
import { AlertTriangle, Loader2, Send, X } from 'lucide-react';
import { toast } from 'sonner';
import _JoditEditor from 'jodit-react';
// jodit-react ships a CJS UMD bundle; Vite may resolve it as a namespace object.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const JoditEditor = (_JoditEditor as any).default ?? _JoditEditor;
import DOMPurify from 'dompurify';
import 'jodit/es5/jodit.min.css';

import { isReminderSelectionRequired, sendReminder } from '@/api/emailReminders';

interface Props {
  reminderNumber: 1 | 2;
  portfolioCompanyId: number;
  threadId: string;
  initialTo: string[];
  initialCc: string[];
  initialSubject: string;
  initialBody: string;
  /** True when the prefilled body/subject still has unresolved {{placeholders}}. */
  hasUnresolved?: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

const emailRegex = /^[\w.!#$%&'*+/=?^`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

/**
 * Editable, pre-filled Reminder 1 / Reminder 2 draft. The reminder is threaded into
 * the original discrepancy email by passing the current thread_id, so the backend
 * never has to ask which thread to use.
 */
export function CompanyReminderForm({
  reminderNumber,
  portfolioCompanyId,
  threadId,
  initialTo,
  initialCc,
  initialSubject,
  initialBody,
  hasUnresolved,
  onClose,
  onSuccess,
}: Props) {
  const [toEmails, setToEmails] = useState<string[]>(initialTo);
  const [ccEmails, setCcEmails] = useState<string[]>(initialCc);
  const [subject, setSubject] = useState(initialSubject);
  const [body, setBody] = useState(initialBody);
  const [newTo, setNewTo] = useState('');
  const [newCc, setNewCc] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addTo = () => {
    const v = newTo.trim();
    if (v && emailRegex.test(v) && !toEmails.map((e) => e.toLowerCase()).includes(v.toLowerCase())) {
      setToEmails((p) => [...p, v]);
    }
    setNewTo('');
  };
  const addCc = () => {
    const v = newCc.trim();
    if (v && emailRegex.test(v) && !ccEmails.map((e) => e.toLowerCase()).includes(v.toLowerCase())) {
      setCcEmails((p) => [...p, v]);
    }
    setNewCc('');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!body.trim()) {
      setError('Reminder body cannot be empty.');
      return;
    }
    if (toEmails.length === 0) {
      setError('At least one To recipient is required.');
      return;
    }
    setIsSubmitting(true);
    setError(null);
    try {
      const res = await sendReminder({
        portfolio_company_id: portfolioCompanyId,
        reminder_number: reminderNumber,
        to_addrs: toEmails,
        cc_addrs: ccEmails,
        subject,
        body_html: DOMPurify.sanitize(body),
        thread_id: threadId,
      });
      if (isReminderSelectionRequired(res)) {
        // We passed an explicit thread_id, so this should not happen — surface it rather than silently dropping.
        setError('Multiple discrepancy threads were found for this company. Please reopen the reminder from the correct thread.');
        setIsSubmitting(false);
        return;
      }
      toast.success(`Reminder ${reminderNumber} sent`);
      onSuccess();
      onClose();
    } catch (err) {
      const msg =
        (err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message ||
        `Failed to send Reminder ${reminderNumber}. Please try again.`;
      setError(msg);
      toast.error(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="mb-3 relative">
      <div className="bg-white rounded-lg border border-indigo-200 p-4 shadow-sm">
        <div className="flex justify-between items-center mb-4">
          <h4 className="text-sm font-semibold text-indigo-900">Reminder {reminderNumber}</h4>
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="p-1 hover:bg-gray-100 rounded-full transition-colors"
          >
            <X size={14} />
          </button>
        </div>

        {hasUnresolved && (
          <div className="mb-3 flex items-start gap-2 p-2 bg-amber-50 border border-amber-200 rounded-md">
            <AlertTriangle size={14} className="text-amber-600 mt-0.5 shrink-0" />
            <p className="text-xs text-amber-700">
              Some template fields could not be auto-filled and still show as{' '}
              <code className="font-mono">{'{{placeholder}}'}</code>. Please complete them before sending.
            </p>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          {/* To */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">To:</label>
            <div className="flex flex-wrap gap-1.5 min-h-[36px] p-2 border border-gray-200 rounded-md bg-gray-50">
              {toEmails.map((email, i) => (
                <span key={i} className="inline-flex items-center px-2 py-0.5 bg-blue-100 text-blue-800 text-xs rounded-full">
                  {email}
                  {toEmails.length > 1 && (
                    <button type="button" onClick={() => setToEmails((p) => p.filter((_, j) => j !== i))} className="ml-1 text-blue-500 hover:text-blue-700">
                      <X size={10} />
                    </button>
                  )}
                </span>
              ))}
              <input
                type="email"
                value={newTo}
                onChange={(e) => setNewTo(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTo(); } }}
                onBlur={addTo}
                placeholder="Add email"
                disabled={isSubmitting}
                className="px-2 py-0.5 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
              />
            </div>
          </div>

          {/* CC */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">CC:</label>
            <div className="flex flex-wrap gap-1.5 min-h-[36px] p-2 border border-gray-200 rounded-md bg-gray-50">
              {ccEmails.map((email, i) => (
                <span key={i} className="inline-flex items-center px-2 py-0.5 bg-gray-200 text-gray-700 text-xs rounded-full">
                  {email}
                  <button type="button" onClick={() => setCcEmails((p) => p.filter((_, j) => j !== i))} className="ml-1 text-gray-500 hover:text-gray-700">
                    <X size={10} />
                  </button>
                </span>
              ))}
              <input
                type="email"
                value={newCc}
                onChange={(e) => setNewCc(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addCc(); } }}
                onBlur={addCc}
                placeholder="Add CC email"
                disabled={isSubmitting}
                className="px-2 py-0.5 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
              />
            </div>
          </div>

          {/* Subject */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Subject:</label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={isSubmitting}
              className="w-full px-3 py-1.5 text-xs border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>

          {/* Body */}
          <div className={isSubmitting ? 'opacity-60 pointer-events-none' : ''}>
            <JoditEditor
              value={body}
              onBlur={(content: string) => setBody(content)}
              config={{
                readonly: isSubmitting,
                height: 260,
                placeholder: 'Reminder message…',
                toolbarSticky: false,
                askBeforePasteHTML: false,
                askBeforePasteFromWord: false,
                defaultActionOnPaste: 'insert_as_html',
              }}
            />
          </div>

          {error && (
            <div className="p-2 bg-red-50 border border-red-200 rounded-md">
              <p className="text-xs text-red-600">{error}</p>
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <button
              type="submit"
              disabled={isSubmitting || !body.trim()}
              className="inline-flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white text-xs font-medium rounded-md hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-indigo-500"
            >
              {isSubmitting ? <><Loader2 size={12} className="animate-spin" /> Sending…</> : <><Send size={12} /> Send Reminder {reminderNumber}</>}
            </button>
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="px-4 py-2 text-xs font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
