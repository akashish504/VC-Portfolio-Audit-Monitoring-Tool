import { useEffect, useRef, useState } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Bell, Download, FileText } from 'lucide-react';

import {
  downloadScopingCompanies,
  downloadScopingPCMDeals,
  getScopingCompletionTimeline,
  listScopingCompanies,
  listPCMDeals,
  type PCMDealRow,
  type ScopingFilters,
} from '@/api/dashboard';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { saveBlob } from '@/lib/utils';
import { fmtInt, displayOrDash } from './dashboardFormat';
import DashboardCompanyTable from './DashboardCompanyTable';
import CompletionTimelineChart from './CompletionTimelineChart';

const PAGE_SIZE = 25;

export interface DrillAction {
  label: string;
  to: string;
  icon?: 'reminder' | 'financials';
}

export interface DrillTarget {
  title: string;
  description?: string;
  status?: string;
  fy_end?: string;
  auditor_category?: string;
  /** Override the active cycle (e.g. drilling the prior-year donut/column). */
  review_cycle_id?: string;
  /** Restrict to scoped-in companies (financials uploaded this cycle). */
  scoped_in?: boolean;
  /** Restrict to companies flagged/highlighted to investor. */
  flagged?: boolean;
  /** Match the per-entity review status precisely (Variance drills). */
  entity_status?: string;
  /** Match the tentative scoping status precisely (overall-status pie, tentative matrix). */
  audit_status?: string;
  /** Match the actual-completion status precisely (actual matrix, IL-wise). */
  actual_status?: string;
  /** Match the auditor opinion (compliance drills). */
  opinion?: string;
  /** Newly-added / removed companies vs the prior cycle. */
  delta?: 'added' | 'removed';
  deal_level_stage_1?: string;
  deal_level_stage_2?: string;
  /** Master-scoping auditor category (Big 4 / Non-Big 4 / …) — drills the PCM list. */
  category_of_auditor?: string;
  /** Master-scoping strategy / geography (Timeline drills). */
  strategy?: string;
  geo_l1?: string;
  /** Restrict to India/SEA companies (Timeline totals). */
  india_sea_only?: boolean;
  /** Explicit CID list (Timeline chart bar → the companies in that bucket). */
  deal_ids?: string[];
  /** One row per CID — matches the Timeline cells' unique-company counts. */
  unique?: boolean;
  /** Master-scoping deal list: true = scoped in for audit, false = scoped out. */
  scoping_for_audit?: boolean;
  /** Force the master-scoping deal list (used by deal-stage matrix totals that
   *  carry only an FYE month, no stage). */
  pcm?: boolean;
  /** Show the months-ordered completion timeline above the list (spec A14/A15). */
  showTimeline?: boolean;
  /** Hand-off buttons to the email flow (spec A34). */
  actions?: DrillAction[];
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  filters: ScopingFilters;
  target: DrillTarget | null;
}

/** Side sheet listing the companies behind a clicked matrix cell / KPI. Paginated. */
export default function DashboardDrillDownSheet({ open, onOpenChange, filters, target }: Props) {
  const [page, setPage] = useState(0);
  const [selectedMonth, setSelectedMonth] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  // The cycle behind this drill: a per-target override (e.g. the prior-year donut)
  // falls back to the dashboard's active cycle.
  const reviewCycleId = target?.review_cycle_id ?? filters.review_cycle_id;

  // Reset page + month filter whenever the drill target changes.
  useEffect(() => {
    setPage(0);
    setSelectedMonth(null);
  }, [target?.title, target?.status, target?.fy_end, target?.auditor_category, target?.review_cycle_id, target?.scoped_in, target?.flagged, target?.entity_status, target?.audit_status, target?.actual_status, target?.opinion, target?.delta, target?.deal_level_stage_1, target?.deal_level_stage_2, target?.category_of_auditor, target?.strategy, target?.geo_l1, target?.india_sea_only, target?.unique, target?.deal_ids, target?.scoping_for_audit, target?.pcm]);

  // Targets backed by the master-scoping (PortfolioCompanyMetadata) deal list
  // rather than the portfolio-company list: deal-level stages, scoped in/out, and
  // deal-stage-matrix cells/totals (the `pcm` flag carries month-only totals).
  const isPcmTarget = !!(target?.deal_level_stage_1 || target?.deal_level_stage_2 || target?.category_of_auditor || target?.strategy || target?.geo_l1 || target?.india_sea_only || target?.deal_ids || target?.scoping_for_audit !== undefined || target?.pcm);
  const showExclusionReason = target?.scoping_for_audit === false;

  const query = useQuery({
    queryKey: ['dashboard', 'scoping-companies', filters, reviewCycleId, target?.status, target?.fy_end, target?.auditor_category, target?.scoped_in, target?.flagged, target?.entity_status, target?.audit_status, target?.actual_status, target?.opinion, target?.delta, selectedMonth, page],
    queryFn: () =>
      listScopingCompanies({
        ...filters,
        review_cycle_id: reviewCycleId,
        status: target?.status,
        fy_end: target?.fy_end,
        auditor_category: target?.auditor_category,
        scoped_in: target?.scoped_in,
        flagged: target?.flagged,
        entity_status: target?.entity_status,
        audit_status: target?.audit_status,
        actual_status: target?.actual_status,
        opinion: target?.opinion,
        delta: target?.delta,
        completion_month: selectedMonth ?? undefined,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    enabled: open && target !== null && !isPcmTarget,
    placeholderData: keepPreviousData,
  });

  const pcmQuery = useQuery({
    queryKey: ['dashboard', 'pcm-deals', reviewCycleId, target?.deal_level_stage_1, target?.deal_level_stage_2, target?.scoping_for_audit, target?.fy_end, target?.category_of_auditor, target?.strategy, target?.geo_l1, target?.india_sea_only, target?.unique, target?.deal_ids, filters.fund, filters.geography, filters.sector, filters.strategy, filters.category, filters.investment_lead, page],
    queryFn: () =>
      listPCMDeals({
        deal_level_stage_1: target?.deal_level_stage_1,
        deal_level_stage_2: target?.deal_level_stage_2,
        scoping_for_audit: target?.scoping_for_audit,
        fy_end: target?.fy_end,
        category_of_auditor: target?.category_of_auditor,
        // The cell's own strategy/geo wins; otherwise the active filter-bar value applies.
        strategy: target?.strategy ?? filters.strategy,
        geo_l1: target?.geo_l1 ?? filters.geography,
        india_sea_only: target?.india_sea_only,
        deal_ids: target?.deal_ids,
        unique: target?.unique,
        fund: filters.fund,
        sector: filters.sector,
        category: filters.category,
        investment_lead: filters.investment_lead,
        review_cycle_id: reviewCycleId,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    enabled: open && isPcmTarget,
    placeholderData: keepPreviousData,
  });

  const timelineQuery = useQuery({
    queryKey: ['dashboard', 'completion-timeline', reviewCycleId, target?.status, target?.fy_end],
    queryFn: () =>
      getScopingCompletionTimeline({
        review_cycle_id: reviewCycleId,
        status: target?.status,
        fy_end: target?.fy_end,
      }),
    enabled: open && !!target?.showTimeline,
  });

  // Page-independent signature of the active drill target. While `keepPreviousData`
  // keeps the prior rows during a fetch (smooth pagination), it must NOT show the
  // previous *target's* rows — when the target changes we force a loading state.
  const targetSig = JSON.stringify([
    isPcmTarget, reviewCycleId, selectedMonth,
    target?.deal_level_stage_1, target?.deal_level_stage_2, target?.scoping_for_audit,
    target?.fy_end, target?.category_of_auditor, target?.strategy, target?.geo_l1,
    target?.india_sea_only, target?.unique, target?.deal_ids,
    target?.status, target?.auditor_category, target?.audit_status, target?.actual_status,
    target?.entity_status, target?.opinion, target?.delta, target?.scoped_in, target?.flagged,
  ]);
  const activeQuery = isPcmTarget ? pcmQuery : query;
  const shownSigRef = useRef<string | null>(null);
  useEffect(() => {
    if (!activeQuery.isPlaceholderData && activeQuery.data !== undefined) {
      shownSigRef.current = targetSig;
    }
  }, [activeQuery.isPlaceholderData, activeQuery.data, targetSig]);
  // Loading = no data yet, OR fetching a new target while only stale (different-target)
  // placeholder data is on hand. A same-target page change keeps the rows (no skeleton).
  const showLoading =
    activeQuery.isLoading ||
    (activeQuery.isFetching && activeQuery.isPlaceholderData && shownSigRef.current !== targetSig);

  const total = showLoading ? 0 : isPcmTarget ? (pcmQuery.data?.total ?? 0) : (query.data?.total ?? 0);
  const rows = query.data?.items ?? [];
  const pcmRows: PCMDealRow[] = pcmQuery.data?.items ?? [];
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Export the COMPLETE filtered set for this drill (every page), not just the
  // page on screen — backend ignores pagination for the download endpoints.
  const handleDownload = async () => {
    if (!target) return;
    setDownloading(true);
    try {
      const result = isPcmTarget
        ? await downloadScopingPCMDeals({
            deal_level_stage_1: target.deal_level_stage_1,
            deal_level_stage_2: target.deal_level_stage_2,
            scoping_for_audit: target.scoping_for_audit,
            fy_end: target.fy_end,
            category_of_auditor: target.category_of_auditor,
            strategy: target.strategy ?? filters.strategy,
            geo_l1: target.geo_l1 ?? filters.geography,
            india_sea_only: target.india_sea_only,
            deal_ids: target.deal_ids,
            unique: target.unique,
            fund: filters.fund,
            sector: filters.sector,
            category: filters.category,
            investment_lead: filters.investment_lead,
            review_cycle_id: reviewCycleId,
          })
        : await downloadScopingCompanies({
            ...filters,
            review_cycle_id: reviewCycleId,
            status: target.status,
            fy_end: target.fy_end,
            auditor_category: target.auditor_category,
            scoped_in: target.scoped_in,
            flagged: target.flagged,
            entity_status: target.entity_status,
            audit_status: target.audit_status,
            actual_status: target.actual_status,
            opinion: target.opinion,
            delta: target.delta,
            completion_month: selectedMonth ?? undefined,
          });
      saveBlob(result.blob, result.filename);
    } catch {
      // non-critical — surfaced via the global API error handler
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>{target?.title ?? 'Companies'}</SheetTitle>
          <SheetDescription>
            {target?.description ?? 'Companies matching the selected cell and active filters.'}
            {' '}
            <span className="font-medium text-gray-700">{fmtInt(total)} total</span>
          </SheetDescription>
        </SheetHeader>

        <div className="mt-3">
          <button
            type="button"
            onClick={() => void handleDownload()}
            disabled={downloading || total === 0}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Download className="h-4 w-4" />
            {downloading ? 'Preparing…' : 'Download XLSX'}
          </button>
        </div>

        {target?.actions?.length ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {target.actions.map((a) => (
              <Link
                key={a.label}
                to={a.to}
                className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
              >
                {a.icon === 'reminder' ? <Bell className="h-3.5 w-3.5" /> : <FileText className="h-3.5 w-3.5" />}
                {a.label}
              </Link>
            ))}
          </div>
        ) : null}

        {target?.showTimeline ? (
          <div className="mt-4 rounded-lg border border-gray-200 p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium uppercase tracking-wide text-gray-500">
                Completion timeline (by month)
              </span>
              {selectedMonth && (
                <button
                  type="button"
                  onClick={() => { setSelectedMonth(null); setPage(0); }}
                  className="rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
                >
                  {selectedMonth} ✕
                </button>
              )}
            </div>
            {timelineQuery.isLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : timelineQuery.data && timelineQuery.data.months.length > 0 ? (
              <CompletionTimelineChart
                data={timelineQuery.data}
                selectedMonth={selectedMonth}
                onSelectMonth={(m) => { setSelectedMonth((cur) => (cur === m ? null : m)); setPage(0); }}
              />
            ) : (
              <div className="py-6 text-center text-xs text-gray-400">No completion-date data.</div>
            )}
          </div>
        ) : null}

        <div className="mt-4">
          {isPcmTarget ? (
            showLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}
              </div>
            ) : pcmQuery.isError ? (
              <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">Failed to load deals.</div>
            ) : pcmRows.length === 0 ? (
              <div className="px-4 py-10 text-center text-sm text-gray-500">No deals match this stage.</div>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-gray-200">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                      {['Deal name', 'Fund', 'Strategy', 'Category', 'FYE', 'IL', 'Stage 1', 'Stage 2', 'Auditor', 'Cost', 'FMV', ...(showExclusionReason ? ['Reason for exclusion'] : [])].map((c) => (
                        <th key={c} className="whitespace-nowrap px-3 py-2">{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {pcmRows.map((r) => (
                      <tr key={r.id} className="hover:bg-gray-50">
                        <td className="whitespace-nowrap px-3 py-2 font-medium text-gray-900">{r.deal_name}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.fund)}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.strategy)}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.category)}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.fy_end)}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.il_main)}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.deal_level_stage_1)}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.deal_level_stage_2)}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.auditor)}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{r.consolidated_cost != null ? r.consolidated_cost.toLocaleString() : '—'}</td>
                        <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{r.consolidated_fmv != null ? r.consolidated_fmv.toLocaleString() : '—'}</td>
                        {showExclusionReason && (
                          <td className="px-3 py-2 text-gray-600 max-w-xs">{displayOrDash(r.reason_for_exclusion)}</td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : showLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}
            </div>
          ) : query.isError ? (
            <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">Failed to load companies.</div>
          ) : (
            <DashboardCompanyTable rows={rows} />
          )}
        </div>

        {total > PAGE_SIZE && (
          <div className="mt-4 flex items-center justify-between text-sm text-gray-600">
            <span>
              Page {page + 1} of {pageCount}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="rounded-md border border-gray-300 px-3 py-1 disabled:opacity-50 hover:bg-gray-50"
              >
                Previous
              </button>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
                disabled={page >= pageCount - 1}
                className="rounded-md border border-gray-300 px-3 py-1 disabled:opacity-50 hover:bg-gray-50"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
