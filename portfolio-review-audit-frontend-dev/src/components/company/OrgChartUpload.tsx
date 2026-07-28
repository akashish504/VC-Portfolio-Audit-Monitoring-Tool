import { useRef, useState } from 'react';
import { Upload, RefreshCw, FileImage, FileText, FileSpreadsheet, Download, X } from 'lucide-react';
import { toast } from '@/components/ui/sonner';
import { uploadOrgChartAndExtract } from '@/api/fileProcessing';
import { fetchFileAsBlobUrl, getFileDownloadUrl } from '@/api/portfolio';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

interface OrgChartUploadProps {
  companyId: number;
  onFileUploaded: (file: File, url: string, fileId: number) => void;
  uploadedFile?: { name: string; url: string; type: string; fileId?: number } | null;
  /** Called when user removes the org chart (may be async — server clears entities + file). */
  onClear: () => void | Promise<void>;
}

function OrgChartFileViewer({ file }: { file: { name: string; url: string; type: string; fileId?: number } }) {
  const [downloading, setDownloading] = useState(false);
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  const isPdf = file.type === 'application/pdf' || ext === '.pdf';
  const isImage = ['image/png', 'image/jpeg', 'image/svg+xml', 'image/webp'].includes(file.type) ||
    ['.png', '.jpg', '.jpeg', '.svg', '.webp'].includes(ext);
  const isDocx = ['.docx', '.doc'].includes(ext);
  const isXlsx = ['.xlsx', '.xls'].includes(ext);

  const handleDownload = async () => {
    if (!file.fileId) return;
    setDownloading(true);
    try {
      // Get the filename from metadata, then fetch bytes through the authenticated
      // stream endpoint. Using a blob URL guarantees the browser honours the
      // `download` attribute — cross-origin presigned S3 URLs are ignored by browsers.
      const [meta, { blobUrl }] = await Promise.all([
        getFileDownloadUrl(file.fileId),
        fetchFileAsBlobUrl(file.fileId),
      ]);
      const a = document.createElement('a');
      a.href = blobUrl;
      a.download = meta.file_name || file.name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
    } catch {
      toast.error('Could not download file. Try again.');
    } finally {
      setDownloading(false);
    }
  };

  const downloadButton = (
    <button
      type="button"
      onClick={() => void handleDownload()}
      disabled={downloading || !file.fileId}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:pointer-events-none"
    >
      <Download className="h-3.5 w-3.5" />
      {downloading ? 'Downloading…' : `Download ${file.name}`}
    </button>
  );

  if (isPdf || isDocx) {
    return (
      <div className="space-y-2">
        <div className="border border-gray-200 rounded-lg overflow-hidden bg-gray-50">
          <iframe src={file.url} className="w-full h-[500px]" title="Org Chart Document" />
        </div>
        <div className="flex justify-end">{downloadButton}</div>
      </div>
    );
  }

  if (isImage) {
    return (
      <div className="space-y-2">
        <div className="border border-gray-200 rounded-lg overflow-hidden bg-gray-50 flex items-center justify-center">
          <img src={file.url} alt="Org Chart" className="max-w-full max-h-[500px] object-contain" />
        </div>
        <div className="flex justify-end">{downloadButton}</div>
      </div>
    );
  }

  if (isXlsx) {
    return (
      <div className="border border-gray-200 rounded-lg bg-gray-50 p-6 flex flex-col items-center gap-3">
        <FileSpreadsheet className="h-10 w-10 text-green-600" />
        <p className="text-sm text-gray-600">Excel file — preview not available in browser.</p>
        {downloadButton}
      </div>
    );
  }

  return (
    <div className="border border-gray-200 rounded-lg bg-gray-50 p-6 flex flex-col items-center gap-3">
      <FileText className="h-10 w-10 text-gray-400" />
      {downloadButton}
    </div>
  );
}

export function OrgChartUpload({ companyId, onFileUploaded, uploadedFile, onClear }: OrgChartUploadProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [confirmRemoveOpen, setConfirmRemoveOpen] = useState(false);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const allowedMime = ['image/png', 'image/jpeg', 'image/svg+xml', 'image/webp', 'application/pdf'];
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    const isDocx = ['.docx', '.doc'].includes(ext);
    const isXlsx = ['.xlsx', '.xls'].includes(ext);
    const isPptx = ['.pptx', '.ppt'].includes(ext);
    if (!allowedMime.includes(file.type) && !isDocx && !isXlsx && !isPptx) {
      toast.error('Please upload an image (PNG, JPG, SVG, WebP), PDF, Word (DOCX), Excel (XLSX), or PowerPoint (PPTX)');
      return;
    }

    try {
      setUploading(true);

      const resp = await uploadOrgChartAndExtract(companyId, file);
      const url = resp.download_url || URL.createObjectURL(file);
      onFileUploaded(file, url, resp.file_id);
      const convertedNote = isDocx ? ' (converted to PDF)' : isXlsx ? ' (Excel file)' : '';
      toast.success(`Org chart "${file.name}" uploaded${convertedNote} and extraction started`);
    } catch (err) {
      console.error(err);
      toast.error('Upload failed. Check API/S3 config and try again.');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleConfirmedRemove = async () => {
    try {
      setClearing(true);
      await Promise.resolve(onClear());
    } finally {
      setClearing(false);
    }
  };

  return (
    <>
      <AlertDialog open={confirmRemoveOpen} onOpenChange={setConfirmRemoveOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove org chart?</AlertDialogTitle>
            <AlertDialogDescription>
              This will delete the org chart file and all entities extracted from it. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 hover:bg-red-700 text-white"
              onClick={() => void handleConfirmedRemove()}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <div className="border border-gray-200 rounded-lg bg-white p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-semibold text-gray-900 uppercase tracking-wider">Org Chart Document</h3>
          <div className="flex items-center gap-2">
            {uploadedFile && (
              <button
                type="button"
                disabled={clearing || uploading}
                onClick={() => setConfirmRemoveOpen(true)}
                className="flex items-center gap-1 text-xs text-gray-500 hover:text-red-500 transition-colors disabled:opacity-50"
              >
                <X className="h-3 w-3" /> {clearing ? 'Removing…' : 'Remove'}
              </button>
            )}
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-blue-500 hover:bg-blue-600 rounded-lg transition-colors disabled:opacity-50 disabled:pointer-events-none"
            >
              {uploadedFile ? <RefreshCw className="h-3 w-3" /> : <Upload className="h-3 w-3" />}
              {uploading ? 'Uploading…' : uploadedFile ? 'Re-upload' : 'Upload'}
            </button>
          </div>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/svg+xml,image/webp,application/pdf,.docx,.doc,.xlsx,.xls,.pptx,.ppt"
          onChange={handleFileChange}
          className="hidden"
        />

        {uploadedFile ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <FileImage className="h-3.5 w-3.5" />
              <span className="truncate">{uploadedFile.name}</span>
            </div>
            <OrgChartFileViewer file={uploadedFile} />
          </div>
        ) : (
          <div
            onClick={() => fileInputRef.current?.click()}
            className="flex flex-col items-center justify-center py-12 border-2 border-dashed border-gray-200 rounded-lg cursor-pointer hover:border-blue-300 hover:bg-blue-50/30 transition-colors"
          >
            <Upload className="h-8 w-8 text-gray-300 mb-2" />
            <p className="text-sm text-gray-500">Click to upload org chart</p>
            <p className="text-xs text-gray-400 mt-1">PNG, JPG, SVG, WebP, PDF, DOCX, XLSX, or PPTX</p>
          </div>
        )}
      </div>
    </>
  );
}
