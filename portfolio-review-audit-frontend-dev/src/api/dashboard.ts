import apiClient from '@/api/axios';

/** Analytics dashboard API (Scoping view — Phase 1).
 *  Mirrors backend `/api/v1/dashboard/*`. Types are snake_case to match the API. */

export interface Page<T> {
  items: T[];
  total: number;
}

export interface ReviewCycleOption {
  id: string;
  name: string | null;
}

/**
 * Default cycle = the most recent CYxx-FYyy cycle whose period has already ended
 * relative to today (i.e. the cycle ending in May of FY year < current month/year).
 * This means we show the last completed cycle, not the currently-active one.
 * Falls back to the most recent CYxx-FYyy cycle if none have ended yet,
 * or to the first available cycle if no CYxx-FYyy cycles exist.
 */
export function pickDefaultCycle(cycles: { id: string }[]): string | undefined {
  const now = new Date();
  const cyPattern = /^CY(\d{2})-FY(\d{2})$/i;
  const parsed = cycles
    .map((c) => {
      const m = cyPattern.exec(c.id);
      if (!m) return null;
      const fyYear = 2000 + Number(m[2]);
      // Cycle ends at end of May of FY year (month index 4 = May, day 31).
      const cycleEnd = new Date(fyYear, 4, 31);
      return { id: c.id, cycleEnd };
    })
    .filter(Boolean) as { id: string; cycleEnd: Date }[];

  parsed.sort((a, b) => a.cycleEnd.getTime() - b.cycleEnd.getTime());

  // Find the latest cycle that has already ended (cycleEnd < today).
  const ended = parsed.filter((c) => c.cycleEnd < now);
  if (ended.length) return ended[ended.length - 1].id;

  // All cycles are future/current — fall back to the most recent CYxx-FYyy.
  if (parsed.length) return parsed[parsed.length - 1].id;

  return cycles[0]?.id;
}

export interface DashboardFilters {
  review_cycles: ReviewCycleOption[];
  funds: string[];
  geographies: string[];
  sectors: string[];
  strategies: string[];
  categories: string[];
  investment_leads: string[];
}

export interface PCMDealRow {
  id: number;
  deal_id: string;
  deal_name: string;
  fund: string;
  strategy: string;
  category: string | null;
  fy_end: string | null;
  il_main: string | null;
  deal_level_stage_1: string | null;
  deal_level_stage_2: string | null;
  auditor: string | null;
  category_of_auditor: string | null;
  consolidated_cost: number | null;
  consolidated_fmv: number | null;
  /** Master-scoping decision: true = scoped in for audit, false = scoped out. */
  scoping_for_audit: boolean | null;
  reason_for_exclusion: string | null;
}

export interface PCMDealQuery {
  deal_level_stage_1?: string;
  deal_level_stage_2?: string;
  scoping_for_audit?: boolean;
  fy_end?: string;
  category_of_auditor?: string;
  strategy?: string;
  geo_l1?: string;
  india_sea_only?: boolean;
  deal_ids?: string[];
  unique?: boolean;
  // Dashboard dimension filters (so a drill respects the active filter bar).
  fund?: string;
  sector?: string;
  category?: string;
  investment_lead?: string;
  review_cycle_id?: string;
  limit?: number;
  offset?: number;
}

export async function listPCMDeals({ deal_ids, ...params }: PCMDealQuery) {
  const { data } = await apiClient.get<Page<PCMDealRow>>(
    '/api/v1/dashboard/scoping/pcm-deals',
    { params: clean({ limit: 50, offset: 0, ...params, deal_ids: deal_ids?.length ? deal_ids.join(',') : undefined }) },
  );
  return data;
}

export interface ScopingOverview {
  review_cycle_id: string | null;
  total_companies: number;
  new_added: number;
  removed: number;
  /** Master-scoping deal counts: scoping_for_audit true / false. */
  scoped_in: number;
  scoped_out: number;
  status_breakdown: Record<string, number>;
  stage_1_breakdown: Record<string, number>;
  stage_2_breakdown: Record<string, number>;
}

export interface StatusMatrixRow {
  status: string;
  by_month: Record<string, number>;
  total: number;
}

export interface StatusMatrix {
  review_cycle_id: string | null;
  months: string[];
  rows: StatusMatrixRow[];
  column_totals: Record<string, number>;
  grand_total: number;
  due_dates_by_month: Record<string, string>;
}

export interface DashboardCompanyRow {
  portfolio_company_id: number;
  company_id: string | null;
  company_name: string;
  fund: string | null;
  geography: string | null;
  fy_end: string | null;
  strategy: string | null;
  investment_lead_1: string | null;
  investment_lead_2: string | null;
  consolidated_cost: string | null;
  consolidated_fmv: string | null;
  latest_category: string | null;
  audit_status: string | null;
  in_review_status: string | null;
  review_stage: string | null;
  last_year_audit_status: string | null;
  flagged_continuous_overdue?: boolean;
}

export interface YoYRow {
  category: string;
  current: number;
  prior: number;
}

export interface YoYSummary {
  review_cycle_id: string | null;
  /** The cycle the prior-year donut/column represents (drill into the right year). */
  prior_review_cycle_id: string | null;
  current_label: string;
  prior_label: string;
  rows: YoYRow[];
  current_total: number;
  prior_total: number;
}

/** Filters shared across every scoping endpoint. */
export interface ScopingFilters {
  review_cycle_id?: string;
  fund?: string;
  geography?: string;
  sector?: string;
  strategy?: string;
  category?: string;
  investment_lead?: string;
}

export interface ScopingCompanyQuery extends ScopingFilters {
  status?: string;
  fy_end?: string;
  auditor_category?: string;
  q?: string;
  completion_month?: string;
  scoped_in?: boolean;
  flagged?: boolean;
  entity_status?: string;
  audit_status?: string;
  actual_status?: string;
  opinion?: string;
  delta?: 'added' | 'removed';
  deal_level_stage_1?: string;
  deal_level_stage_2?: string;
  limit?: number;
  offset?: number;
}

export interface DiscrepancyDetailRow {
  portfolio_company_id: number | null;
  entity_id: number | null;
  company: string;
  entity: string | null;
  metric: string;
  mis_amount: number | null;
  afs_amount: number | null;
  diff_pct: number | null;
  bucket: string | null;
  variance_category: string | null;
  reviewer_remarks: string | null;
  company_response: string | null;
  status: string | null;
}

export async function getVarianceDiscrepancyCompanies(params: {
  metric?: string;
  bucket?: 'gt' | 'lt' | 'nc';
  subcategory?: string;
  review_cycle_id?: string;
  limit?: number;
  offset?: number;
}) {
  const { data } = await apiClient.get<Page<DiscrepancyDetailRow>>(
    '/api/v1/dashboard/variance/discrepancy-companies',
    { params: clean({ limit: 25, offset: 0, ...params }) },
  );
  return data;
}

/** Drop undefined/empty values so we never send blank query params. */
function clean<T extends object>(params: T): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''),
  );
}

export async function getDashboardFilters() {
  const { data } = await apiClient.get<DashboardFilters>('/api/v1/dashboard/filters');
  return data;
}

export async function getScopingOverview(params: ScopingFilters = {}) {
  const { data } = await apiClient.get<ScopingOverview>('/api/v1/dashboard/scoping/overview', {
    params: clean(params),
  });
  return data;
}

export async function getScopingTentativeMatrix(params: ScopingFilters = {}) {
  const { data } = await apiClient.get<StatusMatrix>('/api/v1/dashboard/scoping/tentative-matrix', {
    params: clean(params),
  });
  return data;
}

export async function getScopingActualMatrix(params: ScopingFilters = {}) {
  const { data } = await apiClient.get<StatusMatrix>('/api/v1/dashboard/scoping/actual-matrix', {
    params: clean(params),
  });
  return data;
}

/** Master-scoping deal-stage × FYE-month matrix (rows = stage 1/2 options).
 *  basis: 'cid' = unique CID counts, 'cid_strategy' = unique CID + Strategy. */
export async function getScopingDealStageMatrix(
  params: ScopingFilters & { stage: '1' | '2'; basis: 'cid' | 'cid_strategy' },
) {
  const { data } = await apiClient.get<StatusMatrix>('/api/v1/dashboard/scoping/deal-stage-matrix', {
    params: clean(params),
  });
  return data;
}

export async function listScopingCompanies(params: ScopingCompanyQuery = {}) {
  const { data } = await apiClient.get<Page<DashboardCompanyRow>>(
    '/api/v1/dashboard/scoping/companies',
    { params: clean({ limit: 50, offset: 0, ...params }) },
  );
  return data;
}

export interface ReportViewRow {
  no: number;
  portfolio_company_id: number | null;
  cid: string | null;
  company: string;
  fy_end: string | null;
  current_status: string | null;
  strategy: string | null;
  category: string | null;
  consolidated_cost: string | null;
  consolidated_fmv: string | null;
  investment_lead_1: string | null;
  investment_lead_2: string | null;
  pending_since_days: number | null;
  auditor: string | null;
  auditor_category: string | null;
}

export async function getVarianceReportView(params: {
  kind: 'long_overdue' | 'previous_years_pending';
  current_status?: string;
  review_cycle_id?: string;
  limit?: number;
  offset?: number;
}) {
  const { data } = await apiClient.get<Page<ReportViewRow>>('/api/v1/dashboard/variance/report-view', {
    params: clean({ limit: 25, offset: 0, ...params }),
  });
  return data;
}

export interface MetricCell {
  metric: string;
  afs: number | null;
  mis: number | null;
  diff_value: number | null;
  diff_pct: number | null;
  bucket: string | null;
  to_be_sent: string | null;
  status: string | null;
  company_remarks: string | null;
  subcategory: string | null;
}

export interface VarianceCompanyDetailRow {
  no: number;
  portfolio_company_id: number;
  entity_id: number | null;
  cid: string | null;
  company: string;
  legal_name: string | null;
  entity: string | null;
  currency: string | null;
  holding_or_subsidiary: string | null;
  consolidated_or_standalone: string | null;
  date_of_signing: string | null;
  current_status: string | null;
  financials_added: string | null;
  in_review: string | null;
  tat_review: number | null;
  queries_sent: string | null;
  reminder_1: string | null;
  reminder_2: string | null;
  tat_queries_sent: number | null;
  responded: string | null;
  tat_response: number | null;
  approved_rejected_on: string | null;
  tat_approval: number | null;
  overall_tat: number | null;
  highlighted_to_investor: boolean;
  reporting_standards: string | null;
  auditor_name: string | null;
  auditor_partner: string | null;
  auditor_category: string | null;
  status_of_financials: string | null;
  status_of_signed_financials: string | null;
  audit_report_status: string | null;
  auditor_opinion: string | null;
  emphasis_of_matter: string | null;
  other_matters: string | null;
  going_concern: string | null;
  caro_availability: string | null;
  caro_gaps: string | null;
  caro_notes: string | null;
  internal_financial_control: string | null;
  ifc_gaps: string | null;
  metrics: MetricCell[];
  overall_comments: string | null;
  overall_status: string | null;
  flagged_continuous_overdue: boolean;
}

export async function getVarianceCompanyDetails(params: {
  review_cycle_id?: string;
  limit?: number;
  offset?: number;
}) {
  const { data } = await apiClient.get<Page<VarianceCompanyDetailRow>>(
    '/api/v1/dashboard/variance/company-details',
    { params: clean({ limit: 25, offset: 0, ...params }) },
  );
  return data;
}

export async function getScopingOverallStatus(params: ScopingFilters = {}) {
  const { data } = await apiClient.get<YoYSummary>('/api/v1/dashboard/scoping/overall-status', {
    params: clean(params),
  });
  return data;
}

export async function getScopingAuditorSummary(params: ScopingFilters = {}) {
  const { data } = await apiClient.get<YoYSummary>('/api/v1/dashboard/scoping/auditor-summary', {
    params: clean(params),
  });
  return data;
}

export interface CompletionTimeline {
  review_cycle_id: string | null;
  status: string | null;
  fy_end: string | null;
  months: string[];
  counts: Record<string, number>;
  total: number;
}

export async function getScopingCompletionTimeline(params: {
  review_cycle_id?: string;
  status?: string;
  fy_end?: string;
}) {
  const { data } = await apiClient.get<CompletionTimeline>(
    '/api/v1/dashboard/scoping/completion-timeline',
    { params: clean(params) },
  );
  return data;
}

// ── Variance / Review dashboard ──────────────────────────────────────────────
export interface StatusCount {
  status: string;
  count: number;
}

export interface VarianceActionables {
  review_cycle_id: string | null;
  rows: StatusCount[];
  total: number;
}

export interface VarianceCycleSummary {
  review_cycle_id: string | null;
  scoped_in_total: number;
  scoped_in_by_month: Record<string, number>;
  status_summary: StatusCount[];
  total: number;
}

export interface ComplianceSummary {
  review_cycle_id: string | null;
  completed_highlighted_to_investor: number;
  opinions: Record<string, number>;
  emphasis_of_matter: Record<string, number>;
  other_matters: Record<string, number>;
}

export interface MetricDiscrepancyRow {
  metric: string;
  gt_10: number;
  lt_10: number;
  not_comparable: number;
  total: number;
  /** Configured variance threshold (fraction, e.g. 0.10 = ±10%) from settings. */
  threshold_pct?: number | null;
}

export interface DiscrepancySummary {
  review_cycle_id: string | null;
  metrics: MetricDiscrepancyRow[];
  diff_more_than_3_params: number;
}

export interface SubcategoryCount {
  subcategory: string;
  count: number;
}

export interface DiscrepancySubcategory {
  review_cycle_id: string | null;
  metric: string;
  total: number;
  rows: SubcategoryCount[];
}

interface CycleParam {
  review_cycle_id?: string;
}

export async function getVarianceActionables(params: CycleParam = {}) {
  const { data } = await apiClient.get<VarianceActionables>(
    '/api/v1/dashboard/variance/actionables-summary',
    { params: clean(params) },
  );
  return data;
}

export async function getVarianceCycleSummary(params: CycleParam = {}) {
  const { data } = await apiClient.get<VarianceCycleSummary>(
    '/api/v1/dashboard/variance/cycle-summary',
    { params: clean(params) },
  );
  return data;
}

/** GET an XLSX endpoint as a blob, resolving the server-supplied filename. */
async function downloadXlsx(url: string, params: object, fallbackName: string) {
  const response = await apiClient.get(url, { params: clean(params), responseType: 'blob' });
  const cd = response.headers['content-disposition'] as string | undefined;
  const match = cd?.match(/filename="([^"]+)"/);
  return { blob: new Blob([response.data as BlobPart]), filename: match?.[1] ?? fallbackName };
}

/** Download the current-cycle review-status breakdown (one row per deal × status) as XLSX. */
export async function downloadVarianceStatusBreakdown(params: CycleParam = {}) {
  return downloadXlsx(
    '/api/v1/dashboard/variance/status-breakdown/download',
    params,
    'review_status_breakdown.xlsx',
  );
}

/** Download the COMPLETE (non-paginated) company list for the given scoping filters. */
export async function downloadScopingCompanies(params: ScopingCompanyQuery = {}) {
  return downloadXlsx(
    '/api/v1/dashboard/scoping/companies/download',
    params,
    'scoping_companies.xlsx',
  );
}

/** Download the COMPLETE (non-paginated) master-scoping deal list for the given filters. */
export async function downloadScopingPCMDeals({ deal_ids, ...params }: Omit<PCMDealQuery, 'limit' | 'offset'> = {}) {
  return downloadXlsx(
    '/api/v1/dashboard/scoping/pcm-deals/download',
    { ...params, deal_ids: deal_ids?.length ? deal_ids.join(',') : undefined },
    'scoping_deals.xlsx',
  );
}

/** Download a Timeline table (the aggregated grid) as XLSX. */
export type TimelineTableKey =
  | 'strategy_all'
  | 'strategy_included'
  | 'analysis'
  | 'status'
  | 'matrices'
  | 'all';

export async function downloadTimelineTable(params: {
  review_cycle_id?: string;
  table: TimelineTableKey;
}) {
  return downloadXlsx('/api/v1/dashboard/timeline/summary/download', params, 'timeline.xlsx');
}

// ── Aggregated-grid downloads for the Scoping + Variance summary tables ───────
export async function downloadStatusMatrix(params: { review_cycle_id?: string; view: 'tentative' | 'actual' }) {
  return downloadXlsx('/api/v1/dashboard/scoping/status-matrix/download', params, 'scoping_status.xlsx');
}

export async function downloadDealStageMatrix(
  params: ScopingFilters & { stage: '1' | '2'; basis?: 'cid' | 'cid_strategy' },
) {
  return downloadXlsx('/api/v1/dashboard/scoping/deal-stage-matrix/download', params, 'deal_stage.xlsx');
}

export async function downloadOverallStatus(params: ScopingFilters = {}) {
  return downloadXlsx('/api/v1/dashboard/scoping/overall-status/download', params, 'overall_status.xlsx');
}

export async function downloadAuditorSummary(params: ScopingFilters = {}) {
  return downloadXlsx('/api/v1/dashboard/scoping/auditor-summary/download', params, 'auditor_summary.xlsx');
}

export async function downloadDiscrepancySummary(params: CycleParam = {}) {
  return downloadXlsx('/api/v1/dashboard/variance/discrepancy-summary/download', params, 'discrepancy_summary.xlsx');
}

export async function downloadDiscrepancyReasons(params: CycleParam = {}) {
  return downloadXlsx('/api/v1/dashboard/variance/discrepancy-reasons/download', params, 'discrepancy_reasons.xlsx');
}

export async function getVarianceCompliance(params: CycleParam = {}) {
  const { data } = await apiClient.get<ComplianceSummary>(
    '/api/v1/dashboard/variance/compliance-summary',
    { params: clean(params) },
  );
  return data;
}

export async function getVarianceDiscrepancySummary(params: CycleParam = {}) {
  const { data } = await apiClient.get<DiscrepancySummary>(
    '/api/v1/dashboard/variance/discrepancy-summary',
    { params: clean(params) },
  );
  return data;
}

export async function getVarianceDiscrepancySubcategory(metric: string, params: CycleParam = {}) {
  const { data } = await apiClient.get<DiscrepancySubcategory>(
    '/api/v1/dashboard/variance/discrepancy-subcategory',
    { params: clean({ metric, ...params }) },
  );
  return data;
}

/** Above-threshold variance reasons for ALL metrics at once (the combined matrix). */
export interface DiscrepancySubcategoryMatrix {
  review_cycle_id: string | null;
  metrics: DiscrepancySubcategory[];
}

export async function getVarianceDiscrepancySubcategoryMatrix(params: CycleParam = {}) {
  const { data } = await apiClient.get<DiscrepancySubcategoryMatrix>(
    '/api/v1/dashboard/variance/discrepancy-subcategory-matrix',
    { params: clean(params) },
  );
  return data;
}

// ── Timeline dashboard ───────────────────────────────────────────────────────
// All figures come from the master scoping table (strategy, geo_l1, scoping_for_audit,
// deal_level_stage_1, fy_end). Strategy tables double-count multi-strategy companies;
// the "for analysis" tables dedupe by CID.
export interface TimelineStrategyRow {
  strategy: string;
  india: number;
  sea: number;
  total: number;
}

export interface TimelineStrategyTable {
  rows: TimelineStrategyRow[];
  total: TimelineStrategyRow;
}

export interface TimelineAnalysisCounts {
  india: number;
  sea: number;
  total: number;
}

export interface TimelineStatusRow {
  status: string;
  overall: number;
  by_strategy: Record<string, number>;
  india: number;
  sea: number;
}

export interface TimelineStatusTable {
  strategies: string[];
  rows: TimelineStatusRow[];
  total: TimelineStatusRow;
}

export interface TimelineMatrixRow {
  status: string;
  by_month: Record<string, number>;
  total: number;
}

export interface TimelineMatrix {
  scope: string; // 'Overall' | strategy
  geo: string; // 'India and SEA' | 'India' | 'SEA'
  months: string[];
  rows: TimelineMatrixRow[];
  column_totals: Record<string, number>;
  grand_total: number;
}

export interface TimelineSummary {
  review_cycle_id: string | null;
  strategy_all: TimelineStrategyTable;
  strategy_included: TimelineStrategyTable;
  analysis_all: TimelineAnalysisCounts;
  analysis_included: TimelineAnalysisCounts;
  status_table: TimelineStatusTable;
  matrices: TimelineMatrix[];
  status_rows: string[];
}

export async function getTimelineSummary(params: { review_cycle_id?: string } = {}) {
  const { data } = await apiClient.get<TimelineSummary>('/api/v1/dashboard/timeline/summary', {
    params: clean(params),
  });
  return data;
}

// ── Filterable audit-timeline chart ──────────────────────────────────────────
// Row-level feed: one row per master-scoping record, pre-normalised so the chart
// can filter + bucket (by tentative-completion month) entirely client-side.
export interface TimelineChartRow {
  deal_id: string;
  strategy: string | null;
  geo: string | null; // 'India' | 'SEA' | null
  scoped_in: boolean;
  fy_end: string | null;
  stage_1: string | null;
  due_bucket: string | null; // 'Within due date' | 'Overdue' | null
  month_key: string; // sortable 'YYYY-MM'; '9999-99' = unscheduled
  month_label: string; // e.g. 'Apr-2026' | 'Unscheduled'
}

export interface TimelineChartFeed {
  review_cycle_id: string | null;
  rows: TimelineChartRow[];
  geo_options: string[];
  strategy_options: string[];
  fy_end_options: string[];
  due_options: string[];
}

export async function getTimelineChart(params: { review_cycle_id?: string } = {}) {
  const { data } = await apiClient.get<TimelineChartFeed>('/api/v1/dashboard/timeline/chart', {
    params: clean(params),
  });
  return data;
}
