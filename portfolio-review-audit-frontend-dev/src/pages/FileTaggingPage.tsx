import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, CheckCircle2, Clock, FileText, RotateCcw, Tag, Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';

import { useAppState } from '@/context/AppContext';
import { ColFilter } from '@/components/common/ColumnFilter';
import { type TaggedFile } from '@/data/mockData';
import { deleteFile, listEntities, listFiles, patchFile, searchPortfolioCompanies } from '@/api/portfolio';
import {
  ALLOWED_AUDIT_UPLOAD_EXTENSIONS,
  MAX_AUDIT_UPLOAD_BYTES,
  MAX_BULK_AUDIT_UPLOAD_FILES,
  bulkUploadAuditFiles,
  startFileExtraction,
} from '@/api/fileProcessing';
import { EntityUploadAttach } from '@/components/files/EntityUploadAttach';
import { DealSearchSelect } from '@/components/files/DealSearchSelect';
import { FyEndMonthYearPicker } from '@/components/files/FyEndMonthYearPicker';
import { DuplicateFileWarning } from '@/components/files/DuplicateFileWarning';
import { formatFileSizeFromBytes } from '@/utils/formatFileSize';
import { parseFyEndParts, monthsForReviewCycleId } from '@/utils/fyEnd';
import type { ReviewCycle } from '@/types/reviewCycle';

const statusConfig: Record<string, { icon: React.ElementType; badge: string }> = {
  processed: { icon: CheckCircle2, badge: 'bg-green-100 text-green-800' },
  pending: { icon: Clock, badge: 'bg-yellow-100 text-yellow-800' },
  error: { icon: AlertTriangle, badge: 'bg-red-100 text-red-800' },
};


type TaggedFileRow = TaggedFile & {
  portfolioCompanyId: number | null;
  companyName?: string | null;
  reviewCycleId?: string | null;
  entityDetachedAcknowledged: boolean;
  /** When extraction last completed (FileOCRMetadata.updated_at); null if never processed. */
  processedAt?: string | null;
  /** The linked entity's financial year end (Mmm-YY); null when no entity is attached. */
  entityFyEnd?: string | null;
};

type UploadQueueItem = {
  file: File;
  status: 'queued' | 'uploading' | 'success' | 'failed' | 'extracting';
  error?: string;
  fileId?: number;
};

function fileExtension(name: string): string {
  const i = name.lastIndexOf('.');
  return i >= 0 ? name.slice(i).toLowerCase() : '';
}

function validateSelectedFiles(files: File[], existing: File[]): string | null {
  if (files.length === 0) return 'Please select at least one file';
  const combined = [...existing, ...files];
  if (combined.length > MAX_BULK_AUDIT_UPLOAD_FILES) {
    return `At most ${MAX_BULK_AUDIT_UPLOAD_FILES} files per upload`;
  }
  const names = new Map<string, string>();
  for (const f of combined) {
    const key = f.name.trim().toLowerCase();
    if (names.has(key)) {
      return `Duplicate filename: ${names.get(key)}`;
    }
    names.set(key, f.name);
    const ext = fileExtension(f.name);
    if (!ALLOWED_AUDIT_UPLOAD_EXTENSIONS.includes(ext as (typeof ALLOWED_AUDIT_UPLOAD_EXTENSIONS)[number])) {
      return `${f.name}: only PDF, XLSX, and DOCX are allowed`;
    }
    if (f.size > MAX_AUDIT_UPLOAD_BYTES) {
      return `${f.name}: exceeds 50MB limit`;
    }
  }
  return null;
}

export default function FileTaggingPage() {
  // Start empty: remove seeded/static files from this view.
  const [files, setFiles] = useState<TaggedFileRow[]>([]);
  const [uploadDateSort, setUploadDateSort] = useState<'asc' | 'desc' | null>('desc');
  const navigate = useNavigate();
  const { rcCycles, rcEntries, ensureReviewCycleDataLoaded } = useAppState();

  useEffect(() => {
    void ensureReviewCycleDataLoaded();
  }, [ensureReviewCycleDataLoaded]);

  const [showUploadDialog, setShowUploadDialog] = useState(false);
  const [uploadCompanyId, setUploadCompanyId] = useState<number | null>(null);
  const [uploadFyEnd, setUploadFyEnd] = useState('');
  const [uploadEntityId, setUploadEntityId] = useState<number | null>(null);
  const [uploadFiles, setUploadFiles] = useState<UploadQueueItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadBatchComplete, setUploadBatchComplete] = useState(false);
  const [availableEntities, setAvailableEntities] = useState<Array<{ id: number; name: string }>>([]);

  // Tag dialog state
  const [taggingFileId, setTaggingFileId] = useState<string | null>(null);
  const [tagFyEnd, setTagFyEnd] = useState('');
  const [tagCompanyId, setTagCompanyId] = useState<number | null>(null);
  const [tagEntityId, setTagEntityId] = useState<number | null>(null);
  const [tagEntities, setTagEntities] = useState<Array<{ id: number; name: string }>>([]);
  const [tagSubmitting, setTagSubmitting] = useState(false);
  const [, setFilesLoading] = useState(false);
  // Per-column header filters (client-side), matching the Review Cycle Dashboard style.
  const [filterFileName, setFilterFileName] = useState<string>('');
  const [filterCycleIds, setFilterCycleIds] = useState<Set<string>>(new Set());

  // Derive review cycle for a given fy_end string against the loaded cycles.
  // Primary: startsAt/endsAt date range match (production, where cycles have timestamps).
  // Fallback: name-based match using the CYxx-FYyy convention (local dev / missing timestamps).
  const resolveCycleIdForFyEnd = (fyEnd: string, cycles: ReviewCycle[]): string => {
    if (!fyEnd || !cycles.length) return '';
    const parts = parseFyEndParts(fyEnd);
    if (!parts) return '';
    const selectedUtcMs = Date.UTC(parts.year, parts.month - 1, 1);
    const byDate = cycles.find((c) => {
      if (!c.startsAt || !c.endsAt) return false;
      return selectedUtcMs >= new Date(c.startsAt).getTime() && selectedUtcMs <= new Date(c.endsAt).getTime();
    });
    if (byDate) return byDate.id;
    // Fallback: derive expected CYxx-FYyy from month/year and match by id or label.
    const fy = parts.month >= 6 ? parts.year + 1 : parts.year;
    const cy = fy - 1;
    const cyStr = String(cy).slice(-2).padStart(2, '0');
    const fyStr = String(fy).slice(-2).padStart(2, '0');
    const expectedId = `CY${cyStr}-FY${fyStr}`;
    const byId = cycles.find((c) => c.id === expectedId);
    if (byId) return byId.id;
    // Also match against label (e.g. "CY 25 - FY 26") or by checking months list
    const byLabel = cycles.find((c) => monthsForReviewCycleId(c.id).includes(fyEnd) || monthsForReviewCycleId(c.id).includes(fyEnd.replace('-', '-')));
    return byLabel?.id ?? '';
  };

  const derivedCycleId = useMemo((): string => {
    return resolveCycleIdForFyEnd(uploadFyEnd, rcCycles);
  }, [uploadFyEnd, rcCycles]);

  const derivedCycleLabel = useMemo(() => {
    if (!derivedCycleId) return '';
    return rcCycles.find((c) => c.id === derivedCycleId)?.label ?? derivedCycleId;
  }, [derivedCycleId, rcCycles]);



  const handleResolveUnattached = async (fileId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm('Mark this file as intentionally unattached? The warning will be dismissed permanently.')) return;
    try {
      await patchFile(Number(fileId), { entity_detached_acknowledged: true });
      setFiles((prev) => prev.map((f) => f.id === fileId ? { ...f, entityDetachedAcknowledged: true } : f));
      toast.success('Marked as resolved');
    } catch {
      toast.error('Failed to update file');
    }
  };

  const handleRetrigger = async (fileId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm('Retrigger extraction for this file?')) return;
    setFiles((prev) => prev.map((f) => (f.id === fileId ? { ...f, status: 'pending' as const } : f)));
    try {
      await startFileExtraction(Number(fileId), { kind: 'audit_financials' });
      toast.success('Extraction triggered');
      await refreshFiles();
    } catch (err) {
      console.error(err);
      toast.error('Failed to trigger extraction');
      await refreshFiles();
    }
  };

  const handleDelete = async (fileId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm('Delete this file? This action cannot be undone.')) return;
    try {
      await deleteFile(Number(fileId));
      setFiles((prev) => prev.filter((f) => f.id !== fileId));
      toast.success('File deleted');
    } catch (err) {
      console.error(err);
      toast.error('Failed to delete file');
    }
  };

  const openUploadDialog = () => {
    setUploadCompanyId(null);
    setUploadFyEnd('');
    setUploadEntityId(null);
    setUploadFiles([]);
    setUploadBatchComplete(false);
    setAvailableEntities([]);
    setShowUploadDialog(true);
  };

  const handleFilesSelected = (incoming: FileList | File[] | null) => {
    if (!incoming || incoming.length === 0) return;
    const picked = Array.from(incoming);
    const err = validateSelectedFiles(picked, uploadFiles.map((r) => r.file));
    if (err) {
      toast.error(err);
      return;
    }
    setUploadFiles((prev) => [...prev, ...picked.map((file) => ({ file, status: 'queued' as const }))]);
  };

  const removeUploadFile = (index: number) => {
    if (uploading) return;
    setUploadFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleUploadConfirm = async () => {
    if (uploadFiles.length === 0) {
      toast.error('Please select at least one file');
      return;
    }
    if (!uploadFyEnd) {
      toast.error('Please select month and year');
      return;
    }
    if (!derivedCycleId) {
      toast.error('No review cycle found for the selected month and year');
      return;
    }
    if (uploadCompanyId == null) {
      toast.error('Please select a deal');
      return;
    }
    if (uploadEntityId == null) {
      toast.error('Select an entity for this deal');
      return;
    }

    const err = validateSelectedFiles(
      uploadFiles.map((r) => r.file),
      [],
    );
    if (err) {
      toast.error(err);
      return;
    }

    setUploading(true);
    setUploadBatchComplete(false);
    setUploadFiles((prev) => prev.map((row) => ({ ...row, status: 'uploading', error: undefined })));

    try {
      const response = await bulkUploadAuditFiles({
        ...(uploadCompanyId != null
          ? { portfolio_company_id: uploadCompanyId, entity_id: uploadEntityId ?? undefined }
          : {}),
        fy_end: uploadFyEnd,
        review_cycle_id: derivedCycleId || undefined,
        kind: 'audit_report',
        files: uploadFiles.map((r) => r.file),
      });

      const resultByName = new Map(
        (response.results ?? []).map((r) => [r.filename.trim().toLowerCase(), r] as const),
      );

      if (response.succeeded <= 0) {
        setUploadFiles((prev) =>
          prev.map((row) => {
            const hit = resultByName.get(row.file.name.trim().toLowerCase());
            if (!hit) {
              return { ...row, status: 'failed', error: 'No result returned from server' };
            }
            return { ...row, status: 'failed', error: hit.error || 'Upload failed' };
          }),
        );
        setUploadBatchComplete(true);
        toast.error(`${response.failed} file(s) failed to upload`);
      } else {
        if (response.failed > 0) {
          toast.error(`${response.succeeded} of ${response.total} file(s) uploaded; ${response.failed} failed`);
        } else {
          toast.success(`${response.succeeded} file(s) uploaded — extraction continues in background`);
        }
        window.dispatchEvent(new CustomEvent('files:updated'));
        void refreshFiles();
        setShowUploadDialog(false);
      }
    } catch (e) {
      const msg =
        typeof e === 'object' && e && 'response' in e
          ? String((e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ?? 'Upload failed')
          : e instanceof Error
            ? e.message
            : 'Upload failed';
      toast.error(msg);
      setUploadFiles((prev) => prev.map((row) => ({ ...row, status: 'failed', error: msg })));
      setUploadBatchComplete(true);
      console.error(e);
    } finally {
      setUploading(false);
    }
  };

  const refreshFiles = async () => {
    setFilesLoading(true);
    try {
      const res = await listFiles({ limit: 500, offset: 0 });
      const mapped: TaggedFileRow[] = (res.items ?? []).map((f) => {
        const entry = f.portfolio_company_id != null ? rcEntries.find((e) => e.portfolioCompanyId === f.portfolio_company_id) : undefined;
        const companyName = entry?.companyName ?? f.portfolio_company_name ?? null;
        const entName =
          f.entity_name?.trim() ||
          (f.entity_id != null ? `Entity #${f.entity_id}` : null);
        const ext = (f.filename.split('.').pop() || 'file').toLowerCase();
        const status = (f.status || '').toLowerCase();
        const uiStatus: TaggedFile['status'] =
          status.includes('fail') || status.includes('error')
            ? 'error'
            : status.includes('processed') || status.includes('done') || status.includes('complete')
              ? 'processed'
              : 'pending'; // 'uploaded' / 'pending' / 'verified' treated as in-progress
        return {
          id: String(f.id),
          fileName: f.filename,
          fileType: ext,
          uploadedAt: f.created_at ?? new Date().toISOString(),
          size: formatFileSizeFromBytes(f.size_bytes),
          taggedEntityId: f.entity_id != null ? String(f.entity_id) : null,
          taggedEntityName: entName,
          status: uiStatus,
          portfolioCompanyId: f.portfolio_company_id ?? null,
          companyName,
          reviewCycleId: entry?.reviewCycleId ?? f.review_cycle_id ?? f.portfolio_company_review_cycle_id ?? null,
          entityDetachedAcknowledged: f.entity_detached_acknowledged ?? false,
          processedAt: f.processed_at ?? null,
          entityFyEnd: f.entity_id != null ? (f.entity_fy_end ?? null) : null,
        };
      });
      setFiles(mapped);
    } catch (e) {
      console.warn('Failed to load files for tagging', e);
    } finally {
      setFilesLoading(false);
    }
  };

  const openTagDialog = (fileId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setTaggingFileId(fileId);
    setTagFyEnd('');
    setTagCompanyId(null);
    setTagEntityId(null);
    setTagEntities([]);
    setTagSubmitting(false);
  };

  const tagDerivedCycleId = useMemo((): string => {
    return resolveCycleIdForFyEnd(tagFyEnd, rcCycles);
  }, [tagFyEnd, rcCycles]);

  const tagDerivedCycleLabel = useMemo(() => {
    if (!tagDerivedCycleId) return '';
    return rcCycles.find((c) => c.id === tagDerivedCycleId)?.label ?? tagDerivedCycleId;
  }, [tagDerivedCycleId, rcCycles]);

  useEffect(() => {
    setTagCompanyId(null);
    setTagEntityId(null);
  }, [tagDerivedCycleId]);

  useEffect(() => {
    if (!tagCompanyId) { setTagEntities([]); return; }
    void listEntities({ portfolio_company_id: tagCompanyId, limit: 500, offset: 0 })
      .then((res) => setTagEntities((res.items ?? []).map((e) => ({ id: e.id, name: e.name }))))
      .catch(() => setTagEntities([]));
  }, [tagCompanyId]);

  const handleTagConfirm = async () => {
    if (!taggingFileId) return;
    if (!tagFyEnd) { toast.error('Select month and year'); return; }
    if (!tagDerivedCycleId) { toast.error('No review cycle found for this month/year'); return; }
    if (tagCompanyId == null) { toast.error('Select a deal'); return; }
    if (tagEntityId == null) { toast.error('Select an entity'); return; }
    setTagSubmitting(true);
    const entityName = tagEntities.find((e) => e.id === tagEntityId)?.name;
    try {
      await patchFile(Number(taggingFileId), {
        portfolio_company_id: tagCompanyId,
        entity_id: tagEntityId,
        fy_end: tagFyEnd,
        entity_detached_acknowledged: true,
      });
      window.dispatchEvent(new CustomEvent('files:updated'));
      setTaggingFileId(null);
      toast.success(entityName ? `File tagged to entity “${entityName}”` : 'File tagged');
      await refreshFiles();
    } catch (err) {
      console.error(err);
      toast.error('Failed to save tag');
    } finally {
      setTagSubmitting(false);
    }
  };

  useEffect(() => {
    setUploadCompanyId(null);
    setUploadEntityId(null);
  }, [derivedCycleId]);

  useEffect(() => {
    const run = async () => {
      if (!uploadCompanyId) {
        setAvailableEntities([]);
        return;
      }
      try {
        const res = await listEntities({ portfolio_company_id: uploadCompanyId, limit: 500, offset: 0 });
        setAvailableEntities((res.items ?? []).map((e) => ({ id: e.id, name: e.name, geolocation: e.geolocation ?? null })));
      } catch (e) {
        console.warn('Failed to load entities', e);
        setAvailableEntities([]);
      }
    };
    run();
  }, [uploadCompanyId]);

  useEffect(() => {
    void refreshFiles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rcEntries.length]);

  // Poll every 5 s while any visible file is still in the "pending" (in-flight) state,
  // but only when no dialog is open — avoid redundant /files calls during upload.
  const hasPendingFiles = files.some((f) => f.status === 'pending');
  const dialogOpen = showUploadDialog || taggingFileId != null;
  useEffect(() => {
    if (!hasPendingFiles || dialogOpen) return;
    const t = setInterval(() => {
      void refreshFiles().catch(() => {});
    }, 5_000);
    return () => clearInterval(t);
    // refreshFiles is stable within the component lifetime; rcEntries dep is intentionally omitted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPendingFiles, dialogOpen]);

  // Review-cycle ids that actually appear in the loaded files (for the column filter options).
  const cycleOptions = useMemo(() => {
    const ids = new Set<string>();
    for (const f of files) if (f.reviewCycleId) ids.add(f.reviewCycleId);
    return [...ids];
  }, [files]);

  const visibleFiles = useMemo(() => {
    const q = filterFileName.trim().toLowerCase();
    const filtered = files.filter((f) => {
      if (q && !f.fileName.toLowerCase().includes(q)) return false;
      if (filterCycleIds.size > 0 && !(f.reviewCycleId && filterCycleIds.has(f.reviewCycleId))) return false;
      return true;
    });
    const isUnattached = (f: TaggedFileRow) =>
      (f.portfolioCompanyId == null || f.taggedEntityId == null) && !f.entityDetachedAcknowledged;
    return [...filtered].sort((a, b) => {
      // Unattached files always come first, then ordered by upload date within each group.
      const au = isUnattached(a) ? 0 : 1;
      const bu = isUnattached(b) ? 0 : 1;
      if (au !== bu) return au - bu;
      if (uploadDateSort) {
        const ta = new Date(a.uploadedAt).getTime();
        const tb = new Date(b.uploadedAt).getTime();
        return uploadDateSort === 'asc' ? ta - tb : tb - ta;
      }
      return 0;
    });
  }, [files, filterFileName, filterCycleIds, uploadDateSort]);


  return (
    <div className="h-full overflow-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">File Tagging</h1>
          <p className="text-xs text-gray-500 mt-1">
            Select FY end to upload or tag files. The review cycle is derived from FY end. Use the filter icons in the column headers to filter the list.
          </p>
        </div>
        <button
          onClick={openUploadDialog}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg font-medium text-sm bg-blue-500 text-white hover:bg-blue-600 transition-all focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
        >
          <Upload className="h-3.5 w-3.5" /> Upload Files
        </button>
      </div>

      <div className="overflow-x-auto bg-white rounded-lg border border-gray-200 shadow-sm">
        <table className="w-full">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">
                <div className="flex items-center justify-between gap-1.5">
                  <span>File Name</span>
                  <ColFilter kind="search" value={filterFileName} onChange={setFilterFileName} placeholder="Search file name…" />
                </div>
              </th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">
                <button
                  onClick={() => setUploadDateSort((s) => s === 'desc' ? 'asc' : 'desc')}
                  className="inline-flex items-center gap-1 hover:text-gray-800 transition-colors"
                >
                  Uploaded
                  {uploadDateSort === 'asc' ? (
                    <ArrowUp className="h-3 w-3" />
                  ) : uploadDateSort === 'desc' ? (
                    <ArrowDown className="h-3 w-3" />
                  ) : (
                    <ArrowUpDown className="h-3 w-3 opacity-40" />
                  )}
                </button>
              </th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">Size</th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">Status</th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">Company</th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">Entity</th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">FY End</th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">
                <div className="flex items-center justify-between gap-1.5">
                  <span>Review Cycle</span>
                  <ColFilter
                    kind="multi"
                    options={cycleOptions}
                    selected={filterCycleIds}
                    onChange={setFilterCycleIds}
                    display={(id) => rcCycles.find((c) => c.id === id)?.label ?? id}
                  />
                </div>
              </th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-center px-4 py-3 w-20 border-r border-gray-200">Tag</th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-center px-4 py-3 w-20 border-r border-gray-200">Retrigger</th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-center px-4 py-3 w-20">Delete</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {visibleFiles.map((file) => {
              const config = statusConfig[file.status] || statusConfig.pending;
              const StatusIcon = config.icon;
              const unattached = (file.portfolioCompanyId == null || file.taggedEntityId == null) && !file.entityDetachedAcknowledged;
              return (
                <tr
                  key={file.id}
                  className={[
                    'transition-all cursor-pointer',
                    unattached ? 'bg-amber-50 hover:bg-amber-100 border-l-4 border-l-amber-400' : 'hover:bg-gray-50',
                  ].join(' ')}
                  onClick={() => navigate(`/file-tagging/${file.id}`, { state: { file } })}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <FileText className="h-4 w-4 text-red-400 shrink-0" />
                      <span className="text-sm font-medium text-blue-600 hover:text-blue-800">{file.fileName}</span>
                      {unattached && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-700">
                          <AlertTriangle className="h-2.5 w-2.5" /> Unattached
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-500">{new Date(file.uploadedAt).toLocaleDateString()}</td>
                  <td className="px-4 py-3 text-sm text-gray-500 font-mono">{file.size}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium ${config.badge}`}>
                      <StatusIcon className="h-3 w-3" />
                      <span className="capitalize">{file.status}</span>
                    </span>
                    {file.processedAt && (
                      <div className="mt-1 text-[11px] text-gray-400 whitespace-nowrap" title="Last processed">
                        {new Date(file.processedAt).toLocaleString()}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm">
                    {file.companyName
                      ? <span className="font-medium text-gray-900">{file.companyName}</span>
                      : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-3 text-sm">
                    {file.taggedEntityName
                      ? <span className="text-gray-700">{file.taggedEntityName}</span>
                      : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-3 text-sm">
                    {file.taggedEntityId && file.entityFyEnd
                      ? <span className="text-gray-700">{file.entityFyEnd}</span>
                      : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-500">
                    {file.reviewCycleId
                      ? (rcCycles.find((c) => c.id === file.reviewCycleId)?.label ?? file.reviewCycleId)
                      : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-3 text-center" onClick={(e) => e.stopPropagation()}>
                    <div className="flex flex-col items-center gap-1">
                      <button
                        onClick={(e) => openTagDialog(file.id, e)}
                        className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-blue-600 transition-all"
                        title={file.taggedEntityId ? 'Retag' : 'Tag'}
                      >
                        <Tag className="h-3 w-3" /> {file.taggedEntityId ? 'Retag' : 'Tag'}
                      </button>
                      {unattached && (
                        <button
                          onClick={(e) => void handleResolveUnattached(file.id, e)}
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium border border-amber-300 text-amber-700 hover:bg-amber-50 transition-all"
                        >
                          <CheckCircle2 className="h-3 w-3" /> Resolve
                        </button>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <button
                      onClick={(e) => handleRetrigger(file.id, e)}
                      className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-blue-600 transition-all mx-auto"
                      title="Retrigger OCR extraction"
                    >
                      <RotateCcw className="h-3 w-3" />
                    </button>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <button
                      onClick={(e) => handleDelete(file.id, e)}
                      className="inline-flex items-center gap-1 text-xs text-gray-500 hover:text-red-600 transition-all mx-auto"
                      title="Delete file"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {showUploadDialog && (
        <div
          className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
          onClick={() => {
            if (!uploading) setShowUploadDialog(false);
          }}
        >
          <div
            className="bg-white rounded-lg p-6 w-full max-w-lg shadow-xl max-h-[90vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold text-gray-900 mb-4">
              Upload Files
              {uploadFiles.length > 0 && (
                <span className="text-gray-400 font-normal ml-2">({uploadFiles.length}/{MAX_BULK_AUDIT_UPLOAD_FILES})</span>
              )}
            </h3>

            <div className="space-y-4">
              {/* File picker */}
              <div>
                <div className="border-2 border-dashed border-gray-300 rounded-lg p-4 text-center hover:border-blue-400 transition-colors">
                  <label className={`cursor-pointer block ${uploading || uploadBatchComplete ? 'pointer-events-none opacity-60' : ''}`}>
                    <Upload className="h-8 w-8 text-gray-300 mx-auto mb-2" />
                    <p className="text-sm text-gray-500">Click to browse (multi-select)</p>
                    <p className="text-xs text-gray-400 mt-1">
                      PDF, XLSX, DOCX up to 50MB · max {MAX_BULK_AUDIT_UPLOAD_FILES} files · no duplicate names
                    </p>
                    <input
                      type="file"
                      className="hidden"
                      multiple
                      accept=".pdf,.xlsx,.docx"
                      disabled={uploading || uploadBatchComplete}
                      onChange={(e) => { handleFilesSelected(e.target.files); e.target.value = ''; }}
                    />
                  </label>
                </div>
                {uploadFiles.length > 0 && (
                  <ul className="mt-3 space-y-1 max-h-40 overflow-y-auto border border-gray-100 rounded-lg divide-y divide-gray-100">
                    {uploadFiles.map((row, index) => {
                      const statusLabel = row.status === 'queued' ? 'Queued' : row.status === 'uploading' ? 'Uploading…' : row.status === 'extracting' ? 'Uploaded · extracting' : row.status === 'success' ? 'Uploaded' : 'Failed';
                      const statusClass = row.status === 'failed' ? 'text-red-600' : row.status === 'extracting' || row.status === 'success' ? 'text-green-700' : row.status === 'uploading' ? 'text-blue-600' : 'text-gray-500';
                      return (
                        <li key={`${row.file.name}-${index}`} className="flex items-start gap-2 px-3 py-2 text-sm">
                          <FileText className="h-4 w-4 text-red-400 shrink-0 mt-0.5" />
                          <div className="min-w-0 flex-1">
                            <div className="font-medium text-gray-800 truncate">{row.file.name}</div>
                            <div className={`text-xs ${statusClass}`}>{statusLabel}</div>
                            {row.error && <div className="text-xs text-red-600 mt-0.5">{row.error}</div>}
                          </div>
                          {!uploading && !uploadBatchComplete && (
                            <button type="button" onClick={() => removeUploadFile(index)} className="text-xs text-gray-400 hover:text-red-500 shrink-0">Remove</button>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>

              {/* Step 2: Month + Year → auto-derives review cycle */}
              <div>
                <FyEndMonthYearPicker
                  value={uploadFyEnd}
                  onChange={setUploadFyEnd}
                  disabled={uploading || uploadBatchComplete}
                  label="Month and Year"
                  id="upload-fy-end"
                />
                {uploadFyEnd && (
                  <p className={`mt-1.5 text-xs ${derivedCycleId ? 'text-green-700' : 'text-amber-600'}`}>
                    {derivedCycleId
                      ? `Review cycle: ${derivedCycleLabel}`
                      : 'No review cycle found for this month/year'}
                  </p>
                )}
              </div>

              {/* Step 3: Deal (company) — filtered to derived cycle */}
              <DealSearchSelect
                value={uploadCompanyId}
                onChange={(option) => {
                  setUploadCompanyId(option?.id ?? null);
                  setUploadEntityId(null);
                }}
                onSearch={(q) => searchPortfolioCompanies(q, derivedCycleId || undefined)}
                disabled={uploading || uploadBatchComplete}
                ready={Boolean(derivedCycleId)}
              />

              {/* Step 4: Entity — shown once a deal is selected */}
              {uploadCompanyId != null && (
                <EntityUploadAttach
                  portfolioCompanyId={uploadCompanyId}
                  entities={availableEntities}
                  entityId={uploadEntityId}
                  onEntityIdChange={setUploadEntityId}
                  onEntitiesChange={setAvailableEntities}
                  disabled={uploading || uploadBatchComplete}
                  required
                />
              )}

              <DuplicateFileWarning
                entityId={uploadEntityId}
                reviewCycleId={derivedCycleId || null}
              />

              {/* Actions */}
              <div className="flex justify-end gap-2 pt-1">
                {uploadBatchComplete ? (
                  <button type="button" onClick={() => setShowUploadDialog(false)} className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-500 text-white hover:bg-blue-600">Close</button>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => setShowUploadDialog(false)}
                      disabled={uploading}
                      className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleUploadConfirm()}
                      disabled={
                        uploadFiles.length === 0 ||
                        uploading ||
                        !uploadFyEnd ||
                        !derivedCycleId ||
                        uploadCompanyId == null ||
                        uploadEntityId == null
                      }
                      className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                    >
                      {uploading ? 'Uploading…' : uploadFiles.length > 1 ? `Upload ${uploadFiles.length} files` : 'Upload'}
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {taggingFileId && (
        <div
          className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
          onClick={() => { if (!tagSubmitting) setTaggingFileId(null); }}
        >
          <div
            className="bg-white rounded-lg p-6 w-full max-w-lg shadow-xl max-h-[90vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold text-gray-900 mb-4">
              {files.find((f) => f.id === taggingFileId)?.taggedEntityId ? 'Retag' : 'Tag'} File
            </h3>

            <div className="space-y-4">
              {/* Step 1: FY End → derives review cycle */}
              <div>
                <FyEndMonthYearPicker
                  value={tagFyEnd}
                  onChange={setTagFyEnd}
                  disabled={tagSubmitting}
                  label="Month and Year"
                  id="tag-fy-end"
                />
                {tagFyEnd && (
                  <p className={`mt-1.5 text-xs ${tagDerivedCycleId ? 'text-green-700' : 'text-amber-600'}`}>
                    {tagDerivedCycleId
                      ? `Review cycle: ${tagDerivedCycleLabel}`
                      : 'No review cycle found for this month/year'}
                  </p>
                )}
              </div>

              {/* Step 2: Deal (company) — filtered to derived cycle */}
              <DealSearchSelect
                value={tagCompanyId}
                onChange={(option) => {
                  setTagCompanyId(option?.id ?? null);
                  setTagEntityId(null);
                }}
                onSearch={(q) => searchPortfolioCompanies(q, tagDerivedCycleId || undefined)}
                disabled={tagSubmitting}
                ready={Boolean(tagDerivedCycleId)}
              />

              {/* Step 3: Entity — shown once a deal is selected */}
              {tagCompanyId != null && (
                <EntityUploadAttach
                  portfolioCompanyId={tagCompanyId}
                  entities={tagEntities}
                  entityId={tagEntityId}
                  onEntityIdChange={setTagEntityId}
                  onEntitiesChange={setTagEntities}
                  disabled={tagSubmitting}
                  required
                />
              )}

              <DuplicateFileWarning
                entityId={tagEntityId}
                reviewCycleId={tagDerivedCycleId || null}
                excludeFileId={taggingFileId ? Number(taggingFileId) : null}
              />
            </div>

            <div className="flex justify-end gap-2 pt-5">
              <button
                type="button"
                onClick={() => setTaggingFileId(null)}
                disabled={tagSubmitting}
                className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleTagConfirm()}
                disabled={!tagFyEnd || !tagDerivedCycleId || tagCompanyId == null || tagEntityId == null || tagSubmitting}
                className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                {tagSubmitting ? 'Saving…' : 'Tag File'}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

