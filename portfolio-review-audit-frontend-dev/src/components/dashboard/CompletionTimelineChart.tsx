import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { CompletionTimeline } from '@/api/dashboard';

interface Props {
  data: CompletionTimeline;
  height?: number;
  onSelectMonth?: (month: string) => void;
  selectedMonth?: string | null;
}

/** Months-ordered bar chart of company counts by tentative completion date (spec A14/A15).
 *  Bars are click-through to filter the company list by that completion month. */
export default function CompletionTimelineChart({ data, height = 180, onSelectMonth, selectedMonth }: Props) {
  const points = data.months.map((m) => ({ month: m, companies: data.counts[m] ?? 0 }));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={points} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
        <XAxis dataKey="month" tick={{ fontSize: 11, fill: '#6b7280' }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fontSize: 11, fill: '#6b7280' }} axisLine={false} tickLine={false} allowDecimals={false} />
        <Tooltip contentStyle={{ borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 12 }}
          cursor={{ fill: 'rgba(37,99,235,0.04)' }} />
        <Bar
          dataKey="companies"
          fill="#2563eb"
          maxBarSize={40}
          radius={[3, 3, 0, 0]}
          isAnimationActive={false}
          cursor={onSelectMonth ? 'pointer' : undefined}
          onClick={(d: { month?: string }) => d?.month && onSelectMonth?.(d.month)}
        >
          {points.map((p) => (
            <Cell key={p.month} fill={selectedMonth && selectedMonth !== p.month ? '#bfdbfe' : '#2563eb'} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
