export type AuditStatus =
  | 'Pending Review'
  | 'Discrepancy Identified'
  | 'Clarification Requested'
  | 'Resolved';

export interface AuditPeriod {
  id: string;
  label: string;
  status: AuditStatus;
  isActive: boolean;
  createdAt: string;
}

export interface Company {
  id: string;
  name: string;
  parentId: string | null;
  status: AuditStatus;
  auditPeriod: string;
  auditPeriods: AuditPeriod[];
  contactEmail: string;
  contactName: string;
  hasAuditReport: boolean;
  isArchived?: boolean;
  entityStatus?: AuditStatus;
  geolocation?: string;
}

export interface SourceReference {
  fileId: string;
  fileName: string;
  page: number;
}

export interface ReconciliationField {
  Field_Name: string;
  Source_Value: number;
  Extracted_Value: number;
  entityId?: string;
  entityName?: string;
  sourceRef?: SourceReference;
}

export type DiscrepancyStatus = 'Open' | 'Under Review' | 'Resolved' | 'Dismissed';
export type DiscrepancyCategory = 'system' | 'manual';

export interface DiscrepancyItem {
  id: string;
  fieldName: string;
  sourceValue: number;
  extractedValue: number;
  entityId: string;
  entityName: string;
  enabled: boolean;
  l1_reviewer_remarks: string;
  l2_reviewer_remarks: string;
  highlighted_to_investor: boolean;
  discrepancyType: string;
  discrepancyText: string;
  discrepancyStatus: DiscrepancyStatus;
  discrepancyCategory: DiscrepancyCategory;
}

export interface EntityFile {
  id: string;
  fileName: string;
  entityId: string;
  entityName: string;
  companyId: string;
  reviewPeriod: string;
}

export interface EmailThread {
  id: string;
  companyId: string;
  subject: string;
  timestamp: string;
  from: string;
  to: string;
  body: string;
  status: 'draft' | 'sent' | 'received';
}

export interface EmailTemplate {
  id: string;
  name: string;
  subject: string;
  body: string;
  category: 'discrepancy' | 'follow-up' | 'confirmation' | 'general';
}

export interface TaggedFile {
  id: string;
  fileName: string;
  fileType: string;
  uploadedAt: string;
  size: string;
  taggedEntityId: string | null;
  taggedEntityName: string | null;
  status: 'processed' | 'pending' | 'error';
}

export interface AuditLogEntry {
  id: string;
  companyId: string;
  action: string;
  timestamp: string;
  user: string;
  details: string;
}

export const companies: Company[] = [
  {
    id: 'holding',
    name: 'Vantage Capital Partners',
    parentId: null,
    status: 'Pending Review',
    auditPeriod: 'Q4 2024',
    auditPeriods: [
      { id: 'ap-h-1', label: 'Q4 2024', status: 'Pending Review', isActive: true, createdAt: '2025-01-01T00:00:00Z' },
      { id: 'ap-h-2', label: 'Q3 2024', status: 'Resolved', isActive: false, createdAt: '2024-10-01T00:00:00Z' },
    ],
    contactEmail: '',
    contactName: '',
    hasAuditReport: false,
    entityStatus: 'Pending Review',
    geolocation: 'United States',
  },
  {
    id: 'acme',
    name: 'Acme Corp',
    parentId: 'holding',
    status: 'Discrepancy Identified',
    auditPeriod: 'Q4 2024',
    auditPeriods: [
      { id: 'ap-a-1', label: 'Q4 2024', status: 'Discrepancy Identified', isActive: true, createdAt: '2025-01-01T00:00:00Z' },
      { id: 'ap-a-2', label: 'Q3 2024', status: 'Resolved', isActive: false, createdAt: '2024-10-01T00:00:00Z' },
      { id: 'ap-a-3', label: 'Q2 2024', status: 'Resolved', isActive: false, createdAt: '2024-07-01T00:00:00Z' },
    ],
    contactEmail: 'j.chen@acmecorp.com',
    contactName: 'James Chen',
    hasAuditReport: true,
    entityStatus: 'Discrepancy Identified',
    geolocation: 'United States',
  },
  {
    id: 'acme-eu',
    name: 'Acme Europe GmbH',
    parentId: 'acme',
    status: 'Pending Review',
    auditPeriod: 'Q4 2024',
    auditPeriods: [{ id: 'ap-ae-1', label: 'Q4 2024', status: 'Pending Review', isActive: true, createdAt: '2025-01-01T00:00:00Z' }],
    contactEmail: 'k.mueller@acme-eu.de',
    contactName: 'Klaus Mueller',
    hasAuditReport: false,
    entityStatus: 'Pending Review',
    geolocation: 'Germany',
  },
  {
    id: 'acme-asia',
    name: 'Acme Asia Pacific',
    parentId: 'acme',
    status: 'Resolved',
    auditPeriod: 'Q4 2024',
    auditPeriods: [
      { id: 'ap-aa-1', label: 'Q4 2024', status: 'Resolved', isActive: true, createdAt: '2025-01-01T00:00:00Z' },
      { id: 'ap-aa-2', label: 'Q3 2024', status: 'Resolved', isActive: false, createdAt: '2024-10-01T00:00:00Z' },
    ],
    contactEmail: 'l.tanaka@acme-ap.jp',
    contactName: 'Lisa Tanaka',
    hasAuditReport: true,
    entityStatus: 'Resolved',
    geolocation: 'Japan',
  },
  {
    id: 'nexus',
    name: 'Nexus Technologies',
    parentId: 'holding',
    status: 'Clarification Requested',
    auditPeriod: 'Q4 2024',
    auditPeriods: [
      { id: 'ap-n-1', label: 'Q4 2024', status: 'Clarification Requested', isActive: true, createdAt: '2025-01-01T00:00:00Z' },
      { id: 'ap-n-2', label: 'Q3 2024', status: 'Resolved', isActive: false, createdAt: '2024-10-01T00:00:00Z' },
    ],
    contactEmail: 'm.rodriguez@nexustech.io',
    contactName: 'Maria Rodriguez',
    hasAuditReport: true,
    entityStatus: 'Clarification Requested',
    geolocation: 'United States',
  },
  {
    id: 'nexus-ai',
    name: 'Nexus AI Labs',
    parentId: 'nexus',
    status: 'Pending Review',
    auditPeriod: 'Q4 2024',
    auditPeriods: [{ id: 'ap-na-1', label: 'Q4 2024', status: 'Pending Review', isActive: true, createdAt: '2025-01-01T00:00:00Z' }],
    contactEmail: 'r.patel@nexus-ai.io',
    contactName: 'Raj Patel',
    hasAuditReport: false,
    entityStatus: 'Pending Review',
    geolocation: 'India',
  },
  {
    id: 'meridian',
    name: 'Meridian Health',
    parentId: 'holding',
    status: 'Discrepancy Identified',
    auditPeriod: 'Q4 2024',
    auditPeriods: [
      { id: 'ap-m-1', label: 'Q4 2024', status: 'Discrepancy Identified', isActive: true, createdAt: '2025-01-01T00:00:00Z' },
      { id: 'ap-m-2', label: 'Q3 2024', status: 'Resolved', isActive: false, createdAt: '2024-10-01T00:00:00Z' },
    ],
    contactEmail: 's.johnson@meridianhealth.com',
    contactName: 'Sarah Johnson',
    hasAuditReport: true,
    entityStatus: 'Discrepancy Identified',
    geolocation: 'United Kingdom',
  },
  {
    id: 'meridian-rx',
    name: 'Meridian Pharma',
    parentId: 'meridian',
    status: 'Pending Review',
    auditPeriod: 'Q4 2024',
    auditPeriods: [{ id: 'ap-mr-1', label: 'Q4 2024', status: 'Pending Review', isActive: true, createdAt: '2025-01-01T00:00:00Z' }],
    contactEmail: 'd.kim@meridian-rx.com',
    contactName: 'David Kim',
    hasAuditReport: true,
    entityStatus: 'Pending Review',
    geolocation: 'Singapore',
  },
];

export const entityFiles: EntityFile[] = [
  { id: 'ef1', fileName: 'Acme_Corp_Q4_2024_Audit.pdf', entityId: 'acme', entityName: 'Acme Corp', companyId: 'acme', reviewPeriod: 'Q4 2024' },
  { id: 'ef1b', fileName: 'Acme_Corp_Q4_2024_Supplementary.xlsx', entityId: 'acme', entityName: 'Acme Corp', companyId: 'acme', reviewPeriod: 'Q4 2024' },
  { id: 'ef2', fileName: 'Acme_EU_Q4_2024_Audit.pdf', entityId: 'acme-eu', entityName: 'Acme Europe GmbH', companyId: 'acme', reviewPeriod: 'Q4 2024' },
  { id: 'ef2b', fileName: 'Acme_EU_Q4_2024_Annexure.pdf', entityId: 'acme-eu', entityName: 'Acme Europe GmbH', companyId: 'acme', reviewPeriod: 'Q4 2024' },
  { id: 'ef3', fileName: 'Acme_Asia_Q4_2024_Audit.pdf', entityId: 'acme-asia', entityName: 'Acme Asia Pacific', companyId: 'acme', reviewPeriod: 'Q4 2024' },
  { id: 'ef4', fileName: 'Acme_Corp_Q3_2024_Audit.pdf', entityId: 'acme', entityName: 'Acme Corp', companyId: 'acme', reviewPeriod: 'Q3 2024' },
  { id: 'ef5', fileName: 'Nexus_Tech_Q4_2024_Report.pdf', entityId: 'nexus', entityName: 'Nexus Technologies', companyId: 'nexus', reviewPeriod: 'Q4 2024' },
  { id: 'ef5b', fileName: 'Nexus_Tech_Q4_2024_Notes.pdf', entityId: 'nexus', entityName: 'Nexus Technologies', companyId: 'nexus', reviewPeriod: 'Q4 2024' },
  { id: 'ef6', fileName: 'Nexus_AI_Q4_2024_Report.pdf', entityId: 'nexus-ai', entityName: 'Nexus AI Labs', companyId: 'nexus', reviewPeriod: 'Q4 2024' },
  { id: 'ef7', fileName: 'Meridian_Health_Q4_2024.pdf', entityId: 'meridian', entityName: 'Meridian Health', companyId: 'meridian', reviewPeriod: 'Q4 2024' },
  { id: 'ef7b', fileName: 'Meridian_Health_Q4_2024_Schedules.xlsx', entityId: 'meridian', entityName: 'Meridian Health', companyId: 'meridian', reviewPeriod: 'Q4 2024' },
  { id: 'ef8', fileName: 'Meridian_Pharma_Q4_2024.pdf', entityId: 'meridian-rx', entityName: 'Meridian Pharma', companyId: 'meridian', reviewPeriod: 'Q4 2024' },
];

export const reconciliationData: Record<string, ReconciliationField[]> = {
  acme: [
    {
      Field_Name: 'Revenue',
      Source_Value: 142500000,
      Extracted_Value: 142500000,
      entityId: 'acme',
      entityName: 'Acme Corp',
      sourceRef: { fileId: 'ef1', fileName: 'Acme_Corp_Q4_2024_Audit.pdf', page: 10 },
    },
    {
      Field_Name: 'COGS',
      Source_Value: 85200000,
      Extracted_Value: 85950000,
      entityId: 'acme',
      entityName: 'Acme Corp',
      sourceRef: { fileId: 'ef1', fileName: 'Acme_Corp_Q4_2024_Audit.pdf', page: 12 },
    },
    {
      Field_Name: 'Gross Profit',
      Source_Value: 57300000,
      Extracted_Value: 56550000,
      entityId: 'acme',
      entityName: 'Acme Corp',
      sourceRef: { fileId: 'ef1', fileName: 'Acme_Corp_Q4_2024_Audit.pdf', page: 14 },
    },
    {
      Field_Name: 'EBITDA',
      Source_Value: 28400000,
      Extracted_Value: 28400000,
      entityId: 'acme',
      entityName: 'Acme Corp',
      sourceRef: { fileId: 'ef1', fileName: 'Acme_Corp_Q4_2024_Audit.pdf', page: 18 },
    },
    {
      Field_Name: 'Net Income',
      Source_Value: 18200000,
      Extracted_Value: 18350000,
      entityId: 'acme',
      entityName: 'Acme Corp',
      sourceRef: { fileId: 'ef1', fileName: 'Acme_Corp_Q4_2024_Audit.pdf', page: 22 },
    },
    {
      Field_Name: 'Total Assets',
      Source_Value: 312000000,
      Extracted_Value: 312000000,
      entityId: 'acme',
      entityName: 'Acme Corp',
      sourceRef: { fileId: 'ef1', fileName: 'Acme_Corp_Q4_2024_Audit.pdf', page: 28 },
    },
    {
      Field_Name: 'Total Liabilities',
      Source_Value: 178000000,
      Extracted_Value: 179200000,
      entityId: 'acme',
      entityName: 'Acme Corp',
      sourceRef: { fileId: 'ef1', fileName: 'Acme_Corp_Q4_2024_Audit.pdf', page: 30 },
    },
    {
      Field_Name: 'Shareholders Equity',
      Source_Value: 134000000,
      Extracted_Value: 132800000,
      entityId: 'acme',
      entityName: 'Acme Corp',
      sourceRef: { fileId: 'ef1', fileName: 'Acme_Corp_Q4_2024_Audit.pdf', page: 32 },
    },
    {
      Field_Name: 'Revenue',
      Source_Value: 32000000,
      Extracted_Value: 32150000,
      entityId: 'acme-eu',
      entityName: 'Acme Europe GmbH',
      sourceRef: { fileId: 'ef2', fileName: 'Acme_EU_Q4_2024_Audit.pdf', page: 8 },
    },
    {
      Field_Name: 'COGS',
      Source_Value: 18500000,
      Extracted_Value: 18500000,
      entityId: 'acme-eu',
      entityName: 'Acme Europe GmbH',
      sourceRef: { fileId: 'ef2', fileName: 'Acme_EU_Q4_2024_Audit.pdf', page: 10 },
    },
    {
      Field_Name: 'Net Income',
      Source_Value: 6200000,
      Extracted_Value: 6200000,
      entityId: 'acme-eu',
      entityName: 'Acme Europe GmbH',
      sourceRef: { fileId: 'ef2', fileName: 'Acme_EU_Q4_2024_Audit.pdf', page: 15 },
    },
    {
      Field_Name: 'Revenue',
      Source_Value: 45000000,
      Extracted_Value: 45000000,
      entityId: 'acme-asia',
      entityName: 'Acme Asia Pacific',
      sourceRef: { fileId: 'ef3', fileName: 'Acme_Asia_Q4_2024_Audit.pdf', page: 6 },
    },
    {
      Field_Name: 'COGS',
      Source_Value: 22000000,
      Extracted_Value: 22000000,
      entityId: 'acme-asia',
      entityName: 'Acme Asia Pacific',
      sourceRef: { fileId: 'ef3', fileName: 'Acme_Asia_Q4_2024_Audit.pdf', page: 8 },
    },
    {
      Field_Name: 'Net Income',
      Source_Value: 11500000,
      Extracted_Value: 11500000,
      entityId: 'acme-asia',
      entityName: 'Acme Asia Pacific',
      sourceRef: { fileId: 'ef3', fileName: 'Acme_Asia_Q4_2024_Audit.pdf', page: 11 },
    },
  ],
  meridian: [
    {
      Field_Name: 'Revenue',
      Source_Value: 89300000,
      Extracted_Value: 89300000,
      entityId: 'meridian',
      entityName: 'Meridian Health',
      sourceRef: { fileId: 'ef7', fileName: 'Meridian_Health_Q4_2024.pdf', page: 9 },
    },
    {
      Field_Name: 'COGS',
      Source_Value: 41200000,
      Extracted_Value: 41800000,
      entityId: 'meridian',
      entityName: 'Meridian Health',
      sourceRef: { fileId: 'ef7', fileName: 'Meridian_Health_Q4_2024.pdf', page: 11 },
    },
    {
      Field_Name: 'Gross Profit',
      Source_Value: 48100000,
      Extracted_Value: 47500000,
      entityId: 'meridian',
      entityName: 'Meridian Health',
      sourceRef: { fileId: 'ef7', fileName: 'Meridian_Health_Q4_2024.pdf', page: 13 },
    },
    {
      Field_Name: 'EBITDA',
      Source_Value: 22100000,
      Extracted_Value: 21850000,
      entityId: 'meridian',
      entityName: 'Meridian Health',
      sourceRef: { fileId: 'ef7', fileName: 'Meridian_Health_Q4_2024.pdf', page: 16 },
    },
    {
      Field_Name: 'Net Income',
      Source_Value: 14500000,
      Extracted_Value: 14500000,
      entityId: 'meridian',
      entityName: 'Meridian Health',
      sourceRef: { fileId: 'ef7', fileName: 'Meridian_Health_Q4_2024.pdf', page: 20 },
    },
    {
      Field_Name: 'Total Assets',
      Source_Value: 198000000,
      Extracted_Value: 198000000,
      entityId: 'meridian',
      entityName: 'Meridian Health',
      sourceRef: { fileId: 'ef7', fileName: 'Meridian_Health_Q4_2024.pdf', page: 24 },
    },
    {
      Field_Name: 'Total Liabilities',
      Source_Value: 112000000,
      Extracted_Value: 112700000,
      entityId: 'meridian',
      entityName: 'Meridian Health',
      sourceRef: { fileId: 'ef7', fileName: 'Meridian_Health_Q4_2024.pdf', page: 26 },
    },
    {
      Field_Name: 'Shareholders Equity',
      Source_Value: 86000000,
      Extracted_Value: 85300000,
      entityId: 'meridian',
      entityName: 'Meridian Health',
      sourceRef: { fileId: 'ef7', fileName: 'Meridian_Health_Q4_2024.pdf', page: 28 },
    },
    {
      Field_Name: 'Revenue',
      Source_Value: 25000000,
      Extracted_Value: 25300000,
      entityId: 'meridian-rx',
      entityName: 'Meridian Pharma',
      sourceRef: { fileId: 'ef8', fileName: 'Meridian_Pharma_Q4_2024.pdf', page: 7 },
    },
    {
      Field_Name: 'COGS',
      Source_Value: 12000000,
      Extracted_Value: 12000000,
      entityId: 'meridian-rx',
      entityName: 'Meridian Pharma',
      sourceRef: { fileId: 'ef8', fileName: 'Meridian_Pharma_Q4_2024.pdf', page: 9 },
    },
  ],
  nexus: [
    {
      Field_Name: 'Revenue',
      Source_Value: 67800000,
      Extracted_Value: 67800000,
      entityId: 'nexus',
      entityName: 'Nexus Technologies',
      sourceRef: { fileId: 'ef5', fileName: 'Nexus_Tech_Q4_2024_Report.pdf', page: 8 },
    },
    {
      Field_Name: 'COGS',
      Source_Value: 28900000,
      Extracted_Value: 28900000,
      entityId: 'nexus',
      entityName: 'Nexus Technologies',
      sourceRef: { fileId: 'ef5', fileName: 'Nexus_Tech_Q4_2024_Report.pdf', page: 10 },
    },
    {
      Field_Name: 'Gross Profit',
      Source_Value: 38900000,
      Extracted_Value: 38900000,
      entityId: 'nexus',
      entityName: 'Nexus Technologies',
      sourceRef: { fileId: 'ef5', fileName: 'Nexus_Tech_Q4_2024_Report.pdf', page: 12 },
    },
    {
      Field_Name: 'EBITDA',
      Source_Value: 15200000,
      Extracted_Value: 15200000,
      entityId: 'nexus',
      entityName: 'Nexus Technologies',
      sourceRef: { fileId: 'ef5', fileName: 'Nexus_Tech_Q4_2024_Report.pdf', page: 15 },
    },
    {
      Field_Name: 'Net Income',
      Source_Value: 9800000,
      Extracted_Value: 9800000,
      entityId: 'nexus',
      entityName: 'Nexus Technologies',
      sourceRef: { fileId: 'ef5', fileName: 'Nexus_Tech_Q4_2024_Report.pdf', page: 18 },
    },
    {
      Field_Name: 'Total Assets',
      Source_Value: 145000000,
      Extracted_Value: 145000000,
      entityId: 'nexus',
      entityName: 'Nexus Technologies',
      sourceRef: { fileId: 'ef5', fileName: 'Nexus_Tech_Q4_2024_Report.pdf', page: 22 },
    },
  ],
};

export const emailThreads: EmailThread[] = [
  {
    id: 'e1',
    companyId: 'acme',
    subject: 'COGS Variance — Acme Corp Q4 2024',
    timestamp: '2025-03-15T14:30:00Z',
    from: 'audit@vantagecap.com',
    to: 'j.chen@acmecorp.com',
    body:
      'James,\n\nDuring our Q4 2024 audit reconciliation for Acme Corp, we identified a variance of 0.88% in the Cost of Goods Sold line item. As per MIS: $85,200,000. As per AFS: $85,950,000.\n\nPlease provide documentation to clarify this discrepancy at your earliest convenience.\n\nRegards,\nAudit Team',
    status: 'sent',
  },
  {
    id: 'e2',
    companyId: 'meridian',
    subject: 'Total Liabilities Discrepancy — Meridian Health Q4 2024',
    timestamp: '2025-03-16T09:15:00Z',
    from: 'audit@vantagecap.com',
    to: 's.johnson@meridianhealth.com',
    body:
      'Sarah,\n\nWe have flagged a 0.63% variance in Total Liabilities for Meridian Health. As per MIS: $112,000,000. As per AFS: $112,700,000.\n\nPlease advise.\n\nRegards,\nAudit Team',
    status: 'sent',
  },
];

export const emailTemplates: EmailTemplate[] = [
  {
    id: 'tmpl-1',
    name: 'Discrepancy Notification',
    subject: '{{field_name}} Variance — {{company_name}} {{audit_period}}',
    body:
      '{{contact_name}},\n\nDuring our {{audit_period}} audit reconciliation for {{company_name}}, we identified a variance of {{variance_percent}} in the {{field_name}} line item.\n\nAs per MIS: {{source_value}}\nAs per AFS: {{extracted_value}}\n\nPlease provide documentation to clarify this discrepancy at your earliest convenience.\n\nRegards,\nAudit Team',
    category: 'discrepancy',
  },
  {
    id: 'tmpl-2',
    name: 'Follow-Up Request',
    subject: 'Follow-Up: {{subject}} — {{company_name}}',
    body:
      '{{contact_name}},\n\nThis is a follow-up regarding our previous communication about {{subject}}.\n\nWe have not yet received the requested documentation. Could you please provide an update on the status?\n\nRegards,\nAudit Team',
    category: 'follow-up',
  },
  {
    id: 'tmpl-3',
    name: 'Resolution Confirmation',
    subject: 'Resolved: {{field_name}} — {{company_name}} {{audit_period}}',
    body:
      '{{contact_name}},\n\nWe are writing to confirm that the {{field_name}} discrepancy for {{company_name}} has been resolved following review of the documentation provided.\n\nThank you for your cooperation.\n\nRegards,\nAudit Team',
    category: 'confirmation',
  },
  {
    id: 'tmpl-4',
    name: 'Audit Report Request',
    subject: 'Audit Report Request — {{company_name}} {{audit_period}}',
    body:
      '{{contact_name}},\n\nAs part of our {{audit_period}} review, we kindly request that you submit the audit report for {{company_name}} at your earliest convenience.\n\nPlease upload the report to the secure portal or reply to this email with the attached document.\n\nRegards,\nAudit Team',
    category: 'general',
  },
];

export const taggedFiles: TaggedFile[] = [
  { id: 'f1', fileName: 'Acme_Corp_Q4_2024_Audit.pdf', fileType: 'pdf', uploadedAt: '2025-03-10T08:00:00Z', size: '2.4 MB', taggedEntityId: 'acme', taggedEntityName: 'Acme Corp', status: 'processed' },
  { id: 'f2', fileName: 'Meridian_Health_Financials.pdf', fileType: 'pdf', uploadedAt: '2025-03-11T10:30:00Z', size: '1.8 MB', taggedEntityId: 'meridian', taggedEntityName: 'Meridian Health', status: 'processed' },
  { id: 'f3', fileName: 'Nexus_Tech_Q4_Report.pdf', fileType: 'pdf', uploadedAt: '2025-03-12T14:00:00Z', size: '3.1 MB', taggedEntityId: 'nexus', taggedEntityName: 'Nexus Technologies', status: 'processed' },
  { id: 'f4', fileName: 'Acme_Asia_Supplementary.pdf', fileType: 'pdf', uploadedAt: '2025-03-13T09:20:00Z', size: '890 KB', taggedEntityId: 'acme-asia', taggedEntityName: 'Acme Asia Pacific', status: 'processed' },
  { id: 'f5', fileName: 'Meridian_Pharma_Draft.pdf', fileType: 'pdf', uploadedAt: '2025-03-14T16:45:00Z', size: '1.2 MB', taggedEntityId: null, taggedEntityName: null, status: 'pending' },
  { id: 'f6', fileName: 'Unidentified_Report_March.pdf', fileType: 'pdf', uploadedAt: '2025-03-15T11:00:00Z', size: '4.5 MB', taggedEntityId: null, taggedEntityName: null, status: 'pending' },
];

export const auditLogs: AuditLogEntry[] = [
  { id: 'al1', companyId: 'acme', action: 'Report Attached', timestamp: '2025-03-10T08:05:00Z', user: 'System', details: 'Audit report uploaded and attached to Acme Corp' },
  { id: 'al2', companyId: 'acme', action: 'Discrepancy Flagged', timestamp: '2025-03-15T14:28:00Z', user: 'audit@vantagecap.com', details: 'COGS variance of 0.88% flagged for review' },
  { id: 'al3', companyId: 'acme', action: 'Email Sent', timestamp: '2025-03-15T14:30:00Z', user: 'audit@vantagecap.com', details: 'Discrepancy notification sent to j.chen@acmecorp.com' },
  { id: 'al4', companyId: 'meridian', action: 'Report Attached', timestamp: '2025-03-11T10:35:00Z', user: 'System', details: 'Audit report uploaded and attached to Meridian Health' },
  { id: 'al5', companyId: 'meridian', action: 'Discrepancy Flagged', timestamp: '2025-03-16T09:10:00Z', user: 'audit@vantagecap.com', details: 'Total Liabilities variance of 0.63% flagged for review' },
  { id: 'al6', companyId: 'nexus', action: 'Report Attached', timestamp: '2025-03-12T14:05:00Z', user: 'System', details: 'Audit report uploaded and attached to Nexus Technologies' },
  { id: 'al7', companyId: 'nexus', action: 'Status Changed', timestamp: '2025-03-13T11:00:00Z', user: 'audit@vantagecap.com', details: 'Status changed to Clarification Requested' },
  { id: 'al8', companyId: 'acme-asia', action: 'Status Changed', timestamp: '2025-03-14T09:00:00Z', user: 'audit@vantagecap.com', details: 'Status changed to Resolved' },
];

export const calculateVariance = (source: number, extracted: number, threshold = 0.005, absoluteThreshold?: number) => {
  if (source === 0) return { diff: extracted, percent: 0, isFlagged: false };
  const diff = extracted - source;
  const percent = diff / source;
  const percentFlagged = Math.abs(percent) > threshold;
  const absoluteFlagged = absoluteThreshold != null && absoluteThreshold > 0 ? Math.abs(diff) > absoluteThreshold : false;
  const isFlagged = percentFlagged || absoluteFlagged;
  return { diff, percent, isFlagged };
};

export type ReviewStage = 'Created' | 'Scoped In' | 'Scoped Out' | 'Overdue' | 'In Review' | 'Completed';

export interface ReviewCycle {
  id: string;
  label: string;
  createdAt: string;
}

export interface ReviewCompanyEntry {
  id: string;
  companyName: string;
  reviewCycleId: string;
  stage: ReviewStage;
  contactName: string;
  contactEmail: string;
  updatedAt: string;
}

export interface ReviewCycleLog {
  id: string;
  action: string;
  timestamp: string;
  user: string;
  details: string;
  reviewCycleId?: string;
}

export const reviewCycles: ReviewCycle[] = [
  { id: 'rc-1', label: 'CY 24 - FY 25', createdAt: '2025-01-01T00:00:00Z' },
  { id: 'rc-2', label: 'CY 23 - FY 24', createdAt: '2024-01-01T00:00:00Z' },
  { id: 'rc-3', label: 'CY 22 - FY 23', createdAt: '2023-01-01T00:00:00Z' },
];

export const reviewCompanyEntries: ReviewCompanyEntry[] = [
  { id: 'rce-1', companyName: 'Acme Corp', reviewCycleId: 'rc-1', stage: 'In Review', contactName: 'James Chen', contactEmail: 'j.chen@acmecorp.com', updatedAt: '2025-03-15T00:00:00Z' },
  { id: 'rce-2', companyName: 'Nexus Technologies', reviewCycleId: 'rc-1', stage: 'Scoped In', contactName: 'Maria Rodriguez', contactEmail: 'm.rodriguez@nexustech.io', updatedAt: '2025-03-10T00:00:00Z' },
  { id: 'rce-3', companyName: 'Meridian Health', reviewCycleId: 'rc-1', stage: 'Overdue', contactName: 'Sarah Johnson', contactEmail: 's.johnson@meridianhealth.com', updatedAt: '2025-02-20T00:00:00Z' },
  { id: 'rce-4', companyName: 'Apex Industries', reviewCycleId: 'rc-1', stage: 'Completed', contactName: 'Tom Harris', contactEmail: 't.harris@apex.com', updatedAt: '2025-03-01T00:00:00Z' },
  { id: 'rce-5', companyName: 'Pinnacle Logistics', reviewCycleId: 'rc-1', stage: 'In Review', contactName: 'David Kim', contactEmail: 'd.kim@pinnacle.com', updatedAt: '2025-03-12T00:00:00Z' },
  { id: 'rce-6', companyName: 'Summit Finance', reviewCycleId: 'rc-1', stage: 'Overdue', contactName: 'Lisa Wang', contactEmail: 'l.wang@summitfin.com', updatedAt: '2025-02-28T00:00:00Z' },
  { id: 'rce-7', companyName: 'Acme Corp', reviewCycleId: 'rc-2', stage: 'Completed', contactName: 'James Chen', contactEmail: 'j.chen@acmecorp.com', updatedAt: '2024-12-15T00:00:00Z' },
  { id: 'rce-8', companyName: 'Nexus Technologies', reviewCycleId: 'rc-2', stage: 'Completed', contactName: 'Maria Rodriguez', contactEmail: 'm.rodriguez@nexustech.io', updatedAt: '2024-12-10T00:00:00Z' },
  { id: 'rce-9', companyName: 'Meridian Health', reviewCycleId: 'rc-2', stage: 'Scoped Out', contactName: 'Sarah Johnson', contactEmail: 's.johnson@meridianhealth.com', updatedAt: '2024-11-20T00:00:00Z' },
  { id: 'rce-10', companyName: 'Apex Industries', reviewCycleId: 'rc-2', stage: 'Completed', contactName: 'Tom Harris', contactEmail: 't.harris@apex.com', updatedAt: '2024-11-15T00:00:00Z' },
  { id: 'rce-11', companyName: 'Pinnacle Logistics', reviewCycleId: 'rc-2', stage: 'In Review', contactName: 'David Kim', contactEmail: 'd.kim@pinnacle.com', updatedAt: '2024-12-01T00:00:00Z' },
  { id: 'rce-12', companyName: 'Acme Corp', reviewCycleId: 'rc-3', stage: 'Completed', contactName: 'James Chen', contactEmail: 'j.chen@acmecorp.com', updatedAt: '2023-12-15T00:00:00Z' },
  { id: 'rce-13', companyName: 'Nexus Technologies', reviewCycleId: 'rc-3', stage: 'Completed', contactName: 'Maria Rodriguez', contactEmail: 'm.rodriguez@nexustech.io', updatedAt: '2023-12-10T00:00:00Z' },
  { id: 'rce-14', companyName: 'Summit Finance', reviewCycleId: 'rc-3', stage: 'Scoped Out', contactName: 'Lisa Wang', contactEmail: 'l.wang@summitfin.com', updatedAt: '2023-11-20T00:00:00Z' },
];

export const reviewCycleLogs: ReviewCycleLog[] = [
  { id: 'rcl-1', action: 'Cycle Created', timestamp: '2025-01-01T00:00:00Z', user: 'admin@vantagecap.com', details: 'Review cycle CY 24 - FY 25 created', reviewCycleId: 'rc-1' },
  { id: 'rcl-2', action: 'CSV Uploaded', timestamp: '2025-01-05T10:00:00Z', user: 'admin@vantagecap.com', details: '6 companies uploaded for CY 24 - FY 25', reviewCycleId: 'rc-1' },
  { id: 'rcl-3', action: 'Stage Changed', timestamp: '2025-03-15T00:00:00Z', user: 'audit@vantagecap.com', details: 'Acme Corp stage changed to In Review', reviewCycleId: 'rc-1' },
  { id: 'rcl-4', action: 'Cycle Created', timestamp: '2024-01-01T00:00:00Z', user: 'admin@vantagecap.com', details: 'Review cycle CY 23 - FY 24 created', reviewCycleId: 'rc-2' },
  { id: 'rcl-5', action: 'CSV Uploaded', timestamp: '2024-01-05T10:00:00Z', user: 'admin@vantagecap.com', details: '5 companies uploaded for CY 23 - FY 24', reviewCycleId: 'rc-2' },
  { id: 'rcl-6', action: 'Cycle Created', timestamp: '2023-01-01T00:00:00Z', user: 'admin@vantagecap.com', details: 'Review cycle CY 22 - FY 23 created', reviewCycleId: 'rc-3' },
];

export const formatCurrency = (value: number): string => {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(value);
};

