import { useEffect, useState } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';

import { getVarianceReportView } from '@/api/dashboard';
import { cn } from '@/lib/utils';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { fmtInt } from './dashboardFormat';
import ReportViewTable from './ReportViewTable';

interface Props {
  reviewCycleId?: string;
}

type Kind = 'long_overdue' | 'previous_years_pending';
const ALL = '__all__';
const PAGE = 25;

// Long-overdue current-status options (spec sheet 3 section i Filter 2).
const STATUS_OPTIONS = [
  'Financials requested', 'Discrepancy Identified', 'Query sent',
  'Query response reminder sent', 'In Progress', 'In Progress - Partial answered',
  'In Progress - call to be scheduled',
];

const TABS: { key: Kind; label: string }[] = [
  { key: 'previous_years_pending', label: 'Previous years Pending' },
  { key: 'long_overdue', label: 'Long overdue companies' },
];

/** "i. REPORT VIEW": Previous-years-pending / Long-overdue lists with pending-since-days. */
export default function ReportViewSection({ reviewCycleId }: Props) {
  const [kind, setKind] = useState<Kind>('previous_years_pending');
  const [status, setStatus] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(0);

  useEffect(() => { setPage(0); }, [kind, status]);

  const query = useQuery({
    queryKey: ['dashboard', 'report-view', kind, status, reviewCycleId, page],
    queryFn: () => getVarianceReportView({
      kind,
      current_status: kind === 'long_overdue' ? status : undefined,
      review_cycle_id: reviewCycleId,
      limit: PAGE,
      offset: page * PAGE,
    }),
    placeholderData: keepPreviousData,
  });

  const total = query.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE));

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <h2 className="mb-3 text-sm font-semibold text-gray-900">Report view</h2>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
          {TABS.map((t) => (
            <button key={t.key} type="button" onClick={() => setKind(t.key)}
              className={cn('rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                kind === t.key ? 'bg-white text-blue-700 shadow-sm' : 'text-gray-500 hover:text-gray-700')}>
              {t.label}
            </button>
          ))}
        </div>

        {kind === 'long_overdue' && (
          <Select value={status ?? ALL} onValueChange={(v) => setStatus(v === ALL ? undefined : v)}>
            <SelectTrigger className="h-9 w-64 bg-white text-sm"><SelectValue placeholder="Current status" /></SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All statuses</SelectItem>
              {STATUS_OPTIONS.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
            </SelectContent>
          </Select>
        )}

        <span className="text-xs text-gray-500">{fmtInt(total)} companies</span>
      </div>

      {query.isLoading ? (
        <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}</div>
      ) : (
        <ReportViewTable rows={query.data?.items ?? []} />
      )}

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
    </div>
  );
}
