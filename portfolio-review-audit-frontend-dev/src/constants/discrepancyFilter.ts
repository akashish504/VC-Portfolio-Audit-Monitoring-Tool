/** Financial metric slugs stored on auto-generated discrepancy rows (`discrepancies.type`). */
export const DISCREPANCY_METRIC_TYPES = [
  { value: 'revenue', label: 'Revenue' },
  { value: 'ebitda', label: 'EBITDA' },
  { value: 'pbt', label: 'PBT' },
  { value: 'pat', label: 'PAT' },
  { value: 'cash', label: 'Cash' },
  { value: 'debt', label: 'Debt' },
] as const;

export type DiscrepancyMetricType = (typeof DISCREPANCY_METRIC_TYPES)[number]['value'];

/** Single-select filter values for the In Review Tracker. */
export type DiscrepancyTrackerFilter = 'all' | DiscrepancyMetricType | 'manual';

export function discrepancyFilterLabel(value: DiscrepancyTrackerFilter): string {
  if (value === 'all') return 'All Discrepancies';
  if (value === 'manual') return 'Manually Created';
  return DISCREPANCY_METRIC_TYPES.find((m) => m.value === value)?.label ?? value;
}
