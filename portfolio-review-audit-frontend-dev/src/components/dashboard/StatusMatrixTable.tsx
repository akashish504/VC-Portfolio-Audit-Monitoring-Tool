import type { StatusMatrix } from '@/api/dashboard';
import { cn } from '@/lib/utils';
import { fmtInt } from './dashboardFormat';
import type { DrillTarget } from './DashboardDrillDownSheet';

interface Props {
  matrix: StatusMatrix;
  totalLabel?: string;
  onDrill: (target: DrillTarget) => void;
  /** 'actual' adds reminder / request-financials hand-off buttons for overdue / due-now rows.
   *  'deal_stage_1' / 'deal_stage_2' drill into the master-scoping deal list instead. */
  variant?: 'tentative' | 'actual' | 'deal_stage_1' | 'deal_stage_2';
  reviewCycleId?: string;
}

/** Status (rows) × FYE-month (columns) count matrix with a totals row.
 *  Cells, row totals, and column totals are click-through to the drill-down sheet
 *  (which shows the completion timeline + the company list). */
export default function StatusMatrixTable({
  matrix, totalLabel = 'Total', onDrill, variant = 'tentative', reviewCycleId,
}: Props) {
  const { months, rows, column_totals, grand_total } = matrix;

  const actionsFor = (status?: string): DrillTarget['actions'] => {
    if (variant !== 'actual' || !status) return undefined;
    const s = status.toLowerCase();
    const ctx = new URLSearchParams({
      from: 'audit-dashboard', status, ...(reviewCycleId ? { cycle: reviewCycleId } : {}),
    }).toString();
    if (s.includes('overdue')) {
      return [{ label: 'Send reminder', to: `/communications?action=reminder&${ctx}`, icon: 'reminder' }];
    }
    if (s.includes('due now')) {
      return [{ label: 'Request financials', to: `/communications?action=request-financials&${ctx}`, icon: 'financials' }];
    }
    return undefined;
  };

  // Every variant of this matrix is backed by the master-scoping deal list
  // (PortfolioCompanyMetadata): tentative / actual / deal_stage_1 → deal_level_stage_1,
  // deal_stage_2 → deal_level_stage_2. The scoping-status matrix (tentative/actual) is
  // the scoped-in cohort, so carry scoping_for_audit. `status` is kept for the
  // reminder / request-financials hand-off context.
  const scopedIn = variant === 'tentative' || variant === 'actual';
  const drill = (t: DrillTarget) => {
    let precise: Partial<DrillTarget> = {};
    if (t.status) {
      precise =
        variant === 'deal_stage_2'
          ? { deal_level_stage_2: t.status }
          : { deal_level_stage_1: t.status };
    }
    onDrill({
      ...t,
      ...precise,
      ...(scopedIn ? { scoping_for_audit: true } : {}),
      pcm: true,
      actions: actionsFor(t.status),
    });
  };

  const cellBtn =
    'w-full rounded px-1 py-0.5 text-right tabular-nums transition-colors hover:bg-blue-50 hover:text-blue-700';

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="min-w-full border-collapse text-sm">
        <thead>
          <tr className="bg-gray-50 text-xs font-medium uppercase tracking-wide text-gray-500">
            <th className="sticky left-0 z-10 bg-gray-50 px-3 py-2 text-left">Status</th>
            {months.map((m) => (
              <th key={m} className="px-3 py-2 text-right font-medium">{m}</th>
            ))}
            <th className="px-3 py-2 text-right font-semibold text-gray-700">Total</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((row) => (
            <tr key={row.status} className="hover:bg-gray-50/60">
              <td className="sticky left-0 z-10 bg-white px-3 py-2 text-left font-medium text-gray-800">
                {row.status}
              </td>
              {months.map((m) => {
                const v = row.by_month[m] ?? 0;
                return (
                  <td key={m} className="px-2 py-1 text-right">
                    {v > 0 ? (
                      <button
                        type="button"
                        className={cellBtn}
                        onClick={() =>
                          drill({ title: `${row.status} · ${m}`, status: row.status, fy_end: m })
                        }
                      >
                        {fmtInt(v)}
                      </button>
                    ) : (
                      <span className="px-1 text-gray-300">0</span>
                    )}
                  </td>
                );
              })}
              <td className="px-2 py-1 text-right">
                <button
                  type="button"
                  className={cn(cellBtn, 'font-semibold text-gray-800')}
                  onClick={() => drill({ title: row.status, status: row.status })}
                >
                  {fmtInt(row.total)}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-gray-200 bg-gray-50 font-semibold text-gray-800">
            <td className="sticky left-0 z-10 bg-gray-50 px-3 py-2 text-left">{totalLabel}</td>
            {months.map((m) => {
              const v = column_totals[m] ?? 0;
              return (
                <td key={m} className="px-2 py-1 text-right">
                  {v > 0 ? (
                    <button
                      type="button"
                      className={cn(cellBtn, 'font-semibold')}
                      onClick={() => drill({ title: `All companies · ${m}`, fy_end: m })}
                    >
                      {fmtInt(v)}
                    </button>
                  ) : (
                    <span className="px-1 text-gray-300">0</span>
                  )}
                </td>
              );
            })}
            <td className="px-3 py-2 text-right tabular-nums">{fmtInt(grand_total)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
