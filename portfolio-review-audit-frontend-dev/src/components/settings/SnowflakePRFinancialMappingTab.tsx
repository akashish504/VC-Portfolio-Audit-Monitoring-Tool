import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { Loader2 } from 'lucide-react';

import { SettingsAuditLogPanel } from '@/components/settings/SettingsAuditLogPanel';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  listSnowflakePRNumericColumns,
  getSnowflakePRFinancialMapping,
  putSnowflakePRFinancialMapping,
  type ApiSnowflakePRFinancialMapping,
  type ApiSnowflakePRPnlPack,
  type ApiSnowflakePRBalancePack,
  type SnowflakePRStageGroupId,
} from '@/api/settings';
import { listSnowflakePRFinancialMappingAudit } from '@/api/audit';

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

const PNL_METRICS: MetricKey[] = ['revenue', 'ebitda', 'pbt', 'pat'];
const BALANCE_METRICS: MetricKey[] = ['cash', 'debt'];

const STAGE_GROUP_KEYS: SnowflakePRStageGroupId[] = ['surge_seed', 'growth_venture'];
const STAGE_GROUP_LABELS: Record<SnowflakePRStageGroupId, string> = {
  surge_seed: 'Surge / Seed',
  growth_venture: 'Growth / Venture',
};

type SlotKey = 'pnl' | 'pnl_aligned' | 'pnl_lagged' | 'balance';

const SLOT_METRICS: Record<SlotKey, MetricKey[]> = {
  pnl: PNL_METRICS,
  pnl_aligned: PNL_METRICS,
  pnl_lagged: PNL_METRICS,
  balance: BALANCE_METRICS,
};

interface SlotDef {
  key: SlotKey;
  label: string;
  hint: string;
}

const GROUP_SLOTS: Record<SnowflakePRStageGroupId, SlotDef[]> = {
  surge_seed: [
    { key: 'pnl', label: 'P&L — annual (always Year 1)', hint: 'Surge/Seed always reads the Year 1 column.' },
    { key: 'balance', label: 'Balance sheet — matched to the exact audited quarter', hint: 'Point-in-time columns (cash_on_hand / debt_total).' },
  ],
  growth_venture: [
    { key: 'pnl_aligned', label: 'P&L — quarter aligned (Year 2)', hint: 'Used when the MIS submission quarter equals the audited FY-end quarter.' },
    { key: 'pnl_lagged', label: 'P&L — quarter lagged (Year 1)', hint: 'Used when the freshest MIS submission is a later quarter.' },
    { key: 'balance', label: 'Balance sheet — matched to the exact audited quarter', hint: 'Point-in-time columns (cash_on_hand / debt_total).' },
  ],
};

type Sign = '+' | '-';
interface Term {
  column: string;
  sign: Sign;
}
type SlotTerms = Record<string, Term[]>; // metric -> terms

interface LocalState {
  surge_seed: Record<string, SlotTerms>; // 'pnl' | 'balance'
  growth_venture: Record<string, SlotTerms>; // 'pnl_aligned' | 'pnl_lagged' | 'balance'
}

// --- formula string <-> term-row conversion ---------------------------------
function formulaToTerms(formula: string): Term[] {
  const s = (formula || '').trim();
  if (!s) return [];
  const terms: Term[] = [];
  const idRe = /[A-Za-z_][A-Za-z0-9_]*/y;
  let i = 0;
  let sign: Sign = '+';
  while (i < s.length) {
    const ch = s[i];
    if (ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r') { i++; continue; }
    if (ch === '+') { sign = '+'; i++; continue; }
    if (ch === '-') { sign = '-'; i++; continue; }
    idRe.lastIndex = i;
    const m = idRe.exec(s);
    if (m && m.index === i) {
      terms.push({ column: m[0], sign });
      i = idRe.lastIndex;
      sign = '+';
    } else {
      return [{ column: s, sign: '+' }];
    }
  }
  return terms;
}

function termsToFormula(terms: Term[]): string {
  const clean = (terms || []).filter((t) => t.column && t.column.trim());
  const parts: string[] = [];
  clean.forEach((t, idx) => {
    const col = t.column.trim();
    if (idx === 0) parts.push(t.sign === '-' ? `-${col}` : col);
    else parts.push(t.sign === '-' ? '-' : '+', col);
  });
  return parts.join(' ');
}

function packToTerms(pack: Record<string, { formula?: unknown }> | undefined, metrics: MetricKey[]): SlotTerms {
  const out: SlotTerms = {};
  for (const m of metrics) {
    const raw = pack?.[m];
    const formula = raw && typeof raw.formula === 'string' ? raw.formula : '';
    out[m] = formulaToTerms(formula);
  }
  return out;
}

function normalizeMapping(m: ApiSnowflakePRFinancialMapping): LocalState {
  const sg = m.stage_groups || ({} as ApiSnowflakePRFinancialMapping['stage_groups']);
  const ss = sg.surge_seed || ({} as ApiSnowflakePRFinancialMapping['stage_groups']['surge_seed']);
  const gv = sg.growth_venture || ({} as ApiSnowflakePRFinancialMapping['stage_groups']['growth_venture']);
  return {
    surge_seed: {
      pnl: packToTerms(ss.pnl as Record<string, { formula?: unknown }> | undefined, PNL_METRICS),
      balance: packToTerms(ss.balance as Record<string, { formula?: unknown }> | undefined, BALANCE_METRICS),
    },
    growth_venture: {
      pnl_aligned: packToTerms(gv.pnl_aligned as Record<string, { formula?: unknown }> | undefined, PNL_METRICS),
      pnl_lagged: packToTerms(gv.pnl_lagged as Record<string, { formula?: unknown }> | undefined, PNL_METRICS),
      balance: packToTerms(gv.balance as Record<string, { formula?: unknown }> | undefined, BALANCE_METRICS),
    },
  };
}

function pnlPack(st: SlotTerms): ApiSnowflakePRPnlPack {
  return {
    revenue: { formula: termsToFormula(st.revenue || []) },
    ebitda: { formula: termsToFormula(st.ebitda || []) },
    pbt: { formula: termsToFormula(st.pbt || []) },
    pat: { formula: termsToFormula(st.pat || []) },
  };
}

function balancePack(st: SlotTerms): ApiSnowflakePRBalancePack {
  return {
    cash: { formula: termsToFormula(st.cash || []) },
    debt: { formula: termsToFormula(st.debt || []) },
  };
}

export default function SnowflakePRFinancialMappingTab() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [baseline, setBaseline] = useState<LocalState | null>(null);
  const [local, setLocal] = useState<LocalState | null>(null);

  const [activeGroup, setActiveGroup] = useState<SnowflakePRStageGroupId>('surge_seed');

  const [columnQuery, setColumnQuery] = useState('');
  const [columns, setColumns] = useState<string[]>([]);
  const [auditLogRefreshKey, setAuditLogRefreshKey] = useState(0);

  useEffect(() => {
    const loadCols = async () => {
      try {
        const list = await listSnowflakePRNumericColumns(columnQuery.trim() || undefined);
        setColumns(list);
      } catch (e) {
        console.error(e);
        toast.error('Failed to load PR numeric columns');
      }
    };
    const t = window.setTimeout(() => void loadCols(), columnQuery.trim() ? 280 : 0);
    return () => window.clearTimeout(t);
  }, [columnQuery]);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const data = await getSnowflakePRFinancialMapping();
        const state = normalizeMapping(data);
        setBaseline(state);
        setLocal(state);
      } catch (e) {
        console.error(e);
        toast.error('Failed to load Snowflake PR formulas');
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  const isDirty = useMemo(() => {
    if (!baseline || !local) return false;
    return JSON.stringify(baseline) !== JSON.stringify(local);
  }, [baseline, local]);

  const updateSlot = (
    group: SnowflakePRStageGroupId,
    slot: SlotKey,
    metric: MetricKey,
    updater: (rows: Term[]) => Term[],
  ) => {
    setLocal((prev) => {
      if (!prev) return prev;
      const grp = prev[group];
      const slotTerms = grp[slot] || {};
      const next: LocalState = {
        ...prev,
        [group]: {
          ...grp,
          [slot]: { ...slotTerms, [metric]: updater([...(slotTerms[metric] || [])]) },
        },
      } as LocalState;
      return next;
    });
  };

  const handleAddRow = (slot: SlotKey, metric: MetricKey) => {
    const firstPick = columns[0];
    if (!firstPick) {
      toast.error('No columns loaded — clear the filter or type a narrower search so columns appear, then try again.');
      return;
    }
    updateSlot(activeGroup, slot, metric, (rows) => [...rows, { column: firstPick, sign: '+' }]);
  };

  const handleRemoveRow = (slot: SlotKey, metric: MetricKey, index: number) => {
    updateSlot(activeGroup, slot, metric, (rows) => rows.filter((_, i) => i !== index));
  };

  const handleColumnChange = (slot: SlotKey, metric: MetricKey, index: number, column: string) => {
    updateSlot(activeGroup, slot, metric, (rows) => { rows[index] = { ...rows[index], column }; return rows; });
  };

  const handleSignChange = (slot: SlotKey, metric: MetricKey, index: number, sign: Sign) => {
    updateSlot(activeGroup, slot, metric, (rows) => { rows[index] = { ...rows[index], sign }; return rows; });
  };

  const handleReset = () => {
    if (!baseline) return;
    setLocal(JSON.parse(JSON.stringify(baseline)) as LocalState);
  };

  const handleSave = async () => {
    if (!local) return;
    setSaving(true);
    try {
      const body: ApiSnowflakePRFinancialMapping = {
        stage_groups: {
          surge_seed: {
            pnl: pnlPack(local.surge_seed.pnl),
            balance: balancePack(local.surge_seed.balance),
          },
          growth_venture: {
            pnl_aligned: pnlPack(local.growth_venture.pnl_aligned),
            pnl_lagged: pnlPack(local.growth_venture.pnl_lagged),
            balance: balancePack(local.growth_venture.balance),
          },
        },
      };
      await putSnowflakePRFinancialMapping(body);
      toast.success('Snowflake PR formulas saved');
      const refreshed = await getSnowflakePRFinancialMapping();
      const state = normalizeMapping(refreshed);
      setBaseline(state);
      setLocal(state);
      setAuditLogRefreshKey((x) => x + 1);
    } catch (e: unknown) {
      console.error(e);
      const detail =
        typeof e === 'object' && e && 'response' in e &&
        typeof (e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail === 'string'
          ? String((e as { response?: { data?: { detail?: string } } }).response?.data?.detail)
          : 'Failed to save';
      toast.error(detail);
    } finally {
      setSaving(false);
    }
  };

  // --- one metric's term rows ---
  const renderMetricRows = (slot: SlotKey, metric: MetricKey, rows: Term[]) => (
    <div key={`${slot}-${metric}`} className="p-4">
      <div className="flex items-center justify-between gap-4 mb-2">
        <div>
          <h3 className="text-sm font-semibold">{METRIC_LABELS[metric]}</h3>
          <p className="text-[11px] text-muted-foreground font-mono">
            = {termsToFormula(rows) || '—'}
          </p>
        </div>
        <button
          type="button"
          onClick={() => handleAddRow(slot, metric)}
          className="text-xs font-medium px-3 py-1.5 rounded border border-border hover:bg-muted/60"
        >
          Add term
        </button>
      </div>

      {!rows?.length ? (
        <p className="text-xs text-muted-foreground italic">Empty — this metric is not synced (no value written).</p>
      ) : (
        <div className="space-y-2">
          {rows.map((term, idx) => {
            const optionSet = new Set(columns);
            if (term.column) optionSet.add(term.column);
            const sortedOptions = Array.from(optionSet).sort((a, b) => a.localeCompare(b));
            return (
              <div key={`${slot}-${metric}-${idx}`} className="flex flex-wrap items-center gap-2">
                <select
                  className="h-9 text-xs rounded-md border border-input bg-background px-2 min-w-[280px] max-w-full flex-1"
                  value={term.column || ''}
                  onChange={(e) => handleColumnChange(slot, metric, idx, e.target.value)}
                  aria-label={`Column row ${idx + 1} for ${metric}`}
                >
                  {sortedOptions.length === 0 ? <option value="">— widen column filter above —</option> : null}
                  {sortedOptions.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>

                <select
                  className="h-9 text-xs rounded-md border border-input bg-background px-2"
                  value={term.sign === '-' ? '-' : '+'}
                  onChange={(e) => handleSignChange(slot, metric, idx, e.target.value === '-' ? '-' : '+')}
                  aria-label={`Sign row ${idx + 1} for ${metric}`}
                >
                  <option value="+">+ add</option>
                  <option value="-">− subtract</option>
                </select>

                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8 text-[11px]"
                  onClick={() => handleRemoveRow(slot, metric, idx)}
                >
                  Remove
                </Button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  // --- one slot section (a group of metric editors) ---
  const renderSlot = (group: SnowflakePRStageGroupId, slotDef: SlotDef, state: LocalState) => {
    const slotTerms = state[group][slotDef.key] || {};
    return (
      <div key={slotDef.key} className="border-t border-border">
        <div className="px-4 pt-4">
          <h3 className="text-sm font-semibold">{slotDef.label}</h3>
          <p className="text-xs text-muted-foreground mt-0.5">{slotDef.hint}</p>
        </div>
        <div className="divide-y divide-border">
          {SLOT_METRICS[slotDef.key].map((metric) =>
            renderMetricRows(slotDef.key, metric, slotTerms[metric] || []),
          )}
        </div>
      </div>
    );
  };

  if (loading || !local) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground py-16 justify-center">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading Snowflake PR formulas…
      </div>
    );
  }

  // Resolved formula for the effective-config table (reflects unsaved edits).
  const fx = (group: SnowflakePRStageGroupId, slot: SlotKey, metric: MetricKey): string => {
    const f = termsToFormula((local[group][slot] || {})[metric] || []);
    return f || '—';
  };

  const isBalance = (m: MetricKey) => BALANCE_METRICS.includes(m);
  const metricTypeLabel = (m: MetricKey) => (isBalance(m) ? 'Balance · exact quarter' : 'P&L');
  const metricVariants = (m: MetricKey): { label: string; formula: string }[] =>
    isBalance(m)
      ? [
          { label: 'Surge / Seed', formula: fx('surge_seed', 'balance', m) },
          { label: 'Growth / Venture', formula: fx('growth_venture', 'balance', m) },
        ]
      : [
          { label: 'Surge / Seed', formula: fx('surge_seed', 'pnl', m) },
          { label: 'G/V · aligned (Yr 2)', formula: fx('growth_venture', 'pnl_aligned', m) },
          { label: 'G/V · lagged (Yr 1)', formula: fx('growth_venture', 'pnl_lagged', m) },
        ];

  return (
    <div className="space-y-8 max-w-5xl">
      {/* ---------- Effective config (read-only, both groups at a glance) ---------- */}
      <div className="bg-background border border-border rounded-lg shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h2 className="text-base font-semibold text-foreground">Effective configuration</h2>
          <p className="text-xs text-muted-foreground mt-1">
            The exact column each metric reads, per stage group. The system picks Year 2 vs Year 1
            automatically (see the matching rules below) — you only define what each metric <em>is</em>.
          </p>
        </div>
        <div className="divide-y divide-border">
          {METRIC_KEYS.map((m) => (
            <div key={m} className="px-4 py-3">
              <div className="flex items-baseline gap-2 mb-1.5">
                <h3 className="text-sm font-semibold">{METRIC_LABELS[m]}</h3>
                <span className="text-[11px] text-muted-foreground">{metricTypeLabel(m)}</span>
              </div>
              <dl className="space-y-1">
                {metricVariants(m).map((v) => (
                  <div
                    key={v.label}
                    className="grid grid-cols-1 sm:grid-cols-[170px_minmax(0,1fr)] gap-x-3 gap-y-0.5 items-start"
                  >
                    <dt className="text-xs text-muted-foreground">{v.label}</dt>
                    <dd
                      className={`text-xs font-mono break-words min-w-0 ${
                        v.formula === '—' ? 'text-muted-foreground' : 'text-foreground'
                      }`}
                    >
                      {v.formula}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
      </div>

      {/* ---------- Editor ---------- */}
      <div className="bg-background border border-border rounded-lg shadow-sm overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h2 className="text-base font-semibold text-foreground">Edit formulas</h2>
          <p className="text-xs text-muted-foreground mt-1">
            Each metric sums terms as <strong>Σ (sign × column)</strong> over numeric columns from{' '}
            <code className="text-[11px]">pr_submission_data_raw</code>. P&amp;L metrics use annual{' '}
            <code className="text-[11px]">*_yr_1</code>/<code className="text-[11px]">*_yr_2</code> columns;
            cash &amp; debt use point-in-time balances (<code className="text-[11px]">cash_on_hand</code>,{' '}
            <code className="text-[11px]">debt_total</code>).
          </p>

          <div className="mt-3 max-w-xl">
            <Label className="text-xs font-medium block mb-1.5 text-muted-foreground">Filter column names</Label>
            <Input
              value={columnQuery}
              onChange={(e) => setColumnQuery(e.target.value)}
              placeholder="Substring filter (narrow pick-lists)"
              className="h-9 text-sm"
            />
          </div>

          <div className="mt-3">
            <Tabs value={activeGroup} onValueChange={(v) => setActiveGroup(v as SnowflakePRStageGroupId)}>
              <TabsList>
                {STAGE_GROUP_KEYS.map((g) => (
                  <TabsTrigger key={g} value={g}>
                    {STAGE_GROUP_LABELS[g]}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          </div>
        </div>

        {GROUP_SLOTS[activeGroup].map((slotDef) => renderSlot(activeGroup, slotDef, local))}
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
            'Save formulas'
          )}
        </Button>
      </div>

      {/* ---------- How matching works (read-only reference) ---------- */}
      <div className="bg-background border border-border rounded-lg p-5 shadow-sm space-y-4">
        <div>
          <h3 className="text-sm font-medium text-foreground mb-1">How matching works</h3>
          <p className="text-xs text-muted-foreground">
            For each company, the system finds the most relevant MIS submission for its audited
            financial year-end, then decides the year column. You configure only the formulas above.
          </p>
        </div>

        <div>
          <p className="text-xs font-medium text-foreground mb-1.5">
            P&amp;L year column — Growth / Venture (Surge/Seed always Year 1)
          </p>
          <div className="overflow-x-auto">
            <table className="text-xs border border-border">
              <thead className="bg-muted/50 text-muted-foreground">
                <tr>
                  <th className="text-left font-medium px-3 py-1.5 border-r border-border">Audited period</th>
                  <th className="px-3 py-1.5">MIS Mar</th>
                  <th className="px-3 py-1.5">MIS Jun</th>
                  <th className="px-3 py-1.5">MIS Sep</th>
                  <th className="px-3 py-1.5">MIS Dec</th>
                </tr>
              </thead>
              <tbody className="text-center">
                {[
                  ['Jan – Mar', 'Yr 2', 'Yr 1', 'Yr 1', 'Yr 1'],
                  ['Apr – Jun', '—', 'Yr 2', 'Yr 1', 'Yr 1'],
                  ['Jul – Sep', '—', '—', 'Yr 2', 'Yr 1'],
                  ['Oct – Dec', '—', '—', '—', 'Yr 2'],
                ].map((row) => (
                  <tr key={row[0]} className="border-t border-border">
                    <td className="text-left px-3 py-1.5 border-r border-border">{row[0]}</td>
                    {row.slice(1).map((cell, i) => (
                      <td key={i} className={cell === 'Yr 2' ? 'px-3 py-1.5 font-semibold text-foreground' : 'px-3 py-1.5 text-muted-foreground'}>
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <ul className="text-xs text-muted-foreground space-y-1.5 list-disc pl-4">
          <li>
            <strong>Aligned quarter → Year 2</strong> (the MIS submission quarter equals the audited
            FY-end quarter); any <strong>later</strong> quarter in the window → <strong>Year 1</strong>.
          </li>
          <li><strong>Surge / Seed</strong> always uses Year 1.</li>
          <li>
            <strong>Cash &amp; Debt</strong> are balances — they match the MIS submission on the
            <strong> exact audited quarter-end date</strong>. If there is none, cash/debt are left
            unset (not reconciled) rather than compared across dates.
          </li>
          <li>Only <strong>Surge/Seed</strong> and <strong>Growth/Venture</strong> companies are synced; other stages are skipped.</li>
          <li>Saving re-runs the PR sync in the background so dashboard figures pick up the new formulas immediately.</li>
        </ul>
      </div>

      <SettingsAuditLogPanel
        title="Snowflake PR formula logs"
        fetchLogs={listSnowflakePRFinancialMappingAudit}
        searchable
        refreshKey={auditLogRefreshKey}
      />
    </div>
  );
}
