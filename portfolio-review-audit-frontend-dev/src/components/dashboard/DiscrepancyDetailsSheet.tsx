import { useEffect, useState } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';

import { getVarianceDiscrepancyCompanies } from '@/api/dashboard';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { fmtInt } from './dashboardFormat';
import { METRIC_LABEL } from './DiscrepancyPanel';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  metric: string | null;
  bucket: 'gt' | 'lt' | 'nc' | null;
  subcategory?: string | null;
  reviewCycleId?: string;
}

const BUCKET_LABEL: Record<string, string> = { gt: 'Above threshold', lt: 'Within threshold', nc: 'Not comparable' };
const PAGE = 25;

function pct(v: number | null): string {
  if (v === null || Number.isNaN(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}
function num(v: number | null): string {
  if (v === null || Number.isNaN(v)) return '—';
  return v.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

/** Company/entity-level discrepancy details: MIS vs AFS, %, sub-category, our remarks,
 *  company response and status — the "details of discrepancy" the workbook drills to. */
export default function DiscrepancyDetailsSheet({ open, onOpenChange, metric, bucket, subcategory, reviewCycleId }: Props) {
  const [page, setPage] = useState(0);
  useEffect(() => { setPage(0); }, [metric, bucket, subcategory]);

  const query = useQuery({
    queryKey: ['dashboard', 'discrepancy-companies', metric, bucket, subcategory, reviewCycleId, page],
    queryFn: () => getVarianceDiscrepancyCompanies({
      metric: metric ?? undefined,
      bucket: bucket ?? undefined,
      subcategory: subcategory ?? undefined,
      review_cycle_id: reviewCycleId,
      limit: PAGE,
      offset: page * PAGE,
    }),
    enabled: open && !!metric,
    placeholderData: keepPreviousData,
  });

  const rows = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE));
  const label = metric ? METRIC_LABEL[metric] ?? metric : '';

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-4xl">
        <SheetHeader>
          <SheetTitle>
            {label} discrepancies — {bucket ? BUCKET_LABEL[bucket] : ''}{subcategory ? ` · ${subcategory}` : ''}
          </SheetTitle>
          <SheetDescription>
            MIS vs AFS, our remarks, company response and status.{' '}
            <span className="font-medium text-gray-700">{fmtInt(total)} entities</span>
          </SheetDescription>
        </SheetHeader>

        <div className="mt-4">
          {query.isLoading ? (
            <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}</div>
          ) : query.isError ? (
            <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">Failed to load discrepancy details.</div>
          ) : rows.length === 0 ? (
            <div className="py-8 text-center text-sm text-gray-500">No discrepancies match this selection.</div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-gray-200">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                    {['Company', 'Entity', 'AFS', 'MIS', 'Diff %', 'Sub-category', 'Our remarks', 'Company response', 'Status'].map((c) => (
                      <th key={c} className="whitespace-nowrap px-3 py-2">{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {rows.map((r, i) => (
                    <tr key={`${r.portfolio_company_id}-${r.entity_id}-${i}`} className="align-top hover:bg-gray-50">
                      <td className="whitespace-nowrap px-3 py-2 font-medium text-gray-900">{r.company}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-gray-600">{r.entity ?? '—'}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{num(r.afs_amount)}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-700">{num(r.mis_amount)}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-gray-800">{pct(r.diff_pct)}</td>
                      <td className="px-3 py-2 text-gray-600">{r.variance_category ?? '—'}</td>
                      <td className="max-w-xs px-3 py-2 text-gray-600">{r.reviewer_remarks ?? '—'}</td>
                      <td className="max-w-xs px-3 py-2 text-gray-600">{r.company_response ?? '—'}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-gray-700">{r.status ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {total > PAGE && (
          <div className="mt-3 flex items-center justify-between text-sm text-gray-600">
            <span>Page {page + 1} of {pageCount}</span>
            <div className="flex gap-2">
              <button type="button" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
                className="rounded-md border border-gray-300 px-3 py-1 hover:bg-gray-50 disabled:opacity-50">Previous</button>
              <button type="button" onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))} disabled={page >= pageCount - 1}
                className="rounded-md border border-gray-300 px-3 py-1 hover:bg-gray-50 disabled:opacity-50">Next</button>
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
