import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CalendarClock } from 'lucide-react';

import {
  getDashboardFilters,
  getTimelineChart,
  getTimelineSummary,
  pickDefaultCycle,
} from '@/api/dashboard';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import DashboardViewTabs from '@/components/dashboard/DashboardViewTabs';
import TimelineTopTables from '@/components/dashboard/TimelineTopTables';
import TimelineStatusTable from '@/components/dashboard/TimelineStatusTable';
import TimelineMatrixSection from '@/components/dashboard/TimelineMatrixSection';
import TimelineChart from '@/components/dashboard/TimelineChart';
import DashboardDrillDownSheet, { type DrillTarget } from '@/components/dashboard/DashboardDrillDownSheet';

function SectionCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3">
        <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        {subtitle && <p className="text-xs text-gray-500">{subtitle}</p>}
      </div>
      {children}
    </div>
  );
}

const ALL = '__all__';

/** Timeline dashboard (third view) — master-scoping strategy/geo counts, the
 *  "# of financials" status breakdown, and the audit-timeline cross-tabs. */
export default function TimelineDashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const cycle = searchParams.get('cycle') || undefined;

  const setCycle = (next?: string) => {
    const sp = new URLSearchParams(searchParams);
    if (next) sp.set('cycle', next);
    else sp.delete('cycle');
    setSearchParams(sp, { replace: true });
  };

  const vocabQ = useQuery({ queryKey: ['dashboard', 'filters'], queryFn: getDashboardFilters });
  const timelineQ = useQuery({
    queryKey: ['dashboard', 'timeline', cycle],
    queryFn: () => getTimelineSummary({ review_cycle_id: cycle }),
  });
  const chartQ = useQuery({
    queryKey: ['dashboard', 'timeline-chart', cycle],
    queryFn: () => getTimelineChart({ review_cycle_id: cycle }),
  });

  // Default to the latest cycle once the vocabulary loads.
  useEffect(() => {
    if (!cycle && vocabQ.data?.review_cycles?.length) {
      const def = pickDefaultCycle(vocabQ.data.review_cycles);
      if (def) setCycle(def);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vocabQ.data]);

  const cycles = useMemo(() => vocabQ.data?.review_cycles ?? [], [vocabQ.data]);
  const data = timelineQ.data;

  const [drill, setDrill] = useState<{ open: boolean; target: DrillTarget | null }>({
    open: false,
    target: null,
  });
  const openDrill = useCallback((target: DrillTarget) => setDrill({ open: true, target }), []);

  return (
    <div className="h-full overflow-auto bg-gray-50 p-6">
      <div className="mx-auto max-w-7xl space-y-5">
        {/* Header + view tabs */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <CalendarClock className="h-5 w-5 text-gray-700" />
            <h1 className="text-2xl font-bold text-gray-900">Audit Dashboard — Timeline</h1>
          </div>
          <DashboardViewTabs />
        </div>

        {/* Cycle selector */}
        <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
          <div className="flex min-w-[180px] max-w-xs flex-col gap-1">
            <label className="text-xs font-medium text-gray-500">Review cycle</label>
            <Select value={cycle ?? ALL} onValueChange={(v) => setCycle(v === ALL ? undefined : v)}>
              <SelectTrigger className="h-9 bg-white text-sm">
                <SelectValue placeholder="Select cycle" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All cycles</SelectItem>
                {cycles.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name || c.id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {timelineQ.isLoading ? (
          <div className="space-y-5">
            <Skeleton className="h-48 w-full rounded-xl" />
            <Skeleton className="h-64 w-full rounded-xl" />
          </div>
        ) : timelineQ.isError ? (
          <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
            Failed to load the timeline.
          </div>
        ) : data ? (
          <>
            {/* Strategy-wise unique counts + for-analysis counts */}
            <TimelineTopTables
              strategyAll={data.strategy_all}
              strategyIncluded={data.strategy_included}
              analysisAll={data.analysis_all}
              analysisIncluded={data.analysis_included}
              reviewCycleId={cycle}
              onDrill={openDrill}
            />

            {/* Audit-timeline status breakdown (count + %) */}
            <SectionCard
              title="Audit status — scoped in companies"
              subtitle="Deal-stage-1 status of the scoped-in cohort, by strategy and geography (count and %)."
            >
              <TimelineStatusTable table={data.status_table} reviewCycleId={cycle} onDrill={openDrill} />
            </SectionCard>

            {/* Filterable tentative-completion chart (sits below the status table) */}
            <SectionCard
              title="Tentative audit completion date"
              subtitle="Companies bucketed by tentative completion month. Use the filters to drill into geography, inclusion, strategy, FY end and within-due / overdue."
            >
              {chartQ.isLoading ? (
                <Skeleton className="h-80 w-full rounded-lg" />
              ) : chartQ.isError ? (
                <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
                  Failed to load the timeline chart.
                </div>
              ) : chartQ.data ? (
                <TimelineChart feed={chartQ.data} onDrill={openDrill} />
              ) : null}
            </SectionCard>

            {/* Audit-timeline cross-tabs */}
            <SectionCard
              title="Audit timeline — by FY end"
              subtitle="Scoped in companies: deal-stage-1 status × FYE month, per strategy and geography."
            >
              <TimelineMatrixSection matrices={data.matrices} reviewCycleId={cycle} onDrill={openDrill} />
            </SectionCard>
          </>
        ) : null}
      </div>

      <DashboardDrillDownSheet
        open={drill.open}
        onOpenChange={(open) => setDrill((d) => ({ ...d, open }))}
        filters={{ review_cycle_id: cycle }}
        target={drill.target}
      />
    </div>
  );
}
