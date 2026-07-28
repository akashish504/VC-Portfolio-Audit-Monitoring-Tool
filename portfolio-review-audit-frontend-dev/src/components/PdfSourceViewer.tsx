/**
 * PdfSourceViewer
 *
 * Opens a dialog showing the PDF rendered in-browser (via react-pdf / pdfjs-dist).
 * Automatically jumps to the page where a value was extracted so the user can see
 * the source in context. The extracted text snippet is shown in a small banner above
 * the document; the page itself is rendered plainly (no in-document highlight).
 *
 * Architecture:
 *  • react-pdf renders each page as a <canvas> with an invisible HTML text layer on top
 *    (the text layer is kept so the document text stays selectable/copyable).
 *  • The pdfjs worker is loaded from a CDN so the main bundle stays small.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import { ChevronLeft, ChevronRight, ScanSearch, X } from 'lucide-react';

import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';

import { getFinancialParameterLabel } from '@/constants/financialParameterLabels';

import 'react-pdf/dist/Page/TextLayer.css';
import 'react-pdf/dist/Page/AnnotationLayer.css';

// CDN worker — keeps the app bundle lean.
pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

export interface PdfSourceRef {
  page: number;
  text_snippet: string;
  /** Located value box as page fractions [x0, y0, x1, y1] (top-left origin) — drawn as a highlight. */
  bbox?: [number, number, number, number];
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** Blob URL for the PDF — fetched via apiClient to avoid S3 CORS restrictions. */
  pdfUrl: string;
  /** Dotted field path shown in the header (e.g. "profit_and_loss.revenue.revenue_from_operations"). */
  fieldLabel: string;
  /** Source reference from the backend — page + verbatim text snippet. */
  sourceRef: PdfSourceRef | null;
}

export default function PdfSourceViewer({ open, onClose, pdfUrl, fieldLabel, sourceRef }: Props) {
  const [numPages, setNumPages] = useState<number>(0);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [containerWidth, setContainerWidth] = useState<number>(720);
  const [loadError, setLoadError] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);

  const targetPage = sourceRef?.page ?? 1;
  const snippet = sourceRef?.text_snippet ?? '';

  // Auto-jump to source page whenever dialog opens or ref changes.
  useEffect(() => {
    if (open) {
      setCurrentPage(sourceRef ? Math.max(1, sourceRef.page) : 1);
      setLoadError(null);
    }
  }, [open, sourceRef]);

  // Measure container width so the PDF fills the dialog responsively.
  useEffect(() => {
    if (!open) return;
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        if (w > 0) setContainerWidth(w);
      }
    });
    ro.observe(el);
    // Initial measure after mount.
    setContainerWidth(Math.floor(el.getBoundingClientRect().width) || 720);
    return () => ro.disconnect();
  }, [open]);

  const onDocumentLoadSuccess = useCallback(({ numPages: n }: { numPages: number }) => {
    setNumPages(n);
  }, []);

  const onDocumentLoadError = useCallback((err: Error) => {
    setLoadError(err?.message ?? 'Could not load PDF.');
  }, []);

  const goPrev = () => setCurrentPage((p) => Math.max(1, p - 1));
  const goNext = () => setCurrentPage((p) => Math.min(numPages, p + 1));

  const leafKey = fieldLabel.split('.').pop() ?? fieldLabel;
  const shortLabel = getFinancialParameterLabel(leafKey);

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent
        className="w-[min(100vw-1rem,920px)] max-w-none p-0 gap-0 overflow-hidden rounded-xl shadow-2xl"
        // Allow tall content to scroll inside the dialog, not behind it.
        style={{ maxHeight: '92vh', display: 'flex', flexDirection: 'column' }}
      >
        {/* ── Header ── */}
        <DialogHeader className="shrink-0 px-5 py-3.5 border-b border-gray-100 bg-white">
          <div className="flex items-center gap-2.5 min-w-0">
            <ScanSearch className="h-4 w-4 text-indigo-500 shrink-0" />
            <DialogTitle className="text-sm font-semibold text-gray-900 truncate">
              Source — {shortLabel}
            </DialogTitle>
            <span className="ml-auto shrink-0 text-[11px] text-gray-400 font-mono hidden sm:block truncate max-w-[260px]">
              {fieldLabel}
            </span>
          </div>
        </DialogHeader>

        {/* ── Source snippet banner (plain context — no in-document highlight) ── */}
        {snippet ? (
          <div className="shrink-0 flex items-start gap-3 px-5 py-3 bg-gray-50 border-b border-gray-100">
            <span className="mt-0.5 shrink-0 inline-flex h-5 w-5 items-center justify-center rounded-full bg-gray-200 text-gray-700 text-[10px] font-bold">
              p{targetPage}
            </span>
            <div className="min-w-0">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-500 mb-0.5">
                Extracted from page {targetPage}
              </p>
              <p className="text-sm text-gray-700 font-mono break-words leading-snug">
                {snippet}
              </p>
            </div>
          </div>
        ) : null}

        {/* ── Page navigation bar ── */}
        <div className="shrink-0 flex items-center justify-between px-4 py-1.5 bg-gray-50 border-b border-gray-100">
          <div className="flex items-center gap-1">
            <Button type="button" variant="outline" size="sm" className="h-7 w-7 p-0" onClick={goPrev} disabled={currentPage <= 1}>
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span className="text-xs text-gray-600 px-2 tabular-nums select-none">
              {currentPage} / {numPages || '—'}
            </span>
            <Button type="button" variant="outline" size="sm" className="h-7 w-7 p-0" onClick={goNext} disabled={currentPage >= numPages}>
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
          {targetPage !== currentPage && (
            <button
              type="button"
              className="text-xs text-indigo-600 hover:text-indigo-800 underline underline-offset-2 transition-colors"
              onClick={() => setCurrentPage(targetPage)}
            >
              ↩ Back to source (p.{targetPage})
            </button>
          )}
        </div>

        {/* ── PDF canvas ── */}
        <div ref={containerRef} className="flex-1 overflow-y-auto bg-gray-200 flex justify-center p-4 min-h-0">
          {loadError ? (
            <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
              <X className="h-10 w-10 text-red-300" />
              <p className="text-sm text-red-700 max-w-xs">{loadError}</p>
              <p className="text-xs text-gray-500">
                The document could not be loaded. Close and try again.
              </p>
            </div>
          ) : !pdfUrl ? (
            <div className="flex items-center justify-center py-24">
              <span className="text-sm text-gray-500 animate-pulse">Preparing document…</span>
            </div>
          ) : (
            <Document
              file={pdfUrl}
              onLoadSuccess={onDocumentLoadSuccess}
              onLoadError={onDocumentLoadError}
              loading={
                <div className="flex items-center justify-center py-24">
                  <span className="text-sm text-gray-500 animate-pulse">Loading document…</span>
                </div>
              }
              error={
                <div className="text-sm text-red-600 py-12 text-center px-4">
                  Failed to load document. The presigned URL may have expired.
                </div>
              }
            >
              <div className="relative inline-block">
                <Page
                  pageNumber={currentPage}
                  width={Math.min(containerWidth - 32, 840)}
                  renderTextLayer
                  renderAnnotationLayer={false}
                  className="shadow-lg rounded overflow-hidden"
                />
                {/* In-document highlight intentionally disabled — the page renders plainly
                    (the source page + snippet banner above still locate the value). */}
              </div>
            </Document>
          )}
        </div>

        {/* ── Footer ── */}
        <div className="shrink-0 px-5 py-2 border-t border-gray-100 bg-white flex items-center justify-between gap-4">
          <p className="text-[11px] text-gray-400 leading-relaxed">
            Showing page {targetPage}, where this value was extracted. Use the page arrows to browse the rest of the document.
          </p>
          <Button type="button" variant="outline" size="sm" className="h-7 text-xs shrink-0" onClick={onClose}>
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
