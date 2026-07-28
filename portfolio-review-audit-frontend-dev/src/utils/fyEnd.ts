/** FY end helpers — keep in sync with backend `src/services/fy_end.py`. */

export const MONTH_ABBR = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const;

const ABBR_TO_MONTH: Record<string, number> = Object.fromEntries(
  MONTH_ABBR.map((name, i) => [name.toLowerCase(), i + 1]),
);

const FY_END_RE = /^([A-Za-z]{3})-(\d{2})$/;
const LEGACY_DATE_RE = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/;

export function formatFyEnd(month: number, year: number): string {
  const abbr = MONTH_ABBR[month - 1];
  if (!abbr) throw new Error(`invalid month: ${month}`);
  return `${abbr}-${String(year % 100).padStart(2, '0')}`;
}

export function normalizeFyEnd(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const s = raw.trim();
  if (!s) return null;
  const m = FY_END_RE.exec(s);
  if (!m) return null;
  const month = ABBR_TO_MONTH[m[1].toLowerCase()];
  if (!month) return null;
  const yy = Number(m[2]);
  const year = yy <= 99 ? 2000 + yy : yy;
  return formatFyEnd(month, year);
}

export function fyEndFromLegacyDate(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const s = raw.trim();
  if (!s) return null;
  const m = LEGACY_DATE_RE.exec(s);
  if (m) {
    const month = Number(m[1]);
    const year = Number(m[3]);
    if (month >= 1 && month <= 12) return formatFyEnd(month, year);
  }
  return null;
}

export function resolveCompanyFyEnd(fyEnd?: string | null, fyEndDate?: string | null): string | null {
  return normalizeFyEnd(fyEnd) ?? fyEndFromLegacyDate(fyEndDate);
}

export function parseFyEndParts(raw: string | null | undefined): { month: number; year: number } | null {
  const normalized = normalizeFyEnd(raw);
  if (!normalized) return null;
  const m = FY_END_RE.exec(normalized);
  if (!m) return null;
  const month = ABBR_TO_MONTH[m[1].toLowerCase()];
  if (!month) return null;
  const yy = Number(m[2]);
  const year = yy <= 99 ? 2000 + yy : yy;
  return { month, year };
}

/** Years from 2020 through current+1, sorted descending. */
export function fyEndYearOptions(referenceDate = new Date()): number[] {
  const current = referenceDate.getFullYear();
  const years: number[] = [];
  for (let y = current + 1; y >= 2020; y--) years.push(y);
  return years;
}

export function parseReviewCycleId(cycleId: string): { cy: number; fy: number } | null {
  const m = /^CY(\d{2})-FY(\d{2})$/i.exec((cycleId || '').trim());
  if (!m) return null;
  return { cy: Number(m[1]), fy: Number(m[2]) };
}

/** Jun of CY through May of FY, e.g. CY24-FY25 → Jun-24 … May-25. */
export function monthsForReviewCycleId(cycleId: string): string[] {
  const parsed = parseReviewCycleId(cycleId);
  if (!parsed) return [];
  const startYear = 2000 + parsed.cy;
  const endYear = 2000 + parsed.fy;
  let year = startYear;
  let month = 6;
  const out: string[] = [];
  while (year < endYear || (year === endYear && month <= 5)) {
    out.push(formatFyEnd(month, year));
    if (month === 12) {
      month = 1;
      year += 1;
    } else {
      month += 1;
    }
  }
  return out;
}

export function isValidFyEndForReviewCycle(fyEnd: string, reviewCycleId: string): boolean {
  const normalized = normalizeFyEnd(fyEnd);
  if (!normalized) return false;
  return monthsForReviewCycleId(reviewCycleId).includes(normalized);
}
