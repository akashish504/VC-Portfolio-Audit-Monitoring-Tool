import { useCallback, useEffect, useState } from 'react';
import { Download, Loader2, Trash2, X } from 'lucide-react';
import apiClient from '@/api/axios';

interface ActionableDeal {
  id: number;
  deal_id: string;
  deal_name: string;
  fund: string;
  strategy: string;
  review_cycle_id: string | null;
  created_at: string | null;
}

interface Props {
  onClose: () => void;
  onCountChange?: (count: number) => void;
}

export default function ActionableDealsModal({ onClose, onCountChange }: Props) {
  const [items, setItems] = useState<ActionableDeal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [downloading, setDownloading] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<ActionableDeal | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await apiClient.get<{ items: ActionableDeal[]; total: number }>(
        '/api/v1/actionable-deals',
      );
      setItems(data.items);
      onCountChange?.(data.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load actionable deals');
    } finally {
      setLoading(false);
    }
  }, [onCountChange]);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const response = await apiClient.get('/api/v1/actionable-deals/download', {
        responseType: 'blob',
      });
      const cd = response.headers['content-disposition'] as string | undefined;
      const match = cd?.match(/filename="([^"]+)"/);
      const filename = match?.[1] ?? 'actionable_deals.xlsx';
      const url = URL.createObjectURL(new Blob([response.data as BlobPart]));
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError('Download failed. Please try again.');
    } finally {
      setDownloading(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiClient.delete(`/api/v1/actionable-deals/${encodeURIComponent(deleteTarget.deal_id)}`);
      setDeleteTarget(null);
      await loadItems();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail ?? (e instanceof Error ? e.message : 'Failed to delete.'));
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      {/* Delete confirmation */}
      {deleteTarget && (
        <div className="fixed inset-0 z-60 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-xl">
            <h3 className="text-base font-semibold text-gray-900">Delete actionable deal?</h3>
            <p className="mt-2 text-sm text-gray-500">
              This will remove all placeholder rows for{' '}
              <span className="font-medium text-gray-800">
                {deleteTarget.deal_name || deleteTarget.deal_id}
              </span>
              . The company will remain in the Portfolio but will no longer appear in Actionable Deals.
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
                className="px-4 py-2 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmDelete()}
                disabled={deleting}
                className="px-4 py-2 rounded-lg bg-red-600 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="w-full max-w-3xl rounded-xl bg-white shadow-2xl flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 shrink-0">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Actionable Deals</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              New companies discovered by sync with no fund or strategy assigned. Download the XLSX to review.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-2 px-6 py-3 border-b border-gray-100 bg-gray-50 shrink-0">
          <button
            type="button"
            onClick={() => void handleDownload()}
            disabled={downloading || loading || items.length === 0}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed shadow-sm"
          >
            {downloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            Download XLSX
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto px-6 py-4">
          {error && (
            <p className="text-sm text-red-600 mb-3">{error}</p>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-16 gap-2 text-gray-400">
              <Loader2 className="h-5 w-5 animate-spin" />
              <span className="text-sm">Loading…</span>
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <div className="text-4xl mb-3">✓</div>
              <p className="text-sm font-medium text-gray-700">No actionable deals</p>
              <p className="text-xs text-gray-400 mt-1">All new companies have been assigned a fund and strategy.</p>
            </div>
          ) : (
            <table className="w-full text-sm border-separate border-spacing-0">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200">Deal ID</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200">Deal Name</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200">Review Cycle</th>
                  <th className="text-left px-3 py-2 text-xs font-medium text-gray-500 uppercase tracking-wider border-b border-gray-200">Added</th>
                  <th className="px-3 py-2 border-b border-gray-200 w-10" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {items.map((item) => (
                  <tr key={item.deal_id} className="hover:bg-gray-50">
                    <td className="px-3 py-2 text-xs font-mono text-gray-600">{item.deal_id}</td>
                    <td className="px-3 py-2 text-xs text-gray-900 font-medium">{item.deal_name || '—'}</td>
                    <td className="px-3 py-2 text-xs text-gray-500">{item.review_cycle_id || '—'}</td>
                    <td className="px-3 py-2 text-xs text-gray-400">
                      {item.created_at ? new Date(item.created_at).toLocaleDateString() : '—'}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button
                        type="button"
                        onClick={() => setDeleteTarget(item)}
                        className="p-1 rounded text-gray-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                        title="Delete placeholder rows for this deal"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-3 border-t border-gray-200 bg-gray-50 shrink-0">
          <span className="text-xs text-gray-500">
            {items.length} deal{items.length !== 1 ? 's' : ''} awaiting action
          </span>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
