/**
 * CSV format for POST /api/v1/portfolio-companies/bulk-upsert (PortfolioCompanyBulkItem).
 * Column names match the API except: company_name → name, contact_email → contact_email_id, stage → review_stage.
 */

import type { PortfolioCompany } from '@/types/domain';

export type BulkUpsertCsvPayload = { name: string; company_id?: string | null } & Partial<Omit<PortfolioCompany, 'id'>>;

/** Header order for the downloadable sample and documentation (snake_case CSV headers). */
export const BULK_CSV_COLUMNS = [
  'company_id',
  'company_name',
  'stage',
  'contact_name',
  'contact_email',
  'fund',
  'investment_lead',
  'company_stage',
  'geography',
  'ownership_pct',
  'cost',
  'fmv',
  'position_is_unique',
  'consolidated_ownership_pct',
  'consolidated_cost',
  'consolidated_fmv',
  'company_category_1',
  'company_category_2',
  'scoped_in_for_audit',
  'exclusion_reason',
  'fy_end_date',
  'due_date',
  'audit_status',
  'auditor',
  'tentative_completion_date',
  'company_response',
  'peak_xv_actionable',
] as const;

/** Map CSV header cell → PortfolioCompanyBulkItem / API field name. */
export const CSV_HEADER_TO_API_FIELD: Record<string, string> = {
  company_id: 'company_id',
  company_name: 'name',
  stage: 'review_stage',
  contact_name: 'contact_name',
  contact_email: 'contact_email_id',
  fund: 'fund',
  investment_lead: 'investment_lead',
  company_stage: 'company_stage',
  geography: 'geography',
  ownership_pct: 'ownership_pct',
  cost: 'cost',
  fmv: 'fmv',
  position_is_unique: 'position_is_unique',
  consolidated_ownership_pct: 'consolidated_ownership_pct',
  consolidated_cost: 'consolidated_cost',
  consolidated_fmv: 'consolidated_fmv',
  company_category_1: 'company_category_1',
  company_category_2: 'company_category_2',
  scoped_in_for_audit: 'scoped_in_for_audit',
  exclusion_reason: 'exclusion_reason',
  fy_end_date: 'fy_end_date',
  due_date: 'due_date',
  audit_status: 'audit_status',
  auditor: 'auditor',
  tentative_completion_date: 'tentative_completion_date',
  company_response: 'company_response',
  peak_xv_actionable: 'peak_xv_actionable',
};

export type CsvBulkRow = {
  name: string;
  review_stage: string;
  company_id?: string;
  contact_name?: string;
  contact_email_id?: string;
  fund?: string;
  investment_lead?: string;
  company_stage?: string;
  geography?: string;
  ownership_pct?: string;
  cost?: string;
  fmv?: string;
  position_is_unique?: string;
  consolidated_ownership_pct?: string;
  consolidated_cost?: string;
  consolidated_fmv?: string;
  company_category_1?: string;
  company_category_2?: string;
  scoped_in_for_audit?: string;
  exclusion_reason?: string;
  fy_end_date?: string;
  due_date?: string;
  audit_status?: string;
  auditor?: string;
  tentative_completion_date?: string;
  company_response?: string;
  peak_xv_actionable?: string;
};

const OPTIONAL_PAYLOAD_KEYS: (keyof CsvBulkRow)[] = [
  'company_id',
  'contact_name',
  'contact_email_id',
  'fund',
  'investment_lead',
  'company_stage',
  'geography',
  'ownership_pct',
  'cost',
  'fmv',
  'position_is_unique',
  'consolidated_ownership_pct',
  'consolidated_cost',
  'consolidated_fmv',
  'company_category_1',
  'company_category_2',
  'scoped_in_for_audit',
  'exclusion_reason',
  'fy_end_date',
  'due_date',
  'audit_status',
  'auditor',
  'tentative_completion_date',
  'company_response',
  'peak_xv_actionable',
];

export function padCsvCols(cols: string[], len: number): string[] {
  const out = cols.map((c) => c.trim());
  while (out.length < len) out.push('');
  return out.slice(0, len);
}

function parseLineToRow(header: string[], line: string, validStages: readonly string[]): CsvBulkRow | null {
  const cols = padCsvCols(line.split(','), header.length);
  const raw: Record<string, string> = {};
  for (let i = 0; i < header.length; i++) {
    const h = header[i];
    const apiKey = CSV_HEADER_TO_API_FIELD[h];
    if (!apiKey) continue;
    const v = (cols[i] ?? '').trim();
    if (v !== '') raw[apiKey] = v;
  }
  const name = raw.name;
  if (!name) return null;
  let review_stage = raw.review_stage || 'Scoped In';
  if (!validStages.includes(review_stage)) review_stage = 'Scoped In';

  const row: CsvBulkRow = {
    name,
    review_stage,
  };
  for (const k of OPTIONAL_PAYLOAD_KEYS) {
    const v = raw[k as string];
    if (v !== undefined && v !== '') (row as Record<string, string>)[k] = v;
  }
  return row;
}

/** Parse uploaded CSV text into rows. Requires `company_name` column. */
export function parseBulkCsv(text: string, validStages: readonly string[]): CsvBulkRow[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 2) return [];
  const header = lines[0].split(',').map((h) => h.trim().toLowerCase());
  if (header.indexOf('company_name') === -1) return [];
  const out: CsvBulkRow[] = [];
  for (let li = 1; li < lines.length; li++) {
    const parsed = parseLineToRow(header, lines[li], validStages);
    if (parsed) out.push(parsed);
  }
  return out;
}

/** Build API payload; `review_cycle_id` always comes from the dialog (selected cycle). */
export function rowToBulkUpsertPayload(row: CsvBulkRow, reviewCycleId: string): BulkUpsertCsvPayload {
  const o: Record<string, unknown> = {
    name: row.name,
    review_stage: row.review_stage,
    review_cycle_id: reviewCycleId,
  };
  const cid = (row.company_id ?? '').trim();
  if (cid) o.company_id = cid;
  for (const k of OPTIONAL_PAYLOAD_KEYS) {
    if (k === 'company_id') continue;
    const v = row[k];
    if (v !== undefined && String(v).trim() !== '') o[k] = String(v).trim();
  }
  return o as BulkUpsertCsvPayload;
}

function csvLine(values: Partial<Record<(typeof BULK_CSV_COLUMNS)[number], string>>): string {
  return BULK_CSV_COLUMNS.map((col) => values[col] ?? '').join(',');
}

/** Sample file: same columns as BULK_CSV_COLUMNS; leave `company_id` blank to create new companies; cycle is chosen in the app. */
export function buildSampleBulkCsv(): string {
  const header = BULK_CSV_COLUMNS.join(',');
  const rows = [
    csvLine({
      company_name: 'Northwind NewCo Holdings',
      stage: 'Scoped In',
      contact_name: 'Pat Kim',
      contact_email: 'pat.kim@example.com',
      fund: 'Venture Fund I',
      investment_lead: 'Sam Lee',
      company_stage: 'Growth',
      geography: 'US-East',
      ownership_pct: '12.5',
      cost: '2500000',
      fmv: '3100000',
      company_category_1: 'Software',
      fy_end_date: '12/31/2025',
      due_date: '03/15/2026',
      audit_status: 'Not started',
      auditor: 'Example LLP',
    }),
    csvLine({
      company_name: 'Cedar Labs NewCo',
      stage: 'Scoped Out',
      contact_name: 'Morgan Chen',
      contact_email: 'morgan.chen@example.com',
      fund: 'Venture Fund II',
      investment_lead: 'Alex Park',
      company_stage: 'Seed',
      geography: 'EU',
      position_is_unique: 'Yes',
      scoped_in_for_audit: 'No',
      exclusion_reason: 'Below threshold',
    }),
    csvLine({
      company_name: 'Riverstone AI NewCo',
      stage: 'In Review',
      contact_name: 'Jordan Blake',
      contact_email: 'jordan.blake@example.com',
      fund: 'Growth Fund A',
      investment_lead: 'Riley Ng',
      company_stage: 'Series B',
      geography: 'US-West',
      consolidated_ownership_pct: '8.2',
      consolidated_cost: '1200000',
      consolidated_fmv: '2100000',
      company_category_2: 'AI / ML',
      tentative_completion_date: '04/30/2026',
    }),
    csvLine({
      company_name: 'Summit Bio NewCo',
      stage: 'Overdue',
      contact_name: 'Sam Patel',
      contact_email: 'sam.patel@example.com',
      fund: 'Healthcare Fund',
      investment_lead: 'Casey Wu',
      company_stage: 'Series A',
      geography: 'APAC',
      due_date: '01/10/2026',
      audit_status: 'Fieldwork',
      company_response: 'Draft received',
      peak_xv_actionable: 'Follow up on WP-3',
    }),
    csvLine({
      company_name: 'Harbor Fintech NewCo',
      stage: 'Completed',
      contact_name: 'Alex Rivera',
      contact_email: 'alex.rivera@example.com',
      fund: 'Fintech Fund',
      investment_lead: 'Jamie Ortiz',
      company_stage: 'Late',
      geography: 'US',
      audit_status: 'Issued',
      company_response: 'Signed',
    }),
  ];
  return [header, ...rows].join('\n');
}
