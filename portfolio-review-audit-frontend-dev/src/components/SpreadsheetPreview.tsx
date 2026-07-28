/**
 * SpreadsheetPreview
 *
 * Renders an Excel workbook for in-browser preview using data fetched from
 * GET /files/{id}/preview/spreadsheet (parsed server-side with openpyxl).
 *
 * UI:
 *  • Sheet tabs along the top (one per worksheet).
 *  • Sticky header row (row with smallest index, treated as column headers).
 *  • Read-only table with alternating row shading.
 *  • Truncation banner when the server capped rows.
 *  • Loading skeleton + error state.
 */
import { useEffect, useRef, useState } from 'react';
import { TableIcon, AlertCircle, ChevronLeft, ChevronRight } from 'lucide-react';
import {
  getSpreadsheetPreview,
  type SpreadsheetPreviewResponse,
  type SpreadsheetPreviewSheet,
} from '@/api/fileProcessing';
import { getApiErrorMessage } from '@/api/apiError';

interface Props {
  fileId: number;
}

// How many sheet-tab labels to show before collapsing into a dropdown.
const MAX_VISIBLE_TABS = 8;

function SheetGrid({ sheet }: { sheet: SpreadsheetPreviewSheet }) {
  const tableRef = useRef<HTMLDivElement>(null);

  if (sheet.rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-gray-400 gap-2">
        <TableIcon className="h-10 w-10 opacity-40" />
        <p className="text-sm">This sheet is empty.</p>
      </div>
    );
  }

  // Use the first non-empty row as the sticky header.
  const [headerRow, ...bodyRows] = sheet.rows;
  const colCount = Math.max(...sheet.rows.map((r) => r.cells.length));

  return (
    <div className="flex flex-col gap-0 h-full">
      {sheet.truncated && (
        <div className="flex items-center gap-2 px-4 py-2 bg-amber-50 border-b border-amber-200 text-xs text-amber-700 shrink-0">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          Preview shows first {sheet.rows.length} rows. Download the file for the full workbook.
        </div>
      )}
      <div ref={tableRef} className="overflow-auto flex-1">
        <table className="w-full text-xs border-collapse">
          <thead className="sticky top-0 z-10 bg-gray-100 border-b border-gray-300">
            <tr>
              {/* Row number gutter */}
              <th className="w-10 min-w-[2.5rem] px-2 py-2 text-right text-gray-400 font-normal border-r border-gray-200 bg-gray-50">
                #
              </th>
              {Array.from({ length: colCount }, (_, ci) => (
                <th
                  key={ci}
                  className="px-3 py-2 text-left font-semibold text-gray-700 whitespace-nowrap border-r border-gray-200 last:border-r-0"
                >
                  {headerRow.cells[ci] ?? ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bodyRows.map((r, ri) => (
              <tr
                key={r.row}
                className={ri % 2 === 0 ? 'bg-white' : 'bg-gray-50/60'}
              >
                <td className="px-2 py-1.5 text-right text-gray-400 font-mono border-r border-gray-100 select-none">
                  {r.row}
                </td>
                {Array.from({ length: colCount }, (_, ci) => {
                  const val = r.cells[ci];
                  const isNumeric = val !== null && val !== '' && !isNaN(Number(val.replace(/,/g, '')));
                  return (
                    <td
                      key={ci}
                      className={[
                        'px-3 py-1.5 border-r border-gray-100 last:border-r-0 whitespace-nowrap max-w-[280px] truncate',
                        isNumeric ? 'text-right font-mono text-gray-800' : 'text-gray-700',
                      ].join(' ')}
                      title={val ?? ''}
                    >
                      {val ?? ''}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function SpreadsheetPreview({ fileId }: Props) {
  const [data, setData] = useState<SpreadsheetPreviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeSheet, setActiveSheet] = useState(0);
  const [tabOffset, setTabOffset] = useState(0); // for overflow scrolling

  useEffect(() => {
    setLoading(true);
    setError(null);
    setData(null);
    setActiveSheet(0);
    setTabOffset(0);

    let cancelled = false;
    getSpreadsheetPreview(fileId)
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e) => {
        if (!cancelled) setError(getApiErrorMessage(e, 'Could not load spreadsheet preview.'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [fileId]);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[420px] gap-3 text-gray-400">
        <TableIcon className="h-12 w-12 opacity-30 animate-pulse" />
        <p className="text-sm">Loading spreadsheet…</p>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[420px] gap-3 text-red-500 p-8">
        <AlertCircle className="h-10 w-10 opacity-60" />
        <p className="text-sm font-medium text-center max-w-sm">
          {error ?? 'Could not load spreadsheet preview.'}
        </p>
      </div>
    );
  }

  if (data.sheets.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[420px] gap-3 text-gray-400">
        <TableIcon className="h-10 w-10 opacity-30" />
        <p className="text-sm">No sheets found in this workbook.</p>
      </div>
    );
  }

  const visibleSheets = data.sheets.slice(tabOffset, tabOffset + MAX_VISIBLE_TABS);
  const canScrollLeft = tabOffset > 0;
  const canScrollRight = tabOffset + MAX_VISIBLE_TABS < data.sheets.length;
  const currentSheet: SpreadsheetPreviewSheet = data.sheets[activeSheet] ?? data.sheets[0];

  return (
    <div className="flex flex-col h-[75vh] min-h-[420px]">
      {/* Sheet tabs */}
      <div className="flex items-center gap-0 border-b border-gray-200 bg-gray-50 shrink-0 overflow-hidden">
        {canScrollLeft && (
          <button
            type="button"
            className="p-2 hover:bg-gray-200 text-gray-500 shrink-0"
            onClick={() => setTabOffset((o) => Math.max(0, o - 1))}
            aria-label="Previous sheets"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
        )}
        {visibleSheets.map((sheet, vi) => {
          const idx = tabOffset + vi;
          const isActive = idx === activeSheet;
          return (
            <button
              key={sheet.name}
              type="button"
              onClick={() => setActiveSheet(idx)}
              className={[
                'px-4 py-2.5 text-xs font-medium whitespace-nowrap border-r border-gray-200 transition-colors',
                isActive
                  ? 'bg-white text-blue-700 border-b-2 border-b-blue-600 -mb-px'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900',
              ].join(' ')}
              title={sheet.name}
            >
              {sheet.name.length > 22 ? `${sheet.name.slice(0, 20)}…` : sheet.name}
            </button>
          );
        })}
        {canScrollRight && (
          <button
            type="button"
            className="p-2 hover:bg-gray-200 text-gray-500 shrink-0"
            onClick={() => setTabOffset((o) => o + 1)}
            aria-label="Next sheets"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        )}
        <div className="ml-auto flex items-center pr-3 text-[10px] text-gray-400 shrink-0">
          {data.sheets.length} sheet{data.sheets.length !== 1 ? 's' : ''}
        </div>
      </div>

      {/* Grid */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <SheetGrid sheet={currentSheet} />
      </div>

      {/* Footer */}
      <div className="shrink-0 border-t border-gray-100 px-4 py-2 flex items-center justify-between text-[10px] text-gray-400">
        <span>{data.filename}</span>
        <span>{currentSheet.rows.length} row{currentSheet.rows.length !== 1 ? 's' : ''} shown</span>
      </div>
    </div>
  );
}
