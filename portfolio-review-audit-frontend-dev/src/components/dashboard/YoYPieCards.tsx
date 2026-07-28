import type { ReactNode } from 'react';
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';

import type { YoYSummary } from '@/api/dashboard';
import { colorForIndex, fmtInt } from './dashboardFormat';

interface Props {
  title: string;
  summary: YoYSummary;
  /** Fires with the clicked category and the cycle of the donut/column it came from. */
  onCategoryClick?: (category: string, reviewCycleId: string | null) => void;
  /** Optional header action (e.g. an XLSX download button). */
  action?: ReactNode;
}

function Donut({ label, data, colors, onSlice }: {
  label: string;
  data: { name: string; value: number }[];
  colors: Record<string, string>;
  onSlice?: (name: string) => void;
}) {
  const nonZero = data.filter((d) => d.value > 0);
  return (
    <div>
      <div className="mb-1 text-center text-xs font-medium uppercase tracking-wide text-gray-500">{label}</div>
      <ResponsiveContainer width="100%" height={210}>
        <PieChart>
          <Pie
            data={nonZero}
            dataKey="value"
            nameKey="name"
            innerRadius={52}
            outerRadius={82}
            paddingAngle={2}
            isAnimationActive={false}
            onClick={(d: { name?: string }) => d?.name && onSlice?.(d.name)}
          >
            {nonZero.map((d) => (
              <Cell key={d.name} fill={colors[d.name]} cursor={onSlice ? 'pointer' : 'default'} />
            ))}
          </Pie>
          <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 12 }} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Year-over-year donut pair (current vs prior cycle) + a comparison table.
 *  Used for "Overall Status" and "Auditors details summary" (spec sheet 2 right side). */
export default function YoYPieCards({ title, summary, onCategoryClick, action }: Props) {
  const colors: Record<string, string> = {};
  summary.rows.forEach((r, i) => { colors[r.category] = colorForIndex(i); });

  const current = summary.rows.map((r) => ({ name: r.category, value: r.current }));
  const prior = summary.rows.map((r) => ({ name: r.category, value: r.prior }));

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        {action}
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Donut
          label={summary.current_label}
          data={current}
          colors={colors}
          onSlice={onCategoryClick && ((name) => onCategoryClick(name, summary.review_cycle_id))}
        />
        <Donut
          label={summary.prior_label}
          data={prior}
          colors={colors}
          onSlice={onCategoryClick && ((name) => onCategoryClick(name, summary.prior_review_cycle_id))}
        />
      </div>

      {/* Shared legend */}
      <div className="mt-3 flex flex-wrap justify-center gap-3">
        {summary.rows.map((r) => (
          <span key={r.category} className="inline-flex items-center gap-1.5 text-xs text-gray-600">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: colors[r.category] }} />
            {r.category}
          </span>
        ))}
      </div>

      {/* Comparison table */}
      <div className="mt-4 overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
              <th className="px-3 py-2">Category</th>
              <th className="px-3 py-2 text-right">{summary.current_label}</th>
              <th className="px-3 py-2 text-right">{summary.prior_label}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {summary.rows.map((r) => (
              <tr key={r.category} className="hover:bg-gray-50">
                <td className="px-3 py-2">
                  <button
                    type="button"
                    onClick={() => onCategoryClick?.(r.category, summary.review_cycle_id)}
                    className="flex items-center gap-2 text-left text-gray-800 hover:text-blue-700"
                  >
                    <span className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: colors[r.category] }} />
                    {r.category}
                  </button>
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-gray-800">
                  <button
                    type="button"
                    onClick={() => onCategoryClick?.(r.category, summary.review_cycle_id)}
                    className="hover:text-blue-700 hover:underline"
                  >
                    {fmtInt(r.current)}
                  </button>
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-gray-500">
                  <button
                    type="button"
                    onClick={() => onCategoryClick?.(r.category, summary.prior_review_cycle_id)}
                    className="hover:text-blue-700 hover:underline"
                  >
                    {fmtInt(r.prior)}
                  </button>
                </td>
              </tr>
            ))}
            <tr className="border-t-2 border-gray-200 bg-gray-50 font-semibold text-gray-800">
              <td className="px-3 py-2">Total</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtInt(summary.current_total)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtInt(summary.prior_total)}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
