import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Filter, Search } from 'lucide-react';

/**
 * Funnel-icon column filter — one consistent header filter shared across the review
 * tables (In Review Tracker, File Tagging, …), matching the Review Cycle Dashboard style.
 * Two modes:
 *  - 'search': free-text "contains" filter (single input)
 *  - 'multi':  searchable checkbox list (multi-select)
 */
export function ColFilter(
  props:
    | { kind: 'search'; value: string; onChange: (next: string) => void; placeholder?: string }
    | {
        kind: 'multi';
        options: string[];
        selected: Set<string>;
        onChange: (next: Set<string>) => void;
        display?: (v: string) => string;
      },
) {
  const [open, setOpen] = useState(false);
  const [searchQ, setSearchQ] = useState('');
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0, width: 240 });

  const active = props.kind === 'search' ? props.value.trim() !== '' : props.selected.size > 0;

  const updatePos = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const w = Math.min(288, Math.max(220, window.innerWidth - 16));
    let left = r.left;
    if (left + w > window.innerWidth - 8) left = Math.max(8, window.innerWidth - w - 8);
    setPos({ top: r.bottom + 4, left, width: w });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    updatePos();
    const onWin = () => updatePos();
    window.addEventListener('resize', onWin);
    window.addEventListener('scroll', onWin, true);
    return () => {
      window.removeEventListener('resize', onWin);
      window.removeEventListener('scroll', onWin, true);
    };
  }, [open, updatePos]);

  useEffect(() => {
    if (!open) {
      setSearchQ('');
      return;
    }
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t) || menuRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const q = searchQ.trim().toLowerCase();
  const filteredOptions =
    props.kind === 'multi'
      ? q
        ? props.options.filter((o) => (props.display ? props.display(o) : o).toLowerCase().includes(q))
        : props.options
      : [];

  const toggleValue = (v: string) => {
    if (props.kind !== 'multi') return;
    const next = new Set(props.selected);
    if (next.has(v)) next.delete(v); else next.add(v);
    props.onChange(next);
  };

  return (
    <div className="relative shrink-0">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="Filter this column"
        className={`inline-flex items-center justify-center rounded border px-1.5 py-1 text-[10px] font-medium transition-colors ${
          active || open
            ? 'border-blue-400 bg-blue-50 text-blue-700'
            : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300 hover:bg-gray-50'
        }`}
      >
        <Filter className="h-3 w-3" />
      </button>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            className="fixed z-[500] rounded-lg border border-gray-200 bg-white p-2 shadow-lg"
            style={{ top: pos.top, left: pos.left, width: pos.width }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            {props.kind === 'search' ? (
              <div className="space-y-2">
                <div className="relative">
                  <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
                  <input
                    type="search"
                    autoFocus
                    placeholder={props.placeholder ?? 'Search…'}
                    value={props.value}
                    onChange={(e) => props.onChange(e.target.value)}
                    className="w-full rounded border border-gray-200 py-1.5 pl-7 pr-2 text-xs focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                </div>
                <div className="flex justify-end border-t border-gray-100 pt-2">
                  <button
                    type="button"
                    className="text-xs text-gray-500 hover:text-gray-800"
                    onClick={() => props.onChange('')}
                  >
                    Clear
                  </button>
                </div>
              </div>
            ) : (
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
                          checked={props.selected.has(opt)}
                          onChange={() => toggleValue(opt)}
                        />
                        <span className="min-w-0 flex-1 break-words text-xs text-gray-800">
                          {props.display ? props.display(opt) : opt}
                        </span>
                      </label>
                    ))
                  )}
                </div>
                <div className="flex justify-between gap-2 border-t border-gray-100 pt-2">
                  <button
                    type="button"
                    className="text-xs text-gray-500 hover:text-gray-800"
                    onClick={() => props.onChange(new Set())}
                  >
                    Clear
                  </button>
                  <span className="text-[10px] text-gray-400">{props.selected.size} selected</span>
                </div>
              </div>
            )}
          </div>,
          document.body,
        )}
    </div>
  );
}
