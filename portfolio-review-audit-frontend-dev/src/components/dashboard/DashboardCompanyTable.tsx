import type { DashboardCompanyRow } from '@/api/dashboard';
import { displayOrDash, statusTone } from './dashboardFormat';
import { cn } from '@/lib/utils';

interface Props {
  rows: DashboardCompanyRow[];
  loading?: boolean;
}

const COLS = [
  'Company',
  'Fund',
  'Geography',
  'FYE',
  'Strategy',
  'IL 1',
  'IL 2',
  'Cons. Cost',
  'Cons. FMV',
  'Category',
  'Status',
  'Last yr audit',
] as const;

/** Presentational company list table (spec sections iii/iv). Data is supplied by the caller. */
export default function DashboardCompanyTable({ rows, loading }: Props) {
  if (!loading && rows.length === 0) {
    return <div className="px-4 py-10 text-center text-sm text-gray-500">No companies match the current filters.</div>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
            {COLS.map((c) => (
              <th key={c} className="whitespace-nowrap px-3 py-2">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((r) => {
            // "Status" = the review/audit-process status (entities.status, per spec
            // "Audit status" = In-review tracker), falling back to the scoping label.
            const status = r.review_stage || r.in_review_status || r.audit_status || '';
            return (
              <tr key={r.portfolio_company_id} className="hover:bg-gray-50">
                <td className="whitespace-nowrap px-3 py-2 font-medium text-gray-900">
                  {r.company_name}
                  {r.flagged_continuous_overdue && (
                    <span
                      title="Flagged: continuous overdue (past-year trend)"
                      className="ml-1.5 rounded bg-red-50 px-1 text-xs font-semibold text-red-700"
                    >
                      ⚑
                    </span>
                  )}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.fund)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.geography)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.fy_end)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.strategy)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.investment_lead_1)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.investment_lead_2)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{displayOrDash(r.consolidated_cost)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{displayOrDash(r.consolidated_fmv)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.latest_category)}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  {status ? (
                    <span className={cn('inline-flex rounded-full px-2 py-0.5 text-xs font-medium', statusTone(status))}>
                      {status}
                    </span>
                  ) : (
                    <span className="text-gray-400">—</span>
                  )}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-gray-600">{displayOrDash(r.last_year_audit_status)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
