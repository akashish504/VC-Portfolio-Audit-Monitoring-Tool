import type {
  TimelineAnalysisCounts,
  TimelineStrategyTable,
} from '@/api/dashboard';
import { fmtIntDash } from './dashboardFormat';
import type { DrillTarget } from './DashboardDrillDownSheet';
import TableDownloadButton from './TableDownloadButton';

interface Props {
  strategyAll: TimelineStrategyTable;
  strategyIncluded: TimelineStrategyTable;
  analysisAll: TimelineAnalysisCounts;
  analysisIncluded: TimelineAnalysisCounts;
  reviewCycleId?: string;
  onDrill?: (target: DrillTarget) => void;
}

/** A numeric cell that drills into the underlying companies when clicked (value > 0). */
function DrillNum({
  value,
  target,
  onDrill,
  bold,
}: {
  value: number;
  target: DrillTarget;
  onDrill?: (t: DrillTarget) => void;
  bold?: boolean;
}) {
  const cls = `tabular-nums ${bold ? 'font-semibold text-gray-800' : ''}`;
  if (!onDrill || value <= 0) return <span className={cls}>{fmtIntDash(value)}</span>;
  return (
    <button
      type="button"
      onClick={() => onDrill(target)}
      className={`${cls} rounded px-1 hover:bg-blue-50 hover:text-blue-700`}
    >
      {fmtIntDash(value)}
    </button>
  );
}

/** Strategy × {India, SEA, Total} unique-company counts, with a summed Total row.
 *  Sourced from the master scoping table: rows = `strategy`, India/SEA = `geo_l1`. */
function StrategyTable({
  title,
  table,
  scopedIn,
  tableKey,
  reviewCycleId,
  onDrill,
}: {
  title: string;
  table: TimelineStrategyTable;
  scopedIn?: boolean;
  tableKey: 'strategy_all' | 'strategy_included';
  reviewCycleId?: string;
  onDrill?: (t: DrillTarget) => void;
}) {
  // Timeline cells count UNIQUE India/SEA companies, so the drill must dedupe by CID
  // and stay within India/SEA to match the displayed number.
  const base: Partial<DrillTarget> = {
    unique: true,
    india_sea_only: true,
    ...(scopedIn ? { scoping_for_audit: true } : {}),
  };
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
        <TableDownloadButton table={tableKey} reviewCycleId={reviewCycleId} />
      </div>
      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-full border-collapse text-sm">
          <thead>
            <tr className="bg-gray-50 text-xs font-medium uppercase tracking-wide text-gray-500">
              <th className="px-3 py-2 text-left">Strategy</th>
              <th className="px-3 py-2 text-right font-medium">India</th>
              <th className="px-3 py-2 text-right font-medium">SEA</th>
              <th className="px-3 py-2 text-right font-semibold text-gray-700">Total</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {table.rows.map((r) => (
              <tr key={r.strategy} className="hover:bg-gray-50/60">
                <td className="px-3 py-2 text-left font-medium text-gray-800">{r.strategy}</td>
                <td className="px-3 py-2 text-right">
                  <DrillNum value={r.india} onDrill={onDrill}
                    target={{ ...base, title: `${r.strategy} · India`, strategy: r.strategy, geo_l1: 'India' }} />
                </td>
                <td className="px-3 py-2 text-right">
                  <DrillNum value={r.sea} onDrill={onDrill}
                    target={{ ...base, title: `${r.strategy} · SEA`, strategy: r.strategy, geo_l1: 'SEA' }} />
                </td>
                {/* Total = India + SEA (a geo sum), not a unique set → non-drillable
                    to avoid a count mismatch; drill India / SEA individually. */}
                <td className="px-3 py-2 text-right font-semibold tabular-nums text-gray-800">
                  {fmtIntDash(r.total)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            {/* The Total row SUMS the strategy rows (a multi-strategy company is counted
                under each), so it is not a unique set — left non-drillable to avoid a
                count mismatch. The deduped totals live in the "for analysis" table. */}
            <tr className="border-t-2 border-gray-200 bg-gray-50 font-semibold text-gray-800">
              <td className="px-3 py-2 text-left">{table.total.strategy}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtIntDash(table.total.india)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtIntDash(table.total.sea)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtIntDash(table.total.total)}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

/** Single-row "for analysis" count: unique companies (deduped across strategies). */
function AnalysisTable({
  title,
  counts,
  scopedIn,
  reviewCycleId,
  onDrill,
  note,
}: {
  title: string;
  counts: TimelineAnalysisCounts;
  scopedIn?: boolean;
  reviewCycleId?: string;
  onDrill?: (t: DrillTarget) => void;
  note?: string;
}) {
  // Timeline cells count UNIQUE India/SEA companies, so the drill must dedupe by CID
  // and stay within India/SEA to match the displayed number.
  const base: Partial<DrillTarget> = {
    unique: true,
    india_sea_only: true,
    ...(scopedIn ? { scoping_for_audit: true } : {}),
  };
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
        <TableDownloadButton table="analysis" reviewCycleId={reviewCycleId} />
      </div>
      <div className="overflow-x-auto rounded-lg border border-gray-200">
        <table className="min-w-full border-collapse text-sm">
          <thead>
            <tr className="bg-gray-50 text-xs font-medium uppercase tracking-wide text-gray-500">
              <th className="px-3 py-2 text-left">&nbsp;</th>
              <th className="px-3 py-2 text-right font-medium">India</th>
              <th className="px-3 py-2 text-right font-medium">SEA</th>
              <th className="px-3 py-2 text-right font-semibold text-gray-700">Total</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="px-3 py-2 text-left font-medium text-gray-800">Unique Count</td>
              <td className="px-3 py-2 text-right">
                <DrillNum value={counts.india} onDrill={onDrill}
                  target={{ ...base, title: 'For analysis · India', geo_l1: 'India' }} />
              </td>
              <td className="px-3 py-2 text-right">
                <DrillNum value={counts.sea} onDrill={onDrill}
                  target={{ ...base, title: 'For analysis · SEA', geo_l1: 'SEA' }} />
              </td>
              {/* Total = India + SEA (a geo sum) → non-drillable; drill India / SEA. */}
              <td className="px-3 py-2 text-right font-semibold tabular-nums text-gray-800">
                {fmtIntDash(counts.total)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      {note && <p className="mt-2 text-xs italic text-gray-400">{note}</p>}
    </div>
  );
}

/** Top section of the Timeline page: strategy-wise unique counts (all vs included)
 *  and the deduped "for analysis" counts. */
export default function TimelineTopTables({
  strategyAll,
  strategyIncluded,
  analysisAll,
  analysisIncluded,
  reviewCycleId,
  onDrill,
}: Props) {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <StrategyTable title="Unique count of all Companies — strategy wise" table={strategyAll}
          tableKey="strategy_all" reviewCycleId={reviewCycleId} onDrill={onDrill} />
        <StrategyTable title="Unique count of Scoped in Companies — strategy wise" table={strategyIncluded}
          scopedIn tableKey="strategy_included" reviewCycleId={reviewCycleId} onDrill={onDrill} />
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <AnalysisTable
          title="Total number of companies for analysis"
          counts={analysisAll}
          reviewCycleId={reviewCycleId}
          onDrill={onDrill}
          note="Unique companies only — a company can sit under multiple strategies, so this will not match the strategy-wise totals above."
        />
        <AnalysisTable
          title="Total number of SCOPED IN companies for analysis"
          counts={analysisIncluded}
          scopedIn
          reviewCycleId={reviewCycleId}
          onDrill={onDrill}
          note="Unique companies that are scoped in for audit."
        />
      </div>
    </div>
  );
}
