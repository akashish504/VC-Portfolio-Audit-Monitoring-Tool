/** Classification options for automated (financial-category) discrepancy rows — keyed by API ``type`` (metric slug). */

export const REVENUE_VARIANCE_CATEGORIES = [
  'Audit Adjustment',
  'Gross Vs. Net',
  'Not Material',
  'Company did not submit (MIS)',
  'Not Comparable',
] as const;

/** Shared list for EBITDA, PBT, and PAT financial discrepancies. */
export const EBITDA_PBT_PAT_VARIANCE_CATEGORIES = [
  'Audit adjustment',
  'Business Transition',
  'Capitalization of Expense',
  'Change in Reporting',
  'Difference in reporting',
  'Exceptional Item',
  'Impact of ESOP or Gratuity or Leave Encashment or Dep or Other Income or Bad Debt and others',
  'Ind AS Vs. IGAAP',
  'Non-cash',
  'Not Material',
  'Remeasurement of Financial Liability',
  'Not Comparable',
] as const;

export const CASH_VARIANCE_CATEGORIES = [
  'Audit Adjustment',
  'Cash held at related entity',
  'Different method of reporting cash',
  'Incorrect submission',
  'Not Material',
  'Not Comparable',
] as const;

export const DEBT_VARIANCE_CATEGORIES = [
  'Difference in reporting',
  'Incorrect submission',
  'Ind AS vs. IGAAP',
  'Not Material',
  'Not Comparable',
] as const;

export function varianceCategoriesForDiscrepancyType(typeField: string | null | undefined): readonly string[] {
  const key = (typeField ?? '').trim().toLowerCase();
  if (key === 'revenue') return REVENUE_VARIANCE_CATEGORIES;
  if (key === 'ebitda' || key === 'pbt' || key === 'pat') return EBITDA_PBT_PAT_VARIANCE_CATEGORIES;
  if (key === 'cash') return CASH_VARIANCE_CATEGORIES;
  if (key === 'debt') return DEBT_VARIANCE_CATEGORIES;
  return [];
}
