import { useState } from 'react';
import { Download } from 'lucide-react';

import { downloadTimelineTable, type TimelineTableKey } from '@/api/dashboard';
import { saveBlob } from '@/lib/utils';

/** Small "XLSX" button that downloads one Timeline table (the aggregated grid). */
export default function TableDownloadButton({
  table,
  reviewCycleId,
}: {
  table: TimelineTableKey;
  reviewCycleId?: string;
}) {
  const [busy, setBusy] = useState(false);

  const onClick = async () => {
    setBusy(true);
    try {
      const { blob, filename } = await downloadTimelineTable({
        review_cycle_id: reviewCycleId,
        table,
      });
      saveBlob(blob, filename);
    } catch {
      // non-critical — surfaced via the global API error handler
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50 disabled:opacity-50"
    >
      <Download className="h-3.5 w-3.5" />
      {busy ? 'Preparing…' : 'XLSX'}
    </button>
  );
}
