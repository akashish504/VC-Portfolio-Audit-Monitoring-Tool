import apiClient from '@/api/axios';

// ── shared types ───────────────────────────────────────────────────────────────

export interface ExtractedEntity {
  llm_id: number;
  name: string;
  geolocation?: string | null;
  entity_type?: string | null;
  is_parent: boolean;
  children_ids: number[];
}

export interface ExistingEntitySummary {
  id: number;
  name: string;
  geolocation?: string | null;
  entity_type?: string | null;
  parent_entity_id?: number | null;
  is_parent: boolean;
  status?: string | null;
  file_count: number;
}

export interface PendingUpdateResponse {
  has_pending_update: false;
}

export interface AutoMatchedEntity {
  existing_entity_id: number;
  extracted_temp_id: number;
  match_reason: 'name' | 'geolocation';
  existing_entity: ExistingEntitySummary;
  extracted_entity: ExtractedEntity;
}

export interface PendingUpdateResult {
  has_pending_update: true;
  record_id: number;
  file_id: number | null;
  file_name: string;
  extracted_org_chart: ExtractedEntity[];
  requires_reconciliation: boolean;
  existing_entities: ExistingEntitySummary[];
  current_org_chart_file_id: number | null;
  auto_matched_entities: AutoMatchedEntity[];
  unmatched_existing_entities: ExistingEntitySummary[];
  unmatched_extracted_entities: ExtractedEntity[];
}

export type PendingUpdate = PendingUpdateResponse | PendingUpdateResult;

export async function getOrgChartPendingUpdate(portfolioCompanyId: number): Promise<PendingUpdate> {
  const { data } = await apiClient.get(
    `/api/v1/portfolio-companies/${portfolioCompanyId}/org-chart/pending-update`,
  );
  return data as PendingUpdate;
}

// ── apply (direct, no existing org chart) ─────────────────────────────────────

export interface AppliedEntityResult {
  id: number;
  llm_id: number;
  name: string;
  geolocation?: string | null;
  entity_type?: string | null;
  parent_entity_id?: number | null;
  is_parent: boolean;
}

export interface ApplyResponse {
  applied: boolean;
  entities: AppliedEntityResult[];
  entity_count: number;
}

export async function applyOrgChartRecord(
  recordId: number,
  appliedBy?: string,
): Promise<ApplyResponse> {
  const { data } = await apiClient.post(`/api/v1/org-chart-records/${recordId}/apply`, {
    applied_by: appliedBy ?? null,
  });
  return data as ApplyResponse;
}

// ── reconcile ─────────────────────────────────────────────────────────────────

export type ReconcileAction = 'match' | 'create' | 'keep' | 'archive';

export interface EntityMappingItem {
  existing_entity_id?: number | null;
  extracted_temp_id?: number | null;
  action: ReconcileAction;
  final_name?: string | null;
  final_geolocation?: string | null;
  final_entity_type?: string | null;
  final_is_parent?: boolean | null;
}

export interface ParentLinkItem {
  child_ref: string;
  parent_ref?: string | null;
}

export interface FileMoveItem {
  file_id: number;
  target_entity_ref?: string | null;
  acknowledge_detached?: boolean;
}

export interface ReconcilePayload {
  portfolio_company_id: number;
  entity_mappings: EntityMappingItem[];
  parent_links: ParentLinkItem[];
  file_moves: FileMoveItem[];
  applied_by?: string | null;
}

export interface ReconciledEntityResult {
  id: number;
  name: string;
  geolocation?: string | null;
  entity_type?: string | null;
  parent_entity_id?: number | null;
  is_parent: boolean;
  status?: string | null;
}

export interface ReconcileResponse {
  reconciled: boolean;
  entities: ReconciledEntityResult[];
  entity_count: number;
  new_org_chart_file_id: number | null;
}

export async function reconcileOrgChartRecord(
  recordId: number,
  payload: ReconcilePayload,
): Promise<ReconcileResponse> {
  const { data } = await apiClient.post(
    `/api/v1/org-chart-records/${recordId}/reconcile`,
    payload,
  );
  return data as ReconcileResponse;
}

// ── org chart download ────────────────────────────────────────────────────────

export async function downloadOrgChartXlsx(portfolioCompanyId: number, companyName?: string): Promise<void> {
  const response = await apiClient.get(
    `/api/v1/portfolio-companies/${portfolioCompanyId}/org-chart/download`,
    { responseType: 'blob' },
  );
  const url = URL.createObjectURL(response.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  const safe = (companyName ?? String(portfolioCompanyId)).replace(/[^a-zA-Z0-9_\-]/g, '_');
  a.download = `org_chart_${safe}.xlsx`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── reconciliation comparison download ───────────────────────────────────────

export async function downloadReconciliationComparisonXlsx(recordId: number): Promise<void> {
  const response = await apiClient.get(
    `/api/v1/org-chart-records/${recordId}/comparison-download`,
    { responseType: 'blob' },
  );
  const url = URL.createObjectURL(response.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `org_chart_reconciliation_${recordId}.xlsx`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── dismiss ────────────────────────────────────────────────────────────────────

export async function dismissOrgChartRecord(
  recordId: number,
  appliedBy?: string,
): Promise<void> {
  await apiClient.post(`/api/v1/org-chart-records/${recordId}/dismiss`, {
    applied_by: appliedBy ?? null,
  });
}
