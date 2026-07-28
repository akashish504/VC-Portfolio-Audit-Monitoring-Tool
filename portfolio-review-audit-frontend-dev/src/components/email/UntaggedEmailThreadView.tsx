import { memo, useEffect, useState } from 'react';
import {
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Download,
  Loader2,
  Mail,
  Paperclip,
  Plus,
  User,
} from 'lucide-react';
import { toast } from 'sonner';
import DOMPurify from 'dompurify';

import type { ApiEmailThread, ApiEmailMessage } from '@/api/emailThreads';
import { getUntaggedThreadDetail, tagThread } from '@/api/emailThreads';
import { presignEmailAttachment } from '@/api/emailAttachments';
import { resolveReviewCycleFromFyEnd } from '@/api/settings';
import { listPortfolioCompanies } from '@/api/portfolio';
import type { PortfolioCompany } from '@/types/domain';
import { DealSearchSelect } from '@/components/files/DealSearchSelect';
import { FyEndMonthYearPicker } from '@/components/files/FyEndMonthYearPicker';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function formatDate(iso: string) {
  const d = new Date(iso);
  return {
    date: d.toLocaleDateString([], {
      month: 'short',
      day: 'numeric',
      year: d.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined,
    }),
    time: d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  };
}

function getSenderDisplay(sender: string) {
  const m = sender.match(/^(.+?)\s*<(.+)>$/) ?? [null, sender, sender];
  return { name: (m[1] ?? sender).trim(), email: (m[2] ?? sender).trim() };
}

function renderBody(html: string) {
  const clean = DOMPurify.sanitize(html);
  return (
    <div
      dangerouslySetInnerHTML={{ __html: clean }}
      className="prose prose-sm max-w-none text-gray-800 overflow-x-auto"
    />
  );
}

function renderPreview(html: string) {
  const text = DOMPurify.sanitize(html, { ALLOWED_TAGS: [] }).replace(/\s+/g, ' ').trim();
  return <p className="text-sm text-gray-600 mt-1 line-clamp-1">{text}</p>;
}

// ---------------------------------------------------------------------------
// Email message card
// ---------------------------------------------------------------------------

function EmailMessageCard({
  message,
  defaultExpanded,
}: {
  message: ApiEmailMessage;
  defaultExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded ?? false);
  const { date, time } = formatDate(message.sent_at ?? new Date().toISOString());
  const sender = getSenderDisplay(message.sender ?? '');
  const hasAttachments = (message.attachments?.length ?? 0) > 0;

  const downloadAttachment = async (url: string) => {
    try {
      const key = (() => {
        const marker = '.amazonaws.com/';
        const i = url.indexOf(marker);
        if (i >= 0) return decodeURIComponent(url.substring(i + marker.length));
        try {
          const u = new URL(url);
          return decodeURIComponent(u.pathname.replace(/^\//, ''));
        } catch {
          return url;
        }
      })();
      const res = await presignEmailAttachment(key);
      const href = String((res as any)?.url ?? (res as any)?.data ?? url);
      let urlObj: URL | null = null;
      try { urlObj = new URL(href); } catch { toast.error('Invalid download URL'); return; }
      if (urlObj.protocol !== 'https:' || !urlObj.hostname.endsWith('amazonaws.com')) {
        toast.error('Blocked download: invalid domain');
        return;
      }
      window.open(urlObj.toString(), '_blank', 'noopener,noreferrer');
    } catch {
      window.open(url, '_blank');
    }
  };

  return (
    <div
      className={`bg-white rounded-lg border transition-all duration-200 overflow-x-auto ${
        !expanded ? 'border-blue-200 bg-blue-50/30' : 'border-gray-200'
      }`}
    >
      <div
        className="p-4 cursor-pointer hover:bg-gray-50 transition-colors duration-150"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="flex items-center justify-between">
          <div className="flex space-x-3 flex-1 min-w-0">
            <div
              className={`w-10 h-10 rounded-full flex items-center justify-center text-white font-medium shrink-0 ${
                message.is_inbound ? 'bg-blue-500' : 'bg-gray-500'
              }`}
            >
              {message.is_inbound ? <Mail size={16} /> : <User size={16} />}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center space-x-2 flex-wrap gap-y-1">
                <span className="font-medium text-gray-900 truncate">{sender.name}</span>
                {sender.name !== sender.email && (
                  <span className="text-sm text-gray-500 truncate">&lt;{sender.email}&gt;</span>
                )}
                <span
                  className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                    message.is_inbound ? 'bg-blue-100 text-blue-800' : 'bg-gray-100 text-gray-800'
                  }`}
                >
                  {message.is_inbound ? 'Inbound' : 'Outbound'}
                </span>
              </div>
              {!expanded && renderPreview(message.body ?? '')}
            </div>
          </div>
          <div className="flex items-center space-x-3 text-sm text-gray-500 shrink-0 ml-2">
            {hasAttachments && (
              <div className="flex items-center space-x-1">
                <Paperclip size={14} />
                <span>{message.attachments!.length}</span>
              </div>
            )}
            <div className="text-right">
              <div className="font-medium">{time}</div>
              <div className="text-xs">{date}</div>
            </div>
            <div className="text-gray-400">
              {expanded ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
            </div>
          </div>
        </div>
      </div>

      {expanded && (
        <div className="border-t border-gray-100">
          <div className="px-4 py-3 bg-gray-50/50 text-sm space-y-1">
            <div>
              <span className="font-medium text-gray-700">To: </span>
              <span className="text-gray-600">
                {Array.isArray(message.recipients)
                  ? message.recipients.join(', ')
                  : (message.recipients ?? '')}
              </span>
            </div>
            {(message.cc?.length ?? 0) > 0 && (
              <div>
                <span className="font-medium text-gray-700">CC: </span>
                <span className="text-gray-600">{message.cc!.join(', ')}</span>
              </div>
            )}
          </div>
          <div className="p-4">{renderBody(message.body ?? '')}</div>
          {hasAttachments && (
            <div className="px-4 pb-4">
              <div className="border-t border-gray-200 pt-3">
                <h4 className="text-sm font-medium text-gray-700 mb-2">
                  Attachments ({message.attachments!.length})
                </h4>
                <div className="space-y-2">
                  {message.attachments!.map((att, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between p-2 bg-gray-50 rounded border"
                    >
                      <div className="flex items-center space-x-2">
                        <Paperclip size={16} className="text-gray-400" />
                        <span className="text-sm font-medium text-gray-700 truncate max-w-xs">
                          {att}
                        </span>
                      </div>
                      <button
                        onClick={() => void downloadAttachment(att)}
                        className="p-1 hover:bg-gray-200 rounded transition-colors"
                      >
                        <Download size={16} className="text-gray-600" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tag dialog — FY end selects → resolve review cycle → pick company
// ---------------------------------------------------------------------------

type TagDialogProps = {
  open: boolean;
  threadSubject: string;
  onClose: () => void;
  onConfirm: (company: PortfolioCompany) => void;
};

function TagDialog({ open, threadSubject, onClose, onConfirm }: TagDialogProps) {
  const [fyEnd, setFyEnd] = useState('');
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState('');
  const [cycleLabel, setCycleLabel] = useState('');
  const [cycleId, setCycleId] = useState('');

  const [companies, setCompanies] = useState<PortfolioCompany[]>([]);
  const [companiesLoading, setCompaniesLoading] = useState(false);
  const [companyId, setCompanyId] = useState<number | null>(null);

  const reset = () => {
    setFyEnd('');
    setResolving(false);
    setResolveError('');
    setCycleLabel('');
    setCycleId('');
    setCompanies([]);
    setCompaniesLoading(false);
    setCompanyId(null);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  useEffect(() => {
    if (!open) return;
    if (!fyEnd) {
      setResolveError('');
      setCycleLabel('');
      setCycleId('');
      setCompanies([]);
      setCompanyId(null);
      return;
    }

    let cancelled = false;
    setResolveError('');
    setCycleLabel('');
    setCycleId('');
    setCompanies([]);
    setCompanyId(null);
    setResolving(true);
    setCompaniesLoading(true);

    void (async () => {
      try {
        const resolved = await resolveReviewCycleFromFyEnd(fyEnd);
        if (cancelled) return;
        setCycleId(resolved.review_cycle_id);
        setCycleLabel(resolved.review_cycle_name ?? resolved.review_cycle_id);
        const res = await listPortfolioCompanies({
          review_cycle_id: resolved.review_cycle_id,
          limit: 500,
          offset: 0,
        });
        if (cancelled) return;
        setCompanies(res.items ?? []);
      } catch {
        if (!cancelled) {
          setResolveError('No review cycle found for this FY end. Try a different month/year.');
        }
      } finally {
        if (!cancelled) {
          setResolving(false);
          setCompaniesLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, fyEnd]);

  const handleConfirm = () => {
    const selectedCompany = companies.find((c) => c.id === companyId);
    if (!selectedCompany) return;
    onConfirm(selectedCompany);
    reset();
  };

  const showCycleSection = !resolving && !resolveError && cycleId;

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) handleClose(); }}>
      <DialogContent className="max-w-md overflow-visible">
        <DialogHeader className="min-w-0">
          <DialogTitle>Tag Email Thread</DialogTitle>
          <DialogDescription className="truncate">
            {threadSubject || '(no subject)'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <FyEndMonthYearPicker
            value={fyEnd}
            onChange={setFyEnd}
            label="Month and Year"
            required
            id="thread-tag-fy-end"
          />

          {resolving && (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <Loader2 size={16} className="animate-spin" />
              <span>Looking up review cycle…</span>
            </div>
          )}

          {resolveError && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              {resolveError}
            </p>
          )}

          {showCycleSection && (
            <p className="text-xs text-green-700">
              Review cycle: <span className="font-semibold">{cycleLabel}</span>
            </p>
          )}

          {showCycleSection && (
            companiesLoading ? (
              <div className="flex items-center gap-2 text-sm text-gray-500 py-2">
                <Loader2 size={16} className="animate-spin" />
                <span>Loading companies…</span>
              </div>
            ) : (
              <DealSearchSelect
                label="Company"
                value={companyId}
                onChange={setCompanyId}
                options={companies.map((c) => ({ id: c.id, name: c.name }))}
                placeholder="Search companies…"
              />
            )
          )}
        </div>

        <DialogFooter>
          <button
            type="button"
            onClick={handleClose}
            className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={companyId == null}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            Tag Thread
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

type Props = {
  threadId: string;
  onTagged: () => void;
};

function UntaggedEmailThreadView({ threadId, onTagged }: Props) {
  const [thread, setThread] = useState<ApiEmailThread | null>(null);
  const [loadingThread, setLoadingThread] = useState(false);
  const [threadError, setThreadError] = useState<string | null>(null);

  useEffect(() => {
    if (!threadId) return;
    let cancelled = false;
    setThread(null);
    setThreadError(null);
    setLoadingThread(true);
    getUntaggedThreadDetail(threadId)
      .then((data) => { if (!cancelled) setThread(data); })
      .catch(() => { if (!cancelled) setThreadError('Failed to load thread'); })
      .finally(() => { if (!cancelled) setLoadingThread(false); });
    return () => { cancelled = true; };
  }, [threadId]);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [isTagging, setIsTagging] = useState(false);
  const [feedback, setFeedback] = useState<{
    type: 'success' | 'error' | 'info';
    text: string;
  } | null>(null);

  if (loadingThread) {
    return (
      <div className="flex items-center justify-center h-64 gap-3 text-gray-400">
        <Loader2 size={20} className="animate-spin" />
        <span className="text-sm">Loading thread…</span>
      </div>
    );
  }

  if (threadError || !thread) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <AlertCircle size={24} className="text-red-400 mx-auto mb-2" />
          <p className="text-sm text-red-600">{threadError ?? 'Thread not found'}</p>
        </div>
      </div>
    );
  }

  const sortedEmails = [...thread.emails].sort(
    (a, b) => new Date(a.sent_at ?? 0).getTime() - new Date(b.sent_at ?? 0).getTime(),
  );

  const handleConfirm = async (company: PortfolioCompany) => {
    setDialogOpen(false);
    setIsTagging(true);
    setFeedback({ type: 'info', text: 'Tagging…' });
    try {
      // Threads with no real thread_id are stored under the sentinel "no_thread".
      // In that case send the individual message_id so the backend can tag it directly.
      const hasRealThreadId = thread.thread_id && thread.thread_id !== 'no_thread';
      await tagThread({
        portfolio_company_id: company.id,
        ...(hasRealThreadId
          ? { thread_id: thread.thread_id }
          : { message_id: thread.emails[0]?.id }),
      });
      setFeedback({ type: 'success', text: `Successfully tagged with ${company.name}` });
      toast.success(`Thread tagged with ${company.name}`);
      setTimeout(() => {
        onTagged();
        setIsTagging(false);
        setTimeout(() => setFeedback(null), 3000);
      }, 500);
    } catch (err: any) {
      const detail = err?.response?.data?.detail?.message ?? `Failed to tag with ${company.name}. Please try again.`;
      setFeedback({ type: 'error', text: detail });
      toast.error(detail);
      setIsTagging(false);
      setTimeout(() => setFeedback(null), 5000);
    }
  };

  return (
    <div className="space-y-6">
      {/* Thread header */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="flex items-start justify-between mb-4">
          <div className="flex-1 min-w-0">
            <h2 className="text-xl font-semibold text-gray-900 mb-2 truncate">
              {thread.subject ?? '(no subject)'}
            </h2>
            <div className="flex items-center space-x-4 text-sm text-gray-500">
              <div className="flex items-center space-x-1">
                <Mail size={16} />
                <span>{thread.email_count} messages</span>
              </div>
            </div>
          </div>
        </div>

        <div className="pt-4 border-t border-gray-200 space-y-3">
          <button
            onClick={() => setDialogOpen(true)}
            disabled={isTagging}
            className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <Plus size={16} className="mr-2" />
            Tag Company
          </button>

          {feedback && (
            <div
              className={`flex items-center space-x-2 px-3 py-2 rounded-md text-sm font-medium ${
                feedback.type === 'success'
                  ? 'bg-green-50 text-green-800 border border-green-200'
                  : feedback.type === 'error'
                  ? 'bg-red-50 text-red-800 border border-red-200'
                  : 'bg-blue-50 text-blue-800 border border-blue-200'
              }`}
            >
              <div
                className={`w-2 h-2 rounded-full shrink-0 ${
                  feedback.type === 'success'
                    ? 'bg-green-500'
                    : feedback.type === 'error'
                    ? 'bg-red-500'
                    : 'bg-blue-500 animate-pulse'
                }`}
              />
              <span>{feedback.text}</span>
            </div>
          )}
        </div>
      </div>

      {/* Messages */}
      <div className="space-y-4">
        {sortedEmails.map((msg, idx) => (
          <EmailMessageCard
            key={msg.id}
            message={msg}
            defaultExpanded={idx === sortedEmails.length - 1}
          />
        ))}
      </div>

      {/* Tag dialog */}
      <TagDialog
        open={dialogOpen}
        threadSubject={thread.subject ?? ''}
        onClose={() => setDialogOpen(false)}
        onConfirm={(company) => void handleConfirm(company)}
      />
    </div>
  );
}

// Not-found fallback
export function ThreadNotFound() {
  return (
    <div className="flex items-center justify-center h-96">
      <div className="text-center">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-gray-100 mb-4">
          <AlertCircle size={24} className="text-gray-400" />
        </div>
        <h3 className="text-lg font-medium text-gray-900 mb-2">Thread not found</h3>
        <p className="text-gray-500 text-sm">
          The email thread you're looking for doesn't exist or may have been removed.
        </p>
      </div>
    </div>
  );
}

export default memo(UntaggedEmailThreadView);
