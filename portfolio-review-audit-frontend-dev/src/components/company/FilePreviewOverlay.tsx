import { FileText, Clock, CheckCircle2, AlertTriangle } from 'lucide-react';
import type { EntityFile } from '../../data/mockData';
import { taggedFiles } from '../../data/mockData';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../ui/dialog';

const statusConfig: Record<string, { icon: React.ElementType; badge: string; label: string }> = {
  processed: { icon: CheckCircle2, badge: 'bg-green-100 text-green-800', label: 'Processed' },
  pending: { icon: Clock, badge: 'bg-yellow-100 text-yellow-800', label: 'Pending' },
  error: { icon: AlertTriangle, badge: 'bg-red-100 text-red-800', label: 'Error' },
};

interface FilePreviewOverlayProps {
  file: EntityFile | null;
  open: boolean;
  onClose: () => void;
}

export function FilePreviewOverlay({ file, open, onClose }: FilePreviewOverlayProps) {
  if (!file) return null;

  const taggedFile = taggedFiles.find((tf) => tf.fileName === file.fileName);
  const status = taggedFile ? statusConfig[taggedFile.status] : null;
  const StatusIcon = status?.icon || FileText;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl bg-white max-h-[80vh] overflow-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-gray-900">
            <FileText className="h-5 w-5 text-red-400 shrink-0" />
            <span className="truncate">{file.fileName}</span>
          </DialogTitle>
          <DialogDescription className="flex items-center gap-3 text-xs text-gray-500">
            <span>{file.entityName}</span>
            <span>·</span>
            <span>{file.reviewPeriod}</span>
            {taggedFile && (
              <>
                <span>·</span>
                <span>{taggedFile.size}</span>
                <span>·</span>
                <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${status?.badge || 'bg-gray-100 text-gray-800'}`}>
                  <StatusIcon className="h-3 w-3" />
                  {status?.label || taggedFile.status}
                </span>
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="bg-gray-50 rounded-lg border border-gray-200 min-h-[300px] flex flex-col items-center justify-center gap-4 p-8">
          <FileText className="h-16 w-16 text-gray-300" />
          <div className="text-center space-y-1">
            <p className="text-sm font-medium text-gray-900">{file.fileName}</p>
            {taggedFile && <p className="text-xs text-gray-500">{taggedFile.size}</p>}
          </div>
          <div className="mt-4 grid grid-cols-3 gap-3 w-full max-w-md">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="bg-white border border-gray-200 rounded-md aspect-[3/4] flex items-center justify-center shadow-sm">
                <span className="text-xs text-gray-400">Page {i + 1}</span>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-gray-400 mt-2">Document preview (simulated)</p>
        </div>

        {taggedFile && (
          <div className="grid grid-cols-2 gap-4 text-sm mt-4">
            <div>
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Uploaded</p>
              <p className="text-sm text-gray-900">{new Date(taggedFile.uploadedAt).toLocaleDateString()}</p>
            </div>
            <div>
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">Tagged Entity</p>
              <p className="text-sm text-gray-900">{taggedFile.taggedEntityName || 'Untagged'}</p>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

