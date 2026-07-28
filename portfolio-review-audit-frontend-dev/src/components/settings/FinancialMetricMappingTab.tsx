import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';

import { SettingsAuditLogPanel } from '@/components/settings/SettingsAuditLogPanel';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  listFinancialMetricSchemaPaths,
  getFinancialMetricMapping,
  putFinancialMetricMapping,
  type ApiFinancialMetricTerm,
  type ApiFinancialMetricMapping,
} from '@/api/settings';
import { listFinancialExtractionMappingAudit } from '@/api/audit';

const METRIC_KEYS = ['revenue', 'ebitda', 'pbt', 'pat', 'cash', 'debt'] as const;

type MetricKey = (typeof METRIC_KEYS)[number];

const METRIC_LABELS: Record<MetricKey, string> = {
  revenue: 'Revenue',
  ebitda: 'EBITDA',
  pbt: 'PBT',
  pat: 'PAT',
  cash: 'Cash',
  debt: 'Debt',
};

function normalizeMapping(m: ApiFinancialMetricMapping): Record<MetricKey, ApiFinancialMetricTerm[]> {
  const src = m.metrics;
  const out = {} as Record<MetricKey, ApiFinancialMetricTerm[]>;
  for (const key of METRIC_KEYS) {
    const raw = src[key as string];
    out[key] = Array.isArray(raw)
      ? (raw as ApiFinancialMetricTerm[])
          .filter((x) => x && typeof x.path === 'string' && typeof x.sign === 'string')
          .map((x) => ({ ...x, abs: x.abs ?? false }))
      : [];
  }
  return out;
}

export default function FinancialMetricMappingTab() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [baseline, setBaseline] = useState<Record<MetricKey, ApiFinancialMetricTerm[]> | null>(null);
  const [local, setLocal] = useState<Record<MetricKey, ApiFinancialMetricTerm[]> | null>(null);

  const [pathQuery, setPathQuery] = useState('');
  const [paths, setPaths] = useState<string[]>([]);

  const [auditLogRefreshKey, setAuditLogRefreshKey] = useState(0);

  useEffect(() => {
    const loadPaths = async () => {
      try {
        const list = await listFinancialMetricSchemaPaths(pathQuery.trim() || undefined);
        setPaths(list);
      } catch (e) {
        console.error(e);
        toast.error('Failed to load schema paths');
      }
    };
    const t = window.setTimeout(() => void loadPaths(), pathQuery.trim() ? 280 : 0);
    return () => window.clearTimeout(t);
  }, [pathQuery]);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const data = await getFinancialMetricMapping();
        const normalized = normalizeMapping(data);
        setBaseline(normalized);
        setLocal(normalized);
      } catch (e) {
        console.error(e);
        toast.error('Failed to load financial metric mapping');
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  const isDirty = useMemo(() => {
    if (!baseline || !local) return false;
    return METRIC_KEYS.some((k) => JSON.stringify(baseline[k]) !== JSON.stringify(local[k]));
  }, [baseline, local]);

  const updateMetric = (metric: MetricKey, updater: (rows: ApiFinancialMetricTerm[]) => ApiFinancialMetricTerm[]) => {
    setLocal((prev) => {
      if (!prev) return prev;
      return { ...prev, [metric]: updater([...prev[metric]]) };
    });
  };

  const handleAddRow = (metric: MetricKey) => {
    const firstPick = paths[0];
    if (!firstPick) {
      toast.error(
        'No schema paths loaded — clear the filter or type a narrower search so paths appear, then try again.',
      );
      return;
    }
    updateMetric(metric, (rows) => [...rows, { path: firstPick, sign: '+', abs: false }]);
  };

  const handleRemoveRow = (metric: MetricKey, index: number) => {
    updateMetric(metric, (rows) => rows.filter((_, i) => i !== index));
  };

  const handlePathChange = (metric: MetricKey, index: number, path: string) => {
    updateMetric(metric, (rows) => {
      rows[index] = { ...rows[index], path };
      return rows;
    });
  };

  const handleSignChange = (metric: MetricKey, index: number, sign: '+' | '-') => {
    updateMetric(metric, (rows) => {
      rows[index] = { ...rows[index], sign };
      return rows;
    });
  };

  const handleAbsChange = (metric: MetricKey, index: number, useAbs: boolean) => {
    updateMetric(metric, (rows) => {
      rows[index] = { ...rows[index], abs: useAbs };
      return rows;
    });
  };

  const handleReset = () => {
    if (!baseline) return;
    setLocal(
      METRIC_KEYS.reduce(
        (acc, k) => {
          acc[k] = [...baseline[k]];
          return acc;
        },
        {} as Record<MetricKey, ApiFinancialMetricTerm[]>,
      ),
    );
  };

  const handleSave = async () => {
    if (!local) return;
    setSaving(true);
    try {
      const body: ApiFinancialMetricMapping = { metrics: { ...local } };
      await putFinancialMetricMapping(body);
      toast.success(
        'Mapping saved. Existing files are being re-evaluated in the background — refresh the Discrepancy Dashboard in a few seconds to see updated breakdowns.',
        { duration: 6000 },
      );
      // Broadcast a signal so any open Discrepancy Dashboard tab can auto-refresh
      // once the background reapply has had time to complete on the backend.
      try {
        const stamp = Date.now().toString();
        localStorage.setItem('financial_metric_mapping_saved_at', stamp);
        window.dispatchEvent(
          new CustomEvent('financial-metric-mapping-saved', { detail: { savedAt: stamp } }),
        );
      } catch {
        // localStorage / CustomEvent may be unavailable in some sandboxed environments — non-fatal.
      }
      const refreshed = await getFinancialMetricMapping();
      const normalized = normalizeMapping(refreshed);
      setBaseline(normalized);
      setLocal(normalized);
      setAuditLogRefreshKey((x) => x + 1);
    } catch (e: unknown) {
      console.error(e);
      const detail =
        typeof e === 'object' && e && 'response' in e && typeof (e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail === 'string'
          ? String((e as { response?: { data?: { detail?: string } } }).response?.data?.detail)
          : 'Failed to save';
      toast.error(detail);
    } finally {
      setSaving(false);
    }
  };

  if (loading || !local) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground py-16 justify-center">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading extraction mapping…
      </div>
    );
  }

  return (
    <div className="space-y-8 max-w-5xl">
      <div className="bg-background border border-border rounded-lg shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h2 className="text-base font-semibold text-foreground">Financial extraction mapping</h2>
          <p className="text-xs text-muted-foreground mt-1">
            Each metric sums terms as <strong>Σ (sign × value at dotted path)</strong>. Paths come from the stored audit-financials
            schema. Cash and debt sync only when the balance sheet subtree has numeric amounts.
          </p>
          <div className="mt-3 max-w-xl">
            <Label className="text-xs font-medium block mb-1.5 text-muted-foreground">Filter schema paths</Label>
            <Input
              value={pathQuery}
              onChange={(e) => setPathQuery(e.target.value)}
              placeholder="Substring filter (narrow pick-lists)"
              className="h-9 text-sm"
            />
          </div>
        </div>
        <div className="divide-y divide-border">
          {METRIC_KEYS.map((metric) => (
            <div key={metric} className="p-4">
              <div className="flex items-center justify-between gap-4 mb-2">
                <h3 className="text-sm font-semibold">{METRIC_LABELS[metric]}</h3>
                <button
                  type="button"
                  onClick={() => handleAddRow(metric)}
                  className="text-xs font-medium px-3 py-1.5 rounded border border-border hover:bg-muted/60"
                >
                  Add term
                </button>
              </div>

              {!local[metric]?.length ? (
                <p className="text-xs text-muted-foreground italic">
                  Empty — OCR sync skips this metric (no overwrite from extraction).
                </p>
              ) : (
                <div className="space-y-2">
                  {local[metric].map((term, idx) => {
                    const optionSet = new Set(paths);
                    if (term.path) optionSet.add(term.path);
                    const sortedOptions = Array.from(optionSet).sort((a, b) => a.localeCompare(b));
                    return (
                      <div key={`${metric}-${idx}`} className="flex flex-wrap items-center gap-2">
                        <select
                          className="h-9 text-xs rounded-md border border-input bg-background px-2 min-w-[280px] max-w-full flex-1"
                          value={term.path || ''}
                          onChange={(e) => handlePathChange(metric, idx, e.target.value)}
                          aria-label={`Path row ${idx + 1} for ${metric}`}
                        >
                          {sortedOptions.length === 0 ? (
                            <option value="">— widen path filter above —</option>
                          ) : null}
                          {sortedOptions.map((p) => (
                            <option key={p} value={p}>
                              {p}
                            </option>
                          ))}
                        </select>

                        <select
                          className="h-9 text-xs rounded-md border border-input bg-background px-2"
                          value={term.sign === '-' ? '-' : '+'}
                          onChange={(e) => handleSignChange(metric, idx, e.target.value === '-' ? '-' : '+')}
                          aria-label={`Sign row ${idx + 1} for ${metric}`}
                        >
                          <option value="+">+ add</option>
                          <option value="-">− subtract</option>
                        </select>

                        <label className="flex items-center gap-1.5 text-xs text-muted-foreground select-none cursor-pointer">
                          <input
                            type="checkbox"
                            checked={term.abs ?? false}
                            onChange={(e) => handleAbsChange(metric, idx, e.target.checked)}
                            aria-label={`Use absolute value row ${idx + 1} for ${metric}`}
                            className="h-3.5 w-3.5 accent-primary"
                          />
                          abs
                        </label>

                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-8 text-[11px]"
                          onClick={() => handleRemoveRow(metric, idx)}
                        >
                          Remove
                        </Button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="flex items-center justify-between">
        <Button type="button" variant="outline" size="sm" onClick={handleReset} disabled={!baseline || saving}>
          Reset unsaved edits
        </Button>
        <Button type="button" size="sm" onClick={handleSave} disabled={!isDirty || saving}>
          {saving ? (
            <>
              <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
              Saving…
            </>
          ) : (
            'Save mapping'
          )}
        </Button>
      </div>

      <div className="bg-background border border-border rounded-lg p-5 shadow-sm">
        <h3 className="text-sm font-medium text-foreground mb-2">How it works</h3>
        <ul className="text-xs text-muted-foreground space-y-1.5 list-disc pl-4">
          <li>Configured paths must match numeric leaves in your stored audit-financials schema.</li>
          <li>For each OCR sync, subtree values are summed the same way as in the reviewer UI.</li>
          <li>Use + and − rows to approximate net totals (borrowings − cash, etc.) when helpful.</li>
          <li>Check <strong>abs</strong> on any term to use the absolute value of that field before applying its sign (useful when a field may be stored as negative in the source data).</li>
          <li>
            Saving this mapping triggers an automatic background re-evaluation of every completed
            audit-financials file, so existing reconciliation rows pick up the new formulas without
            re-running OCR. The Discrepancy Dashboard auto-refreshes a few seconds after a save.
          </li>
        </ul>
      </div>

      <SettingsAuditLogPanel title="Financial extraction mapping logs" fetchLogs={listFinancialExtractionMappingAudit} searchable refreshKey={auditLogRefreshKey} />
    </div>
  );
}
