import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';

import { Input } from '@/components/ui/input';
import { SettingsAuditLogPanel } from '@/components/settings/SettingsAuditLogPanel';
import { bulkPatchParameterThresholds, listParameterThresholds, type ApiParameterThreshold } from '@/api/settings';
import { listParameterThresholdAudit } from '@/api/audit';
import { useAsyncAction } from '@/hooks/useAsyncAction';
import { getFinancialParameterLabel, isConfigurableFinancialThresholdKey } from '@/constants/financialParameterLabels';

export default function ParameterThresholdPage() {
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<ApiParameterThreshold[]>([]);
  const [localPercent, setLocalPercent] = useState<Record<string, number>>({});
  const [localAbsolute, setLocalAbsolute] = useState<Record<string, number>>({});
  const [auditLogRefreshKey, setAuditLogRefreshKey] = useState(0);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const page = await listParameterThresholds({ limit: 500, offset: 0 });
        const items = (page.items ?? [])
          .filter((r) => isConfigurableFinancialThresholdKey(r.key))
          .slice()
          .sort((a, b) => a.key.localeCompare(b.key));
        setRows(items);

        const nextPercent: Record<string, number> = {};
        const nextAbs: Record<string, number> = {};
        for (const r of items) {
          const v = r.value || {};
          const pct =
            typeof v.percent_threshold === 'number'
              ? v.percent_threshold
              : typeof v.threshold_percent === 'number'
                ? v.threshold_percent / 100
                : 0.005;
          // Stored in full USD; display in Mn USD
          const absFullUsd = typeof v.absolute_threshold === 'number' ? v.absolute_threshold : 0;
          nextPercent[r.key] = pct * 100;
          nextAbs[r.key] = absFullUsd / 1_000_000;
        }
        setLocalPercent(nextPercent);
        setLocalAbsolute(nextAbs);
      } catch (e) {
        console.error(e);
        toast.error('Failed to load metric thresholds');
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  const handlePercentChange = (field: string, value: string) => {
    const v = parseFloat(value);
    if (!isNaN(v) && v >= 0 && v <= 100) setLocalPercent((prev) => ({ ...prev, [field]: v }));
  };

  const handleAbsoluteChange = (field: string, value: string) => {
    const v = parseFloat(value);
    if (!isNaN(v) && v >= 0) setLocalAbsolute((prev) => ({ ...prev, [field]: v }));
  };

  const fields = useMemo(() => Object.keys(localPercent).sort(), [localPercent]);

  const isDirty = useMemo(() => {
    for (const r of rows) {
      const v = r.value || {};
      const storedPct =
        typeof v.percent_threshold === 'number'
          ? v.percent_threshold
          : typeof v.threshold_percent === 'number'
            ? v.threshold_percent / 100
            : 0.005;
      // Stored in full USD; compare in Mn USD to match localAbsolute state
      const storedAbsMn = (typeof v.absolute_threshold === 'number' ? v.absolute_threshold : 0) / 1_000_000;

      const localPct = (localPercent[r.key] ?? storedPct * 100) / 100;
      const localAbsMn = localAbsolute[r.key] ?? storedAbsMn;

      if (Math.abs(localPct - storedPct) > 1e-9) return true;
      if (Math.abs(localAbsMn - storedAbsMn) > 1e-9) return true;
    }
    return false;
  }, [rows, localPercent, localAbsolute]);

  const { run: saveAll, loading: saving } = useAsyncAction(async () => {
    await bulkPatchParameterThresholds(
      rows.map((r) => ({
        id: r.id,
        value: {
          ...(r.value || {}),
          percent_threshold: (localPercent[r.key] ?? 0.5) / 100,
          // User enters in Mn USD; store in full USD for backend comparison
          absolute_threshold: (localAbsolute[r.key] ?? 0) * 1_000_000,
        },
      })),
    );
    // Keep local UI in sync so Apply disables again after successful save.
    const refreshed = await listParameterThresholds({ limit: 500, offset: 0 });
    const items = (refreshed.items ?? [])
      .filter((r) => isConfigurableFinancialThresholdKey(r.key))
      .slice()
      .sort((a, b) => a.key.localeCompare(b.key));
    setRows(items);
    const nextPercent: Record<string, number> = {};
    const nextAbs: Record<string, number> = {};
    for (const r of items) {
      const v = r.value || {};
      const pct =
        typeof v.percent_threshold === 'number'
          ? v.percent_threshold
          : typeof v.threshold_percent === 'number'
            ? v.threshold_percent / 100
            : 0.005;
      // Stored in full USD; display in Mn USD
      const absFullUsd = typeof v.absolute_threshold === 'number' ? v.absolute_threshold : 0;
      nextPercent[r.key] = pct * 100;
      nextAbs[r.key] = absFullUsd / 1_000_000;
    }
    setLocalPercent(nextPercent);
    setLocalAbsolute(nextAbs);
  });

  const handleApply = async () => {
    try {
      await saveAll();
      setAuditLogRefreshKey((k) => k + 1);
      toast.success('Thresholds saved');
    } catch (e) {
      console.error(e);
      toast.error('Failed to save thresholds');
    }
  };

  const handleReset = () => {
    setLocalPercent(Object.fromEntries(Object.keys(localPercent).map((k) => [k, 0.5])));
    setLocalAbsolute(Object.fromEntries(Object.keys(localAbsolute).map((k) => [k, 0])));
    toast.success('Defaults set (percent 0.50%, absolute 0)');
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h2 className="text-lg font-bold text-foreground flex items-center gap-2">Metric Thresholds</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Set variance thresholds for each metric. A discrepancy is flagged if <strong>either</strong> the percentage or absolute threshold is breached.
        </p>
      </div>

      <div className="bg-background border border-border rounded-lg shadow-sm overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border bg-muted/50">
              <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Metric</th>
              <th className="text-right px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider w-40">% Threshold</th>
              <th className="text-right px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider w-44">Absolute Threshold (Mn USD)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {loading ? (
              <tr>
                <td colSpan={3} className="px-4 py-8 text-center text-sm text-muted-foreground">
                  Loading thresholds…
                </td>
              </tr>
            ) : fields.length === 0 ? (
              <tr>
                <td colSpan={3} className="px-4 py-8 text-center text-sm text-muted-foreground">
                  No metric thresholds found in the database.
                </td>
              </tr>
            ) : (
              fields.map((field) => (
              <tr key={field} className="hover:bg-muted/30 transition-colors">
                <td className="px-4 py-3 text-sm text-foreground">
                  <div className="font-medium">{getFinancialParameterLabel(field)}</div>
                </td>
                <td className="px-4 py-3">
                  <Input
                    type="number"
                    value={localPercent[field]?.toFixed(2) ?? '0.50'}
                    onChange={(e) => handlePercentChange(field, e.target.value)}
                    min={0}
                    max={100}
                    step={0.01}
                    className="text-sm text-right w-28 ml-auto"
                  />
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-1 justify-end">
                    <Input
                      type="number"
                      value={localAbsolute[field] ?? 0}
                      onChange={(e) => handleAbsoluteChange(field, e.target.value)}
                      min={0}
                      step={0.1}
                      placeholder="0 (disabled)"
                      className="text-sm text-right w-28 font-mono"
                    />
                    <span className="text-xs text-muted-foreground whitespace-nowrap">Mn USD</span>
                  </div>
                </td>
              </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between">
        <button
          onClick={handleReset}
          className="px-4 py-2 text-xs font-medium rounded-lg border border-border text-muted-foreground hover:bg-muted/50 transition-colors"
        >
          Reset Defaults
        </button>
        <button
          onClick={handleApply}
          disabled={!isDirty || saving}
          className="px-4 py-2 text-xs font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {saving ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Saving…
            </>
          ) : (
            'Apply Thresholds'
          )}
        </button>
      </div>

      <div className="bg-background border border-border rounded-lg p-5 shadow-sm">
        <h3 className="text-sm font-medium text-foreground mb-2">How it works</h3>
        <ul className="text-xs text-muted-foreground space-y-1.5 list-disc pl-4">
          <li>
            Each metric has both a <strong>percentage</strong> and an <strong>absolute value</strong> threshold
          </li>
          <li>
            Percentage variance: <code className="bg-muted px-1 rounded">(As per AFS − As per MIS) / As per MIS</code>
          </li>
          <li>
            Absolute variance: <code className="bg-muted px-1 rounded">|As per AFS − As per MIS|</code> (in full currency units)
          </li>
          <li>
            Absolute threshold is entered in <strong>Mn USD</strong> and stored as full USD internally.
            When amounts are in INR, the threshold is converted at the live USD→INR exchange rate before comparing.
          </li>
          <li>
            An item is flagged if <strong>either</strong> threshold is breached
          </li>
          <li>
            Set absolute threshold to <strong>0</strong> to disable it (percentage-only mode)
          </li>
        </ul>
      </div>

      <SettingsAuditLogPanel
        title="Metric threshold logs"
        fetchLogs={listParameterThresholdAudit}
        refreshKey={auditLogRefreshKey}
      />
    </div>
  );
}

