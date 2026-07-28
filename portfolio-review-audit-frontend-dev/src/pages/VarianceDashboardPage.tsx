import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { LayoutDashboard } from 'lucide-react';

import {
  downloadDiscrepancyReasons,
  downloadDiscrepancySummary,
  getDashboardFilters,
  getVarianceActionables,
  getVarianceCompliance,
  getVarianceCycleSummary,
  getVarianceDiscrepancySummary,
  getVarianceDiscrepancySubcategoryMatrix,
  pickDefaultCycle,
} from '@/api/dashboard';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import DashboardViewTabs from '@/components/dashboard/DashboardViewTabs';
import VarianceKpiCards from '@/components/dashboard/VarianceKpiCards';
import VarianceStatusPanel from '@/components/dashboard/VarianceStatusPanel';
import CompliancePanel from '@/components/dashboard/CompliancePanel';
import DiscrepancyPanel from '@/components/dashboard/DiscrepancyPanel';
import DiscrepancyReasonMatrix from '@/components/dashboard/DiscrepancyReasonMatrix';
import DiscrepancySubcategorySheet from '@/components/dashboard/DiscrepancySubcategorySheet';
import DiscrepancyDetailsSheet from '@/components/dashboard/DiscrepancyDetailsSheet';
import DashboardDrillDownSheet, { type DrillTarget } from '@/components/dashboard/DashboardDrillDownSheet';
import ReportViewSection from '@/components/dashboard/ReportViewSection';
import VarianceCompanyDetailSection from '@/components/dashboard/VarianceCompanyDetailSection';
import DownloadXlsxButton from '@/components/dashboard/DownloadXlsxButton';

export default function VarianceDashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [drill, setDrill] = useState<{ open: boolean; metric: string | null }>({ open: false, metric: null });
  const [companyDrill, setCompanyDrill] = useState<{ open: boolean; target: DrillTarget | null }>({
    open: false,
    target: null,
  });
  const [detailsDrill, setDetailsDrill] = useState<{
    open: boolean; metric: string | null; bucket: 'gt' | 'lt' | 'nc' | null; subcategory: string | null;
  }>({ open: false, metric: null, bucket: null, subcategory: null });

  const cycle = searchParams.get('cycle') || undefined;
  const cycleParam = useMemo(() => ({ review_cycle_id: cycle }), [cycle]);

  const setCycle = useCallback(
    (id: string) => {
      const sp = new URLSearchParams(searchParams);
      sp.set('cycle', id);
      setSearchParams(sp, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const vocabQ = useQuery({ queryKey: ['dashboard', 'filters'], queryFn: getDashboardFilters });
  const cycleQ = useQuery({
    queryKey: ['dashboard', 'variance-cycle', cycle],
    queryFn: () => getVarianceCycleSummary(cycleParam),
  });
  const actionablesQ = useQuery({
    queryKey: ['dashboard', 'variance-actionables', cycle],
    queryFn: () => getVarianceActionables(cycleParam),
  });
  const complianceQ = useQuery({
    queryKey: ['dashboard', 'variance-compliance', cycle],
    queryFn: () => getVarianceCompliance(cycleParam),
  });
  const discrepancyQ = useQuery({
    queryKey: ['dashboard', 'variance-discrepancy', cycle],
    queryFn: () => getVarianceDiscrepancySummary(cycleParam),
  });
  const discrepancyMatrixQ = useQuery({
    queryKey: ['dashboard', 'variance-discrepancy-matrix', cycle],
    queryFn: () => getVarianceDiscrepancySubcategoryMatrix(cycleParam),
  });

  // Threshold label for the reason matrix heading (mirrors DiscrepancyPanel):
  // show "> ±10%" when every metric shares one configured threshold.
  const thresholdLabel = useMemo(() => {
    const thr = (discrepancyQ.data?.metrics ?? [])
      .map((m) => m.threshold_pct)
      .filter((x): x is number => x != null);
    const uniform = thr.length > 0 && thr.every((x) => x === thr[0]) ? thr[0] : null;
    return uniform != null ? `> ±${+(uniform * 100).toFixed(2)}%` : 'above threshold';
  }, [discrepancyQ.data]);

  useEffect(() => {
    if (!cycle && vocabQ.data?.review_cycles?.length) {
      const def = pickDefaultCycle(vocabQ.data.review_cycles);
      if (def) setCycle(def);
    }
  }, [cycle, vocabQ.data, setCycle]);

  const cycles = vocabQ.data?.review_cycles ?? [];
  const loading = cycleQ.isLoading || complianceQ.isLoading || discrepancyQ.isLoading || actionablesQ.isLoading;

  // Variance company drills default to the scoped-in cohort (financials uploaded).
  const openCompanyDrill = useCallback(
    (target: DrillTarget) => setCompanyDrill({ open: true, target: { scoped_in: true, ...target } }),
    [],
  );

  return (
    <div className="h-full overflow-auto bg-gray-50 p-6">
      <div className="mx-auto max-w-7xl space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <LayoutDashboard className="h-5 w-5 text-gray-700" />
            <h1 className="text-2xl font-bold text-gray-900">Audit Dashboard — Variance</h1>
          </div>
          <div className="flex items-center gap-3">
            <Select value={cycle ?? ''} onValueChange={setCycle}>
              <SelectTrigger className="h-9 w-44 bg-white text-sm">
                <SelectValue placeholder="Review cycle" />
              </SelectTrigger>
              <SelectContent>
                {cycles.map((c) => (
                  <SelectItem key={c.id} value={c.id}>{c.name || c.id}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <DashboardViewTabs />
          </div>
        </div>

        {loading ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20 w-full rounded-xl" />)}
            </div>
            <Skeleton className="h-64 w-full rounded-xl" />
            <Skeleton className="h-80 w-full rounded-xl" />
          </div>
        ) : (
          <>
            {cycleQ.data && complianceQ.data && (
              <VarianceKpiCards
                cycle={cycleQ.data}
                compliance={complianceQ.data}
                onDrill={openCompanyDrill}
              />
            )}
            {cycleQ.data && actionablesQ.data && (
              <VarianceStatusPanel
                cycle={cycleQ.data}
                actionables={actionablesQ.data}
                onDrill={openCompanyDrill}
              />
            )}
            {complianceQ.data && <CompliancePanel compliance={complianceQ.data} onDrill={openCompanyDrill} />}
            {discrepancyQ.data && (
              <DiscrepancyPanel
                summary={discrepancyQ.data}
                action={<DownloadXlsxButton download={() => downloadDiscrepancySummary({ review_cycle_id: cycle })} />}
                onShowDetails={(metric, bucket) =>
                  setDetailsDrill({ open: true, metric, bucket, subcategory: null })}
                onShowReasons={(metric) => setDrill({ open: true, metric })}
              />
            )}
            {discrepancyMatrixQ.data && (
              <DiscrepancyReasonMatrix
                matrix={discrepancyMatrixQ.data}
                thresholdLabel={thresholdLabel}
                action={<DownloadXlsxButton download={() => downloadDiscrepancyReasons({ review_cycle_id: cycle })} />}
                onSelect={(metric, subcategory) =>
                  setDetailsDrill({ open: true, metric, bucket: 'gt', subcategory })}
              />
            )}
            <VarianceCompanyDetailSection reviewCycleId={cycle} />
            <ReportViewSection reviewCycleId={cycle} />
          </>
        )}
      </div>

      <DiscrepancySubcategorySheet
        open={drill.open}
        onOpenChange={(open) => setDrill((d) => ({ ...d, open }))}
        metric={drill.metric}
        reviewCycleId={cycle}
        onSelectSubcategory={(subcategory) =>
          setDetailsDrill({ open: true, metric: drill.metric, bucket: 'gt', subcategory })}
      />

      <DiscrepancyDetailsSheet
        open={detailsDrill.open}
        onOpenChange={(open) => setDetailsDrill((d) => ({ ...d, open }))}
        metric={detailsDrill.metric}
        bucket={detailsDrill.bucket}
        subcategory={detailsDrill.subcategory}
        reviewCycleId={cycle}
      />

      <DashboardDrillDownSheet
        open={companyDrill.open}
        onOpenChange={(open) => setCompanyDrill((d) => ({ ...d, open }))}
        filters={cycleParam}
        target={companyDrill.target}
      />
    </div>
  );
}
