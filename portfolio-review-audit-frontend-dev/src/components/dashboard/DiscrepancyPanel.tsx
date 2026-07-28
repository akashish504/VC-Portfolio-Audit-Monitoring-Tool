import type { ReactNode } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { AlertTriangle } from 'lucide-react';

import type { DiscrepancySummary } from '@/api/dashboard';
import { fmtInt } from './dashboardFormat';
import { cn } from '@/lib/utils';

export const METRIC_LABEL: Record<string, string> = {
  revenue: 'Revenue', ebitda: 'EBITDA', pbt: 'PBT', pat: 'PAT', cash: 'Cash', debt: 'Debt',
};

interface Props {
  summary: DiscrepancySummary;
  onShowDetails: (metric: string, bucket: 'gt' | 'lt' | 'nc') => void;
  onShowReasons: (metric: string) => void;
  /** Optional header action (e.g. an XLSX download button). */
  action?: ReactNode;
}

/** Format a threshold fraction (0.10) as a trimmed percent ("10%", "0.5%"). */
const pctLabel = (t: number) => `${+(t * 100).toFixed(2)}%`;
const NC_LABEL = 'Not comparable';

/** Per-metric variance buckets: grouped bar chart + a click-through matrix table.
 *  Bucket labels reflect the threshold configured on the settings page
 *  (ParameterThreshold), falling back to the backend's hardcoded default. */
export default function DiscrepancyPanel({ summary, onShowDetails, onShowReasons, action }: Props) {
  const countBtn = 'rounded px-2 py-0.5 font-medium tabular-nums hover:bg-blue-50 hover:text-blue-700';

  // If every metric shares the same configured threshold, show it in the labels;
  // otherwise use generic labels and surface each metric's threshold in the table.
  const thresholds = summary.metrics.map((m) => m.threshold_pct).filter((x): x is number => x != null);
  const uniform = thresholds.length > 0 && thresholds.every((x) => x === thresholds[0]) ? thresholds[0] : null;
  const gtLabel = uniform != null ? `> ±${pctLabel(uniform)}` : 'Above threshold';
  const ltLabel = uniform != null ? `< ±${pctLabel(uniform)}` : 'Within threshold';

  const SERIES = [
    { key: gtLabel, color: '#dc2626' },
    { key: ltLabel, color: '#16a34a' },
    { key: NC_LABEL, color: '#94a3b8' },
  ];
  const data = summary.metrics.map((m) => ({
    metric: METRIC_LABEL[m.metric] ?? m.metric,
    [gtLabel]: m.gt_10,
    [ltLabel]: m.lt_10,
    [NC_LABEL]: m.not_comparable,
  }));

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-gray-900">Discrepancy summary by metric</h2>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-red-50 px-3 py-1 text-xs font-medium text-red-700">
            <AlertTriangle className="h-3.5 w-3.5" />
            {fmtInt(summary.diff_more_than_3_params)} companies differ on &gt; 3 parameters
          </span>
          {action}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
          <XAxis dataKey="metric" tick={{ fontSize: 12, fill: '#6b7280' }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fontSize: 12, fill: '#6b7280' }} axisLine={false} tickLine={false} allowDecimals={false} />
          <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 12 }}
            cursor={{ fill: 'rgba(37,99,235,0.04)' }} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {SERIES.map((s) => (
            <Bar key={s.key} dataKey={s.key} fill={s.color} maxBarSize={28} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>

      <div className="mt-4 overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
              <th className="px-3 py-2">Metric</th>
              <th className="px-3 py-2 text-right">{gtLabel}</th>
              <th className="px-3 py-2 text-right">{ltLabel}</th>
              <th className="px-3 py-2 text-right">{NC_LABEL}</th>
              <th className="px-3 py-2 text-right">Total</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {summary.metrics.map((m) => (
              <tr key={m.metric} className="hover:bg-gray-50">
                <td className="px-3 py-2">
                  <button type="button" onClick={() => onShowReasons(m.metric)}
                    className="font-medium text-gray-800 hover:text-blue-700" title="View above-threshold sub-category reasons">
                    {METRIC_LABEL[m.metric] ?? m.metric}
                    {uniform == null && m.threshold_pct != null && (
                      <span className="ml-1 text-xs font-normal text-gray-400">(±{pctLabel(m.threshold_pct)})</span>
                    )}
                  </button>
                </td>
                <td className="px-2 py-1 text-right">
                  {m.gt_10 > 0 ? (
                    <button type="button" onClick={() => onShowDetails(m.metric, 'gt')}
                      className={cn(countBtn, 'text-red-700 hover:bg-red-50')} title="View entity discrepancy details">
                      {fmtInt(m.gt_10)}
                    </button>
                  ) : <span className="px-2 text-gray-300">0</span>}
                </td>
                <td className="px-2 py-1 text-right">
                  {m.lt_10 > 0 ? (
                    <button type="button" onClick={() => onShowDetails(m.metric, 'lt')} className={cn(countBtn, 'text-gray-700')}>
                      {fmtInt(m.lt_10)}
                    </button>
                  ) : <span className="px-2 text-gray-300">0</span>}
                </td>
                <td className="px-2 py-1 text-right">
                  {m.not_comparable > 0 ? (
                    <button type="button" onClick={() => onShowDetails(m.metric, 'nc')} className={cn(countBtn, 'text-gray-500')}>
                      {fmtInt(m.not_comparable)}
                    </button>
                  ) : <span className="px-2 text-gray-300">0</span>}
                </td>
                <td className="px-3 py-2 text-right font-semibold tabular-nums text-gray-800">{fmtInt(m.total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-gray-500">
        Click a count for entity-level discrepancy details (remarks, response, status); click a metric name for the {gtLabel} sub-category reasons.
        {uniform != null && <span className="ml-1 text-gray-400">Threshold ±{pctLabel(uniform)} is set on the settings page.</span>}
      </p>
    </div>
  );
}
