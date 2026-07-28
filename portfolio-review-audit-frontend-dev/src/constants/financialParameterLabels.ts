/** Static metric display labels for audit-financial snake_case keys ↔ UI. */

export enum FinancialParameterLabel {
  pbt = 'PBT',
  pat = 'PAT',
  revenue = 'Revenue',
  ebitda = 'EBITDA',
  cash = 'Cash',
  debt = 'Debt',
}

export type FinancialParameterKey = keyof typeof FinancialParameterLabel;

function humanizeFallback(k: string): string {
  return String(k || '')
    .trim()
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Canonical metric key for lookups (API threshold keys, metric column names).
 * Accepts snake_case, mixed case, or spaced words.
 */
export function normalizeFinancialParameterKey(raw: string): FinancialParameterKey | null {
  const slug = raw.trim().toLowerCase().replace(/\s+/g, '_') as FinancialParameterKey;
  return slug in FinancialParameterLabel ? slug : null;
}

/** UI label for a metric key or free-form name; falls back to Title Case humanization. */
export function getFinancialParameterLabel(raw: string): string {
  const key = normalizeFinancialParameterKey(raw);
  if (key) return FinancialParameterLabel[key];
  return humanizeFallback(raw);
}

/** Keys that belong in variance / metric-threshold configuration (not metadata like currency). */
export function isConfigurableFinancialThresholdKey(raw: string): boolean {
  const slug = raw.trim().toLowerCase();
  if (slug === 'currency') return false;
  return normalizeFinancialParameterKey(raw) != null;
}
