import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AlertCircle, Building2, ChevronLeft, ChevronRight, FileSpreadsheet, Filter, Search, Upload } from 'lucide-react';
import { toast } from 'sonner';
import apiClient from '@/api/axios';
import type { PortfolioCompanyMetadataRow } from '@/pages/MasterScopingPage';

import { AuditReportExportDialog } from '@/components/AuditReportExportDialog';

import type { ReviewStage } from '@/types/reviewCycle';
import type { ReviewCycle } from '@/types/reviewCycle';
import { listCycleEntities, patchPortfolioCompany, patchEntity, fetchScopedInDealIds, type CycleEntityRow } from '@/api/portfolio';
import { downloadDashboardData, uploadDashboardData, type DashboardUploadResult } from '@/api/dashboardData';
import { pickDefaultCycle } from '@/api/dashboard';
import { fetchReviewCycles } from '@/api/reviewCycleAdjustments';
import {
  applyColumnFilters,
  REVIEW_CYCLE_COLUMN,
  STAGES,
  type ColumnFilterState,
  type DashboardColumnMeta,
} from '@/pages/reviewCycleColumnFilters';
import { AuditTrackerFilterCell } from '@/pages/AuditTrackerFilterCell';

/** Page-size options for the table pager; default is the largest (matches the prior 500-row view). */
const PAGE_SIZE_OPTIONS = [100, 200, 500] as const;
const DEFAULT_PAGE_SIZE = 500;
/** The backend caps each list request at 500 rows; we page through with offsets to hold the full scope. */
const FETCH_PAGE_SIZE = 500;

/** Page-number buttons with ellipses, e.g. 1 … 4 5 [6] 7 8 … 20. */
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

type EditInputType = 'text' | 'number' | 'date';
const EDITABLE_FIELDS: Record<string, { label: string; type: EditInputType }> = {
  entity_geolocation:           { label: 'Geo',                    type: 'text' },
  entity_type:                  { label: 'Holding/Subsidiary',     type: 'text' },
  entity_comments:              { label: 'Comments',               type: 'text' },
  entity_one_desk_email_status: { label: 'One Desk Email Status',  type: 'text' },
};

/** Fields that live on the Entity record and must be saved via patchEntity. */
const ENTITY_LEVEL_FIELDS = new Set(['entity_geolocation', 'entity_type', 'entity_comments', 'entity_one_desk_email_status']);

/** Terminal statuses that trigger the Deal Stage update modal. */
const TERMINAL_STATUSES = new Set<string>([
  'Approved', 'Approved - Flagged', 'Not Approved', 'Not Approved - Flagged',
]);

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

const STAGE_2_ENABLED_STAGE_1 = new Set(['Completed within due date', 'Completed post due date']);

/** Entity statuses that count as actively under review (mirrors the backend active band). */
const ACTIVE_REVIEW_STATUSES = new Set<string>([
  'In review',
  'Discrepancy identified',
  'No discrepancy identified',
  'Not comparable',
  'Query sent',
  'Query response reminder sent 1',
  'Query response reminder sent 2',
  'Response received',
  'Response received - Partially answered',
  'Response received - Call to be scheduled',
]);

/** Terminal approval-decision statuses. */
const COMPLETED_REVIEW_STATUSES = new Set<string>([
  'Approved',
  'Approved - Flagged',
  'Not Approved',
  'Not Approved - Flagged',
]);


const scrollCell =
  'px-2 py-2 text-xs text-gray-700 align-top min-w-[7rem] max-w-[18rem] break-words whitespace-normal';
const stickyCompanyTh =
  'sticky left-0 z-20 min-w-[12rem] max-w-[16rem] border-r border-gray-200 bg-gray-50 shadow-[4px_0_12px_-6px_rgba(0,0,0,0.12)]';
const stickyCompanyTd =
  'sticky left-0 z-10 min-w-[12rem] max-w-[16rem] border-r border-gray-200 bg-white shadow-[4px_0_12px_-6px_rgba(0,0,0,0.1)] group-hover:bg-gray-50';

/** Header labels keyed by column id. */
const COLUMN_LABELS: Record<string, string> = {
  company_id:                   'Deal ID',
  name:                         'Deal Name',
  entity_name:                  'Entity Name',
  entity_geolocation:           'Geo (Entity Level)',
  entity_type:                  'Holding / Subsidiary',
  entity_status:                'Status',
  entity_comments:              'Comments',
  entity_one_desk_email_status: 'One Desk Email Status',
  review_cycle_id:              'Review cycle',
};

type CompanyFilter = 'all' | 'overdue' | 'in-review' | 'completed' | 'no-org-chart';
type ScopingTab = 'scoped-in' | 'scoped-out';

const PLACEHOLDER_STATUS = 'Upload org chart / create entities';

const AuditTrackerPage: React.FC = () => {
  const navigate = useNavigate();
  const [rcCycles, setRcCycles] = useState<ReviewCycle[]>([]);
  const [rcDataLoading, setRcDataLoading] = useState(true);
  const [entriesLoading, setEntriesLoading] = useState(false);
  const [entriesError, setEntriesError] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  const [rows, setRows] = useState<CycleEntityRow[]>([]);
  const cacheRef = useRef<Map<string, CycleEntityRow[]>>(new Map());

  // Bulk selection state — keyed by entity_id (not row id).
  const [selectedEntityIds, setSelectedEntityIds] = useState<Set<number>>(new Set());
  const [bulkStatus, setBulkStatus] = useState<string>('');
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);
  const [bulkSaving, setBulkSaving] = useState(false);

  const toggleRowSelected = (entityId: number) => {
    setSelectedEntityIds((prev) => {
      const next = new Set(prev);
      if (next.has(entityId)) next.delete(entityId);
      else next.add(entityId);
      return next;
    });
  };

  const clearBulkSelection = () => {
    setSelectedEntityIds(new Set());
    setBulkStatus('');
  };

  const [pageSize, setPageSize] = useState<number>(DEFAULT_PAGE_SIZE);
  const [currentPage, setCurrentPage] = useState(1);

  const [selectedCycleId, setSelectedCycleId] = useState('');
  const [companyFilter, setCompanyFilter] = useState<CompanyFilter>('all');
  const [searchTerm, setSearchTerm] = useState('');
  // Confirm dialog: step 1 = simple confirm; step 2 = expanded with MCP panel (terminal statuses only).
  const [confirmDialog, setConfirmDialog] = useState<{
    entityId: number; entityName: string; newStatus: ReviewStage; companyId: string;
    // step 2 fields (populated once user says Yes on a terminal status)
    step: 1 | 2;
    wantsMcp: boolean;
    mcpLoading: boolean;
    mcpRows: PortfolioCompanyMetadataRow[];
    mcpDrafts: Record<number, { stage1: string; stage2: string }>;
    mcpChecked: Set<number>;
    bulkStage1: string;
    bulkStage2: string;
    saving: boolean;
  } | null>(null);
  const [editingCell, setEditingCell] = useState<{ companyId: number; field: string; draftValue: string } | null>(null);
  const [editReasonDialog, setEditReasonDialog] = useState<{
    companyId: number; entityId?: number; field: string; fieldLabel: string;
    oldValue: string; newValue: string; reason: string;
  } | null>(null);
  const [columnFilters, setColumnFilters] = useState<Record<string, ColumnFilterState>>({});
  const [openFilterId, setOpenFilterId] = useState<string | null>(null);
  const [scopingTab, setScopingTab] = useState<ScopingTab>('scoped-in');
  const [scopedInDealIds, setScopedInDealIds] = useState<Set<string>>(new Set());
  const [scopingLoading, setScopingLoading] = useState(false);

  const [showExportDialog, setShowExportDialog] = useState(false);
  const [dashboardDownloading, setDashboardDownloading] = useState(false);
  const [dashboardUploading, setDashboardUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<DashboardUploadResult | null>(null);
  const dashboardUploadRef = useRef<HTMLInputElement>(null);
  const pendingScopedInRef = useRef<boolean>(true);
  // Pending action modal: ask user "Scoped In or Scoped Out?" before download/upload.
  const [pendingAction, setPendingAction] = useState<'download' | 'upload' | null>(null);
  useEffect(() => {
    const loadCycles = async () => {
      setRcDataLoading(true);
      try {
        const cycles = await fetchReviewCycles();
        setRcCycles(cycles);
      } catch (e) {
        console.error(e);
        setRcCycles([]);
      } finally {
        setRcDataLoading(false);
      }
    };
    void loadCycles();
  }, []);

  // Default cycle selection:
  // - All companies: latest cycle
  // - Completed: latest cycle
  // - Overdue/In Review: all cycles (empty), but allow selecting a period to scope
  useEffect(() => {
    if (rcCycles.length === 0) return;
    if (companyFilter === 'overdue' || companyFilter === 'in-review') {
      if (selectedCycleId) return;
      setSelectedCycleId('');
      return;
    }
    if (!selectedCycleId) setSelectedCycleId(pickDefaultCycle(rcCycles) ?? '');
  }, [rcCycles, companyFilter, selectedCycleId]);

  const cacheKey = useMemo(() => {
    const cyclePart = selectedCycleId || '__all__';
    return `filter=${companyFilter}|cycle=${cyclePart}`;
  }, [companyFilter, selectedCycleId]);

  useEffect(() => {
    const load = async () => {
      // Guard: when a cycle is required but not selected yet, don't fetch.
      if ((companyFilter === 'all' || companyFilter === 'completed' || companyFilter === 'no-org-chart') && !selectedCycleId) return;

      const cached = cacheRef.current.get(cacheKey);
      if (cached) {
        setRows(cached);
        return;
      }

      setEntriesLoading(true);
      setEntriesError(null);
      try {
        // The Audit Tracker is entity-based: one row per entity in the cycle. The
        // company filter (all / overdue / in-review / completed) is applied client-side
        // over entity_status (see companyFilter handling below), so we always fetch the
        // full set of entities for the selected cycle here.
        const params: {
          limit: number;
          offset: number;
          review_cycle_id?: string;
        } = { limit: FETCH_PAGE_SIZE, offset: 0 };

        if (selectedCycleId) params.review_cycle_id = selectedCycleId;

        const first = await listCycleEntities(params);
        let nextRows: CycleEntityRow[] = first.items ?? [];
        const total = typeof first.total === 'number' ? first.total : nextRows.length;
        if (total > nextRows.length) {
          const offsets: number[] = [];
          for (let o = FETCH_PAGE_SIZE; o < total; o += FETCH_PAGE_SIZE) offsets.push(o);
          const more = await Promise.all(
            offsets.map((o) => listCycleEntities({ ...params, offset: o })),
          );
          for (const pg of more) nextRows = nextRows.concat(pg.items ?? []);
        }

        cacheRef.current.set(cacheKey, nextRows);
        setRows(nextRows);
      } catch (e) {
        console.error(e);
        setEntriesError(e instanceof Error ? e.message : 'Failed to load companies');
        setRows([]);
      } finally {
        setEntriesLoading(false);
      }
    };

    void load();
  }, [cacheKey, companyFilter, selectedCycleId, reloadNonce]);

  // Fetch scoping metadata for the selected review cycle so we can split companies
  // into "Scoped In" and "Scoped Out" tabs. A company (deal_id = company_id) is
  // "Scoped In" if any PortfolioCompanyMetadata row for this cycle has scoping_for_audit=true.
  useEffect(() => {
    if (!selectedCycleId) {
      setScopedInDealIds(new Set());
      return;
    }
    let cancelled = false;
    const load = async () => {
      setScopingLoading(true);
      try {
        const ids = await fetchScopedInDealIds(selectedCycleId);
        if (!cancelled) setScopedInDealIds(ids);
      } catch {
        if (!cancelled) setScopedInDealIds(new Set());
      } finally {
        if (!cancelled) setScopingLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [selectedCycleId]);

  // Reset scoping tab to 'scoped-in' whenever the cycle changes.
  useEffect(() => {
    setScopingTab('scoped-in');
  }, [selectedCycleId]);

  const showAllCyclesDefault = companyFilter === 'overdue' || companyFilter === 'in-review';

  const getCycleLabel = useCallback((id: string) => rcCycles.find((c) => c.id === id)?.label ?? id, [rcCycles]);

  const commitEdit = useCallback((c: CycleEntityRow, field: string, newValue: string) => {
    setEditingCell(null);
    const oldValue = String((c as unknown as Record<string, unknown>)[field] ?? '');
    if (newValue.trim() === oldValue.trim()) return;
    setEditReasonDialog({
      companyId: c.id,
      entityId: ENTITY_LEVEL_FIELDS.has(field) ? (c.entity_id ?? undefined) : undefined,
      field,
      fieldLabel: EDITABLE_FIELDS[field].label,
      oldValue: oldValue || '—',
      newValue,
      reason: '',
    });
  }, []);

  const handleConfirmEdit = async () => {
    if (!editReasonDialog) return;
    const { companyId, entityId, field, newValue, reason } = editReasonDialog;
    try {
      if (ENTITY_LEVEL_FIELDS.has(field) && entityId != null) {
        // Map the display key back to the actual entity field name.
        const entityFieldMap: Record<string, string> = {
          entity_comments: 'comments',
          entity_geolocation: 'geolocation',
          entity_type: 'entity_type',
          entity_one_desk_email_status: 'one_desk_email_status',
        };
        const entityField = entityFieldMap[field] ?? field;
        await patchEntity(entityId, { [entityField]: newValue || null });
      } else {
        await patchPortfolioCompany(
          companyId,
          { [field]: newValue || null, edit_reason: reason },
          { auditContext: 'review-cycle-adjustments' },
        );
      }
      cacheRef.current.clear();
      setReloadNonce((n) => n + 1);
      toast.success(`${editReasonDialog.fieldLabel} updated`);
      setEditReasonDialog(null);
    } catch {
      toast.error('Failed to save change');
    }
  };

  const renderEditableCell = (
    c: CycleEntityRow,
    field: string,
    displayValue: string,
    className = scrollCell,
  ) => {
    const isEditing = editingCell?.companyId === c.id && editingCell?.field === field;
    const meta = EDITABLE_FIELDS[field];
    if (isEditing) {
      return (
        <td key={field} className={className} onClick={(e) => e.stopPropagation()}>
          <input
            autoFocus
            type={meta.type}
            value={editingCell.draftValue}
            className="w-full border border-blue-400 rounded px-1 py-0.5 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500"
            onChange={(e) => setEditingCell((p) => (p ? { ...p, draftValue: e.target.value } : null))}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); commitEdit(c, field, editingCell.draftValue); }
              if (e.key === 'Escape') setEditingCell(null);
            }}
            onBlur={() => {
              if (editingCell?.companyId === c.id && editingCell?.field === field)
                commitEdit(c, field, editingCell.draftValue);
            }}
          />
        </td>
      );
    }
    return (
      <td
        key={field}
        className={`${className} cursor-pointer hover:bg-blue-50 hover:ring-1 hover:ring-inset hover:ring-blue-200`}
        onClick={(e) => {
          e.stopPropagation();
          setEditingCell({ companyId: c.id, field, draftValue: String((c as unknown as Record<string, unknown>)[field] ?? '') });
        }}
      >
        {displayValue === '—' ? <span className="text-gray-300">—</span> : displayValue}
      </td>
    );
  };

  // The 8 visible columns. "Review cycle" is appended when "All Cycles" is selected.
  const visibleFilterColumns = useMemo(() => {
    const base: DashboardColumnMeta<CycleEntityRow>[] = [
      { id: 'company_id',         field: 'company_id',         kind: 'string' },
      { id: 'name',               field: 'name',               kind: 'string' },
      {
        id: 'entity_name', field: null, kind: 'string',
        valueFrom: (r) => (r as unknown as { entity_name?: string | null }).entity_name ?? '—',
      },
      {
        id: 'entity_geolocation', field: null, kind: 'string',
        valueFrom: (r) => (r as unknown as { entity_geolocation?: string | null }).entity_geolocation ?? '—',
      },
      {
        id: 'entity_type', field: null, kind: 'string',
        valueFrom: (r) => (r as unknown as { entity_type?: string | null }).entity_type ?? '—',
      },
      {
        id: 'entity_status', field: null, kind: 'string',
        enumOptions: STAGES,
        valueFrom: (r) => (r as unknown as { entity_status?: string | null }).entity_status ?? '—',
      },
      {
        id: 'entity_comments', field: null, kind: 'string',
        valueFrom: (r) => (r as unknown as { entity_comments?: string | null }).entity_comments ?? '—',
      },
      {
        id: 'entity_one_desk_email_status', field: null, kind: 'string',
        valueFrom: (r) => (r as unknown as { entity_one_desk_email_status?: string | null }).entity_one_desk_email_status ?? '—',
      },
    ];
    if (showAllCyclesDefault) return [...base, REVIEW_CYCLE_COLUMN as unknown as DashboardColumnMeta<CycleEntityRow>];
    return base;
  }, [showAllCyclesDefault]);

  // Company-filter buttons now narrow by entity_status (state lives on entities).
  const companyFilteredRows = useMemo(() => {
    if (companyFilter === 'in-review') {
      return rows.filter((c) => ACTIVE_REVIEW_STATUSES.has((c.entity_status ?? '').trim()));
    }
    if (companyFilter === 'completed') {
      return rows.filter((c) => COMPLETED_REVIEW_STATUSES.has((c.entity_status ?? '').trim()));
    }
    if (companyFilter === 'no-org-chart') {
      return rows.filter((c) => !c.has_org_chart);
    }
    // 'all' and 'overdue' (no entity-status equivalent) show every entity in scope.
    return rows;
  }, [rows, companyFilter]);

  // Split rows by scoping tab: "Scoped In" = company_id is in scopedInDealIds,
  // "Scoped Out" = not present or scoping_for_audit was not true. Default is Scoped Out.
  const scopingFilteredRows = useMemo(() => {
    if (scopingTab === 'scoped-in') {
      return companyFilteredRows.filter((c) => scopedInDealIds.has(c.company_id ?? ''));
    }
    return companyFilteredRows.filter((c) => !scopedInDealIds.has(c.company_id ?? ''));
  }, [companyFilteredRows, scopingTab, scopedInDealIds]);

  // Counts for the tab badges
  const scopedInCount = useMemo(
    () => new Set(companyFilteredRows.filter((c) => scopedInDealIds.has(c.company_id ?? '')).map((c) => c.company_id)).size,
    [companyFilteredRows, scopedInDealIds],
  );
  const scopedOutCount = useMemo(
    () => new Set(companyFilteredRows.filter((c) => !scopedInDealIds.has(c.company_id ?? '')).map((c) => c.company_id)).size,
    [companyFilteredRows, scopedInDealIds],
  );

  const searchFilteredRows = useMemo(() => {
    if (!searchTerm.trim()) return scopingFilteredRows;
    const q = searchTerm.toLowerCase();
    return scopingFilteredRows.filter((c) => {
      const hay = [
        c.name,
        c.entity_name,
        c.company_id,
        c.fund,
        c.investment_lead,
        c.contact_name,
        c.contact_email_id,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return hay.includes(q);
    });
  }, [scopingFilteredRows, searchTerm]);

  const displayRows = useMemo(
    () => applyColumnFilters(searchFilteredRows, columnFilters, visibleFilterColumns, getCycleLabel),
    [searchFilteredRows, columnFilters, visibleFilterColumns, getCycleLabel],
  );

  // Distinct companies in view (a company can appear in multiple cycles).
  const companyCount = useMemo(
    () => new Set(displayRows.map((c) => c.company_id)).size,
    [displayRows],
  );

  // ---- Client-side pagination over the filtered rows ----
  const totalFiltered = displayRows.length;
  const totalPages = Math.max(1, Math.ceil(totalFiltered / pageSize));
  // Render with a clamped page so a shrinking result set never shows a blank page.
  const safePage = Math.min(Math.max(1, currentPage), totalPages);
  useEffect(() => {
    if (currentPage !== safePage) setCurrentPage(safePage);
  }, [currentPage, safePage]);
  // Any change to scope, search, column filters, page size, or scoping tab returns to page 1.
  useEffect(() => {
    setCurrentPage(1);
  }, [cacheKey, searchTerm, columnFilters, pageSize, scopingTab]);
  const pageStart = totalFiltered === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const pageEnd = Math.min(safePage * pageSize, totalFiltered);
  const isPaginated = totalFiltered > pageSize;
  const pagedRows = useMemo(
    () => displayRows.slice((safePage - 1) * pageSize, safePage * pageSize),
    [displayRows, safePage, pageSize],
  );

  // Selectable entity IDs = only non-placeholder rows (those with a real entity_id) in the current view.
  const selectableEntityIds = useMemo(
    () => displayRows.filter((r) => r.entity_id != null && r.has_entities).map((r) => r.entity_id as number),
    [displayRows],
  );
  const allFilteredSelected =
    selectableEntityIds.length > 0 && selectableEntityIds.every((id) => selectedEntityIds.has(id));
  const someFilteredSelected = selectableEntityIds.some((id) => selectedEntityIds.has(id));

  const toggleSelectAll = () => {
    setSelectedEntityIds((prev) => {
      const next = new Set(prev);
      if (allFilteredSelected) {
        selectableEntityIds.forEach((id) => next.delete(id));
      } else {
        selectableEntityIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  // Drop selections that are no longer visible under current filters.
  useEffect(() => {
    setSelectedEntityIds((prev) => {
      if (prev.size === 0) return prev;
      const visible = new Set(selectableEntityIds);
      const next = new Set<number>();
      prev.forEach((id) => { if (visible.has(id)) next.add(id); });
      return next.size === prev.size ? prev : next;
    });
  }, [selectableEntityIds]);

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

  useEffect(() => {
    setColumnFilters({});
    setOpenFilterId(null);
  }, [showAllCyclesDefault]);

  const handleDashboardDownload = async (scopedIn: boolean) => {
    if (!selectedCycleId) {
      toast.error('Select a review cycle before downloading');
      return;
    }
    setDashboardDownloading(true);
    try {
      await downloadDashboardData(selectedCycleId, scopedIn);
      toast.success('Dashboard data downloaded');
    } catch {
      toast.error('Failed to download dashboard data');
    } finally {
      setDashboardDownloading(false);
    }
  };

  // Called when the user picks Scoped In / Scoped Out in the pending-action modal.
  const handleScopingActionConfirm = async (scopedIn: boolean) => {
    const action = pendingAction;
    setPendingAction(null);
    if (action === 'download') {
      await handleDashboardDownload(scopedIn);
    } else if (action === 'upload') {
      dashboardUploadRef.current?.click();
      // Store choice for use in the file-change handler.
      pendingScopedInRef.current = scopedIn;
    }
  };

  const handleDashboardUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!selectedCycleId) {
      toast.error('Select a review cycle before uploading');
      return;
    }
    const scopeLabel = pendingScopedInRef.current ? 'Scoped In' : 'Scoped Out';
    setDashboardUploading(true);
    setUploadResult(null);
    try {
      const result = await uploadDashboardData(file, selectedCycleId);
      setUploadResult(result);
      if (result.error_count > 0) {
        toast.error(`Upload completed with ${result.error_count} error(s) — see details below`);
      } else {
        toast.success(`[${scopeLabel}] Updated ${result.rows_updated} row(s), skipped ${result.rows_skipped} unchanged`);
        cacheRef.current.clear();
        setReloadNonce((n) => n + 1);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setDashboardUploading(false);
    }
  };

  const confirmBulkStatusUpdate = async () => {
    if (selectedEntityIds.size === 0 || !bulkStatus) return;
    setBulkSaving(true);
    try {
      await Promise.all(
        [...selectedEntityIds].map((id) => patchEntity(id, { status: bulkStatus as ReviewStage })),
      );
      cacheRef.current.clear();
      setReloadNonce((n) => n + 1);
      toast.success(`Status updated for ${selectedEntityIds.size} entit${selectedEntityIds.size === 1 ? 'y' : 'ies'}`);
      setBulkConfirmOpen(false);
      clearBulkSelection();
    } catch {
      toast.error('Failed to update one or more statuses');
    } finally {
      setBulkSaving(false);
    }
  };

  return (
    <div className="h-full overflow-auto bg-gray-50 p-6">
      <h1 className="text-2xl font-bold text-gray-900 mb-6">Audit Tracker</h1>
      {rcDataLoading && (
        <p className="text-sm text-gray-500 mb-4">Loading review cycles and company data…</p>
      )}
      {entriesError && (
        <p className="text-sm text-red-600 mb-4">{entriesError}</p>
      )}

      <div className="flex gap-2 mb-4 flex-wrap">
        {([
          { key: 'all', label: 'All Companies' },
          { key: 'overdue', label: 'Overdue' },
          { key: 'in-review', label: 'In Review' },
          { key: 'completed', label: 'Completed' },
          { key: 'no-org-chart', label: 'No Org Chart' },
        ] as { key: CompanyFilter; label: string }[]).map((f) => (
          <button
            key={f.key}
            onClick={() => {
              setCompanyFilter(f.key);
              if (f.key === 'overdue' || f.key === 'in-review') {
                setSelectedCycleId('');
              } else if (!selectedCycleId) {
                setSelectedCycleId(pickDefaultCycle(rcCycles) ?? '');
              }
            }}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
              companyFilter === f.key
                ? f.key === 'no-org-chart'
                  ? 'bg-amber-500 text-white'
                  : 'bg-blue-500 text-white'
                : 'border border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-3 mb-4">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
          <input
            type="text"
            placeholder="Search name, fund, lead…"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-9 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          />
        </div>
        <select
          value={selectedCycleId}
          onChange={(e) => setSelectedCycleId(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
        >
          {showAllCyclesDefault && <option value="">All Cycles</option>}
          {rcCycles.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => setShowExportDialog(true)}
          className="px-4 py-2 rounded-lg font-medium transition-all border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 text-sm flex items-center gap-1.5"
          title="Download audit report XLSX"
        >
          <FileSpreadsheet className="h-4 w-4" /> Download Report
        </button>
        <button
          type="button"
          onClick={() => {
            if (!selectedCycleId) { toast.error('Select a review cycle before downloading'); return; }
            setPendingAction('download');
          }}
          disabled={dashboardDownloading || !selectedCycleId}
          className="px-4 py-2 rounded-lg font-medium transition-all border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 text-sm flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
          title={selectedCycleId ? 'Download dashboard table data as XLSX' : 'Select a review cycle first'}
        >
          <FileSpreadsheet className="h-4 w-4" />
          {dashboardDownloading ? 'Downloading…' : 'Download Dashboard Data'}
        </button>
        <button
          type="button"
          onClick={() => {
            if (!selectedCycleId) { toast.error('Select a review cycle before uploading'); return; }
            setPendingAction('upload');
          }}
          disabled={dashboardUploading || !selectedCycleId}
          className="px-4 py-2 rounded-lg font-medium transition-all border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 text-sm flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
          title={selectedCycleId ? 'Upload edited dashboard XLSX to update records' : 'Select a review cycle first'}
        >
          <Upload className="h-4 w-4" />
          {dashboardUploading ? 'Uploading…' : 'Upload Dashboard Data'}
        </button>
        <input
          ref={dashboardUploadRef}
          type="file"
          accept=".xlsx"
          className="hidden"
          onChange={handleDashboardUpload}
        />
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

      {/* Scoped In / Scoped Out tabs — shown when a cycle is selected */}
      {selectedCycleId && (
        <div className="flex items-center gap-1 mb-4 border-b border-gray-200">
          {([
            { key: 'scoped-in' as ScopingTab, label: 'Scoped In for Review', count: scopedInCount },
            { key: 'scoped-out' as ScopingTab, label: 'Scoped Out for Review', count: scopedOutCount },
          ]).map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setScopingTab(tab.key)}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${
                scopingTab === tab.key
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              {tab.label}
              <span
                className={`ml-2 inline-flex items-center justify-center rounded-full px-2 py-0.5 text-xs font-semibold ${
                  scopingTab === tab.key
                    ? 'bg-blue-100 text-blue-700'
                    : 'bg-gray-100 text-gray-500'
                }`}
              >
                {scopingLoading ? '…' : tab.count}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Bulk edit toolbar — shown when at least one entity is selected */}
      {selectedEntityIds.size > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3">
          <span className="text-sm font-semibold text-blue-800">
            {selectedEntityIds.size} selected
          </span>
          <div className="h-5 w-px bg-blue-200" />
          <label className="text-sm font-medium text-gray-700">Set Status to</label>
          <select
            value={bulkStatus}
            onChange={(e) => setBulkStatus(e.target.value)}
            className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white min-w-[200px]"
          >
            <option value="">— pick a status —</option>
            {STAGES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <button
            type="button"
            disabled={!bulkStatus}
            onClick={() => setBulkConfirmOpen(true)}
            className="px-4 py-1.5 rounded-lg border border-blue-600 bg-blue-600 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Apply
          </button>
          <button
            type="button"
            onClick={clearBulkSelection}
            className="px-3 py-1.5 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Clear
          </button>
        </div>
      )}

      <div className="w-full max-w-full">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center rounded-full bg-blue-100 px-3 py-1 text-sm font-semibold text-blue-700">
              {companyCount} {companyCount === 1 ? 'company' : 'companies'}
            </span>
            <span className="inline-flex items-center rounded-full bg-blue-100 px-3 py-1 text-sm font-semibold text-blue-700">
              {isPaginated
                ? `Showing ${pageStart}–${pageEnd} of ${totalFiltered} entities`
                : `${totalFiltered} ${totalFiltered === 1 ? 'entity' : 'entities'}`}
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
        <div className="overflow-x-auto overflow-y-visible rounded-lg border border-gray-200 bg-white shadow-sm [scrollbar-gutter:stable]">
          <table className="w-max min-w-full border-separate border-spacing-0">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                {/* Select-all checkbox */}
                <th className="px-2 py-2.5 border-b border-gray-200 border-r border-gray-200 w-8">
                  <input
                    type="checkbox"
                    aria-label="Select all visible entities"
                    title="Select all visible entities"
                    className="h-3.5 w-3.5 cursor-pointer rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    checked={allFilteredSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = !allFilteredSelected && someFilteredSelected;
                    }}
                    onChange={toggleSelectAll}
                  />
                </th>
                {visibleFilterColumns.map((col, idx) => {
                  const sticky = idx === 0;
                  return (
                    <th
                      key={col.id}
                      className={[
                        'text-xs font-medium text-gray-500 uppercase tracking-wider py-2.5 align-middle whitespace-nowrap border-b border-gray-200',
                        sticky ? `px-3 text-left ${stickyCompanyTh}` : 'px-2 text-left border-r border-gray-200',
                      ].join(' ')}
                    >
                      <div className="flex items-center justify-between gap-1.5">
                        <span className="truncate">{COLUMN_LABELS[col.id] ?? col.id}</span>
                        <AuditTrackerFilterCell
                          col={col}
                          baseRows={searchFilteredRows}
                          filter={columnFilters[col.id]}
                          onChange={(next) => setColumnFilter(col.id, next)}
                          isOpen={openFilterId === col.id}
                          onToggle={() => setOpenFilterId((x) => (x === col.id ? null : col.id))}
                          getCycleLabel={getCycleLabel}
                          className="shrink-0"
                        />
                      </div>
                    </th>
                  );
                })}
                <th className="px-2 py-2 w-8" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {entriesLoading ? (
                <tr>
                  <td
                    colSpan={visibleFilterColumns.length + 1}
                    className="px-4 py-12 text-center text-sm text-gray-400"
                  >
                    Loading…
                  </td>
                </tr>
              ) : displayRows.length === 0 && searchFilteredRows.length > 0 ? (
                <tr>
                  <td
                    colSpan={visibleFilterColumns.length + 1}
                    className="px-4 py-12 text-center text-sm text-amber-700 bg-amber-50/50"
                  >
                    No rows match the current column filters. Clear filters or widen your criteria.
                  </td>
                </tr>
              ) : displayRows.length === 0 ? (
                <tr>
                  <td
                    colSpan={visibleFilterColumns.length + 1}
                    className="px-4 py-12 text-center text-sm text-gray-400"
                  >
                    No companies found. Upload a CSV to add companies to this review cycle.
                  </td>
                </tr>
              ) : (
                pagedRows.map((c) => {
                  const isPlaceholder = !c.has_entities;
                  const rawStatus = ((c as unknown as CycleEntityRow).entity_status as string) || 'Not applicable';
                  // Resolve legacy casing variants (e.g. "In Review" → "In review") against
                  // the canonical STAGES list before building options, so no duplicate appears.
                  const status: ReviewStage =
                    (STAGES.find((s) => s.toLowerCase() === rawStatus.toLowerCase()) ?? rawStatus) as ReviewStage;
                  // Always offer the current status even if it's a non-lifecycle value
                  // (e.g. Scoped Out / Archive Entity), so the select can show it.
                  const statusOptions = STAGES.includes(status) ? STAGES : [status, ...STAGES];
                  return (
                    <tr
                      key={`${c.company_id}-${c.entity_id ?? 'placeholder'}`}
                      className={`group transition-colors ${
                        !isPlaceholder && c.entity_id != null && selectedEntityIds.has(c.entity_id)
                          ? 'bg-blue-50/60 hover:bg-blue-50'
                          : isPlaceholder
                          ? 'bg-amber-50 hover:bg-amber-100'
                          : 'hover:bg-gray-50'
                      }`}
                    >
                      {/* Row select checkbox — only for real entities */}
                      <td className="px-2 py-2 align-top border-r border-gray-100 w-8" onClick={(e) => e.stopPropagation()}>
                        {!isPlaceholder && c.entity_id != null && (
                          <input
                            type="checkbox"
                            aria-label={`Select ${c.entity_name ?? c.name}`}
                            className="h-3.5 w-3.5 cursor-pointer rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                            checked={selectedEntityIds.has(c.entity_id)}
                            onChange={() => toggleRowSelected(c.entity_id!)}
                          />
                        )}
                      </td>
                      {/* Deal ID */}
                      <td className={`${stickyCompanyTd} px-3 py-2 text-xs font-mono ${isPlaceholder ? 'bg-amber-50 group-hover:bg-amber-100' : 'bg-white group-hover:bg-gray-50'} text-gray-700`}>
                        {c.company_id || <span className="text-gray-300">—</span>}
                      </td>
                      {/* Deal Name */}
                      <td className={`${scrollCell} font-medium text-gray-900`}>
                        <div className="flex items-start gap-1.5">
                          <Building2 className="h-3.5 w-3.5 text-gray-500 shrink-0 mt-0.5" />
                          {isPlaceholder ? (
                            <button
                              onClick={() => navigate(`/company/${c.id}`, { state: { hasEntities: false } })}
                              className="break-words text-left text-blue-600 underline underline-offset-2 hover:text-blue-800 cursor-pointer"
                              title="Click to open company view and upload org chart / create entities"
                            >
                              {c.name}
                            </button>
                          ) : (
                            <span className="break-words">{c.name}</span>
                          )}
                        </div>
                      </td>
                      {/* Entity Name */}
                      <td className={`${scrollCell} font-medium text-gray-800`}>
                        {c.entity_name || <span className="text-gray-300">—</span>}
                      </td>
                      {/* Geo (Entity Level) */}
                      {renderEditableCell(c, 'entity_geolocation', c.entity_geolocation ?? '—')}
                      {/* Holding / Subsidiary */}
                      {renderEditableCell(c, 'entity_type', c.entity_type ?? '—')}
                      {/* Status — entity_status dropdown */}
                      <td className={`${scrollCell} min-w-[10rem]`} onClick={(e) => e.stopPropagation()}>
                        {isPlaceholder ? (
                          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800">
                            {PLACEHOLDER_STATUS}
                          </span>
                        ) : (
                          <select
                            value={status}
                            onChange={(e) => {
                              const newStatus = e.target.value as ReviewStage;
                              if (newStatus !== status)
                                setConfirmDialog({ entityId: c.entity_id!, entityName: c.entity_name!, newStatus, companyId: c.company_id ?? '', step: 1, wantsMcp: false, mcpLoading: false, mcpRows: [], mcpDrafts: {}, mcpChecked: new Set(), bulkStage1: '', bulkStage2: '', saving: false });
                            }}
                            className="w-full border border-gray-300 rounded px-2 py-1 text-xs bg-white text-gray-800 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500"
                          >
                            {statusOptions.map((s) => (
                              <option key={s} value={s}>{s}</option>
                            ))}
                          </select>
                        )}
                      </td>
                      {/* Comments */}
                      {renderEditableCell(c, 'entity_comments', c.entity_comments ?? '—')}
                      {/* One Desk Email Status */}
                      {renderEditableCell(c, 'entity_one_desk_email_status', c.entity_one_desk_email_status ?? '—')}
                      {/* Review cycle (only when "All Cycles" view) */}
                      {showAllCyclesDefault && (
                        <td className={scrollCell}>{c.review_cycle_id ? getCycleLabel(c.review_cycle_id) : <span className="text-gray-300">—</span>}</td>
                      )}
                      <td className="px-2 py-2 align-top w-8">
                        {isPlaceholder && (
                          <button
                            onClick={() => navigate(`/company/${c.id}`, { state: { hasEntities: false } })}
                            title="No org chart uploaded. Click to open company view and upload org chart / create entities."
                            className="text-amber-500 hover:text-amber-700 transition-colors"
                          >
                            <AlertCircle className="h-4 w-4" />
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

      </div>

      {/* Bulk status confirm dialog */}
      {bulkConfirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-semibold text-gray-900">Confirm bulk status change</h2>
            <p className="mt-1 text-sm text-gray-500">
              Set <span className="font-medium text-gray-700">Status</span> to{' '}
              <span className="font-medium text-gray-700">{bulkStatus || '—'}</span> for{' '}
              <span className="font-medium text-gray-700">{selectedEntityIds.size}</span>{' '}
              selected {selectedEntityIds.size === 1 ? 'entity' : 'entities'}?
            </p>
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
                onClick={() => void confirmBulkStatusUpdate()}
                disabled={bulkSaving}
                className="px-4 py-2 rounded-lg border border-blue-600 bg-blue-600 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {bulkSaving ? 'Saving…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {confirmDialog && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
          onClick={() => { if (!confirmDialog.saving) setConfirmDialog(null); }}
        >
          <div
            className={`bg-white rounded-xl shadow-2xl flex flex-col ${confirmDialog.step === 2 ? 'w-full max-w-3xl max-h-[90vh]' : 'w-full max-w-sm'}`}
            onClick={(e) => e.stopPropagation()}
          >
            {/* ── Step 1: simple confirm ── */}
            {confirmDialog.step === 1 && (
              <div className="p-6">
                <h3 className="text-sm font-semibold text-gray-900 mb-2">Confirm Status Change</h3>
                <p className="text-sm text-gray-500 mb-4">
                  Change status of{' '}
                  <span className="font-medium text-gray-900">{confirmDialog.entityName}</span> to{' '}
                  <span className="font-medium text-gray-900">{confirmDialog.newStatus}</span>?
                </p>
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => setConfirmDialog(null)}
                    className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
                  >
                    No
                  </button>
                  <button
                    onClick={async () => {
                      const { entityId, newStatus, companyId } = confirmDialog;
                      const isTerminal = TERMINAL_STATUSES.has(newStatus) && !!selectedCycleId;

                      if (!isTerminal) {
                        // Non-terminal: save immediately and close.
                        setConfirmDialog((p) => p ? { ...p, saving: true } : null);
                        try {
                          await patchEntity(entityId, { status: newStatus });
                          cacheRef.current.clear();
                          setReloadNonce((n) => n + 1);
                          toast.success('Status updated');
                          setConfirmDialog(null);
                        } catch {
                          toast.error('Failed to update status');
                          setConfirmDialog((p) => p ? { ...p, saving: false } : null);
                        }
                        return;
                      }

                      // Terminal: load MCP rows and advance to step 2.
                      setConfirmDialog((p) => p ? { ...p, mcpLoading: true, step: 2 } : null);
                      try {
                        const { data } = await apiClient.get<PortfolioCompanyMetadataRow[]>(
                          '/api/v1/master-scoping',
                          { params: { review_cycle_id: selectedCycleId } },
                        );
                        const filtered = data.filter((r) => r.deal_id === companyId);
                        const drafts: Record<number, { stage1: string; stage2: string }> = {};
                        const checked = new Set<number>();
                        for (const r of filtered) {
                          drafts[r.id] = { stage1: r.deal_level_stage_1 ?? '', stage2: r.deal_level_stage_2 ?? '' };
                          checked.add(r.id);
                        }
                        setConfirmDialog((p) => p ? { ...p, mcpLoading: false, mcpRows: filtered, mcpDrafts: drafts, mcpChecked: checked } : null);
                      } catch {
                        toast.error('Failed to load master scoping records');
                        setConfirmDialog((p) => p ? { ...p, mcpLoading: false, step: 1 } : null);
                      }
                    }}
                    disabled={confirmDialog.saving}
                    className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50"
                  >
                    {confirmDialog.saving ? 'Saving…' : 'Yes'}
                  </button>
                </div>
              </div>
            )}

            {/* ── Step 2: status summary + optional MCP panel ── */}
            {confirmDialog.step === 2 && (
              <>
                {/* Header */}
                <div className="px-6 pt-6 pb-4 border-b border-gray-100 shrink-0">
                  <h3 className="text-base font-semibold text-gray-900">Confirm Status Change</h3>
                  <p className="text-sm text-gray-500 mt-0.5">
                    Setting <span className="font-medium text-gray-800">{confirmDialog.entityName}</span> →{' '}
                    <span className="font-medium text-gray-800">{confirmDialog.newStatus}</span>
                  </p>
                  {/* MCP opt-in checkbox */}
                  <label className="mt-4 flex items-center gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={confirmDialog.wantsMcp}
                      onChange={(e) => setConfirmDialog((p) => p ? { ...p, wantsMcp: e.target.checked } : null)}
                      className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                    <span className="text-sm text-gray-700">Also update Deal Stage 1 / 2 in Master Scoping</span>
                  </label>
                </div>

                {/* MCP panel — only when checkbox is ticked */}
                {confirmDialog.wantsMcp && (
                  <>
                    {/* Bulk-apply bar — active only when all rows are selected */}
                    {(() => {
                      const allSelected = confirmDialog.mcpRows.length > 0 && confirmDialog.mcpChecked.size === confirmDialog.mcpRows.length;
                      return (
                        <div className={`px-6 py-3 border-b border-gray-100 flex flex-wrap items-center gap-3 shrink-0 transition-colors ${allSelected ? 'bg-blue-50' : 'bg-gray-50 opacity-40 pointer-events-none'}`}>
                          <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">Apply to all:</span>
                          <div className="flex items-center gap-2">
                            <label className="text-xs text-gray-600">Stage 1</label>
                            <select
                              value={confirmDialog.bulkStage1}
                              onChange={(e) => setConfirmDialog((p) => p ? { ...p, bulkStage1: e.target.value } : null)}
                              className="text-xs border border-gray-300 rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                            >
                              <option value="">— pick —</option>
                              {DEAL_STAGE_1_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                            </select>
                          </div>
                          <div className="flex items-center gap-2">
                            <label className="text-xs text-gray-600">Stage 2</label>
                            <select
                              value={confirmDialog.bulkStage2}
                              onChange={(e) => setConfirmDialog((p) => p ? { ...p, bulkStage2: e.target.value } : null)}
                              className="text-xs border border-gray-300 rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                            >
                              <option value="">— pick —</option>
                              {DEAL_STAGE_2_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                            </select>
                          </div>
                          <button
                            type="button"
                            disabled={!confirmDialog.bulkStage1 && !confirmDialog.bulkStage2}
                            onClick={() => setConfirmDialog((p) => {
                              if (!p) return null;
                              const next = { ...p.mcpDrafts };
                              for (const id of p.mcpChecked) {
                                const s1 = p.bulkStage1 || next[id]?.stage1 || '';
                                const s2Eligible = STAGE_2_ENABLED_STAGE_1.has(s1);
                                next[id] = {
                                  stage1: p.bulkStage1 || next[id]?.stage1 || '',
                                  stage2: p.bulkStage2 && s2Eligible ? p.bulkStage2 : (s2Eligible ? next[id]?.stage2 || '' : ''),
                                };
                              }
                              return { ...p, mcpDrafts: next };
                            })}
                            className="text-xs px-3 py-1.5 rounded border border-blue-400 bg-white text-blue-700 hover:bg-blue-100 disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            Apply
                          </button>
                        </div>
                      );
                    })()}

                    {/* Row list */}
                    <div className="flex-1 overflow-y-auto px-6 py-4">
                      {confirmDialog.mcpLoading ? (
                        <p className="text-sm text-gray-400 text-center py-8">Loading records…</p>
                      ) : confirmDialog.mcpRows.length === 0 ? (
                        <p className="text-sm text-gray-400 text-center py-8">No master scoping records found for this deal in the selected cycle.</p>
                      ) : (
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b border-gray-200">
                              <th className="pb-2 pr-3 text-left">
                                <input
                                  type="checkbox"
                                  checked={confirmDialog.mcpChecked.size === confirmDialog.mcpRows.length && confirmDialog.mcpRows.length > 0}
                                  onChange={(e) => setConfirmDialog((p) => {
                                    if (!p) return null;
                                    return { ...p, mcpChecked: e.target.checked ? new Set(p.mcpRows.map((r) => r.id)) : new Set(), bulkStage1: '', bulkStage2: '' };
                                  })}
                                  className="rounded border-gray-300"
                                />
                              </th>
                              <th className="pb-2 pr-3 text-left font-medium text-gray-500 uppercase tracking-wide">Deal Name</th>
                              <th className="pb-2 pr-3 text-left font-medium text-gray-500 uppercase tracking-wide">Fund</th>
                              <th className="pb-2 pr-3 text-left font-medium text-gray-500 uppercase tracking-wide">Strategy</th>
                              <th className="pb-2 pr-3 text-left font-medium text-gray-500 uppercase tracking-wide">Deal Stage 1</th>
                              <th className="pb-2 text-left font-medium text-gray-500 uppercase tracking-wide">Deal Stage 2</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-gray-100">
                            {confirmDialog.mcpRows.map((row) => {
                              const allSelected = confirmDialog.mcpChecked.size === confirmDialog.mcpRows.length && confirmDialog.mcpRows.length > 0;
                              const checked = confirmDialog.mcpChecked.has(row.id);
                              const draft = confirmDialog.mcpDrafts[row.id] ?? { stage1: '', stage2: '' };
                              const stage2Locked = !STAGE_2_ENABLED_STAGE_1.has(draft.stage1);
                              return (
                                <tr key={row.id} className={allSelected ? 'opacity-50' : checked ? 'bg-blue-50/40' : ''}>
                                  <td className="py-2 pr-3">
                                    <input
                                      type="checkbox"
                                      checked={checked}
                                      disabled={allSelected}
                                      onChange={(e) => setConfirmDialog((p) => {
                                        if (!p) return null;
                                        const next = new Set(p.mcpChecked);
                                        if (e.target.checked) next.add(row.id); else next.delete(row.id);
                                        return { ...p, mcpChecked: next };
                                      })}
                                      className="rounded border-gray-300 disabled:cursor-not-allowed"
                                    />
                                  </td>
                                  <td className="py-2 pr-3 font-medium text-gray-800">{row.deal_name || row.deal_id}</td>
                                  <td className="py-2 pr-3 text-gray-600">{row.fund || '—'}</td>
                                  <td className="py-2 pr-3 text-gray-600">{row.strategy || '—'}</td>
                                  <td className="py-2 pr-3">
                                    <select
                                      value={draft.stage1}
                                      disabled={!checked || allSelected}
                                      onChange={(e) => setConfirmDialog((p) => {
                                        if (!p) return null;
                                        const s1 = e.target.value;
                                        return { ...p, mcpDrafts: { ...p.mcpDrafts, [row.id]: { stage1: s1, stage2: STAGE_2_ENABLED_STAGE_1.has(s1) ? p.mcpDrafts[row.id]?.stage2 ?? '' : '' } } };
                                      })}
                                      className="w-full border border-gray-300 rounded px-1.5 py-1 text-xs bg-white focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100 disabled:text-gray-400 disabled:cursor-not-allowed"
                                    >
                                      <option value="">—</option>
                                      {DEAL_STAGE_1_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                                    </select>
                                  </td>
                                  <td className="py-2">
                                    <select
                                      value={draft.stage2}
                                      disabled={!checked || stage2Locked || allSelected}
                                      title={stage2Locked ? 'Set Stage 1 to a completed status first' : undefined}
                                      onChange={(e) => setConfirmDialog((p) => {
                                        if (!p) return null;
                                        return { ...p, mcpDrafts: { ...p.mcpDrafts, [row.id]: { ...p.mcpDrafts[row.id], stage2: e.target.value } } };
                                      })}
                                      className="w-full border border-gray-300 rounded px-1.5 py-1 text-xs bg-white focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100 disabled:text-gray-400 disabled:cursor-not-allowed"
                                    >
                                      <option value="">—</option>
                                      {DEAL_STAGE_2_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
                                    </select>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      )}
                    </div>
                  </>
                )}

                {/* Footer */}
                <div className="px-6 py-4 border-t border-gray-100 flex justify-between items-center shrink-0">
                  <span className="text-xs text-gray-400">
                    {confirmDialog.wantsMcp ? `${confirmDialog.mcpChecked.size} of ${confirmDialog.mcpRows.length} MCP records selected` : ''}
                  </span>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => setConfirmDialog(null)}
                      disabled={confirmDialog.saving}
                      className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      disabled={confirmDialog.saving || confirmDialog.mcpLoading}
                      onClick={async () => {
                        const { entityId, newStatus, wantsMcp, mcpChecked, mcpDrafts } = confirmDialog;
                        setConfirmDialog((p) => p ? { ...p, saving: true } : null);
                        try {
                          // Save entity status + MCP stages in parallel.
                          await Promise.all([
                            patchEntity(entityId, { status: newStatus }),
                            ...(wantsMcp
                              ? [...mcpChecked].map((id) => {
                                  const d = mcpDrafts[id] ?? { stage1: '', stage2: '' };
                                  return apiClient.patch(`/api/v1/master-scoping/${id}`, {
                                    deal_level_stage_1: d.stage1 || null,
                                    deal_level_stage_2: d.stage2 || null,
                                  });
                                })
                              : []),
                          ]);
                          cacheRef.current.clear();
                          setReloadNonce((n) => n + 1);
                          toast.success(
                            wantsMcp && mcpChecked.size > 0
                              ? `Status updated · Deal stages updated for ${mcpChecked.size} record(s)`
                              : 'Status updated',
                          );
                          setConfirmDialog(null);
                        } catch {
                          toast.error('Failed to save changes');
                          setConfirmDialog((p) => p ? { ...p, saving: false } : null);
                        }
                      }}
                      className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {confirmDialog.saving ? 'Saving…' : 'Confirm'}
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {editReasonDialog && (
        <div
          className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
          onClick={() => setEditReasonDialog(null)}
        >
          <div className="bg-white rounded-lg p-6 w-full max-w-sm shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-gray-900 mb-2">Confirm Edit</h3>
            <p className="text-sm font-medium text-gray-900 mb-1">{editReasonDialog.fieldLabel}</p>
            <p className="text-xs font-mono text-gray-500 mb-4">
              {editReasonDialog.oldValue} → {editReasonDialog.newValue || '(empty)'}
            </p>
            <label className="block text-xs font-medium text-gray-700 mb-1">
              Reason <span className="text-red-500">*</span>
            </label>
            <textarea
              autoFocus
              rows={3}
              placeholder="Why are you making this change?"
              value={editReasonDialog.reason}
              onChange={(e) => setEditReasonDialog((p) => (p ? { ...p, reason: e.target.value } : null))}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 mb-4 resize-none"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setEditReasonDialog(null)}
                className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                disabled={!editReasonDialog.reason.trim()}
                onClick={handleConfirmEdit}
                className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}

      {showExportDialog && (
        <AuditReportExportDialog
          onClose={() => setShowExportDialog(false)}
          reviewCycles={rcCycles}
          initialCycleId={selectedCycleId || undefined}
        />
      )}

      {uploadResult && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setUploadResult(null)}>
          <div className="bg-white rounded-lg p-6 w-full max-w-lg shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-gray-900 mb-3">Upload Result</h3>
            <div className="grid grid-cols-3 gap-3 mb-4">
              <div className="rounded-lg border border-gray-200 p-3 text-center">
                <div className="text-2xl font-bold text-gray-900">{uploadResult.rows_processed}</div>
                <div className="text-xs text-gray-500 mt-0.5">Processed</div>
              </div>
              <div className="rounded-lg border border-green-200 bg-green-50 p-3 text-center">
                <div className="text-2xl font-bold text-green-700">{uploadResult.rows_updated}</div>
                <div className="text-xs text-green-600 mt-0.5">Updated</div>
              </div>
              <div className="rounded-lg border border-gray-200 p-3 text-center">
                <div className="text-2xl font-bold text-gray-500">{uploadResult.rows_skipped}</div>
                <div className="text-xs text-gray-500 mt-0.5">Skipped (no change)</div>
              </div>
            </div>
            {uploadResult.errors.length > 0 && (
              <div className="mb-4">
                <p className="text-xs font-semibold text-red-700 mb-1">{uploadResult.errors.length} error(s):</p>
                <div className="max-h-48 overflow-auto rounded border border-red-100 bg-red-50 divide-y divide-red-100">
                  {uploadResult.errors.map((err, i) => (
                    <div key={i} className="px-3 py-1.5 text-xs text-red-800">
                      <span className="font-medium">Row {err.row}:</span> {err.reason}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="flex justify-end">
              <button
                onClick={() => setUploadResult(null)}
                className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-500 text-white hover:bg-blue-600"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Scoping-choice modal: shown before Download / Upload Dashboard Data */}
      {pendingAction && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
          onClick={() => setPendingAction(null)}
        >
          <div
            className="bg-white rounded-xl p-6 w-full max-w-sm shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-gray-900 mb-1">
              {pendingAction === 'download' ? 'Download Dashboard Data' : 'Upload Dashboard Data'}
            </h3>
            <p className="text-sm text-gray-500 mb-5">
              Which companies would you like to {pendingAction === 'download' ? 'export' : 'update'}?
            </p>
            <div className="grid grid-cols-2 gap-3 mb-4">
              <button
                type="button"
                onClick={() => void handleScopingActionConfirm(true)}
                className="flex flex-col items-center gap-1.5 px-4 py-4 rounded-lg border-2 border-blue-500 bg-blue-50 text-blue-700 hover:bg-blue-100 transition-colors"
              >
                <span className="text-sm font-semibold">Scoped In</span>
                <span className="text-xs text-blue-500">Companies included in review</span>
              </button>
              <button
                type="button"
                onClick={() => void handleScopingActionConfirm(false)}
                className="flex flex-col items-center gap-1.5 px-4 py-4 rounded-lg border-2 border-gray-300 bg-gray-50 text-gray-700 hover:bg-gray-100 transition-colors"
              >
                <span className="text-sm font-semibold">Scoped Out</span>
                <span className="text-xs text-gray-500">Companies excluded from review</span>
              </button>
            </div>
            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => setPendingAction(null)}
                className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default AuditTrackerPage;


