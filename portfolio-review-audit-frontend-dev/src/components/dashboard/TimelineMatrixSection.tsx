import { useState } from 'react';

import type { TimelineMatrix } from '@/api/dashboard';
import { cn } from '@/lib/utils';
import { fmtIntDash, statusTone } from './dashboardFormat';
import type { DrillTarget } from './DashboardDrillDownSheet';
import TableDownloadButton from './TableDownloadButton';

/** Tab label for a scope ("Overall" / a strategy). */
function scopeLabel(scope: string): string {
  return scope === 'Overall' ? 'Overall' : scope;
}

interface Props {
  matrices: TimelineMatrix[];
  reviewCycleId?: string;
  onDrill?: (target: DrillTarget) => void;
}

/** Base master-scoping filters for a (scope, geo) slice of the cross-tabs.
 *  Cells count unique India/SEA scoped-in companies → dedupe by CID, India/SEA only. */
function sliceBase(scope: string, geo: string): DrillTarget {
  const t: DrillTarget = { title: '', scoping_for_audit: true, unique: true, india_sea_only: true };
  if (scope !== 'Overall') t.strategy = scope;
  if (geo === 'India' || geo === 'SEA') t.geo_l1 = geo;
  return t;
}

/** One audit-timeline cross-tab: status (rows) × FYE-month (cols) + Grand Total. */
function MatrixTable({
  matrix,
  onDrill,
}: {
  matrix: TimelineMatrix;
  onDrill?: (t: DrillTarget) => void;
}) {
  const { months, rows, column_totals, grand_total } = matrix;
  const base = sliceBase(matrix.scope, matrix.geo);

  const Cell = ({ value, target }: { value: number; target: DrillTarget }) => {
    if (value <= 0) return <span className="text-gray-300">—</span>;
    if (!onDrill) return <span className="tabular-nums">{fmtIntDash(value)}</span>;
    return (
      <button
        type="button"
        onClick={() => onDrill(target)}
        className="tabular-nums rounded px-1 hover:bg-blue-50 hover:text-blue-700"
      >
        {fmtIntDash(value)}
      </button>
    );
  };

  return (
    <div>
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
        {matrix.geo}
      </div>
      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-full border-collapse text-sm">
          <thead>
            <tr className="bg-gray-50 text-xs font-medium uppercase tracking-wide text-gray-500">
              <th className="sticky left-0 z-10 bg-gray-50 px-3 py-2 text-left">Status</th>
              {months.map((m) => (
                <th key={m} className="px-3 py-2 text-right font-medium">
                  {m}
                </th>
              ))}
              <th className="px-3 py-2 text-right font-semibold text-gray-700">Grand Total</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((r) => (
              <tr key={r.status} className="hover:bg-gray-50/60">
                <td className="sticky left-0 z-10 bg-white px-3 py-2 text-left">
                  <span
                    className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${statusTone(r.status)}`}
                  >
                    {r.status}
                  </span>
                </td>
                {months.map((m) => (
                  <td key={m} className="px-3 py-2 text-right">
                    <Cell
                      value={r.by_month[m] ?? 0}
                      target={{ ...base, title: `${r.status} · ${m}`, deal_level_stage_1: r.status, fy_end: m }}
                    />
                  </td>
                ))}
                <td className="px-3 py-2 text-right font-semibold text-gray-800">
                  <Cell
                    value={r.total}
                    target={{ ...base, title: r.status, deal_level_stage_1: r.status }}
                  />
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-gray-200 bg-gray-50 font-semibold text-gray-800">
              <td className="sticky left-0 z-10 bg-gray-50 px-3 py-2 text-left">Grand Total</td>
              {months.map((m) => (
                <td key={m} className="px-3 py-2 text-right">
                  <Cell
                    value={column_totals[m] ?? 0}
                    target={{ ...base, title: `All statuses · ${m}`, fy_end: m }}
                  />
                </td>
              ))}
              <td className="px-3 py-2 text-right">
                <Cell value={grand_total} target={{ ...base, title: 'All statuses' }} />
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

/** Audit-timeline cross-tabs grouped by scope (Overall / per-strategy), each with its
 *  India-and-SEA / India / SEA breakdown. */
export default function TimelineMatrixSection({ matrices, reviewCycleId, onDrill }: Props) {
  // Hooks must run unconditionally (before any early return). Switch between scopes
  // (Overall / Seed / Growth / Venture) instead of stacking every scope vertically.
  const [active, setActive] = useState<string | null>(null);

  if (matrices.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-gray-200 px-3 py-6 text-center text-sm text-gray-400">
        No audit-timeline data for this cycle.
      </div>
    );
  }

  // Preserve backend scope order (Overall first, then strategies).
  const scopes: string[] = [];
  const byScope = new Map<string, TimelineMatrix[]>();
  for (const m of matrices) {
    if (!byScope.has(m.scope)) {
      byScope.set(m.scope, []);
      scopes.push(m.scope);
    }
    byScope.get(m.scope)!.push(m);
  }

  const scope = active && scopes.includes(active) ? active : scopes[0];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex flex-wrap rounded-lg border border-gray-200 bg-gray-50 p-0.5">
          {scopes.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setActive(s)}
              className={cn(
                'rounded-md px-4 py-1.5 text-sm font-medium transition-colors',
                scope === s ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-gray-700',
              )}
            >
              {scopeLabel(s)}
            </button>
          ))}
        </div>
        <TableDownloadButton table="matrices" reviewCycleId={reviewCycleId} />
      </div>

      <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <h3 className="mb-4 text-center text-sm font-bold text-gray-900">
          Audit timeline : {scope === 'Overall' ? 'OVERALL · SCOPED IN' : scope.toUpperCase()}
        </h3>
        <div className="space-y-5">
          {byScope.get(scope)!.map((m) => (
            <MatrixTable key={`${m.scope}-${m.geo}`} matrix={m} onDrill={onDrill} />
          ))}
        </div>
      </div>
    </div>
  );
}
