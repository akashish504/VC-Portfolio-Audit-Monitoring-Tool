import { FileText } from 'lucide-react';
import { useEffect, useState } from 'react';

import { listCompanyAudit } from '@/api/audit';

export function CompanyAuditLogs({ companyId }: { companyId: string }) {
  const [logs, setLogs] = useState<
    {
      id: string;
      action: string;
      timestamp: string;
      details: string;
      editReason: string;
      user: string;
    }[]
  >([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const numericId = Number(companyId);
        if (!Number.isFinite(numericId)) {
          setLogs([]);
          return;
        }
        const auditPage = await listCompanyAudit({
          company_id: String(numericId),
          limit: 200,
          offset: 0,
        });
        setLogs(
          (auditPage.items ?? []).map((r) => {
            const meta = r.meta && typeof r.meta === 'object' ? r.meta : {};
            const editReason =
              typeof meta.edit_reason === 'string' && meta.edit_reason.trim()
                ? meta.edit_reason.trim()
                : '';
            const summary =
              typeof meta.summary === 'string' && meta.summary.trim()
                ? meta.summary.trim()
                : typeof r.action === 'string'
                  ? r.action
                  : 'Audit event';
            const ts = r.occurred_at || r.updated_at;
            return {
              id: r.id,
              action: r.action || 'Audit',
              timestamp: ts,
              details: summary,
              editReason,
              user: r.user_id || 'unknown',
            };
          }),
        );
      } catch {
        setLogs([]);
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [companyId]);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2">
        <FileText className="h-8 w-8 text-gray-300" />
        <p className="text-sm text-gray-500">Loading audit logs…</p>
      </div>
    );
  }

  if (logs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2">
        <FileText className="h-8 w-8 text-gray-300" />
        <p className="text-sm text-gray-500">No audit log entries for this company</p>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm divide-y divide-gray-100">
        {logs.map((log) => (
          <div key={log.id} className="px-4 py-3">
              <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                <span className="text-sm font-medium text-gray-900">{log.action}</span>
                <span className="text-[10px] text-gray-400 font-mono">
                  {log.timestamp ? new Date(log.timestamp).toLocaleString() : '—'}
                </span>
              </div>
              {log.details !== log.action ? (
                <p className="text-xs text-gray-500">{log.details}</p>
              ) : null}
              {log.editReason ? (
                <p className="text-xs text-gray-600 mt-1 whitespace-pre-wrap">
                  Reason: {log.editReason}
                </p>
              ) : null}
              <p className="text-[10px] text-gray-400 mt-0.5">by {log.user}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
