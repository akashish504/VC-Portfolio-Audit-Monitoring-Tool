import { useEffect, useState, useCallback } from 'react';
import {
  listSyncAlerts,
  getSyncAlertsUnreadCount,
  acknowledgeSyncAlert,
  acknowledgeAllSyncAlerts,
  type SyncAlert,
} from '@/api/syncAlerts';

type FilterTab = 'all' | 'unread' | 'read';

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function SyncAlertsPage() {
  const [tab, setTab] = useState<FilterTab>('all');
  const [alerts, setAlerts] = useState<SyncAlert[]>([]);
  const [total, setTotal] = useState(0);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<Set<number>>(new Set());
  const [allPending, setAllPending] = useState(false);

  const fetchAlerts = useCallback(async (status: FilterTab) => {
    setLoading(true);
    setError(null);
    try {
      const res = await listSyncAlerts({ status, limit: 100, offset: 0 });
      setAlerts(res.items);
      setTotal(res.total);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchUnreadCount = useCallback(async () => {
    try {
      const res = await getSyncAlertsUnreadCount();
      setUnreadCount(res.count);
    } catch {
      // silently ignore
    }
  }, []);

  useEffect(() => {
    void fetchAlerts(tab);
    void fetchUnreadCount();
  }, [tab, fetchAlerts, fetchUnreadCount]);

  const handleAcknowledge = async (id: number) => {
    setPending((prev) => new Set(prev).add(id));
    try {
      await acknowledgeSyncAlert(id);
      window.dispatchEvent(new CustomEvent('sync-alerts:updated'));
      await Promise.all([fetchAlerts(tab), fetchUnreadCount()]);
    } catch {
      // silently ignore
    } finally {
      setPending((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const handleAcknowledgeAll = async () => {
    setAllPending(true);
    try {
      await acknowledgeAllSyncAlerts();
      window.dispatchEvent(new CustomEvent('sync-alerts:updated'));
      await Promise.all([fetchAlerts(tab), fetchUnreadCount()]);
    } catch {
      // silently ignore
    } finally {
      setAllPending(false);
    }
  };

  const tabs: { label: string; value: FilterTab }[] = [
    { label: 'All', value: 'all' },
    { label: 'Unread', value: 'unread' },
    { label: 'Read', value: 'read' },
  ];

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-semibold text-gray-900">Sync Alerts</h1>
        {unreadCount > 0 && (
          <button
            onClick={handleAcknowledgeAll}
            disabled={allPending}
            className="text-sm px-3 py-1.5 rounded-md bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {allPending ? 'Marking…' : 'Mark all as read'}
          </button>
        )}
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 border-b border-gray-200 mb-6">
        {tabs.map(({ label, value }) => (
          <button
            key={value}
            onClick={() => setTab(value)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === value
                ? 'border-blue-600 text-blue-700'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {label}
            {value === 'unread' && unreadCount > 0 && (
              <span className="ml-1.5 inline-flex items-center justify-center rounded-full bg-red-500 text-white text-xs w-4 h-4">
                {unreadCount > 99 ? '99+' : unreadCount}
              </span>
            )}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Failed to load alerts: {error}
        </div>
      )}

      {loading ? (
        <div className="flex justify-center py-12 text-gray-400 text-sm">Loading…</div>
      ) : alerts.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-gray-400">
          <p className="text-sm">
            {tab === 'unread'
              ? "No unread alerts — you're all caught up."
              : tab === 'read'
              ? 'No read alerts yet.'
              : 'No sync alerts yet.'}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {alerts.map((alert) => (
            <div
              key={alert.id}
              className={`rounded-lg border p-4 transition-colors ${
                alert.is_read
                  ? 'border-gray-200 bg-white'
                  : 'border-blue-200 bg-blue-50'
              }`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <p className={`text-sm ${alert.is_read ? 'text-gray-700' : 'text-gray-900 font-medium'}`}>
                    {alert.message}
                  </p>
                  <p className="text-xs text-gray-400 mt-1">
                    Received {formatDate(alert.created_at)}
                  </p>
                  {alert.is_read && alert.acknowledged_by_user_email && (
                    <p className="text-xs text-gray-400 mt-0.5">
                      Acknowledged by {alert.acknowledged_by_user_email}
                      {alert.read_at ? ` on ${formatDate(alert.read_at)}` : ''}
                    </p>
                  )}
                </div>
                {!alert.is_read && (
                  <button
                    onClick={() => handleAcknowledge(alert.id)}
                    disabled={pending.has(alert.id)}
                    className="shrink-0 text-xs px-3 py-1.5 rounded-md border border-gray-300 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
                  >
                    {pending.has(alert.id) ? 'Marking…' : 'Mark as read'}
                  </button>
                )}
              </div>
            </div>
          ))}
          {total > alerts.length && (
            <p className="text-xs text-center text-gray-400 pt-2">
              Showing {alerts.length} of {total} alerts
            </p>
          )}
        </div>
      )}
    </div>
  );
}
