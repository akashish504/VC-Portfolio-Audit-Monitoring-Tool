import { useEffect, useMemo, useState } from 'react';
import { FileText, Paperclip, Plus, RefreshCcw, Save, Send, X } from 'lucide-react';
import JoditEditorImport from 'jodit-react';
// jodit-react ships a CJS UMD bundle; Vite may resolve it as a namespace object
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const JoditEditor: any = (JoditEditorImport as any).default ?? JoditEditorImport;
import 'jodit/es5/jodit.min.css';
import { useAppState } from '../../context/AppContext';
import { toast } from '@/components/ui/sonner';
import { getCompanyDraftEmail, generateCompanyDraftEmail, sendCompanyDraftEmail, updateCompanyDraftEmail, type ApiDraftEmail } from '@/api/emailDrafts';
import { listActiveEmailTemplates, type ApiEmailTemplate } from '@/api/emailTemplates';
import { presignEmailAttachment, uploadEmailAttachments } from '@/api/emailAttachments';
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

export function CompanyEmailDraft({ companyId }: { companyId: string }) {
  const { companies } = useAppState();
  const company = companies.find((c) => c.id === companyId);

  const portfolioCompanyId = useMemo(() => {
    const raw = String(companyId);
    const m = raw.match(/^pc-(\d+)$/);
    if (m) return Number(m[1]);
    if (/^\d+$/.test(raw)) return Number(raw);
    return NaN;
  }, [companyId]);

  const [draft, setDraft] = useState<ApiDraftEmail | null>(null);
  const [loading, setLoading] = useState(false);
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [toEmails, setToEmails] = useState<string[]>(company?.contactEmail ? [company.contactEmail] : []);
  const [ccEmails, setCcEmails] = useState<string[]>([]);
  const [showToInput, setShowToInput] = useState(false);
  const [showCcInput, setShowCcInput] = useState(false);
  const [newToEmail, setNewToEmail] = useState('');
  const [newCcEmail, setNewCcEmail] = useState('');
  const [attachmentKeys, setAttachmentKeys] = useState<string[]>([]);
  const [attachmentsId, setAttachmentsId] = useState<string>('');
  const [newFiles, setNewFiles] = useState<File[]>([]);
  const [confirmSendOpen, setConfirmSendOpen] = useState(false);
  const [activeTemplates, setActiveTemplates] = useState<ApiEmailTemplate[]>([]);
  const [selectedTemplateName, setSelectedTemplateName] = useState('');

  const templateNameOptions = useMemo(() => {
    const names = new Set<string>();
    for (const t of activeTemplates) {
      if (t.template_name) names.add(t.template_name);
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [activeTemplates]);

  const joditConfig = useMemo(
    () => ({
      readonly: false,
      height: 320,
      toolbarAdaptive: false,
    }),
    [],
  );

  /** True when the dropdown picks a different template than the one this draft was built from. */
  const regenerateSelectionDiffersFromDraft = useMemo(() => {
    if (!draft) return false;
    const used = (draft.template_name ?? '').trim();
    const sel = selectedTemplateName.trim();
    return Boolean(sel) && sel !== used;
  }, [draft, draft?.template_name, draft?.id, selectedTemplateName]);

  // Load active templates + existing draft (if any).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (cancelled) return;
        if (!Number.isFinite(portfolioCompanyId)) return;
        setLoading(true);
        const [tplRows, existing] = await Promise.all([listActiveEmailTemplates(), getCompanyDraftEmail(portfolioCompanyId)]);
        if (cancelled) return;
        setActiveTemplates(tplRows);
        const names = [...new Set(tplRows.map((t) => t.template_name).filter(Boolean))].sort((a, b) =>
          String(a).localeCompare(String(b)),
        ) as string[];
        let pick = '';
        if (existing?.template_name && names.includes(existing.template_name)) pick = existing.template_name;
        else if (names.includes('Discrepancy Template')) pick = 'Discrepancy Template';
        else pick = names[0] ?? '';
        setSelectedTemplateName(pick);
        setDraft(existing);
        setSubject(existing?.subject ?? '');
        setBody(existing?.email_body ?? '');
        setAttachmentKeys((existing?.attachments ?? []).filter(Boolean) as string[]);
        setAttachmentsId((existing?.attachments_id ?? '') as string);
        const toList = (existing?.to_add ?? []).filter(Boolean);
        const ccList = (existing?.cc ?? []).filter(Boolean);
        setToEmails(toList.length ? toList : company?.contactEmail ? [company.contactEmail] : []);
        setCcEmails(ccList);
      } catch {
        toast.error('Failed to load draft');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [portfolioCompanyId, company?.contactEmail]);

  const generateDraft = async (overwrite: boolean) => {
    if (!Number.isFinite(portfolioCompanyId)) {
      toast.error('Invalid company id');
      return;
    }
    if (!selectedTemplateName.trim()) {
      toast.error('Select an email template');
      return;
    }
    setLoading(true);
    try {
      const row = await generateCompanyDraftEmail({
        portfolio_company_id: portfolioCompanyId,
        overwrite,
        template_name: selectedTemplateName.trim(),
      });
      setDraft(row);
      if (row.template_name) setSelectedTemplateName(row.template_name);
      setSubject(row.subject ?? '');
      setBody(row.email_body ?? '');
      setAttachmentKeys((row.attachments ?? []).filter(Boolean) as string[]);
      setAttachmentsId((row.attachments_id ?? '') as string);
      setNewFiles([]);
      const toList = (row.to_add ?? []).filter(Boolean);
      const ccList = (row.cc ?? []).filter(Boolean);
      setToEmails(toList.length ? toList : company?.contactEmail ? [company.contactEmail] : []);
      setCcEmails(ccList);
      toast.success(overwrite ? 'Draft regenerated' : 'Draft generated');
    } catch (e: any) {
      const msg = e?.response?.data?.detail?.message || 'Failed to generate draft';
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  };

  const saveDraft = async () => {
    if (!draft?.id) return;
    setLoading(true);
    try {
      let nextKeys = attachmentKeys;
      let nextAttachmentsId = attachmentsId;
      if (newFiles.length > 0) {
        const uploaded = await uploadEmailAttachments({ files: newFiles, attachments_id: nextAttachmentsId || undefined });
        nextAttachmentsId = uploaded.attachments_id;
        nextKeys = [...nextKeys, ...(uploaded.attachment_keys || [])];
      }
      const row = await updateCompanyDraftEmail(draft.id, {
        subject,
        email_body: body,
        to_add: toEmails,
        cc: ccEmails,
        attachments: nextKeys,
        attachments_id: nextAttachmentsId || undefined,
      });
      setDraft(row);
      setAttachmentKeys((row.attachments ?? []).filter(Boolean) as string[]);
      setAttachmentsId((row.attachments_id ?? '') as string);
      setNewFiles([]);
      toast.success('Draft saved');
    } catch (e: any) {
      const msg = e?.response?.data?.detail?.message || 'Failed to save draft';
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  };

  const sendDraft = async () => {
    if (!draft?.id) return;
    setLoading(true);
    try {
      // Ensure any pending attachments are uploaded and the draft is persisted
      // before we queue the send operation.
      let nextKeys = attachmentKeys;
      let nextAttachmentsId = attachmentsId;
      if (newFiles.length > 0) {
        const uploaded = await uploadEmailAttachments({ files: newFiles, attachments_id: nextAttachmentsId || undefined });
        nextAttachmentsId = uploaded.attachments_id;
        nextKeys = [...nextKeys, ...(uploaded.attachment_keys || [])];
      }
      const row = await updateCompanyDraftEmail(draft.id, {
        subject,
        email_body: body,
        to_add: toEmails,
        cc: ccEmails,
        attachments: nextKeys,
        attachments_id: nextAttachmentsId || undefined,
      });
      setDraft(row);
      setAttachmentKeys((row.attachments ?? []).filter(Boolean) as string[]);
      setAttachmentsId((row.attachments_id ?? '') as string);
      setNewFiles([]);

      await sendCompanyDraftEmail(draft.id);
      toast.success('Email send queued');
    } catch (e: any) {
      const msg = e?.response?.data?.detail?.message || 'Failed to queue email';
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  };

  const openAttachment = async (key: string) => {
    try {
      const res = await presignEmailAttachment(key);
      window.open(res.url, '_blank', 'noopener,noreferrer');
    } catch {
      toast.error('Failed to open attachment');
    }
  };

  return (
    <div className="p-6 max-w-3xl">
      {!draft ? (
        <div className="bg-white border border-gray-200 rounded-lg p-6">
          <div className="text-sm font-semibold text-gray-900">No draft generated yet</div>
          <div className="text-xs text-gray-500 mt-1">
            Choose which active email template to use, then generate the draft. Variables such as company name and discrepancies are filled in automatically.
          </div>
          <div className="mt-4 space-y-3 max-w-md">
            <div>
              <label className="text-xs font-medium text-gray-500 block mb-1.5">Email template</label>
              <select
                value={selectedTemplateName}
                onChange={(e) => setSelectedTemplateName(e.target.value)}
                disabled={loading || templateNameOptions.length === 0}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:opacity-60"
              >
                {templateNameOptions.length === 0 ? (
                  <option value="">No active templates</option>
                ) : (
                  templateNameOptions.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))
                )}
              </select>
              {templateNameOptions.length === 0 ? (
                <p className="text-xs text-amber-700 mt-1.5">
                  There are no active email templates. Create and activate one under Email Templates.
                </p>
              ) : null}
            </div>
            <div>
              <button
                disabled={loading || !selectedTemplateName}
                onClick={() => generateDraft(false)}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg font-medium text-sm bg-blue-500 text-white hover:bg-blue-600 transition-all disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
              >
                <FileText className="h-3.5 w-3.5" /> Generate Draft
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {draft ? (
        <>
          <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-800">
            <span className="text-slate-500">Template used for this draft</span>
            <span className="mx-2 text-slate-300">·</span>
            <span className="font-semibold text-slate-900">{draft.template_name ?? '—'}</span>
          </div>

          <div className="mb-4 max-w-2xl">
            <label className="text-xs font-medium text-gray-500 block mb-1.5">Template for regenerate</label>
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={selectedTemplateName}
                onChange={(e) => setSelectedTemplateName(e.target.value)}
                disabled={loading || templateNameOptions.length === 0}
                className="min-w-[12rem] flex-1 max-w-md px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:opacity-60"
              >
                {templateNameOptions.length === 0 ? (
                  <option value="">No active templates</option>
                ) : (
                  templateNameOptions.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))
                )}
              </select>
              {regenerateSelectionDiffersFromDraft ? (
                <button
                  type="button"
                  disabled={loading || !selectedTemplateName}
                  onClick={() => generateDraft(true)}
                  className="inline-flex shrink-0 items-center gap-1.5 px-3 py-2 rounded-lg font-medium text-sm bg-blue-600 text-white hover:bg-blue-700 transition-all disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                  title="Regenerate draft using the selected template"
                >
                  <RefreshCcw className="h-3.5 w-3.5" /> Regenerate
                </button>
              ) : null}
            </div>
            <p className="text-[11px] text-gray-500 mt-1">
              Pick another template and use Regenerate beside this field to switch. Regenerate replaces subject, body, and
              recipients from the template; attachments are cleared.
            </p>
          </div>

          <div className="grid grid-cols-1 gap-4 mb-3">
            <div className="bg-blue-50 rounded-lg p-4 min-h-[100px] border-2 border-dashed border-transparent">
              <div className="flex items-start space-x-3">
                <div className="flex-1 min-w-0">
                  <h4 className="text-sm font-medium text-blue-800 mb-2">To</h4>
                  <div className="flex flex-wrap gap-1 mb-2">
                    {toEmails.map((recipient, index) => (
                      <span
                        key={`${recipient}-${index}`}
                        className="inline-flex items-center px-2.5 py-1 bg-blue-200 text-blue-800 text-xs font-medium rounded-full border border-blue-300 transition-all"
                      >
                        {recipient}
                        {toEmails.length > 1 ? (
                          <button
                            type="button"
                            onClick={() => setToEmails((prev) => prev.filter((_, i) => i !== index))}
                            className="ml-1 text-blue-600 hover:text-blue-800"
                            disabled={loading}
                          >
                            <X size={12} />
                          </button>
                        ) : null}
                      </span>
                    ))}
                  </div>

                  {showToInput ? (
                    <div className="flex items-center gap-1 relative z-10">
                      <input
                        type="email"
                        value={newToEmail}
                        onChange={(e) => setNewToEmail(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            const v = newToEmail.trim();
                            if (!v) return;
                            setToEmails((prev) => (prev.includes(v) ? prev : [...prev, v]));
                            setNewToEmail('');
                            setShowToInput(false);
                          }
                          if (e.key === 'Escape') {
                            setNewToEmail('');
                            setShowToInput(false);
                          }
                        }}
                        placeholder="Enter email address"
                        className="px-2 py-1 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-transparent bg-white"
                        autoFocus
                        disabled={loading}
                      />
                      <button
                        type="button"
                        onClick={() => {
                          const v = newToEmail.trim();
                          if (!v) return;
                          setToEmails((prev) => (prev.includes(v) ? prev : [...prev, v]));
                          setNewToEmail('');
                          setShowToInput(false);
                        }}
                        className="p-1 text-green-600 hover:text-green-800"
                        disabled={loading}
                        title="Add"
                      >
                        <Plus size={12} />
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setShowToInput(false);
                          setNewToEmail('');
                        }}
                        className="p-1 text-gray-400 hover:text-gray-600"
                        disabled={loading}
                        title="Cancel"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setShowToInput(true)}
                      className="text-xs text-blue-600 hover:text-blue-800"
                      disabled={loading}
                    >
                      + Add recipient
                    </button>
                  )}
                </div>
              </div>
            </div>

            <div className="bg-gray-50 rounded-lg p-4 min-h-[100px] border-2 border-dashed border-transparent">
              <div className="flex items-start space-x-3">
                <div className="flex-1 min-w-0">
                  <h4 className="text-sm font-medium text-blue-800 mb-2">CC</h4>
                  <div className="flex flex-wrap gap-1 mb-2">
                    {ccEmails.map((ccemail, index) => (
                      <span
                        key={`${ccemail}-${index}`}
                        className="inline-flex items-center px-2.5 py-1 bg-gray-200 text-gray-800 text-xs font-medium rounded-full border border-gray-300 transition-all"
                      >
                        {ccemail}
                        <button
                          type="button"
                          onClick={() => setCcEmails((prev) => prev.filter((_, i) => i !== index))}
                          className="ml-1 text-gray-600 hover:text-gray-800"
                          disabled={loading}
                        >
                          <X size={12} />
                        </button>
                      </span>
                    ))}
                  </div>

                  {showCcInput ? (
                    <div className="flex items-center gap-1 relative z-10">
                      <input
                        type="email"
                        value={newCcEmail}
                        onChange={(e) => setNewCcEmail(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            const v = newCcEmail.trim();
                            if (!v) return;
                            setCcEmails((prev) => (prev.includes(v) ? prev : [...prev, v]));
                            setNewCcEmail('');
                            setShowCcInput(false);
                          }
                          if (e.key === 'Escape') {
                            setNewCcEmail('');
                            setShowCcInput(false);
                          }
                        }}
                        placeholder="Enter CC email address"
                        className="px-2 py-1 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-transparent bg-white"
                        autoFocus
                        disabled={loading}
                      />
                      <button
                        type="button"
                        onClick={() => {
                          const v = newCcEmail.trim();
                          if (!v) return;
                          setCcEmails((prev) => (prev.includes(v) ? prev : [...prev, v]));
                          setNewCcEmail('');
                          setShowCcInput(false);
                        }}
                        className="p-1 text-green-600 hover:text-green-800"
                        disabled={loading}
                        title="Add"
                      >
                        <Plus size={12} />
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setShowCcInput(false);
                          setNewCcEmail('');
                        }}
                        className="p-1 text-gray-400 hover:text-gray-600"
                        disabled={loading}
                        title="Cancel"
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setShowCcInput(true)}
                      className="text-xs text-blue-600 hover:text-blue-800"
                      disabled={loading}
                    >
                      + Add CC
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className="mb-3">
            <label className="text-xs font-medium text-gray-500 block mb-1.5">Subject</label>
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              placeholder="Email subject..."
              disabled={loading}
            />
          </div>

          <div className="mb-4">
            <label className="text-xs font-medium text-gray-500 block mb-1.5">Body</label>
            <div className={loading ? 'opacity-60 pointer-events-none' : ''}>
              <JoditEditor
                value={body}
                onBlur={(next: string) => setBody(next)}
                onChange={() => {}}
                config={joditConfig}
              />
            </div>
          </div>

          <div className="mb-4">
            <label className="text-xs font-medium text-gray-500 block mb-1.5">Attachments</label>
            <div className="flex items-center gap-2">
              <label className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg font-medium text-xs border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all cursor-pointer">
                <Paperclip className="h-3.5 w-3.5" />
                Choose files
                <input
                  type="file"
                  multiple
                  className="hidden"
                  disabled={loading}
                  onChange={(e) => {
                    const files = Array.from(e.target.files || []);
                    if (files.length) setNewFiles((prev) => [...prev, ...files]);
                    e.currentTarget.value = '';
                  }}
                />
              </label>
              <div className="text-xs text-gray-500">
                {attachmentKeys.length + newFiles.length} file{attachmentKeys.length + newFiles.length === 1 ? '' : 's'}
              </div>
            </div>

            {(attachmentKeys.length > 0 || newFiles.length > 0) ? (
              <div className="mt-2 space-y-1">
                {attachmentKeys.map((k) => {
                  const name = String(k).split('/').pop() || k;
                  return (
                    <div key={k} className="flex items-center justify-between gap-2 p-2 bg-gray-50 border border-gray-200 rounded-lg">
                      <button
                        type="button"
                        onClick={() => openAttachment(k)}
                        className="text-xs text-blue-700 hover:text-blue-900 truncate"
                        title={k}
                      >
                        {name}
                      </button>
                      <button
                        type="button"
                        disabled={loading}
                        onClick={() => setAttachmentKeys((prev) => prev.filter((x) => x !== k))}
                        className="p-1 text-gray-500 hover:text-gray-900"
                        title="Remove"
                      >
                        <X size={14} />
                      </button>
                    </div>
                  );
                })}
                {newFiles.map((f, idx) => (
                  <div
                    key={`${f.name}-${f.size}-${idx}`}
                    className="flex items-center justify-between gap-2 p-2 bg-white border border-dashed border-gray-300 rounded-lg"
                  >
                    <div className="text-xs text-gray-700 truncate" title={f.name}>
                      {f.name} <span className="text-gray-400">(pending upload)</span>
                    </div>
                    <button
                      type="button"
                      disabled={loading}
                      onClick={() => setNewFiles((prev) => prev.filter((_, i) => i !== idx))}
                      className="p-1 text-gray-500 hover:text-gray-900"
                      title="Remove"
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            ) : null}
          </div>

          <div className="flex gap-2">
            <button
              disabled={loading}
              onClick={saveDraft}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg font-medium text-sm bg-blue-500 text-white hover:bg-blue-600 transition-all disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
            >
              <Save className="h-3.5 w-3.5" /> Save Changes
            </button>
            <button
              disabled={loading}
              onClick={() => setConfirmSendOpen(true)}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg font-medium text-sm bg-emerald-600 text-white hover:bg-emerald-700 transition-all disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-emerald-600"
              title="Queue send in background"
            >
              <Send className="h-3.5 w-3.5" /> Send Email
            </button>
            {!regenerateSelectionDiffersFromDraft ? (
              <button
                type="button"
                disabled={loading || !selectedTemplateName}
                onClick={() => generateDraft(true)}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg font-medium text-sm border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-500"
                title="Regenerate from the current template (overwrites your edits)"
              >
                <RefreshCcw className="h-3.5 w-3.5" /> Regenerate Draft
              </button>
            ) : null}
          </div>
        </>
      ) : null}

      <AlertDialog open={confirmSendOpen} onOpenChange={setConfirmSendOpen}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-gray-900">Send this email?</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-500">
              This will queue the email to be sent in the background. The page will return immediately, but the email will be delivered shortly.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-gray-300 text-gray-700 hover:bg-gray-50" disabled={loading}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                setConfirmSendOpen(false);
                await sendDraft();
              }}
              disabled={loading}
              className="bg-emerald-600 text-white hover:bg-emerald-700"
            >
              {loading ? 'Queuing…' : 'Send'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

