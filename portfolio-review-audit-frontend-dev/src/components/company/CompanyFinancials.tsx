import { useEffect, useMemo, useState } from 'react';
import { Pencil } from 'lucide-react';
import { toast } from 'sonner';

import {
  convertFinancialDataCurrency,
  convertFinancialDataSnowflakeCurrency,
  createFinancialData,
  listEntities,
  listFinancialData,
  listFinancialDataSnowflake,
  patchFinancialData,
  patchFinancialDataSnowflake,
  type ApiFinancialDataRow,
  type ApiFinancialDataSnowflakeRow,
  type FinancialMetricKey,
} from '@/api/portfolio';
import { fetchFxRates, type FxRatesResponse } from '@/api/fx';
import { FinancialParameterLabel } from '@/constants/financialParameterLabels';
import { formatAbsoluteVarianceAmount, formatFinancialAmount } from '@/constants/financialDisplayFormat';
import { useAppState } from '@/context/AppContext';
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
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

/** Rows with no entity are grouped under this key (company-level / unassigned metrics). */
type EntityGroupKey = number | 'company';

function rowEntityGroupKey(r: { entity_id?: number | null | string }): EntityGroupKey {
  const raw = r.entity_id;
  if (raw == null || raw === '') return 'company';
  const n = typeof raw === 'number' ? raw : Number(raw);
  return Number.isFinite(n) ? n : 'company';
}

function coalesceMetric(v: unknown): number | null {
  if (v == null) return null;
  if (typeof v === 'number' && !Number.isNaN(v)) return v;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v);
    return Number.isNaN(n) ? null : n;
  }
  return null;
}

/** Matches backend ``generate_discrepancies_for_company`` variance rules for a numeric metric key. */
function varianceAnalysis(
  source: number | null,
  extracted: number | null,
  metricKey: string,
  fieldThresholds: Record<string, number>,
  absoluteThresholds: Record<string, number>,
): { pct: number; flagged: boolean; pctThresholdRatio: number } | null {
  if (source == null || extracted == null) return null;
  const absDiff = Math.abs(extracted - source);
  const pct = source === 0 ? 0 : absDiff / Math.abs(source);
  const pctThresh = fieldThresholds[metricKey] ?? 0.005;
  const absThresh = absoluteThresholds[metricKey];
  let flagged = pct > pctThresh;
  if (absThresh != null && absThresh > 0) flagged = flagged || absDiff > absThresh;
  return { pct, flagged, pctThresholdRatio: pctThresh };
}

function formatThresholdPercent(thresholdRatio: number): string {
  const pct = thresholdRatio * 100;
  if (!Number.isFinite(pct)) return '—';
  const s = pct.toFixed(2).replace(/\.?0+$/, '');
  return s;
}

function normalizeCurrencyCode(code: string | null | undefined): string | null {
  const c = code?.trim().toUpperCase();
  return c && /^[A-Z]{3}$/.test(c) ? c : null;
}

function CurrencyBadgeButton({
  code,
  onClick,
  disabled,
}: {
  code: string | null | undefined;
  onClick: () => void;
  disabled?: boolean;
}) {
  const normalized = normalizeCurrencyCode(code);
  if (!normalized) {
    return <span className="text-xs text-gray-400">—</span>;
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title="Change currency"
      className="inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-0.5 text-xs font-semibold font-mono tracking-wide text-foreground hover:bg-muted hover:border-blue-300 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
    >
      {normalized}
    </button>
  );
}

export function CompanyFinancials({ companyId, selectedEntityId }: { companyId: number; selectedEntityId?: string }) {
  const { fieldThresholds, absoluteThresholds } = useAppState();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [entities, setEntities] = useState<Array<{ id: number; name: string; review_cycle?: string | null }>>([]);
  const [reported, setReported] = useState<ApiFinancialDataSnowflakeRow[]>([]);
  const [extracted, setExtracted] = useState<ApiFinancialDataRow[]>([]);

  const [editOpen, setEditOpen] = useState(false);
  const [editCtx, setEditCtx] = useState<{
    entityId: number;
    entityName: string;
    metric: { key: FinancialMetricKey; label: string; kind: 'currency' | 'percent' | 'number' };
    column: 'reported' | 'extracted';
    current: number | null;
    repRowId: number | null;
    extRowId: number | null;
    /** Entity review cycle — required when creating a new extracted row. */
    reviewCycle: string | null;
  } | null>(null);
  const [editValue, setEditValue] = useState('');
  const [editReason, setEditReason] = useState('');

  const [currencyOpen, setCurrencyOpen] = useState(false);
  const [currencyConfirmOpen, setCurrencyConfirmOpen] = useState(false);
  const [currencyCtx, setCurrencyCtx] = useState<{
    column: 'reported' | 'extracted';
    rowId: number;
    entityName: string;
    currentCurrency: string;
  } | null>(null);
  const [fxRates, setFxRates] = useState<FxRatesResponse | null>(null);
  const [fxLoading, setFxLoading] = useState(false);
  const [targetCurrency, setTargetCurrency] = useState('');
  const [converting, setConverting] = useState(false);

  const conversionRate = useMemo(() => {
    if (!fxRates || !targetCurrency) return null;
    const fromMid = fxRates.from.mid || 1;
    const to = fxRates.to.find((t) => t.quotecurrency.toUpperCase() === targetCurrency.toUpperCase());
    if (!to) return null;
    return to.mid / fromMid;
  }, [fxRates, targetCurrency]);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const [ents, rep, ext] = await Promise.all([
          listEntities({ portfolio_company_id: companyId, limit: 500, offset: 0 }),
          listFinancialDataSnowflake({ portfolio_company_id: companyId, limit: 500, offset: 0 }),
          listFinancialData({ portfolio_company_id: companyId, limit: 500, offset: 0 }),
        ]);
        setEntities((ents.items ?? []).map((e) => ({ id: e.id, name: e.name, review_cycle: e.review_cycle ?? null })));
        setReported(rep.items ?? []);
        setExtracted(ext.items ?? []);
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [companyId]);

  const reload = async () => {
    const [ents, rep, ext] = await Promise.all([
      listEntities({ portfolio_company_id: companyId, limit: 500, offset: 0 }),
      listFinancialDataSnowflake({ portfolio_company_id: companyId, limit: 500, offset: 0 }),
      listFinancialData({ portfolio_company_id: companyId, limit: 500, offset: 0 }),
    ]);
    setEntities((ents.items ?? []).map((e) => ({ id: e.id, name: e.name, review_cycle: e.review_cycle ?? null })));
    setReported(rep.items ?? []);
    setExtracted(ext.items ?? []);
  };

  const metricRows = useMemo(() => {
    const rows: Array<{ key: FinancialMetricKey; label: string; kind: 'currency' | 'percent' | 'number' }> = [
      { key: 'revenue', label: FinancialParameterLabel.revenue, kind: 'currency' },
      { key: 'ebitda', label: FinancialParameterLabel.ebitda, kind: 'currency' },
      { key: 'pbt', label: FinancialParameterLabel.pbt, kind: 'currency' },
      { key: 'pat', label: FinancialParameterLabel.pat, kind: 'currency' },
      { key: 'cash', label: FinancialParameterLabel.cash, kind: 'currency' },
      { key: 'debt', label: FinancialParameterLabel.debt, kind: 'currency' },
    ];
    return rows;
  }, []);

  const entityMetaById = useMemo(
    () => new Map(entities.map((e) => [e.id, e] as const)),
    [entities],
  );

  const entityNameById = useMemo(() => new Map(entities.map((e) => [e.id, e.name] as const)), [entities]);

  const groupOrder = useMemo(() => {
    // Any row with a non-null entity_id gets its own group; null/ missing entity_id rolls up to `'company'`.
    const keys = new Set<EntityGroupKey>();
    for (const r of reported) keys.add(rowEntityGroupKey(r));
    for (const r of extracted) keys.add(rowEntityGroupKey(r));
    const list = Array.from(keys).sort((a, b) => {
      if (a === 'company' && b !== 'company') return 1;
      if (b === 'company' && a !== 'company') return -1;
      if (a === 'company' && b === 'company') return 0;
      const an = entityNameById.get(a as number) ?? String(a);
      const bn = entityNameById.get(b as number) ?? String(b);
      return an.localeCompare(bn);
    });
    if (!selectedEntityId) return list;
    const only = Number(selectedEntityId);
    if (!Number.isFinite(only)) return list;
    return list.filter((x) => x !== 'company' && x === only);
  }, [reported, extracted, entityNameById, selectedEntityId]);

  const pickLatest = <T extends { id: number; period_end?: string | null; review_cycle?: string | null }>(items: T[]) => {
    return items
      .slice()
      .sort((a, b) => {
        const rc = (b.review_cycle ?? '').localeCompare(a.review_cycle ?? '');
        if (rc !== 0) return rc;
        const pe = (b.period_end ?? '').localeCompare(a.period_end ?? '');
        if (pe !== 0) return pe;
        return b.id - a.id;
      })[0];
  };

  const formatCell = (
    v: number | null,
    kind: 'currency' | 'percent' | 'number',
    currencyCode?: string | null,
  ) => {
    if (v == null) return <span className="text-xs text-gray-400">NA</span>;
    if (kind === 'percent') return <span className="font-mono">{(v * 100).toFixed(2)}%</span>;
    const formatted =
      kind === 'currency'
        ? formatFinancialAmount(normalizeCurrencyCode(currencyCode) ?? undefined, v)
        : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return <span className="font-mono">{formatted}</span>;
  };

  const openCurrencyConvert = async (args: {
    column: 'reported' | 'extracted';
    rowId: number;
    entityName: string;
    currentCurrency: string | null | undefined;
  }) => {
    const normalized = normalizeCurrencyCode(args.currentCurrency);
    if (!normalized) {
      toast.error('Set a currency before converting');
      return;
    }
    setCurrencyCtx({
      column: args.column,
      rowId: args.rowId,
      entityName: args.entityName,
      currentCurrency: normalized,
    });
    setCurrencyOpen(true);
    setFxLoading(true);
    setTargetCurrency('');
    setFxRates(null);
    try {
      const rates = await fetchFxRates(normalized);
      setFxRates(rates);
      const first = rates.to.find((t) => t.quotecurrency.toUpperCase() !== normalized);
      if (first) setTargetCurrency(first.quotecurrency);
    } catch {
      toast.error('Failed to load exchange rates');
      setCurrencyOpen(false);
      setCurrencyCtx(null);
    } finally {
      setFxLoading(false);
    }
  };

  const runCurrencyConversion = async () => {
    if (!currencyCtx || !targetCurrency || conversionRate == null) return;
    setConverting(true);
    try {
      if (currencyCtx.column === 'reported') {
        await convertFinancialDataSnowflakeCurrency(currencyCtx.rowId, targetCurrency);
      } else {
        await convertFinancialDataCurrency(currencyCtx.rowId, targetCurrency);
      }
      await reload();
      toast.success(
        `${currencyCtx.column === 'reported' ? 'As per MIS' : 'As per AFS'} currency converted to ${targetCurrency.toUpperCase()}`,
      );
      setCurrencyConfirmOpen(false);
      setCurrencyOpen(false);
      setCurrencyCtx(null);
    } catch (e) {
      let msg = 'Currency conversion failed';
      if (e && typeof e === 'object' && 'response' in e) {
        const d = (e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
        if (typeof d === 'string') msg = d;
      }
      toast.error(msg);
    } finally {
      setConverting(false);
    }
  };

  const openEdit = (args: NonNullable<typeof editCtx>) => {
    setEditCtx(args);
    setEditValue(args.current == null ? '' : String(args.current));
    setEditReason('');
    setEditOpen(true);
  };

  const saveEdit = async () => {
    if (!editCtx) return;
    const reasonTrimmed = editReason.trim();
    if (!reasonTrimmed) {
      toast.error('Explain why you are changing this number (required for audit logs)');
      return;
    }
    const raw = editValue.trim();
    const parsed = raw === '' ? null : Number(raw);
    if (raw !== '' && Number.isNaN(parsed)) {
      toast.error('Enter a valid number');
      return;
    }
    setSaving(true);
    try {
      if (editCtx.column === 'reported') {
        if (!editCtx.repRowId) {
          toast.error('As per MIS row missing (seed/sync issue)');
          return;
        }
        await patchFinancialDataSnowflake(editCtx.repRowId, {
          [editCtx.metric.key]: parsed,
          edit_reason: reasonTrimmed,
        });
      } else {
        if (editCtx.extRowId) {
          await patchFinancialData(editCtx.extRowId, {
            [editCtx.metric.key]: parsed,
            edit_reason: reasonTrimmed,
          });
        } else {
          if (!editCtx.reviewCycle?.trim()) {
            toast.error('Set a review cycle on the entity before creating as per AFS financial data.');
            return;
          }
          // Create extracted row if audit file not received yet (was NA).
          const repForEntity = reported.find((r) => rowEntityGroupKey(r) === editCtx.entityId);
          const extForEntity = extracted.find((r) => rowEntityGroupKey(r) === editCtx.entityId);
          const currencyForCreate =
            normalizeCurrencyCode(extForEntity?.currency) ??
            normalizeCurrencyCode(repForEntity?.currency) ??
            null;
          await createFinancialData({
            portfolio_company_id: companyId,
            entity_id: editCtx.entityId,
            review_cycle: editCtx.reviewCycle.trim(),
            frequency: 'manual',
            ...(currencyForCreate ? { currency: currencyForCreate } : {}),
            [editCtx.metric.key]: parsed,
            extra_data: { source: 'manual', mapping: 'placeholder' },
          } as any);
        }
      }
      await reload();
      toast.success('Saved');
      setEditOpen(false);
    } catch (e) {
      let msg = 'Save failed';
      if (e && typeof e === 'object' && 'response' in e) {
        const d = (e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
        if (typeof d === 'string') msg = d;
        else if (Array.isArray(d) && d[0]?.msg) msg = String(d[0].msg);
      } else if (e instanceof Error) msg = e.message;
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6 space-y-6">
      {loading ? (
        <div className="text-sm text-muted-foreground">Loading financials…</div>
      ) : groupOrder.length === 0 ? (
        <div className="text-sm text-muted-foreground">No financial data available.</div>
      ) : (
        groupOrder.map((entityKey) => {
          const entityName =
            entityKey === 'company'
              ? 'Company — unassigned entity'
              : entityNameById.get(entityKey) ?? `Entity ${entityKey}`;
          const reviewCycle =
            entityKey === 'company' ? null : entityMetaById.get(entityKey)?.review_cycle?.trim() || null;
          const repLatest = pickLatest(reported.filter((r) => rowEntityGroupKey(r) === entityKey));
          const extLatest = pickLatest(extracted.filter((r) => rowEntityGroupKey(r) === entityKey));
          const repCurrency = normalizeCurrencyCode(repLatest?.currency);
          const extCurrency = normalizeCurrencyCode(extLatest?.currency);
          const currenciesMismatch =
            repCurrency != null && extCurrency != null && repCurrency !== extCurrency;

          return (
            <div key={entityKey === 'company' ? 'company' : entityKey}>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
                <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{entityName}</h3>
                {(repCurrency || extCurrency) && (
                  <span className="text-[11px] text-muted-foreground">
                    {repCurrency ? (
                      <>
                        As per MIS: <span className="font-mono font-medium text-foreground">{repCurrency}</span>
                      </>
                    ) : null}
                    {repCurrency && extCurrency ? ' · ' : null}
                    {extCurrency ? (
                      <>
                        As per AFS: <span className="font-mono font-medium text-foreground">{extCurrency}</span>
                      </>
                    ) : null}
                    {currenciesMismatch ? (
                      <span className="text-amber-700 font-medium"> (currency mismatch)</span>
                    ) : null}
                  </span>
                )}
              </div>
              <div className="overflow-x-auto bg-background rounded-lg border border-border shadow-sm">
                <table className="w-full">
                  <thead>
                    <tr className="bg-muted/50">
                      <th className="text-xs font-medium text-muted-foreground uppercase tracking-wider text-left px-4 py-3">Metric</th>
                      <th className="text-xs font-medium text-muted-foreground uppercase tracking-wider text-right px-4 py-3 w-56">
                        <div>As per MIS</div>
                        <div className="mt-0.5 font-normal normal-case tracking-normal flex justify-end">
                          <CurrencyBadgeButton
                            code={repLatest?.currency}
                            disabled={!repLatest || entityKey === 'company'}
                            onClick={() =>
                              repLatest &&
                              void openCurrencyConvert({
                                column: 'reported',
                                rowId: repLatest.id,
                                entityName,
                                currentCurrency: repLatest.currency,
                              })
                            }
                          />
                        </div>
                      </th>
                      <th className="text-xs font-medium text-muted-foreground uppercase tracking-wider text-right px-4 py-3 w-56">
                        <div>As per AFS</div>
                        <div className="mt-0.5 font-normal normal-case tracking-normal flex justify-end">
                          <CurrencyBadgeButton
                            code={extLatest?.currency}
                            disabled={!extLatest || entityKey === 'company'}
                            onClick={() =>
                              extLatest &&
                              void openCurrencyConvert({
                                column: 'extracted',
                                rowId: extLatest.id,
                                entityName,
                                currentCurrency: extLatest.currency,
                              })
                            }
                          />
                        </div>
                      </th>
                      <th className="text-xs font-medium text-muted-foreground uppercase tracking-wider text-right px-4 py-3 w-44">
                        |Δ| amount
                      </th>
                      <th className="text-xs font-medium text-muted-foreground uppercase tracking-wider text-right px-4 py-3 w-40">Variance</th>
                      <th className="text-xs font-medium text-muted-foreground uppercase tracking-wider text-center px-4 py-3 w-44">
                        vs threshold
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {metricRows.map((m) => {
                      const srcVal = coalesceMetric(repLatest?.[m.key]);
                      const extVal = coalesceMetric(extLatest?.[m.key]);
                      const absAmount = srcVal != null && extVal != null ? Math.abs(extVal - srcVal) : null;
                      const currencyMismatchDiff =
                        repCurrency != null && extCurrency != null && repCurrency !== extCurrency;
                      const va = varianceAnalysis(srcVal, extVal, m.key, fieldThresholds, absoluteThresholds);
                      const tagLabel = va ? formatThresholdPercent(va.pctThresholdRatio) : '';
                      const tag = !va
                        ? ({ kind: 'nc' } as const)
                        : va.flagged
                          ? ({ kind: 'outside', thresholdLabel: tagLabel } as const)
                          : ({ kind: 'inside', thresholdLabel: tagLabel } as const);
                      return (
                        <tr key={m.key} className={va?.flagged ? 'bg-destructive/10' : 'hover:bg-muted/30'}>
                          <td className="px-4 py-3 text-sm text-foreground">{m.label}</td>
                          <td className="px-4 py-3 text-sm text-right text-muted-foreground">
                            <div className="inline-flex items-center justify-end gap-2 group">
                              {formatCell(srcVal, m.kind, repLatest?.currency)}
                              {repLatest && (
                                <button
                                  onClick={() =>
                                    entityKey !== 'company' &&
                                    openEdit({
                                      entityId: entityKey as number,
                                      entityName,
                                      metric: m,
                                      column: 'reported',
                                      current: srcVal,
                                      repRowId: repLatest.id,
                                      extRowId: extLatest?.id ?? null,
                                      reviewCycle,
                                    })
                                  }
                                  className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-muted"
                                  title="Edit as per MIS"
                                >
                                  <Pencil className="h-3.5 w-3.5" />
                                </button>
                              )}
                            </div>
                          </td>
                          <td className="px-4 py-3 text-sm text-right text-muted-foreground">
                            <div className="inline-flex items-center justify-end gap-2 group">
                              {formatCell(extVal, m.kind, extLatest?.currency)}
                              <button
                                onClick={() =>
                                  entityKey !== 'company' &&
                                  openEdit({
                                    entityId: entityKey as number,
                                    entityName,
                                    metric: m,
                                    column: 'extracted',
                                    current: extVal,
                                    repRowId: repLatest?.id ?? null,
                                    extRowId: extLatest?.id ?? null,
                                    reviewCycle,
                                  })
                                }
                                className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-muted"
                                title="Edit as per AFS"
                              >
                                <Pencil className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          </td>
                          <td
                            className={`px-4 py-3 text-sm font-mono text-right ${
                              currencyMismatchDiff && absAmount != null ? 'text-amber-800' : 'text-muted-foreground'
                            }`}
                            title={
                              currencyMismatchDiff && absAmount != null
                                ? 'MIS and AFS currencies differ — |Δ| is the raw numeric gap (figures may not be comparable).'
                                : undefined
                            }
                          >
                            {absAmount == null
                              ? '—'
                              : formatAbsoluteVarianceAmount(absAmount, {
                                  misCurrency: repLatest?.currency,
                                  afsCurrency: extLatest?.currency,
                                })}
                          </td>
                          <td
                            className={`px-4 py-3 text-sm font-mono text-right ${va?.flagged ? 'text-destructive font-semibold' : 'text-muted-foreground'}`}
                          >
                            {va ? `${(va.pct * 100).toFixed(2)}%` : '—'}
                          </td>
                          <td className="px-4 py-3 text-center">
                            {tag.kind === 'nc' ? (
                              <span className="inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium bg-muted text-muted-foreground">
                                Not Comparable
                              </span>
                            ) : tag.kind === 'outside' ? (
                              <span
                                className="inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium bg-destructive/15 text-destructive border border-destructive/25"
                                title="Variance exceeds metric threshold (percent and/or absolute)"
                              >
                                &gt; +/-{tag.thresholdLabel}%
                              </span>
                            ) : (
                              <span
                                className="inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium bg-emerald-500/10 text-emerald-800 border border-emerald-500/20"
                                title="Within configured metric threshold"
                              >
                                &lt; +/-{tag.thresholdLabel}%
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })
      )}

      <Dialog
        open={editOpen}
        onOpenChange={(open) => {
          setEditOpen(open);
          if (!open) setEditReason('');
        }}
      >
        <DialogContent className="bg-white sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-gray-900">Edit value</DialogTitle>
            <DialogDescription className="text-gray-500">
              {editCtx ? (
                <>
                  <span className="font-medium text-gray-900">{editCtx.entityName}</span> —{' '}
                  <span className="font-medium text-gray-900">{editCtx.metric.label}</span> (
                  {editCtx.column === 'reported' ? 'As per MIS' : 'As per AFS'})
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Amount</Label>
              <Input
                type="number"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                placeholder="Raw stored amount (same units as database)"
                className="font-mono"
              />
              <p className="text-[11px] text-muted-foreground">
                Table cells use INR Cr / USD millions for display; enter values in stored currency units from your source system.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Reason for change (required)</Label>
              <textarea
                value={editReason}
                onChange={(e) => setEditReason(e.target.value)}
                placeholder="Briefly document why this value is being updated."
                rows={3}
                className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              />
            </div>
            <div className="text-[11px] text-gray-500">
              Saved to the database and recorded on the company audit log with your comment.
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={saveEdit} disabled={saving || !editCtx || !editReason.trim()}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={currencyOpen}
        onOpenChange={(open) => {
          if (!open && !converting) {
            setCurrencyOpen(false);
            setCurrencyCtx(null);
          }
        }}
      >
        <DialogContent className="bg-white sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="text-gray-900">Change currency</DialogTitle>
            <DialogDescription className="text-gray-500">
              {currencyCtx ? (
                <>
                  <span className="font-medium text-gray-900">{currencyCtx.entityName}</span> —{' '}
                  {currencyCtx.column === 'reported' ? 'As per MIS' : 'As per AFS'}
                </>
              ) : null}
            </DialogDescription>
          </DialogHeader>
          {fxLoading ? (
            <p className="text-sm text-muted-foreground py-4">Loading exchange rates…</p>
          ) : currencyCtx && fxRates ? (
            <div className="space-y-4">
              <div className="text-sm text-gray-700">
                Current currency:{' '}
                <span className="font-mono font-semibold">{currencyCtx.currentCurrency}</span>
              </div>
              <div>
                <label className="text-xs uppercase tracking-wider text-gray-500">Convert to</label>
                <select
                  value={targetCurrency}
                  onChange={(e) => setTargetCurrency(e.target.value)}
                  className="mt-1 w-full text-sm border border-gray-300 rounded-lg px-3 py-2 bg-white text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  {fxRates.to
                    .filter((t) => t.quotecurrency.toUpperCase() !== currencyCtx.currentCurrency)
                    .map((t) => (
                      <option key={t.quotecurrency} value={t.quotecurrency}>
                        {t.quotecurrency}
                      </option>
                    ))}
                </select>
              </div>
              {conversionRate != null && targetCurrency ? (
                <p className="text-sm text-gray-600 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
                  Rate: 1 {currencyCtx.currentCurrency} = {conversionRate.toFixed(6)} {targetCurrency.toUpperCase()}
                  {fxRates.timestamp ? (
                    <span className="block text-xs text-gray-400 mt-1">Quote: {new Date(fxRates.timestamp).toLocaleString()}</span>
                  ) : null}
                </p>
              ) : null}
              <p className="text-xs text-gray-500">
                All non-null metrics for this entity will be converted and saved (four decimal places in the DB). The
                other basis column is not affected.
              </p>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setCurrencyOpen(false)} disabled={converting}>
              Cancel
            </Button>
            <Button
              onClick={() => setCurrencyConfirmOpen(true)}
              disabled={!currencyCtx || !targetCurrency || conversionRate == null || fxLoading}
            >
              Continue
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={currencyConfirmOpen} onOpenChange={setCurrencyConfirmOpen}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-gray-900">Confirm currency conversion</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-500">
              {currencyCtx && targetCurrency && conversionRate != null ? (
                <>
                  Convert all <span className="font-medium text-gray-700">as per {currencyCtx.column === 'reported' ? 'MIS' : 'AFS'}</span> monetary
                  values for <span className="font-medium text-gray-700">{currencyCtx.entityName}</span> from{' '}
                  <span className="font-mono">{currencyCtx.currentCurrency}</span> to{' '}
                  <span className="font-mono">{targetCurrency.toUpperCase()}</span> using rate{' '}
                  <span className="font-mono">{conversionRate.toFixed(6)}</span>? This updates stored amounts in the
                  database.
                </>
              ) : null}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={converting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void runCurrencyConversion();
              }}
              disabled={converting}
              className="bg-blue-500 text-white hover:bg-blue-600"
            >
              {converting ? 'Converting…' : 'Convert'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

