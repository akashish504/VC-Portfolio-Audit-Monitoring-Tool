import { type ReactNode } from 'react';
import { Building2, TrendingUp, TrendingDown, CheckCircle2, MinusCircle } from 'lucide-react';

import type { ScopingOverview } from '@/api/dashboard';
import { cn } from '@/lib/utils';
import { fmtInt } from './dashboardFormat';
import type { DrillTarget } from './DashboardDrillDownSheet';

interface Props {
  overview: ScopingOverview;
  onDrill: (target: DrillTarget) => void;
}

function KpiCard({ label, value, icon, accent, onClick }: {
  label: string; value: number; icon: ReactNode; accent: string; onClick?: () => void;
}) {
  const inner = (
    <>
      <div className={cn('flex h-11 w-11 items-center justify-center rounded-lg', accent)}>{icon}</div>
      <div>
        <div className="text-2xl font-semibold tabular-nums text-gray-900">{fmtInt(value)}</div>
        <div className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</div>
      </div>
    </>
  );
  const base = 'flex items-center gap-4 rounded-xl border border-gray-200 bg-white p-4 shadow-sm';
  return onClick ? (
    <button type="button" onClick={onClick} className={cn(base, 'text-left transition-shadow hover:shadow-md')}>
      {inner}
    </button>
  ) : (
    <div className={base}>{inner}</div>
  );
}

function StageBreakdown({ label, stageKey, entries, onDrill }: {
  label: string;
  stageKey: 'deal_level_stage_1' | 'deal_level_stage_2';
  entries: [string, number][];
  onDrill: (target: DrillTarget) => void;
}) {
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</span>
        <span className="text-xs font-medium tabular-nums text-gray-400">{fmtInt(total)} total</span>
      </div>
      {entries.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {entries.map(([stage, count]) => (
            <button
              key={stage}
              type="button"
              onClick={() => onDrill({ title: `${label}: ${stage}`, [stageKey]: stage })}
              className="inline-flex items-center gap-2 rounded-full bg-gray-100 px-3 py-1 text-sm font-medium text-gray-700 transition-opacity hover:opacity-80"
            >
              <span>{stage}</span>
              <span className="tabular-nums">{fmtInt(count)}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-gray-200 px-3 py-4 text-center text-sm text-gray-400">
          No data for this stage
        </div>
      )}
    </div>
  );
}

/** Header KPI cards + deal-level stage breakdown chips for the scoping view. */
export default function ScopingKpiCards({ overview, onDrill }: Props) {
  const stage1 = Object.entries(overview.stage_1_breakdown ?? {}).sort((a, b) => b[1] - a[1]);
  const stage2 = Object.entries(overview.stage_2_breakdown ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <KpiCard
          label="Total companies"
          value={overview.total_companies}
          icon={<Building2 className="h-5 w-5 text-blue-600" />}
          accent="bg-blue-50"
          onClick={() => onDrill({ title: 'All companies' })}
        />
        <KpiCard
          label="Deals scoped in"
          value={overview.scoped_in}
          icon={<CheckCircle2 className="h-5 w-5 text-green-600" />}
          accent="bg-green-50"
          onClick={overview.scoped_in > 0
            ? () => onDrill({ title: 'Deals scoped in for audit', scoping_for_audit: true })
            : undefined}
        />
        <KpiCard
          label="Deals scoped out"
          value={overview.scoped_out}
          icon={<MinusCircle className="h-5 w-5 text-gray-500" />}
          accent="bg-gray-100"
          onClick={overview.scoped_out > 0
            ? () => onDrill({ title: 'Deals scoped out of audit', scoping_for_audit: false })
            : undefined}
        />
        <KpiCard
          label="Newly added"
          value={overview.new_added}
          icon={<TrendingUp className="h-5 w-5 text-green-600" />}
          accent="bg-green-50"
          onClick={overview.new_added > 0
            ? () => onDrill({ title: 'Newly added companies', delta: 'added' })
            : undefined}
        />
        <KpiCard
          label="Removed"
          value={overview.removed}
          icon={<TrendingDown className="h-5 w-5 text-red-600" />}
          accent="bg-red-50"
          onClick={overview.removed > 0
            ? () => onDrill({ title: 'Removed companies', delta: 'removed' })
            : undefined}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <StageBreakdown label="Deal level stage 1" stageKey="deal_level_stage_1" entries={stage1} onDrill={onDrill} />
        <StageBreakdown label="Deal level stage 2" stageKey="deal_level_stage_2" entries={stage2} onDrill={onDrill} />
      </div>
    </div>
  );
}
