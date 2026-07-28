import { AlertTriangle, CheckCircle2, Clock, MessageSquare } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { useAppState } from '@/context/AppContext';
import type { AuditStatus } from '@/data/mockData';

const statusConfig: Record<AuditStatus, { icon: React.ElementType; color: string; bg: string }> = {
  'Pending Review': { icon: Clock, color: 'text-yellow-600', bg: 'bg-yellow-50' },
  'Discrepancy Identified': { icon: AlertTriangle, color: 'text-red-600', bg: 'bg-red-50' },
  'Clarification Requested': { icon: MessageSquare, color: 'text-blue-600', bg: 'bg-blue-50' },
  Resolved: { icon: CheckCircle2, color: 'text-green-600', bg: 'bg-green-50' },
};

const statusOrder: AuditStatus[] = ['Pending Review', 'Discrepancy Identified', 'Clarification Requested', 'Resolved'];

export default function AuditPipelinePage() {
  const { companies, setSelectedCompanyId } = useAppState();
  const navigate = useNavigate();
  const entities = companies.filter((c) => c.parentId !== null);

  const handleClick = (companyId: string) => {
    setSelectedCompanyId(companyId);
    navigate('/workspace');
  };

  return (
    <div className="h-full overflow-auto p-4">
      <div className="mb-4">
        <h1 className="text-2xl font-bold text-gray-900">Audit Pipeline</h1>
        <p className="text-xs text-gray-500">Entity reconciliation status tracker</p>
      </div>

      <div className="grid grid-cols-4 gap-px bg-gray-200 rounded-lg overflow-hidden">
        {statusOrder.map((status) => {
          const config = statusConfig[status];
          const Icon = config.icon;
          const items = entities.filter((c) => c.status === status);

          return (
            <div key={status} className="bg-white flex flex-col">
              <div className="px-3 py-2.5 border-b border-gray-200 flex items-center gap-2">
                <Icon className={`h-3.5 w-3.5 ${config.color}`} />
                <span className="text-xs font-semibold text-gray-700 uppercase tracking-wider">{status}</span>
                <span className={`ml-auto text-xs font-mono font-semibold ${config.color}`}>{items.length}</span>
              </div>

              <div className="flex-1 p-2 space-y-1 min-h-[200px]">
                {items.length === 0 ? (
                  <p className="text-xs text-gray-500 text-center py-8">No entities</p>
                ) : (
                  items.map((company) => (
                    <button
                      key={company.id}
                      onClick={() => handleClick(company.id)}
                      className={`w-full text-left px-3 py-2.5 rounded-md border border-gray-200 hover:bg-gray-50 transition-colors ${config.bg}`}
                    >
                      <div className="text-sm font-medium text-gray-900 truncate">{company.name}</div>
                      <div className="text-xs text-gray-500 mt-0.5">{company.auditPeriod}</div>
                      {company.hasAuditReport && <div className="text-[10px] text-green-700 mt-1">Report attached</div>}
                    </button>
                  ))
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

