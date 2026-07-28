import { useCallback, useEffect, useState } from 'react';
import { Search } from 'lucide-react';

import { Input } from '@/components/ui/input';

export type SettingsAuditLogEntry = {
  id: string;
  action: string;
  summary?: string;
  timestamp: string;
  user: string;
  reviewCycleId?: string;
};

type FetchLogs = (params: { q?: string; limit?: number; offset?: number }) => Promise<{
  items: Array<{
    id: string;
    action?: string | null;
    summary?: string | null;
    occurred_at?: string | null;
    updated_at?: string | null;
    user_id?: string | null;
    review_cycle_id?: string | null;
  }>;
  total: number;
}>;

export function SettingsAuditLogPanel({
  title,
  fetchLogs,
  searchable = false,
  getReviewCycleLabel,
  refreshKey = 0,
}: {
  title: string;
  fetchLogs: FetchLogs;
  searchable?: boolean;
  getReviewCycleLabel?: (id: string) => string;
  refreshKey?: number;
}) {
  const [logs, setLogs] = useState<SettingsAuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => window.clearTimeout(t);
  }, [search]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await fetchLogs({
        q: debouncedSearch || undefined,
        limit: 200,
        offset: 0,
      });
      setLogs(
        (page.items ?? []).map((r) => ({
          id: r.id,
          action: r.action || 'Audit',
          summary: r.summary || r.action || undefined,
          timestamp: r.occurred_at || r.updated_at || '',
          user: r.user_id || 'unknown',
          reviewCycleId: r.review_cycle_id || undefined,
        })),
      );
    } catch {
      setLogs([]);
    } finally {
      setLoading(false);
    }
  }, [fetchLogs, debouncedSearch]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  return (
    <div className="bg-background border border-border rounded-lg shadow-sm overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        {searchable ? (
          <div className="relative w-full max-w-xs">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search logs…"
              className="pl-8 h-8 text-xs"
            />
          </div>
        ) : null}
      </div>
      {loading ? (
        <p className="px-4 py-8 text-sm text-muted-foreground text-center">Loading audit logs…</p>
      ) : logs.length === 0 ? (
        <p className="px-4 py-8 text-sm text-muted-foreground text-center">No audit log entries yet</p>
      ) : (
        <div className="divide-y divide-border max-h-[28rem] overflow-y-auto">
          {logs.map((log) => {
            const message = log.summary || log.action;
            return (
            <div key={log.id} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-2 mb-0.5">
                <span className="text-sm font-medium text-foreground whitespace-pre-line break-words max-w-full">
                  {message}
                </span>
                <span className="text-[10px] text-muted-foreground font-mono">
                  {log.timestamp ? new Date(log.timestamp).toLocaleString() : '—'}
                </span>
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-0.5">
                {log.reviewCycleId && getReviewCycleLabel ? (
                  <p className="text-[10px] text-muted-foreground">
                    Cycle: {getReviewCycleLabel(log.reviewCycleId)}
                  </p>
                ) : null}
                <p className="text-[10px] text-muted-foreground">by {log.user}</p>
              </div>
            </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
