import type { PortfolioCompany } from '@/types/domain';
import type { ReviewStage } from '@/types/reviewCycle';

export type FilterColumnKind = 'string' | 'number' | 'date';

export type ColumnFilterState =
  | { kind: 'string'; selected: string[] }
  | { kind: 'number'; min: string; max: string }
  | { kind: 'date'; start: string; end: string };

export type DashboardColumnMeta<T = PortfolioCompany> = {
  id: string;
  field: keyof T | null;
  /** When set, use this instead of `field` for string raw value (e.g. review cycle label). */
  valueFrom?: (row: T, getCycleLabel: (id: string) => string) => string;
  kind: FilterColumnKind;
  /** Fixed multiselect options (e.g. review stage); otherwise options are distincts from the current row set. */
  enumOptions?: readonly string[];
};

export const STAGES: ReviewStage[] = [
  'Not applicable',
  'Financials to be received',
  'In review',
  'Discrepancy identified',
  'No discrepancy identified',
  'Not comparable',
  'Query sent',
  'Query response reminder sent 1',
  'Query response reminder sent 2',
  'Response received',
  'Response received - Partially answered',
  'Response received - Call to be scheduled',
  'Approved',
  'Approved - Flagged',
  'Not Approved',
  'Not Approved - Flagged',
];

export const DASHBOARD_FILTER_COLUMNS: DashboardColumnMeta[] = [
  { id: 'name', field: 'name', kind: 'string' },
  { id: 'company_id', field: 'company_id', kind: 'string' },
  {
    id: 'entity_name',
    field: null,
    kind: 'string',
    valueFrom: (row) => normalizeCellString((row as { entity_name?: string | null }).entity_name),
  },
  // Audit state of record — entity-level status (replaces the old company review_stage).
  {
    id: 'entity_status',
    field: null,
    kind: 'string',
    enumOptions: STAGES,
    valueFrom: (row) => normalizeCellString((row as { entity_status?: string | null }).entity_status),
  },
  { id: 'contact_name', field: 'contact_name', kind: 'string' },
  { id: 'contact_email_id', field: 'contact_email_id', kind: 'string' },
  { id: 'fund', field: 'fund', kind: 'string' },
  { id: 'investment_lead', field: 'investment_lead', kind: 'string' },
  { id: 'company_stage', field: 'company_stage', kind: 'string' },
  { id: 'geography', field: 'geography', kind: 'string' },
  { id: 'ownership_pct', field: 'ownership_pct', kind: 'number' },
  { id: 'cost', field: 'cost', kind: 'number' },
  { id: 'fmv', field: 'fmv', kind: 'number' },
  { id: 'position_is_unique', field: 'position_is_unique', kind: 'string' },
  { id: 'consolidated_ownership_pct', field: 'consolidated_ownership_pct', kind: 'number' },
  { id: 'consolidated_cost', field: 'consolidated_cost', kind: 'number' },
  { id: 'consolidated_fmv', field: 'consolidated_fmv', kind: 'number' },
  { id: 'company_category_1', field: 'company_category_1', kind: 'string' },
  { id: 'company_category_2', field: 'company_category_2', kind: 'string' },
  { id: 'scoped_in_for_audit', field: 'scoped_in_for_audit', kind: 'string' },
  { id: 'exclusion_reason', field: 'exclusion_reason', kind: 'string' },
  { id: 'fy_end_date', field: 'fy_end_date', kind: 'date' },
  { id: 'due_date', field: 'due_date', kind: 'date' },
  { id: 'audit_status', field: 'audit_status', kind: 'string' },
  { id: 'auditor', field: 'auditor', kind: 'string' },
  { id: 'tentative_completion_date', field: 'tentative_completion_date', kind: 'date' },
  { id: 'company_response', field: 'company_response', kind: 'string' },
  { id: 'peak_xv_actionable', field: 'peak_xv_actionable', kind: 'string' },
  { id: 'reason_to_scope_out', field: 'reason_to_scope_out', kind: 'string' },
  {
    id: 'entity_comments',
    field: null,
    kind: 'string',
    valueFrom: (row) => normalizeCellString((row as { entity_comments?: string | null }).entity_comments),
  },
  {
    id: 'entity_one_desk_email_status',
    field: null,
    kind: 'string',
    valueFrom: (row) => normalizeCellString((row as { entity_one_desk_email_status?: string | null }).entity_one_desk_email_status),
  },
];

export const REVIEW_CYCLE_COLUMN: DashboardColumnMeta = {
  id: 'review_cycle_id',
  field: 'review_cycle_id',
  kind: 'string',
  valueFrom: (row, getCycleLabel) => {
    const id = row.review_cycle_id;
    if (id == null || String(id).trim() === '') return '—';
    return getCycleLabel(String(id));
  },
};

export const UPDATED_COLUMN: DashboardColumnMeta = {
  id: 'updated_at',
  field: 'updated_at',
  kind: 'date',
};

export const CREATED_COLUMN: DashboardColumnMeta = {
  id: 'created_at',
  field: 'created_at',
  kind: 'date',
};

export function normalizeCellString(v: unknown): string {
  if (v == null) return '—';
  const s = String(v).trim();
  return s === '' ? '—' : s;
}

export function parseNumberLike(v: string | null | undefined): number | null {
  if (v == null) return null;
  const t = String(v).replace(/,/g, '').replace(/%/g, '').trim();
  if (t === '' || t === '—') return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

function parseFilterBound(s: string): number | null {
  const t = s.trim();
  if (t === '') return null;
  const n = Number(t.replace(/,/g, ''));
  return Number.isFinite(n) ? n : null;
}

function localDayKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function startOfDayMs(ymd: string): number | null {
  const p = ymd.trim();
  if (!p) return null;
  const [y, mo, d] = p.split('-').map(Number);
  if (!y || !mo || !d) return null;
  return new Date(y, mo - 1, d, 0, 0, 0, 0).getTime();
}

function endOfDayMs(ymd: string): number | null {
  const p = ymd.trim();
  if (!p) return null;
  const [y, mo, d] = p.split('-').map(Number);
  if (!y || !mo || !d) return null;
  return new Date(y, mo - 1, d, 23, 59, 59, 999).getTime();
}

export function getRawCellValue<T = PortfolioCompany>(
  row: T,
  col: DashboardColumnMeta<T>,
  getCycleLabel: (id: string) => string,
): string {
  if (col.valueFrom) return col.valueFrom(row, getCycleLabel);
  if (col.field === null) return '—';
  const v = row[col.field];
  return normalizeCellString(v);
}

export function rowMatchesFilter<T = PortfolioCompany>(
  row: T,
  col: DashboardColumnMeta<T>,
  filter: ColumnFilterState,
  getCycleLabel: (id: string) => string,
): boolean {
  if (filter.kind === 'string') {
    if (filter.selected.length === 0) return true;
    const raw = getRawCellValue(row, col, getCycleLabel);
    return filter.selected.includes(raw);
  }
  if (filter.kind === 'number') {
    const hasMin = filter.min.trim() !== '';
    const hasMax = filter.max.trim() !== '';
    if (!hasMin && !hasMax) return true;
    if (col.field == null) return false;
    const raw = row[col.field];
    const n = parseNumberLike(raw == null ? null : String(raw));
    if (n === null) return false;
    if (hasMin) {
      const lo = parseFilterBound(filter.min);
      if (lo != null && n < lo) return false;
    }
    if (hasMax) {
      const hi = parseFilterBound(filter.max);
      if (hi != null && n > hi) return false;
    }
    return true;
  }
  if (filter.kind === 'date') {
    const hasStart = filter.start.trim() !== '';
    const hasEnd = filter.end.trim() !== '';
    if (!hasStart && !hasEnd) return true;
    if (col.field == null) return false;
    const raw = row[col.field];
    if (raw == null || String(raw).trim() === '') return false;

    if (col.id === 'updated_at' || col.id === 'created_at') {
      const t = new Date(String(raw)).getTime();
      if (Number.isNaN(t)) return false;
      if (hasStart) {
        const lo = startOfDayMs(filter.start);
        if (lo != null && t < lo) return false;
      }
      if (hasEnd) {
        const hi = endOfDayMs(filter.end);
        if (hi != null && t > hi) return false;
      }
      return true;
    }

    const d = new Date(String(raw));
    if (Number.isNaN(d.getTime())) return false;
    const key = localDayKey(d);
    if (hasStart && key < filter.start) return false;
    if (hasEnd && key > filter.end) return false;
    return true;
  }
  return true;
}

export function applyColumnFilters<T = PortfolioCompany>(
  rows: T[],
  filters: Record<string, ColumnFilterState | undefined>,
  columns: DashboardColumnMeta<T>[],
  getCycleLabel: (id: string) => string,
): T[] {
  const active = Object.entries(filters).filter(([, f]) => f != null) as [string, ColumnFilterState][];
  if (active.length === 0) return rows;

  const colById = new Map(columns.map((c) => [c.id, c]));

  return rows.filter((row) =>
    active.every(([id, filterState]) => {
      const col = colById.get(id);
      if (!col) return true;
      return rowMatchesFilter(row, col, filterState, getCycleLabel);
    }),
  );
}

export function isFilterActive(f: ColumnFilterState | undefined): boolean {
  if (!f) return false;
  if (f.kind === 'string') return f.selected.length > 0;
  if (f.kind === 'number') return f.min.trim() !== '' || f.max.trim() !== '';
  if (f.kind === 'date') return f.start.trim() !== '' || f.end.trim() !== '';
  return false;
}

export function distinctStringValues<T = PortfolioCompany>(rows: T[], col: DashboardColumnMeta<T>, getCycleLabel: (id: string) => string): string[] {
  if (col.enumOptions?.length) return Array.from(new Set(col.enumOptions)).sort((a, b) => a.localeCompare(b));
  const set = new Set<string>();
  for (const row of rows) {
    set.add(getRawCellValue(row, col, getCycleLabel));
  }
  return Array.from(set).sort((a, b) => a.localeCompare(b));
}
