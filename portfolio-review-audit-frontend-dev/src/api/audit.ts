import apiClient from '@/api/axios';

export type ReviewCycleAuditRow = {
  id: string;
  user_id?: string | null;
  review_cycle_id?: string | null;
  action?: string | null;
  meta: Record<string, unknown>;
  updated_at: string;
  occurred_at?: string | null;
  summary?: string | null;
};

export type ParameterThresholdAuditRow = {
  id: string;
  user_id?: string | null;
  action?: string | null;
  meta: Record<string, unknown>;
  updated_at: string;
  occurred_at?: string | null;
  summary?: string | null;
};

export type CompanyAuditRow = {
  id: string;
  user_id?: string | null;
  company_id?: string | null;
  action?: string | null;
  meta: Record<string, unknown>;
  updated_at: string;
  occurred_at?: string | null;
  summary?: string | null;
};

export async function listReviewCycleAudit(params?: {
  review_cycle_id?: string;
  q?: string;
  limit?: number;
  offset?: number;
}) {
  const res = await apiClient.get('/api/v1/audit/review-cycle-view', { params });
  return res.data as { items: ReviewCycleAuditRow[]; total: number };
}

export async function listParameterThresholdAudit(params?: { q?: string; limit?: number; offset?: number }) {
  const res = await apiClient.get('/api/v1/audit/parameter-threshold-view', { params });
  return res.data as { items: ParameterThresholdAuditRow[]; total: number };
}

export type FinancialExtractionMappingAuditRow = {
  id: string;
  user_id?: string | null;
  action?: string | null;
  meta: Record<string, unknown>;
  updated_at: string;
  occurred_at?: string | null;
  summary?: string | null;
};

export async function listFinancialExtractionMappingAudit(params?: {
  q?: string;
  limit?: number;
  offset?: number;
}) {
  const res = await apiClient.get('/api/v1/audit/financial-extraction-mapping-view', { params });
  return res.data as { items: FinancialExtractionMappingAuditRow[]; total: number };
}

export type SnowflakePRFinancialMappingAuditRow = {
  id: string;
  user_id?: string | null;
  action?: string | null;
  meta: Record<string, unknown>;
  updated_at: string;
  occurred_at?: string | null;
  summary?: string | null;
};

export async function listSnowflakePRFinancialMappingAudit(params?: {
  q?: string;
  limit?: number;
  offset?: number;
}) {
  const res = await apiClient.get('/api/v1/audit/snowflake-pr-financial-mapping-view', { params });
  return res.data as { items: SnowflakePRFinancialMappingAuditRow[]; total: number };
}

export async function listCompanyAudit(params?: { company_id?: string; limit?: number; offset?: number }) {
  const res = await apiClient.get('/api/v1/audit/company-view', { params });
  return res.data as { items: CompanyAuditRow[]; total: number };
}
