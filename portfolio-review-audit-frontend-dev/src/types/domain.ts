export type AuditStatus =
  | 'Pending Review'
  | 'Discrepancy Identified'
  | 'Clarification Requested'
  | 'Resolved'
  | 'Archive Entity';

/** Company in-review status options when resolving an entity (subset of CompanyInReviewStage). */
export type EntityResolvedCompanyStage = 'Closed' | 'Closed - Flagged';

/** Company-level stage stored in `portfolio_companies.review_stage`. */
export type CompanyInReviewStage =
  | 'Not applicable'
  | 'Financials to be received'
  | 'In review'
  | 'Discrepancy identified'
  | 'No discrepancy identified'
  | 'Not comparable'
  | 'Query sent'
  | 'Query response reminder sent 1'
  | 'Query response reminder sent 2'
  | 'Response received'
  | 'Response received - Partially answered'
  | 'Response received - Call to be scheduled'
  | 'Approved'
  | 'Approved - Flagged'
  | 'Not Approved'
  | 'Not Approved - Flagged';

export interface PortfolioCompany {
  id: number;
  company_id: string;
  name: string;
  in_review_status?: string | null;
  review_cycle_id?: string | null;
  review_stage?: string | null;
  contact_name?: string | null;
  contact_email_id?: string | null;
  files_count?: number | null;

  fund?: string | null;
  investment_lead?: string | null;
  /** Growth / Venture / Seed (portfolio stage). */
  company_stage?: string | null;
  geography?: string | null;
  ownership_pct?: string | null;
  cost?: string | null;
  fmv?: string | null;
  position_is_unique?: string | null;
  consolidated_ownership_pct?: string | null;
  consolidated_cost?: string | null;
  consolidated_fmv?: string | null;
  company_category_1?: string | null;
  company_category_2?: string | null;
  scoped_in_for_audit?: string | null;
  exclusion_reason?: string | null;
  fy_end?: string | null;
  fy_end_date?: string | null;
  due_date?: string | null;
  audit_status?: string | null;
  auditor?: string | null;
  tentative_completion_date?: string | null;
  company_response?: string | null;
  peak_xv_actionable?: string | null;
  reason_to_scope_out?: string | null;

  org_chart_file_id?: number | null;

  has_orphan_files?: boolean;
  created_at?: string;
  updated_at?: string;
}

export type EntityType =
  | 'Intermediate Holding'
  | 'Associate'
  | 'Branch'
  | 'Ultimate Holding'
  | 'Subsidiary'
  | 'Holding'
  | 'Other';

export const ENTITY_TYPE_OPTIONS: EntityType[] = [
  'Intermediate Holding',
  'Associate',
  'Branch',
  'Ultimate Holding',
  'Subsidiary',
  'Holding',
  'Other',
];

export interface Entity {
  id: number;
  portfolio_company_id: number;
  name: string;
  geolocation?: string | null;
  entity_type?: EntityType | null;
  review_cycle?: string | null;
  fy_end?: string | null;
  /** Same vocabulary as portfolio company `in_review_status` (audit workflow). */
  status?: string | null;
  parent_entity_id?: number | null;
  region?: string | null;
  is_parent: boolean;
  extra_data: Record<string, unknown>;
  comments?: string | null;
  one_desk_email_status?: string | null;
  portfolio_company_name?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface FileData {
  id: number;
  portfolio_company_id: number;
  entity_id?: number | null;
  entity_name?: string | null;
  entity_geolocation?: string | null;
  /** The linked entity's financial year end (Mmm-YY); null when no entity is attached. */
  entity_fy_end?: string | null;
  filename: string;
  content_type?: string | null;
  storage_uri?: string | null;
  status?: string | null;
  tags: string[];
  size_bytes?: number | null;
  review_cycle_id?: string | null;
  portfolio_company_name?: string | null;
  portfolio_company_review_cycle_id?: string | null;
  entity_detached_acknowledged?: boolean;
  created_at?: string;
  updated_at?: string;
  /** When extraction last completed (FileOCRMetadata.updated_at); null if never processed. */
  processed_at?: string | null;
}

