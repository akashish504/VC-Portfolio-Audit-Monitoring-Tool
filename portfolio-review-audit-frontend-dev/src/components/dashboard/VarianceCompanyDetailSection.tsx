import { type ReactNode, useState } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';

import {
  getVarianceCompanyDetails,
  type VarianceCompanyDetailRow,
  type MetricCell,
} from '@/api/dashboard';
import { Skeleton } from '@/components/ui/skeleton';
import { displayOrDash, statusTone } from './dashboardFormat';
import { cn } from '@/lib/utils';

const PAGE_SIZE = 25;
const METRICS = ['revenue', 'ebitda', 'pbt', 'pat', 'cash', 'debt'] as const;
const METRIC_LABEL: Record<string, string> = {
  revenue: 'Revenue', ebitda: 'EBITDA', pbt: 'PBT', pat: 'PAT', cash: 'Cash', debt: 'Debt',
};

const num = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 3 });
const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`;
const days = (v: number | null | undefined) => (v === null || v === undefined ? '—' : String(v));
const cell = (r: VarianceCompanyDetailRow, m: string): MetricCell | undefined => r.metrics.find((x) => x.metric === m);

/** Scalar (non-metric) columns, in spec order. */
interface ColDef {
  label: string;
  render: (r: VarianceCompanyDetailRow) => ReactNode;
  right?: boolean;
}

const COLS: ColDef[] = [
  { label: 'No', render: (r) => r.no, right: true },
  { label: 'CID', render: (r) => displayOrDash(r.cid) },
  { label: 'Company', render: (r) => <span className="font-medium text-gray-900">{r.company}</span> },
  { label: 'Legal Name', render: (r) => displayOrDash(r.legal_name) },
  { label: 'Entity', render: (r) => displayOrDash(r.entity) },
  { label: 'Currency', render: (r) => displayOrDash(r.currency) },
  { label: 'Holding/Sub', render: (r) => displayOrDash(r.holding_or_subsidiary) },
  { label: 'Consol/Standalone', render: (r) => displayOrDash(r.consolidated_or_standalone) },
  { label: 'Date of signing', render: (r) => displayOrDash(r.date_of_signing) },
  {
    label: 'Current Status',
    render: (r) =>
      r.current_status ? (
        <span className={cn('inline-flex rounded-full px-2 py-0.5 text-xs font-medium', statusTone(r.current_status))}>
          {r.current_status}
        </span>
      ) : '—',
  },
  { label: 'Financials added', render: (r) => displayOrDash(r.financials_added) },
  { label: 'In Review', render: (r) => displayOrDash(r.in_review) },
  { label: 'TAT (Review)', render: (r) => days(r.tat_review), right: true },
  { label: 'Queries sent', render: (r) => displayOrDash(r.queries_sent) },
  { label: 'Reminder 1', render: (r) => displayOrDash(r.reminder_1) },
  { label: 'Reminder 2', render: (r) => displayOrDash(r.reminder_2) },
  { label: 'TAT (Queries)', render: (r) => days(r.tat_queries_sent), right: true },
  { label: 'Responded', render: (r) => displayOrDash(r.responded) },
  { label: 'TAT (Response)', render: (r) => days(r.tat_response), right: true },
  { label: 'Approved/Rejected', render: (r) => displayOrDash(r.approved_rejected_on) },
  { label: 'TAT (Approval)', render: (r) => days(r.tat_approval), right: true },
  { label: 'Overall TAT', render: (r) => days(r.overall_tat), right: true },
  {
    label: 'Highlighted',
    render: (r) => (r.highlighted_to_investor ? <span className="font-medium text-blue-700">Yes</span> : '—'),
  },
  { label: 'Reporting Standards', render: (r) => displayOrDash(r.reporting_standards) },
  { label: 'Auditor', render: (r) => displayOrDash(r.auditor_name) },
  { label: 'Partner', render: (r) => displayOrDash(r.auditor_partner) },
  { label: 'Auditor Cat.', render: (r) => displayOrDash(r.auditor_category) },
  { label: 'Status of Financials', render: (r) => displayOrDash(r.status_of_financials) },
  { label: 'Signed Status', render: (r) => displayOrDash(r.status_of_signed_financials) },
  { label: 'Audit Report Status', render: (r) => displayOrDash(r.audit_report_status) },
  { label: 'Opinion', render: (r) => displayOrDash(r.auditor_opinion) },
  { label: 'Emphasis of Matter', render: (r) => displayOrDash(r.emphasis_of_matter) },
  { label: 'Other Matters', render: (r) => displayOrDash(r.other_matters) },
  { label: 'Going Concern', render: (r) => displayOrDash(r.going_concern) },
  { label: 'CARO Avail.', render: (r) => displayOrDash(r.caro_availability) },
  { label: 'CARO Gaps', render: (r) => displayOrDash(r.caro_gaps) },
  { label: 'CARO Notes', render: (r) => displayOrDash(r.caro_notes) },
  { label: 'IFC', render: (r) => displayOrDash(r.internal_financial_control) },
  { label: 'IFC Gaps', render: (r) => displayOrDash(r.ifc_gaps) },
];

const OVERALL_COLS: ColDef[] = [
  { label: 'Overall comments', render: (r) => displayOrDash(r.overall_comments) },
  { label: 'Overall status', render: (r) => displayOrDash(r.overall_status) },
  {
    label: 'Flagged (overdue trend)',
    render: (r) => (r.flagged_continuous_overdue ? <span className="font-medium text-red-700">⚑ Flagged</span> : '—'),
  },
];

/** Per-metric sub-columns. */
const METRIC_SUBCOLS: { key: keyof MetricCell; label: string; fmt: (m: MetricCell) => ReactNode; right?: boolean }[] = [
  { key: 'afs', label: 'AFS', fmt: (m) => num(m.afs), right: true },
  { key: 'mis', label: 'MIS', fmt: (m) => num(m.mis), right: true },
  { key: 'diff_pct', label: 'Diff%', fmt: (m) => pct(m.diff_pct), right: true },
  {
    key: 'bucket',
    label: 'Bucket',
    fmt: (m) =>
      m.bucket ? (
        <span className={cn('text-xs font-medium', m.bucket === '>10%' ? 'text-red-600' : m.bucket === '<10%' ? 'text-green-600' : 'text-gray-400')}>
          {m.bucket}
        </span>
      ) : '—',
  },
  { key: 'status', label: 'Status', fmt: (m) => displayOrDash(m.status) },
  { key: 'subcategory', label: 'Sub-cat', fmt: (m) => displayOrDash(m.subcategory) },
];

interface Props {
  reviewCycleId?: string;
}

/** Per-entity master detail table for included (scoped-in) companies — spec sheet 3 top table. */
export default function VarianceCompanyDetailSection({ reviewCycleId }: Props) {
  const [page, setPage] = useState(0);

  const query = useQuery({
    queryKey: ['dashboard', 'variance-company-details', reviewCycleId, page],
    queryFn: () => getVarianceCompanyDetails({ review_cycle_id: reviewCycleId, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  const rows = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-gray-900">Included companies — detail</h2>
        <span className="text-xs text-gray-500">{total} entities (financials uploaded)</span>
      </div>
      <p className="mb-3 text-xs text-gray-500">
        Per-entity review workflow (TATs), compliance and metric-level reconciliation for scoped-in companies.
      </p>

      {query.isLoading ? (
        <Skeleton className="h-48 w-full rounded-lg" />
      ) : query.isError ? (
        <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">Failed to load company details.</div>
      ) : rows.length === 0 ? (
        <div className="px-4 py-8 text-center text-sm text-gray-500">No companies have financials uploaded for this cycle yet.</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="text-sm">
            <thead>
              {/* group row */}
              <tr className="bg-gray-100 text-left text-[11px] font-semibold uppercase tracking-wide text-gray-500">
                <th className="border-r border-gray-200 px-2 py-1.5" colSpan={COLS.length}>Company · Review · Compliance</th>
                {METRICS.map((m) => (
                  <th key={m} className="border-r border-gray-200 px-2 py-1.5 text-center" colSpan={METRIC_SUBCOLS.length}>
                    {METRIC_LABEL[m]}
                  </th>
                ))}
                <th className="px-2 py-1.5" colSpan={OVERALL_COLS.length}>Overall</th>
              </tr>
              {/* column row */}
              <tr className="bg-gray-50 text-left text-[11px] font-medium uppercase tracking-wide text-gray-500">
                {COLS.map((c) => (
                  <th key={c.label} className={cn('whitespace-nowrap px-2 py-1.5', c.right && 'text-right')}>{c.label}</th>
                ))}
                {METRICS.map((m) =>
                  METRIC_SUBCOLS.map((sc) => (
                    <th key={`${m}-${sc.label}`} className={cn('whitespace-nowrap px-2 py-1.5', sc.right && 'text-right')}>{sc.label}</th>
                  )),
                )}
                {OVERALL_COLS.map((c) => (
                  <th key={c.label} className="whitespace-nowrap px-2 py-1.5">{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((r) => (
                <tr key={`${r.portfolio_company_id}-${r.entity_id ?? r.no}`} className="hover:bg-gray-50">
                  {COLS.map((c) => (
                    <td key={c.label} className={cn('whitespace-nowrap px-2 py-1.5 text-gray-700', c.right && 'text-right tabular-nums')}>
                      {c.render(r)}
                    </td>
                  ))}
                  {METRICS.map((m) => {
                    const mc = cell(r, m);
                    return METRIC_SUBCOLS.map((sc) => (
                      <td key={`${m}-${sc.label}`} className={cn('whitespace-nowrap px-2 py-1.5 text-gray-700', sc.right && 'text-right tabular-nums')}>
                        {mc ? sc.fmt(mc) : '—'}
                      </td>
                    ));
                  })}
                  {OVERALL_COLS.map((c) => (
                    <td key={c.label} className="whitespace-nowrap px-2 py-1.5 text-gray-700">{c.render(r)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-between text-sm text-gray-600">
          <span>Page {page + 1} of {pageCount}</span>
          <div className="flex gap-2">
            <button type="button" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
              className="rounded-md border border-gray-300 px-3 py-1 disabled:opacity-50 hover:bg-gray-50">Previous</button>
            <button type="button" onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))} disabled={page >= pageCount - 1}
              className="rounded-md border border-gray-300 px-3 py-1 disabled:opacity-50 hover:bg-gray-50">Next</button>
          </div>
        </div>
      )}
    </div>
  );
}
