/** Shared formatting + colour helpers for the analytics dashboard.
 *  Kept tiny and presentational so every dashboard component stays consistent. */

const INT_FMT = new Intl.NumberFormat('en-US');

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '0';
  return INT_FMT.format(n);
}

/** Format an integer, but render an em dash for zero / missing (no-data cells). */
export function fmtIntDash(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n) || n === 0) return '—';
  return INT_FMT.format(n);
}

/** Display a passthrough string value (e.g. consolidated cost/FMV) or an em dash. */
export function displayOrDash(v: string | null | undefined): string {
  const s = (v ?? '').trim();
  return s === '' ? '—' : s;
}

/** Harmonious palette for charts (hex — SVG fills can't use Tailwind classes). */
export const CHART_PALETTE: string[] = [
  '#2563eb', // blue-600
  '#16a34a', // green-600
  '#f59e0b', // amber-500
  '#dc2626', // red-600
  '#7c3aed', // violet-600
  '#0891b2', // cyan-600
  '#db2777', // pink-600
  '#65a30d', // lime-600
];

export function colorForIndex(i: number): string {
  return CHART_PALETTE[i % CHART_PALETTE.length];
}

/** Light chip tone (bg + text) for a status label. Falls back to neutral. */
export function statusTone(status: string): string {
  const s = status.toLowerCase();
  if (s.includes('complete') && !s.includes('overdue')) return 'bg-green-50 text-green-700';
  if (s.includes('overdue')) return 'bg-red-50 text-red-700';
  if (s.includes('due now')) return 'bg-amber-50 text-amber-700';
  if (s.includes('not yet due')) return 'bg-blue-50 text-blue-700';
  if (s.includes('not applicable') || s.includes('excluded')) return 'bg-gray-100 text-gray-600';
  if (s.includes('expected')) return 'bg-indigo-50 text-indigo-700';
  return 'bg-slate-100 text-slate-700';
}
