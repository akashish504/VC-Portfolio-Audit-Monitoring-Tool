import { useRef, useState } from 'react';
import { FileText, Trash2, Upload, X } from 'lucide-react';
import { toast } from '@/components/ui/sonner';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../ui/dialog';
import { DialogDescription } from '../ui/dialog';

export interface UploadedAuditFile {
  id: string;
  name: string;
  size: string;
  type: string;
  url: string;
  uploadedAt: string;
  reviewPeriod: string;
  entityName: string;
}

interface AuditFileUploadProps {
  companyId: string;
  files: UploadedAuditFile[];
  onFilesChange: (files: UploadedAuditFile[]) => void;
  availableEntities: { id: string; name: string }[];
  availablePeriods: string[];
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function AuditFileUpload({ files, onFilesChange, availableEntities, availablePeriods }: AuditFileUploadProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [selectedPeriod, setSelectedPeriod] = useState('');
  const [selectedEntity, setSelectedEntity] = useState('');
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);

  const handleUploadClick = () => {
    setSelectedPeriod(availablePeriods[0] || '');
    setSelectedEntity(availableEntities[0]?.name || '');
    setPendingFiles([]);
    setDialogOpen(true);
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files;
    if (!selected || selected.length === 0) return;
    setPendingFiles(Array.from(selected));
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleConfirmUpload = () => {
    if (!selectedPeriod || !selectedEntity || pendingFiles.length === 0) {
      toast.error('Please select review period, entity, and at least one file');
      return;
    }

    const newFiles: UploadedAuditFile[] = pendingFiles.map((file) => ({
      id: `audit-file-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      name: file.name,
      size: formatSize(file.size),
      type: file.type,
      url: URL.createObjectURL(file),
      uploadedAt: new Date().toISOString(),
      reviewPeriod: selectedPeriod,
      entityName: selectedEntity,
    }));

    onFilesChange([...newFiles, ...files]);
    toast.success(`${newFiles.length} file(s) uploaded for ${selectedEntity} — ${selectedPeriod}`);
    setDialogOpen(false);
    setPendingFiles([]);
  };

  const handleRemove = (id: string) => {
    onFilesChange(files.filter((f) => f.id !== id));
    toast.success('File removed');
  };

  const removePendingFile = (idx: number) => setPendingFiles((prev) => prev.filter((_, i) => i !== idx));

  return (
    <div className="border border-gray-200 rounded-lg bg-white p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-gray-900 uppercase tracking-wider">Audit Files</h3>
        <button
          type="button"
          onClick={handleUploadClick}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-600 text-white hover:bg-blue-700 transition-colors"
        >
          <Upload className="h-3 w-3" /> Upload Files
        </button>
      </div>

      {files.length === 0 ? (
        <button
          type="button"
          onClick={handleUploadClick}
          className="w-full flex flex-col items-center justify-center py-8 border-2 border-dashed border-gray-200 rounded-lg cursor-pointer hover:border-blue-300 hover:bg-blue-50/30 transition-colors"
        >
          <Upload className="h-6 w-6 text-gray-300 mb-2" />
          <p className="text-sm text-gray-500">Click to upload audit files</p>
        </button>
      ) : (
        <div className="space-y-1">
          {files.map((f) => (
            <div key={f.id} className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-gray-50 group">
              <FileText className="h-4 w-4 text-red-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-gray-900 truncate">{f.name}</p>
                <p className="text-xs text-gray-500">
                  {f.size} · {f.entityName} · {f.reviewPeriod}
                </p>
              </div>
              <button
                type="button"
                onClick={() => handleRemove(f.id)}
                className="text-gray-400 hover:text-red-600 opacity-0 group-hover:opacity-100 transition-all"
                aria-label="Remove file"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Upload Audit Files</DialogTitle>
            <DialogDescription>
              Select the review period, entity, and files to upload.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            <div>
              <label className="text-xs font-medium text-gray-700 block mb-1.5">Review Period *</label>
              <select
                value={selectedPeriod}
                onChange={(e) => setSelectedPeriod(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              >
                {availablePeriods.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-xs font-medium text-gray-700 block mb-1.5">Entity *</label>
              <select
                value={selectedEntity}
                onChange={(e) => setSelectedEntity(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              >
                {availableEntities.map((ent) => (
                  <option key={ent.id} value={ent.name}>
                    {ent.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-xs font-medium text-gray-700 block mb-1.5">Files *</label>
              <input ref={fileInputRef} type="file" multiple onChange={handleFileSelect} className="hidden" />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="w-full flex flex-col items-center justify-center py-6 border-2 border-dashed border-gray-200 rounded-lg cursor-pointer hover:border-blue-300 hover:bg-blue-50/30 transition-colors"
              >
                <Upload className="h-5 w-5 text-gray-300 mb-1" />
                <p className="text-xs text-gray-500">Click to select files</p>
              </button>
              {pendingFiles.length > 0 && (
                <div className="mt-2 space-y-1">
                  {pendingFiles.map((f, idx) => (
                    <div key={idx} className="flex items-center gap-2 text-xs text-gray-700 bg-gray-50 rounded px-2 py-1.5 border border-gray-200">
                      <FileText className="h-3 w-3 text-gray-400 shrink-0" />
                      <span className="truncate flex-1">{f.name}</span>
                      <span className="text-gray-400">{formatSize(f.size)}</span>
                      <button type="button" onClick={() => removePendingFile(idx)} className="text-gray-400 hover:text-red-600" aria-label="Remove pending file">
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <DialogFooter>
            <button
              type="button"
              onClick={() => setDialogOpen(false)}
              className="px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmUpload}
              disabled={!selectedPeriod || !selectedEntity || pendingFiles.length === 0}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
            >
              Upload {pendingFiles.length > 0 ? `(${pendingFiles.length})` : ''}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

