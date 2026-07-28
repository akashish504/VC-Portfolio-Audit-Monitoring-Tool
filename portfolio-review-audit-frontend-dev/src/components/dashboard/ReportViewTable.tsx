import type { ReportViewRow } from '@/api/dashboard';
import { displayOrDash, fmtInt, statusTone } from './dashboardFormat';
import { cn } from '@/lib/utils';

interface Props {
  rows: ReportViewRow[];
}

const COLS = [
  'No', 'CID', 'Company', 'FYE', 'Current Status', 'Strategy', 'Category',
  'Cons. Cost', 'Cons. FMV', 'IL 1', 'IL 2', 'Pending (days)', 'Auditor', 'Auditor Cat.',
] as const;

/** Report-view table (spec sheet 3 section i): pending / long-overdue companies with
 *  pending-since-days, auditor and auditor category. */
export default function ReportViewTable({ rows }: Props) {
  if (rows.length === 0) {
    return <div className="px-4 py-8 text-center text-sm text-gray-500">No companies match the current filters.</div>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
            {COLS.map((c) => <th key={c} className="whitespace-nowrap px-3 py-2">{c}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((r) => (
            <tr key={`${r.no}-${r.cid}`} className="hover:bg-gray-50">
              <td className="px-3 py-2 tabular-nums text-gray-500">{r.no}</td>
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.cid)}</td>
              <td className="whitespace-nowrap px-3 py-2 font-medium text-gray-900">{r.company}</td>
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.fy_end)}</td>
              <td className="whitespace-nowrap px-3 py-2">
                {r.current_status ? (
                  <span className={cn('inline-flex rounded-full px-2 py-0.5 text-xs font-medium', statusTone(r.current_status))}>
                    {r.current_status}
                  </span>
                ) : <span className="text-gray-400">—</span>}
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.strategy)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.category)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{displayOrDash(r.consolidated_cost)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{displayOrDash(r.consolidated_fmv)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.investment_lead_1)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.investment_lead_2)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums font-medium text-gray-800">
                {r.pending_since_days === null ? '—' : fmtInt(r.pending_since_days)}
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.auditor)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.auditor_category)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
