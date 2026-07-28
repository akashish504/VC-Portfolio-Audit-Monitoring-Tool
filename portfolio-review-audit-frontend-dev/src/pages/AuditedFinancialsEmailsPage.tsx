import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Bot,
  ExternalLink,
  FileText,
  Loader2,
  Mail,
  RefreshCw,
  Tag,
  User,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  listAuditedFinancialsEmails,
  tagEmailWithEntity,
  type AuditedFinancialsEmail,
  type AttachmentIngestResult,
} from '@/api/emailThreads';
import { listEntities, searchPortfolioCompanies } from '@/api/portfolio';
import { resolveReviewCycleFromFyEnd } from '@/api/settings';
import { DealSearchSelect } from '@/components/files/DealSearchSelect';
import { EntityUploadAttach } from '@/components/files/EntityUploadAttach';
import { FyEndMonthYearPicker } from '@/components/files/FyEndMonthYearPicker';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type SortKey = 'sent_at' | 'portfolio_company_name' | 'sender';
type SortDir = 'asc' | 'desc';

function SortIcon({ col, active, dir }: { col: string; active: string; dir: SortDir }) {
  if (active !== col) return <ArrowUpDown size={13} className="text-gray-400 inline ml-1" />;
  return dir === 'asc'
    ? <ArrowUp size={13} className="text-blue-600 inline ml-1" />
    : <ArrowDown size={13} className="text-blue-600 inline ml-1" />;
}

// sent_at is stored as IST (naive datetime, no tz suffix) — append +05:30 so the
// browser always interprets and displays it as IST regardless of the user's locale.
function parseIstDate(iso: string): Date {
  return new Date(iso.includes('+') || iso.endsWith('Z') ? iso : iso + '+05:30');
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return parseIstDate(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' });
}

function formatTime(iso: string | null): string {
  if (!iso) return '';
  return parseIstDate(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' });
}

const FILE_STATUS_STYLE: Record<string, string> = {
  processed: 'bg-green-100 text-green-800',
  pending: 'bg-yellow-100 text-yellow-800',
  error: 'bg-red-100 text-red-800',
  extracting: 'bg-blue-100 text-blue-800',
  uploaded: 'bg-gray-100 text-gray-700',
};

// ---------------------------------------------------------------------------
// Tag-with-Entity dialog
// ---------------------------------------------------------------------------

interface TagDialogProps {
  open: boolean;
  email: AuditedFinancialsEmail | null;
  onClose: () => void;
  onTagged: (emailId: string) => void;
  forceReprocess?: boolean;
}

function TagWithEntityDialog({ open, email, onClose, onTagged, forceReprocess = false }: TagDialogProps) {
  const [fyEnd, setFyEnd] = useState('');
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState('');
  const [cycleId, setCycleId] = useState('');
  const [cycleLabel, setCycleLabel] = useState('');

  const [companyId, setCompanyId] = useState<number | null>(null);

  const [entities, setEntities] = useState<Array<{ id: number; name: string; geolocation?: string | null }>>([]);
  const [entityId, setEntityId] = useState<number | null>(null);

  const [submitting, setSubmitting] = useState(false);

  const reset = useCallback(() => {
    setFyEnd('');
    setResolving(false);
    setResolveError('');
    setCycleId('');
    setCycleLabel('');
    setCompanyId(null);
    setEntities([]);
    setEntityId(null);
    setSubmitting(false);
  }, []);

  useEffect(() => {
    if (!open) return;
    if (!fyEnd) {
      setResolveError('');
      setCycleId('');
      setCycleLabel('');
      setCompanyId(null);
      setEntities([]);
      setEntityId(null);
      return;
    }

    let cancelled = false;
    setResolveError('');
    setCycleId('');
    setCycleLabel('');
    setCompanyId(null);
    setEntities([]);
    setEntityId(null);
    setResolving(true);

    void (async () => {
      try {
        const resolved = await resolveReviewCycleFromFyEnd(fyEnd);
        if (cancelled) return;
        setCycleId(resolved.review_cycle_id);
        setCycleLabel(resolved.review_cycle_name ?? resolved.review_cycle_id);
      } catch {
        if (!cancelled) {
          setResolveError('No review cycle found for this FY end. Try a different month/year.');
        }
      } finally {
        if (!cancelled) setResolving(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, fyEnd]);

  useEffect(() => {
    if (!companyId) {
      setEntities([]);
      setEntityId(null);
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        const res = await listEntities({
          portfolio_company_id: companyId,
          review_cycle: cycleId || undefined,
          limit: 200,
          offset: 0,
        });
        if (!cancelled) {
          setEntities(
            (res.items ?? []).map((e) => ({
              id: e.id,
              name: e.name,
              geolocation: e.geolocation ?? null,
            })),
          );
        }
      } catch {
        if (!cancelled) setEntities([]);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [companyId, cycleId]);

  const handleSubmit = async () => {
    if (!email || companyId == null) return;
    setSubmitting(true);
    try {
      const result = await tagEmailWithEntity({
        email_id: email.id,
        portfolio_company_id: companyId,
        entity_id: entityId ?? undefined,
        review_cycle_id: cycleId || undefined,
        force_reprocess: forceReprocess,
      });
      const failed = (result.attachment_results ?? []).filter((r: AttachmentIngestResult) => r.status === 'failed');
      const skipped = (result.attachment_results ?? []).filter((r: AttachmentIngestResult) => r.status === 'skipped');
      if (failed.length > 0) {
        toast.error(
          result.message ?? `Tagged. ${result.attachments_processed} processed, ${failed.length} failed.`,
          {
            description: failed.map((r: AttachmentIngestResult) => `${r.filename}: ${r.error ?? 'unknown error'}`).join('\n'),
            duration: 8000,
          },
        );
      } else if (skipped.length > 0) {
        toast.warning(result.message ?? `Tagged. ${skipped.length} attachment(s) already extracted.`);
      } else {
        toast.success(result.message ?? 'Tagged successfully');
      }
      onTagged(email.id);
      reset();
      onClose();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message
        ?? 'Failed to tag email. Please try again.';
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const showCycleSection = !resolving && !resolveError && cycleId;

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) { reset(); onClose(); } }}>
      <DialogContent className="max-w-md overflow-visible">
        <DialogHeader className="min-w-0">
          <DialogTitle>{forceReprocess ? 'Re-process Email Attachments' : 'Tag Email with Company & Entity'}</DialogTitle>
          <DialogDescription className="truncate text-xs">
            {email?.subject ?? '(no subject)'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <FyEndMonthYearPicker
            value={fyEnd}
            onChange={setFyEnd}
            disabled={submitting}
            label="Month and Year"
            required
            id="email-tag-fy-end"
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
            <DealSearchSelect
              label="Company"
              value={companyId}
              onChange={(option) => {
                setCompanyId(option?.id ?? null);
                setEntityId(null);
              }}
              onSearch={(q) => searchPortfolioCompanies(q, cycleId)}
              disabled={submitting}
              placeholder="Search companies…"
            />
          )}

          {companyId != null && (
            <EntityUploadAttach
              portfolioCompanyId={companyId}
              entities={entities}
              entityId={entityId}
              onEntityIdChange={setEntityId}
              onEntitiesChange={setEntities}
              disabled={submitting}
            />
          )}
        </div>

        <DialogFooter>
          <button
            type="button"
            onClick={() => { reset(); onClose(); }}
            disabled={submitting}
            className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void handleSubmit()}
            disabled={companyId == null || submitting}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            {submitting && <Loader2 size={14} className="animate-spin" />}
            {submitting ? (forceReprocess ? 'Re-processing…' : 'Tagging…') : (forceReprocess ? 'Re-process' : 'Tag & Extract')}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function AuditedFinancialsEmailsPage() {
  const navigate = useNavigate();
  const [emails, setEmails] = useState<AuditedFinancialsEmail[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('sent_at');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [classifiedByFilter, setClassifiedByFilter] = useState<'all' | 'system' | 'user'>('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Tag dialog
  const [tagEmail, setTagEmail] = useState<AuditedFinancialsEmail | null>(null);
  const [reprocessEmail, setReprocessEmail] = useState<AuditedFinancialsEmail | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listAuditedFinancialsEmails();
      setEmails(data);
    } catch {
      setError('Failed to fetch audited financials emails');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void fetchData(); }, [fetchData]);

  // After tagging, refresh just the one row by re-fetching the full list
  // (no per-email endpoint exists; list is small so full refresh is fine)
  const handleTagged = useCallback((_emailId: string) => {
    void fetchData();
  }, [fetchData]);

  const filtered = useMemo(() => {
    if (!emails) return [];
    let rows = emails;

    if (classifiedByFilter !== 'all') {
      rows = rows.filter((e) => e.classified_by === classifiedByFilter);
    }

    if (search.trim()) {
      const q = search.trim().toLowerCase();
      rows = rows.filter(
        (e) =>
          e.subject?.toLowerCase().includes(q) ||
          e.portfolio_company_name?.toLowerCase().includes(q) ||
          e.sender?.toLowerCase().includes(q),
      );
    }

    return [...rows].sort((a, b) => {
      let av: string | null = null;
      let bv: string | null = null;
      if (sortKey === 'sent_at') { av = a.sent_at; bv = b.sent_at; }
      else if (sortKey === 'portfolio_company_name') { av = a.portfolio_company_name; bv = b.portfolio_company_name; }
      else if (sortKey === 'sender') { av = a.sender; bv = b.sender; }
      if (av === bv) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    });
  }, [emails, search, sortKey, sortDir, classifiedByFilter]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(key); setSortDir('desc'); }
  };

  const totalFiles = useMemo(
    () => (emails ? emails.reduce((s, e) => s + e.files.length, 0) : 0),
    [emails],
  );

  return (
    <div className="h-full overflow-auto">
      {/* Header */}
      <div className="sticky top-0 z-10 bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold text-gray-900">Audited Financials Emails</h1>
            <p className="text-xs text-gray-500 mt-1">
              Incoming emails carrying audited financial documents. Tag untagged emails to a company
              &amp; entity to trigger file extraction.
            </p>
          </div>
          {emails && (
            <div className="flex items-center gap-4 shrink-0 text-sm text-gray-500">
              <span><span className="font-semibold text-gray-800">{emails.length}</span> emails</span>
              <span><span className="font-semibold text-gray-800">{totalFiles}</span> files</span>
            </div>
          )}
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3 mt-3">
          <input
            type="text"
            placeholder="Search by subject, company, or sender…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 max-w-xs text-sm border border-gray-300 rounded-md px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <div className="flex items-center gap-0 rounded-md border border-gray-200 overflow-hidden text-xs font-medium">
            {(['all', 'system', 'user'] as const).map((v) => (
              <button
                key={v}
                onClick={() => setClassifiedByFilter(v)}
                className={`px-3 py-1.5 transition-colors ${
                  classifiedByFilter === v
                    ? 'bg-blue-600 text-white'
                    : 'bg-white text-gray-600 hover:bg-gray-50'
                }`}
              >
                {v === 'all' ? 'All' : v === 'system' ? 'System' : 'User tagged'}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="p-6">
        {loading ? (
          <div className="text-center py-16 bg-white rounded-lg border border-gray-200">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-gray-100 mb-4">
              <Mail size={24} className="text-gray-400 animate-pulse" />
            </div>
            <h3 className="text-base font-medium text-gray-900 mb-1">Loading…</h3>
            <p className="text-sm text-gray-500">Fetching audited financials emails</p>
          </div>
        ) : error ? (
          <div className="text-center py-16 bg-white rounded-lg border border-red-200">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-red-100 mb-4">
              <Mail size={24} className="text-red-400" />
            </div>
            <h3 className="text-base font-medium text-red-900 mb-1">Error</h3>
            <p className="text-sm text-red-500">{error}</p>
            <button
              onClick={() => void fetchData()}
              className="mt-4 text-sm px-4 py-2 bg-red-600 text-white rounded-md hover:bg-red-700 transition-colors"
            >
              Retry
            </button>
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-lg border border-gray-200">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-gray-100 mb-4">
              <Mail size={24} className="text-gray-400" />
            </div>
            <h3 className="text-base font-medium text-gray-900 mb-1">No emails found</h3>
            <p className="text-sm text-gray-500">
              {search || classifiedByFilter !== 'all'
                ? 'No emails match the current filters.'
                : 'No audited financials emails have been classified yet.'}
            </p>
          </div>
        ) : (
          <div className="bg-white rounded-lg border border-gray-200 overflow-x-auto">
            <table className="w-full min-w-[900px] text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600 w-7" />
                  <th
                    className="text-left px-4 py-3 font-medium text-gray-600 cursor-pointer hover:text-gray-900 select-none whitespace-nowrap"
                    onClick={() => toggleSort('sent_at')}
                  >
                    Date <SortIcon col="sent_at" active={sortKey} dir={sortDir} />
                  </th>
                  <th
                    className="text-left px-4 py-3 font-medium text-gray-600 cursor-pointer hover:text-gray-900 select-none whitespace-nowrap"
                    onClick={() => toggleSort('portfolio_company_name')}
                  >
                    Company <SortIcon col="portfolio_company_name" active={sortKey} dir={sortDir} />
                  </th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Subject</th>
                  <th
                    className="text-left px-4 py-3 font-medium text-gray-600 cursor-pointer hover:text-gray-900 select-none whitespace-nowrap"
                    onClick={() => toggleSort('sender')}
                  >
                    Sender <SortIcon col="sender" active={sortKey} dir={sortDir} />
                  </th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600 whitespace-nowrap">Classified by</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Files</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filtered.map((email) => {
                  const expanded = expandedId === email.id;
                  const isUntagged = email.portfolio_company_id === null;
                  return [
                    <tr
                      key={email.id}
                      className={`hover:bg-gray-50 transition-colors cursor-pointer ${expanded ? 'bg-blue-50' : ''}`}
                      onClick={() => setExpandedId(expanded ? null : email.id)}
                    >
                      <td className="px-4 py-3 text-gray-400 w-7">
                        <span className={`inline-block transition-transform duration-150 ${expanded ? 'rotate-90' : ''}`}>▶</span>
                      </td>
                      <td className="px-4 py-3 text-gray-700 whitespace-nowrap">
                        <div className="font-medium">{formatDate(email.sent_at)}</div>
                        <div className="text-xs text-gray-400">{formatTime(email.sent_at)}</div>
                      </td>
                      <td className="px-4 py-3">
                        {email.portfolio_company_name ? (
                          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-50 text-indigo-700 max-w-[180px] truncate">
                            {email.portfolio_company_name}
                          </span>
                        ) : (
                          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-600">
                            Untagged
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-gray-700 max-w-[280px] truncate">
                        {email.subject ?? <span className="text-gray-400">—</span>}
                      </td>
                      <td className="px-4 py-3 text-gray-600 max-w-[160px] truncate text-xs">
                        {email.sender ?? '—'}
                      </td>
                      <td className="px-4 py-3">
                        {email.classified_by === 'system' ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-purple-50 text-purple-700">
                            <Bot size={11} /> System
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-700">
                            <User size={11} /> User
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {email.files.length === 0 ? (
                          <span className="text-xs text-gray-400">No files</span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-700">
                            <FileText size={12} className="text-gray-400" />
                            {email.files.length} file{email.files.length !== 1 ? 's' : ''}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                        {isUntagged && (
                          <button
                            onClick={() => setTagEmail(email)}
                            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 transition-colors whitespace-nowrap"
                          >
                            <Tag size={11} /> Tag &amp; Extract
                          </button>
                        )}
                      </td>
                    </tr>,
                    expanded && (
                      <tr key={`${email.id}-expanded`} className="bg-blue-50">
                        <td colSpan={8} className="px-6 pb-4 pt-0">
                          <div className="border-t border-blue-100 pt-3">
                            {/* Files section */}
                            {email.files.length === 0 ? (
                              <div className="flex items-center justify-between">
                                <p className="text-sm text-gray-500 italic py-2">
                                  {(email.attachments?.length ?? 0) > 0
                                    ? 'No files extracted from this email yet.'
                                    : 'No files extracted yet.'}
                                  {isUntagged && ' Tag this email to a company & entity to trigger extraction.'}
                                </p>
                                {isUntagged && (
                                  <button
                                    onClick={(e) => { e.stopPropagation(); setTagEmail(email); }}
                                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 transition-colors"
                                  >
                                    <Tag size={11} /> Tag &amp; Extract
                                  </button>
                                )}
                              </div>
                            ) : (
                              <div className="space-y-2">
                                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
                                  Extracted files
                                </p>
                                <div className="grid gap-2">
                                  {email.files.map((f) => {
                                    const fileStatus = f.status ?? '';
                                    const statusLabel =
                                      fileStatus === 'uploaded' ? 'Awaiting extraction' :
                                      fileStatus === 'processed' ? 'Extracted' :
                                      fileStatus === 'failed' ? 'Extraction failed' :
                                      fileStatus || null;
                                    const needsReprocess = fileStatus === 'uploaded' || fileStatus === 'failed';
                                    return (
                                      <div
                                        key={f.id}
                                        className="flex items-center justify-between bg-white rounded-md border border-blue-100 px-3 py-2 hover:border-blue-300 transition-colors"
                                      >
                                        <div className="flex items-center gap-2 min-w-0">
                                          <FileText size={14} className="text-blue-500 shrink-0" />
                                          <span className="text-sm text-gray-800 truncate max-w-xs">
                                            {f.filename ?? `File #${f.id}`}
                                          </span>
                                          {statusLabel && (
                                            <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${FILE_STATUS_STYLE[fileStatus] ?? 'bg-gray-100 text-gray-600'}`}>
                                              {statusLabel}
                                            </span>
                                          )}
                                        </div>
                                        <div className="flex items-center gap-3 shrink-0 ml-4">
                                          <span className="text-xs text-gray-400">{formatDate(f.created_at)}</span>
                                          {needsReprocess && !isUntagged && (
                                            <button
                                              onClick={(ev) => { ev.stopPropagation(); setReprocessEmail(email); }}
                                              className="inline-flex items-center gap-1 text-xs text-amber-600 hover:text-amber-800 font-medium"
                                              title="Re-trigger extraction for this email's attachments"
                                            >
                                              <RefreshCw size={11} /> Re-process
                                            </button>
                                          )}
                                          <button
                                            onClick={(ev) => { ev.stopPropagation(); navigate(`/file-tagging/${f.id}`); }}
                                            className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
                                          >
                                            Open file <ExternalLink size={11} />
                                          </button>
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              </div>
                            )}

                            {/* Attachments with matched/unmatched status */}
                            {(email.attachments?.length ?? 0) > 0 && (
                              <div className="mt-3">
                                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
                                  Email attachments ({email.attachments!.length})
                                </p>
                                <div className="flex flex-wrap gap-2">
                                  {email.attachments!.map((key) => {
                                    const matchedFile = email.files.find((f) => f.source_attachment_key === key);
                                    const basename = key.split('/').pop() ?? key;
                                    return (
                                      <span
                                        key={key}
                                        title={key}
                                        className={`inline-flex items-center gap-1 px-2 py-1 border rounded text-xs max-w-xs truncate ${
                                          matchedFile
                                            ? 'bg-green-50 border-green-200 text-green-700'
                                            : 'bg-white border-gray-200 text-gray-600'
                                        }`}
                                      >
                                        {matchedFile ? (
                                          <FileText size={10} className="shrink-0 text-green-500" />
                                        ) : (
                                          <AlertCircle size={10} className="shrink-0 text-amber-400" />
                                        )}
                                        {basename}
                                        {!matchedFile && (
                                          <span className="ml-1 text-amber-500 font-medium">not extracted</span>
                                        )}
                                      </span>
                                    );
                                  })}
                                </div>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    ),
                  ];
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Tag dialog */}
      <TagWithEntityDialog
        open={tagEmail !== null}
        email={tagEmail}
        onClose={() => setTagEmail(null)}
        onTagged={handleTagged}
      />

      {/* Re-process dialog */}
      <TagWithEntityDialog
        open={reprocessEmail !== null}
        email={reprocessEmail}
        onClose={() => setReprocessEmail(null)}
        onTagged={handleTagged}
        forceReprocess
      />
    </div>
  );
}
