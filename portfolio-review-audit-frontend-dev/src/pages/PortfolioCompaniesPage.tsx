import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, Building2, ChevronDown, ChevronRight, X } from 'lucide-react';

import { ColFilter } from '@/components/common/ColumnFilter';
import { listInReviewTracker } from '@/api/portfolio';
import { fetchReviewCycles } from '@/api/reviewCycleAdjustments';
import { EntityStatusConfirmDialog } from '@/components/company/EntityStatusConfirmDialog';
import { applyEntityStatusChange, type EntityStatusConfirmState } from '@/components/company/entityStatusChange';
import {
  COMPANY_REVIEW_STAGE_OPTIONS,
  normalizeReviewStage,
  reviewStageBadgeClass,
  reviewStageDisplayLabel,
  reviewStageDotClass,
  type CompanyReviewStage,
} from '@/constants/auditStatus';
import { useAsyncAction } from '@/hooks/useAsyncAction';
import { toast } from '@/components/ui/sonner';

type TrackerRow = {
  rowKey: string;
  portfolioCompanyId: number;
  companyName: string;
  entityId: number | null;
  entityName: string | null;
  // Entity status is the audit state of record (replaced the company review_stage).
  entityStatus: CompanyReviewStage | null;
  auditPeriod: string;
  contactName?: string;
  hasFilesWithoutEntity: boolean;
  hasPendingOrgChartReconciliation: boolean;
};

function StageStats({ rows }: { rows: TrackerRow[] }) {
  const statuses = useMemo(
    () => rows.map((r) => r.entityStatus).filter((s): s is CompanyReviewStage => s != null),
    [rows],
  );

  const counts = COMPANY_REVIEW_STAGE_OPTIONS.map((stage) => ({
    stage,
    count: statuses.filter((s) => s === stage).length,
  })).filter((x) => x.count > 0);

  if (counts.length === 0) return null;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3 mb-5">
      {counts.map(({ stage, count }) => (
        <div
          key={stage}
          className="bg-white border border-gray-200 rounded-lg p-4 shadow-sm hover:shadow-md transition-shadow flex flex-col justify-between min-h-[6rem]"
        >
          <div className="flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ring-1 ring-gray-200 shrink-0 ${reviewStageDotClass[stage] || 'bg-gray-400'}`} />
            <span className="text-xs text-gray-500 uppercase leading-tight line-clamp-2">{stage}</span>
          </div>
          <div className="text-2xl font-semibold text-gray-900 mt-2">{count}</div>
        </div>
      ))}
    </div>
  );
}

function StageDropdown<T extends string>({
  value,
  options,
  badgeClass,
  dotClass,
  display,
  onChange,
}: {
  value: T;
  options: readonly T[];
  badgeClass: Record<string, string>;
  dotClass: Record<string, string>;
  display: (v: T) => string;
  onChange: (next: T) => void;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuPos, setMenuPos] = useState({ top: 0, left: 0, minWidth: 240 });

  const updateMenuPosition = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const minW = Math.max(240, r.width);
    const estH = options.length * 36 + 16;
    let top = r.bottom + 4;
    if (top + estH > window.innerHeight - 8 && r.top - estH - 4 > 8) {
      top = r.top - estH - 4;
    }
    let left = r.left;
    if (left + minW > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - minW - 8);
    }
    setMenuPos({ top, left, minWidth: minW });
  }, [options.length]);

  useLayoutEffect(() => {
    if (!open) return;
    updateMenuPosition();
    const onScrollOrResize = () => updateMenuPosition();
    window.addEventListener('scroll', onScrollOrResize, true);
    window.addEventListener('resize', onScrollOrResize);
    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true);
      window.removeEventListener('resize', onScrollOrResize);
    };
  }, [open, updateMenuPosition]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t)) return;
      if (menuRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const menu =
    open &&
    createPortal(
      <div
        ref={menuRef}
        className="fixed z-[300] bg-white border border-gray-200 rounded-lg shadow-lg py-1 max-h-72 overflow-y-auto"
        style={{ top: menuPos.top, left: menuPos.left, minWidth: menuPos.minWidth }}
        onClick={(e) => e.stopPropagation()}
      >
        {options.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => {
              onChange(s);
              setOpen(false);
            }}
            className={`w-full text-left px-3 py-2 text-xs hover:bg-gray-50 flex items-center gap-2 ${s === value ? 'font-semibold' : ''}`}
          >
            <span className={`w-2 h-2 rounded-full shrink-0 ${dotClass[s] || 'bg-gray-400'}`} />
            {display(s)}
          </button>
        ))}
      </div>,
      document.body,
    );

  return (
    <>
      <div className="relative" ref={triggerRef} onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium cursor-pointer max-w-[14rem] ${
            badgeClass[value] || 'bg-gray-100 text-gray-800'
          }`}
        >
          <span className="truncate">{display(value)}</span>
          <ChevronDown className="h-3 w-3 shrink-0" />
        </button>
      </div>
      {menu}
    </>
  );
}

export default function PortfolioCompaniesPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<TrackerRow[]>([]);
  const [rcCycles, setRcCycles] = useState<{ id: string; label: string; createdAt: string }[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [pendingEntityStatus, setPendingEntityStatus] = useState<EntityStatusConfirmState | null>(null);
  const [selectedCycleId, setSelectedCycleId] = useState('__all__');

  // Per-column filters (client-side)
  const [colFilterCompany, setColFilterCompany] = useState('');
  const [colFilterEntity, setColFilterEntity] = useState('');
  const [colFilterEntityStatuses, setColFilterEntityStatuses] = useState<Set<string>>(new Set());
  const [colFilterPeriods, setColFilterPeriods] = useState<Set<string>>(new Set());
  const [colFilterContact, setColFilterContact] = useState('');

  const loadTracker = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const page = await listInReviewTracker({
        limit: 500,
        offset: 0,
        ...(selectedCycleId !== '__all__' ? { review_cycle_id: selectedCycleId } : {}),
      });
      const mapped: TrackerRow[] = page.items.map((r) => ({
        rowKey: `${r.portfolio_company_id}-${r.entity_id ?? 'company'}`,
        portfolioCompanyId: r.portfolio_company_id,
        companyName: r.company_name,
        entityId: r.entity_id,
        entityName: r.entity_name,
        entityStatus: r.entity_id != null ? normalizeReviewStage(r.entity_status) : null,
        auditPeriod:
          selectedCycleId === '__all__'
            ? rcCycles.find((x) => x.id === r.review_cycle_id)?.label || r.review_cycle_id || '—'
            : rcCycles.find((x) => x.id === selectedCycleId)?.label || '—',
        contactName: r.contact_name || undefined,
        hasFilesWithoutEntity: r.has_files_without_entity ?? false,
        hasPendingOrgChartReconciliation: r.has_pending_org_chart_reconciliation ?? false,
      }));
      setRows(mapped);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to load tracker';
      setLoadError(msg);
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [selectedCycleId, rcCycles]);

  const { run: runEntityStatusUpdate, loading: updatingEntityStatus } = useAsyncAction(
    async (payload: { state: EntityStatusConfirmState }) => {
      await applyEntityStatusChange(payload.state);
      await loadTracker();
      toast.success(
        `${payload.state.entityLabel} status updated to "${reviewStageDisplayLabel(payload.state.newStatus)}"`,
      );
    },
  );

  useEffect(() => {
    const loadCycles = async () => {
      try {
        const cycles = await fetchReviewCycles();
        setRcCycles(cycles);
      } catch {
        // Best-effort; tracker can still function without cycle labels.
      }
    };
    void loadCycles();
  }, []);

  useEffect(() => {
    void loadTracker();
  }, [loadTracker]);

  const filteredRows = useMemo(() => {
    return rows.filter((row) => {
      if (colFilterCompany && !row.companyName.toLowerCase().includes(colFilterCompany.toLowerCase())) return false;
      if (colFilterEntity && !(row.entityName || '').toLowerCase().includes(colFilterEntity.toLowerCase())) return false;
      if (colFilterEntityStatuses.size > 0) {
        const es = row.entityStatus || '';
        if (!colFilterEntityStatuses.has(es)) return false;
      }
      if (colFilterPeriods.size > 0 && !colFilterPeriods.has(row.auditPeriod)) return false;
      if (colFilterContact && !(row.contactName || '').toLowerCase().includes(colFilterContact.toLowerCase())) return false;
      return true;
    });
  }, [rows, colFilterCompany, colFilterEntity, colFilterEntityStatuses, colFilterPeriods, colFilterContact]);

  // Each visible row is an entity under review — show the total number of rows.
  const entityCount = filteredRows.length;
  const companyCount = useMemo(
    () => new Set(filteredRows.map((r) => r.portfolioCompanyId)).size,
    [filteredRows],
  );

  const hasColFilters =
    colFilterCompany || colFilterEntity ||
    colFilterEntityStatuses.size > 0 || colFilterPeriods.size > 0 || colFilterContact;

  const clearAllColFilters = () => {
    setColFilterCompany('');
    setColFilterEntity('');
    setColFilterEntityStatuses(new Set());
    setColFilterPeriods(new Set());
    setColFilterContact('');
  };

  // Unique values for multi-select column filters
  const uniqueEntityStatuses = [...COMPANY_REVIEW_STAGE_OPTIONS] as string[];
  const uniquePeriods = useMemo(() => [...new Set(rows.map((r) => r.auditPeriod))].sort(), [rows]);

  const confirmEntityStatusChange = async () => {
    if (!pendingEntityStatus) return;
    const snapshot = pendingEntityStatus;
    setPendingEntityStatus(null);
    try {
      await runEntityStatusUpdate({ state: snapshot });
    } catch {
      toast.error('Failed to update');
    }
  };

  return (
    <div className="h-full overflow-auto bg-gray-50 p-6">
      {loadError && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {loadError}
        </div>
      )}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">In Review Tracker</h1>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={selectedCycleId}
            onChange={(e) => setSelectedCycleId(e.target.value)}
            className="px-4 py-2 border border-gray-300 rounded-lg text-sm text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
          >
            <option value="__all__">All Cycles</option>
            {rcCycles.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <StageStats rows={rows} />

      <div className="mb-2 flex justify-end gap-2">
        <span className="inline-flex items-center rounded-full bg-gray-100 px-3 py-1 text-sm font-semibold text-gray-600">
          {companyCount} {companyCount === 1 ? 'company' : 'companies'}
        </span>
        <span className="inline-flex items-center rounded-full bg-blue-100 px-3 py-1 text-sm font-semibold text-blue-700">
          {entityCount} {entityCount === 1 ? 'entity' : 'entities'}
        </span>
      </div>

      <div className="overflow-x-auto bg-white rounded-lg border border-gray-200 shadow-sm">
        <table className="w-full table-fixed">
          <colgroup>
            <col className="w-[28%]" />
            <col className="w-[18%]" />
            <col className="w-[20%]" />
            <col className="w-[16%]" />
            <col className="w-[14%]" />
            <col className="w-[4%]" />
          </colgroup>
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">
                <div className="flex items-center justify-between gap-1.5">
                  <span>Deal Name</span>
                  <ColFilter kind="search" value={colFilterCompany} onChange={setColFilterCompany} />
                </div>
              </th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">
                <div className="flex items-center justify-between gap-1.5">
                  <span>Entity</span>
                  <ColFilter kind="search" value={colFilterEntity} onChange={setColFilterEntity} />
                </div>
              </th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">
                <div className="flex items-center justify-between gap-1.5">
                  <span>Status</span>
                  <ColFilter
                    kind="multi"
                    options={uniqueEntityStatuses}
                    selected={colFilterEntityStatuses}
                    onChange={setColFilterEntityStatuses}
                    display={reviewStageDisplayLabel}
                  />
                </div>
              </th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">
                <div className="flex items-center justify-between gap-1.5">
                  <span>Review Period</span>
                  <ColFilter
                    kind="multi"
                    options={uniquePeriods}
                    selected={colFilterPeriods}
                    onChange={setColFilterPeriods}
                  />
                </div>
              </th>
              <th className="text-xs font-medium text-gray-500 uppercase tracking-wider text-left px-4 py-3 border-r border-gray-200">
                <div className="flex items-center justify-between gap-1.5">
                  <span>Contact</span>
                  <ColFilter kind="search" value={colFilterContact} onChange={setColFilterContact} />
                </div>
              </th>
              <th className="px-4 py-3 text-right">
                {hasColFilters && (
                  <button
                    onClick={clearAllColFilters}
                    title="Clear all filters"
                    className="inline-flex items-center gap-1 text-[10px] text-blue-600 hover:text-blue-800 font-medium whitespace-nowrap"
                  >
                    <X className="h-3 w-3" />Clear
                  </button>
                )}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {filteredRows.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-sm text-gray-500">
                  {loading ? 'Loading…' : 'No rows match the current filters'}
                </td>
              </tr>
            ) : (
              filteredRows.map((row, idx) => {
                const prevRow = idx > 0 ? filteredRows[idx - 1] : null;
                const isFirstInGroup = !prevRow || prevRow.portfolioCompanyId !== row.portfolioCompanyId;
                const nextRow = idx < filteredRows.length - 1 ? filteredRows[idx + 1] : null;
                const isInMultiRowGroup =
                  (prevRow && prevRow.portfolioCompanyId === row.portfolioCompanyId) ||
                  (nextRow && nextRow.portfolioCompanyId === row.portfolioCompanyId);

                const groupBg = isInMultiRowGroup ? 'bg-blue-50/30' : '';
                const groupBorder = isInMultiRowGroup && isFirstInGroup ? 'border-t-2 border-t-blue-100' : '';

                return (
                <tr
                  key={row.rowKey}
                  onClick={() => navigate(`/company/${row.portfolioCompanyId}`)}
                  className={`hover:bg-blue-50/50 cursor-pointer transition-colors group ${groupBg} ${groupBorder}`}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Building2 className="h-4 w-4 text-blue-500 shrink-0" />
                      <span className="text-sm font-medium text-blue-600 hover:text-blue-800">{row.companyName}</span>
                      {row.hasFilesWithoutEntity && (
                        <span
                          title="This company has files uploaded but none attached to an entity"
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-700 border border-amber-200 ml-1"
                        >
                          <AlertTriangle className="h-3 w-3 shrink-0" />
                          Unattached files
                        </span>
                      )}
                      {row.hasPendingOrgChartReconciliation && (
                        <span
                          title="A new org chart was extracted and is waiting to be reconciled. Open the company's Org Chart tab to review."
                          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-purple-100 text-purple-700 border border-purple-200 ml-1"
                        >
                          <AlertTriangle className="h-3 w-3 shrink-0" />
                          Org chart pending
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{row.entityName || <span className="text-gray-300">—</span>}</td>
                  <td className="px-4 py-3">
                    {row.entityId != null && row.entityStatus ? (
                      <StageDropdown
                        value={row.entityStatus}
                        options={COMPANY_REVIEW_STAGE_OPTIONS}
                        badgeClass={reviewStageBadgeClass}
                        dotClass={reviewStageDotClass}
                        display={reviewStageDisplayLabel}
                        onChange={(to) =>
                          setPendingEntityStatus({
                            entityId: row.entityId!,
                            portfolioCompanyId: row.portfolioCompanyId,
                            entityLabel: row.entityName || `Entity #${row.entityId}`,
                            previousStatus: row.entityStatus!,
                            newStatus: to,
                          })
                        }
                      />
                    ) : (
                      <span className="text-sm text-gray-300">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-500">{row.auditPeriod === '—' ? <span className="text-gray-300">—</span> : row.auditPeriod}</td>
                  <td className="px-4 py-3 text-sm text-gray-500">{row.contactName || <span className="text-gray-300">—</span>}</td>
                  <td className="px-4 py-3 text-center">
                    <ChevronRight className="h-4 w-4 text-gray-400 group-hover:text-gray-600 transition-colors" />
                  </td>
                </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <EntityStatusConfirmDialog
        open={!!pendingEntityStatus}
        state={pendingEntityStatus}
        loading={updatingEntityStatus}
        onOpenChange={(open) => {
          if (!open) setPendingEntityStatus(null);
        }}
        onConfirm={confirmEntityStatusChange}
      />
    </div>
  );
}
