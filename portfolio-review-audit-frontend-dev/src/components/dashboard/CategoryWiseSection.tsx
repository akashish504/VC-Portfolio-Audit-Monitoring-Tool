import { useMemo, useState } from 'react';

import { cn } from '@/lib/utils';
import PaginatedCompanyList from './PaginatedCompanyList';

interface Props {
  reviewCycleId?: string;
  categories: string[];
}

/** "iv. Category-wise data": multi-select Latest Category → company list (with Audit Status). */
export default function CategoryWiseSection({ reviewCycleId, categories }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const toggle = (c: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(c) ? next.delete(c) : next.add(c);
      return next;
    });

  const params = useMemo(
    () => ({ review_cycle_id: reviewCycleId, category: selected.size ? [...selected].join(',') : undefined }),
    [reviewCycleId, selected],
  );

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-gray-900">Category-wise data</h2>
        <div className="flex gap-2 text-xs">
          <button type="button" onClick={() => setSelected(new Set(categories))} className="text-blue-600 hover:underline">Select all</button>
          <button type="button" onClick={() => setSelected(new Set())} className="text-gray-500 hover:underline">Clear</button>
        </div>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {categories.map((c) => {
          const on = selected.has(c);
          return (
            <button
              key={c}
              type="button"
              onClick={() => toggle(c)}
              className={cn(
                'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                on ? 'border-blue-500 bg-blue-50 text-blue-700' : 'border-gray-300 text-gray-600 hover:bg-gray-50',
              )}
            >
              {c}
            </button>
          );
        })}
      </div>

      <PaginatedCompanyList params={params} />
    </div>
  );
}
