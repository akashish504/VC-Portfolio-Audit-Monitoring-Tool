import { useMemo } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import type { StatusMatrix } from '@/api/dashboard';
import { colorForIndex } from './dashboardFormat';

interface Props {
  matrix: StatusMatrix;
  height?: number;
  onSegmentClick?: (status: string, month: string) => void;
}

/** Stacked bar chart: FYE month (X) × company count (Y), one stack segment per status row.
 *  Segments are click-through to the drill-down for that status × month. */
export default function StatusByMonthChart({ matrix, height = 320, onSegmentClick }: Props) {
  const { months, rows } = matrix;

  const data = useMemo(
    () =>
      months.map((m) => {
        const point: Record<string, number | string> = { month: m };
        for (const row of rows) point[row.status] = row.by_month[m] ?? 0;
        return point;
      }),
    [months, rows],
  );

  if (rows.length === 0) {
    return <div className="py-10 text-center text-sm text-gray-500">No data to chart.</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" vertical={false} />
        <XAxis dataKey="month" tick={{ fontSize: 12, fill: '#6b7280' }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fontSize: 12, fill: '#6b7280' }} axisLine={false} tickLine={false} allowDecimals={false} />
        <Tooltip
          contentStyle={{ borderRadius: 8, border: '1px solid #e5e7eb', fontSize: 12 }}
          cursor={{ fill: 'rgba(37,99,235,0.04)' }}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {rows.map((row, i) => (
          <Bar
            key={row.status}
            dataKey={row.status}
            stackId="s"
            fill={colorForIndex(i)}
            maxBarSize={48}
            isAnimationActive={false}
            cursor={onSegmentClick ? 'pointer' : undefined}
            onClick={(d: { month?: string }) => d?.month && onSegmentClick?.(row.status, d.month)}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
