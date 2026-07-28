import { memo, useMemo, useState } from 'react';
import { Mail, MessageSquare, Search } from 'lucide-react';
import type { ApiEmailThreadSummary } from '@/api/emailThreads';

type Props = {
  threads: ApiEmailThreadSummary[];
  selectedThreadId: string | null;
  onSelect: (threadId: string) => void;
};

function getSenderName(sender: string | null): string {
  if (!sender) return '';
  const m = sender.match(/^(.+?)\s*<.+>$/) ?? sender.match(/^(.+?)@/);
  return m ? m[1].trim() : sender;
}

function UntaggedEmailThreadList({ threads, selectedThreadId, onSelect }: Props) {
  const [searchTerm, setSearchTerm] = useState('');

  const filtered = useMemo(() => {
    if (!searchTerm.trim()) return threads;
    const q = searchTerm.toLowerCase();
    return threads.filter(
      (t) =>
        (t.subject ?? '').toLowerCase().includes(q) ||
        (t.sender ?? '').toLowerCase().includes(q),
    );
  }, [threads, searchTerm]);

  return (
    <div className="h-full flex flex-col">
      {/* Search */}
      <div className="flex-shrink-0 mb-4">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" size={18} />
          <input
            type="text"
            className="w-full pl-10 pr-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all bg-white text-sm"
            placeholder="Search email threads..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>
        {searchTerm && (
          <p className="mt-2 text-xs text-gray-500">
            {filtered.length} of {threads.length} threads
          </p>
        )}
      </div>

      {/* Thread list */}
      <div className="flex-1 overflow-y-auto space-y-2">
        {filtered.map((thread, idx) => {
          const senderName = getSenderName(thread.sender);
          const isSelected = selectedThreadId === thread.thread_id;

          return (
            <button
              key={thread.thread_id}
              onClick={() => onSelect(thread.thread_id)}
              style={{ animationDelay: `${idx * 50}ms` }}
              className={`w-full text-left block p-4 rounded-lg border transition-all duration-200 group ${
                isSelected
                  ? 'bg-blue-50 border-blue-400 shadow-md'
                  : 'bg-white border-gray-200 hover:border-blue-300 hover:shadow-md'
              }`}
            >
              <div className="space-y-3">
                {/* Header */}
                <div className="flex items-start justify-between">
                  <div className="flex-1 min-w-0">
                    <h3
                      className={`font-semibold transition-colors duration-200 truncate text-sm ${
                        isSelected ? 'text-blue-800' : 'text-gray-900 group-hover:text-blue-600'
                      }`}
                    >
                      {thread.subject ?? '(no subject)'}
                    </h3>
                    {senderName && (
                      <p className="text-xs text-gray-600 mt-1">From: {senderName}</p>
                    )}
                  </div>
                </div>

                {/* Footer */}
                <div className="flex items-center justify-between pt-2 border-t border-gray-100">
                  <div className="flex items-center space-x-1 text-xs text-gray-500">
                    <MessageSquare size={13} />
                    <span>{thread.email_count} messages</span>
                  </div>
                  <div
                    className={`w-2 h-2 bg-blue-500 rounded-full transition-opacity duration-200 ${
                      isSelected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
                    }`}
                  />
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Empty state */}
      {filtered.length === 0 && (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center py-12">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-gray-100 mb-4">
              {searchTerm ? (
                <Search size={24} className="text-gray-400" />
              ) : (
                <Mail size={24} className="text-gray-400" />
              )}
            </div>
            <h3 className="text-lg font-medium text-gray-900 mb-2">
              {searchTerm ? 'No matching threads found' : 'No email threads'}
            </h3>
            <p className="text-sm text-gray-500 max-w-xs mx-auto">
              {searchTerm
                ? 'Try adjusting your search terms.'
                : 'Email threads will appear here when they become available.'}
            </p>
            {searchTerm && (
              <button
                onClick={() => setSearchTerm('')}
                className="mt-4 text-sm text-blue-600 hover:text-blue-700 font-medium"
              >
                Clear search
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default memo(UntaggedEmailThreadList);
