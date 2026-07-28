import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, FileText, Upload, X } from 'lucide-react';
import { toast } from 'sonner';

import {
  ALLOWED_AUDIT_UPLOAD_EXTENSIONS,
  MAX_AUDIT_UPLOAD_BYTES,
  MAX_BULK_AUDIT_UPLOAD_FILES,
  bulkUploadAuditFiles,
} from '@/api/fileProcessing';
import { listEntities, listFiles, patchFile } from '@/api/portfolio';
import { EntityUploadAttach } from '@/components/files/EntityUploadAttach';
import { FyEndMonthYearPicker } from '@/components/files/FyEndMonthYearPicker';
import { DuplicateFileWarning } from '@/components/files/DuplicateFileWarning';
import { formatFileSizeFromBytes } from '@/utils/formatFileSize';
import { resolveCompanyFyEnd } from '@/utils/fyEnd';

type UiFileRow = {
  id: string;
  fileName: string;
  entityId: number | null;
  entityLabel: string;
  uploadedAt: string;
  status: 'pending' | 'processed' | 'error';
  isOrphan: boolean;
};

function formatEntityLabel(
  entityId: number | null | undefined,
  entityName: string | null | undefined,
  entityGeolocation: string | null | undefined,
): string {
  if (entityId == null) return '—';
  const name = (entityName ?? '').trim();
  const geo = (entityGeolocation ?? '').trim();
  if (name && geo) return `${name} (${geo})`;
  if (name) return name;
  return `Entity #${entityId}`;
}

type UploadQueueItem = {
  file: File;
  status: 'queued' | 'uploading' | 'failed' | 'extracting';
  error?: string;
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
    if (names.has(key)) return `Duplicate filename: ${names.get(key)}`;
    names.set(key, f.name);
    const ext = fileExtension(f.name);
    if (!ALLOWED_AUDIT_UPLOAD_EXTENSIONS.includes(ext as (typeof ALLOWED_AUDIT_UPLOAD_EXTENSIONS)[number])) {
      return `${f.name}: only PDF, XLSX, DOCX, PPTX, and PPT are allowed`;
    }
    if (f.size > MAX_AUDIT_UPLOAD_BYTES) {
      return `${f.name}: exceeds 50MB limit`;
    }
  }
  return null;
}

export function CompanyFiles({
  companyId,
  companyName,
  reviewCycleId,
  initialFyEnd,
  initialFyEndDate,
  onOrphanResolved,
}: {
  companyId: number;
  companyName: string;
  reviewCycleId?: string | null;
  initialFyEnd?: string | null;
  initialFyEndDate?: string | null;
  onOrphanResolved?: () => void;
}) {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<UiFileRow[]>([]);
  const [showUploadDialog, setShowUploadDialog] = useState(false);
  const [uploadFyEnd, setUploadFyEnd] = useState('');
  const [uploadEntityId, setUploadEntityId] = useState<number | null>(null);
  const [entities, setEntities] = useState<Array<{ id: number; name: string }>>([]);
  const [uploadFiles, setUploadFiles] = useState<UploadQueueItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadBatchComplete, setUploadBatchComplete] = useState(false);

  // Inline attach state — tracks which file row has the attach picker open
  const [attachingFileId, setAttachingFileId] = useState<string | null>(null);
  const [attachEntityId, setAttachEntityId] = useState<number | null>(null);
  const [attachFyEnd, setAttachFyEnd] = useState('');
  const [attachEntities, setAttachEntities] = useState<Array<{ id: number; name: string }>>([]);
  const [attachEntitiesLoading, setAttachEntitiesLoading] = useState(false);

  const defaultFyEnd = useMemo(
    () => resolveCompanyFyEnd(initialFyEnd, initialFyEndDate) ?? '',
    [initialFyEnd, initialFyEndDate],
  );

  const orphanCount = useMemo(() => rows.filter((r) => r.isOrphan).length, [rows]);

  const refreshFiles = async () => {
    setLoading(true);
    try {
      const res = await listFiles({ portfolio_company_id: companyId, limit: 500, offset: 0 });
      const mapped: UiFileRow[] = (res.items ?? []).map((f) => {
        const st = String(f.status || '').toLowerCase();
        const status: UiFileRow['status'] =
          st.includes('fail') || st.includes('error')
            ? 'error'
            : st.includes('processed') || st.includes('done') || st.includes('complete')
              ? 'processed'
              : 'pending';
        const isOrphan = f.portfolio_company_id != null && f.entity_id == null && !(f.entity_detached_acknowledged ?? false);
        return {
          id: String(f.id),
          fileName: f.filename,
          entityId: f.entity_id ?? null,
          entityLabel: formatEntityLabel(f.entity_id, f.entity_name, f.entity_geolocation),
          uploadedAt: f.updated_at || f.created_at || '',
          status,
          isOrphan,
        };
      });
      setRows(mapped);
      // Notify parent when all orphans are resolved so the dot clears.
      const remainingOrphans = mapped.filter((r) => r.isOrphan).length;
      if (remainingOrphans === 0) onOrphanResolved?.();
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const onUpdated = () => { void refreshFiles(); };
    window.addEventListener('files:updated', onUpdated as EventListener);
    void refreshFiles();
    return () => window.removeEventListener('files:updated', onUpdated as EventListener);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId]);

  useEffect(() => {
    if (!showUploadDialog) return;
    let cancelled = false;
    void listEntities({ portfolio_company_id: companyId, limit: 500, offset: 0 })
      .then((res) => {
        if (!cancelled) setEntities((res.items ?? []).map((e) => ({ id: e.id, name: e.name })));
      })
      .catch(() => { if (!cancelled) setEntities([]); });
    return () => { cancelled = true; };
  }, [showUploadDialog, companyId]);

  const openAttach = async (fileId: string) => {
    setAttachingFileId(fileId);
    setAttachEntityId(null);
    setAttachFyEnd(defaultFyEnd);
    setAttachEntitiesLoading(true);
    try {
      const res = await listEntities({ portfolio_company_id: companyId, limit: 500, offset: 0 });
      setAttachEntities((res.items ?? []).map((e) => ({ id: e.id, name: e.name })));
    } catch {
      setAttachEntities([]);
    } finally {
      setAttachEntitiesLoading(false);
    }
  };

  const handleAttachConfirm = async (fileId: string) => {
    if (attachEntityId == null) {
      toast.error('Select an entity first');
      return;
    }
    if (!attachFyEnd) {
      toast.error('Select FY end');
      return;
    }
    if (!window.confirm('Attach this file to the selected entity?')) return;
    try {
      await patchFile(Number(fileId), {
        entity_id: attachEntityId,
        fy_end: attachFyEnd,
        entity_detached_acknowledged: true,
      });
      toast.success('File attached');
      setAttachingFileId(null);
      void refreshFiles();
    } catch {
      toast.error('Failed to attach file');
    }
  };

  const handleDismissOrphan = async (fileId: string) => {
    if (!window.confirm('Mark this file as intentionally unattached? The warning will be dismissed permanently.')) return;
    try {
      await patchFile(Number(fileId), { entity_detached_acknowledged: true });
      toast.success('Marked as intentionally unattached');
      setAttachingFileId(null);
      void refreshFiles();
    } catch {
      toast.error('Failed to update file');
    }
  };

  const openUpload = () => {
    setUploadFyEnd(defaultFyEnd);
    setUploadEntityId(null);
    setUploadFiles([]);
    setUploadBatchComplete(false);
    setShowUploadDialog(true);
  };

  const handleFilesSelected = (incoming: FileList | File[] | null) => {
    if (!incoming || incoming.length === 0) return;
    const picked = Array.from(incoming);
    const err = validateSelectedFiles(picked, uploadFiles.map((r) => r.file));
    if (err) { toast.error(err); return; }
    setUploadFiles((prev) => [...prev, ...picked.map((file) => ({ file, status: 'queued' as const }))]);
  };

  const handleUploadConfirm = async () => {
    if (!uploadFyEnd) { toast.error('Select FY end'); return; }
    if (uploadEntityId == null) { toast.error('Select or create an entity'); return; }
    if (uploadFiles.length === 0) { toast.error('Select at least one file'); return; }
    setUploading(true);
    setUploadBatchComplete(false);
    setUploadFiles((prev) => prev.map((row) => ({ ...row, status: 'uploading', error: undefined })));
    try {
      const response = await bulkUploadAuditFiles({
        portfolio_company_id: companyId,
        entity_id: uploadEntityId,
        fy_end: uploadFyEnd,
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
            if (!hit) return { ...row, status: 'failed', error: 'No result returned' };
            return { ...row, status: 'failed', error: hit.error || 'Upload failed' };
          }),
        );
        setUploadBatchComplete(true);
        toast.error(`${response.failed} file(s) failed to upload`);
      } else {
        if (response.failed > 0) {
          toast.error(`${response.succeeded} of ${response.total} uploaded; ${response.failed} failed`);
        } else {
          toast.success(`${response.succeeded} file(s) uploaded — extraction continues in background`);
        }
        window.dispatchEvent(new CustomEvent('files:updated'));
        void refreshFiles();
        setShowUploadDialog(false);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Upload failed';
      toast.error(msg);
      setUploadFiles((prev) => prev.map((row) => ({ ...row, status: 'failed', error: msg })));
      setUploadBatchComplete(true);
    } finally {
      setUploading(false);
    }
  };

  const countLabel = useMemo(() => `${rows.length} file(s)`, [rows.length]);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">Files</h2>
          <p className="text-xs text-gray-500 mt-0.5">{companyName}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500">{countLabel}</span>
          <button
            type="button"
            onClick={openUpload}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-blue-500 text-white hover:bg-blue-600"
          >
            <Upload className="h-3.5 w-3.5" /> Upload
          </button>
        </div>
      </div>

      {!loading && orphanCount > 0 && (
        <div className="flex items-start gap-2 px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5 text-amber-500" />
          <span>
            <span className="font-medium">{orphanCount} file{orphanCount > 1 ? 's' : ''}</span>
            {' '}lost their entity when the org chart was reuploaded. Attach them to an entity or dismiss the warning.
          </span>
        </div>
      )}

      {loading ? (
        <div className="text-sm text-gray-500">Loading files…</div>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center bg-white border border-gray-200 rounded-lg">
          <FileText className="h-10 w-10 text-gray-300 mb-2" />
          <p className="text-sm text-gray-500">No files for this company yet</p>
        </div>
      ) : (
        <div className="overflow-x-auto bg-white rounded-lg border border-gray-200 shadow-sm">
          <table className="w-full">
            <thead>
              <tr className="bg-gray-50">
                <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3">File</th>
                <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3">Entity</th>
                <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3">Uploaded</th>
                <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3">Status</th>
                <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 w-40">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((r) => (
                <>
                  <tr
                    key={r.id}
                    className={[
                      'transition-all cursor-pointer',
                      r.isOrphan
                        ? 'bg-amber-50 hover:bg-amber-100 border-l-4 border-l-amber-400'
                        : 'hover:bg-gray-50',
                    ].join(' ')}
                    onClick={() => {
                      if (attachingFileId === r.id) return;
                      navigate(`/file-tagging/${r.id}`, {
                        state: { from: { kind: 'company', companyId }, tab: 'files' },
                      });
                    }}
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <FileText className="h-4 w-4 text-red-400 shrink-0" />
                        <span className="text-sm font-medium text-blue-600 hover:text-blue-800">{r.fileName}</span>
                        {r.isOrphan && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-700">
                            <AlertTriangle className="h-2.5 w-2.5" /> Unattached
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-600">
                      <span className={r.entityLabel === '—' ? 'text-gray-400 italic' : ''}>{r.entityLabel}</span>
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-500">{new Date(r.uploadedAt).toLocaleString()}</td>
                    <td className="px-4 py-3 text-sm text-gray-600 capitalize">{r.status}</td>
                    <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                      {attachingFileId !== r.id && (
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => void openAttach(r.id)}
                            className="px-2.5 py-1 rounded-md text-xs font-medium bg-blue-500 text-white hover:bg-blue-600"
                          >
                            {r.entityId != null ? 'Re-attach' : 'Attach'}
                          </button>
                          {r.isOrphan && (
                            <button
                              type="button"
                              onClick={() => void handleDismissOrphan(r.id)}
                              className="px-2.5 py-1 rounded-md text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50"
                            >
                              Dismiss
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                  {attachingFileId === r.id && (
                    <tr key={`${r.id}-attach`} className={r.isOrphan ? 'bg-amber-50 border-l-4 border-l-amber-400' : 'bg-blue-50 border-l-4 border-l-blue-300'}>
                      <td colSpan={5} className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center gap-3 flex-wrap">
                          <span className="text-xs font-medium text-gray-700 shrink-0">Attach to entity:</span>
                          {attachEntitiesLoading ? (
                            <span className="text-xs text-gray-400">Loading entities…</span>
                          ) : attachEntities.length === 0 ? (
                            <span className="text-xs text-red-500">No entities available — add entities via Org Chart first.</span>
                          ) : (
                            <select
                              value={attachEntityId ?? ''}
                              onChange={(e) => setAttachEntityId(e.target.value ? Number(e.target.value) : null)}
                              className="px-2 py-1 border border-gray-300 rounded text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            >
                              <option value="" disabled>Select entity…</option>
                              {attachEntities.map((e) => (
                                <option key={e.id} value={e.id}>{e.name}</option>
                              ))}
                            </select>
                          )}
                          <FyEndMonthYearPicker
                            value={attachFyEnd}
                            onChange={setAttachFyEnd}
                          />
                          <button
                            type="button"
                            disabled={attachEntityId == null || !attachFyEnd || attachEntitiesLoading}
                            onClick={() => void handleAttachConfirm(r.id)}
                            className="px-3 py-1 rounded text-xs font-medium bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50"
                          >
                            Confirm
                          </button>
                          {r.isOrphan && (
                            <button
                              type="button"
                              onClick={() => void handleDismissOrphan(r.id)}
                              className="px-3 py-1 rounded text-xs font-medium border border-gray-300 text-gray-600 hover:bg-gray-50"
                            >
                              Keep unattached
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => setAttachingFileId(null)}
                            className="ml-auto text-gray-400 hover:text-gray-600"
                          >
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </div>
                        <DuplicateFileWarning
                          entityId={attachEntityId}
                          reviewCycleId={reviewCycleId ?? null}
                          excludeFileId={Number(r.id)}
                          className="mt-3"
                        />
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showUploadDialog ? (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => !uploading && setShowUploadDialog(false)}>
          <div className="bg-white rounded-lg p-6 w-full max-w-lg shadow-xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-gray-900 mb-1">Upload audit files</h3>
            <p className="text-xs text-gray-500 mb-4">{companyName}</p>

            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ALLOWED_AUDIT_UPLOAD_EXTENSIONS.join(',')}
              className="hidden"
              onChange={(e) => {
                handleFilesSelected(e.target.files);
                e.target.value = '';
              }}
            />
            <button
              type="button"
              disabled={uploading || uploadBatchComplete}
              onClick={() => fileInputRef.current?.click()}
              className="w-full border border-dashed border-gray-300 rounded-lg py-4 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50"
            >
              Select files (PDF, XLSX, DOCX, PPTX — max {MAX_BULK_AUDIT_UPLOAD_FILES})
            </button>

            {uploadFiles.length > 0 ? (
              <ul className="mt-3 space-y-1 max-h-32 overflow-y-auto text-sm">
                {uploadFiles.map((row, i) => (
                  <li key={`${row.file.name}-${i}`} className="flex justify-between gap-2">
                    <span className="truncate">{row.file.name}</span>
                    <span className="text-xs text-gray-500 shrink-0">{formatFileSizeFromBytes(row.file.size)}</span>
                  </li>
                ))}
              </ul>
            ) : null}

            <div className="mt-5 space-y-4 border-t border-gray-100 pt-4">
              <FyEndMonthYearPicker
                value={uploadFyEnd}
                onChange={setUploadFyEnd}
                disabled={uploading || uploadBatchComplete}
                required
                id="company-files-fy-end"
              />
              <EntityUploadAttach
                portfolioCompanyId={companyId}
                entities={entities}
                entityId={uploadEntityId}
                onEntityIdChange={setUploadEntityId}
                onEntitiesChange={setEntities}
                disabled={uploading || uploadBatchComplete}
                required
              />
              <DuplicateFileWarning
                entityId={uploadEntityId}
                reviewCycleId={reviewCycleId ?? null}
              />
            </div>

            <div className="flex justify-end gap-2 mt-6">
              {uploadBatchComplete ? (
                <button type="button" onClick={() => setShowUploadDialog(false)} className="px-4 py-2 rounded-lg text-sm bg-blue-500 text-white">
                  Close
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    disabled={uploading}
                    onClick={() => setShowUploadDialog(false)}
                    className="px-4 py-2 rounded-lg text-sm border border-gray-300"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={uploading || !uploadFyEnd || uploadEntityId == null || uploadFiles.length === 0}
                    onClick={() => void handleUploadConfirm()}
                    className="px-4 py-2 rounded-lg text-sm bg-blue-500 text-white disabled:opacity-50"
                  >
                    {uploading ? 'Uploading…' : 'Upload'}
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
