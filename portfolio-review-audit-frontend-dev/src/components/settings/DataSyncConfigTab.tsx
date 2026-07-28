import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
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
import { getDataSyncConfig, putDataSyncConfig, type ApiDataSyncConfig } from '@/api/settings';

type Frequency = 'daily' | 'weekly';

const FREQUENCY_OPTIONS: { value: Frequency; label: string; description: string }[] = [
  {
    value: 'weekly',
    label: 'Weekly',
    description: 'Runs every Sunday night at 9 PM IST',
  },
  {
    value: 'daily',
    label: 'Daily',
    description: 'Runs every night at 9 PM IST',
  },
];

interface FormState {
  frequency: Frequency;
  cutoffDate: string; // YYYY-MM-DD or ''
}

function stateFromConfig(cfg: ApiDataSyncConfig): FormState {
  return {
    frequency: cfg.frequency === 'daily' ? 'daily' : 'weekly',
    cutoffDate: cfg.cutoff_date ?? '',
  };
}

function statesEqual(a: FormState, b: FormState): boolean {
  return a.frequency === b.frequency && a.cutoffDate === b.cutoffDate;
}

export default function DataSyncConfigTab() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [baseline, setBaseline] = useState<FormState | null>(null);
  const [form, setForm] = useState<FormState>({ frequency: 'weekly', cutoffDate: '' });
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getDataSyncConfig()
      .then((cfg: ApiDataSyncConfig) => {
        if (cancelled) return;
        const s = stateFromConfig(cfg);
        setBaseline(s);
        setForm(s);
      })
      .catch(() => {
        if (cancelled) return;
        toast.error('Failed to load data sync configuration.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const isDirty = baseline !== null && !statesEqual(form, baseline);

  async function handleConfirmSave() {
    setSaving(true);
    try {
      const result = await putDataSyncConfig({
        frequency: form.frequency,
        cutoff_date: form.cutoffDate || null,
      });
      const s = stateFromConfig(result);
      setBaseline(s);
      setForm(s);
      toast.success('Data sync configuration saved successfully.');
    } catch {
      toast.error('Failed to save data sync configuration. Please try again.');
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-8 text-gray-500 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading configuration…
      </div>
    );
  }

  return (
    <div className="max-w-lg">
      <h2 className="text-base font-semibold text-gray-900 mb-1">Automatic Data Sync</h2>
      <p className="text-sm text-gray-500 mb-6">
        Configure how often the system automatically pulls the latest data. The manual Data Sync button
        is unaffected by this setting.
      </p>

      <fieldset className="mb-8">
        <legend className="text-sm font-medium text-gray-700 mb-3">Cadence</legend>
        <div className="space-y-3">
          {FREQUENCY_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className={`flex items-start gap-3 rounded-lg border p-4 cursor-pointer transition-colors ${
                form.frequency === opt.value
                  ? 'border-blue-500 bg-blue-50'
                  : 'border-gray-200 hover:border-gray-300'
              }`}
            >
              <input
                type="radio"
                name="frequency"
                value={opt.value}
                checked={form.frequency === opt.value}
                onChange={() => setForm((f) => ({ ...f, frequency: opt.value }))}
                className="mt-0.5 accent-blue-600"
              />
              <span>
                <span className="block text-sm font-medium text-gray-900">{opt.label}</span>
                <span className="block text-xs text-gray-500 mt-0.5">{opt.description}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="mb-8">
        <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="cutoff-date">
          Data Cutoff Date
        </label>
        <p className="text-xs text-gray-500 mb-2">
          Only records with a <code className="font-mono">REPORTING_DATE</code> later than this date
          will be fetched from Snowflake. Leave blank to fetch all records.
        </p>
        <p className="text-xs mb-3">
          <span className="text-gray-500">Currently active: </span>
          {baseline?.cutoffDate ? (
            <span className="font-medium text-gray-800">{baseline.cutoffDate}</span>
          ) : (
            <span className="text-gray-400 italic">no filter — all records fetched</span>
          )}
        </p>
        <div className="flex items-center gap-3">
          <input
            id="cutoff-date"
            type="date"
            value={form.cutoffDate}
            onChange={(e) => setForm((f) => ({ ...f, cutoffDate: e.target.value }))}
            className="block rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          {form.cutoffDate && (
            <button
              type="button"
              onClick={() => setForm((f) => ({ ...f, cutoffDate: '' }))}
              className="text-xs text-gray-400 hover:text-gray-600 underline"
            >
              Clear
            </button>
          )}
        </div>
      </div>

      <Button
        disabled={!isDirty || saving}
        onClick={() => setConfirmOpen(true)}
        className="w-28"
      >
        {saving ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
        Save
      </Button>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm configuration change</AlertDialogTitle>
            <AlertDialogDescription>
              This will update the <strong>automatic data sync configuration</strong>:
              <ul className="mt-2 list-disc pl-5 space-y-1">
                <li>
                  Cadence:{' '}
                  <strong>
                    {form.frequency === 'daily'
                      ? 'daily (every night at 9 PM IST)'
                      : 'weekly (every Sunday night at 9 PM IST)'}
                  </strong>
                </li>
                <li>
                  Data cutoff:{' '}
                  <strong>{form.cutoffDate ? `after ${form.cutoffDate}` : 'no filter (all records)'}</strong>
                </li>
              </ul>
              <span className="block mt-2">
                The change takes effect at the next scheduled run. The manual Data Sync button is not
                affected.
              </span>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={saving}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={saving}
              onClick={(e) => {
                e.preventDefault();
                setConfirmOpen(false);
                handleConfirmSave();
              }}
            >
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
