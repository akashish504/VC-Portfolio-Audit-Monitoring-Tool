import apiClient from '@/api/axios';

export interface Page<T> {
  items: T[];
  total: number;
}

export interface ApiParameterThreshold {
  id: number;
  key: string;
  value: Record<string, unknown>;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export async function listParameterThresholds(params?: { limit?: number; offset?: number }) {
  const { data } = await apiClient.get<Page<ApiParameterThreshold>>('/api/v1/parameter-thresholds', {
    params: { limit: params?.limit ?? 500, offset: params?.offset ?? 0 },
  });
  return data;
}

export async function patchParameterThreshold(
  id: number,
  payload: { value?: Record<string, unknown>; description?: string | null },
) {
  const { data } = await apiClient.patch<ApiParameterThreshold>(`/api/v1/parameter-thresholds/${id}`, payload);
  return data;
}

export async function bulkPatchParameterThresholds(
  items: Array<{ id: number; value?: Record<string, unknown>; description?: string | null }>,
) {
  const { data } = await apiClient.patch<ApiParameterThreshold[]>('/api/v1/parameter-thresholds', { items });
  return data;
}

/** Global mapping from audit-financials dotted paths → FinancialData numeric columns */

export interface ApiFinancialMetricTerm {
  path: string;
  sign: '+' | '-';
  abs?: boolean;
}

export interface ApiFinancialMetricMapping {
  metrics: {
    revenue: ApiFinancialMetricTerm[];
    ebitda: ApiFinancialMetricTerm[];
    pbt: ApiFinancialMetricTerm[];
    pat: ApiFinancialMetricTerm[];
    cash: ApiFinancialMetricTerm[];
    debt: ApiFinancialMetricTerm[];
  };
}

export async function getFinancialMetricMapping() {
  const { data } = await apiClient.get<ApiFinancialMetricMapping>('/api/v1/financial-metric-mapping');
  return data;
}

export async function putFinancialMetricMapping(payload: ApiFinancialMetricMapping) {
  const { data } = await apiClient.put<ApiFinancialMetricMapping>('/api/v1/financial-metric-mapping', payload);
  return data;
}

export async function listFinancialMetricSchemaPaths(q?: string) {
  const { data } = await apiClient.get<{ paths: string[] }>('/api/v1/financial-metric-mapping/schema-paths', {
    params: { q },
  });
  return data.paths;
}

/** PRSubmissionDataRaw numeric columns → FinancialDataSnowflake six metrics (v2). */

export interface ApiSnowflakePRFinancialFormula {
  formula: string;
}

/** Stage-group ids understood by the backend (keyed off PortfolioCompany.investment_stage). */
export type SnowflakePRStageGroupId = 'surge_seed' | 'growth_venture';

/** P&L (flow) metrics — read from an annual yr_1/yr_2 column. */
export interface ApiSnowflakePRPnlPack {
  revenue: ApiSnowflakePRFinancialFormula;
  ebitda: ApiSnowflakePRFinancialFormula;
  pbt: ApiSnowflakePRFinancialFormula;
  pat: ApiSnowflakePRFinancialFormula;
}

/** Balance-sheet (stock) metrics — read from a point-in-time column. */
export interface ApiSnowflakePRBalancePack {
  cash: ApiSnowflakePRFinancialFormula;
  debt: ApiSnowflakePRFinancialFormula;
}

/** Surge / Seed: P&L always uses Year 1 → a single P&L slot + a balance slot. */
export interface ApiSnowflakePRSurgeSeedGroup {
  pnl: ApiSnowflakePRPnlPack;
  balance: ApiSnowflakePRBalancePack;
}

/** Growth / Venture: P&L year depends on quarter alignment → aligned + lagged slots. */
export interface ApiSnowflakePRGrowthVentureGroup {
  pnl_aligned: ApiSnowflakePRPnlPack; // Year 2
  pnl_lagged: ApiSnowflakePRPnlPack; // Year 1
  balance: ApiSnowflakePRBalancePack;
}

export interface ApiSnowflakePRFinancialMapping {
  stage_groups: {
    surge_seed: ApiSnowflakePRSurgeSeedGroup;
    growth_venture: ApiSnowflakePRGrowthVentureGroup;
  };
}

export async function getSnowflakePRFinancialMapping() {
  const { data } = await apiClient.get<ApiSnowflakePRFinancialMapping>('/api/v1/snowflake-pr-financial-mapping');
  return data;
}

export async function putSnowflakePRFinancialMapping(payload: ApiSnowflakePRFinancialMapping) {
  const { data } = await apiClient.put<ApiSnowflakePRFinancialMapping>(
    '/api/v1/snowflake-pr-financial-mapping',
    payload,
  );
  return data;
}

export async function listSnowflakePRNumericColumns(q?: string) {
  const { data } = await apiClient.get<{ columns: string[] }>(
    '/api/v1/snowflake-pr-financial-mapping/numeric-columns',
    { params: { q } },
  );
  return data.columns;
}

export interface FyEndResolveResponse {
  fy_end: string;
  review_cycle_id: string;
  review_cycle_name: string | null;
}

export async function resolveReviewCycleFromFyEnd(fyEnd: string) {
  const { data } = await apiClient.get<FyEndResolveResponse>('/api/v1/review-cycles/resolve-from-fy-end', {
    params: { fy_end: fyEnd },
  });
  return data;
}

export async function getFyEndOptions(reviewCycleId: string): Promise<string[]> {
  const { data } = await apiClient.get<{ review_cycle_id: string; options: string[] }>(
    `/api/v1/review-cycles/${encodeURIComponent(reviewCycleId)}/fy-end-options`,
  );
  return data.options;
}

export interface ApiDataSyncConfig {
  frequency: 'daily' | 'weekly';
  enabled: boolean;
  time: string;
  timezone: string;
  day_of_week: string;
  cutoff_date: string | null;
}

export async function getDataSyncConfig(): Promise<ApiDataSyncConfig> {
  const { data } = await apiClient.get<ApiDataSyncConfig>('/api/v1/data-sync-config');
  return data;
}

export async function putDataSyncConfig(payload: {
  frequency: 'daily' | 'weekly';
  cutoff_date?: string | null;
}): Promise<ApiDataSyncConfig> {
  const { data } = await apiClient.put<ApiDataSyncConfig>('/api/v1/data-sync-config', payload);
  return data;
}
