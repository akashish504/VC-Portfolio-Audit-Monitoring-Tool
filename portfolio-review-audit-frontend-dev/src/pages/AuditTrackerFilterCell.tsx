import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Filter, Search } from 'lucide-react';

import {
  type ColumnFilterState,
  type DashboardColumnMeta,
  distinctStringValues,
  isFilterActive,
} from '@/pages/reviewCycleColumnFilters';

export type FilterCellProps<T> = {
  col: DashboardColumnMeta<T>;
  /** Rows after global search — used to build distinct string options. */
  baseRows: T[];
  filter: ColumnFilterState | undefined;
  onChange: (next: ColumnFilterState | undefined) => void;
  isOpen: boolean;
  onToggle: () => void;
  /** Resolves a review-cycle id to its label; pass an identity fn when not applicable. */
  getCycleLabel?: (id: string) => string;
  className?: string;
};

export function AuditTrackerFilterCell<T>({
  col,
  baseRows,
  filter,
  onChange,
  isOpen,
  onToggle,
  getCycleLabel = (id) => id,
  className = '',
}: FilterCellProps<T>) {
  const [searchQ, setSearchQ] = useState('');
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [panelPos, setPanelPos] = useState<{ top: number; left: number; width: number }>({ top: 0, left: 0, width: 280 });
  const active = isFilterActive(filter);

  useEffect(() => {
    if (isOpen) setSearchQ('');
  }, [isOpen]);

  const updatePanelPos = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const w = Math.min(288, Math.max(220, window.innerWidth - 16));
    let left = r.left;
    if (left + w > window.innerWidth - 8) left = Math.max(8, window.innerWidth - w - 8);
    let top = r.bottom + 4;
    const estH = 320;
    if (top + estH > window.innerHeight - 8 && r.top - estH - 4 > 8) {
      top = r.top - estH - 4;
    }
    setPanelPos({ top, left, width: w });
  }, []);

  useLayoutEffect(() => {
    if (!isOpen) return;
    updatePanelPos();
    const onWin = () => updatePanelPos();
    window.addEventListener('resize', onWin);
    window.addEventListener('scroll', onWin, true);
    return () => {
      window.removeEventListener('resize', onWin);
      window.removeEventListener('scroll', onWin, true);
    };
  }, [isOpen, updatePanelPos]);

  const stringOptions = useMemo(
    () => distinctStringValues(baseRows, col, getCycleLabel),
    [baseRows, col, getCycleLabel],
  );

  const filteredOptions = useMemo(() => {
    const q = searchQ.trim().toLowerCase();
    if (!q) return stringOptions;
    return stringOptions.filter((o) => o.toLowerCase().includes(q));
  }, [stringOptions, searchQ]);

  const selectedSet =
    filter?.kind === 'string' ? new Set(filter.selected) : new Set<string>();

  const toggleStringValue = (v: string) => {
    const prev = filter?.kind === 'string' ? [...filter.selected] : [];
    const i = prev.indexOf(v);
    if (i >= 0) prev.splice(i, 1);
    else prev.push(v);
    if (prev.length === 0) onChange(undefined);
    else onChange({ kind: 'string', selected: prev });
  };

  return (
    <div className={`relative ${className}`} data-column-filter={col.id}>
      <button
        ref={triggerRef}
        type="button"
        onClick={onToggle}
        title="Filter this column"
        className={`inline-flex items-center justify-center rounded border px-1.5 py-1 text-[10px] font-medium transition-colors ${
          active || isOpen
            ? 'border-blue-400 bg-blue-50 text-blue-700'
            : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:bg-gray-50'
        }`}
      >
        <Filter className="h-3 w-3" />
      </button>
      {isOpen &&
        createPortal(
          <div
            className="fixed z-[500] rounded-lg border border-gray-200 bg-white p-2 shadow-lg"
            style={{ top: panelPos.top, left: panelPos.left, width: panelPos.width }}
            data-column-filter={col.id}
            onMouseDown={(e) => e.stopPropagation()}
          >
            {col.kind === 'string' && (
              <div className="space-y-2">
                <div className="relative">
                  <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
                  <input
                    type="search"
                    placeholder="Search values…"
                    value={searchQ}
                    onChange={(e) => setSearchQ(e.target.value)}
                    className="w-full rounded border border-gray-200 py-1.5 pl-7 pr-2 text-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                </div>
                <div className="max-h-48 overflow-auto rounded border border-gray-100">
                  {filteredOptions.length === 0 ? (
                    <p className="px-2 py-3 text-center text-xs text-gray-400">No matches</p>
                  ) : (
                    filteredOptions.map((opt) => (
                      <label
                        key={opt}
                        className="flex cursor-pointer items-start gap-2 border-b border-gray-50 px-2 py-1.5 last:border-0 hover:bg-gray-50"
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5 rounded border-gray-300"
                          checked={selectedSet.has(opt)}
                          onChange={() => toggleStringValue(opt)}
                        />
                        <span className="min-w-0 flex-1 break-words text-xs text-gray-800">{opt}</span>
                      </label>
                    ))
                  )}
                </div>
                <div className="flex justify-between gap-2 border-t border-gray-100 pt-2">
                  <button
                    type="button"
                    className="text-xs text-gray-500 hover:text-gray-800"
                    onClick={() => {
                      onChange(undefined);
                      setSearchQ('');
                    }}
                  >
                    Clear
                  </button>
                  <span className="text-[10px] text-gray-400">{selectedSet.size} selected</span>
                </div>
              </div>
            )}
          {col.kind === 'number' && (
            <div className="space-y-2">
              <p className="text-[10px] font-medium uppercase tracking-wide text-gray-500">Range</p>
              <div className="flex gap-2">
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="Min"
                  className="w-full min-w-0 rounded border border-gray-200 px-2 py-1.5 text-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  value={filter?.kind === 'number' ? filter.min : ''}
                  onChange={(e) =>
                    onChange({
                      kind: 'number',
                      min: e.target.value,
                      max: filter?.kind === 'number' ? filter.max : '',
                    })
                  }
                />
                <input
                  type="text"
                  inputMode="decimal"
                  placeholder="Max"
                  className="w-full min-w-0 rounded border border-gray-200 px-2 py-1.5 text-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  value={filter?.kind === 'number' ? filter.max : ''}
                  onChange={(e) =>
                    onChange({
                      kind: 'number',
                      min: filter?.kind === 'number' ? filter.min : '',
                      max: e.target.value,
                    })
                  }
                />
              </div>
              <button
                type="button"
                className="w-full rounded border border-gray-200 py-1 text-xs text-gray-600 hover:bg-gray-50"
                onClick={() => onChange(undefined)}
              >
                Clear range
              </button>
            </div>
          )}
          {col.kind === 'date' && (
            <div className="space-y-2">
              <p className="text-[10px] font-medium uppercase tracking-wide text-gray-500">Date range</p>
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] text-gray-500">
                  From
                  <input
                    type="date"
                    className="mt-0.5 w-full rounded border border-gray-200 px-2 py-1 text-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                    value={filter?.kind === 'date' ? filter.start : ''}
                    onChange={(e) =>
                      onChange({
                        kind: 'date',
                        start: e.target.value,
                        end: filter?.kind === 'date' ? filter.end : '',
                      })
                    }
                  />
                </label>
                <label className="text-[10px] text-gray-500">
                  To
                  <input
                    type="date"
                    className="mt-0.5 w-full rounded border border-gray-200 px-2 py-1 text-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                    value={filter?.kind === 'date' ? filter.end : ''}
                    onChange={(e) =>
                      onChange({
                        kind: 'date',
                        start: filter?.kind === 'date' ? filter.start : '',
                        end: e.target.value,
                      })
                    }
                  />
                </label>
              </div>
              <button
                type="button"
                className="w-full rounded border border-gray-200 py-1 text-xs text-gray-600 hover:bg-gray-50"
                onClick={() => onChange(undefined)}
              >
                Clear dates
              </button>
            </div>
          )}
          </div>,
    document.body,
  )}
    </div>
  );
}
