import { useState } from 'react';
import { ArrowLeft, Clock, Mail, Send } from 'lucide-react';

import { useAppState } from '@/context/AppContext';

export default function CommunicationsPage() {
  const { emails, companies } = useAppState();
  const [selectedEmail, setSelectedEmail] = useState<string | null>(null);

  const email = emails.find((e) => e.id === selectedEmail);

  return (
    <div className="h-full flex">
      <div className="w-80 border-r border-gray-200 flex flex-col shrink-0 bg-white">
        <div className="px-3 py-2.5 border-b border-gray-200">
          <h1 className="text-sm font-semibold tracking-tight text-gray-900">Communications</h1>
          <p className="text-xs text-gray-500">
            {emails.length} thread{emails.length !== 1 ? 's' : ''}
          </p>
        </div>

        <div className="flex-1 overflow-auto">
          {emails.length === 0 ? (
            <p className="text-xs text-gray-500 text-center py-8">No threads</p>
          ) : (
            emails.map((e) => {
              const company = companies.find((c) => c.id === e.companyId);
              return (
                <button
                  key={e.id}
                  onClick={() => setSelectedEmail(e.id)}
                  className={`w-full text-left px-3 py-3 border-b border-gray-200 hover:bg-gray-50 transition-colors ${selectedEmail === e.id ? 'bg-gray-50' : ''}`}
                >
                  <div className="flex items-center gap-2 mb-1">
                    {e.status === 'draft' ? (
                      <Clock className="h-3 w-3 text-yellow-600 shrink-0" />
                    ) : (
                      <Send className="h-3 w-3 text-green-600 shrink-0" />
                    )}
                    <span className="text-xs font-semibold uppercase text-gray-500">{e.status === 'draft' ? 'Draft' : 'Sent'}</span>
                  </div>
                  <div className="text-sm font-medium text-gray-900 truncate">{e.subject}</div>
                  <div className="text-xs text-gray-500 mt-0.5">
                    {company?.name} · {new Date(e.timestamp).toLocaleDateString()}
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      <div className="flex-1 flex flex-col bg-white">
        {email ? (
          <>
            <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
              <button onClick={() => setSelectedEmail(null)} className="text-xs text-gray-500 hover:text-gray-900 mb-2 flex items-center gap-1 transition-colors">
                <ArrowLeft className="h-3 w-3" /> Back
              </button>
              <h2 className="text-sm font-semibold text-gray-900">{email.subject}</h2>
              <div className="flex gap-4 mt-1.5">
                <span className="text-xs text-gray-500">
                  From: <span className="text-gray-900 font-mono text-xs">{email.from}</span>
                </span>
                <span className="text-xs text-gray-500">
                  To: <span className="text-gray-900 font-mono text-xs">{email.to}</span>
                </span>
              </div>
            </div>

            <div className="flex-1 overflow-auto p-4">
              <pre className="text-sm text-gray-900 whitespace-pre-wrap font-sans leading-relaxed">{email.body}</pre>
            </div>

            {email.status === 'draft' && (
              <div className="px-4 py-3 border-t border-gray-200 bg-gray-50 flex gap-2">
                <button className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors">
                  <Send className="h-3.5 w-3.5" /> Send Inquiry
                </button>
                <button className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-900 border border-gray-200 rounded-md transition-colors">
                  Edit Draft
                </button>
              </div>
            )}
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <Mail className="h-8 w-8 text-gray-300 mx-auto mb-2" />
              <p className="text-xs text-gray-500">Select a thread to view</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

