import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowRight, CheckCircle2, ChevronDown, ChevronRight, Download, Loader2, PlusCircle, Trash2, X } from 'lucide-react';

import { listFiles } from '@/api/portfolio';
import { ENTITY_TYPE_OPTIONS } from '@/types/domain';
import {
  applyOrgChartRecord,
  dismissOrgChartRecord,
  downloadReconciliationComparisonXlsx,
  reconcileOrgChartRecord,
  type AutoMatchedEntity,
  type EntityMappingItem,
  type ExistingEntitySummary,
  type ExtractedEntity,
  type FileMoveItem,
  type ParentLinkItem,
} from '@/api/orgChartReconciliation';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import type { FileData } from '@/types/domain';

// ── types ─────────────────────────────────────────────────────────────────────

type ActionType = 'match' | 'create' | 'keep' | 'archive' | 'unset';

interface MappingRow {
  key: string;
  action: ActionType;
  existingId: number | null;
  extractedLlmId: number | null;
  finalName: string;
  finalGeolocation: string;
  finalEntityType: string;
  finalIsParent: boolean;
}

interface ParentRow {
  childRef: string;
  parentRef: string;
}

interface FileMoveRow {
  fileId: number;
  fileName: string;
  originalEntityId: number | null;
  targetRef: string;
}

// ── helpers ───────────────────────────────────────────────────────────────────

function entityRef(type: 'existing' | 'extracted', id: number): string {
  return `${type}:${id}`;
}

function refLabel(
  ref: string,
  existing: ExistingEntitySummary[],
  extracted: ExtractedEntity[],
): string {
  if (!ref) return '(none)';
  const [type, idStr] = ref.split(':');
  const id = Number(idStr);
  if (type === 'existing') {
    const e = existing.find((x) => x.id === id);
    return e ? (e.geolocation ? `${e.name} (${e.geolocation})` : e.name) : `Entity ${id}`;
  }
  if (type === 'extracted') {
    const e = extracted.find((x) => x.llm_id === id);
    return e ? (e.geolocation ? `${e.name} (${e.geolocation})` : e.name) : `Extracted ${id}`;
  }
  return ref;
}

const ACTION_COLORS: Record<ActionType, string> = {
  match: 'bg-blue-100 text-blue-800',
  create: 'bg-green-100 text-green-800',
  keep: 'bg-gray-100 text-gray-700',
  archive: 'bg-amber-100 text-amber-800',
  unset: 'bg-gray-50 text-gray-400',
};

const ACTION_LABELS: Record<ActionType, string> = {
  match: 'Match',
  create: 'Create new',
  keep: 'Keep unchanged',
  archive: 'Archive',
  unset: 'Not mapped',
};

// ── main component ────────────────────────────────────────────────────────────

export function OrgChartReconciliationDialog({
  open,
  portfolioCompanyId,
  recordId,
  extractedEntities,
  existingEntities,
  unmatchedExistingEntities,
  unmatchedExtractedEntities,
  autoMatchedEntities,
  requiresReconciliation,
  fileId,
  onClose,
  onApplied,
}: {
  open: boolean;
  portfolioCompanyId: number;
  recordId: number;
  extractedEntities: ExtractedEntity[];
  existingEntities: ExistingEntitySummary[];
  unmatchedExistingEntities: ExistingEntitySummary[];
  unmatchedExtractedEntities: ExtractedEntity[];
  autoMatchedEntities: AutoMatchedEntity[];
  requiresReconciliation: boolean;
  fileId?: number | null;
  onClose: () => void;
  onApplied: (fileId?: number | null) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [dismissing, setDismissing] = useState(false);
  const [downloadingComparison, setDownloadingComparison] = useState(false);
  const [companyFiles, setCompanyFiles] = useState<FileData[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [autoMatchCollapsed, setAutoMatchCollapsed] = useState(true);

  // Mapping rows: one row per UNMATCHED existing entity + one row per unmatched extracted entity
  const [mappings, setMappings] = useState<MappingRow[]>([]);
  const [parentLinks, setParentLinks] = useState<ParentRow[]>([]);
  const [fileMoves, setFileMoves] = useState<FileMoveRow[]>([]);

  // ── initialise state when dialog opens ────────────────────────────────────

  useEffect(() => {
    if (!open) return;

    // One row per UNMATCHED existing entity (auto-matched ones are excluded)
    const rows: MappingRow[] = unmatchedExistingEntities.map((e) => ({
      key: `existing:${e.id}`,
      action: 'keep',
      existingId: e.id,
      extractedLlmId: null,
      finalName: e.name,
      finalGeolocation: e.geolocation ?? '',
      finalEntityType: e.entity_type ?? '',
      finalIsParent: e.is_parent,
    }));

    // Add one 'create' row per UNMATCHED extracted entity only.
    for (const ex of unmatchedExtractedEntities) {
      rows.push({
        key: `extracted:${ex.llm_id}`,
        action: 'create',
        existingId: null,
        extractedLlmId: ex.llm_id,
        finalName: ex.name,
        finalGeolocation: ex.geolocation ?? '',
        finalEntityType: ex.entity_type ?? '',
        finalIsParent: ex.is_parent,
      });
    }
    setMappings(rows);

    // Default parent links from full extracted graph — skip dangling refs.
    const validLlmIds = new Set(extractedEntities.map((e) => e.llm_id));
    const links: ParentRow[] = [];
    for (const ex of extractedEntities) {
      for (const childId of ex.children_ids) {
        if (!validLlmIds.has(childId)) continue;
        // Use existing ref for auto-matched entities so refs resolve correctly.
        const autoMatchForChild = autoMatchedEntities.find((am) => am.extracted_temp_id === childId);
        const autoMatchForParent = autoMatchedEntities.find((am) => am.extracted_temp_id === ex.llm_id);
        const childRef = autoMatchForChild
          ? entityRef('existing', autoMatchForChild.existing_entity_id)
          : entityRef('extracted', childId);
        const parentRef = autoMatchForParent
          ? entityRef('existing', autoMatchForParent.existing_entity_id)
          : entityRef('extracted', ex.llm_id);
        links.push({ childRef, parentRef });
      }
    }
    setParentLinks(links);
    setAutoMatchCollapsed(true);
  }, [open, unmatchedExistingEntities, unmatchedExtractedEntities, extractedEntities, autoMatchedEntities]);

  // ── load company files for file-move table ─────────────────────────────────
  useEffect(() => {
    if (!open) return;
    const load = async () => {
      setFilesLoading(true);
      try {
        const res = await listFiles({ portfolio_company_id: portfolioCompanyId, limit: 200 });
        setCompanyFiles(res.items);
        // Initialise file moves from files that have entity_id
        setFileMoves(
          res.items
            .filter((f) => f.entity_id != null)
            .map((f) => ({
              fileId: f.id,
              fileName: f.filename ?? String(f.id),
              originalEntityId: f.entity_id ?? null,
              targetRef: f.entity_id ? entityRef('existing', f.entity_id) : '',
            })),
        );
      } finally {
        setFilesLoading(false);
      }
    };
    void load();
  }, [open, portfolioCompanyId]);

  // ── mapping helpers ────────────────────────────────────────────────────────

  const updateMapping = useCallback((key: string, patch: Partial<MappingRow>) => {
    setMappings((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
    // If the action is being set to unset/archive, remove any parent links that
    // reference this row's ref so stale links don't linger in the UI or payload.
    if (patch.action === 'unset' || patch.action === 'archive') {
      setParentLinks((prev) =>
        prev.filter((l) => l.childRef !== key && l.parentRef !== key),
      );
    }
  }, []);

  // Build ref→display-label for parent selectors.
  // Excluded: archived, unset, and extracted-only rows whose llm_id is already
  // covered by a matched existing-entity row (avoids duplicates in the selector).
  const matchedExtractedIdsForRefs = useMemo(
    () =>
      new Set(
        mappings
          .filter((m) => m.action === 'match' && m.existingId !== null && m.extractedLlmId !== null)
          .map((m) => m.extractedLlmId!),
      ),
    [mappings],
  );

  const allRefs = useMemo(() => {
    const out: { ref: string; label: string }[] = [];
    // Include auto-matched entities first (they resolve via existing: refs)
    for (const am of autoMatchedEntities) {
      const ext = am.extracted_entity;
      const name = ext.name || '(unnamed)';
      const geo = ext.geolocation ? ` (${ext.geolocation})` : '';
      out.push({ ref: entityRef('existing', am.existing_entity_id), label: `${name}${geo}` });
    }
    for (const m of mappings) {
      if (m.action === 'archive' || m.action === 'unset') continue;
      // Skip extracted-only create rows that are already represented by a match row
      if (m.existingId === null && m.extractedLlmId !== null && matchedExtractedIdsForRefs.has(m.extractedLlmId)) continue;
      const name = m.finalName || '(unnamed)';
      const geo = m.finalGeolocation ? ` (${m.finalGeolocation})` : '';
      if (m.existingId !== null) {
        out.push({ ref: entityRef('existing', m.existingId), label: `${name}${geo}` });
      } else if (m.extractedLlmId !== null) {
        out.push({ ref: entityRef('extracted', m.extractedLlmId), label: `${name}${geo} [new]` });
      }
    }
    return out;
  }, [mappings, autoMatchedEntities]);

  // ── save ───────────────────────────────────────────────────────────────────

  const handleSave = async () => {
    setSaving(true);
    try {
      if (!requiresReconciliation) {
        await applyOrgChartRecord(recordId);
      } else {
        // Collect which extracted llm_ids are already covered by a 'match' on an existing row.
        // The corresponding extracted-only 'create' row must be skipped to avoid duplicates.
        const matchedExtractedIds = new Set(
          mappings
            .filter((m) => m.action === 'match' && m.existingId !== null && m.extractedLlmId !== null)
            .map((m) => m.extractedLlmId!),
        );

        const entityMappings: EntityMappingItem[] = [];
        for (const m of mappings) {
          if (m.action === 'unset') continue;
          // Skip the auto-generated 'create' row for an extracted entity that was
          // matched to an existing entity — the match row already covers it.
          if (m.existingId === null && m.extractedLlmId !== null && matchedExtractedIds.has(m.extractedLlmId)) continue;
          entityMappings.push({
            existing_entity_id: m.existingId ?? undefined,
            extracted_temp_id: m.extractedLlmId ?? undefined,
            action: m.action,
            final_name: m.finalName || null,
            final_geolocation: m.finalGeolocation || null,
            final_entity_type: m.finalEntityType || null,
            final_is_parent: m.finalIsParent,
          });
        }

        const parentLinksPayload: ParentLinkItem[] = parentLinks
          .filter((l) => l.childRef)
          .map((l) => ({ child_ref: l.childRef, parent_ref: l.parentRef || null }));

        const fileMovesPayload: FileMoveItem[] = fileMoves.map((f) => ({
          file_id: f.fileId,
          target_entity_ref: f.targetRef || null,
          acknowledge_detached: !f.targetRef,
        }));

        const result = await reconcileOrgChartRecord(recordId, {
          portfolio_company_id: portfolioCompanyId,
          entity_mappings: entityMappings,
          parent_links: parentLinksPayload,
          file_moves: fileMovesPayload,
        });
        onApplied(result.new_org_chart_file_id ?? fileId);
        toast.success('Org chart updated successfully');
        return;
      }
      toast.success('Org chart updated successfully');
      onApplied(fileId);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to apply org chart';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleDismiss = async () => {
    setDismissing(true);
    try {
      await dismissOrgChartRecord(recordId);
      toast.success('Org chart update dismissed');
      onClose();
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to dismiss';
      toast.error(msg);
    } finally {
      setDismissing(false);
    }
  };

  // ── render ─────────────────────────────────────────────────────────────────

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="bg-white max-w-5xl w-full max-h-[92vh] overflow-y-auto p-0">
        <div className="sticky top-0 bg-white z-10 px-6 pt-5 pb-4 border-b border-gray-200">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500 shrink-0" />
              <div>
                <DialogTitle className="text-gray-900 text-base font-semibold">
                  {requiresReconciliation ? 'Reconcile Org Chart Update' : 'Apply Extracted Org Chart'}
                </DialogTitle>
                <DialogDescription className="text-xs text-gray-500 mt-0.5">
                  {requiresReconciliation
                    ? 'A new org chart was extracted from the uploaded file. Map existing entities to the newly extracted ones before applying.'
                    : 'Review the extracted entities below and click Apply to create the org chart.'}
                </DialogDescription>
              </div>
            </div>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-700 shrink-0">
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="px-6 py-4 space-y-6">

          {/* ── Direct Apply (no existing chart) ── */}
          {!requiresReconciliation && (
            <section>
              <h3 className="text-xs font-semibold text-gray-900 uppercase tracking-wider mb-3">
                Extracted entities ({extractedEntities.length})
              </h3>
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-3 py-2 text-left text-gray-500 font-medium">Name</th>
                      <th className="px-3 py-2 text-left text-gray-500 font-medium">Geolocation</th>
                      <th className="px-3 py-2 text-left text-gray-500 font-medium">Type</th>
                      <th className="px-3 py-2 text-left text-gray-500 font-medium">Root</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {extractedEntities.map((e) => (
                      <tr key={e.llm_id}>
                        <td className="px-3 py-2 text-gray-900 font-medium">{e.name}</td>
                        <td className="px-3 py-2 text-gray-600">{e.geolocation ?? '—'}</td>
                        <td className="px-3 py-2 text-gray-600">{e.entity_type ?? '—'}</td>
                        <td className="px-3 py-2">
                          {e.is_parent && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-blue-50 text-blue-700 text-[10px]">
                              <CheckCircle2 className="h-2.5 w-2.5" /> Root
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {/* ── Reconciliation mapping table ── */}
          {requiresReconciliation && (
            <>
              {/* ── Auto-matched entities (collapsed by default) ── */}
              {autoMatchedEntities.length > 0 && (
                <section>
                  <button
                    type="button"
                    onClick={() => setAutoMatchCollapsed((c) => !c)}
                    className="flex items-center gap-1.5 text-xs font-semibold text-gray-600 uppercase tracking-wider mb-2 hover:text-gray-900"
                  >
                    {autoMatchCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                    Auto-matched entities ({autoMatchedEntities.length})
                  </button>
                  {!autoMatchCollapsed && (
                    <div className="border border-gray-200 rounded-lg overflow-hidden">
                      <table className="w-full text-xs">
                        <thead className="bg-gray-50 border-b border-gray-200">
                          <tr>
                            <th className="px-3 py-2 text-left text-gray-500 font-medium w-[28%]">Existing</th>
                            <th className="px-3 py-2 text-left text-gray-500 font-medium w-[28%]">Extracted (will apply)</th>
                            <th className="px-3 py-2 text-left text-gray-500 font-medium w-[16%]">Match reason</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                          {autoMatchedEntities.map((am) => (
                            <tr key={`${am.existing_entity_id}-${am.extracted_temp_id}`} className="bg-green-50">
                              <td className="px-3 py-2">
                                <span className="text-gray-900 font-medium">{am.existing_entity.name}</span>
                                {am.existing_entity.geolocation && (
                                  <span className="text-gray-400 ml-1">({am.existing_entity.geolocation})</span>
                                )}
                              </td>
                              <td className="px-3 py-2">
                                <span className="text-gray-900 font-medium">{am.extracted_entity.name}</span>
                                {am.extracted_entity.geolocation && (
                                  <span className="text-gray-400 ml-1">({am.extracted_entity.geolocation})</span>
                                )}
                              </td>
                              <td className="px-3 py-2">
                                <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium ${am.match_reason === 'name' ? 'bg-blue-50 text-blue-700' : 'bg-purple-50 text-purple-700'}`}>
                                  <CheckCircle2 className="h-2.5 w-2.5" />
                                  {am.match_reason === 'name' ? 'Name match' : 'Geolocation match'}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              )}

              <section>
                <h3 className="text-xs font-semibold text-gray-900 uppercase tracking-wider mb-1">
                  Entity mapping{mappings.length > 0 ? ` (${mappings.length} unresolved)` : ''}
                </h3>
                <p className="text-[11px] text-gray-500 mb-3">
                  For each unmatched entity choose an action. Match it to an extracted entity, keep it unchanged, create a new entity from an extracted entry, or archive it.
                </p>

                {mappings.length === 0 ? (
                  <p className="text-[11px] text-gray-400 italic">All entities were auto-matched — no manual mapping required.</p>
                ) : (
                <div className="border border-gray-200 rounded-lg overflow-hidden">
                  <table className="w-full text-xs">
                    <thead className="bg-gray-50 border-b border-gray-200">
                      <tr>
                        <th className="px-3 py-2 text-left text-gray-500 font-medium w-[22%]">Existing entity</th>
                        <th className="px-3 py-2 text-left text-gray-500 font-medium w-[14%]">Action</th>
                        <th className="px-3 py-2 text-left text-gray-500 font-medium w-[22%]">Extracted entity</th>
                        <th className="px-3 py-2 text-left text-gray-500 font-medium w-[18%]">Final name</th>
                        <th className="px-3 py-2 text-left text-gray-500 font-medium w-[14%]">Geolocation</th>
                        <th className="px-3 py-2 text-left text-gray-500 font-medium w-[10%]">Type</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {mappings.map((m) => (
                        <MappingTableRow
                          key={m.key}
                          row={m}
                          extractedEntities={unmatchedExtractedEntities}
                          usedExtractedIds={mappings
                            .filter((r) => r.key !== m.key && r.extractedLlmId !== null && r.action === 'match')
                            .map((r) => r.extractedLlmId!)}
                          isMatched={
                            m.existingId === null &&
                            m.extractedLlmId !== null &&
                            mappings.some((r) => r.action === 'match' && r.extractedLlmId === m.extractedLlmId)
                          }
                          onUpdate={(patch) => updateMapping(m.key, patch)}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
                )}
              </section>

              {/* ── Parent links ── */}
              <section>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-xs font-semibold text-gray-900 uppercase tracking-wider">
                    Parent relationships
                  </h3>
                  <button
                    type="button"
                    onClick={() => setParentLinks((prev) => [...prev, { childRef: '', parentRef: '' }])}
                    className="flex items-center gap-1 text-[11px] text-blue-600 hover:text-blue-800"
                  >
                    <PlusCircle className="h-3 w-3" /> Add
                  </button>
                </div>
                <p className="text-[11px] text-gray-500 mb-3">
                  Set the parent for each entity in the final org chart. Leave parent blank to make it a root.
                </p>
                <div className="space-y-2">
                  {parentLinks.map((link, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <select
                        value={link.childRef}
                        onChange={(e) => setParentLinks((prev) => prev.map((l, i) => i === idx ? { ...l, childRef: e.target.value } : l))}
                        className="flex-1 px-2 py-1.5 border border-gray-300 rounded text-xs bg-white"
                      >
                        <option value="">Select child…</option>
                        {allRefs.map((r) => (
                          <option key={r.ref} value={r.ref}>{r.label}</option>
                        ))}
                      </select>
                      <ArrowRight className="h-3 w-3 text-gray-400 shrink-0" />
                      <select
                        value={link.parentRef}
                        onChange={(e) => setParentLinks((prev) => prev.map((l, i) => i === idx ? { ...l, parentRef: e.target.value } : l))}
                        className="flex-1 px-2 py-1.5 border border-gray-300 rounded text-xs bg-white"
                      >
                        <option value="">(root / no parent)</option>
                        {allRefs
                          .filter((r) => r.ref !== link.childRef)
                          .map((r) => (
                            <option key={r.ref} value={r.ref}>{r.label}</option>
                          ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => setParentLinks((prev) => prev.filter((_, i) => i !== idx))}
                        className="text-gray-400 hover:text-red-600"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                  {parentLinks.length === 0 && (
                    <p className="text-[11px] text-gray-400 italic">No parent relationships configured (all entities will be roots).</p>
                  )}
                </div>
              </section>

              {/* ── File moves ── */}
              {!filesLoading && fileMoves.length > 0 && (
                <section>
                  <h3 className="text-xs font-semibold text-gray-900 uppercase tracking-wider mb-2">
                    File reassignment ({fileMoves.length} files with entity attachments)
                  </h3>
                  <p className="text-[11px] text-gray-500 mb-3">
                    Reassign attached files from old entities to entities in the new org chart. Leave blank to detach.
                  </p>
                  <div className="border border-gray-200 rounded-lg overflow-hidden">
                    <table className="w-full text-xs">
                      <thead className="bg-gray-50 border-b border-gray-200">
                        <tr>
                          <th className="px-3 py-2 text-left text-gray-500 font-medium w-[45%]">File</th>
                          <th className="px-3 py-2 text-left text-gray-500 font-medium w-[30%]">Current entity</th>
                          <th className="px-3 py-2 text-left text-gray-500 font-medium w-[25%]">Reassign to</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100">
                        {fileMoves.map((fm) => (
                          <tr key={fm.fileId}>
                            <td className="px-3 py-2 text-gray-800 truncate max-w-xs">{fm.fileName}</td>
                            <td className="px-3 py-2 text-gray-500">
                              {fm.originalEntityId
                                ? refLabel(entityRef('existing', fm.originalEntityId), existingEntities, extractedEntities)
                                : '—'}
                            </td>
                            <td className="px-3 py-2">
                              <select
                                value={fm.targetRef}
                                onChange={(e) =>
                                  setFileMoves((prev) =>
                                    prev.map((f) =>
                                      f.fileId === fm.fileId ? { ...f, targetRef: e.target.value } : f,
                                    ),
                                  )
                                }
                                className="w-full px-2 py-1 border border-gray-300 rounded text-xs bg-white"
                              >
                                <option value="">(detach)</option>
                                {allRefs.map((r) => (
                                  <option key={r.ref} value={r.ref}>{r.label}</option>
                                ))}
                              </select>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
            </>
          )}
        </div>

        {/* ── footer ── */}
        <div className="sticky bottom-0 bg-white border-t border-gray-200 px-6 py-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void handleDismiss()}
              disabled={dismissing || saving}
              className="px-3 py-2 text-xs rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
            >
              {dismissing ? 'Dismissing…' : 'Dismiss update'}
            </button>
            <button
              type="button"
              onClick={async () => {
                setDownloadingComparison(true);
                try {
                  await downloadReconciliationComparisonXlsx(recordId);
                } catch {
                  toast.error('Could not download comparison. Try again.');
                } finally {
                  setDownloadingComparison(false);
                }
              }}
              disabled={downloadingComparison || saving || dismissing}
              className="flex items-center gap-1.5 px-3 py-2 text-xs rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
            >
              {downloadingComparison ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Download className="h-3 w-3" />
              )}
              Download comparison
            </button>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={saving || dismissing}
              className="px-4 py-2 text-xs rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving || dismissing}
              className="flex items-center gap-2 px-4 py-2 text-xs rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:pointer-events-none"
            >
              {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {saving ? 'Applying…' : requiresReconciliation ? 'Save & apply' : 'Apply'}
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── MappingTableRow ────────────────────────────────────────────────────────────

function MappingTableRow({
  row,
  extractedEntities,
  usedExtractedIds,
  isMatched,
  onUpdate,
}: {
  row: MappingRow;
  extractedEntities: ExtractedEntity[];
  usedExtractedIds: number[];
  isMatched: boolean;
  onUpdate: (patch: Partial<MappingRow>) => void;
}) {
  const isExisting = row.existingId !== null;
  const isExtractedOnly = row.existingId === null;
  const archived = row.action === 'archive';

  const availableExtracted = extractedEntities.filter(
    (e) => !usedExtractedIds.includes(e.llm_id) || e.llm_id === row.extractedLlmId,
  );

  const actionOptions: ActionType[] = isExisting
    ? ['match', 'keep', 'archive']
    : ['create', 'unset'];

  return (
    <tr className={archived || isMatched ? 'opacity-50' : undefined}>
      <td className="px-3 py-2">
        {isExisting ? (
          <div>
            <span className="text-gray-900 font-medium">{row.finalName}</span>
            {row.finalGeolocation && <span className="text-gray-400 ml-1">({row.finalGeolocation})</span>}
          </div>
        ) : (
          <span className="text-gray-400 italic text-[11px]">—</span>
        )}
      </td>
      <td className="px-3 py-2">
        {isMatched ? (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-blue-100 text-blue-700">
            <CheckCircle2 className="h-2.5 w-2.5" /> Matched
          </span>
        ) : (
        <select
          value={row.action}
          onChange={(e) => {
            const next = e.target.value as ActionType;
            onUpdate({ action: next });
          }}
          className={`w-full px-2 py-1 rounded border border-gray-200 text-[11px] font-medium ${ACTION_COLORS[row.action]}`}
        >
          {actionOptions.map((a) => (
            <option key={a} value={a}>{ACTION_LABELS[a]}</option>
          ))}
        </select>
        )}
      </td>
      <td className="px-3 py-2">
        {row.action === 'match' ? (
          <select
            value={row.extractedLlmId ?? ''}
            onChange={(e) => {
              const lid = e.target.value ? Number(e.target.value) : null;
              const ex = extractedEntities.find((x) => x.llm_id === lid);
              onUpdate({
                extractedLlmId: lid,
                finalName: ex?.name ?? row.finalName,
                finalGeolocation: ex?.geolocation ?? row.finalGeolocation,
                finalEntityType: ex?.entity_type ?? row.finalEntityType,
                finalIsParent: ex?.is_parent ?? row.finalIsParent,
              });
            }}
            className="w-full px-2 py-1 border border-gray-300 rounded text-[11px] bg-white"
          >
            <option value="">Select extracted…</option>
            {availableExtracted.map((ex) => (
              <option key={ex.llm_id} value={ex.llm_id}>
                {ex.geolocation ? `${ex.name} (${ex.geolocation})` : ex.name}
              </option>
            ))}
          </select>
        ) : isExtractedOnly ? (
          <span className="text-gray-700 text-[11px]">
            {row.finalName}{row.finalGeolocation ? ` (${row.finalGeolocation})` : ''}
          </span>
        ) : (
          <span className="text-gray-400 text-[11px]">—</span>
        )}
      </td>
      <td className="px-3 py-2">
        {!archived && !isMatched && row.action !== 'unset' ? (
          <input
            value={row.finalName}
            onChange={(e) => onUpdate({ finalName: e.target.value })}
            className="w-full px-2 py-1 border border-gray-300 rounded text-[11px]"
            placeholder="Name"
          />
        ) : null}
      </td>
      <td className="px-3 py-2">
        {!archived && !isMatched && row.action !== 'unset' ? (
          <input
            value={row.finalGeolocation}
            onChange={(e) => onUpdate({ finalGeolocation: e.target.value })}
            className="w-full px-2 py-1 border border-gray-300 rounded text-[11px]"
            placeholder="Optional"
          />
        ) : null}
      </td>
      <td className="px-3 py-2">
        {!archived && !isMatched && row.action !== 'unset' ? (
          <select
            value={row.finalEntityType}
            onChange={(e) => onUpdate({ finalEntityType: e.target.value })}
            className="w-full px-2 py-1 border border-gray-300 rounded text-[11px] bg-white"
          >
            <option value="">—</option>
            {ENTITY_TYPE_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        ) : null}
      </td>
    </tr>
  );
}
