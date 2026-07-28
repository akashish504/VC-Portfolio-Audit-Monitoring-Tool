import { useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle, ChevronDown, ChevronRight, CopyPlus, History, Loader2, Pencil, Plus, Save, X } from 'lucide-react';
import { toast } from 'sonner';
import _JoditEditor from 'jodit-react';
import DOMPurify from 'dompurify';
import 'jodit/es5/jodit.min.css';

const JoditEditor: any = (_JoditEditor as any).default ?? _JoditEditor;

import {
  activateEmailTemplate,
  createEmailTemplate,
  listAllEmailTemplateVersions,
  listEmailTemplateHistory,
  updateEmailTemplateVersion,
  type ApiEmailTemplateHistory,
} from '@/api/emailTemplates';

type TemplateVersion = {
  id: string;
  templateName: string;
  versionName: string;
  versionId: string;
  subject: string;
  body: string;
  isActive: boolean;
  createdAt: string;
  updatedAt?: string | null;
};

function apiErrorMessage(e: unknown): string {
  const err = e as { response?: { data?: { detail?: unknown } } };
  const d = err?.response?.data?.detail;
  if (typeof d === 'string') return d;
  if (d && typeof d === 'object' && 'message' in d && typeof (d as { message: unknown }).message === 'string') {
    return (d as { message: string }).message;
  }
  return 'Something went wrong';
}

export default function EmailTemplatesPage() {
  const [versions, setVersions] = useState<TemplateVersion[]>([]);
  const [expandedVersions, setExpandedVersions] = useState<Record<string, boolean>>({});
  const [expandedTemplates, setExpandedTemplates] = useState<Record<string, boolean>>({});

  const [editing, setEditing] = useState<{
    templateName: string;
    mode: 'new' | 'edit';
    isNewTemplate: boolean;
    baseVersion?: TemplateVersion | null;
  } | null>(null);

  const [newTemplateNameInput, setNewTemplateNameInput] = useState('');
  const [editVersionName, setEditVersionName] = useState('');
  const [editSubject, setEditSubject] = useState('');
  const [editBody, setEditBody] = useState('');
  const [activeEditor, setActiveEditor] = useState<'subject' | 'body'>('body');

  const [historyCache, setHistoryCache] = useState<Record<string, ApiEmailTemplateHistory[] | undefined>>({});
  const [historyLoading, setHistoryLoading] = useState<Record<string, boolean>>({});
  const [historyLoadFailed, setHistoryLoadFailed] = useState<Record<string, boolean>>({});

  const subjectRef = useRef<HTMLInputElement | null>(null);
  const joditRef = useRef<JoditEditor | null>(null);

  const dynamicVariables = useMemo(
    () => [
      { key: 'company_name', label: 'company_name' },
      { key: 'fy_year', label: 'fy_year' },
      { key: 'poc_name', label: 'poc_name' },
      { key: 'financials', label: 'financials' },
    ],
    [],
  );

  const joditConfig = useMemo(
    () => ({
      readonly: false,
      height: 300,
      placeholder: 'Type here...',
      toolbarSticky: false,
      toolbarAdaptive: false,
      askBeforePasteHTML: false,
      askBeforePasteFromWord: false,
      defaultActionOnPaste: 'insert_as_html' as const,
      enableDragAndDropFileToEditor: true,
      image: {
        openOnDblClick: false,
        editSrc: false,
        useImageEditor: false,
      },
      uploader: {
        insertImageAsBase64URI: true,
        imagesExtensions: ['jpg', 'png', 'jpeg', 'gif', 'svg', 'webp'],
      },
    }),
    [],
  );

  const formatDate = (dateString: string) => {
    const d = new Date(dateString);
    if (Number.isNaN(d.getTime())) return dateString;
    return d.toLocaleString([], {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const eventLabel = (event: string) => {
    if (event === 'template_created') return 'Created';
    if (event === 'template_updated') return 'Updated';
    if (event === 'template_activated') return 'Activated';
    return event;
  };

  const insertVariable = (key: string) => {
    const token = `{{${key}}}`;
    if (activeEditor === 'subject') {
      const el = subjectRef.current;
      if (!el) return;
      const start = el.selectionStart ?? editSubject.length;
      const end = el.selectionEnd ?? editSubject.length;
      const next = editSubject.slice(0, start) + token + editSubject.slice(end);
      setEditSubject(next);
      requestAnimationFrame(() => {
        el.focus();
        const pos = start + token.length;
        el.setSelectionRange(pos, pos);
      });
      return;
    }

    const editor = (joditRef.current as any)?.editor;
    if (editor?.selection?.insertHTML) {
      editor.selection.insertHTML(token);
      return;
    }
    setEditBody((prev) => `${prev}${token}`);
  };

  const refresh = async () => {
    const rows = await listAllEmailTemplateVersions();
    const next: TemplateVersion[] = rows
      .filter((r) => !!r.id && !!r.template_name && !!r.version_id)
      .map((r) => ({
        id: r.id,
        templateName: r.template_name || 'Untitled',
        versionName: r.version_name || 'Unnamed Version',
        versionId: r.version_id || '',
        subject: r.subject || '',
        body: r.body || '',
        isActive: Boolean(r.is_active),
        createdAt: r.created_at,
        updatedAt: r.updated_at,
      }))
      .sort((a, b) => (a.createdAt > b.createdAt ? -1 : a.createdAt < b.createdAt ? 1 : 0));
    setVersions(next);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (cancelled) return;
        await refresh();
      } catch {
        toast.error('Failed to load templates');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const openNewTemplateEditor = () => {
    setEditing({ templateName: '', mode: 'new', isNewTemplate: true, baseVersion: null });
    setNewTemplateNameInput('');
    setEditVersionName('Initial version');
    setEditSubject('');
    setEditBody('');
  };

  const openNewVersionEditor = (templateName: string, baseVersion?: TemplateVersion | null) => {
    setEditing({ templateName, mode: 'new', isNewTemplate: false, baseVersion: baseVersion ?? null });
    setEditVersionName(baseVersion ? `${baseVersion.versionName} - Copy` : 'New Version');
    setEditSubject(baseVersion?.subject ?? '');
    setEditBody(baseVersion?.body ?? '');
  };

  const openEditVersionEditor = (templateName: string, version: TemplateVersion) => {
    setEditing({ templateName, mode: 'edit', isNewTemplate: false, baseVersion: version });
    setEditVersionName(version.versionName);
    setEditSubject(version.subject);
    setEditBody(version.body);
  };

  const saveEdit = () => {
    if (!editing) return;
    (async () => {
      try {
        if (editing.mode === 'edit') {
          if (!editing.baseVersion?.id) throw new Error('Missing template id');
          await updateEmailTemplateVersion(editing.baseVersion.id, {
            subject: editSubject,
            body: editBody,
            version_name: editVersionName,
          });
          toast.success('Version updated');
        } else if (editing.isNewTemplate) {
          const name = newTemplateNameInput.trim();
          if (!name) {
            toast.error('Template name is required');
            return;
          }
          await createEmailTemplate({
            template_name: name,
            subject: editSubject,
            body: editBody,
            version_name: editVersionName,
          });
          toast.success('Template created');
        } else {
          await createEmailTemplate({
            template_name: editing.templateName,
            subject: editSubject,
            body: editBody,
            version_name: editVersionName,
          });
          toast.success('Version saved');
        }
        await refresh();
        setEditing(null);
      } catch (e: unknown) {
        toast.error(apiErrorMessage(e));
      }
    })();
  };

  const loadHistoryForRow = async (templateRowId: string) => {
    if (historyCache[templateRowId] !== undefined || historyLoading[templateRowId]) return;
    setHistoryLoading((prev) => ({ ...prev, [templateRowId]: true }));
    setHistoryLoadFailed((prev) => ({ ...prev, [templateRowId]: false }));
    try {
      const rows = await listEmailTemplateHistory(templateRowId);
      setHistoryCache((prev) => ({ ...prev, [templateRowId]: rows }));
    } catch {
      toast.error('Failed to load activity history');
      setHistoryLoadFailed((prev) => ({ ...prev, [templateRowId]: true }));
      setHistoryCache((prev) => ({ ...prev, [templateRowId]: [] }));
    } finally {
      setHistoryLoading((prev) => ({ ...prev, [templateRowId]: false }));
    }
  };

  const toggleVersionExpanded = (v: TemplateVersion) => {
    setExpandedVersions((prev) => {
      const nextOpen = !prev[v.id];
      if (nextOpen) void loadHistoryForRow(v.id);
      return { ...prev, [v.id]: nextOpen };
    });
  };

  const templateGroups = useMemo(() => {
    const map = new Map<string, TemplateVersion[]>();
    for (const v of versions) {
      if (!map.has(v.templateName)) map.set(v.templateName, []);
      map.get(v.templateName)!.push(v);
    }
    const groups = Array.from(map.entries()).map(([templateName, items]) => ({
      templateName,
      versions: items.sort((a, b) => (a.createdAt > b.createdAt ? -1 : a.createdAt < b.createdAt ? 1 : 0)),
    }));
    groups.sort((a, b) => a.templateName.localeCompare(b.templateName));
    return groups;
  }, [versions]);

  const editorTitle = () => {
    if (!editing) return '';
    if (editing.mode === 'edit') return 'Edit version';
    if (editing.isNewTemplate) return 'New template';
    if (editing.baseVersion) return 'Duplicate version';
    return 'New version';
  };

  const saveButtonLabel = () => {
    if (!editing) return 'Save';
    if (editing.mode === 'edit') return 'Save changes';
    if (editing.isNewTemplate) return 'Create template';
    return 'Save version';
  };

  return (
    <div className="h-full overflow-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Email Templates</h1>
          <p className="text-xs text-gray-500 mt-1">Predefined templates for audit communications</p>
        </div>
        <button
          type="button"
          onClick={openNewTemplateEditor}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg font-medium text-sm bg-blue-500 text-white hover:bg-blue-600 transition-all focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
        >
          <Plus className="h-4 w-4" /> New template
        </button>
      </div>

      {editing ? (
        <div className="mb-6 bg-white border-2 border-blue-200 rounded-lg shadow-sm">
          <div className="p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm font-semibold text-gray-900">{editorTitle()}</div>
              <button
                type="button"
                onClick={() => setEditing(null)}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg font-medium text-sm border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-gray-500"
              >
                <X className="h-3.5 w-3.5" /> Close
              </button>
            </div>

            {editing.isNewTemplate ? (
              <div>
                <label className="text-xs font-medium text-gray-500 block mb-1">Template name</label>
                <input
                  value={newTemplateNameInput}
                  onChange={(e) => setNewTemplateNameInput(e.target.value)}
                  placeholder="e.g. Quarterly follow-up"
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
              </div>
            ) : null}

            <div className="flex gap-3">
              <div className="flex-1">
                <label className="text-xs font-medium text-gray-500 block mb-1">Version name</label>
                <input
                  value={editVersionName}
                  onChange={(e) => setEditVersionName(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
              </div>
            </div>

            <div>
              <label className="text-xs font-medium text-gray-500 block mb-1">Subject</label>
              <input
                value={editSubject}
                onChange={(e) => setEditSubject(e.target.value)}
                ref={subjectRef}
                onFocus={() => setActiveEditor('subject')}
                className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>

            <div>
              <label className="text-xs font-medium text-gray-500 block mb-1">Body</label>
              <div onFocus={() => setActiveEditor('body')}>
                <JoditEditor
                  key={`${editing?.baseVersion?.id ?? 'new'}-${editing?.mode}`}
                  ref={joditRef}
                  value={editBody}
                  onBlur={(next: string) => setEditBody(next)}
                  onChange={() => {}}
                  config={joditConfig}
                />
              </div>
            </div>

            <div className="pt-1">
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs font-medium text-gray-500">Dynamic variables</div>
                <div className="text-[11px] text-gray-400">Insert into {activeEditor === 'subject' ? 'Subject' : 'Body'}</div>
              </div>
              <div className="flex flex-wrap gap-2">
                {dynamicVariables.map((dv) => (
                  <button
                    key={dv.key}
                    type="button"
                    onClick={() => insertVariable(dv.key)}
                    className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium border border-gray-200 bg-gray-50 text-gray-700 hover:bg-gray-100 transition-all font-mono"
                  >
                    {`{{${dv.label}}}`}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex gap-2 pt-1">
              <button
                type="button"
                onClick={saveEdit}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg font-medium text-sm bg-blue-500 text-white hover:bg-blue-600 transition-all focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
              >
                <Save className="h-3.5 w-3.5" /> {saveButtonLabel()}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="space-y-4">
        {templateGroups.length === 0 ? (
          <div className="bg-white border border-gray-200 rounded-lg shadow-sm p-8 text-center text-sm text-gray-600">
            No email templates yet. Use <span className="font-medium text-gray-900">New template</span> above to create one.
          </div>
        ) : null}
        {templateGroups.map((g) => {
          const active = g.versions.find((v) => v.isActive);
          const lastEdited = g.versions
            .map((v) => v.updatedAt || v.createdAt)
            .sort((a, b) => (a > b ? -1 : a < b ? 1 : 0))[0];
          const isTemplateExpanded = expandedTemplates[g.templateName] ?? true;
          return (
            <div key={g.templateName} className="bg-white border border-gray-200 rounded-lg shadow-sm">
              <div
                className="p-4 border-b border-gray-100 flex items-center justify-between cursor-pointer hover:bg-gray-50 transition-colors"
                onClick={() =>
                  setExpandedTemplates((prev) => ({
                    ...prev,
                    [g.templateName]: !(prev[g.templateName] ?? true),
                  }))
                }
              >
                <div className="flex items-center gap-3 min-w-0">
                  <div className="text-gray-400 flex-shrink-0">
                    {isTemplateExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-gray-900">{g.templateName}</div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      Active version: <span className="font-mono text-gray-900">{active?.versionName ?? '—'}</span>
                    </div>
                    <div className="text-xs text-gray-500 mt-0.5">
                      Last edited: <span className="font-mono text-gray-700">{lastEdited ? formatDate(lastEdited) : '—'}</span>
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    openNewVersionEditor(g.templateName, null);
                  }}
                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg font-medium text-sm bg-blue-500 text-white hover:bg-blue-600 transition-all focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                >
                  <Plus className="h-3.5 w-3.5" /> New Version
                </button>
              </div>

              {isTemplateExpanded ? (
                <div className="divide-y divide-gray-100">
                  {g.versions.map((v) => {
                    const isExpanded = Boolean(expandedVersions[v.id]);
                    const hist = historyCache[v.id];
                    const histLoading = historyLoading[v.id];
                    return (
                      <div key={v.id} className="hover:bg-gray-50 transition-colors">
                        <div
                          className="p-4 cursor-pointer"
                          onClick={() => toggleVersionExpanded(v)}
                        >
                          <div className="flex items-center justify-between gap-4">
                            <div className="flex items-center gap-3 min-w-0">
                              <div className="text-gray-400">{isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</div>
                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <div className="font-semibold text-gray-900">{v.versionName}</div>
                                  {v.isActive ? (
                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-100 text-green-800 text-xs font-medium rounded-full">
                                      <CheckCircle size={10} />
                                      Active
                                    </span>
                                  ) : null}
                                </div>
                                <div className="text-xs text-gray-500 mt-0.5">
                                  Created: <span className="font-mono text-gray-700">{formatDate(v.createdAt)}</span>
                                </div>
                                <div className="text-xs text-gray-500 mt-0.5">
                                  Last edited:{' '}
                                  <span className="font-mono text-gray-700">{formatDate(v.updatedAt || v.createdAt)}</span>
                                </div>
                              </div>
                            </div>

                            <div className="flex items-center gap-2">
                              {!v.isActive ? (
                                <button
                                  type="button"
                                  onClick={async (e) => {
                                    e.stopPropagation();
                                    try {
                                      await activateEmailTemplate({ template_name: g.templateName, version_id: v.versionId });
                                      setVersions((prev) =>
                                        prev.map((x) =>
                                          x.templateName === g.templateName ? { ...x, isActive: x.id === v.id } : x,
                                        ),
                                      );
                                      toast.success('Template activated');
                                    } catch {
                                      toast.error('Failed to activate template');
                                    }
                                  }}
                                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors"
                                >
                                  Mark Active
                                </button>
                              ) : null}
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  openEditVersionEditor(g.templateName, v);
                                }}
                                className="p-1.5 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded transition-colors"
                                title="Edit this version"
                              >
                                <Pencil size={18} />
                              </button>
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  openNewVersionEditor(g.templateName, v);
                                }}
                                className="p-1.5 text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded transition-colors"
                                title="Duplicate as new version"
                              >
                                <CopyPlus size={18} />
                              </button>
                            </div>
                          </div>
                        </div>

                        {isExpanded ? (
                          <div className="px-4 pb-4">
                            <div className="text-xs text-gray-500 mb-1">
                              Subject: <span className="font-mono text-gray-900">{v.subject || '—'}</span>
                            </div>
                            <div
                              className="text-xs text-gray-700 mt-2 bg-gray-50 rounded-lg p-3 leading-relaxed border border-gray-100"
                              dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(v.body || '—') }}
                            />

                            <div className="mt-4 border border-gray-100 rounded-lg bg-white overflow-hidden">
                              <div className="px-3 py-2 border-b border-gray-100 flex items-center gap-2 text-xs font-semibold text-gray-700">
                                <History className="h-3.5 w-3.5 text-gray-400" />
                                Activity (this version)
                              </div>
                              <div className="p-3">
                                {histLoading ? (
                                  <div className="flex items-center gap-2 text-xs text-gray-500">
                                    <Loader2 className="h-4 w-4 animate-spin" /> Loading…
                                  </div>
                                ) : historyLoadFailed[v.id] ? (
                                  <p className="text-xs text-gray-500">Activity could not be loaded.</p>
                                ) : hist && hist.length > 0 ? (
                                  <ul className="space-y-2 text-xs text-gray-700">
                                    {hist.map((h) => (
                                      <li key={h.id} className="flex flex-col gap-0.5 border-b border-gray-50 last:border-0 pb-2 last:pb-0">
                                        <div className="flex items-center justify-between gap-2">
                                          <span className="font-medium text-gray-900">{eventLabel(h.event)}</span>
                                          <span className="text-gray-400 shrink-0">{formatDate(h.created_at)}</span>
                                        </div>
                                        {h.meta && typeof h.meta === 'object' && 'version_name' in h.meta ? (
                                          <span className="text-gray-500 font-mono text-[11px]">
                                            {(h.meta as { version_name?: string }).version_name ?? ''}
                                          </span>
                                        ) : null}
                                      </li>
                                    ))}
                                  </ul>
                                ) : (
                                  <p className="text-xs text-gray-500">No recorded events for this row.</p>
                                )}
                              </div>
                            </div>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
