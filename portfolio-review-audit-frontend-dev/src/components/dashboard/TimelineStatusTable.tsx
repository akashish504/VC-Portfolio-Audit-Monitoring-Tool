import type { TimelineStatusTable as StatusTable, TimelineStatusRow } from '@/api/dashboard';
import { fmtIntDash, statusTone } from './dashboardFormat';
import type { DrillTarget } from './DashboardDrillDownSheet';
import TableDownloadButton from './TableDownloadButton';

interface Props {
  table: StatusTable;
  reviewCycleId?: string;
  onDrill?: (target: DrillTarget) => void;
}

type ColKey = 'overall' | string | 'india' | 'sea';

/** Resolve a status row's value for a given column key (Overall / strategy / India / SEA). */
function cellValue(row: TimelineStatusRow, col: ColKey): number {
  if (col === 'overall') return row.overall;
  if (col === 'india') return row.india;
  if (col === 'sea') return row.sea;
  return row.by_strategy?.[col] ?? 0;
}

function pct(n: number, denom: number): string {
  if (!denom || !n) return '—';
  return `${((n / denom) * 100).toFixed(1)}%`;
}

/** Audit-timeline status × {Overall, per-strategy, India, SEA} — the count table plus
 *  its percentage twin. Count cells drill into the scoped-in companies behind them. */
export default function TimelineStatusTable({ table, reviewCycleId, onDrill }: Props) {
  const { strategies, rows, total } = table;
  const cols: { key: ColKey; label: string; accent?: boolean }[] = [
    { key: 'overall', label: 'Overall', accent: true },
    ...strategies.map((s) => ({ key: s as ColKey, label: s })),
    { key: 'india', label: 'India' },
    { key: 'sea', label: 'SEA' },
  ];

  // Build the master-scoping drill target for a (status, column) cell. The whole table
  // is the scoped-in cohort; a null `status` (Total row) drops the stage filter.
  const buildTarget = (status: string | null, col: ColKey, label: string): DrillTarget => {
    // Counts are unique India/SEA scoped-in companies → dedupe by CID, India/SEA only.
    const t: DrillTarget = { title: label, scoping_for_audit: true, unique: true, india_sea_only: true };
    if (status) t.deal_level_stage_1 = status;
    if (col === 'india') t.geo_l1 = 'India';
    else if (col === 'sea') t.geo_l1 = 'SEA';
    else if (col !== 'overall') t.strategy = col;
    return t;
  };

  const Header = ({ first }: { first: string }) => (
    <thead>
      <tr className="bg-gray-50 text-xs font-medium uppercase tracking-wide text-gray-500">
        <th className="sticky left-0 z-10 bg-gray-50 px-3 py-2 text-left">{first}</th>
        {cols.map((c) => (
          <th
            key={c.label}
            className={`px-3 py-2 text-right font-medium ${c.accent ? 'text-gray-700' : ''}`}
          >
            {c.label}
          </th>
        ))}
      </tr>
    </thead>
  );

  const CountCell = ({ status, col, label, accent }: { status: string | null; col: ColKey; label: string; accent?: boolean }) => {
    const v = cellValue(status === null ? total : rows.find((r) => r.status === status)!, col);
    const cls = `tabular-nums ${accent ? 'font-semibold text-gray-800' : ''}`;
    if (v <= 0) return <span className="text-gray-300">—</span>;
    if (!onDrill) return <span className={cls}>{fmtIntDash(v)}</span>;
    return (
      <button
        type="button"
        onClick={() => onDrill(buildTarget(status, col, `${status ?? 'All statuses'} · ${label}`))}
        className={`${cls} rounded px-1 hover:bg-blue-50 hover:text-blue-700`}
      >
        {fmtIntDash(v)}
      </button>
    );
  };

  return (
    <div className="space-y-5">
      <div className="flex justify-end">
        <TableDownloadButton table="status" reviewCycleId={reviewCycleId} />
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-full border-collapse text-sm">
          <Header first="Status" />
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
                {cols.map((c) => (
                  <td key={c.label} className="px-3 py-2 text-right">
                    <CountCell status={r.status} col={c.key} label={c.label} accent={c.accent} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-gray-200 bg-gray-50 font-semibold text-gray-800">
              <td className="sticky left-0 z-10 bg-gray-50 px-3 py-2 text-left">Total</td>
              {cols.map((c) => (
                <td key={c.label} className="px-3 py-2 text-right">
                  <CountCell status={null} col={c.key} label={c.label} accent />
                </td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-full border-collapse text-sm">
          <Header first="%" />
          <tbody className="divide-y divide-gray-100">
            {rows.map((r) => (
              <tr key={r.status} className="hover:bg-gray-50/60">
                <td className="sticky left-0 z-10 bg-white px-3 py-2 text-left font-medium text-gray-700">
                  {r.status}
                </td>
                {cols.map((c) => (
                  <td
                    key={c.label}
                    className={`px-3 py-2 text-right tabular-nums text-gray-600 ${c.accent ? 'font-semibold text-gray-800' : ''}`}
                  >
                    {pct(cellValue(r, c.key), cellValue(total, c.key))}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
