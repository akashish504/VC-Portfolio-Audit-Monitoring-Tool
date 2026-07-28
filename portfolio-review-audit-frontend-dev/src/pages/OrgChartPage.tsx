import React, { useEffect, useRef, useState } from 'react';
import { Building2, CheckCircle2, FileText, Paperclip, Search, X } from 'lucide-react';
import { toast } from 'sonner';

import { useAppState } from '@/context/AppContext';
import { type Company, taggedFiles } from '@/data/mockData';

const statusBadge: Record<string, string> = {
  'Pending Review': 'bg-yellow-100 text-yellow-800',
  'Discrepancy Identified': 'bg-red-100 text-red-800',
  'Clarification Requested': 'bg-blue-100 text-blue-800',
  Resolved: 'bg-green-100 text-green-800',
};

function FilePickerPopover({ companyName, onClose }: { companyId: string; companyName: string; onClose: () => void }) {
  const [search, setSearch] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  const filtered = taggedFiles.filter((f) => f.fileName.toLowerCase().includes(search.toLowerCase()));

  const handleAttach = (fileName: string) => {
    toast.success(`"${fileName}" attached to ${companyName}`);
    onClose();
  };

  return (
    <div ref={ref} className="absolute top-full left-1/2 -translate-x-1/2 z-50 mt-2 w-72 bg-white border border-gray-200 rounded-lg shadow-lg p-2">
      <div className="flex items-center gap-2 mb-2">
        <Search className="h-3.5 w-3.5 text-gray-400 shrink-0" />
        <input
          autoFocus
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search files..."
          className="w-full text-xs bg-transparent border-none outline-none text-gray-900 placeholder:text-gray-400"
        />
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="border-t border-gray-100 pt-1 max-h-48 overflow-auto">
        {filtered.length === 0 ? (
          <p className="text-xs text-gray-500 py-3 text-center">No files found</p>
        ) : (
          filtered.map((f) => (
            <button
              key={f.id}
              onClick={() => handleAttach(f.fileName)}
              className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left hover:bg-gray-50 transition-all"
            >
              <FileText className="h-3.5 w-3.5 text-gray-400 shrink-0" />
              <div className="min-w-0">
                <p className="text-xs text-gray-900 truncate">{f.fileName}</p>
                <p className="text-[10px] text-gray-400">
                  {f.size} · {f.status}
                </p>
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}

function OrgNodeCard({ company }: { company: Company }) {
  const { attachReport } = useAppState();
  const [showFilePicker, setShowFilePicker] = useState(false);

  return (
    <div className="relative flex flex-col items-center">
      <div className="bg-white border border-gray-200 rounded-lg shadow-sm px-4 py-3 min-w-[200px] max-w-[240px] hover:shadow-md transition-all group">
        <div className="flex items-center gap-2 mb-1.5">
          <Building2 className="h-4 w-4 text-blue-500 shrink-0" />
          <span className="text-sm font-semibold text-gray-900 truncate">{company.name}</span>
        </div>
        <div className="flex items-center gap-2 mb-2">
          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusBadge[company.status] || 'bg-gray-100 text-gray-800'}`}>
            {company.status}
          </span>
        </div>
        <div className="text-xs text-gray-500 mb-2">{company.auditPeriod}</div>
        <div className="flex items-center justify-between gap-1">
          <button
            onClick={(e) => {
              e.stopPropagation();
              setShowFilePicker((prev) => !prev);
            }}
            className="flex items-center gap-1 text-xs text-gray-500 hover:text-blue-600 px-2 py-1 rounded-md border border-gray-200 opacity-0 group-hover:opacity-100 transition-all"
          >
            <FileText className="h-3 w-3" /> Attach File
          </button>
          {company.hasAuditReport ? (
            <span className="flex items-center gap-1 text-xs text-green-600">
              <CheckCircle2 className="h-3.5 w-3.5" /> Report
            </span>
          ) : (
            <button
              onClick={(e) => {
                e.stopPropagation();
                attachReport(company.id);
                toast.success(`Audit report attached to ${company.name}`);
              }}
              className="flex items-center gap-1 text-xs text-gray-500 hover:text-blue-600 px-2 py-1 rounded-md border border-gray-200 opacity-0 group-hover:opacity-100 transition-all"
            >
              <Paperclip className="h-3 w-3" /> Report
            </button>
          )}
        </div>
      </div>
      {showFilePicker && <FilePickerPopover companyId={company.id} companyName={company.name} onClose={() => setShowFilePicker(false)} />}
    </div>
  );
}

function TreeNode({ company, companies }: { company: Company; companies: Company[] }) {
  const children = companies.filter((c) => c.parentId === company.id);
  return (
    <div className="flex flex-col items-center">
      <OrgNodeCard company={company} />
      {children.length > 0 && (
        <>
          <div className="w-0.5 h-6 bg-gray-200" />
          <div className="flex gap-8">
            {children.map((child, idx) => (
              <div key={child.id} className="relative flex flex-col items-center">
                <div className="w-0.5 h-6 bg-gray-200" />
                {children.length > 1 && (
                  <div
                    className="absolute top-0 h-0.5 bg-gray-200"
                    style={{
                      left: idx === 0 ? '50%' : 0,
                      right: idx === children.length - 1 ? '50%' : 0,
                    }}
                  />
                )}
                <TreeNode company={child} companies={companies} />
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default function OrgChartPage() {
  const { companies } = useAppState();
  const roots = companies.filter((c) => c.parentId === null);

  return (
    <div className="h-full overflow-auto p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Entity Structure</h1>
        <p className="text-xs text-gray-500 mt-1">Portfolio companies and subsidiaries</p>
      </div>
      <div className="overflow-auto pb-12">
        <div className="inline-flex flex-col gap-10 items-center min-w-full">
          {roots.map((root) => (
            <TreeNode key={root.id} company={root} companies={companies} />
          ))}
        </div>
      </div>
    </div>
  );
}

