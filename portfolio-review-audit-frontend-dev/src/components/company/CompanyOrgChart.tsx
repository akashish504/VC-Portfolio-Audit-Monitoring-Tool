import { useCallback, useEffect, useState } from 'react';
import { Download, Loader2 } from 'lucide-react';

import { listOrgEntities, reparentOrgEntity, retriggerOrgChartExtraction } from '@/api/fileProcessing';
import { clearOrgChartAndEntities, createEntity, deleteEntity, getFileDownloadUrl, getPortfolioCompany, patchEntity } from '@/api/portfolio';
import { applyOrgChartRecord, downloadOrgChartXlsx, getOrgChartPendingUpdate, type PendingUpdateResult } from '@/api/orgChartReconciliation';
import { OrgChartUpload } from '@/components/company/OrgChartUpload';
import { OrgChartGraph } from '@/components/company/OrgChartGraph';
import { OrgChartReconciliationDialog } from '@/components/company/OrgChartReconciliationDialog';
import { EntityStatusConfirmDialog } from '@/components/company/EntityStatusConfirmDialog';
import { applyEntityStatusChange, type EntityStatusConfirmState } from '@/components/company/entityStatusChange';
import { normalizeEntityStatus } from '@/constants/auditStatus';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { toast } from '@/components/ui/sonner';
import { ENTITY_TYPE_OPTIONS, type EntityType } from '@/types/domain';
import type { EntityReviewStatusValue } from '@/constants/statusEnums';

type OrgChartEntityRow = {
  id: number;
  name: string;
  geolocation?: string | null;
  entity_type?: EntityType | null;
  parent_entity_id?: number | null;
  is_parent: boolean;
  status?: string | null;
};

function mapOrgEntityRow(e: {
  id: number;
  name: string;
  geolocation?: string | null;
  entity_type?: EntityType | null;
  parent_entity_id?: number | null;
  is_parent: boolean;
  status?: string | null;
}): OrgChartEntityRow {
  return {
    id: e.id,
    name: e.name,
    geolocation: e.geolocation ?? null,
    entity_type: e.entity_type ?? null,
    parent_entity_id: e.parent_entity_id ?? null,
    is_parent: e.is_parent,
    status: e.status ?? null,
  };
}

export function CompanyOrgChart({
  companyId,
  companyName,
  selectedEntityId: _selectedEntityId,
}: {
  companyId: number;
  companyName?: string;
  selectedEntityId?: string;
}) {
  // NOTE: This component is mid-refactor away from mock data.
  // For now we show the extracted org structure from backend Entities after upload.
  const [orgChartFile, setOrgChartFile] = useState<{ name: string; url: string; type: string; fileId?: number } | null>(null);
  const [uploadExpanded, setUploadExpanded] = useState(true);
  const [entitiesLoading, setEntitiesLoading] = useState(false);
  const [extractedEntities, setExtractedEntities] = useState<OrgChartEntityRow[]>([]);
  const [childId, setChildId] = useState<number | ''>('');
  const [parentId, setParentId] = useState<number | ''>('');
  const [editError, setEditError] = useState<string | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
  const [savingEntityId, setSavingEntityId] = useState<number | null>(null);
  const [statusConfirm, setStatusConfirm] = useState<EntityStatusConfirmState | null>(null);

  const [addName, setAddName] = useState('');
  const [addGeolocation, setAddGeolocation] = useState('');
  const [addEntityType, setAddEntityType] = useState('');
  const [addParentId, setAddParentId] = useState<number | ''>('');
  const [addSaving, setAddSaving] = useState(false);

  const [editDialog, setEditDialog] = useState<{
    id: number;
    name: string;
    geolocation: string;
    entity_type: string;
    parent_entity_id: number | null;
  } | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);
  const [deletingEntityId, setDeletingEntityId] = useState<number | null>(null);

  const [downloadingXlsx, setDownloadingXlsx] = useState(false);

  // Pending org-chart update from batch upload
  const [pendingUpdate, setPendingUpdate] = useState<PendingUpdateResult | null>(null);
  const [reconciliationOpen, setReconciliationOpen] = useState(false);

  const portfolioCompanyId = companyId;
  const companyNameForCopy = companyName?.trim() || 'This company';

  useEffect(() => {
    const load = async () => {
      try {
        const company = await getPortfolioCompany(portfolioCompanyId);
        if (company.org_chart_file_id) {
          // Only store the fileId/name on mount — the presigned URL is fetched
          // fresh when the user clicks View to avoid expired-URL errors.
          setOrgChartFile({ name: 'Org chart', url: '', type: '', fileId: company.org_chart_file_id });
          setUploadExpanded(false);
        }
      } catch {
        // non-fatal
      }
    };
    void load();
  }, [portfolioCompanyId]);

  const handleViewOrgChart = async () => {
    if (!orgChartFile?.fileId) {
      setUploadExpanded(true);
      return;
    }
    try {
      const meta = await getFileDownloadUrl(orgChartFile.fileId);
      setOrgChartFile({
        name: meta.file_name || orgChartFile.name,
        url: meta.download_url,
        type: meta.content_type || 'application/pdf',
        fileId: orgChartFile.fileId,
      });
    } catch {
      toast.error('Could not load org chart file. Try again.');
    }
    setUploadExpanded(true);
  };

  // Graph rendering handles hierarchy + layout; no need for manual tree maps.

  const refreshEntities = useCallback(async () => {
    setEntitiesLoading(true);
    try {
      const ents = await listOrgEntities(portfolioCompanyId);
      setExtractedEntities(ents.map(mapOrgEntityRow));
    } finally {
      setEntitiesLoading(false);
    }
  }, [portfolioCompanyId]);

  useEffect(() => {
    void refreshEntities();
  }, [refreshEntities]);

  // Check for pending org-chart batch update on mount
  useEffect(() => {
    const check = async () => {
      try {
        const result = await getOrgChartPendingUpdate(portfolioCompanyId);
        if (!result.has_pending_update) return;
        const pending = result as PendingUpdateResult;
        if (!pending.requires_reconciliation) {
          await applyOrgChartRecord(pending.record_id);
          toast.success('Org chart applied automatically');
          await refreshEntities();
          // Re-fetch company so the org chart file reference reflects the newly attached file
          const updated = await getPortfolioCompany(portfolioCompanyId);
          if (updated.org_chart_file_id) {
            setOrgChartFile({ name: pending.file_name ?? 'Org chart', url: '', type: '', fileId: updated.org_chart_file_id });
            setUploadExpanded(false);
          }
        } else {
          setPendingUpdate(pending);
          setReconciliationOpen(true);
        }
      } catch {
        // non-fatal: silently skip if endpoint not available
      }
    };
    void check();
  }, [portfolioCompanyId, refreshEntities]);

  const handleOrgChartUploaded = async (file: File, url: string, fileId: number) => {
    const next = { name: file.name, url, type: file.type, fileId };
    setOrgChartFile(next);
    setUploadExpanded(false);
    // Extraction runs via the batch-record path. Poll for the pending update
    // and let the reconciliation dialog handle applying it (same as ZIP uploads).
    let attempts = 0;
    while (attempts < 30) {
      attempts += 1;
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const result = await getOrgChartPendingUpdate(portfolioCompanyId);
        if (!result.has_pending_update) continue;
        const pending = result as PendingUpdateResult;
        if (!pending.requires_reconciliation) {
          await applyOrgChartRecord(pending.record_id);
          toast.success('Org chart applied automatically');
          await refreshEntities();
          const updated = await getPortfolioCompany(portfolioCompanyId);
          if (updated.org_chart_file_id) {
            setOrgChartFile({ name: file.name, url: '', type: '', fileId: updated.org_chart_file_id });
            setUploadExpanded(false);
          }
        } else {
          setPendingUpdate(pending);
          setReconciliationOpen(true);
        }
        break;
      } catch {
        // non-fatal: keep polling
      }
    }
  };

  const handleDownloadOrgChart = async () => {
    const fid = orgChartFile?.fileId;
    if (!fid) {
      toast.error('Org chart file is not available for download');
      return;
    }
    try {
      const { download_url } = await getFileDownloadUrl(fid);
      if (!download_url) {
        toast.error('Org chart file is not available for download');
        return;
      }
      const a = document.createElement('a');
      a.href = download_url;
      a.download = orgChartFile?.name ?? '';
      a.target = '_blank';
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch {
      toast.error('Could not download the org chart');
    }
  };

  const handleRetrigger = async () => {
    if (!orgChartFile) return;
    setEntitiesLoading(true);
    setExtractedEntities([]);
    try {
      const resp = await retriggerOrgChartExtraction(portfolioCompanyId, orgChartFile.fileId ?? null);
      if (resp.download_url) {
        setOrgChartFile({ ...orgChartFile, url: resp.download_url, fileId: resp.file_id });
      }
      // Poll until entities appear again.
      let attempts = 0;
      while (attempts < 30) {
        attempts += 1;
        const ents = await listOrgEntities(portfolioCompanyId);
        if (ents.length > 0) {
          setExtractedEntities(ents.map(mapOrgEntityRow));
          break;
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
    } finally {
      setEntitiesLoading(false);
    }
  };

  const sortedEntities = [...extractedEntities].sort((a, b) => a.name.localeCompare(b.name));
  const optionLabel = (e: { name: string; geolocation?: string | null }) =>
    e.geolocation ? `${e.name} (${e.geolocation})` : e.name;

  const isInvalidReparent = (child: number, parent: number | null) => {
    if (parent == null) return false;
    if (child === parent) return true;
    const parentMap = new Map<number, number | null>();
    for (const e of extractedEntities) parentMap.set(e.id, e.parent_entity_id ?? null);
    // cycle check: walking up from parent must not hit child
    let cur: number | null = parent;
    let steps = 0;
    while (cur != null) {
      steps += 1;
      if (steps > 2000) return true;
      if (cur === child) return true;
      cur = parentMap.get(cur) ?? null;
    }
    return false;
  };

  const applyReparent = async () => {
    setEditError(null);
    if (childId === '') return;
    const child = extractedEntities.find((e) => e.id === childId);
    if (!child) return;
    if (child.is_parent && parentId !== '') {
      setEditError('Root entity cannot be moved under another entity.');
      return;
    }
    const newParent = parentId === '' ? null : parentId;
    if (newParent != null && isInvalidReparent(childId, newParent)) {
      setEditError('Invalid change: would create a cycle.');
      return;
    }
    setSavingEdit(true);
    try {
      const updated = await reparentOrgEntity({
        portfolioCompanyId,
        childEntityId: childId,
        newParentEntityId: newParent,
      });
      setExtractedEntities(updated.map(mapOrgEntityRow));
      // keep selection
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to update entity structure';
      setEditError(msg);
    } finally {
      setSavingEdit(false);
    }
  };

  const handleAddEntity = async () => {
    const name = addName.trim();
    if (!name) {
      toast.error('Enter an entity name');
      return;
    }
    const parent = addParentId === '' ? null : Number(addParentId);
    setAddSaving(true);
    try {
      await createEntity({
        portfolio_company_id: portfolioCompanyId,
        name,
        geolocation: addGeolocation.trim() || null,
        entity_type: addEntityType || null,
        parent_entity_id: parent,
        is_parent: false,
      });
      setAddName('');
      setAddGeolocation('');
      setAddEntityType('');
      setAddParentId('');
      await refreshEntities();
      toast.success('Entity added');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to add entity';
      toast.error(msg);
    } finally {
      setAddSaving(false);
    }
  };

  const openEditDialog = (entityId: number) => {
    const ent = extractedEntities.find((e) => e.id === entityId);
    if (!ent) return;
    setEditDialog({
      id: ent.id,
      name: ent.name,
      geolocation: ent.geolocation ?? '',
      entity_type: ent.entity_type ?? '',
      parent_entity_id: ent.parent_entity_id ?? null,
    });
  };

  const saveEditDialog = async () => {
    if (!editDialog) return;
    const { id, name, geolocation, entity_type, parent_entity_id } = editDialog;
    const trimmed = name.trim();
    if (!trimmed) {
      toast.error('Name is required');
      return;
    }
    const child = extractedEntities.find((e) => e.id === id);
    if (!child) return;
    if (child.is_parent && parent_entity_id !== null) {
      toast.error('The org chart root cannot be placed under another entity.');
      return;
    }
    if (parent_entity_id !== null && isInvalidReparent(id, parent_entity_id)) {
      toast.error('Invalid parent: would create a cycle.');
      return;
    }
    setEditSaving(true);
    try {
      await patchEntity(id, {
        name: trimmed,
        geolocation: geolocation.trim() || null,
        entity_type: entity_type || null,
        parent_entity_id,
        ...(parent_entity_id !== null && child.is_parent ? { is_parent: false } : {}),
      });
      setEditDialog(null);
      await refreshEntities();
      toast.success('Entity updated');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to update entity';
      toast.error(msg);
    } finally {
      setEditSaving(false);
    }
  };

  const requestDeleteEntity = (entityId: number) => {
    const children = extractedEntities.filter((e) => e.parent_entity_id === entityId);
    if (children.length > 0) {
      toast.error(
        'This entity has child entities attached. Move those child entities under another parent (or make them top-level) first, then you can delete this entity.',
      );
      return;
    }
    setDeleteConfirmId(entityId);
  };

  const confirmDeleteEntity = async () => {
    if (deleteConfirmId == null) return;
    const id = deleteConfirmId;
    if (extractedEntities.some((e) => e.parent_entity_id === id)) {
      toast.error(
        'Child entities are still attached. Move them first, then try deleting again.',
      );
      setDeleteConfirmId(null);
      return;
    }
    setDeleteConfirmId(null);
    setDeletingEntityId(id);
    try {
      await deleteEntity(id);
      await refreshEntities();
      toast.success('Entity removed');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to remove entity';
      toast.error(msg);
    } finally {
      setDeletingEntityId(null);
    }
  };

  const handleEntityStatusChangeRequest = useCallback(
    async (entityId: number, newStatus: EntityReviewStatusValue) => {
      const ent = extractedEntities.find((e) => e.id === entityId);
      const entityLabel = ent
        ? ent.geolocation
          ? `${ent.name} (${ent.geolocation})`
          : ent.name
        : `Entity ${entityId}`;
      const previousStatus = normalizeEntityStatus(ent?.status);
      if (newStatus === previousStatus) return;

      let companyInReviewStatus: string | null = null;
      let reviewStage: string | null = null;
      try {
        const pc = await getPortfolioCompany(companyId);
        companyInReviewStatus = pc.in_review_status ?? null;
        reviewStage = pc.review_stage ?? null;
      } catch {
        // Company fields optional for the dialog.
      }

      setStatusConfirm({
        entityId,
        portfolioCompanyId: companyId,
        newStatus,
        entityLabel,
        previousStatus,
        companyInReviewStatus,
        reviewStage,
      });
    },
    [extractedEntities, companyId],
  );

  const applyConfirmedEntityStatus = useCallback(
    async (options: Parameters<typeof applyEntityStatusChange>[1]) => {
      if (!statusConfirm) return;
      const snapshot = statusConfirm;
      setStatusConfirm(null);
      setSavingEntityId(snapshot.entityId);
      try {
        await applyEntityStatusChange(snapshot, options);
        setExtractedEntities((prev) =>
          prev.map((e) => (e.id === snapshot.entityId ? { ...e, status: snapshot.newStatus } : e)),
        );
        toast.success('Entity status updated.');
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Failed to update entity status';
        toast.error(msg);
      } finally {
        setSavingEntityId(null);
      }
    },
    [statusConfirm],
  );

  return (
    <div className="p-6 overflow-auto h-full space-y-6">
      {pendingUpdate && (
        <OrgChartReconciliationDialog
          open={reconciliationOpen}
          portfolioCompanyId={portfolioCompanyId}
          recordId={pendingUpdate.record_id}
          fileId={pendingUpdate.file_id}
          extractedEntities={pendingUpdate.extracted_org_chart}
          existingEntities={pendingUpdate.existing_entities}
          unmatchedExistingEntities={pendingUpdate.unmatched_existing_entities ?? pendingUpdate.existing_entities}
          unmatchedExtractedEntities={pendingUpdate.unmatched_extracted_entities ?? pendingUpdate.extracted_org_chart}
          autoMatchedEntities={pendingUpdate.auto_matched_entities ?? []}
          requiresReconciliation={pendingUpdate.requires_reconciliation}
          onClose={() => setReconciliationOpen(false)}
          onApplied={(appliedFileId?: number | null) => {
            setReconciliationOpen(false);
            if (appliedFileId) {
              // Store only the id/name; the presigned URL is fetched fresh
              // when the user clicks View (mirrors the on-mount behavior).
              setOrgChartFile({
                name: pendingUpdate?.file_name ?? 'Org chart',
                url: '',
                type: 'application/pdf',
                fileId: appliedFileId,
              });
              setUploadExpanded(false);
            }
            setPendingUpdate(null);
            void refreshEntities();
          }}
        />
      )}

      <EntityStatusConfirmDialog
        open={!!statusConfirm}
        state={statusConfirm}
        loading={savingEntityId !== null}
        onOpenChange={(open) => {
          if (!open) setStatusConfirm(null);
        }}
        onConfirm={applyConfirmedEntityStatus}
      />

      <Dialog
        open={!!editDialog}
        onOpenChange={(open) => {
          if (!open) setEditDialog(null);
        }}
      >
        <DialogContent className="bg-white sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-gray-900">Edit entity</DialogTitle>
            <DialogDescription className="text-gray-500 text-left">Update name, geolocation, entity type, and parent in the org chart.</DialogDescription>
          </DialogHeader>
          {editDialog && (
            <div className="space-y-3 py-2">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Name</label>
                <input
                  value={editDialog.name}
                  onChange={(e) => setEditDialog((d) => (d ? { ...d, name: e.target.value } : d))}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Geolocation</label>
                <input
                  value={editDialog.geolocation}
                  onChange={(e) => setEditDialog((d) => (d ? { ...d, geolocation: e.target.value } : d))}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                  placeholder="Optional"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Entity type</label>
                <select
                  value={ENTITY_TYPE_OPTIONS.includes(editDialog.entity_type as EntityType) || editDialog.entity_type === '' ? editDialog.entity_type : 'Other'}
                  onChange={(e) =>
                    setEditDialog((d) =>
                      d ? { ...d, entity_type: e.target.value } : d,
                    )
                  }
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
                >
                  <option value="">Not set</option>
                  {ENTITY_TYPE_OPTIONS.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
                {(editDialog.entity_type === 'Other' || (editDialog.entity_type !== '' && !ENTITY_TYPE_OPTIONS.includes(editDialog.entity_type as EntityType))) && (
                  <input
                    type="text"
                    value={editDialog.entity_type === 'Other' ? '' : editDialog.entity_type}
                    onChange={(e) =>
                      setEditDialog((d) => d ? { ...d, entity_type: e.target.value || 'Other' } : d)
                    }
                    className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                    placeholder="Describe entity type (optional)"
                  />
                )}
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Parent entity</label>
                <select
                  value={editDialog.parent_entity_id ?? ''}
                  onChange={(e) =>
                    setEditDialog((d) =>
                      d ? { ...d, parent_entity_id: e.target.value ? Number(e.target.value) : null } : d,
                    )
                  }
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
                >
                  <option value="">None (top level)</option>
                  {sortedEntities
                    .filter((e) => e.id !== editDialog.id && !isInvalidReparent(editDialog.id, e.id))
                    .map((e) => (
                      <option key={e.id} value={e.id}>
                        {optionLabel(e)}
                      </option>
                    ))}
                </select>
              </div>
            </div>
          )}
          <DialogFooter className="gap-2 sm:gap-0">
            <button
              type="button"
              onClick={() => setEditDialog(null)}
              className="px-4 py-2 text-sm rounded-lg border border-gray-300 bg-white text-gray-800 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void saveEditDialog()}
              disabled={editSaving}
              className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:pointer-events-none"
            >
              {editSaving ? 'Saving…' : 'Save'}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleteConfirmId !== null} onOpenChange={(open) => !open && setDeleteConfirmId(null)}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-gray-900">Remove entity?</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-500">
              This removes the entity from the org chart. Entities with child nodes cannot be deleted until those children are moved elsewhere.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-gray-300 text-gray-700 hover:bg-gray-50">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void confirmDeleteEntity()}
              className="bg-red-600 text-white hover:bg-red-700 focus:ring-red-600"
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {orgChartFile && !uploadExpanded ? (
        <div className="flex items-center gap-2">
          <button
            onClick={() => void handleViewOrgChart()}
            className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-900 border border-gray-200 rounded-lg px-3 py-2 transition-colors"
          >
            <span className="truncate max-w-[200px]">📄 {orgChartFile.name}</span>
            <span className="text-blue-600">View / Re-upload</span>
          </button>
          <button
            onClick={handleRetrigger}
            disabled={entitiesLoading}
            className="text-xs px-3 py-2 rounded-lg border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:pointer-events-none"
          >
            Retrigger extraction
          </button>
          <button
            onClick={() => void handleDownloadOrgChart()}
            disabled={!orgChartFile?.fileId}
            title={
              orgChartFile?.fileId
                ? 'Download the org chart file'
                : 'Org chart file is not available for download'
            }
            className="inline-flex items-center gap-1.5 text-xs px-3 py-2 rounded-lg border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-white"
          >
            <Download className="h-3.5 w-3.5" /> Download
          </button>
        </div>
      ) : (
        <OrgChartUpload
          companyId={companyId}
          onFileUploaded={handleOrgChartUploaded}
          uploadedFile={orgChartFile}
          onClear={async () => {
            try {
              const out = await clearOrgChartAndEntities(portfolioCompanyId);
              toast.success(
                `Org chart removed. Deleted ${out.deleted_entities} entit${out.deleted_entities === 1 ? 'y' : 'ies'}.`,
              );
            } catch {
              toast.error('Could not remove org chart. Try again.');
              return;
            }
            setOrgChartFile(null);
            setExtractedEntities([]);
            setUploadExpanded(true);
          }}
        />
      )}

      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs font-semibold text-gray-900 uppercase tracking-wider">Entity structure</h3>
          {extractedEntities.length > 0 && (
            <button
              type="button"
              onClick={async () => {
                setDownloadingXlsx(true);
                try {
                  await downloadOrgChartXlsx(portfolioCompanyId, companyName);
                } catch {
                  toast.error('Could not download org chart. Try again.');
                } finally {
                  setDownloadingXlsx(false);
                }
              }}
              disabled={downloadingXlsx}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:pointer-events-none"
            >
              {downloadingXlsx ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <span>↓</span>
              )}
              Download XLSX
            </button>
          )}
        </div>
        <p className="text-xs text-gray-500 mb-4">Add entities manually or use an uploaded org chart. Set an optional parent to place the entity in the hierarchy.</p>

        <div className="bg-white border border-gray-200 rounded-lg p-4 mb-4">
          <h4 className="text-xs font-semibold text-gray-900 uppercase tracking-wider mb-3">Add entity</h4>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Name</label>
              <input
                value={addName}
                onChange={(e) => setAddName(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                placeholder="Entity name"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Geolocation</label>
              <input
                value={addGeolocation}
                onChange={(e) => setAddGeolocation(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                placeholder="Optional"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Entity type</label>
              <select
                value={ENTITY_TYPE_OPTIONS.includes(addEntityType as EntityType) || addEntityType === '' ? addEntityType : 'Other'}
                onChange={(e) => setAddEntityType(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
              >
                <option value="">Not set</option>
                {ENTITY_TYPE_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
              {(addEntityType === 'Other' || (addEntityType !== '' && !ENTITY_TYPE_OPTIONS.includes(addEntityType as EntityType))) && (
                <input
                  type="text"
                  value={addEntityType === 'Other' ? '' : addEntityType}
                  onChange={(e) => setAddEntityType(e.target.value || 'Other')}
                  className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                  placeholder="Describe entity type (optional)"
                />
              )}
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Parent</label>
              <select
                value={addParentId}
                onChange={(e) => setAddParentId(e.target.value ? Number(e.target.value) : '')}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
              >
                <option value="">None (top level)</option>
                {sortedEntities.map((e) => (
                  <option key={e.id} value={e.id}>
                    {optionLabel(e)}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="mt-3">
            <button
              type="button"
              onClick={() => void handleAddEntity()}
              disabled={addSaving}
              className="px-4 py-2 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:pointer-events-none"
            >
              {addSaving ? 'Adding…' : 'Add entity'}
            </button>
          </div>
        </div>

        {extractedEntities.length > 0 && (
          <details className="bg-white border border-gray-200 rounded-lg mb-4 overflow-hidden">
            <summary className="cursor-pointer select-none px-4 py-3 text-xs font-semibold text-gray-900 uppercase tracking-wider hover:bg-gray-50">
              Reparent (move under another entity)
            </summary>
            <div className="p-4 pt-3 border-t border-gray-200">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Child entity</label>
                  <select
                    value={childId}
                    onChange={(e) => setChildId(e.target.value ? Number(e.target.value) : '')}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-xs bg-white"
                  >
                    <option value="">Select entity…</option>
                    {sortedEntities.map((e) => (
                      <option key={e.id} value={e.id}>
                        {optionLabel(e)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-500 mb-1">New parent</label>
                  <select
                    value={parentId}
                    onChange={(e) => setParentId(e.target.value ? Number(e.target.value) : '')}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-xs bg-white"
                  >
                    <option value="">(no parent / root)</option>
                    {sortedEntities.map((e) => (
                      <option key={e.id} value={e.id} disabled={childId !== '' && e.id === childId}>
                        {optionLabel(e)}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void applyReparent()}
                    disabled={savingEdit || childId === ''}
                    className="px-3 py-2 text-xs rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:pointer-events-none"
                  >
                    {savingEdit ? 'Saving…' : 'Apply'}
                  </button>
                  {editError && <div className="text-xs text-red-600">{editError}</div>}
                </div>
              </div>
              <div className="mt-2 text-[11px] text-gray-500">
                Note: the extracted root can’t be attached under another entity, and cycles are blocked.
              </div>
            </div>
          </details>
        )}

        <div className="min-w-full">
          {entitiesLoading ? (
            <div className="flex flex-col items-center justify-center py-12 gap-3">
              <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
              <div className="text-center">
                <p className="text-sm text-gray-900">Loading entities…</p>
                {orgChartFile && (
                  <p className="text-xs text-gray-500 mt-1">If you just uploaded a chart, extraction may take a few seconds.</p>
                )}
              </div>
            </div>
          ) : extractedEntities.length > 0 ? (
            <OrgChartGraph
              entities={extractedEntities}
              onEntityStatusChange={handleEntityStatusChangeRequest}
              savingEntityId={savingEntityId}
              onEditEntity={openEditDialog}
              onDeleteEntity={requestDeleteEntity}
              deletingEntityId={deletingEntityId}
            />
          ) : (
            <div className="text-sm text-gray-500 rounded-lg border border-dashed border-gray-200 bg-gray-50/50 px-4 py-8 text-center">
              No entities yet. Add one above or upload an org chart file.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

