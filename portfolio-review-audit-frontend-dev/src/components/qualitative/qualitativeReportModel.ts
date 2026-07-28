export function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

export function formatEnumLabel(v: string) {
  return v
    .split('_')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

export function auditorTierBadgeClass(tier: string | undefined) {
  switch (tier) {
    case 'big_4':
      return 'bg-indigo-600 text-white';
    case 'big_6':
      return 'bg-violet-600 text-white';
    case 'big_10':
      return 'bg-slate-600 text-white';
    case 'non_big_10':
      return 'bg-gray-500 text-white';
    default:
      return 'bg-gray-400 text-white';
  }
}

export function formatOpinionTypeLabel(opinionType: string | undefined) {
  if (!opinionType || opinionType === 'unknown') return 'Unknown';
  if (opinionType === 'unmodified') return 'Clean (unmodified)';
  if (opinionType === 'disclaimer_of_opinion') return 'Disclaimer of opinion';
  return formatEnumLabel(opinionType);
}

export type ReportParagraph = { present?: boolean; text?: string | null };

export type QualitativeRedFlag = {
  clause_ref?: string | null;
  theme?: string;
  matched_keywords?: string[];
  excerpt?: string | null;
};

export type AuditQualitativeMeta = {
  reporting_standards?: string | null;
  financials_status?: 'Signed' | 'Draft' | null;
  reporting_scope?: {
    statement_basis?: string;
    financials_basis?: string;
    entity_coverage?: string;
    entity_roles?: string[];
    entities_named?: string[];
    evidence_notes?: string | null;
  };
  caro?: {
    available?: boolean;
    title?: string | null;
    overall_assessment?: string;
    summary?: string | null;
    red_flags?: QualitativeRedFlag[];
  };
  ifc?: {
    available?: boolean;
    title?: string | null;
    overall_assessment?: string;
    summary?: string | null;
    red_flags?: QualitativeRedFlag[];
  };
  auditor_engagement?: {
    summary?: string;
    auditor_firm?: string | null;
    signing_partner_or_team?: string | null;
    auditor_tier?: string;
    auditor_tier_label?: string;
    signing_date?: string | null;
    signing_date_raw?: string | null;
    sections_present?: {
      independent_auditors_report?: boolean;
      caro?: boolean;
      ifc?: boolean;
    };
  };
};

export type AuditorOpinionMeta = {
  text?: string;
  basis_for_opinion?: string | null;
  entities_mentioned?: string[];
  opinion_type?: string;
  opinion_type_confidence?: string;
  classification_phrases?: string[];
  other_report_paragraphs?: {
    emphasis_of_matter?: ReportParagraph;
    other_matters?: ReportParagraph;
    going_concern_material_uncertainty?: ReportParagraph;
  };
};

export type EntityComplianceBadge = 'red' | 'amber' | 'unclassified' | 'green' | 'pending';

export type QualitativeReportViewModel = {
  isAuditFinancials: boolean;
  isExtractRunning: boolean;
  isExtractDone: boolean;
  isExtractError: boolean;
  hasAuditQualitativeContent: boolean;
  qualitativeReportIsPlaceholder: boolean;
  legacyQualitativeText: string;
  auditorOpinion: AuditorOpinionMeta | null;
  opinionText: string;
  basisForOpinion: string;
  classificationPhrases: string[];
  otherParagraphs: AuditorOpinionMeta['other_report_paragraphs'];
  auditQualitative: AuditQualitativeMeta | null;
  reportingScope: AuditQualitativeMeta['reporting_scope'];
  caroSection: AuditQualitativeMeta['caro'];
  ifcSection: AuditQualitativeMeta['ifc'];
  auditorEngagement: AuditQualitativeMeta['auditor_engagement'];
  engagementDisplayText: string;
  sectionLabels: string[];
  hasMattersHighlighted: boolean;
  hasCaroContent: boolean;
  hasIfcContent: boolean;
  reportingStandards: string | null;
  financialsStatus: 'Signed' | 'Draft' | null;
  /** Per-entity compliance overview (company view / consumers). */
  entityComplianceBadge: EntityComplianceBadge;
};

type QualitativeReportForBadge = Omit<QualitativeReportViewModel, 'entityComplianceBadge'>;

/** Per-entity compliance rollup: red (known-bad opinion), unclassified (opinion could not be determined), amber (CARO/IFC issues), green, or pending. */
export function deriveEntityComplianceBadge(vm: QualitativeReportForBadge): EntityComplianceBadge {
  if (!vm.isAuditFinancials) return 'pending';
  if (vm.isExtractRunning) return 'pending';
  if (vm.isExtractError) return 'red';
  if (!vm.isExtractDone) return 'pending';

  if (!vm.hasAuditQualitativeContent) return 'pending';

  const opinionType = vm.auditorOpinion?.opinion_type;

  // Opinion could not be determined from the document — cannot classify, do not raise as attention required.
  if (!vm.auditorOpinion || !opinionType || opinionType === 'unknown') return 'unclassified';

  // Known-bad opinion types (qualified, adverse, disclaimer) → attention required.
  if (opinionType !== 'unmodified') return 'red';

  const caro = vm.caroSection;
  const caroProblems =
    Boolean(caro?.available) &&
    caro.overall_assessment === 'has_highlights' &&
    (caro.red_flags?.length ?? 0) > 0;

  const ifc = vm.ifcSection;
  const ifcProblems =
    Boolean(ifc?.available) &&
    ifc.overall_assessment === 'has_weaknesses' &&
    (ifc.red_flags?.length ?? 0) > 0;

  if (caroProblems || ifcProblems) return 'amber';
  return 'green';
}

/** Short labels for entity-level rollup (shown next to entity name). */
export function entityComplianceBadgeLabel(level: EntityComplianceBadge): string {
  switch (level) {
    case 'pending':
      return 'Assessment pending';
    case 'red':
      return 'Attention required';
    case 'unclassified':
      return 'Unable to classify';
    case 'amber':
      return 'CARO / IFC highlights';
    case 'green':
      return 'Clean overview';
    default:
      return level;
  }
}

export function entityComplianceBadgeChipClass(level: EntityComplianceBadge): string {
  const base = 'text-[11px] font-bold uppercase tracking-wide px-3 py-1 rounded-full shrink-0 shadow-sm';
  switch (level) {
    case 'pending':
      return `${base} bg-slate-200 text-slate-700`;
    case 'red':
      return `${base} bg-red-600 text-white`;
    case 'unclassified':
      return `${base} bg-slate-500 text-white`;
    case 'amber':
      return `${base} bg-amber-400 text-amber-950`;
    case 'green':
      return `${base} bg-green-600 text-white`;
    default:
      return base;
  }
}

const DUMMY_QUALITATIVE_AUDIT_REPORT = `Executive summary — qualitative assessment

The audit engagement was conducted in accordance with agreed-upon procedures. Overall, management demonstrated a sound control environment over financial reporting processes reviewed during the period. No material exceptions were noted in the areas tested.

Scope and methodology

The work focused on qualitative aspects of the audit file provided, including consistency of narrative disclosures with underlying records, clarity of management representations, and alignment with the stated audit period. Sampling was judgmental and designed to highlight areas requiring follow-up rather than to provide statistical assurance.

Key observations

• Governance: Board oversight documentation was complete for the review cycle.
• Internal communication: Escalation paths for financial misstatements were documented and appear operational.
• Going concern: No contradictory evidence was identified in the materials supplied; management’s assessment is presented consistently with the supporting analysis.

Limitations

This qualitative narrative does not replace substantive testing of balances and is not a substitute for the full audit opinion. Any items marked for further review should be tracked to closure before sign-off.

Conclusion

Subject to the limitations above, the qualitative file content appears suitable for inclusion in the portfolio review workflow pending any open queries from the financial extraction step.`;

function isAuditFinancialsKind(kind: string) {
  const k = kind.trim().toLowerCase();
  return k === 'audit_financials' || k === 'financials' || k === 'audit-financials';
}

export function parseQualitativeReport(
  meta: unknown,
  options: {
    extractKind?: string;
    extractStatus?: string | null;
    /** When false, non-audit files never show sample placeholder text. */
    showPlaceholderSample?: boolean;
  } = {},
): QualitativeReportViewModel {
  const extractKind = (options.extractKind || '').trim().toLowerCase();
  const st = (options.extractStatus || '').trim().toLowerCase();
  const isAuditFinancials = isAuditFinancialsKind(extractKind);
  const isExtractRunning = st === 'running' || st === 'queued';
  const isExtractError = st === 'error';
  const isExtractDone = st === 'completed' || st === 'processed';

  const metaObj = isPlainObject(meta) ? meta : null;

  const auditorOpinionRaw = metaObj ? metaObj.auditor_opinion : null;
  const auditorOpinion =
    auditorOpinionRaw != null && typeof auditorOpinionRaw === 'object' && !Array.isArray(auditorOpinionRaw)
      ? (auditorOpinionRaw as AuditorOpinionMeta)
      : null;
  const opinionText = auditorOpinion && typeof auditorOpinion.text === 'string' ? auditorOpinion.text.trim() : '';
  const basisForOpinion =
    auditorOpinion && typeof auditorOpinion.basis_for_opinion === 'string'
      ? auditorOpinion.basis_for_opinion.trim()
      : '';
  const otherParagraphs = auditorOpinion?.other_report_paragraphs;
  const classificationPhrases = auditorOpinion?.classification_phrases ?? [];

  const auditQualitativeRaw =
    metaObj && isPlainObject(metaObj.audit_qualitative) ? metaObj.audit_qualitative : null;
  const auditQualitative = auditQualitativeRaw as AuditQualitativeMeta | null;
  const reportingScope = auditQualitative?.reporting_scope;
  const caroSection = auditQualitative?.caro;
  const ifcSection = auditQualitative?.ifc;
  const auditorEngagement = auditQualitative?.auditor_engagement;
  const reportingStandards = auditQualitative?.reporting_standards ?? null;
  const financialsStatus = auditQualitative?.financials_status ?? null;
  const engagementSummary =
    auditorEngagement && typeof auditorEngagement.summary === 'string'
      ? auditorEngagement.summary.trim()
      : '';
  const engagementSections = auditorEngagement?.sections_present;
  const sectionLabels: string[] = [];
  if (engagementSections?.independent_auditors_report) sectionLabels.push("Auditor's report");
  if (engagementSections?.caro) sectionLabels.push('CARO');
  if (engagementSections?.ifc) sectionLabels.push('IFC');

  const qualitativeReportFromMeta =
    metaObj && typeof metaObj.qualitative_audit_report === 'string'
      ? metaObj.qualitative_audit_report.trim()
      : '';
  const engagementDisplayText = engagementSummary || qualitativeReportFromMeta;
  const showSample = options.showPlaceholderSample !== false;
  const legacyQualitativeText =
    engagementDisplayText || (showSample && !isAuditFinancials ? DUMMY_QUALITATIVE_AUDIT_REPORT : '');

  const hasMattersHighlighted =
    Boolean(otherParagraphs?.emphasis_of_matter?.present && otherParagraphs.emphasis_of_matter.text) ||
    Boolean(otherParagraphs?.other_matters?.present && otherParagraphs.other_matters.text) ||
    Boolean(
      otherParagraphs?.going_concern_material_uncertainty?.present &&
        otherParagraphs.going_concern_material_uncertainty.text,
    );
  const hasCaroContent = Boolean(caroSection?.available);
  const hasIfcContent = Boolean(ifcSection?.available);
  const signingPartnerOrTeam =
    auditorEngagement && typeof auditorEngagement.signing_partner_or_team === 'string'
      ? auditorEngagement.signing_partner_or_team.trim()
      : '';

  const hasAuditQualitativeContent =
    Boolean(opinionText) ||
    Boolean(basisForOpinion) ||
    hasMattersHighlighted ||
    Boolean(engagementDisplayText) ||
    Boolean(signingPartnerOrTeam) ||
    Boolean(reportingScope?.entities_named?.length) ||
    (reportingScope?.statement_basis && reportingScope.statement_basis !== 'unknown') ||
    (reportingScope?.entity_coverage && reportingScope.entity_coverage !== 'unknown') ||
    hasCaroContent ||
    hasIfcContent ||
    Boolean(reportingStandards) ||
    Boolean(financialsStatus);

  const qualitativeReportIsPlaceholder =
    !isAuditFinancials && showSample
      ? !qualitativeReportFromMeta && !engagementSummary
      : isExtractDone && !isExtractError && !hasAuditQualitativeContent;

  const base: QualitativeReportForBadge = {
    isAuditFinancials,
    isExtractRunning,
    isExtractDone,
    isExtractError,
    hasAuditQualitativeContent,
    qualitativeReportIsPlaceholder,
    legacyQualitativeText,
    auditorOpinion,
    opinionText,
    basisForOpinion,
    classificationPhrases,
    otherParagraphs,
    auditQualitative,
    reportingScope,
    caroSection,
    ifcSection,
    auditorEngagement,
    engagementDisplayText,
    sectionLabels,
    hasMattersHighlighted,
    hasCaroContent,
    hasIfcContent,
    reportingStandards,
    financialsStatus,
  };
  return {
    ...base,
    entityComplianceBadge: deriveEntityComplianceBadge(base),
  };
}
