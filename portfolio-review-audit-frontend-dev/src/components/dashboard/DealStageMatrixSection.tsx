import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { downloadDealStageMatrix, getScopingDealStageMatrix, type ScopingFilters } from '@/api/dashboard';
import { cn } from '@/lib/utils';
import { Skeleton } from '@/components/ui/skeleton';
import StatusByMonthChart from './StatusByMonthChart';
import StatusMatrixTable from './StatusMatrixTable';
import DashboardDrillDownSheet, { type DrillTarget } from './DashboardDrillDownSheet';
import DownloadXlsxButton from './DownloadXlsxButton';

type Basis = 'cid' | 'cid_strategy';

interface Props {
  reviewCycleId?: string;
  stage: '1' | '2';
  title: string;
  /** Active dimension filters (so the matrix + download respect the filter bar). */
  filters?: ScopingFilters;
}

const BASIS_LABEL: Record<Basis, string> = {
  cid: 'CID',
  cid_strategy: 'CID + Strategy',
};

/** Master-scoping deal-stage × FYE-month dashboard: a stacked bar chart + a
 *  status(=stage) × month matrix table, with the count basis toggled between
 *  unique CID and unique CID + Strategy. Cells drill into the master-scoping
 *  deal list (with a Download XLSX). */
export default function DealStageMatrixSection({ reviewCycleId, stage, title, filters }: Props) {
  const [basis, setBasis] = useState<Basis>('cid');
  const [drill, setDrill] = useState<{ open: boolean; target: DrillTarget | null }>({
    open: false,
    target: null,
  });

  const variant: 'deal_stage_1' | 'deal_stage_2' = stage === '2' ? 'deal_stage_2' : 'deal_stage_1';
  const dims = { ...filters, review_cycle_id: reviewCycleId };

  const query = useQuery({
    queryKey: ['dashboard', 'deal-stage-matrix', stage, basis, dims],
    queryFn: () => getScopingDealStageMatrix({ ...dims, stage, basis }),
  });

  const openDrill = (target: DrillTarget) => setDrill({ open: true, target });

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
          <p className="text-xs text-gray-500">
            From master scoping — count of unique {BASIS_LABEL[basis]} per deal stage × FYE month.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
            {(['cid', 'cid_strategy'] as Basis[]).map((b) => (
              <button
                key={b}
                type="button"
                onClick={() => setBasis(b)}
                className={cn(
                  'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                  basis === b ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-gray-700',
                )}
              >
                {BASIS_LABEL[b]}
              </button>
            ))}
          </div>
          <DownloadXlsxButton
            download={() => downloadDealStageMatrix({ ...dims, stage, basis })}
          />
        </div>
      </div>

      {query.isLoading ? (
        <Skeleton className="h-80 w-full rounded-lg" />
      ) : query.isError ? (
        <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">Failed to load the matrix.</div>
      ) : query.data ? (
        <div className="space-y-5">
          <StatusByMonthChart
            matrix={query.data}
            onSegmentClick={(status, month) =>
              openDrill({
                title: `${status} · ${month}`,
                ...(stage === '2' ? { deal_level_stage_2: status } : { deal_level_stage_1: status }),
                fy_end: month,
                pcm: true,
              })
            }
          />
          <StatusMatrixTable
            matrix={query.data}
            totalLabel="Total"
            onDrill={openDrill}
            variant={variant}
            reviewCycleId={reviewCycleId}
          />
        </div>
      ) : null}

      <DashboardDrillDownSheet
        open={drill.open}
        onOpenChange={(open) => setDrill((d) => ({ ...d, open }))}
        filters={{ review_cycle_id: reviewCycleId }}
        target={drill.target}
      />
    </div>
  );
}
