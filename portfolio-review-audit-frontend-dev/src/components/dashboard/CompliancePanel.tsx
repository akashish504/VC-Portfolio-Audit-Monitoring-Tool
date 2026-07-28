import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';

import type { ComplianceSummary } from '@/api/dashboard';
import { fmtInt } from './dashboardFormat';
import type { DrillTarget } from './DashboardDrillDownSheet';

interface Props {
  compliance: ComplianceSummary;
  onDrill?: (target: DrillTarget) => void;
}

const OPINION_COLORS: Record<string, string> = {
  Clean: '#16a34a',
  Qualified: '#f59e0b',
  Adverse: '#dc2626',
  'Disclaimer of Opinion': '#6b7280',
};

function AvailabilityRow({ label, data }: { label: string; data: Record<string, number> }) {
  const available = data['Available'] ?? 0;
  const notAvailable = data['Not Available'] ?? 0;
  const total = Math.max(1, available + notAvailable);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="font-medium text-gray-700">{label}</span>
        <span className="text-gray-500">
          {fmtInt(available)} available · {fmtInt(notAvailable)} not
        </span>
      </div>
      <div className="flex h-2.5 overflow-hidden rounded-full bg-gray-100">
        <div className="h-full bg-emerald-500" style={{ width: `${(available / total) * 100}%` }} />
        <div className="h-full bg-gray-300" style={{ width: `${(notAvailable / total) * 100}%` }} />
      </div>
    </div>
  );
}

/** Audit-report compliance for completed companies: opinion mix + EoM/OM availability. */
export default function CompliancePanel({ compliance, onDrill }: Props) {
  const pie = Object.entries(compliance.opinions)
    .map(([name, value]) => ({ name, value }))
    .filter((d) => d.value > 0);
  const drillOpinion = (name: string) =>
    onDrill?.({ title: `Opinion: ${name}`, scoped_in: true, opinion: name });

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold text-gray-900">Compliance (completed audits)</h2>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <div>
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-gray-500">Audit opinion</div>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie data={pie} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80}
                paddingAngle={2} isAnimationActive={false}
                onClick={(d: { name?: string }) => d?.name && drillOpinion(d.name)}>
                {pie.map((d) => (
                  <Cell key={d.name} fill={OPINION_COLORS[d.name] ?? '#94a3b8'} cursor={onDrill ? 'pointer' : 'default'} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
          {/* Clickable opinion legend (reliable drill target). */}
          <div className="mt-1 flex flex-wrap justify-center gap-x-3 gap-y-1">
            {pie.map((d) => (
              <button key={d.name} type="button" onClick={() => drillOpinion(d.name)}
                className="inline-flex items-center gap-1.5 text-xs text-gray-600 hover:text-blue-700 hover:underline">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: OPINION_COLORS[d.name] ?? '#94a3b8' }} />
                {d.name} <span className="tabular-nums font-medium">{fmtInt(d.value)}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-col justify-center gap-5">
          <AvailabilityRow label="Emphasis of Matter" data={compliance.emphasis_of_matter} />
          <AvailabilityRow label="Other Matters (flagged)" data={compliance.other_matters} />
        </div>
      </div>
    </div>
  );
}
