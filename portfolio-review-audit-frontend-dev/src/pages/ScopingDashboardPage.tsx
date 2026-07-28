import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { LayoutDashboard } from 'lucide-react';

import {
  downloadAuditorSummary,
  downloadOverallStatus,
  getDashboardFilters,
  getScopingAuditorSummary,
  getScopingOverallStatus,
  getScopingOverview,
  pickDefaultCycle,
  type ScopingFilters,
} from '@/api/dashboard';
import { Skeleton } from '@/components/ui/skeleton';
import DashboardViewTabs from '@/components/dashboard/DashboardViewTabs';
import DashboardFilterBar from '@/components/dashboard/DashboardFilterBar';
import ScopingKpiCards from '@/components/dashboard/ScopingKpiCards';
import YoYPieCards from '@/components/dashboard/YoYPieCards';
import ILWiseSection from '@/components/dashboard/ILWiseSection';
import CategoryWiseSection from '@/components/dashboard/CategoryWiseSection';
import DealStageMatrixSection from '@/components/dashboard/DealStageMatrixSection';
import DashboardDrillDownSheet, { type DrillTarget } from '@/components/dashboard/DashboardDrillDownSheet';
import DownloadXlsxButton from '@/components/dashboard/DownloadXlsxButton';

const PARAM_KEYS: Record<keyof ScopingFilters, string> = {
  review_cycle_id: 'cycle',
  fund: 'fund',
  geography: 'geography',
  sector: 'sector',
  strategy: 'strategy',
  category: 'category',
  investment_lead: 'il',
};

export default function ScopingDashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [drill, setDrill] = useState<{ open: boolean; target: DrillTarget | null }>({
    open: false,
    target: null,
  });

  const filters: ScopingFilters = useMemo(
    () => ({
      review_cycle_id: searchParams.get('cycle') || undefined,
      fund: searchParams.get('fund') || undefined,
      geography: searchParams.get('geography') || undefined,
      sector: searchParams.get('sector') || undefined,
      strategy: searchParams.get('strategy') || undefined,
      category: searchParams.get('category') || undefined,
      investment_lead: searchParams.get('il') || undefined,
    }),
    [searchParams],
  );

  const updateFilters = useCallback(
    (next: ScopingFilters) => {
      const sp = new URLSearchParams();
      (Object.keys(PARAM_KEYS) as (keyof ScopingFilters)[]).forEach((k) => {
        const v = next[k];
        if (v) sp.set(PARAM_KEYS[k], v);
      });
      setSearchParams(sp, { replace: true });
    },
    [setSearchParams],
  );

  // Matrices + summaries honour the full filter set (cycle + dimensions, spec R1).
  const cohort: ScopingFilters = useMemo(() => filters, [filters]);

  const vocabQ = useQuery({ queryKey: ['dashboard', 'filters'], queryFn: getDashboardFilters });
  const overviewQ = useQuery({
    queryKey: ['dashboard', 'scoping-overview', cohort],
    queryFn: () => getScopingOverview(cohort),
  });
  const overallStatusQ = useQuery({
    queryKey: ['dashboard', 'scoping-overall-status', cohort],
    queryFn: () => getScopingOverallStatus(cohort),
  });
  const auditorSummaryQ = useQuery({
    queryKey: ['dashboard', 'scoping-auditor-summary', cohort],
    queryFn: () => getScopingAuditorSummary(cohort),
  });

  // Default to the first available review cycle once the vocab loads.
  useEffect(() => {
    if (!filters.review_cycle_id && vocabQ.data?.review_cycles?.length) {
      updateFilters({ ...filters, review_cycle_id: pickDefaultCycle(vocabQ.data.review_cycles) });
    }
  }, [vocabQ.data, filters, updateFilters]);

  const openDrill = useCallback((target: DrillTarget) => setDrill({ open: true, target }), []);

  return (
    <div className="h-full overflow-auto bg-gray-50 p-6">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <LayoutDashboard className="h-5 w-5 text-gray-700" />
            <h1 className="text-2xl font-bold text-gray-900">Audit Dashboard — Scoping</h1>
          </div>
          <DashboardViewTabs />
        </div>

        <DashboardFilterBar vocab={vocabQ.data} value={filters} onChange={updateFilters} />

        {overviewQ.isLoading ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-20 w-full rounded-xl" />)}
          </div>
        ) : overviewQ.data ? (
          <ScopingKpiCards overview={overviewQ.data} onDrill={openDrill} />
        ) : null}

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          {overallStatusQ.isLoading ? (
            <Skeleton className="h-96 w-full rounded-xl" />
          ) : overallStatusQ.data ? (
            <YoYPieCards
              title="Overall status (vs. previous year)"
              summary={overallStatusQ.data}
              action={
                <DownloadXlsxButton
                  download={() => downloadOverallStatus(filters)}
                />
              }
              onCategoryClick={(category, reviewCycleId) =>
                openDrill({
                  title: reviewCycleId ? `${category} · ${reviewCycleId}` : category,
                  // Overall status = master-scoping deal_level_stage_1 → drill PCM list.
                  deal_level_stage_1: category,
                  review_cycle_id: reviewCycleId ?? undefined,
                })
              }
            />
          ) : null}

          {auditorSummaryQ.isLoading ? (
            <Skeleton className="h-96 w-full rounded-xl" />
          ) : auditorSummaryQ.data ? (
            <YoYPieCards
              title="Auditors details summary (vs. previous year)"
              summary={auditorSummaryQ.data}
              action={
                <DownloadXlsxButton
                  download={() => downloadAuditorSummary(filters)}
                />
              }
              onCategoryClick={(category, reviewCycleId) =>
                openDrill({
                  title: reviewCycleId ? `Auditor: ${category} · ${reviewCycleId}` : `Auditor: ${category}`,
                  // Auditor category now from master scoping (category_of_auditor) → PCM list.
                  category_of_auditor: category,
                  review_cycle_id: reviewCycleId ?? undefined,
                })
              }
            />
          ) : null}
        </div>

        <DealStageMatrixSection
          reviewCycleId={filters.review_cycle_id}
          stage="1"
          title="Deal Stage 1 by FYE month"
          filters={filters}
        />
        <DealStageMatrixSection
          reviewCycleId={filters.review_cycle_id}
          stage="2"
          title="Deal Stage 2 by FYE month"
          filters={filters}
        />

        <ILWiseSection reviewCycleId={filters.review_cycle_id} />
        <CategoryWiseSection reviewCycleId={filters.review_cycle_id} categories={vocabQ.data?.categories ?? []} />
      </div>

      <DashboardDrillDownSheet
        open={drill.open}
        onOpenChange={(open) => setDrill((d) => ({ ...d, open }))}
        filters={filters}
        target={drill.target}
      />
    </div>
  );
}
