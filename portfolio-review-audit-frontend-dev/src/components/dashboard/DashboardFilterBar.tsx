import { RotateCcw } from 'lucide-react';

import type { DashboardFilters, ScopingFilters } from '@/api/dashboard';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const ALL = '__all__';

interface Props {
  vocab?: DashboardFilters;
  value: ScopingFilters;
  onChange: (next: ScopingFilters) => void;
}

function FilterSelect({
  label, placeholder, options, current, onPick,
}: {
  label: string;
  placeholder: string;
  options: string[];
  current?: string;
  onPick: (v?: string) => void;
}) {
  return (
    <div className="flex min-w-[150px] flex-col gap-1">
      <label className="text-xs font-medium text-gray-500">{label}</label>
      <Select value={current ?? ALL} onValueChange={(v) => onPick(v === ALL ? undefined : v)}>
        <SelectTrigger className="h-9 bg-white text-sm">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All</SelectItem>
          {options.map((o) => (
            <SelectItem key={o} value={o}>{o}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/** Filter bar: review-cycle selector + dimension dropdowns. Writes back via onChange. */
export default function DashboardFilterBar({ vocab, value, onChange }: Props) {
  const set = (patch: Partial<ScopingFilters>) => onChange({ ...value, ...patch });
  const cycles = vocab?.review_cycles ?? [];
  const hasAnyDimension =
    value.fund || value.geography || value.sector || value.strategy || value.category || value.investment_lead;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-end gap-3">
        {/* Review cycle */}
        <div className="flex min-w-[180px] flex-col gap-1">
          <label className="text-xs font-medium text-gray-500">Review cycle</label>
          <Select
            value={value.review_cycle_id ?? ALL}
            onValueChange={(v) => set({ review_cycle_id: v === ALL ? undefined : v })}
          >
            <SelectTrigger className="h-9 bg-white text-sm">
              <SelectValue placeholder="Select cycle" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All cycles</SelectItem>
              {cycles.map((c) => (
                <SelectItem key={c.id} value={c.id}>{c.name || c.id}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <FilterSelect label="Fund" placeholder="All funds" options={vocab?.funds ?? []}
          current={value.fund} onPick={(v) => set({ fund: v })} />
        <FilterSelect label="Geography" placeholder="All" options={vocab?.geographies ?? []}
          current={value.geography} onPick={(v) => set({ geography: v })} />
        <FilterSelect label="Sector" placeholder="All" options={vocab?.sectors ?? []}
          current={value.sector} onPick={(v) => set({ sector: v })} />
        <FilterSelect label="Strategy" placeholder="All" options={vocab?.strategies ?? []}
          current={value.strategy} onPick={(v) => set({ strategy: v })} />
        <FilterSelect label="Category" placeholder="All" options={vocab?.categories ?? []}
          current={value.category} onPick={(v) => set({ category: v })} />
        <FilterSelect label="Investment lead" placeholder="All" options={vocab?.investment_leads ?? []}
          current={value.investment_lead} onPick={(v) => set({ investment_lead: v })} />

        {hasAnyDimension && (
          <button
            type="button"
            onClick={() => onChange({ review_cycle_id: value.review_cycle_id })}
            className="flex h-9 items-center gap-1.5 rounded-md border border-gray-300 px-3 text-sm text-gray-600 hover:bg-gray-50"
          >
            <RotateCcw className="h-3.5 w-3.5" /> Reset
          </button>
        )}
      </div>
    </div>
  );
}
