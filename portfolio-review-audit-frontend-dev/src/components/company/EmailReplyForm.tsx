import { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Paperclip, Plus, Send, X } from 'lucide-react';
import { toast } from 'sonner';
import _JoditEditor from 'jodit-react';
const JoditEditor = (_JoditEditor as any).default ?? _JoditEditor;
import DOMPurify from 'dompurify';
import 'jodit/es5/jodit.min.css';

import { type ApiEmailMessage, type ApiEmailThread, sendEmail } from '@/api/emailThreads';

const PORTFOLIO_REVIEW_EMAIL = 'portfolioreview@peakxv.com';
const MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024; // 1 MB
const MAX_FILES = 10;

interface Props {
  thread: ApiEmailThread;
  originalEmail: ApiEmailMessage;
  portfolioCompanyId: number;
  pocEmails?: string[];
  pocCcEmails?: string[];
  suggestedEmails?: string[];
  onClose: () => void;
  onSuccess: () => void;
}

export function EmailReplyForm({ thread, originalEmail, portfolioCompanyId, pocEmails = [], pocCcEmails = [], suggestedEmails = [], onClose, onSuccess }: Props) {
  // --- recipients ---
  const [toEmails, setToEmails] = useState<string[]>([]);
  const [ccEmails, setCcEmails] = useState<string[]>([]);
  const [showToInput, setShowToInput] = useState(false);
  const [showCcInput, setShowCcInput] = useState(false);
  const [newToEmail, setNewToEmail] = useState('');
  const [newCcEmail, setNewCcEmail] = useState('');
  const [toWarning, setToWarning] = useState<string | null>(null);

  // --- autocomplete ---
  const [toSuggestions, setToSuggestions] = useState<string[]>([]);
  const [ccSuggestions, setCcSuggestions] = useState<string[]>([]);
  const [toActiveIndex, setToActiveIndex] = useState(-1);
  const [ccActiveIndex, setCcActiveIndex] = useState(-1);

  // --- compose ---
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [attachments, setAttachments] = useState<File[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const editorRef = useRef<any>(null);

  // Build email pool from thread history + stored company contacts for autocomplete
  const emailPool = useMemo(() => {
    const pool = new Set<string>();
    for (const msg of thread.emails) {
      (msg.recipients || []).forEach((e) => e && pool.add(e));
      (msg.cc || []).forEach((e) => e && pool.add(e));
      if (msg.sender) pool.add(msg.sender);
    }
    pocEmails.forEach((e) => e && pool.add(e));
    pocCcEmails.forEach((e) => e && pool.add(e));
    suggestedEmails.forEach((e) => e && pool.add(e));
    return Array.from(pool).sort((a, b) => a.localeCompare(b));
  }, [thread, pocEmails, pocCcEmails, suggestedEmails]);

  // Pre-fill To/CC/Subject when originalEmail changes
  useEffect(() => {
    const originalSender = originalEmail.sender || '';
    const toSet = new Set<string>([originalSender].filter(Boolean));

    const ccSet = new Set<string>((originalEmail.cc || []).filter(Boolean));
    if (!ccSet.has(PORTFOLIO_REVIEW_EMAIL)) ccSet.add(PORTFOLIO_REVIEW_EMAIL);
    // Remove any CC that is already in To
    toSet.forEach((e) => ccSet.delete(e));

    setToEmails(Array.from(toSet));
    setCcEmails(Array.from(ccSet));

    const rawSubject = thread.subject || '';
    setSubject(rawSubject.startsWith('Re: ') ? rawSubject : `Re: ${rawSubject}`);
    setBody('');
  }, [originalEmail, thread.subject]);

  // Build autocomplete suggestions as user types
  useEffect(() => {
    const q = newToEmail.trim().toLowerCase();
    if (q) {
      const already = new Set(toEmails.map((e) => e.toLowerCase()));
      setToSuggestions(emailPool.filter((e) => e.toLowerCase().startsWith(q) && !already.has(e.toLowerCase())).slice(0, 8));
      setToActiveIndex(-1);
    } else {
      setToSuggestions([]);
    }
  }, [newToEmail, emailPool, toEmails]);

  useEffect(() => {
    const q = newCcEmail.trim().toLowerCase();
    if (q) {
      const already = new Set(ccEmails.map((e) => e.toLowerCase()));
      setCcSuggestions(emailPool.filter((e) => e.toLowerCase().startsWith(q) && !already.has(e.toLowerCase())).slice(0, 8));
      setCcActiveIndex(-1);
    } else {
      setCcSuggestions([]);
    }
  }, [newCcEmail, emailPool, ccEmails]);

  // --- recipient helpers ---
  const emailRegex = /^[\w.!#$%&'*+/=?^`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

  const addTo = (value?: string) => {
    const candidate = (value ?? newToEmail).trim();
    if (candidate && emailRegex.test(candidate) && !toEmails.map((e) => e.toLowerCase()).includes(candidate.toLowerCase())) {
      setToEmails((prev) => [...prev, candidate]);
    }
    setNewToEmail('');
    setShowToInput(false);
    setToSuggestions([]);
  };

  const addCc = (value?: string) => {
    const candidate = (value ?? newCcEmail).trim();
    if (candidate && emailRegex.test(candidate) && !ccEmails.map((e) => e.toLowerCase()).includes(candidate.toLowerCase())) {
      setCcEmails((prev) => [...prev, candidate]);
    }
    setNewCcEmail('');
    setShowCcInput(false);
    setCcSuggestions([]);
  };

  const removeTo = (index: number) => {
    if (toEmails.length <= 1) {
      setToWarning("At least one 'To' recipient is required.");
      setTimeout(() => setToWarning(null), 2500);
      return;
    }
    setToEmails((prev) => prev.filter((_, i) => i !== index));
  };

  const removeCc = (index: number) => setCcEmails((prev) => prev.filter((_, i) => i !== index));

  // --- keyboard navigation for autocomplete ---
  const onToKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (toSuggestions.length === 0) {
      if (e.key === 'Enter') { e.preventDefault(); addTo(); }
      if (e.key === 'Escape') { setShowToInput(false); setNewToEmail(''); }
      return;
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); setToActiveIndex((i) => Math.min(i + 1, toSuggestions.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setToActiveIndex((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); addTo(toActiveIndex >= 0 ? toSuggestions[toActiveIndex] : newToEmail); }
    else if (e.key === 'Escape') { setToSuggestions([]); setToActiveIndex(-1); }
  };

  const onCcKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (ccSuggestions.length === 0) {
      if (e.key === 'Enter') { e.preventDefault(); addCc(); }
      if (e.key === 'Escape') { setShowCcInput(false); setNewCcEmail(''); }
      return;
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); setCcActiveIndex((i) => Math.min(i + 1, ccSuggestions.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setCcActiveIndex((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); addCc(ccActiveIndex >= 0 ? ccSuggestions[ccActiveIndex] : newCcEmail); }
    else if (e.key === 'Escape') { setCcSuggestions([]); setCcActiveIndex(-1); }
  };

  // --- file helpers ---
  const addFiles = (incoming: File[]) => {
    if (attachments.length + incoming.length > MAX_FILES) {
      setError(`Maximum ${MAX_FILES} files allowed. Currently ${attachments.length} attached.`);
      return;
    }
    const valid: File[] = [];
    for (const f of incoming) {
      if (f.size > MAX_FILE_SIZE_BYTES) { setError(`"${f.name}" exceeds 1 MB limit.`); continue; }
      if (attachments.some((x) => x.name === f.name && x.size === f.size)) { setError(`"${f.name}" is already attached.`); continue; }
      valid.push(f);
    }
    if (valid.length) { setAttachments((prev) => [...prev, ...valid]); setError(null); }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    addFiles(Array.from(e.target.files || []));
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  // Insert images as base64 data URLs at cursor position inside Jodit
  const insertImagesAtCursor = async (files: File[]) => {
    if (!editorRef.current || files.length === 0) return;
    const toDataURL = (f: File) => new Promise<string>((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result));
      r.onerror = reject;
      r.readAsDataURL(f);
    });
    try {
      const urls = await Promise.all(files.map(toDataURL));
      const html = urls.map((src) => `<img src="${src}" alt="image" style="max-width:100%;height:auto;margin:4px;" />`).join('');
      if (typeof editorRef.current.execCommand === 'function') {
        editorRef.current.execCommand('insertHTML', false, html);
      } else {
        editorRef.current.selection?.insertHTML(html);
      }
    } catch {
      addFiles(files);
    }
  };

  // --- submit ---
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!body.trim()) { setError('Please enter a reply message.'); return; }
    if (toEmails.length === 0) { setError('At least one To recipient is required.'); return; }

    setIsSubmitting(true);
    setError(null);
    try {
      await sendEmail({
        portfolio_company_id: portfolioCompanyId,
        to_addrs: toEmails,
        cc_addrs: ccEmails,
        subject,
        body_html: DOMPurify.sanitize(body),
        thread_id: originalEmail.thread_id || undefined,
        reply_to_message_id: originalEmail.id,
        attachments,
      });
      toast.success('Reply sent');
      onSuccess();
      onClose();
    } catch (err: any) {
      const msg = err?.response?.data?.detail?.message || 'Failed to send reply. Please try again.';
      setError(msg);
      toast.error(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const totalSize = attachments.reduce((s, f) => s + f.size, 0);
  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
  };

  return (
    <div className="mt-3 relative">
      {/* Connector line */}
      <div className="absolute -top-3 left-6 w-px h-3 bg-gray-300" />
      <div className="bg-white rounded-lg border border-gray-200 p-4 shadow-sm">
        <div className="flex justify-between items-center mb-4">
          <h4 className="text-sm font-semibold text-gray-900">Reply</h4>
          <button
            type="button"
            onClick={onClose}
            disabled={isSubmitting}
            className="p-1 hover:bg-gray-100 rounded-full transition-colors"
          >
            <X size={14} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          {/* To */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">To:</label>
            <div className="flex flex-wrap gap-1.5 min-h-[36px] p-2 border border-gray-200 rounded-md bg-gray-50">
              {toEmails.map((email, i) => (
                <span key={i} className="inline-flex items-center px-2 py-0.5 bg-blue-100 text-blue-800 text-xs rounded-full">
                  {email}
                  {toEmails.length > 1 && (
                    <button type="button" onClick={() => removeTo(i)} className="ml-1 text-blue-500 hover:text-blue-700">
                      <X size={10} />
                    </button>
                  )}
                </span>
              ))}
              {toWarning && <span className="text-xs text-red-500 w-full">{toWarning}</span>}
              {showToInput ? (
                <div className="relative inline-flex items-center gap-1">
                  <input
                    type="email"
                    autoFocus
                    value={newToEmail}
                    onChange={(e) => setNewToEmail(e.target.value)}
                    onKeyDown={onToKeyDown}
                    placeholder="Add email"
                    className="px-2 py-0.5 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
                  />
                  <button type="button" onClick={() => addTo()} className="text-green-600 hover:text-green-800"><Plus size={12} /></button>
                  <button type="button" onClick={() => { setShowToInput(false); setNewToEmail(''); setToSuggestions([]); }} className="text-gray-400 hover:text-gray-600"><X size={12} /></button>
                  {toSuggestions.length > 0 && (
                    <ul className="absolute top-full left-0 mt-1 w-60 bg-white border border-gray-200 rounded shadow-lg z-20 max-h-40 overflow-auto">
                      {toSuggestions.map((s, i) => (
                        <li
                          key={s}
                          onMouseDown={() => addTo(s)}
                          className={`px-3 py-1.5 text-xs cursor-pointer ${i === toActiveIndex ? 'bg-blue-50 text-blue-800' : 'hover:bg-gray-50'}`}
                        >
                          {s}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                <button type="button" onClick={() => setShowToInput(true)} className="text-xs text-blue-600 hover:text-blue-800">
                  + Add
                </button>
              )}
            </div>
          </div>

          {/* CC */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">CC:</label>
            <div className="flex flex-wrap gap-1.5 min-h-[36px] p-2 border border-gray-200 rounded-md bg-gray-50">
              {ccEmails.map((email, i) => (
                <span key={i} className="inline-flex items-center px-2 py-0.5 bg-gray-200 text-gray-700 text-xs rounded-full">
                  {email}
                  <button type="button" onClick={() => removeCc(i)} className="ml-1 text-gray-500 hover:text-gray-700"><X size={10} /></button>
                </span>
              ))}
              {showCcInput ? (
                <div className="relative inline-flex items-center gap-1">
                  <input
                    type="email"
                    autoFocus
                    value={newCcEmail}
                    onChange={(e) => setNewCcEmail(e.target.value)}
                    onKeyDown={onCcKeyDown}
                    placeholder="Add CC email"
                    className="px-2 py-0.5 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-400"
                  />
                  <button type="button" onClick={() => addCc()} className="text-green-600 hover:text-green-800"><Plus size={12} /></button>
                  <button type="button" onClick={() => { setShowCcInput(false); setNewCcEmail(''); setCcSuggestions([]); }} className="text-gray-400 hover:text-gray-600"><X size={12} /></button>
                  {ccSuggestions.length > 0 && (
                    <ul className="absolute top-full left-0 mt-1 w-60 bg-white border border-gray-200 rounded shadow-lg z-20 max-h-40 overflow-auto">
                      {ccSuggestions.map((s, i) => (
                        <li
                          key={s}
                          onMouseDown={() => addCc(s)}
                          className={`px-3 py-1.5 text-xs cursor-pointer ${i === ccActiveIndex ? 'bg-blue-50 text-blue-800' : 'hover:bg-gray-50'}`}
                        >
                          {s}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                <button type="button" onClick={() => setShowCcInput(true)} className="text-xs text-blue-600 hover:text-blue-800">
                  + Add CC
                </button>
              )}
            </div>
          </div>

          {/* Subject */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Subject:</label>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={isSubmitting}
              className="w-full px-3 py-1.5 text-xs border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-400"
            />
          </div>

          {/* Body */}
          <div className={isSubmitting ? 'opacity-60 pointer-events-none' : ''}>
            <JoditEditor
              ref={editorRef}
              value={body}
              onBlur={(content: string) => setBody(content)}
              config={{
                readonly: isSubmitting,
                height: 260,
                placeholder: 'Type your reply here...',
                toolbarSticky: false,
                askBeforePasteHTML: false,
                askBeforePasteFromWord: false,
                defaultActionOnPaste: 'insert_as_html',
                enableDragAndDropFileToEditor: true,
                uploader: {
                  insertImageAsBase64URI: true,
                  imagesExtensions: ['jpg', 'png', 'jpeg', 'gif', 'svg', 'webp'],
                },
                events: {
                  drop: (e: DragEvent) => {
                    if (!e.dataTransfer) return;
                    const files = Array.from(e.dataTransfer.files || []);
                    if (files.length === 0) return;
                    e.preventDefault();
                    e.stopPropagation();
                    const images = files.filter((f) => f.type.startsWith('image/'));
                    if (images.length === files.length) {
                      insertImagesAtCursor(images);
                    } else {
                      addFiles(files);
                    }
                  },
                  paste: (e: ClipboardEvent) => {
                    const files = Array.from(e.clipboardData?.files || []);
                    if (files.length > 0) {
                      const nonImages = files.filter((f) => !f.type.startsWith('image/'));
                      if (nonImages.length > 0) {
                        e.preventDefault();
                        e.stopPropagation();
                        addFiles(files);
                      }
                      // pure images: let Jodit handle naturally (insertImageAsBase64URI)
                    }
                  },
                } as any,
              }}
            />
          </div>

          {/* Attachments */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Attachments</label>
            <label className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-300 rounded-md cursor-pointer transition-colors ${attachments.length >= MAX_FILES ? 'bg-gray-100 text-gray-400 cursor-not-allowed' : 'bg-white hover:bg-gray-50 text-gray-700'}`}>
              <Paperclip size={12} />
              {attachments.length >= MAX_FILES ? 'Max files reached' : 'Choose files'}
              <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleFileChange} disabled={isSubmitting || attachments.length >= MAX_FILES} />
            </label>
            <span className="ml-2 text-xs text-gray-400">Max 1 MB / file, {MAX_FILES} files</span>

            {attachments.length > 0 && (
              <div className="mt-2 space-y-1">
                <div className="text-xs text-gray-500">{attachments.length}/{MAX_FILES} files ({formatSize(totalSize)})</div>
                {attachments.map((f, i) => (
                  <div key={i} className="flex items-center justify-between p-1.5 bg-gray-50 border border-gray-200 rounded-md">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <Paperclip size={11} className="text-gray-400 shrink-0" />
                      <span className="text-xs text-gray-700 truncate">{f.name}</span>
                      <span className="text-xs text-gray-400 shrink-0">({formatSize(f.size)})</span>
                    </div>
                    <button type="button" onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))} disabled={isSubmitting} className="ml-2 text-red-400 hover:text-red-600 shrink-0">
                      <X size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {error && (
            <div className="p-2 bg-red-50 border border-red-200 rounded-md">
              <p className="text-xs text-red-600">{error}</p>
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <button
              type="submit"
              disabled={isSubmitting || !body.trim()}
              className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white text-xs font-medium rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus:outline-none focus:ring-2 focus:ring-offset-1 focus:ring-blue-500"
            >
              {isSubmitting ? <><Loader2 size={12} className="animate-spin" /> Sending…</> : <><Send size={12} /> Send Reply</>}
            </button>
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="px-4 py-2 text-xs font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
