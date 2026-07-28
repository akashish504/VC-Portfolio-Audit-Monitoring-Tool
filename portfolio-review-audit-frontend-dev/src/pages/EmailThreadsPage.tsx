import { useMemo, useState } from 'react';
import { Mail } from 'lucide-react';
import { useAppState } from '@/context/AppContext';
import { CompanyEmailThreads } from '@/components/company/CompanyEmailThreads';

export default function EmailThreadsPage() {
  const { companies } = useAppState();
  const [companyId, setCompanyId] = useState<string>('');

  const options = useMemo(() => {
    return [...companies].sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  }, [companies]);

  return (
    <div className="h-full overflow-auto">
      <div className="sticky top-0 z-10 bg-white border-b border-gray-200 px-6 py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-lg font-bold text-gray-900">Email Threads</h1>
            <p className="text-xs text-gray-500 mt-1">Manage and track email communications by company.</p>
          </div>

          <div className="w-[360px] max-w-full">
            <label className="block text-xs text-gray-500 mb-1">Company</label>
            <select
              value={companyId}
              onChange={(e) => setCompanyId(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
            >
              <option value="">Select a company…</option>
              {options.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {companyId ? (
        <div className="h-[calc(100vh-140px)]">
          <CompanyEmailThreads companyId={companyId} />
        </div>
      ) : (
        <div className="p-6">
          <div className="text-center py-12 bg-white rounded-lg border border-gray-200">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-gray-100 mb-4">
              <Mail size={24} className="text-gray-400" />
            </div>
            <h3 className="text-lg font-medium text-gray-900 mb-1">Select a company</h3>
            <p className="text-gray-500">Choose a company to view its email threads.</p>
          </div>
        </div>
      )}
    </div>
  );
}

