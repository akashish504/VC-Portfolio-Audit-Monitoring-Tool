import { format, isValid, parseISO } from 'date-fns';
import { ScrollText } from 'lucide-react';

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import {
  auditorTierBadgeClass,
  entityComplianceBadgeChipClass,
  entityComplianceBadgeLabel,
  formatEnumLabel,
  formatOpinionTypeLabel,
  type QualitativeRedFlag,
  type QualitativeReportViewModel,
  type ReportParagraph,
} from '@/components/qualitative/qualitativeReportModel';

function formatSigningDate(signingIso?: string | null, signingRaw?: string | null): string | null {
  if (signingIso && /^\d{4}-\d{2}-\d{2}/.test(signingIso)) {
    try {
      const d = parseISO(signingIso.slice(0, 10));
      if (isValid(d)) return format(d, 'dd MMM yyyy');
    } catch {
      /* fall through */
    }
  }
  if (signingRaw?.trim()) return signingRaw.trim();
  return null;
}

/** Styling for the opinion headline chip. */
function opinionPresentation(auditorOpinion: QualitativeReportViewModel['auditorOpinion']) {
  const t = auditorOpinion?.opinion_type;
  const clean = t === 'unmodified';
  const unclassified = !auditorOpinion || !t || t === 'unknown';
  const red = !unclassified && !clean;
  return { clean, red, unclassified, label: t ? formatOpinionTypeLabel(t) : 'Unable to classify' };
}

function QualitativeParagraphBlockRed({ title, block }: { title: string; block?: ReportParagraph }) {
  if (!block?.present || !block.text?.trim()) return null;
  return (
    <div className="space-y-1 rounded-md border border-red-100 bg-red-50/60 p-3">
      <p className="text-xs font-semibold text-red-900">{title}</p>
      <p className="text-sm text-red-950 leading-relaxed whitespace-pre-wrap">{block.text.trim()}</p>
    </div>
  );
}

function RedFlagsListAlerts({ flags }: { flags: QualitativeRedFlag[] }) {
  if (!flags.length) return null;
  return (
    <ul className="space-y-3">
      {flags.map((f, i) => (
        <li key={i} className="text-xs border border-red-100 bg-red-50/70 rounded-md p-3 space-y-1.5">
          <div className="flex flex-wrap gap-1.5 items-baseline">
            {f.clause_ref ? <span className="font-semibold text-red-900">Clause {f.clause_ref}</span> : null}
            {f.theme ? <span className="text-red-950 font-medium">{formatEnumLabel(String(f.theme))}</span> : null}
          </div>
          {f.matched_keywords && f.matched_keywords.length > 0 ? (
            <p className="text-red-950/90">
              <span className="font-medium">Signals: </span>
              {f.matched_keywords.join(', ')}
            </p>
          ) : null}
          {f.excerpt ? (
            <p className="text-red-950 leading-relaxed whitespace-pre-wrap border-t border-red-100/80 pt-2 mt-2">
              {f.excerpt}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function SectionChip({ variant, children }: { variant: 'green' | 'red' | 'slate'; children: React.ReactNode }) {
  const cls =
    variant === 'green'
      ? 'bg-green-600 text-white'
      : variant === 'red'
        ? 'bg-red-600 text-white'
        : 'bg-slate-500 text-white';
  return (
    <span className={`text-[10px] font-bold uppercase tracking-wide px-2.5 py-0.5 rounded-full shadow-sm shrink-0 ${cls}`}>{children}</span>
  );
}

function caroChip(caro: QualitativeReportViewModel['caroSection']) {
  if (!caro?.available) return <SectionChip variant="slate">Not identified</SectionChip>;
  if (caro.overall_assessment === 'not_available')
    return <SectionChip variant="slate">Unclear</SectionChip>;
  if (caro.overall_assessment === 'has_highlights') return <SectionChip variant="red">Highlights</SectionChip>;
  return <SectionChip variant="green">Clean</SectionChip>;
}

function ifcChip(ifc: QualitativeReportViewModel['ifcSection']) {
  if (!ifc?.available) return <SectionChip variant="slate">Not identified</SectionChip>;
  if (ifc.overall_assessment === 'not_available')
    return <SectionChip variant="slate">Unclear</SectionChip>;
  if (ifc.overall_assessment === 'has_weaknesses') return <SectionChip variant="red">Weaknesses noted</SectionChip>;
  return <SectionChip variant="green">Effective</SectionChip>;
}

type QualitativeReportContentProps = QualitativeReportViewModel & {
  embedded?: boolean;
  showTechnicalHint?: boolean;
};

export function QualitativeReportContent({
  embedded = false,
  showTechnicalHint = true,
  isAuditFinancials,
  isExtractRunning,
  isExtractDone,
  isExtractError,
  hasAuditQualitativeContent,
  legacyQualitativeText,
  auditorOpinion,
  opinionText,
  basisForOpinion,
  classificationPhrases,
  otherParagraphs,
  reportingScope,
  caroSection,
  ifcSection,
  auditorEngagement,
  engagementDisplayText,
  sectionLabels,
  hasMattersHighlighted,
  reportingStandards,
  financialsStatus,
  entityComplianceBadge,
}: QualitativeReportContentProps) {
  const opinion = opinionPresentation(auditorOpinion);
  const signingDisplayed = formatSigningDate(auditorEngagement?.signing_date, auditorEngagement?.signing_date_raw);

  const defaultOpen: string[] = [];

  const caroFlags = caroSection?.red_flags ?? [];
  const showCaroFlagDetails =
    Boolean(caroSection?.available) &&
    caroSection?.overall_assessment === 'has_highlights' &&
    caroFlags.length > 0;

  const ifcFlags = ifcSection?.red_flags ?? [];
  const showIfcFlagDetails =
    Boolean(ifcSection?.available) &&
    ifcSection?.overall_assessment === 'has_weaknesses' &&
    ifcFlags.length > 0;

  const auditInner = (
    <>
      {isExtractRunning && <p className="text-sm text-gray-500">Extraction in progress…</p>}
      {!isExtractRunning && !isExtractDone && !isExtractError && (
        <p className="text-sm text-gray-500">
          Extraction has not completed yet. Open the file and run audit extraction if needed.
        </p>
      )}
      {isExtractError && (
        <p className="text-sm text-red-700">
          Extraction did not complete successfully. Re-run extraction from File Tagging.
        </p>
      )}
      {isExtractDone && !isExtractError && hasAuditQualitativeContent && (
        <Accordion type="multiple" defaultValue={defaultOpen} className="w-full divide-y divide-gray-100">
          {/* 1 — Auditing firm & signing (non-collapsible) */}
          <div className="py-3 border-b border-gray-100">
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <span className="font-bold text-gray-900">Auditing firm &amp; signing</span>
              {auditorEngagement?.auditor_tier_label ? (
                <span className={`text-[10px] font-bold uppercase tracking-wide px-2.5 py-0.5 rounded-full shadow-sm ${auditorTierBadgeClass(auditorEngagement.auditor_tier)}`}>
                  {auditorEngagement.auditor_tier_label}
                </span>
              ) : null}
            </div>
            {!auditorEngagement?.auditor_firm &&
            !signingDisplayed &&
            !auditorEngagement?.auditor_tier_label &&
            !auditorEngagement?.signing_partner_or_team?.trim() &&
            !engagementDisplayText &&
            !financialsStatus &&
            !reportingStandards ? (
              <p className="text-xs text-gray-600">Engagement metadata was not extracted for this report.</p>
            ) : (
              <div className="space-y-1.5">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-medium text-gray-900 w-32 shrink-0">Audit firm</span>
                  <span className="text-sm text-gray-900">{auditorEngagement?.auditor_firm || 'Not extracted'}</span>
                </div>
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-medium text-gray-900 w-32 shrink-0">Signing partner</span>
                  <span className="text-sm text-gray-900">{auditorEngagement?.signing_partner_or_team?.trim() || 'Not extracted'}</span>
                </div>
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-medium text-gray-900 w-32 shrink-0">Date of signing</span>
                  <span className="text-sm text-gray-900">{signingDisplayed || 'Not extracted'}</span>
                </div>
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-medium text-gray-900 w-32 shrink-0">Status</span>
                  {financialsStatus ? (
                    <span
                      className={`text-[10px] font-bold uppercase tracking-wide px-2.5 py-0.5 rounded-full shadow-sm ${
                        financialsStatus === 'Signed' ? 'bg-green-600 text-white' : 'bg-amber-400 text-amber-950'
                      }`}
                    >
                      {financialsStatus}
                    </span>
                  ) : (
                    <span className="text-sm text-gray-900">Not extracted</span>
                  )}
                </div>
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-medium text-gray-900 w-32 shrink-0">Reporting standard</span>
                  {reportingStandards ? (
                    <span className="text-[10px] font-bold uppercase tracking-wide px-2.5 py-0.5 rounded-full shadow-sm bg-slate-600 text-white">
                      {reportingStandards}
                    </span>
                  ) : (
                    <span className="text-sm text-gray-900">Not extracted</span>
                  )}
                </div>
              </div>
            )}
            {sectionLabels.length > 0 ? (
              <p className="text-sm text-gray-900 mt-2">
                <span className="font-medium text-gray-900">Detected sections: </span>
                {sectionLabels.join(', ')}
              </p>
            ) : null}
            {/* Summary — always shown */}
            {engagementDisplayText.trim() ? (
              <div className="mt-3">
                <p className="text-sm font-semibold text-gray-900">Summary</p>
                <p className="mt-1 text-sm text-gray-900 leading-relaxed whitespace-pre-wrap">{engagementDisplayText}</p>
              </div>
            ) : null}
          </div>

          {/* 2 — Auditor opinion */}
          <AccordionItem value="auditor-opinion" className="border-0">
            <AccordionTrigger className="py-3 hover:no-underline">
              <span className="flex flex-wrap items-center gap-2 pr-2">
                <span className="font-bold text-gray-900 text-left">Auditor opinion</span>
                <span
                  className={`text-[10px] font-bold uppercase tracking-wide px-2.5 py-0.5 rounded-full shadow-sm shrink-0 ${
                    opinion.unclassified
                      ? 'bg-slate-500 text-white'
                      : opinion.red
                        ? 'bg-red-600 text-white'
                        : 'bg-green-600 text-white'
                  }`}
                >
                  {opinion.label}
                </span>
              </span>
            </AccordionTrigger>
            <AccordionContent className="space-y-3">
              {auditorOpinion?.opinion_type_confidence &&
              auditorOpinion.opinion_type_confidence !== 'high' &&
              !opinion.red ? (
                <p className="text-[10px] text-gray-500">
                  Confidence: {formatEnumLabel(auditorOpinion.opinion_type_confidence)}
                </p>
              ) : null}
              {classificationPhrases.length > 0 ? (
                <p className="text-xs text-gray-600">
                  <span className="font-medium text-gray-800">Supporting phrases: </span>
                  {classificationPhrases.join(' · ')}
                </p>
              ) : null}
              {auditorOpinion?.entities_mentioned && auditorOpinion.entities_mentioned.length > 0 ? (
                <p className="text-xs text-gray-600">
                  <span className="font-medium text-gray-800">Entities: </span>
                  {auditorOpinion.entities_mentioned.join('; ')}
                </p>
              ) : null}
              {opinionText ? (
                <div className="space-y-2">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-gray-500">Opinion paragraph</p>
                  <div className="text-sm text-gray-900 space-y-2">
                    {opinionText.split(/\n\n+/).map((para, i) => (
                      <p key={i} className="leading-relaxed whitespace-pre-wrap">
                        {para}
                      </p>
                    ))}
                  </div>
                </div>
              ) : null}
              {basisForOpinion ? (
                <div className="space-y-1 pt-2 border-t border-gray-100">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-gray-500">Basis for opinion</p>
                  <p className="text-sm text-gray-800 leading-relaxed whitespace-pre-wrap">{basisForOpinion}</p>
                </div>
              ) : null}
            </AccordionContent>
          </AccordionItem>

          {/* 3 — Other matters (presentation: red framing) */}
          <AccordionItem value="critical-matters" className="border-0">
            <AccordionTrigger className="py-3 hover:no-underline">
              <span className="flex flex-wrap items-center gap-2 pr-2">
                <span className="font-bold text-gray-900">Other critical disclosures in auditor&apos;s report</span>
                {hasMattersHighlighted && <SectionChip variant="red">Highlighted</SectionChip>}
              </span>
            </AccordionTrigger>
            <AccordionContent className="space-y-3">
              {!hasMattersHighlighted ? (
                <p className="text-xs text-gray-600">
                  Emphasis of matter, other matters, or going concern disclosures were not identified in this extraction.
                </p>
              ) : (
                <>
                  <QualitativeParagraphBlockRed title="Emphasis of matter" block={otherParagraphs?.emphasis_of_matter} />
                  <QualitativeParagraphBlockRed title="Other matters" block={otherParagraphs?.other_matters} />
                  <QualitativeParagraphBlockRed
                    title="Material uncertainty related to going concern"
                    block={otherParagraphs?.going_concern_material_uncertainty}
                  />
                </>
              )}
            </AccordionContent>
          </AccordionItem>

          {/* 4 — CARO */}
          <AccordionItem value="caro" className="border-0">
            <AccordionTrigger className="py-3 hover:no-underline">
              <span className="flex flex-wrap items-center gap-2 pr-2">
                <span className="font-bold text-gray-900 text-left">CARO</span>
                {caroChip(caroSection)}
              </span>
            </AccordionTrigger>
            <AccordionContent className="space-y-3">
              {!caroSection?.available ? (
                <p className="text-xs text-gray-600">No CARO / other legal &amp; regulatory report block was matched.</p>
              ) : (
                <>
                  {caroSection.title ? <p className="text-xs text-gray-600">{caroSection.title}</p> : null}
                  {caroSection.summary?.trim() ? (
                    <p className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">{caroSection.summary.trim()}</p>
                  ) : null}
                  {showCaroFlagDetails ? (
                    <RedFlagsListAlerts flags={caroFlags} />
                  ) : caroSection.overall_assessment === 'has_highlights' ? (
                    <p className="text-xs text-amber-800 bg-amber-50/80 border border-amber-100 rounded-md p-2">
                      Highlights indicated with no clause-level excerpts in this extraction run — re-run extraction if needed.
                    </p>
                  ) : (
                    <p className="text-xs text-gray-600">No clause-level highlights above standard clean wording.</p>
                  )}
                </>
              )}
            </AccordionContent>
          </AccordionItem>

          {/* 5 — IFC */}
          <AccordionItem value="ifc" className="border-0">
            <AccordionTrigger className="py-3 hover:no-underline">
              <span className="flex flex-wrap items-center gap-2 pr-2">
                <span className="font-bold text-gray-900 text-left">Internal financial controls (IFC)</span>
                {ifcChip(ifcSection)}
              </span>
            </AccordionTrigger>
            <AccordionContent className="space-y-3">
              {!ifcSection?.available ? (
                <p className="text-xs text-gray-600">No IFC / ICFR report section was matched.</p>
              ) : (
                <>
                  {ifcSection.title ? <p className="text-xs text-gray-600">{ifcSection.title}</p> : null}
                  {ifcSection.summary?.trim() ? (
                    <p className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">{ifcSection.summary.trim()}</p>
                  ) : null}
                  {showIfcFlagDetails ? (
                    <RedFlagsListAlerts flags={ifcFlags} />
                  ) : ifcSection.overall_assessment === 'has_weaknesses' ? (
                    <p className="text-xs text-amber-800 bg-amber-50/80 border border-amber-100 rounded-md p-2">
                      Weaknesses indicated with no excerpts in this extraction run — try re-running extraction or check the IFC section.
                    </p>
                  ) : (
                    <p className="text-xs text-gray-600">
                      Scan includes signals such as &quot;Material Weakness&quot;, &quot;Significant Deficiency&quot;, &quot;Did not operate
                      effectively&quot;, &quot;Inadequate&quot;, &quot;Override of controls&quot;, and &quot;Reasonable possibility&quot;.
                    </p>
                  )}
                </>
              )}
            </AccordionContent>
          </AccordionItem>

          {/* 6 — Consolidated / standalone */}
          <AccordionItem value="report-basis" className="border-0">
            <AccordionTrigger className="py-3 hover:no-underline">
              <span className="flex flex-wrap items-center gap-2 pr-2">
                <span className="font-bold text-gray-900">Standalone / consolidated</span>
                {reportingScope?.statement_basis && reportingScope.statement_basis !== 'unknown' ? (
                  <SectionChip variant="slate">{formatEnumLabel(reportingScope.statement_basis)}</SectionChip>
                ) : null}
              </span>
            </AccordionTrigger>
            <AccordionContent className="space-y-2 text-sm">
              <div className="flex flex-wrap gap-2">
                {reportingScope?.statement_basis && reportingScope.statement_basis !== 'unknown' ? (
                  <SectionChip variant="slate">{`Statements reported: ${formatEnumLabel(reportingScope.statement_basis)}`}</SectionChip>
                ) : (
                  <p className="text-xs text-gray-600">Statement basis could not be determined from extracted metadata.</p>
                )}
              </div>
              {reportingScope?.financials_basis && reportingScope.financials_basis !== 'unclear' ? (
                <p className="text-xs text-gray-700">
                  <span className="font-medium text-gray-900">Primary financials basis: </span>
                  {formatEnumLabel(reportingScope.financials_basis)}
                </p>
              ) : null}
              {reportingScope?.evidence_notes?.trim() ? (
                <p className="text-xs text-gray-500 italic">{reportingScope.evidence_notes.trim()}</p>
              ) : null}
            </AccordionContent>
          </AccordionItem>

          {/* 7 — Holding / subsidiary */}
          <AccordionItem value="entity-role" className="border-0">
            <AccordionTrigger className="py-3 hover:no-underline">
              <span className="flex flex-wrap items-center gap-2 pr-2">
                <span className="font-bold text-gray-900">Entity role</span>
                {(reportingScope?.entity_roles?.length ?? 0) > 0
                  ? reportingScope!.entity_roles!.map((role) => (
                      <SectionChip key={role} variant="slate">{formatEnumLabel(role)}</SectionChip>
                    ))
                  : null}
              </span>
            </AccordionTrigger>
            <AccordionContent className="space-y-3 text-sm">
              {reportingScope?.entity_coverage && reportingScope.entity_coverage !== 'unknown' ? (
                <p className="text-xs text-gray-700">
                  <span className="font-medium text-gray-900">Coverage pattern: </span>
                  {formatEnumLabel(reportingScope.entity_coverage)}
                </p>
              ) : null}
              {(reportingScope?.entity_roles?.length ?? 0) > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {reportingScope!.entity_roles!.map((role) => (
                    <span
                      key={role}
                      className="text-xs font-bold uppercase tracking-wide px-2.5 py-0.5 rounded-full shadow-sm bg-blue-600 text-white"
                    >
                      {formatEnumLabel(role)}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-gray-600">Explicit holding/parent/subsidiary wording was not detected.</p>
              )}
              {reportingScope?.entities_named && reportingScope.entities_named.length > 0 ? (
                <p className="text-xs text-gray-700">
                  <span className="font-medium text-gray-900">Names referenced: </span>
                  {reportingScope.entities_named.join('; ')}
                </p>
              ) : null}
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      )}
      {isExtractDone && !isExtractError && !hasAuditQualitativeContent && (
        <p className="text-sm text-gray-600">No qualitative audit metadata is available for this file.</p>
      )}
    </>
  );

  const inner = (
    <>
      {isAuditFinancials ? (
        auditInner
      ) : (
        <pre className="text-sm text-gray-800 whitespace-pre-wrap font-sans leading-relaxed">{legacyQualitativeText}</pre>
      )}
    </>
  );

  if (embedded) {
    return <div className="p-4 max-h-[75vh] overflow-y-auto">{inner}</div>;
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center gap-2">
        <ScrollText className="h-4 w-4 text-gray-400 shrink-0" />
        <h3 className="text-sm font-semibold text-gray-900">
          {isAuditFinancials ? 'Compliance report' : 'Qualitative audit report'}
        </h3>
        {isAuditFinancials && entityComplianceBadge && entityComplianceBadge !== 'pending' ? (
          <span
            className={entityComplianceBadgeChipClass(entityComplianceBadge)}
            title="Opinion drives red; amber when CARO/IFC clause flags exist with a clean opinion."
          >
            {entityComplianceBadgeLabel(entityComplianceBadge)}
          </span>
        ) : null}
      </div>
      {showTechnicalHint ? (
        <div className="px-4 py-3 bg-gray-50/80 border-b border-gray-100">
          <p className="text-xs text-gray-600 leading-relaxed">
            {isAuditFinancials
              ? 'Structured from the qualitative extraction pass: opinion, disclosures, CARO, IFC, scope, auditing firm.'
              : 'Narrative from qualitative audit report files when provided.'}
          </p>
        </div>
      ) : null}
      <div className="p-4 max-h-[75vh] overflow-y-auto">{inner}</div>
    </div>
  );
}
