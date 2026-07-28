import { type ReactNode } from 'react';
import { Layers, CheckCircle2, AlertTriangle, Flag } from 'lucide-react';

import type { ComplianceSummary, VarianceCycleSummary } from '@/api/dashboard';
import { cn } from '@/lib/utils';
import { fmtInt } from './dashboardFormat';
import type { DrillTarget } from './DashboardDrillDownSheet';

interface Props {
  cycle: VarianceCycleSummary;
  compliance: ComplianceSummary;
  onDrill: (target: DrillTarget) => void;
}

// Spec KPI -> entity review-status (entities.status) vocabulary.
const COMPLETED_STATUSES = ['Approved', 'Approved - Flagged'];
const DISCREPANCY_STATUS = 'Discrepancy identified';

function Card({ label, value, icon, accent, onClick }: {
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

function statusValue(cycle: VarianceCycleSummary, status: string): number {
  return cycle.status_summary.find((s) => s.status === status)?.count ?? 0;
}
function sumStatuses(cycle: VarianceCycleSummary, statuses: string[]): number {
  return statuses.reduce((acc, s) => acc + statusValue(cycle, s), 0);
}

/** Top KPI cards for the variance view. Each is click-through to its company list. */
export default function VarianceKpiCards({ cycle, compliance, onDrill }: Props) {
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <Card label="Scoped-in (cycle)" value={cycle.scoped_in_total}
        icon={<Layers className="h-5 w-5 text-blue-600" />} accent="bg-blue-50"
        onClick={() => onDrill({ title: 'Scoped-in companies', scoped_in: true })} />
      <Card label="Completed" value={sumStatuses(cycle, COMPLETED_STATUSES)}
        icon={<CheckCircle2 className="h-5 w-5 text-green-600" />} accent="bg-green-50"
        onClick={() => onDrill({ title: 'Completed', scoped_in: true, entity_status: COMPLETED_STATUSES.join(',') })} />
      <Card label="Discrepancy identified" value={statusValue(cycle, DISCREPANCY_STATUS)}
        icon={<AlertTriangle className="h-5 w-5 text-amber-600" />} accent="bg-amber-50"
        onClick={() => onDrill({ title: 'Discrepancy identified', scoped_in: true, entity_status: DISCREPANCY_STATUS })} />
      <Card label="Highlighted to investor" value={compliance.completed_highlighted_to_investor}
        icon={<Flag className="h-5 w-5 text-red-600" />} accent="bg-red-50"
        onClick={() => onDrill({ title: 'Highlighted to investor', scoped_in: true, flagged: true })} />
    </div>
  );
}
