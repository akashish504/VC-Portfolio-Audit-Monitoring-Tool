import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Check, ChevronDown, ListFilter } from 'lucide-react';

import type { TimelineChartFeed } from '@/api/dashboard';
import type { DrillTarget } from './DashboardDrillDownSheet';

interface Props {
  feed: TimelineChartFeed;
  onDrill?: (target: DrillTarget) => void;
}

const BAR_FILL = '#6f55b3'; // violet — matches the workbook chart

/** Inclusion/Exclusion is a fixed pair (maps to scoping_for_audit true/false). */
const INCLUSION_OPTIONS = ['Scoped in', 'Scoped out'] as const;

type FilterKey = 'inclusion' | 'geo' | 'strategy' | 'fy' | 'due';

interface FilterDef {
  key: FilterKey;
  label: string;
  color: string; // pill background (exact workbook palette)
  options: string[];
}

/** A single colored filter pill with a multi-select checkbox dropdown.
 *  Shows "All" when nothing is selected (= no constraint on that dimension). */
function FilterPill({
  def,
  selected,
  open,
  onToggleOpen,
  onChange,
}: {
  def: FilterDef;
  selected: Set<string>;
  open: boolean;
  onToggleOpen: () => void;
  onChange: (next: Set<string>) => void;
}) {
  const summary =
    selected.size === 0
      ? 'All'
      : selected.size === 1
        ? [...selected][0]
        : `${selected.size} selected`;

  const toggle = (opt: string) => {
    const next = new Set(selected);
    if (next.has(opt)) next.delete(opt);
    else next.add(opt);
    onChange(next);
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={onToggleOpen}
        className="flex w-full items-center gap-2 rounded-full px-4 py-2.5 text-left text-sm font-medium text-white shadow-sm transition hover:brightness-110"
        style={{ backgroundColor: def.color }}
      >
        <ListFilter className="h-4 w-4 shrink-0 opacity-90" />
        <span className="flex-1 truncate">{def.label}</span>
        <span className="flex shrink-0 items-center gap-1 text-xs font-normal opacity-90">
          <span className="max-w-[88px] truncate">{summary}</span>
          <ChevronDown className={`h-3.5 w-3.5 transition ${open ? 'rotate-180' : ''}`} />
        </span>
      </button>

      {open && (
        <div className="absolute right-0 z-30 mt-2 w-60 overflow-hidden rounded-lg border border-gray-200 bg-white py-1 shadow-lg">
          <button
            type="button"
            onClick={() => onChange(new Set())}
            className="flex w-full items-center justify-between px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
          >
            <span className={selected.size === 0 ? 'font-semibold text-gray-900' : ''}>
              All
            </span>
            {selected.size === 0 && <Check className="h-4 w-4 text-violet-600" />}
          </button>
          <div className="my-1 border-t border-gray-100" />
          {def.options.length === 0 ? (
            <div className="px-3 py-2 text-xs italic text-gray-400">No values</div>
          ) : (
            def.options.map((opt) => {
              const on = selected.has(opt);
              return (
                <button
                  key={opt}
                  type="button"
                  onClick={() => toggle(opt)}
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
                >
                  <span className={on ? 'font-medium text-gray-900' : ''}>{opt}</span>
                  <span
                    className={`flex h-4 w-4 items-center justify-center rounded border ${
                      on ? 'border-violet-600 bg-violet-600' : 'border-gray-300'
                    }`}
                  >
                    {on && <Check className="h-3 w-3 text-white" />}
                  </span>
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

/** Filterable audit-timeline chart: companies bucketed by tentative-completion month.
 *  Five filters (inclusion, geography, strategy, FY end, within-due/overdue) re-aggregate
 *  the bars instantly client-side; counts are de-duplicated by company (CID). */
export default function TimelineChart({ feed, onDrill }: Props) {
  const [open, setOpen] = useState<FilterKey | null>(null);
  const [sel, setSel] = useState<Record<FilterKey, Set<string>>>({
    inclusion: new Set(),
    geo: new Set(),
    strategy: new Set(),
    fy: new Set(),
    due: new Set(),
  });
  const wrapRef = useRef<HTMLDivElement>(null);

  // Close any open dropdown on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(null);
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(null);
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const filters: FilterDef[] = useMemo(
    () => [
      {
        key: 'inclusion',
        label: 'Inclusion / Exclusion',
        color: '#5b54a4',
        options: [...INCLUSION_OPTIONS],
      },
      { key: 'geo', label: 'Geography (L1)', color: '#3f7d3a', options: feed.geo_options },
      { key: 'strategy', label: 'Strategy', color: '#bfa12e', options: feed.strategy_options },
      { key: 'fy', label: 'FY end', color: '#322a5e', options: feed.fy_end_options },
      {
        key: 'due',
        label: 'Within due date / Overdue',
        color: '#7c2020',
        options: feed.due_options,
      },
    ],
    [feed],
  );

  const anyActive = Object.values(sel).some((s) => s.size > 0);

  // Apply the active filters, then bucket by tentative-completion month and count
  // unique companies (a company in multiple strategies must not be double-counted).
  const data = useMemo(() => {
    const buckets = new Map<string, { label: string; ids: Set<string> }>();
    for (const r of feed.rows) {
      if (sel.inclusion.size) {
        const tag = r.scoped_in ? 'Scoped in' : 'Scoped out';
        if (!sel.inclusion.has(tag)) continue;
      }
      if (sel.geo.size && !(r.geo && sel.geo.has(r.geo))) continue;
      if (sel.strategy.size && !(r.strategy && sel.strategy.has(r.strategy))) continue;
      if (sel.fy.size && !(r.fy_end && sel.fy.has(r.fy_end))) continue;
      if (sel.due.size && !(r.due_bucket && sel.due.has(r.due_bucket))) continue;

      let b = buckets.get(r.month_key);
      if (!b) {
        b = { label: r.month_label, ids: new Set() };
        buckets.set(r.month_key, b);
      }
      b.ids.add(r.deal_id);
    }
    return [...buckets.entries()]
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([, b]) => ({ month: b.label, count: b.ids.size, ids: [...b.ids] }));
  }, [feed, sel]);

  const total = useMemo(() => data.reduce((acc, d) => acc + d.count, 0), [data]);

  return (
    <div ref={wrapRef}>
      <h3 className="mb-4 text-center text-sm font-bold text-gray-900">
        AUDIT TIMELINE :{' '}
        <span className="font-normal text-gray-500">
          (Need to choose the desired filters to populate the chart)
        </span>
      </h3>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_280px]">
        {/* Chart */}
        <div className="rounded-lg border border-gray-100 bg-gray-50/60 p-3">
          {data.length === 0 ? (
            <div className="flex h-[340px] items-center justify-center text-center text-sm text-gray-400">
              No companies match the selected filters.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={360}>
              <BarChart data={data} margin={{ top: 24, right: 16, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" vertical={false} />
                <XAxis
                  dataKey="month"
                  tick={{ fontSize: 12, fill: '#4b5563' }}
                  axisLine={{ stroke: '#d1d5db' }}
                  tickLine={false}
                  interval={0}
                  angle={data.length > 8 ? -30 : 0}
                  textAnchor={data.length > 8 ? 'end' : 'middle'}
                  height={data.length > 8 ? 48 : 30}
                />
                <YAxis
                  tick={{ fontSize: 12, fill: '#6b7280' }}
                  axisLine={false}
                  tickLine={false}
                  allowDecimals={false}
                />
                <Tooltip
                  contentStyle={{ borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 12 }}
                  cursor={{ fill: 'rgba(111,85,179,0.06)' }}
                  formatter={(v: number) => [v, 'Companies']}
                />
                <Bar
                  dataKey="count"
                  fill={BAR_FILL}
                  maxBarSize={56}
                  isAnimationActive={false}
                  cursor={onDrill ? 'pointer' : undefined}
                  onClick={(d: { month?: string; ids?: string[] }) =>
                    onDrill && d?.ids?.length &&
                    onDrill({ title: `Tentative completion · ${d.month}`, deal_ids: d.ids, unique: true })
                  }
                >
                  <LabelList
                    dataKey="count"
                    position="top"
                    style={{ fill: BAR_FILL, fontSize: 12, fontWeight: 600 }}
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Filters */}
        <div className="space-y-3">
          {filters.map((def) => (
            <FilterPill
              key={def.key}
              def={def}
              selected={sel[def.key]}
              open={open === def.key}
              onToggleOpen={() => setOpen((o) => (o === def.key ? null : def.key))}
              onChange={(next) => setSel((s) => ({ ...s, [def.key]: next }))}
            />
          ))}

          <div className="flex items-center justify-between px-1 pt-1 text-xs text-gray-500">
            <span>
              {total} compan{total === 1 ? 'y' : 'ies'} shown
            </span>
            {anyActive && (
              <button
                type="button"
                onClick={() =>
                  setSel({
                    inclusion: new Set(),
                    geo: new Set(),
                    strategy: new Set(),
                    fy: new Set(),
                    due: new Set(),
                  })
                }
                className="font-medium text-violet-600 hover:underline"
              >
                Clear all
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
