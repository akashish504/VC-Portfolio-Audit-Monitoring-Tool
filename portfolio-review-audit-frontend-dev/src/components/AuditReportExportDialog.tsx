/**
 * AuditReportExportDialog
 *
 * Modal for selecting a review cycle (or all cycles) and tracking the
 * async XLSX export job through pending → running → done / failed.
 */
import React, { useEffect, useRef, useState } from 'react';
import { X, Download, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';

import type { ReviewCycle } from '@/types/reviewCycle';
import {
  downloadAuditReportXlsx,
  pollAuditReportExport,
  startAuditReportExport,
  SUPPORTED_OUTPUT_CURRENCIES,
  type ExportJobResponse,
  type ExportJobStatus,
  type OutputCurrency,
  type ReportType,
} from '@/api/auditReportExport';

const ALL_CYCLES_VALUE = '__all__';
const POLL_INTERVAL_MS = 2500;

interface Props {
  onClose: () => void;
  reviewCycles: ReviewCycle[];
  /** Pre-select a cycle (e.g. the one currently viewed in the dashboard). */
  initialCycleId?: string;
}

export function AuditReportExportDialog({ onClose, reviewCycles, initialCycleId }: Props) {
  const [selectedCycleId, setSelectedCycleId] = useState<string>(
    initialCycleId ?? ALL_CYCLES_VALUE,
  );
  const [reportType, setReportType] = useState<ReportType>('reconciliation');
  const [selectedCurrency, setSelectedCurrency] = useState<OutputCurrency | ''>('');

  const needsCurrency = reportType === 'extracted_financials';

  const handleReportTypeChange = (type: ReportType) => {
    setReportType(type);
    if (type === 'reconciliation') setSelectedCurrency('');
  };
  const [job, setJob] = useState<ExportJobResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Stop polling when component unmounts
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const startPolling = (jobId: string) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const updated = await pollAuditReportExport(jobId);
        setJob(updated);
        if (updated.status === 'done' || updated.status === 'failed') {
          stopPolling();
        }
      } catch {
        // transient error — keep polling
      }
    }, POLL_INTERVAL_MS);
  };

  const handleStart = async () => {
    if (needsCurrency && !selectedCurrency) {
      toast.error('Please select an output currency');
      return;
    }
    setSubmitting(true);
    try {
      const cycleId = selectedCycleId === ALL_CYCLES_VALUE ? null : selectedCycleId;
      const currency = selectedCurrency ? (selectedCurrency as OutputCurrency) : undefined;
      const jobResp = await startAuditReportExport(cycleId, reportType, currency);
      setJob(jobResp);
      startPolling(jobResp.job_id);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to start export';
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDownload = async () => {
    if (!job || job.status !== 'done') return;
    const filename = job.filename ?? `audit_report_${job.job_id}.xlsx`;
    try {
      await downloadAuditReportXlsx(job.job_id, filename);
    } catch {
      toast.error('Download failed — please try again');
    }
  };

  const cycleName = (id: string) => {
    if (id === ALL_CYCLES_VALUE) return 'All review cycles';
    return reviewCycles.find((c) => c.id === id)?.label ?? id;
  };

  const statusLabel: Record<ExportJobStatus, string> = {
    pending: 'Queued…',
    running: 'Generating report…',
    done: 'Ready to download',
    failed: 'Export failed',
  };

  const isIdle = job === null;
  const isBusy = submitting || job?.status === 'pending' || job?.status === 'running';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="relative w-full max-w-md rounded-xl bg-white shadow-2xl p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-base font-semibold text-gray-900 flex items-center gap-2">
            <Download className="h-4 w-4 text-blue-600" />
            Download Audit Report
          </h2>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Report-type + cycle selector — only shown before job starts */}
        {isIdle && (
          <div className="mb-5">
            <fieldset className="mb-4">
              <legend className="block text-sm font-medium text-gray-700 mb-1.5">
                Report type
              </legend>
              <div className="flex flex-col gap-2">
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="report_type"
                    value="reconciliation"
                    checked={reportType === 'reconciliation'}
                    onChange={() => handleReportTypeChange('reconciliation')}
                    className="mt-0.5"
                  />
                  <span className="text-sm text-gray-700">
                    <span className="font-medium">Reconciliation report</span>
                    <span className="block text-xs text-gray-500">
                      Original report: 94 columns including PBT, PAT, EBITDA variance vs MIS.
                    </span>
                  </span>
                </label>
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="report_type"
                    value="extracted_financials"
                    checked={reportType === 'extracted_financials'}
                    onChange={() => handleReportTypeChange('extracted_financials')}
                    className="mt-0.5"
                  />
                  <span className="text-sm text-gray-700">
                    <span className="font-medium">Extracted financials</span>
                    <span className="block text-xs text-gray-500">
                      Canonical P&amp;L, Balance Sheet and Cash-Flow leaves extracted from audited
                      financial statements. Requires a target currency.
                    </span>
                  </span>
                </label>
              </div>
            </fieldset>

            <label className="block text-sm font-medium text-gray-700 mb-1.5">
              Review cycle
            </label>
            <select
              value={selectedCycleId}
              onChange={(e) => setSelectedCycleId(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            >
              <option value={ALL_CYCLES_VALUE}>All review cycles</option>
              {reviewCycles.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
            <p className="mt-1.5 text-xs text-gray-500">
              "All review cycles" creates one worksheet per cycle in the XLSX.
            </p>

            {needsCurrency && (
              <>
                <label className="block text-sm font-medium text-gray-700 mt-4 mb-1.5">
                  Output currency
                </label>
                <select
                  data-testid="currency-select"
                  value={selectedCurrency}
                  onChange={(e) => setSelectedCurrency(e.target.value as OutputCurrency | '')}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                >
                  <option value="" disabled>
                    -- Select currency --
                  </option>
                  {SUPPORTED_OUTPUT_CURRENCIES.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
                <p className="mt-1.5 text-xs text-gray-500">
                  Numeric financial values will be FX-converted to the selected currency using the
                  historical rate at each entity's financial year-end date.
                </p>
              </>
            )}
          </div>
        )}

        {/* Job status */}
        {job !== null && (
          <div className="mb-5">
            <StatusBadge status={job.status} />
            <p className="mt-2 text-sm text-gray-600">
              <span className="font-medium">Report:</span>{' '}
              {reportType === 'reconciliation' ? 'Reconciliation' : 'Extracted financials'}
              {' '}&middot;{' '}
              <span className="font-medium">Scope:</span>{' '}
              {cycleName(selectedCycleId)}
              {selectedCurrency && (
                <>
                  {' '}&middot;{' '}
                  <span className="font-medium">Currency:</span> {selectedCurrency}
                </>
              )}
            </p>
            {job.status === 'done' && job.filename && (
              <p className="mt-1 text-xs text-gray-500 break-all">{job.filename}</p>
            )}
            {job.status === 'failed' && job.error_message && (
              <p className="mt-1 text-xs text-red-600 break-all">{job.error_message}</p>
            )}
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-colors"
          >
            {job?.status === 'done' ? 'Close' : 'Cancel'}
          </button>

          {isIdle && (
            <button
              type="button"
              onClick={handleStart}
              disabled={submitting || (needsCurrency && !selectedCurrency)}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-1.5"
              title={needsCurrency && !selectedCurrency ? 'Select an output currency to continue' : undefined}
            >
              {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Generate report
            </button>
          )}

          {isBusy && !isIdle && (
            <button
              type="button"
              disabled
              className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white opacity-60 flex items-center gap-1.5 cursor-not-allowed"
            >
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {statusLabel[job!.status]}
            </button>
          )}

          {job?.status === 'done' && (
            <button
              type="button"
              onClick={handleDownload}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 transition-colors flex items-center gap-1.5"
            >
              <Download className="h-3.5 w-3.5" />
              Download XLSX
            </button>
          )}

          {job?.status === 'failed' && (
            <button
              type="button"
              onClick={() => {
                setJob(null);
                stopPolling();
              }}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors"
            >
              Retry
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: ExportJobStatus }) {
  const config: Record<ExportJobStatus, { icon: React.ReactNode; label: string; cls: string }> = {
    pending: {
      icon: <Loader2 className="h-4 w-4 animate-spin" />,
      label: 'Queued',
      cls: 'bg-gray-100 text-gray-700',
    },
    running: {
      icon: <Loader2 className="h-4 w-4 animate-spin" />,
      label: 'Generating…',
      cls: 'bg-blue-50 text-blue-700',
    },
    done: {
      icon: <CheckCircle2 className="h-4 w-4" />,
      label: 'Ready',
      cls: 'bg-green-50 text-green-700',
    },
    failed: {
      icon: <AlertCircle className="h-4 w-4" />,
      label: 'Failed',
      cls: 'bg-red-50 text-red-700',
    },
  };
  const { icon, label, cls } = config[status];
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${cls}`}>
      {icon}
      {label}
    </span>
  );
}
