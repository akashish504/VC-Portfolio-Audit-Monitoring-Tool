import { useEffect, useState } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { Download } from 'lucide-react';

import { downloadScopingCompanies, listScopingCompanies, type ScopingCompanyQuery } from '@/api/dashboard';
import { Skeleton } from '@/components/ui/skeleton';
import { saveBlob } from '@/lib/utils';
import { fmtInt } from './dashboardFormat';
import DashboardCompanyTable from './DashboardCompanyTable';

interface Props {
  params: ScopingCompanyQuery;
  pageSize?: number;
}

/** Self-contained paginated company list driven by `params`. Reused by the
 *  IL-wise and Category-wise scoping sections. Resets to page 1 on filter change.
 *  The Download button exports the COMPLETE filtered set, not just the page. */
export default function PaginatedCompanyList({ params, pageSize = 10 }: Props) {
  const [page, setPage] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const key = JSON.stringify(params);
  useEffect(() => { setPage(0); }, [key]);

  const query = useQuery({
    queryKey: ['dashboard', 'company-list', key, page],
    queryFn: () => listScopingCompanies({ ...params, limit: pageSize, offset: page * pageSize }),
    placeholderData: keepPreviousData,
  });

  const total = query.data?.total ?? 0;
  const rows = query.data?.items ?? [];
  const pageCount = Math.max(1, Math.ceil(total / pageSize));

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const { blob, filename } = await downloadScopingCompanies(params);
      saveBlob(blob, filename);
    } catch {
      // non-critical — surfaced via the global API error handler
    } finally {
      setDownloading(false);
    }
  };

  if (query.isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-xs text-gray-500">{fmtInt(total)} companies</span>
        <button
          type="button"
          onClick={() => void handleDownload()}
          disabled={downloading || total === 0}
          className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Download className="h-3.5 w-3.5" />
          {downloading ? 'Preparing…' : 'Download XLSX'}
        </button>
      </div>
      <DashboardCompanyTable rows={rows} />
      {total > pageSize && (
        <div className="mt-3 flex items-center justify-between text-sm text-gray-600">
          <span>Page {page + 1} of {pageCount}</span>
          <div className="flex gap-2">
            <button type="button" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
              className="rounded-md border border-gray-300 px-3 py-1 hover:bg-gray-50 disabled:opacity-50">Previous</button>
            <button type="button" onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))} disabled={page >= pageCount - 1}
              className="rounded-md border border-gray-300 px-3 py-1 hover:bg-gray-50 disabled:opacity-50">Next</button>
          </div>
        </div>
      )}
    </div>
  );
}
