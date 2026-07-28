import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { FileText, ScrollText } from 'lucide-react';

import { getFileExtractionStatus } from '@/api/fileProcessing';
import { listFiles } from '@/api/portfolio';
import { QualitativeReportContent } from '@/components/qualitative/QualitativeReportContent';
import {
  entityComplianceBadgeChipClass,
  entityComplianceBadgeLabel,
  parseQualitativeReport,
} from '@/components/qualitative/qualitativeReportModel';
import type { FileData } from '@/types/domain';

type CompanyQualitativeFile = {
  fileId: number;
  fileName: string;
  entityId: number;
  entityName: string;
  uploadedAt: string;
  extractKind: string;
  extractStatus: string;
  meta: Record<string, unknown>;
};

function entityDisplayName(f: FileData) {
  const n = (f.entity_name || '').trim();
  if (n) return n;
  if (f.entity_id != null) return `Entity #${f.entity_id}`;
  return 'Unknown entity';
}

export function CompanyQualitativeReport({ companyId }: { companyId: number }) {
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<CompanyQualitativeFile[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await listFiles({ portfolio_company_id: companyId, limit: 500, offset: 0 });
      const withEntity = (res.items ?? []).filter((f) => f.entity_id != null && f.entity_id !== undefined);
      withEntity.sort((a, b) => {
        const ta = new Date(a.updated_at || a.created_at || 0).getTime();
        const tb = new Date(b.updated_at || b.created_at || 0).getTime();
        return tb - ta;
      });

      const enriched = await Promise.all(
        withEntity.map(async (f) => {
          let extractKind = 'audit_financials';
          let extractStatus = String(f.status || '').toLowerCase();
          let meta: Record<string, unknown> = {};
          try {
            const st = await getFileExtractionStatus(f.id);
            extractStatus = String(st.status || extractStatus).toLowerCase();
            meta = (st.meta && typeof st.meta === 'object' ? st.meta : {}) as Record<string, unknown>;
            const k = meta.kind;
            if (typeof k === 'string' && k.trim()) extractKind = k.trim().toLowerCase();
          } catch {
            // file may not have OCR metadata yet
          }
          return {
            fileId: f.id,
            fileName: f.filename,
            entityId: f.entity_id as number,
            entityName: entityDisplayName(f),
            uploadedAt: f.updated_at || f.created_at || '',
            extractKind,
            extractStatus,
            meta,
          } satisfies CompanyQualitativeFile;
        }),
      );

      setRows(enriched);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [companyId]);

  useEffect(() => {
    void load();
    const onUpdated = () => void load();
    window.addEventListener('files:updated', onUpdated as EventListener);
    return () => window.removeEventListener('files:updated', onUpdated as EventListener);
  }, [load]);

  const anyRunning = useMemo(
    () => rows.some((r) => r.extractStatus === 'running' || r.extractStatus === 'queued'),
    [rows],
  );

  useEffect(() => {
    if (!anyRunning) return;
    const id = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(id);
  }, [anyRunning, load]);

  if (loading && rows.length === 0) {
    return (
      <div className="p-6">
        <p className="text-sm text-gray-500">Loading qualitative reports…</p>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="p-6">
        <div className="flex flex-col items-center justify-center py-16 text-center bg-white border border-gray-200 rounded-lg">
          <ScrollText className="h-10 w-10 text-gray-300 mb-2" />
          <p className="text-sm font-medium text-gray-800">No qualitative report yet</p>
          <p className="text-xs text-gray-500 mt-1 max-w-md">
            Attach an audit report file to this company and tag it to an entity. The qualitative summary will
            appear here after extraction runs.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-gray-900">Compliance Section</h2>
      </div>

      {rows.map((row) => {
        const vm = parseQualitativeReport(row.meta, {
          extractKind: row.extractKind,
          extractStatus: row.extractStatus,
          showPlaceholderSample: false,
        });
        return (
          <div key={row.fileId} className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-100 bg-slate-50/80">
              <div className="flex flex-wrap items-center gap-2 mb-1">
                <p className="text-sm font-bold text-gray-900">
                  Entity: <span className="text-blue-700">{row.entityName}</span>
                </p>
                {vm.entityComplianceBadge !== 'pending' && (
                  <span
                    className={entityComplianceBadgeChipClass(vm.entityComplianceBadge)}
                    title="Roll-up: opinion first; amber when CARO/IFC clause flags remain with a clean opinion"
                  >
                    {entityComplianceBadgeLabel(vm.entityComplianceBadge)}
                  </span>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-600">
                <span className="inline-flex items-center gap-1">
                  <FileText className="h-3.5 w-3.5 text-gray-400" />
                  <Link
                    to={`/file-tagging/${row.fileId}`}
                    state={{ from: { kind: 'company', companyId } }}
                    className="text-blue-600 hover:text-blue-800 hover:underline"
                  >
                    {row.fileName}
                  </Link>
                </span>
                {row.uploadedAt ? (
                  <span>Updated {new Date(row.uploadedAt).toLocaleString()}</span>
                ) : null}
              </div>
            </div>
            <QualitativeReportContent {...vm} embedded showTechnicalHint={false} />
          </div>
        );
      })}
    </div>
  );
}
