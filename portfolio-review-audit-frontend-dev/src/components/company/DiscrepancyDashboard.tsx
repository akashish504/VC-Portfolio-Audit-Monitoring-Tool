/**
 * Discrepancy Dashboard — unified financial reconciliation + manual queries view.
 *
 * Layout:
 *  • One card per entity (entity name in header)
 *  • Each card has a horizontally scrollable table of 6 metric rows.
 *    – First col (Metric) and last col (Edit) are sticky.
 *  • Below the metric table: manual queries list for that entity.
 *  • Toolbar: "Add Manual Query" button.
 *
 * Rules enforced by the backend:
 *  • enable = true only if variance breaches thresholds (API validates).
 *  • No "generate discrepancies" button – rows are auto-upserted on extraction.
 */

import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Pencil, Plus, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

import { fetchFxRates } from '@/api/fx';
import {
  convertAfsCurrencyForEntityCycle,
  convertFinancialDataSnowflakeCurrency,
  createFinancialDataSnowflake,
  createManualReconciliationQuery,
  deleteManualReconciliationQuery,
  listFinancialDataSnowflake,
  listFinancialMetricReconciliation,
  listManualReconciliationQueries,
  patchFinancialDataSnowflake,
  patchFinancialMetricReconciliation,
  patchManualReconciliationQuery,
  type ApiFinancialDataSnowflakeRow,
  type ApiFinancialMetricReconciliationRow,
  type ApiManualReconciliationQueryRow,
} from '@/api/portfolio';
import {
  listEntities,
  getEntityAfsSource,
  setEntityPrimaryAfsFile,
  getFileStreamUrl,
  type AfsSourceRef,
  type AfsCandidateFile,
} from '@/api/portfolio';
import apiClient from '@/api/axios';
import PdfSourceViewer from '@/components/PdfSourceViewer';
import { getFinancialParameterLabel } from '@/constants/financialParameterLabels';
import { varianceCategoriesForDiscrepancyType } from '@/constants/discrepancyVarianceCategories';
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
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useAppState } from '@/context/AppContext';
import {
  getFinancialMetricMapping,
  getSnowflakePRFinancialMapping,
  type ApiFinancialMetricMapping,
  type ApiFinancialMetricTerm,
  type ApiSnowflakePRFinancialMapping,
} from '@/api/settings';
import type { Entity } from '@/types/domain';
import { AfsBreakdownTooltip, afsBreakdownHasContent } from './AfsBreakdownTooltip';
import { EditedIndicator } from '@/components/common/EditedIndicator';
import { MisFormulaTooltip, misFormulaHasContent } from './MisFormulaTooltip';
import { cn } from '@/lib/utils';

import {
  RECONCILIATION_STATUS_OPTIONS,
  type ReconciliationStatus,
} from '@/constants/auditStatus';

const statusBadge: Record<ReconciliationStatus, string> = {
  'Open':              'bg-gray-100 text-gray-600',
  'Sent':              'bg-green-100 text-green-800',
  'Not Sent (if No)':  'bg-red-100 text-red-700',
  'Partial':           'bg-yellow-100 text-yellow-800',
  'Closed':            'bg-blue-100 text-blue-800',
  'Sent - Flagged':    'bg-green-100 text-green-800 ring-1 ring-orange-400',
  'Partial - Flagged': 'bg-yellow-100 text-yellow-800 ring-1 ring-orange-400',
  'Closed - Flagged':  'bg-blue-100 text-blue-800 ring-1 ring-orange-400',
};

const STATUS_SET = new Set<string>(RECONCILIATION_STATUS_OPTIONS);

function normalizeStatus(s: string | null | undefined): ReconciliationStatus {
  const v = (s ?? '').trim();
  if (STATUS_SET.has(v)) return v as ReconciliationStatus;
  return 'Open';
}

/** Returns the divisor and short unit label for a currency. INR → Crore, everything else → Million. */
export function currencyScale(currency: string | null | undefined): { divisor: number; unit: string } {
  const c = (currency ?? '').trim().toUpperCase();
  if (c === 'INR') return { divisor: 1_00_00_000, unit: 'Cr' };
  // No currency on the row ⇒ the amount is already stored in display units
  // (millions), so it must NOT be divided again — otherwise it renders as ~0
  // (and inline edits would save base units, corrupting the value). This mirrors
  // formatFinancialAmount, which also leaves non-USD/INR values unscaled.
  if (!c) return { divisor: 1, unit: 'Mn' };
  return { divisor: 1_000_000, unit: 'Mn' };
}

/** MIS amounts from the Snowflake-PR pipeline are always stored in millions, so —
 *  unlike AFS amounts — they are shown as-is (never divided by the currency scale). */
export function fmtMisAmount(v: number | null | undefined): string {
  if (v == null) return '—';
  return v.toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

export function fmtAmount(v: number | null | undefined, currency: string | null | undefined): string {
  if (v == null) return '—';
  const { divisor } = currencyScale(currency);
  const scaled = v / divisor;
  return scaled.toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

/** The 6 canonical metrics, in display order. */
const MIS_METRIC_KEYS = ['revenue', 'ebitda', 'pbt', 'pat', 'cash', 'debt'] as const;

function lookupThreshold(map: Record<string, number>, metricKey: string): number | undefined {
  if (metricKey in map) return map[metricKey];
  const label = getFinancialParameterLabel(metricKey);
  if (label in map) return map[label];
  return undefined;
}

/** Mirrors backend ``is_metric_comparable`` for all six reconciliation metrics. */
function isMetricComparable(
  mis: number | null | undefined,
  afs: number | null | undefined,
): boolean {
  if (mis == null || afs == null) return false;
  if (mis === 0 || afs === 0) return false;
  return true;
}

function varianceBreached(
  mis: number | null | undefined,
  afs: number | null | undefined,
  metricKey: string,
  fieldThresholds: Record<string, number>,
  absoluteThresholds: Record<string, number>,
  usdToInrRate: number | null,
  misCurrency?: string | null,
  afsCurrency?: string | null,
): boolean {
  if (!isMetricComparable(mis, afs)) return false;
  const absDiff = Math.abs(afs - mis);
  if (absDiff <= 1e-9) return false;
  const pctRatio = mis === 0 ? 0 : absDiff / Math.abs(mis);
  const pctThresh = lookupThreshold(fieldThresholds, metricKey) ?? 0.005;
  const absThresh = lookupThreshold(absoluteThresholds, metricKey) ?? 0;
  let flagged = pctRatio > pctThresh;
  if (absThresh > 0) {
    const cur = (afsCurrency || misCurrency || '').trim().toUpperCase();
    let effectiveAbs = absThresh;
    if (cur === 'INR' && usdToInrRate != null && usdToInrRate > 0) {
      effectiveAbs = absThresh * usdToInrRate;
    }
    flagged = flagged || absDiff > effectiveAbs;
  }
  return flagged;
}

function fmtVariance(
  mis: number | null | undefined,
  afs: number | null | undefined,
  misCurrency: string | null | undefined,
  metricKey: string,
  fieldThresholds: Record<string, number>,
  absoluteThresholds: Record<string, number>,
  usdToInrRate: number | null,
  afsCurrency?: string | null,
): { absLabel: string; pctLabel: string; breach: boolean; diffCategory: string } {
  if (!isMetricComparable(mis, afs)) return { absLabel: '—', pctLabel: '—', breach: false, diffCategory: 'Not Comparable' };
  const diff = afs - mis;
  const signedPct = mis !== 0 ? (diff / Math.abs(mis)) * 100 : 0;
  const absPct = Math.abs(signedPct);
  const { divisor } = currencyScale(misCurrency);
  const scaledDiff = diff / divisor;
  const sign = scaledDiff >= 0 ? '+' : '';
  const breach = varianceBreached(
    mis,
    afs,
    metricKey,
    fieldThresholds,
    absoluteThresholds,
    usdToInrRate,
    misCurrency,
    afsCurrency,
  );
  const pctThresh = lookupThreshold(fieldThresholds, metricKey) ?? 0.005;
  const threshPct = +(pctThresh * 100).toFixed(2);
  const pctSign = signedPct >= 0 ? '+' : '';
  let diffCategory: string;
  if (mis === 0) {
    diffCategory = 'Not Comparable';
  } else if (absPct > threshPct) {
    diffCategory = `> +/- ${threshPct}%`;
  } else {
    diffCategory = `< +/- ${threshPct}%`;
  }
  return {
    absLabel: `${sign}${scaledDiff.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`,
    pctLabel: `${pctSign}${signedPct.toFixed(1)}%`,
    breach,
    diffCategory,
  };
}

function normalizeCurrencyCode(code: string | null | undefined): string | null {
  const c = code?.trim().toUpperCase();
  return c && /^[A-Z]{3}$/.test(c) ? c : null;
}


function CurrencyConvertSelect({
  currentCurrency,
  converting,
  onConvert,
}: {
  currentCurrency: string | null;
  converting?: boolean;
  onConvert: (target: 'USD' | 'INR') => void;
}) {
  const { unit } = currencyScale(currentCurrency);
  const targets: ('USD' | 'INR')[] = currentCurrency === 'USD' ? ['INR'] : currentCurrency === 'INR' ? ['USD'] : ['USD', 'INR'];
  return (
    <div className="flex items-center gap-1.5">
      {currentCurrency && (
        <span className="text-xs font-semibold text-blue-900 bg-blue-100 px-2 py-0.5 rounded">
          {currentCurrency} · {unit}
        </span>
      )}
      <select
        disabled={converting}
        defaultValue=""
        onChange={(e) => {
          const v = e.target.value as 'USD' | 'INR' | '';
          if (v === 'USD' || v === 'INR') onConvert(v);
          e.target.value = '';
        }}
        className="h-6 rounded border border-blue-200 bg-white px-1.5 text-[11px] text-blue-800 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <option value="">{converting ? 'Converting…' : 'Convert currency…'}</option>
        {targets.map((t) => <option key={t} value={t}>→ {t} ({currencyScale(t).unit})</option>)}
      </select>
    </div>
  );
}

// ---- Edit dialogs ---- //



interface AddManualForm {
  entity_id: string;
  type: string;
  discrepency_text: string;
  company_response: string;
  reviewer_remarks: string;
  flagged: boolean;
}

// ---- component ---- //

export function DiscrepancyDashboard({ companyId }: { companyId: number }) {
  const { fieldThresholds, absoluteThresholds } = useAppState();
  const [usdToInrRate, setUsdToInrRate] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [reconRows, setReconRows] = useState<ApiFinancialMetricReconciliationRow[]>([]);
  const [manualRows, setManualRows] = useState<ApiManualReconciliationQueryRow[]>([]);
  const [snowflakeRows, setSnowflakeRows] = useState<ApiFinancialDataSnowflakeRow[]>([]);
  const [entities, setEntities] = useState<Entity[]>([]);
  // Fallback terms for AFS breakdown tooltip when a row pre-dates the breakdown write.
  const [mappingConfig, setMappingConfig] = useState<ApiFinancialMetricMapping['metrics'] | null>(null);
  // Snowflake PR formula config for MIS cell tooltip.
  const [snowflakeFormulaConfig, setSnowflakeFormulaConfig] = useState<ApiSnowflakePRFinancialMapping['metrics'] | null>(null);
  // AFS source (primary file id + per-path source refs + candidate files) per entity — powers
  // click-to-source in the AFS tooltip and the "reconciliation source file" picker.
  const [afsSourceByEntity, setAfsSourceByEntity] = useState<
    Record<number, { fileId: number | null; sourceRefs: Record<string, AfsSourceRef>; files: AfsCandidateFile[] }>
  >({});
  // Entity whose reconciliation source file is currently being switched (disables its picker).
  const [primaryFileBusyEntity, setPrimaryFileBusyEntity] = useState<number | null>(null);
  // Pending source-file change awaiting confirmation (the picker stays on the current file until confirmed).
  const [pendingFileChange, setPendingFileChange] = useState<{ entityId: number; fileId: number; filename: string } | null>(null);
  // PDF source viewer state (reuses the same <PdfSourceViewer> as the file-tagging page).
  const [pdfViewer, setPdfViewer] = useState<{
    open: boolean;
    pdfUrl: string;
    fieldLabel: string;
    sourceRef: AfsSourceRef | null;
  }>({ open: false, pdfUrl: '', fieldLabel: '', sourceRef: null });

  const entityMap = useMemo<Record<number, string>>(() => {
    const m: Record<number, string> = {};
    for (const e of entities) m[e.id] = e.name;
    return m;
  }, [entities]);

  /** Entity id → stored `fy_end` (financial year end), shown on each entity card header. */
  const entityFyEndMap = useMemo<Record<number, string | null>>(() => {
    const m: Record<number, string | null> = {};
    for (const e of entities) m[e.id] = e.fy_end ?? null;
    return m;
  }, [entities]);

  const refresh = async () => {
    setLoading(true);
    try {
      const [recon, manual, snowflake, mapping, snowflakeFormula] = await Promise.all([
        listFinancialMetricReconciliation({ portfolio_company_id: companyId, limit: 2000, offset: 0 }),
        listManualReconciliationQueries({ portfolio_company_id: companyId, limit: 500, offset: 0 }),
        listFinancialDataSnowflake({ portfolio_company_id: companyId, limit: 100, offset: 0 }),
        // Re-pull the mapping config in case it changed (drives the AFS tooltip fallback).
        getFinancialMetricMapping().catch(() => null),
        // Snowflake PR formula config for the MIS tooltip.
        getSnowflakePRFinancialMapping().catch(() => null),
      ]);
      setReconRows(recon.items ?? []);
      setManualRows(manual.items ?? []);
      setSnowflakeRows(snowflake.items ?? []);
      // Resolve each entity's AFS source file before rendering — entities without an
      // audited-financials file are excluded from the dashboard tables.
      const afsEntityIds = [...new Set((recon.items ?? []).map((r) => r.entity_id))];
      const afsPairs = await Promise.all(
        afsEntityIds.map((eid) =>
          getEntityAfsSource(eid)
            .then((s) => [eid, { fileId: s.file_id, sourceRefs: s.source_refs ?? {}, files: s.files ?? [] }] as const)
            .catch(() => [eid, { fileId: null, sourceRefs: {}, files: [] }] as const),
        ),
      );
      setAfsSourceByEntity(Object.fromEntries(afsPairs));
      if (mapping) setMappingConfig(mapping.metrics);
      if (snowflakeFormula) setSnowflakeFormulaConfig(snowflakeFormula.metrics);
    } finally {
      setLoading(false);
    }
  };

  // Open the shared PDF source viewer for a dotted AFS path (mirrors FileDetailPage.openSourceViewer).
  const openAfsSource = async (entityId: number, path: string) => {
    const src = afsSourceByEntity[entityId];
    if (!src?.fileId) return;
    const sourceRef = src.sourceRefs[path] ?? null;
    setPdfViewer({ open: true, pdfUrl: '', fieldLabel: path, sourceRef });
    try {
      // Fetch through apiClient (adds auth) and hand pdfjs a blob URL — same pattern as file-tagging.
      const res = await apiClient.get(getFileStreamUrl(src.fileId), { responseType: 'blob' });
      const blobUrl = URL.createObjectURL(res.data);
      setPdfViewer((prev) => (prev.open ? { ...prev, pdfUrl: blobUrl } : prev));
    } catch {
      // PdfSourceViewer renders its own "failed to load" state.
    }
  };

  // Choose which AFS file drives this entity's reconciliation. The backend re-syncs, so the
  // values, breakdown, source links and query-email numbers all switch to the selected file.
  const handleSetPrimaryFile = async (entityId: number, fileId: number) => {
    setPrimaryFileBusyEntity(entityId);
    try {
      await setEntityPrimaryAfsFile(entityId, fileId);
      toast.success('Reconciliation source file updated');
      await refresh();
    } catch {
      toast.error('Could not update the reconciliation source file');
    } finally {
      setPrimaryFileBusyEntity(null);
    }
  };

  const closePdfViewer = () => {
    setPdfViewer((prev) => {
      if (prev.pdfUrl) URL.revokeObjectURL(prev.pdfUrl);
      return { open: false, pdfUrl: '', fieldLabel: '', sourceRef: null };
    });
  };

  useEffect(() => {
    void refresh();
    void listEntities({ portfolio_company_id: companyId, limit: 200, offset: 0 })
      .then((r) => setEntities(r.items ?? []))
      .catch(() => {});
    void fetchFxRates('USD')
      .then((rates) => {
        const inr = rates.to.find((q) => q.quotecurrency === 'INR');
        if (inr?.mid) setUsdToInrRate(inr.mid);
      })
      .catch(() => setUsdToInrRate(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId]);

  // Auto-refresh whenever the Financial Extraction Mapping is saved elsewhere.
  // The Settings tab broadcasts both a CustomEvent (same window) and a
  // localStorage write (cross-tab). We schedule a sequence of refetches (3s/8s/16s)
  // because the backend re-evaluation runs as a background task and the new
  // breakdown may not yet be in the DB when the save returns.
  useEffect(() => {
    const REFETCH_DELAYS_MS = [3000, 8000, 16000];
    const handleSaved = () => {
      const timers: number[] = [];
      for (const delay of REFETCH_DELAYS_MS) {
        timers.push(window.setTimeout(() => { void refresh(); }, delay));
      }
      // Drop any scheduled refetches if the component unmounts before they fire.
      return () => timers.forEach((t) => window.clearTimeout(t));
    };
    let cancel: (() => void) | null = null;
    const onCustomEvent = () => {
      cancel?.();
      cancel = handleSaved();
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'financial_metric_mapping_saved_at') {
        cancel?.();
        cancel = handleSaved();
      }
    };
    window.addEventListener('financial-metric-mapping-saved', onCustomEvent);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener('financial-metric-mapping-saved', onCustomEvent);
      window.removeEventListener('storage', onStorage);
      cancel?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId]);

  const fallbackTermsForMetric = (
    key: ApiFinancialMetricReconciliationRow['metric_key'],
  ): ApiFinancialMetricTerm[] | null => mappingConfig?.[key] ?? null;

  const snowflakeFormulaForMetric = (
    key: ApiFinancialMetricReconciliationRow['metric_key'],
  ): string | null => snowflakeFormulaConfig?.[key]?.formula ?? null;

  // ---- group by entity (only entities with an uploaded AFS file) ---- //
  const entityGroups = useMemo(() => {
    const entityIds = [...new Set(reconRows.map((r) => r.entity_id))];
    return entityIds
      .filter((eid) => (afsSourceByEntity[eid]?.files?.length ?? 0) > 0)
      .map((eid) => ({
        entityId: eid,
        label: entityMap[eid] ?? `Entity ${eid}`,
        fyEnd: entityFyEndMap[eid] ?? null,
        rows: reconRows
          .filter((r) => r.entity_id === eid)
          .sort((a, b) => {
            const ORDER = ['revenue', 'ebitda', 'pbt', 'pat', 'cash', 'debt'];
            return ORDER.indexOf(a.metric_key) - ORDER.indexOf(b.metric_key);
          }),
        manual: manualRows.filter((m) => m.entity_id === eid),
      }));
  }, [reconRows, manualRows, entityMap, entityFyEndMap, afsSourceByEntity]);

  const misSnowflakeByCycle = useMemo(() => {
    const m = new Map<string, ApiFinancialDataSnowflakeRow>();
    for (const row of snowflakeRows) {
      if (row.entity_id != null) continue;
      const rc = (row.review_cycle ?? '').trim();
      if (rc) m.set(rc, row);
    }
    return m;
  }, [snowflakeRows]);

  // Company-level MIS rows whose review cycle has NO audited (AFS) entity group.
  // These would otherwise be silently dropped — surface them as reported-only,
  // deduped by cycle (via misSnowflakeByCycle) so they never duplicate a matched group.
  const unmatchedMisRows = useMemo(() => {
    const coveredCycles = new Set(
      entityGroups.map((g) => (g.rows[0]?.review_cycle ?? '').trim()).filter(Boolean),
    );
    const out: ApiFinancialDataSnowflakeRow[] = [];
    for (const [cycle, row] of misSnowflakeByCycle) {
      if (!coveredCycles.has(cycle)) out.push(row);
    }
    return out;
  }, [entityGroups, misSnowflakeByCycle]);

  const patchReconField = async (id: number, patch: Parameters<typeof patchFinancialMetricReconciliation>[1]) => {
    try {
      await patchFinancialMetricReconciliation(id, patch);
      await refresh();
    } catch (e: unknown) {
      const detail =
        e && typeof e === 'object' && 'response' in e
          ? String((e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ?? '')
          : '';
      toast.error(detail || 'Failed to update');
    }
  };

  // ---- text field edit dialog ---- //
  const [textFieldEdit, setTextFieldEdit] = useState<{
    label: string;
    metricLabel: string;
    current: string;
    draft: string;
    onSave: (val: string | null) => void;
  } | null>(null);

  const openTextFieldEdit = (
    label: string,
    metricLabel: string,
    current: string | null,
    onSave: (val: string | null) => void,
  ) => {
    setTextFieldEdit({ label, metricLabel, current: current ?? '', draft: current ?? '', onSave });
  };

  const submitTextFieldEdit = () => {
    if (!textFieldEdit) return;
    const val = textFieldEdit.draft.trim() || null;
    const old = textFieldEdit.current.trim() || null;
    if (val === old) { setTextFieldEdit(null); return; }
    setPendingFieldEdit({
      label: textFieldEdit.label,
      oldValue: old ?? '—',
      newValue: val ?? '—',
      apply: () => textFieldEdit.onSave(val),
    });
    setTextFieldEdit(null);
  };

  // ---- generic field confirm ---- //
  const [pendingFieldEdit, setPendingFieldEdit] = useState<{
    label: string;
    oldValue: string;
    newValue: string;
    apply: () => void;
  } | null>(null);

  // ---- enable toggle confirm ---- //
  const [pendingToggle, setPendingToggle] = useState<{ id: number; newValue: boolean; kind: 'recon' | 'manual' } | null>(null);

  const confirmToggle = async () => {
    if (!pendingToggle) return;
    try {
      if (pendingToggle.kind === 'recon') {
        await patchFinancialMetricReconciliation(pendingToggle.id, { enable: pendingToggle.newValue });
      } else {
        await patchManualReconciliationQuery(pendingToggle.id, { enable: pendingToggle.newValue });
      }
      await refresh();
      toast.success(`Row ${pendingToggle.newValue ? 'enabled' : 'disabled'}`);
    } catch (e: unknown) {
      const detail =
        e && typeof e === 'object' && 'response' in e
          ? String((e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ?? '')
          : '';
      toast.error(detail || 'Failed to toggle');
    } finally {
      setPendingToggle(null);
    }
  };

  const patchManualField = async (id: number, patch: Parameters<typeof patchManualReconciliationQuery>[1]) => {
    try {
      await patchManualReconciliationQuery(id, patch);
      await refresh();
    } catch (e: unknown) {
      const detail =
        e && typeof e === 'object' && 'response' in e
          ? String((e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ?? '')
          : '';
      toast.error(detail || 'Failed to update');
    }
  };

  // ---- delete manual query ---- //
  const [deletingManualId, setDeletingManualId] = useState<number | null>(null);

  const confirmDeleteManual = async () => {
    if (deletingManualId === null) return;
    try {
      await deleteManualReconciliationQuery(deletingManualId);
      await refresh();
      toast.success('Query deleted');
    } catch {
      toast.error('Failed to delete query');
    } finally {
      setDeletingManualId(null);
    }
  };

  // ---- add manual query ---- //
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [addSubmitting, setAddSubmitting] = useState(false);
  const [addForm, setAddForm] = useState<AddManualForm>({
    entity_id: '',
    type: '',
    discrepency_text: '',
    company_response: '',
    reviewer_remarks: '',
    flagged: false,
  });

  const addFormValid = addForm.entity_id !== '' && addForm.discrepency_text.trim() !== '';

  const handleAddManual = async () => {
    if (!addFormValid) return;
    setAddSubmitting(true);
    try {
      await createManualReconciliationQuery({
        portfolio_company_id: companyId,
        entity_id: Number(addForm.entity_id),
        discrepency_text: addForm.discrepency_text.trim(),
        type: addForm.type.trim() || null,
        status: 'Open',
        enable: true,
        company_response: addForm.company_response.trim() || null,
        reviewer_remarks: addForm.reviewer_remarks.trim() || null,
        flagged: addForm.flagged,
      });
      await refresh();
      toast.success('Manual query added');
      setAddForm({ entity_id: '', type: '', discrepency_text: '', company_response: '', reviewer_remarks: '', flagged: false });
      setShowAddDialog(false);
    } catch {
      toast.error('Failed to add query');
    } finally {
      setAddSubmitting(false);
    }
  };

  // ---- inline amount edit ---- //
  const [inlineAmountEdit, setInlineAmountEdit] = useState<{
    rowId: number;
    kind: 'mis' | 'afs';
    currentDisplayValue: string;
    currency: string | null;
    afsCurrency: string | null;
    metricKey: string;
    reviewCycle: string;
  } | null>(null);
  const [inlineValue, setInlineValue] = useState('');
  const [inlineReason, setInlineReason] = useState('');
  const [pendingInlineConfirm, setPendingInlineConfirm] = useState<{
    rowId: number;
    kind: 'mis' | 'afs';
    fullVal: number;
    metricKey: string;
    reviewCycle: string;
    currency: string | null;
    afsCurrency: string | null;
    oldDisplay: string;
    newDisplay: string;
    reason: string;
  } | null>(null);

  const openInlineEdit = (row: ApiFinancialMetricReconciliationRow, kind: 'mis' | 'afs') => {
    const currency = kind === 'mis' ? row.mis_currency : row.afs_currency;
    const amount = kind === 'mis' ? row.mis_amount : row.afs_amount;
    // MIS is already in millions ⇒ no scaling; AFS uses the currency scale.
    const divisor = kind === 'mis' ? 1 : currencyScale(currency).divisor;
    setInlineValue(amount != null ? String(+(amount / divisor).toFixed(4)) : '');
    setInlineReason('');
    setInlineAmountEdit({
      rowId: row.id,
      kind,
      currency,
      afsCurrency: row.afs_currency,
      currentDisplayValue: kind === 'mis' ? fmtMisAmount(amount) : fmtAmount(amount, currency),
      metricKey: row.metric_key,
      reviewCycle: (row.review_cycle ?? '').trim(),
    });
  };

  const submitInlineEdit = () => {
    if (!inlineAmountEdit) return;
    const reason = inlineReason.trim();
    if (!reason) { toast.error('Edit reason is required'); return; }
    const parsed = parseFloat(inlineValue);
    if (isNaN(parsed)) { toast.error('Enter a valid number'); return; }
    // For MIS edits where no row exists yet, use AFS currency as the unit for input
    const effectiveCurrency = inlineAmountEdit.currency ?? inlineAmountEdit.afsCurrency;
    // MIS values stay in millions (no scaling); AFS scales by currency.
    const divisor = inlineAmountEdit.kind === 'mis' ? 1 : currencyScale(effectiveCurrency).divisor;
    const fullVal = parsed * divisor;
    setPendingInlineConfirm({
      rowId: inlineAmountEdit.rowId,
      kind: inlineAmountEdit.kind,
      fullVal,
      metricKey: inlineAmountEdit.metricKey,
      reviewCycle: inlineAmountEdit.reviewCycle,
      currency: inlineAmountEdit.currency,
      afsCurrency: inlineAmountEdit.afsCurrency,
      oldDisplay: inlineAmountEdit.currentDisplayValue,
      newDisplay: inlineAmountEdit.kind === 'mis' ? fmtMisAmount(fullVal) : fmtAmount(fullVal, effectiveCurrency),
      reason,
    });
    setInlineAmountEdit(null);
  };

  const confirmInlineEdit = async () => {
    if (!pendingInlineConfirm) return;
    const { rowId, kind, fullVal, metricKey, reviewCycle, currency, afsCurrency, reason } = pendingInlineConfirm;
    setPendingInlineConfirm(null);
    try {
      if (kind === 'afs') {
        await patchFinancialMetricReconciliation(rowId, { afs_amount: fullVal, edit_reason: reason });
      } else {
        const sfRow = misSnowflakeByCycle.get(reviewCycle);
        if (sfRow) {
          await patchFinancialDataSnowflake(sfRow.id, {
            [metricKey]: fullVal,
            edit_reason: reason,
          } as Parameters<typeof patchFinancialDataSnowflake>[1]);
        } else {
          // No MIS row exists — create one using AFS currency so amounts are comparable.
          await createFinancialDataSnowflake({
            portfolio_company_id: companyId,
            entity_id: null,
            review_cycle: reviewCycle || null,
            currency: currency ?? afsCurrency ?? null,
            metric_key: metricKey,
            metric_value: fullVal,
            edit_reason: reason,
          });
        }
      }
      await refresh();
      toast.success('Amount updated');
    } catch (e: unknown) {
      const detail =
        e && typeof e === 'object' && 'response' in e
          ? String((e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ?? '')
          : '';
      toast.error(detail || 'Failed to update amount');
    }
  };

  // ---- currency conversion ---- //
  const [convertingMisKey, setConvertingMisKey] = useState<string | null>(null);
  const [convertingAfsKey, setConvertingAfsKey] = useState<string | null>(null);
  // entityIds whose currency mismatch the user has resolved (or is resolving)
  const [mismatchResolved, setMismatchResolved] = useState<Set<number>>(new Set());
  const [pendingConvert, setPendingConvert] = useState<{
    kind: 'mis' | 'afs' | 'both';
    reviewCycle: string;
    entityId: number;
    entityLabel: string;
    targetCurrency: 'USD' | 'INR';
    sourceCurrency: string | null;
    afsCurrency?: string | null;
  } | null>(null);

  const requestConvertBoth = (
    entityId: number,
    reviewCycle: string,
    targetCurrency: 'USD' | 'INR',
    misCurrency: string | null,
    afsCurrency: string | null,
    entityLabel: string,
  ) => {
    setPendingConvert({
      kind: 'both',
      reviewCycle: reviewCycle.trim(),
      entityId,
      entityLabel,
      targetCurrency,
      sourceCurrency: misCurrency,
      afsCurrency,
    });
  };

  const confirmConvert = async () => {
    if (!pendingConvert) return;
    const { kind, reviewCycle, entityId, targetCurrency } = pendingConvert;
    setPendingConvert(null);
    if (kind === 'mis') {
      await handleConvertMis(reviewCycle, targetCurrency);
    } else if (kind === 'afs') {
      await handleConvertAfs(entityId, reviewCycle, targetCurrency);
    } else {
      await handleConvertMis(reviewCycle, targetCurrency);
      await handleConvertAfs(entityId, reviewCycle, targetCurrency);
    }
  };

  const handleConvertMis = async (reviewCycle: string, targetCurrency: 'USD' | 'INR') => {
    const rc = reviewCycle.trim();
    const sfRow = misSnowflakeByCycle.get(rc);
    if (!sfRow) {
      toast.error('No company-level MIS row found for this review cycle');
      return;
    }
    setConvertingMisKey(rc);
    try {
      await convertFinancialDataSnowflakeCurrency(sfRow.id, targetCurrency);
      await refresh();
      toast.success(`MIS amounts converted to ${targetCurrency}`);
    } catch (e: unknown) {
      const detail =
        e && typeof e === 'object' && 'response' in e
          ? String((e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ?? '')
          : '';
      toast.error(detail || 'Failed to convert MIS currency');
    } finally {
      setConvertingMisKey(null);
    }
  };

  const handleConvertAfs = async (entityId: number, reviewCycle: string, targetCurrency: 'USD' | 'INR') => {
    const key = `${entityId}:${reviewCycle}`;
    setConvertingAfsKey(key);
    try {
      const r = await convertAfsCurrencyForEntityCycle({
        entity_id: entityId,
        review_cycle: reviewCycle,
        target_currency: targetCurrency,
      });
      await refresh();
      toast.success(`AFS amounts converted to ${targetCurrency} (${r.updated} row(s))`);
    } catch (e: unknown) {
      const detail =
        e && typeof e === 'object' && 'response' in e
          ? String((e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ?? '')
          : '';
      toast.error(detail || 'Failed to convert AFS currency');
    } finally {
      setConvertingAfsKey(null);
    }
  };

  return (
    <TooltipProvider delayDuration={150}>
    <div className="p-6 space-y-6">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <p className="text-xs text-gray-500">
          {loading ? 'Loading…' : `${reconRows.length} reconciliation row(s) · ${manualRows.length} manual quer${manualRows.length === 1 ? 'y' : 'ies'}`}
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { void refresh(); }}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            title="Re-fetch reconciliation rows and mapping (use after saving the Financial Extraction Mapping)"
          >
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} /> Refresh
          </button>
          <button
            onClick={() => setShowAddDialog(true)}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-blue-500 text-white hover:bg-blue-600 transition-all"
          >
            <Plus className="h-4 w-4" /> Add Manual Query
          </button>
        </div>
      </div>

      {!loading && entityGroups.length === 0 && unmatchedMisRows.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-2 bg-white rounded-lg border border-gray-200">
          <AlertTriangle className="h-8 w-8 text-gray-300" />
          <p className="text-sm text-gray-400">No reconciliation data yet. Upload audit files to populate.</p>
        </div>
      ) : (
        <>
        {entityGroups.map((group) => {
          const reviewCycle = (group.rows[0]?.review_cycle ?? '').trim();
          const misCurrency = normalizeCurrencyCode(group.rows[0]?.mis_currency);
          const afsCurrency = normalizeCurrencyCode(group.rows[0]?.afs_currency);
          const afsConvertKey = `${group.entityId}:${reviewCycle}`;
          const hasCurrencyMismatch =
            misCurrency != null &&
            afsCurrency != null &&
            misCurrency !== afsCurrency;
          const isMismatchConverting = convertingAfsKey === afsConvertKey;
          const isMismatchResolved = mismatchResolved.has(group.entityId);
          const showMismatchGate = hasCurrencyMismatch && !isMismatchResolved;

          const resolveMismatch = async () => {
            if (!misCurrency) return;
            setMismatchResolved((prev) => new Set(prev).add(group.entityId));
            await handleConvertAfs(group.entityId, reviewCycle, misCurrency as 'USD' | 'INR');
          };

          return (
            <div key={group.entityId} className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
              {/* Entity header */}
              <div className="flex flex-wrap items-center gap-3 px-4 py-2.5 bg-blue-50 border-b border-blue-100">
                <span className="text-sm font-semibold text-blue-800 mr-2">{group.label}</span>
                <span className="text-[11px] font-medium text-blue-600/80 mr-2">
                  FY End: {group.fyEnd?.trim() ? group.fyEnd : '—'}
                </span>
                {!showMismatchGate && (
                  <CurrencyConvertSelect
                    currentCurrency={misCurrency}
                    converting={convertingMisKey === reviewCycle || convertingAfsKey === afsConvertKey}
                    onConvert={(target) => {
                      requestConvertBoth(group.entityId, reviewCycle, target, misCurrency, afsCurrency, group.label);
                    }}
                  />
                )}
                {/* Source-file picker — shown when the entity has more than one AFS file. The
                    chosen file drives the values, breakdown, source links and query-email numbers. */}
                {(() => {
                  const files = afsSourceByEntity[group.entityId]?.files ?? [];
                  if (files.length <= 1) return null;
                  const primaryId = files.find((f) => f.is_primary)?.file_id ?? '';
                  const busy = primaryFileBusyEntity === group.entityId;
                  return (
                    <div className="flex items-center gap-1.5">
                      <span className="text-[11px] text-blue-700/70">Source file</span>
                      <select
                        value={primaryId}
                        disabled={busy}
                        onChange={(e) => {
                          const fid = Number(e.target.value);
                          if (!fid || fid === primaryId) return;
                          const fn = files.find((x) => x.file_id === fid)?.filename ?? '';
                          setPendingFileChange({ entityId: group.entityId, fileId: fid, filename: fn });
                        }}
                        title="Which audited-financials file drives this entity's reconciliation"
                        className="max-w-[220px] truncate px-2 py-1 border border-blue-200 rounded-md text-xs bg-white text-blue-900 focus:outline-none focus:ring-2 focus:ring-blue-300 disabled:opacity-60"
                      >
                        {files.map((f) => (
                          <option key={f.file_id} value={f.file_id}>
                            {f.filename}
                            {f.ocr_status && f.ocr_status !== 'completed' ? ' (incomplete)' : ''}
                          </option>
                        ))}
                      </select>
                      {busy && <span className="text-[11px] text-blue-700/70">updating…</span>}
                    </div>
                  );
                })()}
              </div>

              {/* Currency mismatch gate — blocks the table until AFS is aligned to MIS */}
              {showMismatchGate && (
                <div className="flex flex-col items-center justify-center gap-4 py-10 px-6 bg-amber-50 border-b border-amber-100">
                  <AlertTriangle className="h-8 w-8 text-amber-500 shrink-0" />
                  <div className="text-center space-y-1">
                    <p className="text-sm font-semibold text-amber-900">Currency mismatch detected</p>
                    <p className="text-xs text-amber-800">
                      MIS values are in <span className="font-bold">{misCurrency} ({currencyScale(misCurrency).unit})</span> but
                      AFS values are in <span className="font-bold">{afsCurrency} ({currencyScale(afsCurrency).unit})</span>.
                      AFS must be converted to {misCurrency} before you can review this entity.
                    </p>
                  </div>
                  <button
                    disabled={isMismatchConverting}
                    onClick={() => void resolveMismatch()}
                    className="inline-flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-60 disabled:cursor-not-allowed transition-all"
                  >
                    {isMismatchConverting
                      ? 'Converting…'
                      : `Convert AFS → ${misCurrency} (${currencyScale(misCurrency).unit})`}
                  </button>
                </div>
              )}

              {/* Reconciliation table — hidden while mismatch gate is active */}
              {!showMismatchGate && group.rows.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-max min-w-full text-sm border-collapse">

                    <thead>
                      <tr className="bg-gray-50 border-b border-gray-200 text-xs text-gray-500 uppercase tracking-wider">
                        <th className="sticky left-0 bg-gray-50 z-10 px-4 py-3 text-left font-medium whitespace-nowrap min-w-[8rem]">Metric</th>
                        <th className="px-4 py-3 text-right font-medium whitespace-nowrap min-w-[9rem]">
                          As Per MIS{misCurrency ? ` (${currencyScale(misCurrency).unit})` : ''}
                        </th>
                        <th className="px-4 py-3 text-right font-medium whitespace-nowrap min-w-[9rem]">
                          As Per AFS{afsCurrency ? ` (${currencyScale(afsCurrency).unit})` : ''}
                        </th>
                        <th className="px-4 py-3 text-right font-medium whitespace-nowrap min-w-[9rem]">
                          Variance Abs{misCurrency ? ` (${currencyScale(misCurrency).unit})` : ''}
                        </th>
                        <th className="px-4 py-3 text-right font-medium whitespace-nowrap min-w-[7rem]">Variance (%)</th>
                        <th className="px-4 py-3 text-left font-medium whitespace-nowrap min-w-[9rem]">Difference Category</th>
                        <th className="px-4 py-3 text-left font-medium whitespace-nowrap min-w-[6rem]">To Send?</th>
                        <th className="px-4 py-3 text-left font-medium whitespace-nowrap min-w-[8rem]">Status</th>
                        <th className="px-4 py-3 text-left font-medium min-w-[8rem] max-w-[10rem]">Variance Category</th>
                        <th className="px-4 py-3 text-left font-medium min-w-[12rem]">Company Response</th>
                        <th className="px-4 py-3 text-left font-medium min-w-[12rem]">Reviewer Remarks</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {group.rows.map((row) => {
                        const { absLabel, pctLabel, breach, diffCategory } = fmtVariance(
                          row.mis_amount,
                          row.afs_amount,
                          row.mis_currency,
                          row.metric_key,
                          fieldThresholds,
                          absoluteThresholds,
                          usdToInrRate,
                          row.afs_currency,
                        );
                        const status = normalizeStatus(row.status);
                        return (
                          <tr key={row.id} className="group hover:bg-gray-50 transition-colors">
                            {/* Metric label — plain identifier, no tooltip */}
                            <td className="sticky left-0 bg-white z-10 px-4 py-3 font-medium text-gray-800 overflow-hidden">
                              <span className="block truncate">
                                {getFinancialParameterLabel(row.metric_key)}
                              </span>
                            </td>

                            {/* As Per MIS — hover shows the Snowflake PR formula */}
                            <td className="px-4 py-3 text-right text-gray-700 overflow-hidden font-mono text-xs">
                              <span className="inline-flex items-center gap-1 justify-end">
                                {(() => {
                                  const formula = snowflakeFormulaForMetric(row.metric_key);
                                  const hasFormula = misFormulaHasContent(formula);
                                  const misEdited =
                                    misSnowflakeByCycle.get((row.review_cycle ?? '').trim())?.manual_edits?.[row.metric_key];
                                  return (
                                    <MisFormulaTooltip
                                      metricLabel={getFinancialParameterLabel(row.metric_key)}
                                      formula={formula}
                                    >
                                      <span
                                        tabIndex={hasFormula ? 0 : -1}
                                        className={cn(
                                          'rounded focus:outline-none',
                                          hasFormula &&
                                            'underline decoration-dotted decoration-gray-400 underline-offset-4 cursor-help focus:ring-2 focus:ring-blue-300',
                                          misEdited && 'text-amber-700 font-semibold',
                                        )}
                                      >
                                        {fmtMisAmount(row.mis_amount)}
                                      </span>
                                    </MisFormulaTooltip>
                                  );
                                })()}
                                {misSnowflakeByCycle.get((row.review_cycle ?? '').trim())?.manual_edits?.[row.metric_key] ? (
                                  <EditedIndicator
                                    marker={misSnowflakeByCycle.get((row.review_cycle ?? '').trim())!.manual_edits![row.metric_key]}
                                    className="shrink-0"
                                  />
                                ) : null}
                                <button
                                  onClick={() => openInlineEdit(row, 'mis')}
                                  className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-blue-500 transition-all"
                                  title="Edit MIS amount"
                                >
                                  <Pencil className="h-3 w-3" />
                                </button>
                              </span>
                            </td>

                            {/* As Per AFS — hover shows OCR extraction mapping breakdown */}
                            <td className="px-4 py-3 text-right text-gray-700 overflow-hidden font-mono text-xs">
                              <span className="inline-flex items-center gap-1 justify-end">
                                {(() => {
                                  const fallback = fallbackTermsForMetric(row.metric_key);
                                  const hasContent = afsBreakdownHasContent(row.mapping_breakdown, fallback);
                                  const afsEdited = row.manual_edits?.afs_amount;
                                  return (
                                    <AfsBreakdownTooltip
                                      metricLabel={getFinancialParameterLabel(row.metric_key)}
                                      breakdown={row.mapping_breakdown ?? null}
                                      fallbackTerms={fallback}
                                      currency={row.afs_currency}
                                      sourceRefs={afsSourceByEntity[row.entity_id]?.sourceRefs ?? null}
                                      onShowSource={(path) => void openAfsSource(row.entity_id, path)}
                                      fileAvailable={(afsSourceByEntity[row.entity_id]?.fileId ?? null) != null}
                                    >
                                      <span
                                        tabIndex={hasContent ? 0 : -1}
                                        className={cn(
                                          'rounded focus:outline-none',
                                          hasContent &&
                                            'underline decoration-dotted decoration-gray-400 underline-offset-4 cursor-help focus:ring-2 focus:ring-blue-300',
                                          afsEdited && 'text-amber-700 font-semibold',
                                        )}
                                      >
                                        {fmtAmount(row.afs_amount, row.afs_currency)}
                                      </span>
                                    </AfsBreakdownTooltip>
                                  );
                                })()}
                                {row.manual_edits?.afs_amount ? (
                                  <EditedIndicator marker={row.manual_edits.afs_amount} className="shrink-0" />
                                ) : null}
                                <button
                                  onClick={() => openInlineEdit(row, 'afs')}
                                  className="opacity-0 group-hover:opacity-100 text-gray-500 hover:text-blue-500 transition-all"
                                  title="Edit AFS amount"
                                >
                                  <Pencil className="h-3 w-3" />
                                </button>
                              </span>
                            </td>
                            <td className={`px-4 py-3 text-right overflow-hidden font-mono text-xs ${breach ? 'text-red-600 font-semibold' : 'text-gray-500'}`}>
                              <span className="block truncate">{absLabel}</span>
                            </td>
                            <td className={`px-4 py-3 text-right overflow-hidden font-mono text-xs ${breach ? 'text-red-600 font-semibold' : 'text-gray-500'}`}>
                              <span className="block truncate">{pctLabel}</span>
                            </td>
                            <td className="px-4 py-3 overflow-hidden text-xs">
                              <span className={`inline-block max-w-full truncate px-2 py-0.5 rounded-full font-medium ${
                                diffCategory === 'Not Comparable'
                                  ? 'bg-gray-100 text-gray-500'
                                  : breach
                                  ? 'bg-red-100 text-red-700'
                                  : 'bg-green-100 text-green-700'
                              }`}>
                                {diffCategory}
                              </span>
                            </td>
                            <td className="px-4 py-3 overflow-hidden">
                              <select
                                value={row.enable ? 'Yes' : 'No'}
                                onChange={(e) => {
                                  const newVal = e.target.value === 'Yes';
                                  if (newVal !== row.enable) setPendingToggle({ id: row.id, newValue: newVal, kind: 'recon' });
                                }}
                                className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium cursor-pointer border-none focus:outline-none focus:ring-2 focus:ring-blue-500 ${row.enable ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-500'}`}
                              >
                                <option value="Yes">Yes</option>
                                <option value="No">No</option>
                              </select>
                            </td>
                            <td className="px-4 py-3 overflow-hidden">
                              <div className="flex items-center gap-1.5">
                                <select
                                  value={status}
                                  onChange={(e) => {
                                    const newStatus = e.target.value as ReconciliationStatus;
                                    if (newStatus === status) return;
                                    setPendingFieldEdit({
                                      label: 'Status',
                                      oldValue: status,
                                      newValue: newStatus,
                                      apply: () => void patchReconField(row.id, { status: newStatus }),
                                    });
                                  }}
                                  className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium cursor-pointer border-none focus:outline-none focus:ring-2 focus:ring-blue-500 ${statusBadge[status]}`}
                                >
                                  {RECONCILIATION_STATUS_OPTIONS.map((s) => (
                                    <option key={s} value={s}>{s.toUpperCase()}</option>
                                  ))}
                                </select>
                                {row.manual_edits?.status ? (
                                  <EditedIndicator marker={row.manual_edits.status} label="" className="shrink-0 px-1" />
                                ) : null}
                              </div>
                            </td>
                            <td className="px-4 py-3 overflow-hidden min-w-[8rem] max-w-[10rem]">
                              <div className="flex items-center gap-1">
                                {(() => {
                                  const opts = varianceCategoriesForDiscrepancyType(row.metric_key);
                                  const extra = row.variance_category && !opts.includes(row.variance_category as never) ? [row.variance_category] : [];
                                  return (
                                    <select
                                      value={row.variance_category ?? ''}
                                      onChange={(e) => {
                                        const val = e.target.value || null;
                                        if (val === (row.variance_category ?? null)) return;
                                        setPendingFieldEdit({
                                          label: 'Variance Category',
                                          oldValue: row.variance_category ?? '—',
                                          newValue: val ?? '—',
                                          apply: () => void patchReconField(row.id, { variance_category: val }),
                                        });
                                      }}
                                      className="w-full text-xs border border-gray-300 rounded-md px-2 py-1.5 bg-white text-gray-800 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 appearance-auto shadow-sm"
                                    >
                                      <option value="">—</option>
                                      {[...extra, ...opts].map((o) => (
                                        <option key={o} value={o}>{o}</option>
                                      ))}
                                    </select>
                                  );
                                })()}
                                {row.manual_edits?.variance_category ? (
                                  <EditedIndicator marker={row.manual_edits.variance_category} label="" className="shrink-0 px-1" />
                                ) : null}
                              </div>
                            </td>
                            <td className="px-4 py-3 overflow-hidden">
                              <div className="flex items-start gap-1 group/cell">
                                <span className="flex-1 text-xs text-gray-700 whitespace-pre-wrap break-words leading-relaxed min-w-0">
                                  {row.company_response ?? <span className="text-gray-400">—</span>}
                                </span>
                                {row.manual_edits?.company_response ? (
                                  <EditedIndicator marker={row.manual_edits.company_response} label="" className="shrink-0 px-1 mt-0.5" />
                                ) : null}
                                <button
                                  onClick={() => openTextFieldEdit('Company Response', getFinancialParameterLabel(row.metric_key), row.company_response, (val) => void patchReconField(row.id, { company_response: val }))}
                                  className="shrink-0 opacity-0 group-hover/cell:opacity-100 text-gray-500 hover:text-blue-500 transition-all mt-0.5"
                                  title="Edit company response"
                                >
                                  <Pencil className="h-3 w-3" />
                                </button>
                              </div>
                            </td>
                            <td className="px-4 py-3 overflow-hidden">
                              <div className="flex items-start gap-1 group/cell">
                                <span className="flex-1 text-xs text-gray-700 whitespace-pre-wrap break-words leading-relaxed min-w-0">
                                  {row.reviewer_remarks ?? <span className="text-gray-400">—</span>}
                                </span>
                                {row.manual_edits?.reviewer_remarks ? (
                                  <EditedIndicator marker={row.manual_edits.reviewer_remarks} label="" className="shrink-0 px-1 mt-0.5" />
                                ) : null}
                                <button
                                  onClick={() => openTextFieldEdit('Reviewer Remarks', getFinancialParameterLabel(row.metric_key), row.reviewer_remarks, (val) => void patchReconField(row.id, { reviewer_remarks: val }))}
                                  className="shrink-0 opacity-0 group-hover/cell:opacity-100 text-gray-500 hover:text-blue-500 transition-all mt-0.5"
                                  title="Edit reviewer remarks"
                                >
                                  <Pencil className="h-3 w-3" />
                                </button>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>

                    {/* Manual queries — same table, columns align automatically */}
                    {group.manual.length > 0 && (<>
                      <tbody>
                        <tr className="border-t border-gray-200">
                          <td colSpan={11} className="px-4 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider bg-gray-50">
                            Manual Queries
                          </td>
                        </tr>
                      </tbody>
                      <tbody className="divide-y divide-gray-100">
                        {group.manual.map((mq) => {
                          const mqStatus = normalizeStatus(mq.status);
                          const greyCell = 'px-4 py-3 text-right text-gray-500 font-mono text-xs';
                          return (
                            <tr key={mq.id} className="group hover:bg-gray-50 transition-colors">
                              {/* Metric col — query text with edit pencil */}
                              <td className="sticky left-0 bg-white z-10 px-4 py-3 overflow-hidden">
                                <div className="flex items-start gap-1 group/cell">
                                  <span className="flex-1 text-xs text-gray-800 leading-relaxed whitespace-pre-wrap break-words min-w-0">
                                    {mq.discrepency_text ?? <span className="text-gray-400">—</span>}
                                  </span>
                                  <button
                                    onClick={() => openTextFieldEdit('Query Text', 'Manual Query', mq.discrepency_text, (val) => void patchManualField(mq.id, { discrepency_text: val ?? '' }))}
                                    className="shrink-0 opacity-0 group-hover/cell:opacity-100 text-gray-500 hover:text-blue-500 transition-all mt-0.5"
                                    title="Edit query text"
                                  >
                                    <Pencil className="h-3 w-3" />
                                  </button>
                                </div>
                              </td>
                              {/* As Per MIS — N/A */}
                              <td className={`${greyCell} overflow-hidden`}>—</td>
                              {/* As Per AFS — N/A */}
                              <td className={`${greyCell} overflow-hidden`}>—</td>
                              {/* Variance Abs — N/A */}
                              <td className={`${greyCell} overflow-hidden`}>—</td>
                              {/* Variance % — N/A */}
                              <td className={`${greyCell} overflow-hidden`}>—</td>
                              {/* Difference Category — N/A */}
                              <td className={`${greyCell} overflow-hidden`}>—</td>
                              {/* To Send? */}
                              <td className="px-4 py-3 overflow-hidden">
                                <select
                                  value={mq.enable ? 'Yes' : 'No'}
                                  onChange={(e) => {
                                    const newVal = e.target.value === 'Yes';
                                    if (newVal !== mq.enable) setPendingToggle({ id: mq.id, newValue: newVal, kind: 'manual' });
                                  }}
                                  className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium cursor-pointer border-none focus:outline-none focus:ring-2 focus:ring-blue-500 ${mq.enable ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-500'}`}
                                >
                                  <option value="Yes">Yes</option>
                                  <option value="No">No</option>
                                </select>
                              </td>
                              {/* Status */}
                              <td className="px-4 py-3 overflow-hidden">
                                <select
                                  value={mqStatus}
                                  onChange={(e) => {
                                    const newStatus = e.target.value as ReconciliationStatus;
                                    if (newStatus === mqStatus) return;
                                    setPendingFieldEdit({
                                      label: 'Status',
                                      oldValue: mqStatus,
                                      newValue: newStatus,
                                      apply: () => void patchManualField(mq.id, { status: newStatus }),
                                    });
                                  }}
                                  className={`inline-flex items-center px-2 py-1 rounded-full text-xs font-medium cursor-pointer border-none focus:outline-none focus:ring-2 focus:ring-blue-500 ${statusBadge[mqStatus]}`}
                                >
                                  {RECONCILIATION_STATUS_OPTIONS.map((s) => (
                                    <option key={s} value={s}>{s.toUpperCase()}</option>
                                  ))}
                                </select>
                              </td>
                              {/* Variance Category — N/A */}
                              <td className={`${greyCell} overflow-hidden`}>—</td>
                              {/* Company Response */}
                              <td className="px-4 py-3 overflow-hidden">
                                <div className="flex items-start gap-1 group/cell">
                                  <span className="flex-1 text-xs text-gray-700 whitespace-pre-wrap break-words leading-relaxed min-w-0">
                                    {mq.company_response ?? <span className="text-gray-400">—</span>}
                                  </span>
                                  <button
                                    onClick={() => openTextFieldEdit('Company Response', 'Manual Query', mq.company_response, (val) => void patchManualField(mq.id, { company_response: val }))}
                                    className="shrink-0 opacity-0 group-hover/cell:opacity-100 text-gray-500 hover:text-blue-500 transition-all mt-0.5"
                                    title="Edit company response"
                                  >
                                    <Pencil className="h-3 w-3" />
                                  </button>
                                </div>
                              </td>
                              {/* Reviewer Remarks */}
                              <td className="px-4 py-3 overflow-hidden">
                                <div className="flex items-start gap-1 group/cell">
                                  <span className="flex-1 text-xs text-gray-700 whitespace-pre-wrap break-words leading-relaxed min-w-0">
                                    {mq.reviewer_remarks ?? <span className="text-gray-400">—</span>}
                                  </span>
                                  <button
                                    onClick={() => openTextFieldEdit('Reviewer Remarks', 'Manual Query', mq.reviewer_remarks, (val) => void patchManualField(mq.id, { reviewer_remarks: val }))}
                                    className="shrink-0 opacity-0 group-hover/cell:opacity-100 text-gray-500 hover:text-blue-500 transition-all mt-0.5"
                                    title="Edit reviewer remarks"
                                  >
                                    <Pencil className="h-3 w-3" />
                                  </button>
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </>)}
                  </table>
                </div>
              )}
            </div>
          );
        })}
        {/* MIS-only: reported data whose review cycle has no uploaded audited file.
            Shown so API-returned MIS rows are never silently hidden. No variance is
            computed (there is no audited counterpart to reconcile against). */}
        {unmatchedMisRows.map((mis) => {
          const cycle = (mis.review_cycle ?? '').trim();
          const misCurrency = normalizeCurrencyCode(mis.currency);
          return (
            <div key={`mis-only-${mis.id}`} className="bg-white rounded-xl border border-amber-200 shadow-sm overflow-hidden">
              <div className="flex flex-wrap items-center gap-3 px-4 py-2.5 bg-amber-50 border-b border-amber-100">
                <span className="text-sm font-semibold text-amber-800 mr-2">Reported (MIS) — no audited file</span>
                {cycle && (
                  <span className="text-xs font-semibold text-amber-900 bg-amber-100 px-2 py-0.5 rounded">{cycle}</span>
                )}
                {misCurrency && (
                  <span className="text-xs font-semibold text-amber-900 bg-amber-100 px-2 py-0.5 rounded">
                    {misCurrency} · {currencyScale(misCurrency).unit}
                  </span>
                )}
              </div>
              <div className="px-4 py-2 text-xs text-amber-700 bg-amber-50/40 border-b border-amber-100">
                No audited-financials file has been uploaded for this review cycle, so there is nothing to
                reconcile against. These are the reported (MIS) figures only — no variance is computed.
              </div>
              <div className="overflow-x-auto">
                <table className="w-max min-w-full text-sm border-collapse">
                  <thead>
                    <tr className="bg-gray-50 border-b border-gray-200 text-xs text-gray-500 uppercase tracking-wider">
                      <th className="sticky left-0 bg-gray-50 z-10 px-4 py-3 text-left font-medium whitespace-nowrap min-w-[8rem]">Metric</th>
                      <th className="px-4 py-3 text-right font-medium whitespace-nowrap min-w-[9rem]">
                        As Per MIS{misCurrency ? ` (${currencyScale(misCurrency).unit})` : ''}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {MIS_METRIC_KEYS.map((key) => (
                      <tr key={key} className="hover:bg-gray-50 transition-colors">
                        <td className="sticky left-0 bg-white z-10 px-4 py-3 font-medium text-gray-800">
                          {getFinancialParameterLabel(key)}
                        </td>
                        <td className="px-4 py-3 text-right text-gray-700 font-mono text-xs">
                          {fmtAmount(mis[key], mis.currency)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })}
        </>
      )}


      {/* ---- Add Manual Query Dialog ---- */}
      <Dialog open={showAddDialog} onOpenChange={(open) => { if (!addSubmitting) setShowAddDialog(open); }}>
        <DialogContent className="bg-white rounded-lg p-6 w-full max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold text-gray-900">Add Manual Query</DialogTitle>
            <DialogDescription className="text-gray-500">Create a manual investor query (not tied to metric values).</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label className="text-xs uppercase tracking-wider text-gray-500">Entity <span className="text-red-500">*</span></Label>
              <select value={addForm.entity_id} onChange={(e) => setAddForm((p) => ({ ...p, entity_id: e.target.value }))} className="mt-1 w-full text-sm border border-gray-300 rounded-lg px-3 py-2 bg-white text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500">
                <option value="">Select entity…</option>
                {entities.map((e) => <option key={e.id} value={String(e.id)}>{e.name}</option>)}
              </select>
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-gray-500">Type</Label>
              <Input value={addForm.type} onChange={(e) => setAddForm((p) => ({ ...p, type: e.target.value }))} className="mt-1" placeholder="e.g. COGS, Revenue" />
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-gray-500">Query Text <span className="text-red-500">*</span></Label>
              <textarea value={addForm.discrepency_text} onChange={(e) => setAddForm((p) => ({ ...p, discrepency_text: e.target.value }))} rows={3} className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="Describe the discrepancy…" />
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-gray-500">Company Response</Label>
              <textarea value={addForm.company_response} onChange={(e) => setAddForm((p) => ({ ...p, company_response: e.target.value }))} rows={2} className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="Optional" />
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-gray-500">Reviewer Remarks</Label>
              <textarea value={addForm.reviewer_remarks} onChange={(e) => setAddForm((p) => ({ ...p, reviewer_remarks: e.target.value }))} rows={2} className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="Optional" />
            </div>
          </div>
          <DialogFooter className="mt-4">
            <button disabled={addSubmitting} onClick={() => setShowAddDialog(false)} className="px-4 py-2 rounded-lg font-medium text-sm border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all disabled:opacity-50 disabled:cursor-not-allowed">Cancel</button>
            <button
              disabled={!addFormValid || addSubmitting}
              onClick={() => void handleAddManual()}
              className="px-4 py-2 rounded-lg font-medium text-sm bg-blue-500 text-white hover:bg-blue-600 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {addSubmitting ? 'Adding…' : 'Add Query'}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---- Inline amount edit dialog ---- */}
      <Dialog open={!!inlineAmountEdit} onOpenChange={(open) => !open && setInlineAmountEdit(null)}>
        <DialogContent className="bg-white rounded-lg p-6 w-full max-w-sm">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold text-gray-900">
              Edit {inlineAmountEdit?.kind === 'mis' ? 'MIS' : 'AFS'} Amount
            </DialogTitle>
            <DialogDescription className="text-gray-500">
              Current: <span className="font-mono text-gray-700">{inlineAmountEdit?.currentDisplayValue}</span>
            </DialogDescription>
          </DialogHeader>
          {inlineAmountEdit && (
            <form className="mt-2 space-y-4" onSubmit={(e) => { e.preventDefault(); submitInlineEdit(); }}>
              <div>
                <Label className="text-xs uppercase tracking-wider text-gray-500">
                  {(() => {
                    const effectiveCurrency = inlineAmountEdit.currency ?? inlineAmountEdit.afsCurrency;
                    return `New Value (${currencyScale(effectiveCurrency).unit} ${effectiveCurrency ?? ''})`;
                  })()}
                </Label>
                <input
                  type="number"
                  step="any"
                  value={inlineValue}
                  onChange={(e) => setInlineValue(e.target.value)}
                  className="mt-1 w-full font-mono text-right px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <Label className="text-xs uppercase tracking-wider text-gray-500">Reason for Change <span className="text-red-500">*</span></Label>
                <textarea
                  value={inlineReason}
                  onChange={(e) => setInlineReason(e.target.value)}
                  rows={2}
                  className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="Why is this value being changed?…"
                />
              </div>
              <DialogFooter className="gap-2 sm:gap-0">
                <button type="button" onClick={() => setInlineAmountEdit(null)} className="px-4 py-2 rounded-lg font-medium text-sm border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all">Cancel</button>
                <button type="submit" className="px-4 py-2 rounded-lg font-medium text-sm bg-blue-500 text-white hover:bg-blue-600 transition-all">Review Change</button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      {/* ---- Inline amount confirm ---- */}
      <AlertDialog open={!!pendingInlineConfirm} onOpenChange={(open) => !open && setPendingInlineConfirm(null)}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-gray-900">Confirm Amount Change</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-500">
              {pendingInlineConfirm && (
                <>
                  Update <span className="font-medium text-gray-700">{pendingInlineConfirm.kind === 'mis' ? 'MIS' : 'AFS'}</span> amount from{' '}
                  <span className="font-mono text-gray-700">{pendingInlineConfirm.oldDisplay}</span> to{' '}
                  <span className="font-mono text-gray-700">{pendingInlineConfirm.newDisplay}</span>?
                  <br />
                  <span className="text-xs mt-1 block">Reason: {pendingInlineConfirm.reason}</span>
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-gray-300 text-gray-700 hover:bg-gray-50">Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmInlineEdit()} className="bg-blue-500 text-white hover:bg-blue-600">Confirm</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* ---- Currency conversion confirm ---- */}
      <AlertDialog open={!!pendingConvert} onOpenChange={(open) => !open && setPendingConvert(null)}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-gray-900">Confirm currency conversion</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-500">
              {pendingConvert ? (
                pendingConvert.kind === 'mis' ? (
                  <>
                    Convert all company-level <span className="font-medium text-gray-700">MIS</span> amounts for this
                    review cycle from{' '}
                    <span className="font-mono">{pendingConvert.sourceCurrency ?? '—'}</span> to{' '}
                    <span className="font-mono">{pendingConvert.targetCurrency}</span> using live exchange rates? Stored
                    amounts in the database will be updated.
                  </>
                ) : pendingConvert.kind === 'afs' ? (
                  <>
                    Convert all <span className="font-medium text-gray-700">AFS</span> amounts for{' '}
                    <span className="font-medium text-gray-700">{pendingConvert.entityLabel}</span> from{' '}
                    <span className="font-mono">{pendingConvert.sourceCurrency ?? '—'}</span> to{' '}
                    <span className="font-mono">{pendingConvert.targetCurrency}</span> using live exchange rates? All
                    six metric rows for this entity will be updated.
                  </>
                ) : (
                  <>
                    Convert both <span className="font-medium text-gray-700">MIS</span> and{' '}
                    <span className="font-medium text-gray-700">AFS</span> amounts for{' '}
                    <span className="font-medium text-gray-700">{pendingConvert.entityLabel}</span> to{' '}
                    <span className="font-mono">{pendingConvert.targetCurrency}</span> using live exchange rates?
                    All stored amounts will be updated.
                  </>
                )
              ) : null}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-gray-300 text-gray-700 hover:bg-gray-50">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void confirmConvert();
              }}
              className="bg-blue-500 text-white hover:bg-blue-600"
            >
              Convert
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* ---- Enable/Disable confirm ---- */}
      <AlertDialog open={!!pendingToggle} onOpenChange={(open) => !open && setPendingToggle(null)}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-gray-900">Confirm Change</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-500">
              {pendingToggle?.newValue
                ? 'Enable this row? It will be included in the email draft. Note: enabling requires a variance breach.'
                : 'Disable this row? It will be excluded from the email draft.'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-gray-300 text-gray-700 hover:bg-gray-50">No</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmToggle()} className="bg-blue-500 text-white hover:bg-blue-600">Yes</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* ---- Text field edit dialog ---- */}
      <Dialog open={!!textFieldEdit} onOpenChange={(open) => !open && setTextFieldEdit(null)}>
        <DialogContent className="bg-white rounded-lg p-6 w-full max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold text-gray-900">{textFieldEdit?.label}</DialogTitle>
            <DialogDescription className="text-gray-500">
              Metric: <span className="font-medium text-gray-700">{textFieldEdit?.metricLabel}</span>
            </DialogDescription>
          </DialogHeader>
          {textFieldEdit && (
            <form className="mt-2 space-y-4" onSubmit={(e) => { e.preventDefault(); submitTextFieldEdit(); }}>
              <textarea
                value={textFieldEdit.draft}
                onChange={(e) => setTextFieldEdit((p) => p ? { ...p, draft: e.target.value } : p)}
                rows={6}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder={`Enter ${textFieldEdit.label.toLowerCase()}…`}
                autoFocus
              />
              <DialogFooter className="gap-2 sm:gap-0">
                <button type="button" onClick={() => setTextFieldEdit(null)} className="px-4 py-2 rounded-lg font-medium text-sm border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all">Cancel</button>
                <button type="submit" className="px-4 py-2 rounded-lg font-medium text-sm bg-blue-500 text-white hover:bg-blue-600 transition-all">Save</button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      {/* ---- Field edit confirm ---- */}
      <AlertDialog open={!!pendingFieldEdit} onOpenChange={(open) => !open && setPendingFieldEdit(null)}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-gray-900">Confirm Change — {pendingFieldEdit?.label}</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-500">
              {pendingFieldEdit && (
                <span className="space-y-1 block text-sm">
                  <span className="block">From: <span className="font-mono text-gray-700">{pendingFieldEdit.oldValue}</span></span>
                  <span className="block">To: <span className="font-mono text-gray-700">{pendingFieldEdit.newValue}</span></span>
                </span>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-gray-300 text-gray-700 hover:bg-gray-50">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => { pendingFieldEdit?.apply(); setPendingFieldEdit(null); }}
              className="bg-blue-500 text-white hover:bg-blue-600"
            >
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* ---- Delete confirm ---- */}
      <AlertDialog open={deletingManualId !== null} onOpenChange={(open) => !open && setDeletingManualId(null)}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-gray-900">Delete Query</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-500">Are you sure? This cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-gray-300 text-gray-700 hover:bg-gray-50">Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmDeleteManual()} className="bg-red-500 text-white hover:bg-red-600">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* ---- Change reconciliation source file confirm ---- */}
      <AlertDialog open={pendingFileChange !== null} onOpenChange={(open) => !open && setPendingFileChange(null)}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-gray-900">Change reconciliation source file?</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-500">
              The AFS values, discrepancies, breakdowns and the query-email figures for this entity
              will be recomputed from
              {pendingFileChange ? <span className="font-medium text-gray-700"> “{pendingFileChange.filename}”</span> : null}.
              Existing discrepancy decisions for this entity may change.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-gray-300 text-gray-700 hover:bg-gray-50">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (pendingFileChange) void handleSetPrimaryFile(pendingFileChange.entityId, pendingFileChange.fileId);
                setPendingFileChange(null);
              }}
              className="bg-blue-500 text-white hover:bg-blue-600"
            >
              Change file
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Shared PDF source viewer — opened from clickable paths in the AFS breakdown tooltip. */}
      <PdfSourceViewer
        open={pdfViewer.open}
        onClose={closePdfViewer}
        pdfUrl={pdfViewer.pdfUrl}
        fieldLabel={pdfViewer.fieldLabel}
        sourceRef={pdfViewer.sourceRef}
      />
    </div>
    </TooltipProvider>
  );
}
