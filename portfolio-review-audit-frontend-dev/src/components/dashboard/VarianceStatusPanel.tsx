import { useState } from 'react';
import { Download } from 'lucide-react';

import { downloadVarianceStatusBreakdown, type VarianceActionables, type VarianceCycleSummary } from '@/api/dashboard';
import { fmtInt, statusTone } from './dashboardFormat';
import { cn } from '@/lib/utils';
import type { DrillTarget } from './DashboardDrillDownSheet';

interface Props {
  cycle: VarianceCycleSummary;
  actionables: VarianceActionables;
  onDrill: (target: DrillTarget) => void;
}

/** Current-cycle status breakdown (proportion bars) + long-overdue actionables list.
 *  Every status row is click-through to the company list (spec F42). Counts are
 *  distinct deals, matching the drill-down; the XLSX export lists those deals. */
export default function VarianceStatusPanel({ cycle, actionables, onDrill }: Props) {
  const max = Math.max(1, ...cycle.status_summary.map((s) => s.count));
  const [downloading, setDownloading] = useState(false);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const { blob, filename } = await downloadVarianceStatusBreakdown({
        review_cycle_id: cycle.review_cycle_id ?? undefined,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // non-critical — surfaced via the global API error handler
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm lg:col-span-2">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-gray-900">Current-cycle status</h2>
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-500">{fmtInt(cycle.total)} companies</span>
            <button
              type="button"
              onClick={() => void handleDownload()}
              disabled={downloading}
              className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Download className="h-3.5 w-3.5" />
              {downloading ? 'Preparing…' : 'Download XLSX'}
            </button>
          </div>
        </div>
        <div className="space-y-2">
          {cycle.status_summary.map((s) => (
            <button
              key={s.status}
              type="button"
              onClick={() => onDrill({ title: s.status, entity_status: s.status })}
              className="flex w-full items-center gap-3 rounded-md px-1 py-0.5 text-left hover:bg-gray-50"
            >
              <div className="w-56 shrink-0 text-sm text-gray-700">{s.status}</div>
              <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-gray-100">
                <div className="h-full rounded-full bg-blue-500" style={{ width: `${(s.count / max) * 100}%` }} />
              </div>
              <div className="w-12 shrink-0 text-right text-sm font-medium tabular-nums text-gray-800">
                {fmtInt(s.count)}
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold text-gray-900">Long-overdue actionables</h2>
          <span className="text-xs text-gray-500">{fmtInt(actionables.total)} total</span>
        </div>
        <div className="space-y-2">
          {actionables.rows.map((r) => (
            <button
              key={r.status}
              type="button"
              onClick={() => onDrill({ title: `${r.status} (long overdue)`, entity_status: r.status })}
              className="flex w-full items-center justify-between gap-2 rounded-md px-1 py-0.5 hover:bg-gray-50"
            >
              <span className={cn('rounded-full px-2.5 py-0.5 text-xs font-medium', statusTone(r.status))}>
                {r.status}
              </span>
              <span className="text-sm font-semibold tabular-nums text-gray-800">{fmtInt(r.count)}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
