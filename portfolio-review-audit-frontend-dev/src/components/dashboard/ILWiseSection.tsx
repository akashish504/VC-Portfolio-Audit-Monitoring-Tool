import { useMemo, useState } from 'react';

import { cn } from '@/lib/utils';
import PaginatedCompanyList from './PaginatedCompanyList';

interface Props {
  reviewCycleId?: string;
}

// Actual-completion statuses (spec sheet 2 section iii multi-select).
const STATUSES = [
  'Completed within due date',
  'Completed with Overdue',
  'Due now',
  'Not yet due',
  'Overdue companies',
  'Not applicable',
  'Excluded',
];

/** "iii. IL-wise data": pick one or more statuses → company list with IL details. */
export default function ILWiseSection({ reviewCycleId }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const toggle = (s: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(s) ? next.delete(s) : next.add(s);
      return next;
    });

  const params = useMemo(
    () => ({ review_cycle_id: reviewCycleId, actual_status: selected.size ? [...selected].join(',') : undefined }),
    [reviewCycleId, selected],
  );

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-gray-900">IL-wise data</h2>
        <div className="flex gap-2 text-xs">
          <button type="button" onClick={() => setSelected(new Set(STATUSES))} className="text-blue-600 hover:underline">Select all</button>
          <button type="button" onClick={() => setSelected(new Set())} className="text-gray-500 hover:underline">Clear</button>
        </div>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {STATUSES.map((s) => {
          const on = selected.has(s);
          return (
            <button
              key={s}
              type="button"
              onClick={() => toggle(s)}
              className={cn(
                'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                on ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-300 text-gray-600 hover:bg-gray-50',
              )}
            >
              {s}
            </button>
          );
        })}
      </div>

      <PaginatedCompanyList params={params} />
    </div>
  );
}
