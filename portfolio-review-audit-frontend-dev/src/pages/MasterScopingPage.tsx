import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Building2, ChevronLeft, ChevronRight, Download, Filter, Search, Trash2, Upload } from 'lucide-react';
import apiClient from '@/api/axios';
import { pickDefaultCycle } from '@/api/dashboard';
import ActionableDealsModal from '@/components/ActionableDealsModal';
import { AuditTrackerFilterCell } from '@/pages/AuditTrackerFilterCell';
import {
  applyColumnFilters,
  type ColumnFilterState,
  type DashboardColumnMeta,
  type FilterColumnKind,
} from '@/pages/reviewCycleColumnFilters';

interface ReviewCycle {
  id: string;
  name: string | null;
  status: string | null;
}

export interface PortfolioCompanyMetadataRow {
  id: number;
  fund: string;
  deal_id: string;
  deal_id_for_analysis: string | null;
  deal_id_for_analysis_and_strategy: string | null;
  deal_name: string;
  strategy: string;
  il_main: string | null;
  sector_l1: string | null;
  sector_l2: string | null;
  geo_l1: string | null;
  geo_l2: string | null;
  cost: string | null;
  distributed: string | null;
  proceeds: string | null;
  fmv: string | null;
  ownership: string | null;
  scoping_for_audit: boolean | null;
  reason_for_exclusion: string | null;
  category: string | null;
  unique_by_company_id: number | null;
  unique_by_company_id_strategy: number | null;
  consolidated_cost: string | null;
  consolidated_fmv: string | null;
  deal_level_stage_1: string | null;
  deal_level_stage_2: string | null;
  tentative_audit_completion_date: string | null;
  fy_end: string | null;
  auditor: string | null;
  category_of_auditor: string | null;
  py_audit_status: string | null;
  review_cycle_id: string | null;
  comments: string | null;
  created_at: string | null;
  updated_at: string | null;
}

const PAGE_SIZE_OPTIONS = [100, 200, 500] as const;
const DEFAULT_PAGE_SIZE = 500;

function getPageNumbers(current: number, total: number): (number | '…')[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages: (number | '…')[] = [1];
  const left = Math.max(2, current - 1);
  const right = Math.min(total - 1, current + 1);
  if (left > 2) pages.push('…');
  for (let p = left; p <= right; p++) pages.push(p);
  if (right < total - 1) pages.push('…');
  pages.push(total);
  return pages;
}

function cellStr(v: string | number | boolean | null | undefined): string {
  if (v == null || String(v).trim() === '') return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  return String(v);
}

const scrollCell =
  'px-2 py-2 text-xs text-gray-700 align-top min-w-[7rem] max-w-[18rem] break-words whitespace-normal';
const scrollCellNum = `${scrollCell} whitespace-nowrap font-mono text-right max-w-none`;
const stickyTh =
  'sticky left-0 z-20 min-w-[14rem] max-w-[18rem] border-r border-gray-200 bg-gray-50 shadow-[4px_0_12px_-6px_rgba(0,0,0,0.12)]';
const stickyTd =
  'sticky left-0 z-10 min-w-[14rem] max-w-[18rem] border-r border-gray-200 bg-white shadow-[4px_0_12px_-6px_rgba(0,0,0,0.1)] group-hover:bg-gray-50';

const COLUMNS: { id: keyof PortfolioCompanyMetadataRow; label: string; numeric?: boolean }[] = [
  { id: 'deal_name',                     label: 'Deal Name' },
  { id: 'fund',                          label: 'Fund' },
  { id: 'deal_id',                       label: 'Deal ID' },
  { id: 'deal_id_for_analysis',          label: 'Deal ID for Analysis' },
  { id: 'deal_id_for_analysis_and_strategy', label: 'Deal ID for Analysis & Strategy' },
  { id: 'strategy',                      label: 'Strategy' },
  { id: 'review_cycle_id',               label: 'Review Cycle ID' },
  { id: 'il_main',                       label: 'IL Main' },
  { id: 'sector_l1',                     label: 'Sector L1' },
  { id: 'sector_l2',                     label: 'Sector L2' },
  { id: 'geo_l1',                        label: 'Geo L1' },
  { id: 'geo_l2',                        label: 'Geo L2' },
  { id: 'cost',                          label: 'Cost',                        numeric: true },
  { id: 'distributed',                   label: 'Distributed',                 numeric: true },
  { id: 'proceeds',                      label: 'Proceeds',                    numeric: true },
  { id: 'fmv',                           label: 'FMV',                         numeric: true },
  { id: 'ownership',                     label: 'Ownership',                   numeric: true },
  { id: 'scoping_for_audit',             label: 'Scoping for Audit' },
  { id: 'reason_for_exclusion',          label: 'Reason for Exclusion' },
  { id: 'category',                      label: 'Category' },
  { id: 'unique_by_company_id',          label: 'Unique by Company' },
  { id: 'unique_by_company_id_strategy', label: 'Unique by Co. + Strategy' },
  { id: 'consolidated_cost',             label: 'Consol. Cost',                numeric: true },
  { id: 'consolidated_fmv',             label: 'Consol. FMV',                 numeric: true },
  { id: 'deal_level_stage_1',            label: 'Deal Stage 1' },
  { id: 'deal_level_stage_2',            label: 'Deal Stage 2' },
  { id: 'tentative_audit_completion_date', label: 'Tent. Audit Completion' },
  { id: 'fy_end',                        label: 'FY End' },
  { id: 'auditor',                       label: 'Auditor' },
  { id: 'category_of_auditor',           label: 'Auditor Category' },
  { id: 'py_audit_status',               label: 'PY Audit Status' },
  { id: 'comments',                      label: 'Comments' },
  { id: 'updated_at',                    label: 'Updated' },
  { id: 'created_at',                    label: 'Created' },
];

// --- Per-column filters (mirrors the Audit Tracker filter UI) ---------------
// Numeric columns get a min/max range filter; the two timestamps get a date
// range; everything else is a distinct-values multiselect.
const NUMERIC_FILTER_COLS = new Set<keyof PortfolioCompanyMetadataRow>([
  'cost', 'distributed', 'proceeds', 'fmv', 'ownership',
  'consolidated_cost', 'consolidated_fmv',
  'unique_by_company_id', 'unique_by_company_id_strategy',
]);
const DATE_FILTER_COLS = new Set<keyof PortfolioCompanyMetadataRow>(['updated_at', 'created_at']);

const FILTER_COLUMNS: DashboardColumnMeta<PortfolioCompanyMetadataRow>[] = COLUMNS.map((c) => {
  if (c.id === 'scoping_for_audit') {
    return {
      id: c.id,
      field: c.id,
      kind: 'string',
      enumOptions: ['Yes', 'No'],
      valueFrom: (row) => (row.scoping_for_audit == null ? '—' : row.scoping_for_audit ? 'Yes' : 'No'),
    };
  }
  if (c.id === 'review_cycle_id') {
    return {
      id: c.id,
      field: c.id,
      kind: 'string',
      valueFrom: (row, getCycleLabel) => {
        const id = row.review_cycle_id;
        if (id == null || String(id).trim() === '') return '—';
        return getCycleLabel(String(id));
      },
    };
  }
  const kind: FilterColumnKind = NUMERIC_FILTER_COLS.has(c.id)
    ? 'number'
    : DATE_FILTER_COLS.has(c.id)
    ? 'date'
    : 'string';
  return { id: c.id, field: c.id, kind };
});

const FILTER_COLUMN_BY_ID = new Map(FILTER_COLUMNS.map((c) => [c.id, c]));

// Allowed values mirror the backend DealLevelStage1 / DealLevelStage2 enums.
const DEAL_STAGE_1_OPTIONS = [
  'Completed within due date',
  'Completed post due date',
  'Overdue',
  'Expected to complete within timeline',
  'Expected delay',
] as const;
const DEAL_STAGE_2_OPTIONS = [
  'Operating subsidiary completed, Holding pending',
  'Standalone completed, Consolidated pending',
  'Closed',
  'Closed - Flagged',
] as const;

// Columns rendered as an always-visible dropdown in the table (rather than the
// click-to-edit text used by other editable columns). Options come from EDITABLE.
const DROPDOWN_COLUMNS = new Set<keyof PortfolioCompanyMetadataRow>([
  'scoping_for_audit',
  'deal_level_stage_1',
  'deal_level_stage_2',
]);

// Deal Level Stage 2 only becomes relevant once Stage 1 reports completion, so
// its dropdown stays disabled for every other Stage 1 value.
const STAGE_2_ENABLED_STAGE_1: ReadonlySet<string> = new Set([
  'Completed within due date',
  'Completed post due date',
]);

// Fields that can be set in bulk for a multi-row selection — exactly the
// always-on dropdown columns. Each maps to its picker options ('' = clear).
const BULK_FIELDS: {
  field: keyof PortfolioCompanyMetadataRow;
  label: string;
  options: readonly string[];
}[] = [
  { field: 'scoping_for_audit', label: 'Scoping for Audit', options: ['Yes', 'No'] },
  { field: 'deal_level_stage_1', label: 'Deal Stage 1', options: DEAL_STAGE_1_OPTIONS },
  { field: 'deal_level_stage_2', label: 'Deal Stage 2', options: DEAL_STAGE_2_OPTIONS },
];

type EditType = 'text' | 'number' | 'enum' | 'boolean';

// Fields editable inline. Keys (fund/deal_id/strategy), review_cycle_id (set at
// upload time) and the timestamps are intentionally read-only.
const EDITABLE: Partial<Record<keyof PortfolioCompanyMetadataRow, { type: EditType; options?: readonly string[] }>> = {
  deal_id_for_analysis:          { type: 'text' },
  deal_id_for_analysis_and_strategy: { type: 'text' },
  deal_name:                     { type: 'text' },
  il_main:                       { type: 'text' },
  sector_l1:                     { type: 'text' },
  sector_l2:                     { type: 'text' },
  geo_l1:                        { type: 'text' },
  geo_l2:                        { type: 'text' },
  cost:                          { type: 'number' },
  distributed:                   { type: 'number' },
  proceeds:                      { type: 'number' },
  fmv:                           { type: 'number' },
  ownership:                     { type: 'number' },
  scoping_for_audit:             { type: 'boolean' },
  reason_for_exclusion:          { type: 'text' },
  deal_level_stage_1:            { type: 'enum', options: DEAL_STAGE_1_OPTIONS },
  deal_level_stage_2:            { type: 'enum', options: DEAL_STAGE_2_OPTIONS },
  tentative_audit_completion_date: { type: 'text' },
  fy_end:                        { type: 'text' },
  auditor:                       { type: 'text' },
  category_of_auditor:           { type: 'text' },
  py_audit_status:               { type: 'text' },
  comments:                      { type: 'text' },
};

interface UploadResult {
  rows_processed: number;
  rows_updated: number;
  rows_inserted: number;
  rows_skipped: number;
  error_count: number;
  errors: { row: number; reason: string }[];
}

const MasterScopingPage: React.FC = () => {
  const [rows, setRows] = useState<PortfolioCompanyMetadataRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE);
  const [currentPage, setCurrentPage] = useState(1);
  const [reviewCycles, setReviewCycles] = useState<ReviewCycle[]>([]);
  const [selectedCycleId, setSelectedCycleId] = useState<string>('');

  // Per-column filters (same model as the Audit Tracker).
  const [columnFilters, setColumnFilters] = useState<Record<string, ColumnFilterState>>({});
  const [openFilterId, setOpenFilterId] = useState<string | null>(null);

  const [downloading, setDownloading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Actionable Deals modal
  const [showActionableDeals, setShowActionableDeals] = useState(false);
  const [actionableCount, setActionableCount] = useState(0);

  // Download modal — review cycle must be chosen before the file is downloaded.
  const [showDownloadModal, setShowDownloadModal] = useState(false);
  const [downloadCycleId, setDownloadCycleId] = useState('');

  // Upload modal — review cycle must be chosen before the file is sent.
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [uploadCycleId, setUploadCycleId] = useState('');
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  // Inline editing.
  const [editingCell, setEditingCell] = useState<{ rowId: number; field: keyof PortfolioCompanyMetadataRow; draft: string } | null>(null);
  const [savingCell, setSavingCell] = useState<{ rowId: number; field: string } | null>(null);
  // Pending edit awaiting user confirmation before it is saved.
  const [pendingEdit, setPendingEdit] = useState<{
    row: PortfolioCompanyMetadataRow;
    field: keyof PortfolioCompanyMetadataRow;
    label: string;
    oldValue: string;
    newValue: string;
  } | null>(null);

  // Delete confirmation.
  const [deleteTarget, setDeleteTarget] = useState<PortfolioCompanyMetadataRow | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Bulk edit — multi-row selection + a single dropdown field to set on all of them.
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkField, setBulkField] = useState<keyof PortfolioCompanyMetadataRow>(BULK_FIELDS[0].field);
  const [bulkValue, setBulkValue] = useState<string>('');
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);
  const [bulkSaving, setBulkSaving] = useState(false);
  const [bulkNotice, setBulkNotice] = useState<string | null>(null);

  const toggleRowSelected = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const clearSelection = () => setSelectedIds(new Set());

  const bulkFieldMeta = BULK_FIELDS.find((b) => b.field === bulkField) ?? BULK_FIELDS[0];

  const confirmBulkUpdate = async () => {
    if (selectedIds.size === 0) return;
    setBulkSaving(true);
    setError(null);
    setBulkNotice(null);
    try {
      const { data } = await apiClient.post<{ updated: number; skipped: number; not_found: number }>(
        '/api/v1/master-scoping/bulk',
        { ids: Array.from(selectedIds), field: bulkField, value: bulkValue },
      );
      // Re-fetch so derived/sibling state and any entity-status changes are reflected.
      await loadData();
      const parts = [`${data.updated} updated`];
      if (data.skipped > 0) parts.push(`${data.skipped} skipped`);
      if (data.not_found > 0) parts.push(`${data.not_found} not found`);
      setBulkNotice(`Bulk update: ${parts.join(', ')}.`);
      setBulkConfirmOpen(false);
      clearSelection();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail ?? (e instanceof Error ? e.message : 'Bulk update failed.'));
      setBulkConfirmOpen(false);
    } finally {
      setBulkSaving(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiClient.delete(`/api/v1/master-scoping/${deleteTarget.id}`);
      setRows((prev) => prev.filter((r) => r.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail ?? (e instanceof Error ? e.message : 'Failed to delete record.'));
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await apiClient.get<PortfolioCompanyMetadataRow[]>('/api/v1/master-scoping');
      setRows(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load master scoping data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
    apiClient
      .get<{ items: ReviewCycle[]; total: number }>('/api/v1/review-cycles', { params: { limit: 200, offset: 0 } })
      .then(({ data }) => {
        setReviewCycles(data.items);
        if (data.items.length > 0) {
          setSelectedCycleId(pickDefaultCycle(data.items) ?? data.items[0].id);
        }
      })
      .catch(() => {/* non-critical */});
    // Load actionable deals count for the badge
    apiClient
      .get<{ count: number }>('/api/v1/actionable-deals/count')
      .then(({ data }) => setActionableCount(data.count))
      .catch(() => {/* non-critical */});
  }, []);

  const handleDownload = async (cycleId: string) => {
    setDownloading(true);
    setShowDownloadModal(false);
    try {
      const params = cycleId ? { review_cycle_id: cycleId } : {};
      const response = await apiClient.get('/api/v1/master-scoping/download', {
        responseType: 'blob',
        params,
      });
      const contentDisposition = response.headers['content-disposition'] as string | undefined;
      const match = contentDisposition?.match(/filename="([^"]+)"/);
      const filename = match?.[1] ?? 'master_scoping.xlsx';
      const url = URL.createObjectURL(new Blob([response.data as BlobPart]));
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError('Download failed. Please try again.');
    } finally {
      setDownloading(false);
    }
  };

  const closeUploadModal = () => {
    setShowUploadModal(false);
    setUploadCycleId('');
    setUploadFile(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleUpload = async (file: File, cycleId: string) => {
    setUploading(true);
    setUploadResult(null);
    setUploadError(null);
    const form = new FormData();
    form.append('file', file);
    form.append('review_cycle_id', cycleId);
    try {
      const { data } = await apiClient.post<UploadResult>('/api/v1/master-scoping/upload', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setUploadResult(data);
      if (data.error_count === 0) void loadData();
      closeUploadModal();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setUploadError(detail ?? (e instanceof Error ? e.message : 'Upload failed.'));
    } finally {
      setUploading(false);
    }
  };

  // Stage an edit for confirmation rather than saving immediately.
  const commitEdit = (row: PortfolioCompanyMetadataRow, field: keyof PortfolioCompanyMetadataRow, draft: string) => {
    setEditingCell(null);
    const current = row[field];
    const currentStr = current == null ? '' : typeof current === 'boolean' ? (current ? 'Yes' : 'No') : String(current);
    if (draft.trim() === currentStr.trim()) return; // no-op

    const label = COLUMNS.find((c) => c.id === field)?.label ?? String(field);
    setPendingEdit({ row, field, label, oldValue: currentStr, newValue: draft });
  };

  const AGGREGATE_FIELDS = new Set(['cost', 'fmv']);

  const confirmEdit = async () => {
    if (!pendingEdit) return;
    const { row, field, newValue } = pendingEdit;
    setSavingCell({ rowId: row.id, field });
    setError(null);
    try {
      const { data } = await apiClient.patch<PortfolioCompanyMetadataRow>(
        `/api/v1/master-scoping/${row.id}`,
        { [field]: newValue },
      );

      if (AGGREGATE_FIELDS.has(field)) {
        // cost/fmv patch recalculates consolidated_cost/fmv for all rows sharing
        // the same deal_id in this cycle — re-fetch the full list so siblings
        // also reflect the new totals.
        const params = row.review_cycle_id ? { review_cycle_id: row.review_cycle_id } : {};
        const { data: freshRows } = await apiClient.get<PortfolioCompanyMetadataRow[]>(
          '/api/v1/master-scoping',
          { params },
        );
        setRows(freshRows);
      } else {
        setRows((prev) => prev.map((r) => (r.id === row.id ? data : r)));
      }

      setPendingEdit(null);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail ?? (e instanceof Error ? e.message : 'Failed to save change.'));
      setPendingEdit(null);
    } finally {
      setSavingCell(null);
    }
  };

  const renderCell = (
    row: PortfolioCompanyMetadataRow,
    col: (typeof COLUMNS)[number],
    idx: number,
  ) => {
    const value = row[col.id];
    const cfg = EDITABLE[col.id];
    const isEditing = editingCell?.rowId === row.id && editingCell?.field === col.id;
    const isSaving = savingCell?.rowId === row.id && savingCell?.field === col.id;
    const baseClass =
      idx === 0
        ? `${stickyTd} px-3 py-2 text-xs font-medium text-gray-900`
        : col.numeric
        ? scrollCellNum
        : scrollCell;

    // --- Always-on dropdown columns -----------------------------------------
    // Stage 1/2 and Scoping for Audit render as a live <select> in the table.
    if (cfg && DROPDOWN_COLUMNS.has(col.id)) {
      const options = cfg.type === 'boolean' ? ['Yes', 'No'] : cfg.options ?? [];
      const currentStr =
        value == null ? '' : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);
      // Stage 2 is locked until Stage 1 reports a completed status.
      const stage2Locked =
        col.id === 'deal_level_stage_2' &&
        !STAGE_2_ENABLED_STAGE_1.has(row.deal_level_stage_1 ?? '');
      const disabled = isSaving || stage2Locked;
      return (
        <td key={col.id} className={`${baseClass} ${isSaving ? 'opacity-50' : ''}`} onClick={(e) => e.stopPropagation()}>
          <select
            value={currentStr}
            disabled={disabled}
            title={stage2Locked ? 'Set Deal Stage 1 to a completed status to edit' : undefined}
            className={`w-full rounded border px-1 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 ${
              disabled
                ? 'cursor-not-allowed border-gray-200 bg-gray-100 text-gray-400'
                : 'border-gray-300 bg-white text-gray-700 hover:border-blue-400'
            }`}
            onChange={(e) => commitEdit(row, col.id, e.target.value)}
          >
            <option value="">—</option>
            {options.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        </td>
      );
    }

    // --- Editing mode -------------------------------------------------------
    if (isEditing && cfg) {
      const isChoice = cfg.type === 'enum' || cfg.type === 'boolean';
      const options = cfg.type === 'boolean' ? ['Yes', 'No'] : cfg.options ?? [];
      return (
        <td key={col.id} className={baseClass} onClick={(e) => e.stopPropagation()}>
          {isChoice ? (
            <select
              autoFocus
              value={editingCell.draft}
              className="w-full border border-blue-400 rounded px-1 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
              onChange={(e) => {
                const v = e.target.value;
                setEditingCell((p) => (p ? { ...p, draft: v } : null));
                void commitEdit(row, col.id, v);
              }}
              onBlur={() => setEditingCell(null)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setEditingCell(null);
              }}
            >
              <option value="">—</option>
              {options.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          ) : (
            <input
              autoFocus
              type="text"
              value={editingCell.draft}
              className="w-full border border-blue-400 rounded px-1 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
              onChange={(e) => setEditingCell((p) => (p ? { ...p, draft: e.target.value } : null))}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  void commitEdit(row, col.id, editingCell.draft);
                }
                if (e.key === 'Escape') setEditingCell(null);
              }}
              onBlur={() => {
                if (editingCell?.rowId === row.id && editingCell?.field === col.id)
                  void commitEdit(row, col.id, editingCell.draft);
              }}
            />
          )}
        </td>
      );
    }

    const startEdit = () => {
      const cur =
        value == null ? '' : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);
      setEditingCell({ rowId: row.id, field: col.id, draft: cur });
    };

    // --- Display content ----------------------------------------------------
    let content: React.ReactNode;
    if (idx === 0) {
      content = (
        <div className="flex items-start gap-1.5">
          <Building2 className="h-3.5 w-3.5 text-gray-500 shrink-0 mt-0.5" />
          <span className="break-words">{cellStr(value)}</span>
        </div>
      );
    } else if (col.id === 'scoping_for_audit') {
      content =
        value == null ? (
          <span className="text-gray-300">—</span>
        ) : (
          <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${value ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
            {value ? 'Yes' : 'No'}
          </span>
        );
    } else if (col.id === 'py_audit_status') {
      const s = value as string | null;
      const badge = s
        ? s.toLowerCase().includes('complete') || s.toLowerCase().includes('done')
          ? 'bg-green-100 text-green-800'
          : s.toLowerCase().includes('pending') || s.toLowerCase().includes('progress')
          ? 'bg-yellow-100 text-yellow-800'
          : 'bg-gray-100 text-gray-700'
        : '';
      content = s ? (
        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${badge}`}>{s}</span>
      ) : (
        <span className="text-gray-300">—</span>
      );
    } else if (col.id === 'updated_at' || col.id === 'created_at') {
      content = value ? (
        new Date(String(value)).toLocaleDateString()
      ) : (
        <span className="text-gray-300">—</span>
      );
    } else {
      const display = cellStr(value);
      content = display === '—' ? <span className="text-gray-300">—</span> : display;
    }

    const editableClass = cfg
      ? 'cursor-pointer hover:bg-blue-50 hover:ring-1 hover:ring-inset hover:ring-blue-200'
      : '';
    return (
      <td
        key={col.id}
        className={`${baseClass} ${editableClass} ${isSaving ? 'opacity-50' : ''}`}
        onClick={cfg && !isSaving ? startEdit : undefined}
        title={cfg ? 'Click to edit' : undefined}
      >
        {content}
      </td>
    );
  };

  const filteredRows = useMemo(() => {
    let result = rows;
    if (selectedCycleId) {
      result = result.filter((r) => r.review_cycle_id === selectedCycleId);
    }
    if (!searchTerm.trim()) return result;
    const q = searchTerm.toLowerCase();
    return result.filter((r) =>
      [r.deal_name, r.fund, r.deal_id, r.strategy, r.sector_l1, r.geo_l1, r.auditor, r.category]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(q),
    );
  }, [rows, searchTerm, selectedCycleId]);

  // Resolve a review-cycle id to its display name for cycle-aware filters.
  const getCycleLabel = useCallback(
    (id: string) => reviewCycles.find((c) => c.id === id)?.name ?? id,
    [reviewCycles],
  );

  // Rows after the global search + cycle filter, then narrowed by column filters.
  const displayRows = useMemo(
    () => applyColumnFilters(filteredRows, columnFilters, FILTER_COLUMNS, getCycleLabel),
    [filteredRows, columnFilters, getCycleLabel],
  );

  // Select-all spans every filtered row (all pages), not just the visible page.
  const displayRowIds = useMemo(() => displayRows.map((r) => r.id), [displayRows]);
  const allFilteredSelected =
    displayRowIds.length > 0 && displayRowIds.every((id) => selectedIds.has(id));
  const someFilteredSelected = displayRowIds.some((id) => selectedIds.has(id));

  const toggleSelectAll = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        displayRowIds.forEach((id) => next.delete(id));
      } else {
        displayRowIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  // Drop selections that are no longer visible under the current filters so the
  // selection count and bulk apply never act on hidden rows.
  useEffect(() => {
    setSelectedIds((prev) => {
      if (prev.size === 0) return prev;
      const visible = new Set(displayRowIds);
      const next = new Set<number>();
      prev.forEach((id) => {
        if (visible.has(id)) next.add(id);
      });
      return next.size === prev.size ? prev : next;
    });
  }, [displayRowIds]);

  const setColumnFilter = useCallback((id: string, next: ColumnFilterState | undefined) => {
    setColumnFilters((prev) => {
      const copy = { ...prev };
      if (!next) {
        delete copy[id];
        return copy;
      }
      if (next.kind === 'number' && !next.min.trim() && !next.max.trim()) {
        delete copy[id];
        return copy;
      }
      if (next.kind === 'date' && !next.start.trim() && !next.end.trim()) {
        delete copy[id];
        return copy;
      }
      if (next.kind === 'string' && next.selected.length === 0) {
        delete copy[id];
        return copy;
      }
      copy[id] = next;
      return copy;
    });
  }, []);

  // Close an open filter popup when clicking outside it.
  useEffect(() => {
    if (!openFilterId) return;
    const onDoc = (e: MouseEvent) => {
      const el = e.target as HTMLElement;
      if (el.closest(`[data-column-filter="${openFilterId}"]`)) return;
      setOpenFilterId(null);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [openFilterId]);

  // Distinct count of non-blank values in a given column across the displayed rows.
  const distinctNonEmpty = (key: keyof PortfolioCompanyMetadataRow): number => {
    const seen = new Set<string>();
    for (const r of displayRows) {
      const v = r[key];
      if (v != null && String(v).trim() !== '') seen.add(String(v).trim());
    }
    return seen.size;
  };
  const totalUniqueByCompany = useMemo(
    () => distinctNonEmpty('deal_id_for_analysis'),
    [displayRows],
  );
  const totalUniqueByCompanyStrategy = useMemo(
    () => distinctNonEmpty('deal_id_for_analysis_and_strategy'),
    [displayRows],
  );

  const totalFiltered = displayRows.length;
  const totalPages = Math.max(1, Math.ceil(totalFiltered / pageSize));
  const safePage = Math.min(Math.max(1, currentPage), totalPages);

  useEffect(() => {
    if (currentPage !== safePage) setCurrentPage(safePage);
  }, [currentPage, safePage]);

  useEffect(() => {
    setCurrentPage(1);
  }, [searchTerm, pageSize, selectedCycleId, columnFilters]);

  const pageStart = totalFiltered === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const pageEnd = Math.min(safePage * pageSize, totalFiltered);
  const isPaginated = totalFiltered > pageSize;
  const pagedRows = useMemo(
    () => displayRows.slice((safePage - 1) * pageSize, safePage * pageSize),
    [displayRows, safePage, pageSize],
  );

  const pendingSaving = pendingEdit != null && savingCell?.rowId === pendingEdit.row.id && savingCell?.field === pendingEdit.field;

  return (
    <div className="h-full overflow-auto bg-gray-50 p-6">
      {/* Confirm dialog — runs before any value is saved */}
      {pendingEdit && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold text-gray-900">Confirm change</h2>
            <p className="mt-1 text-sm text-gray-500">
              Update <span className="font-medium text-gray-700">{pendingEdit.label}</span> for{' '}
              <span className="font-medium text-gray-700">{pendingEdit.row.deal_name || pendingEdit.row.deal_id}</span>?
            </p>

            <div className="mt-4 space-y-2 text-sm">
              <div className="flex gap-2">
                <span className="w-16 shrink-0 text-gray-400">From</span>
                <span className="rounded bg-gray-100 px-2 py-0.5 text-gray-700">{pendingEdit.oldValue || '—'}</span>
              </div>
              <div className="flex gap-2">
                <span className="w-16 shrink-0 text-gray-400">To</span>
                <span className="rounded bg-blue-50 px-2 py-0.5 font-medium text-blue-800">{pendingEdit.newValue || '—'}</span>
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingEdit(null)}
                disabled={pendingSaving}
                className="px-4 py-2 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmEdit()}
                disabled={pendingSaving}
                className="px-4 py-2 rounded-lg border border-blue-600 bg-blue-600 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {pendingSaving ? 'Saving…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk edit confirmation dialog */}
      {bulkConfirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold text-gray-900">Confirm bulk change</h2>
            <p className="mt-1 text-sm text-gray-500">
              Set <span className="font-medium text-gray-700">{bulkFieldMeta.label}</span> to{' '}
              <span className="font-medium text-gray-700">{bulkValue || '—'}</span> for{' '}
              <span className="font-medium text-gray-700">{selectedIds.size}</span>{' '}
              selected {selectedIds.size === 1 ? 'record' : 'records'}?
            </p>
            {bulkField === 'deal_level_stage_2' && (
              <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                Rows whose Deal Stage 1 is not a completed status will be skipped.
              </p>
            )}
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setBulkConfirmOpen(false)}
                disabled={bulkSaving}
                className="px-4 py-2 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmBulkUpdate()}
                disabled={bulkSaving}
                className="px-4 py-2 rounded-lg border border-blue-600 bg-blue-600 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {bulkSaving ? 'Saving…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirmation dialog */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold text-gray-900">Delete record?</h2>
            <p className="mt-2 text-sm text-gray-500">
              This will permanently delete the master scoping record for{' '}
              <span className="font-medium text-gray-800">{deleteTarget.deal_name || deleteTarget.deal_id}</span>
              {deleteTarget.fund ? ` (${deleteTarget.fund})` : ''}.
              This action cannot be undone.
            </p>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
                className="px-4 py-2 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmDelete()}
                disabled={deleting}
                className="px-4 py-2 rounded-lg border border-red-600 bg-red-600 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Download modal — review cycle selection before downloading */}
      {showDownloadModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold text-gray-900">Download Master Scoping</h2>
            <p className="mt-1 text-sm text-gray-500">
              Select a review cycle to download only that cycle's data, or choose "All cycles" to download everything.
            </p>

            <label htmlFor="download-cycle" className="mt-4 block text-sm font-medium text-gray-700">
              Review Cycle <span className="text-red-500">*</span>
            </label>
            <select
              id="download-cycle"
              value={downloadCycleId}
              onChange={(e) => setDownloadCycleId(e.target.value)}
              className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
            >
              <option value="">All cycles</option>
              {reviewCycles.map((rc) => (
                <option key={rc.id} value={rc.id}>
                  {rc.name ?? rc.id}
                </option>
              ))}
            </select>

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowDownloadModal(false)}
                className="px-4 py-2 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleDownload(downloadCycleId)}
                className="px-4 py-2 rounded-lg border border-blue-600 bg-blue-600 text-sm font-medium text-white hover:bg-blue-700"
              >
                Download
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Upload modal — review cycle is required before the file is sent */}
      {showUploadModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold text-gray-900">Upload Master Scoping</h2>
            <p className="mt-1 text-sm text-gray-500">
              Select the review cycle these records belong to. Every uploaded row will be assigned to
              this cycle.
            </p>

            <label htmlFor="upload-cycle" className="mt-4 block text-sm font-medium text-gray-700">
              Review Cycle <span className="text-red-500">*</span>
            </label>
            <select
              id="upload-cycle"
              value={uploadCycleId}
              onChange={(e) => setUploadCycleId(e.target.value)}
              className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
            >
              <option value="">Select a review cycle…</option>
              {reviewCycles.map((rc) => (
                <option key={rc.id} value={rc.id}>
                  {rc.name ?? rc.id}
                </option>
              ))}
            </select>

            <label htmlFor="upload-file" className="mt-4 block text-sm font-medium text-gray-700">
              XLSX File <span className="text-red-500">*</span>
            </label>
            <input
              id="upload-file"
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xls"
              disabled={uploading}
              onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
              className="mt-1 w-full text-sm text-gray-700 file:mr-3 file:rounded-lg file:border-0 file:bg-gray-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-gray-700 hover:file:bg-gray-200"
            />

            {uploadError && (
              <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {uploadError}
              </p>
            )}

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={closeUploadModal}
                disabled={uploading}
                className="px-4 py-2 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  if (uploadFile && uploadCycleId) void handleUpload(uploadFile, uploadCycleId);
                }}
                disabled={uploading || !uploadFile || !uploadCycleId}
                className="px-4 py-2 rounded-lg border border-blue-600 bg-blue-600 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {uploading ? 'Uploading…' : 'Upload'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Master Scoping</h1>
        <div className="flex items-center gap-2">
          {/* Actionable Deals button with badge */}
          <button
            type="button"
            onClick={() => setShowActionableDeals(true)}
            className="relative inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-orange-400 bg-orange-50 text-sm font-medium text-orange-700 hover:bg-orange-100 shadow-sm"
          >
            <AlertCircle className="h-4 w-4" />
            Actionable Deals
            {actionableCount > 0 && (
              <span className="absolute -top-1.5 -right-1.5 inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1 rounded-full bg-orange-500 text-white text-xs font-bold">
                {actionableCount}
              </span>
            )}
          </button>
          <button
            type="button"
            onClick={() => {
              setDownloadCycleId(selectedCycleId);
              setShowDownloadModal(true);
            }}
            disabled={downloading}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed shadow-sm"
          >
            <Download className="h-4 w-4" />
            {downloading ? 'Downloading…' : 'Download XLSX'}
          </button>
          <button
            type="button"
            onClick={() => {
              setUploadError(null);
              setShowUploadModal(true);
            }}
            disabled={uploading}
            className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg border text-sm font-medium shadow-sm ${uploading ? 'border-gray-300 bg-gray-100 text-gray-400 cursor-not-allowed' : 'border-blue-600 bg-blue-600 text-white hover:bg-blue-700'}`}
          >
            <Upload className="h-4 w-4" />
            {uploading ? 'Uploading…' : 'Upload XLSX'}
          </button>
        </div>
      </div>

      {error && <p className="text-sm text-red-600 mb-4">{error}</p>}

      {/* Upload feedback */}
      {uploadError && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <strong>Upload failed:</strong> {uploadError}
        </div>
      )}
      {uploadResult && (
        <div className={`mb-4 rounded-lg border px-4 py-3 text-sm ${uploadResult.error_count > 0 ? 'border-yellow-200 bg-yellow-50 text-yellow-800' : 'border-green-200 bg-green-50 text-green-800'}`}>
          <p className="font-semibold mb-1">{uploadResult.error_count > 0 ? 'Upload completed with errors' : 'Upload successful'}</p>
          <p>Processed: {uploadResult.rows_processed} · Inserted: {uploadResult.rows_inserted} · Updated: {uploadResult.rows_updated} · Skipped: {uploadResult.rows_skipped}</p>
          {uploadResult.errors.length > 0 && (
            <ul className="mt-2 list-disc list-inside space-y-0.5 text-xs">
              {uploadResult.errors.map((err) => (
                <li key={err.row}>Row {err.row}: {err.reason}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Search bar + cycle filter */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search deal, fund, sector…"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-9 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          />
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <label htmlFor="cycle-filter" className="whitespace-nowrap font-medium">Review Cycle</label>
          <select
            id="cycle-filter"
            value={selectedCycleId}
            onChange={(e) => setSelectedCycleId(e.target.value)}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white min-w-[180px]"
          >
            <option value="">All cycles (no filter)</option>
            {reviewCycles.map((rc) => (
              <option key={rc.id} value={rc.id}>
                {rc.name ?? rc.id}
              </option>
            ))}
          </select>
        </div>
        {Object.keys(columnFilters).length > 0 && (
          <button
            type="button"
            onClick={() => {
              setColumnFilters({});
              setOpenFilterId(null);
            }}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
          >
            <Filter className="h-4 w-4" />
            Clear column filters
          </button>
        )}
      </div>

      {bulkNotice && (
        <div className="mb-4 rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          {bulkNotice}
        </div>
      )}

      {/* Bulk edit toolbar — visible whenever at least one row is selected */}
      {selectedIds.size > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3">
          <span className="text-sm font-semibold text-blue-800">
            {selectedIds.size} selected
          </span>
          <div className="h-5 w-px bg-blue-200" />
          <label className="text-sm font-medium text-gray-700">Set</label>
          <select
            value={bulkField}
            onChange={(e) => {
              setBulkField(e.target.value as keyof PortfolioCompanyMetadataRow);
              setBulkValue('');
            }}
            className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
          >
            {BULK_FIELDS.map((b) => (
              <option key={b.field} value={b.field}>
                {b.label}
              </option>
            ))}
          </select>
          <label className="text-sm font-medium text-gray-700">to</label>
          <select
            value={bulkValue}
            onChange={(e) => setBulkValue(e.target.value)}
            className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white min-w-[160px]"
          >
            <option value="">— (clear)</option>
            {bulkFieldMeta.options.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setBulkConfirmOpen(true)}
            className="px-4 py-1.5 rounded-lg border border-blue-600 bg-blue-600 text-sm font-medium text-white hover:bg-blue-700"
          >
            Apply
          </button>
          <button
            type="button"
            onClick={clearSelection}
            className="px-3 py-1.5 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Clear
          </button>
        </div>
      )}

      <div className="w-full max-w-full">
        {/* Count + pagination controls above the table */}
        <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center rounded-full bg-blue-100 px-3 py-1 text-sm font-semibold text-blue-700">
              {isPaginated
                ? `Showing ${pageStart}–${pageEnd} of ${totalFiltered} records`
                : `${totalFiltered} ${totalFiltered === 1 ? 'record' : 'records'}`}
            </span>
            <span className="inline-flex items-center rounded-full bg-gray-100 px-3 py-1 text-sm font-semibold text-gray-600">
              {loading ? '—' : totalUniqueByCompany} unique CID for Analysis
            </span>
            <span className="inline-flex items-center rounded-full bg-gray-100 px-3 py-1 text-sm font-semibold text-gray-600">
              {loading ? '—' : totalUniqueByCompanyStrategy} unique CID for Analysis &amp; Strategy
            </span>
          </div>

          {totalFiltered > 0 && (
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2 text-sm text-gray-600">
                <span>Rows per page</span>
                <select
                  value={pageSize}
                  onChange={(e) => setPageSize(Number(e.target.value))}
                  className="px-2 py-1 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
                >
                  {PAGE_SIZE_OPTIONS.map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
              </div>
              {totalPages > 1 && (
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                    disabled={safePage <= 1}
                    className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <ChevronLeft className="h-4 w-4" /> Prev
                  </button>
                  {getPageNumbers(safePage, totalPages).map((p, i) =>
                    p === '…' ? (
                      <span key={`gap-${i}`} className="px-2 text-gray-400 select-none">…</span>
                    ) : (
                      <button
                        key={p}
                        type="button"
                        onClick={() => setCurrentPage(p as number)}
                        className={`min-w-[2rem] px-2 py-1.5 rounded-lg text-sm font-medium border ${
                          p === safePage
                            ? 'bg-blue-500 text-white border-blue-500'
                            : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
                        }`}
                      >
                        {p}
                      </button>
                    ),
                  )}
                  <button
                    type="button"
                    onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                    disabled={safePage >= totalPages}
                    className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    Next <ChevronRight className="h-4 w-4" />
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Table */}
        <div className="overflow-x-auto overflow-y-visible rounded-lg border border-gray-200 bg-white shadow-sm [scrollbar-gutter:stable]">
          <table className="w-max min-w-full border-separate border-spacing-0">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                {/* Select-all checkbox column */}
                <th className="px-2 py-2.5 border-b border-gray-200 border-r border-gray-200 w-8">
                  <input
                    type="checkbox"
                    aria-label="Select all filtered rows"
                    title="Select all filtered rows"
                    className="h-3.5 w-3.5 cursor-pointer rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    checked={allFilteredSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = !allFilteredSelected && someFilteredSelected;
                    }}
                    onChange={toggleSelectAll}
                  />
                </th>
                {/* Delete action column */}
                <th className="px-2 py-2.5 text-xs font-medium text-gray-500 uppercase tracking-wider whitespace-nowrap border-b border-gray-200 border-r border-gray-200 w-8" />
                {COLUMNS.map((col, idx) => {
                  const filterCol = FILTER_COLUMN_BY_ID.get(col.id);
                  return (
                    <th
                      key={col.id}
                      className={[
                        'text-xs font-medium text-gray-500 uppercase tracking-wider py-2.5 align-middle whitespace-nowrap border-b border-gray-200',
                        idx === 0
                          ? `px-3 text-left ${stickyTh}`
                          : 'px-2 text-left border-r border-gray-200',
                      ].join(' ')}
                    >
                      <div className="flex items-center justify-between gap-1.5">
                        <span className="truncate">{col.label}</span>
                        {filterCol && (
                          <AuditTrackerFilterCell
                            col={filterCol}
                            baseRows={filteredRows}
                            filter={columnFilters[col.id]}
                            onChange={(next) => setColumnFilter(col.id, next)}
                            isOpen={openFilterId === col.id}
                            onToggle={() => setOpenFilterId((x) => (x === col.id ? null : col.id))}
                            getCycleLabel={getCycleLabel}
                            className="shrink-0"
                          />
                        )}
                      </div>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {loading ? (
                <tr>
                  <td colSpan={COLUMNS.length + 2} className="px-4 py-12 text-center text-sm text-gray-400">
                    Loading…
                  </td>
                </tr>
              ) : pagedRows.length === 0 && filteredRows.length > 0 ? (
                <tr>
                  <td colSpan={COLUMNS.length + 2} className="px-4 py-12 text-center text-sm text-amber-700 bg-amber-50/50">
                    No rows match the current column filters. Clear filters or widen your criteria.
                  </td>
                </tr>
              ) : pagedRows.length === 0 ? (
                <tr>
                  <td colSpan={COLUMNS.length + 2} className="px-4 py-12 text-center text-sm text-gray-400">
                    No records found.
                  </td>
                </tr>
              ) : (
                pagedRows.map((row) => (
                  <tr
                    key={row.id}
                    className={`group transition-colors ${selectedIds.has(row.id) ? 'bg-blue-50/60 hover:bg-blue-50' : 'hover:bg-gray-50'}`}
                  >
                    {/* Row select checkbox */}
                    <td className="px-2 py-2 align-top border-r border-gray-100 w-8" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={`Select ${row.deal_name || row.deal_id}`}
                        className="h-3.5 w-3.5 cursor-pointer rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                        checked={selectedIds.has(row.id)}
                        onChange={() => toggleRowSelected(row.id)}
                      />
                    </td>
                    {/* Delete button */}
                    <td className="px-2 py-2 align-top border-r border-gray-100 w-8">
                      <button
                        type="button"
                        onClick={() => setDeleteTarget(row)}
                        title="Delete record"
                        className="opacity-0 group-hover:opacity-100 transition-opacity text-gray-400 hover:text-red-600 focus:opacity-100"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                    {COLUMNS.map((col, idx) => renderCell(row, col, idx))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {showActionableDeals && (
        <ActionableDealsModal
          onClose={() => setShowActionableDeals(false)}
          onCountChange={setActionableCount}
        />
      )}
    </div>
  );
};

export default MasterScopingPage;
