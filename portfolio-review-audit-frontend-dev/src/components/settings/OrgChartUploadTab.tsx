import React, { useEffect, useRef, useState } from 'react';
import { CheckCircle2, ChevronDown, ChevronRight, Download, Loader2, Upload } from 'lucide-react';
import { toast } from 'sonner';

import {
  downloadMappingTemplate,
  downloadStatusReport,
  listOrgChartBatches,
  type OrgChartUploadBatch,
  type OrgChartUploadBatchDetail,
  getOrgChartBatch,
  uploadMappingXlsx,
  uploadOrgChartZip,
} from '@/api/orgChartBatches';
import { fetchReviewCycles } from '@/api/reviewCycleAdjustments';
import type { ReviewCycle } from '@/types/reviewCycle';
import { pickDefaultCycle } from '@/api/dashboard';

// ---------------------------------------------------------------------------
// Status badge helpers
// ---------------------------------------------------------------------------

const BATCH_STATUS_COLORS: Record<string, string> = {
  uploaded: 'bg-blue-100 text-blue-800',
  partially_mapped: 'bg-yellow-100 text-yellow-800',
  processing: 'bg-purple-100 text-purple-800',
  completed: 'bg-green-100 text-green-800',
  failed: 'bg-red-100 text-red-800',
};

const RECORD_STATUS_COLORS: Record<string, string> = {
  pending_mapping: 'bg-gray-100 text-gray-700',
  mapped: 'bg-blue-100 text-blue-800',
  processing: 'bg-purple-100 text-purple-800',
  completed: 'bg-green-100 text-green-800',
  failed: 'bg-red-100 text-red-800',
};

function StatusBadge({ status, palette }: { status: string; palette: Record<string, string> }) {
  const cls = palette[status] ?? 'bg-gray-100 text-gray-700';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {status.replace(/_/g, ' ')}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Cycle selector (shared)
// ---------------------------------------------------------------------------

function CycleSelect({
  cycles,
  value,
  onChange,
  label,
  required,
}: {
  cycles: ReviewCycle[];
  value: string;
  onChange: (v: string) => void;
  label?: string;
  required?: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      {label && <span className="text-sm font-medium text-gray-700 shrink-0">{label}</span>}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={`border rounded-md px-3 py-1.5 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 ${
          required && !value ? 'border-red-400 bg-red-50' : 'border-gray-300'
        }`}
      >
        {!required && <option value="">— select cycle —</option>}
        {cycles.map((c) => (
          <option key={c.id} value={c.id}>
            {c.label}
          </option>
        ))}
      </select>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Batch row (collapsible)
// ---------------------------------------------------------------------------

function BatchRow({
  batch: initialBatch,
  cycles,
}: {
  batch: OrgChartUploadBatch;
  cycles: ReviewCycle[];
}) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<OrgChartUploadBatchDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // mapping upload state
  const [mappingFile, setMappingFile] = useState<File | null>(null);
  const [mappingCycleId, setMappingCycleId] = useState(
    initialBatch.review_cycle_id ?? pickDefaultCycle(cycles) ?? '',
  );

  // Auto-select default cycle once cycles load if none is set
  useEffect(() => {
    if (!mappingCycleId && cycles.length > 0) {
      setMappingCycleId(pickDefaultCycle(cycles) ?? cycles[0].id);
    }
  }, [cycles, mappingCycleId]);
  const [uploadingMapping, setUploadingMapping] = useState(false);
  const [downloadingTemplate, setDownloadingTemplate] = useState(false);
  const [downloadingStatusReport, setDownloadingStatusReport] = useState(false);
  const [batch, setBatch] = useState(initialBatch);
  const mappingInputRef = useRef<HTMLInputElement>(null);

  const loadDetail = async () => {
    if (detail) return;
    setLoadingDetail(true);
    try {
      const d = await getOrgChartBatch(batch.id, { skipGlobalLoading: true });
      setDetail(d);
      setBatch(d);
    } catch {
      toast.error('Failed to load batch details');
    } finally {
      setLoadingDetail(false);
    }
  };

  const handleExpand = () => {
    const next = !expanded;
    setExpanded(next);
    if (next) void loadDetail();
  };

  const handleMappingUpload = async () => {
    if (!mappingFile || !mappingCycleId) return;
    setUploadingMapping(true);
    try {
      const result = await uploadMappingXlsx(batch.id, mappingFile, mappingCycleId);
      toast.success(
        `Mapping uploaded: ${result.rows_mapped} mapped, ${result.rows_skipped} skipped, ${result.extraction_queued} queued for extraction`,
      );
      setMappingFile(null);
      if (mappingInputRef.current) mappingInputRef.current.value = '';
      const d = await getOrgChartBatch(batch.id, { skipGlobalLoading: true });
      setDetail(d);
      setBatch(d);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Mapping upload failed';
      toast.error(msg);
    } finally {
      setUploadingMapping(false);
    }
  };

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <button
        type="button"
        className="w-full flex items-center gap-3 px-4 py-3 bg-white hover:bg-gray-50 text-left"
        onClick={handleExpand}
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-gray-400 shrink-0" />
        ) : (
          <ChevronRight className="h-4 w-4 text-gray-400 shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-gray-900 truncate">{batch.original_zip_filename}</span>
            <StatusBadge status={batch.status} palette={BATCH_STATUS_COLORS} />
          </div>
          <div className="text-xs text-gray-500 mt-0.5">
            {batch.file_count} file{batch.file_count !== 1 ? 's' : ''} · Cycle: {batch.review_cycle_id} ·{' '}
            {new Date(batch.created_at).toLocaleDateString()}
          </div>
        </div>
      </button>

      {expanded && (
        <div className="border-t border-gray-100 bg-gray-50 px-4 py-4 space-y-4">

          {/* Download template */}
          <div>
            <button
              type="button"
              disabled={downloadingTemplate}
              onClick={async () => {
                setDownloadingTemplate(true);
                try {
                  await downloadMappingTemplate(batch.id);
                } catch {
                  toast.error('Failed to download mapping template');
                } finally {
                  setDownloadingTemplate(false);
                }
              }}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-blue-700 bg-blue-50 border border-blue-200 rounded-md hover:bg-blue-100 disabled:opacity-50 disabled:pointer-events-none"
            >
              {downloadingTemplate ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              Download mapping template
            </button>
          </div>

          {/* Mapping XLSX upload */}
          <div className="rounded-md border border-gray-200 bg-white px-4 py-3 space-y-3">
            <p className="text-xs font-medium text-gray-700">Upload filled mapping XLSX</p>

            {batch.mapping_uploaded_at ? (
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                Mapping was already uploaded on{' '}
                {new Date(batch.mapping_uploaded_at).toLocaleString()}. Upload a new ZIP to create a new batch.
              </p>
            ) : (
              <div className="flex flex-wrap items-center gap-3">
                <label className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 cursor-pointer">
                  <Upload className="h-3.5 w-3.5" />
                  {mappingFile ? mappingFile.name : 'Choose filled XLSX…'}
                  <input
                    ref={mappingInputRef}
                    type="file"
                    accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    className="sr-only"
                    onChange={(e) => setMappingFile(e.target.files?.[0] ?? null)}
                  />
                </label>

                <CycleSelect
                  cycles={cycles}
                  value={mappingCycleId}
                  onChange={setMappingCycleId}
                  label="Review cycle"
                  required
                />

                <button
                  type="button"
                  disabled={!mappingFile || !mappingCycleId || uploadingMapping}
                  onClick={() => void handleMappingUpload()}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:pointer-events-none"
                >
                  {uploadingMapping ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <CheckCircle2 className="h-3.5 w-3.5" />
                  )}
                  Upload mapping
                </button>
              </div>
            )}
          </div>

          {/* Status report download */}
          {batch.status !== 'uploaded' && (
            <div>
              <button
                type="button"
                disabled={downloadingStatusReport}
                onClick={async () => {
                  setDownloadingStatusReport(true);
                  try {
                    await downloadStatusReport(batch.id);
                  } catch {
                    toast.error('Failed to download status report');
                  } finally {
                    setDownloadingStatusReport(false);
                  }
                }}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:pointer-events-none"
              >
                {downloadingStatusReport ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                Download status report
              </button>
            </div>
          )}

          {/* Records table */}
          {loadingDetail && (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading files…
            </div>
          )}
          {detail && detail.records.length > 0 && (
            <div className="overflow-x-auto">
              <div className="mb-1 text-xs text-gray-500">
                Showing {detail.records.length} of {batch.file_count} file{batch.file_count !== 1 ? 's' : ''} — download the mapping template to see all
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-white border-b border-gray-200">
                    <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider">File</th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider">Company ID</th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider">Name</th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                    <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider">Error</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {detail.records.map((r) => (
                    <tr key={r.id} className="bg-white hover:bg-gray-50">
                      <td className="px-3 py-2 font-mono text-xs text-gray-800 max-w-xs truncate">{r.file_name}</td>
                      <td className="px-3 py-2 text-xs text-gray-600">{r.company_id ?? '—'}</td>
                      <td className="px-3 py-2 text-xs text-gray-600">{r.name ?? '—'}</td>
                      <td className="px-3 py-2">
                        <StatusBadge status={r.extraction_status} palette={RECORD_STATUS_COLORS} />
                      </td>
                      <td className="px-3 py-2 text-xs text-red-600 max-w-xs truncate" title={r.error_message ?? ''}>
                        {r.error_message ?? ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {detail && detail.records.length === 0 && (
            <p className="text-sm text-gray-500">No files in this batch.</p>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main tab component
// ---------------------------------------------------------------------------

export default function OrgChartUploadTab() {
  const [cycles, setCycles] = useState<ReviewCycle[]>([]);
  const [cyclesLoading, setCyclesLoading] = useState(true);

  const [zipFile, setZipFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const zipInputRef = useRef<HTMLInputElement>(null);

  // Batch list — separate filter (optional)
  const [filterCycleId, setFilterCycleId] = useState<string>('');
  const [batches, setBatches] = useState<OrgChartUploadBatch[]>([]);
  const [batchesTotal, setBatchesTotal] = useState(0);
  const [batchesLoading, setBatchesLoading] = useState(false);
  const [batchesError, setBatchesError] = useState<string | null>(null);

  // Load review cycles once (used for batch list filter and mapping upload)
  useEffect(() => {
    void (async () => {
      try {
        const c = await fetchReviewCycles();
        setCycles(c);
      } catch {
        toast.error('Failed to load review cycles');
      } finally {
        setCyclesLoading(false);
      }
    })();
  }, []);

  const loadBatches = async (cycleId?: string, skipGlobalLoading?: boolean) => {
    setBatchesLoading(true);
    setBatchesError(null);
    try {
      const resp = await listOrgChartBatches({ review_cycle_id: cycleId || undefined, limit: 100, skipGlobalLoading });
      setBatches(resp.items);
      setBatchesTotal(resp.total);
    } catch {
      setBatchesError('Failed to load batches');
    } finally {
      setBatchesLoading(false);
    }
  };

  useEffect(() => {
    void loadBatches(filterCycleId || undefined);
  }, [filterCycleId]);

  const handleZipUpload = async () => {
    if (!zipFile) return;
    setUploading(true);
    try {
      const result = await uploadOrgChartZip(zipFile);
      setZipFile(null);
      if (zipInputRef.current) zipInputRef.current.value = '';

      toast.success(
        `ZIP uploaded — ${result.batch.file_count} file${result.batch.file_count !== 1 ? 's' : ''} are being processed in the background. This may take a few minutes — the batch list will update automatically when done.`,
        { duration: 8000 },
      );

      // Poll until background processing completes (status leaves 'processing').
      const batchId = result.batch.id;
      let attempts = 0;
      const MAX_ATTEMPTS = 120; // 2 minutes at 1s intervals
      const poll = async (): Promise<void> => {
        if (attempts >= MAX_ATTEMPTS) {
          toast.warning('Processing is taking longer than expected. Refresh the page to check progress.');
          await loadBatches(filterCycleId || undefined, true);
          return;
        }
        attempts += 1;
        await new Promise((r) => setTimeout(r, 1000));
        const batch = await getOrgChartBatch(batchId, { skipGlobalLoading: true });
        if (batch.status === 'processing') {
          return poll();
        }
        if (batch.status === 'failed') {
          toast.error(`Batch processing failed. ${batch.file_count} files were queued.`);
        } else {
          toast.success(`Batch ready: ${batch.file_count} file${batch.file_count !== 1 ? 's' : ''} processed.`);
        }
        await loadBatches(filterCycleId || undefined, true);
      };
      void poll();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'ZIP upload failed';
      toast.error(msg);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* ZIP upload panel */}
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
        <div className="px-4 py-3 border-b border-gray-200">
          <h2 className="text-sm font-semibold text-gray-900">Upload Org Chart ZIP</h2>
        </div>
        <div className="px-4 py-4 space-y-4">
          <p className="text-sm text-gray-600">
            Upload a ZIP file containing org chart files (.pdf, .docx, .xlsx).
            All files in a single ZIP are treated as one batch. You can assign a review cycle when uploading the mapping XLSX.
          </p>

          <div className="flex items-center gap-3 flex-wrap">
            <label className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 cursor-pointer">
              <Upload className="h-3.5 w-3.5" />
              {zipFile ? zipFile.name : 'Choose ZIP file…'}
              <input
                ref={zipInputRef}
                type="file"
                accept=".zip,application/zip,application/x-zip-compressed"
                className="sr-only"
                onChange={(e) => setZipFile(e.target.files?.[0] ?? null)}
              />
            </label>

            <button
              type="button"
              disabled={!zipFile || uploading}
              onClick={() => void handleZipUpload()}
              className="inline-flex items-center gap-1.5 px-4 py-1.5 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:pointer-events-none"
            >
              {uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
              Upload ZIP
            </button>
          </div>
        </div>
      </div>

      {/* Batch list */}
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm">
        <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between gap-4">
          <h2 className="text-sm font-semibold text-gray-900 shrink-0">
            Batches{batchesTotal > 0 ? ` (${batchesTotal})` : ''}
          </h2>
          <div className="flex items-center gap-3">
            {!cyclesLoading && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500 shrink-0">Filter by cycle</span>
                <select
                  value={filterCycleId}
                  onChange={(e) => setFilterCycleId(e.target.value)}
                  className="border border-gray-300 rounded-md px-2 py-1 text-xs text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">All cycles</option>
                  {cycles.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <button
              type="button"
              disabled={batchesLoading}
              onClick={() => void loadBatches(filterCycleId || undefined)}
              className="text-xs text-blue-600 hover:underline disabled:opacity-50 disabled:pointer-events-none"
            >
              Refresh
            </button>
          </div>
        </div>

        <div className="px-4 py-4">
          {batchesError && (
            <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
              {batchesError}
            </div>
          )}

          {batchesLoading && (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-4">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading batches…
            </div>
          )}

          {!batchesLoading && batches.length === 0 && (
            <p className="text-sm text-gray-500 py-2">
              No batches found{filterCycleId ? ` for cycle ${filterCycleId}` : ''}.
            </p>
          )}

          {!batchesLoading && batches.length > 0 && (
            <div className="space-y-2">
              {batches.map((b) => (
                <BatchRow key={b.id} batch={b} cycles={cycles} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
