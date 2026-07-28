import { useEffect, useRef } from 'react';
import { FileText } from 'lucide-react';
import type { SourceReference } from '../../data/mockData';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../ui/dialog';

interface FilePageViewerProps {
  sourceRef: SourceReference | null;
  fieldName: string;
  open: boolean;
  onClose: () => void;
}

const TOTAL_PAGES = 42;

export function FilePageViewer({ sourceRef, fieldName, open, onClose }: FilePageViewerProps) {
  const targetRef = useRef<HTMLDivElement | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open || !targetRef.current) return;
    const timer = setTimeout(() => {
      const target = targetRef.current;
      const container = scrollContainerRef.current;
      if (!target) return;
      if (container) {
        const targetTop = target.offsetTop - container.offsetTop;
        container.scrollTo({ top: Math.max(targetTop - 16, 0), behavior: 'auto' });
      } else {
        target.scrollIntoView({ behavior: 'auto', block: 'start' });
      }
    }, 150);
    return () => clearTimeout(timer);
  }, [open, sourceRef?.fileId, sourceRef?.page]);

  if (!sourceRef) return null;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5 text-red-400 shrink-0" />
            <span className="truncate">{sourceRef.fileName}</span>
          </DialogTitle>
          <DialogDescription className="text-xs text-gray-500">
            Showing source for <span className="font-medium text-gray-900">{fieldName}</span> — Page {sourceRef.page}
          </DialogDescription>
        </DialogHeader>

        <div ref={scrollContainerRef} className="flex-1 min-h-0 bg-gray-50 rounded-lg border border-gray-200 overflow-auto">
          <div className="flex flex-col items-center gap-4 p-6">
            {Array.from({ length: TOTAL_PAGES }, (_, i) => {
              const page = i + 1;
              const isTarget = page === sourceRef.page;
              return (
                <div
                  key={page}
                  ref={isTarget ? targetRef : undefined}
                  className={[
                    'bg-white border rounded-md shadow-sm w-full max-w-md aspect-[3/4] flex flex-col items-center justify-center gap-3 relative transition-all',
                    isTarget ? 'border-blue-500 ring-2 ring-blue-100' : 'border-gray-200',
                  ].join(' ')}
                >
                  <div className="absolute top-3 right-3 text-[10px] text-gray-400 font-mono">Page {page}</div>
                  <FileText className="h-10 w-10 text-gray-300" />
                  <p className="text-xs text-gray-500">
                    Page {page} of {TOTAL_PAGES}
                  </p>
                  {isTarget && (
                    <div className="mt-2 px-3 py-1.5 bg-blue-50 border border-blue-200 rounded text-xs text-blue-700 font-medium">
                      ✦ {fieldName} value extracted from this page
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

