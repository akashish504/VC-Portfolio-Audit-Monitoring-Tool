import type { ReactNode } from 'react';

import type { DiscrepancySubcategoryMatrix } from '@/api/dashboard';
import { fmtInt } from './dashboardFormat';
import { METRIC_LABEL } from './DiscrepancyPanel';

interface Props {
  matrix: DiscrepancySubcategoryMatrix;
  /** e.g. "> ±10%" — shown in the heading to match the spec. */
  thresholdLabel?: string;
  /** Click a reason count → discrepancy details (remarks, response, closure). */
  onSelect: (metric: string, subcategory: string) => void;
  /** Optional header action (e.g. an XLSX download button). */
  action?: ReactNode;
}

/** Combined metric × discrepancy-reason matrix (spec Included_co__Variance):
 *  one card per metric listing its above-threshold variance reasons + a Total.
 *  Every count is click-through to the entity-level discrepancy details. */
export default function DiscrepancyReasonMatrix({ matrix, thresholdLabel, onSelect, action }: Props) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">
            Discrepancy reasons by metric{thresholdLabel ? ` (${thresholdLabel})` : ''}
          </h2>
          <p className="text-xs text-gray-500">
            Above-threshold variance reasons per metric. Click a number for the discrepancy details
            (our remarks, company response, and how it was closed).
          </p>
        </div>
        {action}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {matrix.metrics.map((m) => (
          <div key={m.metric} className="flex flex-col overflow-hidden rounded-lg border border-gray-200">
            <div className="bg-gray-900 px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-white">
              {METRIC_LABEL[m.metric] ?? m.metric}
            </div>
            <ul className="flex-1 divide-y divide-gray-100">
              {m.rows.length === 0 ? (
                <li className="px-3 py-4 text-center text-xs text-gray-400">No discrepancies</li>
              ) : (
                m.rows.map((r) => (
                  <li key={r.subcategory}>
                    <button
                      type="button"
                      onClick={() => r.count > 0 && onSelect(m.metric, r.subcategory)}
                      disabled={r.count === 0}
                      className="flex w-full items-start justify-between gap-2 px-3 py-1.5 text-left enabled:hover:bg-blue-50 enabled:hover:text-blue-700 disabled:cursor-default"
                    >
                      <span className="text-xs leading-snug text-gray-700">{r.subcategory}</span>
                      <span className="shrink-0 text-xs font-semibold tabular-nums text-gray-900">
                        {fmtInt(r.count)}
                      </span>
                    </button>
                  </li>
                ))
              )}
            </ul>
            <div className="flex items-center justify-between border-t-2 border-gray-200 bg-green-50 px-3 py-1.5 text-xs font-semibold text-gray-800">
              <span>Total</span>
              <span className="tabular-nums">{fmtInt(m.total)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
