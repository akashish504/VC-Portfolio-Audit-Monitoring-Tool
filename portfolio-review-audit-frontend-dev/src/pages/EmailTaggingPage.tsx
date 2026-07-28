import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileBarChart2, Mail } from 'lucide-react';

import { listUntaggedThreads, type ApiEmailThreadSummary } from '@/api/emailThreads';
import UntaggedEmailThreadList from '@/components/email/UntaggedEmailThreadList';
import UntaggedEmailThreadView from '@/components/email/UntaggedEmailThreadView';

export default function EmailTaggingPage() {
  const navigate = useNavigate();
  const [threads, setThreads] = useState<ApiEmailThreadSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);

  const fetchData = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const rows = await listUntaggedThreads();
      setThreads(rows);
    } catch {
      setError('Failed to fetch untagged emails');
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  const refreshData = useCallback(() => fetchData(false), [fetchData]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  return (
    <div className="h-full overflow-auto">
      {/* Page header */}
      <div className="sticky top-0 z-10 bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold text-gray-900">Email Tagging</h1>
            <p className="text-xs text-gray-500 mt-1">
              Tag unclassified email threads to the relevant portfolio company.
            </p>
          </div>
          <button
            onClick={() => navigate('/audited-financials-emails')}
            className="inline-flex items-center gap-2 shrink-0 px-3 py-2 text-sm font-medium rounded-md border border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 transition-colors"
          >
            <FileBarChart2 size={15} />
            Audited Financials Emails
          </button>
        </div>
      </div>

      <div className="p-6">
        {loading ? (
          <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-gray-100 mb-4">
              <Mail size={24} className="text-gray-400 animate-spin" />
            </div>
            <h3 className="text-lg font-medium text-gray-900 mb-1">Loading Email Threads…</h3>
            <p className="text-gray-500 text-sm">Please wait while we fetch the latest email threads</p>
          </div>
        ) : error ? (
          <div className="text-center py-12 bg-white rounded-lg border border-red-200">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-red-100 mb-4">
              <Mail size={24} className="text-red-400" />
            </div>
            <h3 className="text-lg font-medium text-red-900 mb-1">Error Fetching Threads</h3>
            <p className="text-red-500 text-sm">{error}</p>
          </div>
        ) : threads && threads.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-gray-100 mb-4">
              <Mail size={24} className="text-gray-400" />
            </div>
            <h3 className="text-lg font-medium text-gray-900 mb-1">No Untagged Email Threads</h3>
            <p className="text-gray-500 text-sm">
              All email threads from the last 60 days have been tagged.
            </p>
          </div>
        ) : threads && threads.length > 0 ? (
          <div className="flex gap-6 h-[calc(100vh-200px)]">
            {/* Thread list — 1/3 width */}
            <div className="w-1/3 flex-shrink-0 h-full">
              <UntaggedEmailThreadList
                threads={threads}
                selectedThreadId={selectedThreadId}
                onSelect={setSelectedThreadId}
              />
            </div>

            {/* Thread detail — 2/3 width */}
            <div className="flex-1 h-full overflow-y-auto">
              {selectedThreadId ? (
                <UntaggedEmailThreadView
                  threadId={selectedThreadId}
                  onTagged={refreshData}
                />
              ) : (
                <div className="text-center py-12 bg-white rounded-lg border border-gray-200 h-full flex items-center justify-center">
                  <div>
                    <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-gray-100 mb-4">
                      <Mail size={24} className="text-gray-400" />
                    </div>
                    <h3 className="text-lg font-medium text-gray-900 mb-1">Select a Thread</h3>
                    <p className="text-gray-500 text-sm">
                      Choose an email thread from the list to view its details
                    </p>
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : null}
      </div>

    </div>
  );
}
