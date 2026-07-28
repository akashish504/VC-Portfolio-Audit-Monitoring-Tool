import { useQuery } from '@tanstack/react-query';

import { getVarianceDiscrepancySubcategory } from '@/api/dashboard';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { fmtInt } from './dashboardFormat';
import { METRIC_LABEL } from './DiscrepancyPanel';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  metric: string | null;
  reviewCycleId?: string;
  onSelectSubcategory?: (subcategory: string) => void;
}

/** Side sheet: above-threshold variance sub-category breakdown for one metric. */
export default function DiscrepancySubcategorySheet({ open, onOpenChange, metric, reviewCycleId, onSelectSubcategory }: Props) {
  const query = useQuery({
    queryKey: ['dashboard', 'variance-subcategory', metric, reviewCycleId],
    queryFn: () => getVarianceDiscrepancySubcategory(metric as string, { review_cycle_id: reviewCycleId }),
    enabled: open && !!metric,
  });

  const rows = (query.data?.rows ?? []).slice().sort((a, b) => b.count - a.count);
  const label = metric ? METRIC_LABEL[metric] ?? metric : '';

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{label} — above-threshold sub-categories</SheetTitle>
          <SheetDescription>
            Reasons behind the above-threshold differences.{' '}
            <span className="font-medium text-gray-700">{fmtInt(query.data?.total ?? 0)} companies</span>
          </SheetDescription>
        </SheetHeader>

        <div className="mt-4">
          {query.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : query.isError ? (
            <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">Failed to load sub-categories.</div>
          ) : rows.length === 0 ? (
            <div className="py-8 text-center text-sm text-gray-500">No sub-category data.</div>
          ) : (
            <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200">
              {rows.map((r) => (
                <li key={r.subcategory}>
                  <button
                    type="button"
                    onClick={() => r.count > 0 && onSelectSubcategory?.(r.subcategory)}
                    disabled={r.count === 0}
                    className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left enabled:hover:bg-gray-50 disabled:cursor-default"
                  >
                    <span className="text-sm text-gray-700">{r.subcategory}</span>
                    <span className="text-sm font-semibold tabular-nums text-gray-900">{fmtInt(r.count)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
