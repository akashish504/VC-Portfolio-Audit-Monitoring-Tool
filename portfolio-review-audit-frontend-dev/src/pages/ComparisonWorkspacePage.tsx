import { useState } from 'react';
import { ChevronDown, FileText, Flag } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

import { getFinancialParameterLabel } from '@/constants/financialParameterLabels';
import { useAppState } from '@/context/AppContext';
import { calculateVariance, formatCurrency, reconciliationData, type Company } from '@/data/mockData';

function PdfViewer({ company }: { company: Company }) {
  return (
    <div className="h-full flex flex-col bg-white border-r border-gray-200">
      <div className="px-3 py-2 border-b border-gray-200 flex items-center gap-2">
        <FileText className="h-3.5 w-3.5 text-gray-500" />
        <span className="text-xs font-medium text-gray-900 truncate">
          {company.name} — Audit Report {company.auditPeriod}
        </span>
      </div>
      <div className="flex-1 flex items-center justify-center">
        {company.hasAuditReport ? (
          <div className="text-center space-y-3 px-8">
            <div className="w-full max-w-[280px] mx-auto border border-gray-200 rounded-sm bg-white p-6">
              <div className="space-y-2">
                <div className="h-3 bg-gray-200 rounded-sm w-3/4 mx-auto" />
                <div className="h-2 bg-gray-100 rounded-sm w-full" />
                <div className="h-2 bg-gray-100 rounded-sm w-5/6" />
                <div className="h-2 bg-gray-100 rounded-sm w-full" />
                <div className="h-6 my-3" />
                <div className="h-2 bg-gray-100 rounded-sm w-full" />
                <div className="h-2 bg-gray-100 rounded-sm w-4/5" />
                <div className="h-2 bg-blue-50 rounded-sm w-full border border-blue-100" />
                <div className="h-2 bg-gray-100 rounded-sm w-3/4" />
                <div className="h-6 my-3" />
                <div className="h-2 bg-gray-100 rounded-sm w-full" />
                <div className="h-2 bg-red-50 rounded-sm w-5/6 border border-red-100" />
                <div className="h-2 bg-gray-100 rounded-sm w-full" />
              </div>
            </div>
            <p className="text-xs text-gray-500">PDF viewer — OCR regions highlighted</p>
          </div>
        ) : (
          <p className="text-xs text-gray-500">No document attached</p>
        )}
      </div>
    </div>
  );
}

function ReconciliationTable({ companyId, company }: { companyId: string; company: Company }) {
  const { addEmail, updateCompanyStatus } = useAppState();
  const navigate = useNavigate();
  const data = reconciliationData[companyId] || [];

  const handleFlag = (fieldName: string, sourceVal: number, extractedVal: number) => {
    const variance = calculateVariance(sourceVal, extractedVal);
    const metricLabel = getFinancialParameterLabel(fieldName);
    const email = {
      id: `e-${Date.now()}`,
      companyId,
      subject: `${metricLabel} Variance — ${company.name} ${company.auditPeriod}`,
      timestamp: new Date().toISOString(),
      from: 'audit@vantagecap.com',
      to: company.contactEmail,
      body: `${company.contactName},\n\nDuring our ${company.auditPeriod} audit reconciliation for ${company.name}, we identified a variance of ${(Math.abs(variance.percent) * 100).toFixed(2)}% in the ${metricLabel} line item.\n\nAs per MIS: ${formatCurrency(sourceVal)}\nAs per AFS: ${formatCurrency(extractedVal)}\n\nPlease provide documentation to clarify this discrepancy.\n\nRegards,\nAudit Team`,
      status: 'draft' as const,
    };
    addEmail(email);
    updateCompanyStatus(companyId, 'Clarification Requested');
    toast.success(`Discrepancy flagged. Draft email created.`);
    navigate('/communications');
  };

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 py-2 border-b border-gray-200 flex items-center gap-2">
        <span className="text-xs font-medium text-gray-900">Financial Reconciliation</span>
        <span className="text-[10px] text-gray-500 font-mono">// {company.name}</span>
      </div>

      <div className="flex-1 overflow-auto">
        {data.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <p className="text-xs text-gray-500">No reconciliation data available</p>
          </div>
        ) : (
          <table className="w-full">
            <thead>
              <tr className="bg-gray-50">
                <th className="text-left px-3 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">Metric</th>
                <th className="text-right px-3 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">As per MIS</th>
                <th className="text-right px-3 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">As per AFS</th>
                <th className="text-right px-3 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">Variance</th>
                <th className="text-center px-3 py-2 w-12"></th>
              </tr>
            </thead>
            <tbody>
              {data.map((row) => {
                const v = calculateVariance(row.Source_Value, row.Extracted_Value);
                return (
                  <tr
                    key={row.Field_Name}
                    className={`h-10 border-b border-gray-200 group ${v.isFlagged ? 'bg-red-50/30' : 'hover:bg-gray-50'} transition-colors`}
                  >
                    <td className="px-3 text-sm text-gray-900">{getFinancialParameterLabel(row.Field_Name)}</td>
                    <td className="px-3 text-sm font-mono text-right text-gray-900">{formatCurrency(row.Source_Value)}</td>
                    <td className="px-3 text-sm font-mono text-right text-gray-900">{formatCurrency(row.Extracted_Value)}</td>
                    <td className={`px-3 text-sm font-mono text-right ${v.isFlagged ? 'text-red-700 font-semibold' : 'text-gray-500'}`}>
                      {v.percent === 0 ? '—' : `${(v.percent * 100).toFixed(2)}%`}
                    </td>
                    <td className="px-3 text-center">
                      {v.isFlagged && (
                        <button
                          onClick={() => handleFlag(row.Field_Name, row.Source_Value, row.Extracted_Value)}
                          className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded-sm hover:bg-red-100"
                          title="Flag Discrepancy"
                        >
                          <Flag className="h-3.5 w-3.5 text-red-600" />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default function ComparisonWorkspacePage() {
  const { companies, selectedCompanyId, setSelectedCompanyId } = useAppState();
  const [selectorOpen, setSelectorOpen] = useState(false);

  const entitiesWithData = companies.filter((c) => c.parentId !== null);
  const selected = companies.find((c) => c.id === selectedCompanyId) || entitiesWithData[0];
  const currentId = selected?.id || '';

  return (
    <div className="h-full flex flex-col">
      <div className="px-3 py-2 border-b border-gray-200 flex items-center gap-3 bg-white shrink-0">
        <span className="text-xs text-gray-500">Entity:</span>
        <div className="relative">
          <button onClick={() => setSelectorOpen(!selectorOpen)} className="flex items-center gap-1.5 text-sm font-medium text-gray-900 hover:text-blue-600 transition-colors">
            {selected?.name || 'Select Entity'}
            <ChevronDown className="h-3 w-3" />
          </button>
          {selectorOpen && (
            <div className="absolute top-full left-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-50 min-w-[200px]">
              {entitiesWithData.map((c) => (
                <button
                  key={c.id}
                  onClick={() => {
                    setSelectedCompanyId(c.id);
                    setSelectorOpen(false);
                  }}
                  className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-50 transition-colors ${c.id === currentId ? 'text-blue-600 bg-blue-50' : 'text-gray-900'}`}
                >
                  {c.name}
                </button>
              ))}
            </div>
          )}
        </div>
        <span className="text-xs text-gray-500 font-mono">// {selected?.auditPeriod}</span>
      </div>

      <div className="flex-1 flex min-h-0">
        <div className="w-1/2 border-r border-gray-200">{selected && <PdfViewer company={selected} />}</div>
        <div className="w-1/2">{selected && <ReconciliationTable companyId={currentId} company={selected} />}</div>
      </div>
    </div>
  );
}

