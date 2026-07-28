import { useEffect, useState } from 'react';
import { CheckCircle2, Loader2, Mail } from 'lucide-react';
import { toast } from 'sonner';
import apiClient from '@/api/axios';
import { listAllEmailTemplateVersions, type ApiEmailTemplate } from '@/api/emailTemplates';
import type { ApiConfigRow, Page } from '@/api/reviewCycleAdjustments';

const CONFIG_KEYS = {
  discrepancy: 'email_templates.discrepancy',
  reminder_1: 'email_templates.reminder_1',
  reminder_2: 'email_templates.reminder_2',
} as const;

type RoleKey = keyof typeof CONFIG_KEYS;

const ROLE_LABELS: Record<RoleKey, string> = {
  discrepancy: 'Discrepancy Email',
  reminder_1: 'Reminder 1',
  reminder_2: 'Reminder 2',
};

const ROLE_DESCRIPTIONS: Record<RoleKey, string> = {
  discrepancy: 'Sent when a financial discrepancy is first identified and flagged.',
  reminder_1: 'First follow-up reminder sent if no response is received.',
  reminder_2: 'Second follow-up reminder sent after Reminder 1 goes unanswered.',
};

interface ConfigState {
  id: number | null;
  templateId: string;
}

export default function EmailTemplateAssignmentTab() {
  const [templates, setTemplates] = useState<ApiEmailTemplate[]>([]);
  const [configs, setConfigs] = useState<Record<RoleKey, ConfigState>>({
    discrepancy: { id: null, templateId: '' },
    reminder_1: { id: null, templateId: '' },
    reminder_2: { id: null, templateId: '' },
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<RoleKey | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [tmplRows, configPage] = await Promise.all([
        listAllEmailTemplateVersions(),
        apiClient.get<Page<ApiConfigRow>>('/api/v1/config', { params: { limit: 500, offset: 0 } }),
      ]);
      setTemplates(tmplRows);

      const rows: ApiConfigRow[] = configPage.data.items ?? [];
      const next = { ...configs };
      for (const role of Object.keys(CONFIG_KEYS) as RoleKey[]) {
        const row = rows.find((r) => r.key === CONFIG_KEYS[role]);
        const stored = row?.value;
        const templateId =
          typeof stored === 'object' && stored !== null
            ? String((stored as Record<string, unknown>).value ?? (stored as Record<string, unknown>).template_id ?? (stored as Record<string, unknown>).id ?? '')
            : typeof stored === 'string'
            ? stored
            : '';
        next[role] = { id: row?.id ?? null, templateId };
      }
      setConfigs(next);
    } catch {
      toast.error('Failed to load email template configuration');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSave = async (role: RoleKey, templateId: string) => {
    setSaving(role);
    try {
      const payload = { value: templateId };
      const existing = configs[role];
      if (existing.id != null) {
        await apiClient.patch(`/api/v1/config/${existing.id}`, { value: payload });
      } else {
        const res = await apiClient.post<ApiConfigRow>('/api/v1/config', {
          key: CONFIG_KEYS[role],
          value: payload,
          description: `Template ID for ${ROLE_LABELS[role]}`,
        });
        setConfigs((prev) => ({ ...prev, [role]: { id: res.data.id, templateId } }));
      }
      setConfigs((prev) => ({ ...prev, [role]: { ...prev[role], templateId } }));
      toast.success(`${ROLE_LABELS[role]} template saved`);
    } catch {
      toast.error(`Failed to save ${ROLE_LABELS[role]} template`);
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-blue-500" />
      </div>
    );
  }

  // Group templates by template_name for display
  const byName: Record<string, ApiEmailTemplate[]> = {};
  for (const t of templates) {
    const name = t.template_name || t.id;
    if (!byName[name]) byName[name] = [];
    byName[name].push(t);
  }

  return (
    <div className="max-w-3xl space-y-6">
      <div className="bg-blue-50 border border-blue-100 rounded-lg px-4 py-3 flex items-start gap-3">
        <Mail className="h-4 w-4 text-blue-500 shrink-0 mt-0.5" />
        <p className="text-sm text-blue-800">
          Assign which email template is used for each notification type. Templates are managed in the
          Email Templates section.
        </p>
      </div>

      {(Object.keys(CONFIG_KEYS) as RoleKey[]).map((role) => {
        const current = configs[role].templateId;
        const isSaving = saving === role;

        return (
          <div key={role} className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <h3 className="text-sm font-semibold text-gray-900">{ROLE_LABELS[role]}</h3>
                <p className="text-xs text-gray-500 mt-0.5">{ROLE_DESCRIPTIONS[role]}</p>
              </div>
              {current && (
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-green-700 bg-green-50 border border-green-200 rounded-full px-2 py-0.5 shrink-0">
                  <CheckCircle2 className="h-3 w-3" /> Configured
                </span>
              )}
            </div>

            <div className="space-y-3">
              <select
                value={current}
                onChange={(e) => {
                  const val = e.target.value;
                  setConfigs((prev) => ({ ...prev, [role]: { ...prev[role], templateId: val } }));
                }}
                className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
              >
                <option value="">— Select a template —</option>
                {Object.entries(byName).map(([name, versions]) => (
                  <optgroup key={name} label={name}>
                    {versions.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.version_name || t.version_id || t.id}
                        {t.is_active ? ' ✓ active' : ''}
                        {t.subject ? ` — ${t.subject}` : ''}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>

              <div className="flex items-center justify-between gap-3">
                {current ? (
                  <p className="text-[11px] text-gray-400 font-mono truncate">ID: {current}</p>
                ) : (
                  <p className="text-[11px] text-amber-600">No template assigned — sending this type will fail.</p>
                )}
                <button
                  disabled={isSaving || !current}
                  onClick={() => void handleSave(role, current)}
                  className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-semibold bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-all shrink-0"
                >
                  {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                  Save
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
