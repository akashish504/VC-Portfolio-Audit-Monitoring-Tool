import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Download, Pencil, Plus } from 'lucide-react';
import { toast } from 'sonner';

import { createDiscrepancy, generateDiscrepancies, listDiscrepancies, listEntities, patchDiscrepancy, type ApiDiscrepancyRow } from '@/api/portfolio';
import { varianceCategoriesForDiscrepancyType } from '@/constants/discrepancyVarianceCategories';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
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
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { Entity } from '@/types/domain';

const STATUS_OPTIONS: DiscrepancyStatus[] = ['Open', 'Under Review', 'Resolved', 'Dismissed'];

const statusBadge: Record<DiscrepancyStatus, string> = {
  Open: 'bg-red-100 text-red-800',
  'Under Review': 'bg-yellow-100 text-yellow-800',
  Resolved: 'bg-green-100 text-green-800',
  Dismissed: 'bg-gray-100 text-gray-500',
};

const enabledBadge = (enabled: boolean) => (enabled ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-500');

type DiscrepancyStatus = 'Open' | 'Under Review' | 'Resolved' | 'Dismissed';

interface DiscrepancyItem {
  id: number;
  portfolio_company_id: number;
  entity_id: number | null;
  /** API ``category``: ``financial`` (system-generated) or ``manual``. */
  category: string;
  discrepancyType: string;
  discrepancyText: string;
  enabled: boolean;
  discrepancyStatus: DiscrepancyStatus;
  l1_reviewer_remarks: string;
  l2_reviewer_remarks: string;
  highlighted_to_investor: boolean;
  varianceCategory: string | null;
  created_at: string;
  updated_at: string;
}

function normalizeDiscrepancyStatus(s: string | null | undefined): DiscrepancyStatus {
  const v = (s ?? '').trim();
  if (!v) return 'Open';
  const lower = v.toLowerCase();
  if (lower === 'open') return 'Open';
  if (lower === 'under review') return 'Under Review';
  if (lower === 'resolved') return 'Resolved';
  if (lower === 'dismissed') return 'Dismissed';
  if (STATUS_OPTIONS.includes(v as DiscrepancyStatus)) return v as DiscrepancyStatus;
  return 'Open';
}

function toUi(row: ApiDiscrepancyRow): DiscrepancyItem {
  return {
    id: row.id,
    portfolio_company_id: row.portfolio_company_id,
    entity_id: row.entity_id ?? null,
    category: row.category,
    discrepancyType: row.type ?? '',
    discrepancyText: row.discrepency_text,
    enabled: row.enable,
    discrepancyStatus: normalizeDiscrepancyStatus(row.status),
    l1_reviewer_remarks: row.l1_reviewer_remarks ?? '',
    l2_reviewer_remarks: row.l2_reviewer_remarks ?? '',
    highlighted_to_investor: row.highlighted_to_investor ?? false,
    varianceCategory: row.variance_category ?? null,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

const NULL_ENTITY_KEY = '__none__';

export function CompanyDiscrepancies({ companyId }: { companyId: number }) {
  const [loading, setLoading] = useState(true);
  const [items, setItems] = useState<DiscrepancyItem[]>([]);
  const [entities, setEntities] = useState<Entity[]>([]);
  const [generating, setGenerating] = useState(false);

  const entityMap = useMemo<Record<number, string>>(() => {
    const m: Record<number, string> = {};
    for (const e of entities) m[e.id] = e.name;
    return m;
  }, [entities]);

  const refresh = async () => {
    setLoading(true);
    try {
      const page = await listDiscrepancies({ portfolio_company_id: companyId, limit: 500, offset: 0 });
      setItems((page.items ?? []).map(toUi));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    void listEntities({ portfolio_company_id: companyId, limit: 200, offset: 0 })
      .then((res) => setEntities(res.items ?? []))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId]);

  /** Group discrepancies by entity_id; null entity last. */
  const grouped = useMemo<Array<{ key: string; label: string; rows: DiscrepancyItem[] }>>(() => {
    const groups = new Map<string, { label: string; rows: DiscrepancyItem[] }>();
    for (const item of items) {
      const key = item.entity_id !== null ? String(item.entity_id) : NULL_ENTITY_KEY;
      if (!groups.has(key)) {
        const label =
          item.entity_id !== null
            ? (entityMap[item.entity_id] ?? `Entity ${item.entity_id}`)
            : 'Company-level (no entity)';
        groups.set(key, { label, rows: [] });
      }
      groups.get(key)!.rows.push(item);
    }
    // sort: real entities first (by label), null last
    const entries = [...groups.entries()].sort(([aKey, aVal], [bKey, bVal]) => {
      if (aKey === NULL_ENTITY_KEY) return 1;
      if (bKey === NULL_ENTITY_KEY) return -1;
      return aVal.label.localeCompare(bVal.label);
    });
    return entries.map(([key, { label, rows }]) => ({ key, label, rows }));
  }, [items, entityMap]);

  const [editingItem, setEditingItem] = useState<DiscrepancyItem | null>(null);
  const [editForm, setEditForm] = useState({
    enabled: true,
    l1_reviewer_remarks: '',
    l2_reviewer_remarks: '',
    highlighted_to_investor: false,
    discrepancyType: '',
    discrepancyText: '',
    discrepancyStatus: 'Open' as DiscrepancyStatus,
    varianceCategory: '',
  });
  const [pendingToggle, setPendingToggle] = useState<{ id: number; newValue: boolean } | null>(null);
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [addForm, setAddForm] = useState({
    discrepancyType: '',
    discrepancyText: '',
    entityId: '',
    l1_reviewer_remarks: '',
    l2_reviewer_remarks: '',
    highlighted_to_investor: false,
  });

  const openEdit = (item: DiscrepancyItem) => {
    setEditForm({
      enabled: item.enabled,
      l1_reviewer_remarks: item.l1_reviewer_remarks,
      l2_reviewer_remarks: item.l2_reviewer_remarks,
      highlighted_to_investor: item.highlighted_to_investor,
      discrepancyType: item.discrepancyType,
      discrepancyText: item.discrepancyText,
      discrepancyStatus: item.discrepancyStatus,
      varianceCategory: item.varianceCategory ?? '',
    });
    setEditingItem(item);
  };

  const saveEdit = () => {
    if (!editingItem) return;
    const payload: Parameters<typeof patchDiscrepancy>[1] = {
      enable: editForm.enabled,
      l1_reviewer_remarks: editForm.l1_reviewer_remarks.trim() || null,
      l2_reviewer_remarks: editForm.l2_reviewer_remarks.trim() || null,
      highlighted_to_investor: editForm.highlighted_to_investor,
      type: editForm.discrepancyType || null,
      discrepency_text: editForm.discrepancyText,
      status: editForm.discrepancyStatus,
    };
    if (editingItem.category !== 'manual') {
      payload.variance_category = editForm.varianceCategory.trim() || null;
    }
    void patchDiscrepancy(editingItem.id, payload)
      .then(() => refresh())
      .then(() => toast.success('Discrepancy updated'))
      .catch(() => toast.error('Failed to update discrepancy'))
      .finally(() => setEditingItem(null));
  };

  const confirmToggle = () => {
    if (!pendingToggle) return;
    void patchDiscrepancy(pendingToggle.id, { enable: pendingToggle.newValue })
      .then(() => refresh())
      .then(() => toast.success(`Discrepancy ${pendingToggle.newValue ? 'enabled' : 'disabled'}`))
      .catch(() => toast.error('Failed to update discrepancy'))
      .finally(() => setPendingToggle(null));
  };

  const handleAddManual = () => {
    if (!addForm.discrepancyType.trim() || !addForm.discrepancyText.trim()) {
      toast.error('Type and query text are required');
      return;
    }
    if (!addForm.entityId) {
      toast.error('Please select an entity');
      return;
    }
    void createDiscrepancy({
      portfolio_company_id: companyId,
      entity_id: Number(addForm.entityId),
      category: 'manual',
      type: addForm.discrepancyType.trim(),
      discrepency_text: addForm.discrepancyText.trim(),
      status: 'Open',
      enable: true,
      l1_reviewer_remarks: addForm.l1_reviewer_remarks.trim() || null,
      l2_reviewer_remarks: addForm.l2_reviewer_remarks.trim() || null,
      highlighted_to_investor: addForm.highlighted_to_investor,
    })
      .then(() => refresh())
      .then(() => toast.success('Manual discrepancy added'))
      .catch(() => toast.error('Failed to add discrepancy'))
      .finally(() => {
        setAddForm({
          discrepancyType: '',
          discrepancyText: '',
          entityId: '',
          l1_reviewer_remarks: '',
          l2_reviewer_remarks: '',
          highlighted_to_investor: false,
        });
        setShowAddDialog(false);
      });
  };

  const handleDownloadExcel = () => {
    const header = ['Query', 'Type', 'To Be Sent?', 'Status', 'L1 Remarks', 'L2 Remarks', 'Highlighted to investor', 'Category'];
    const rows = items.map((item) =>
      [
        item.discrepancyText,
        item.discrepancyType,
        item.enabled ? 'Yes' : 'No',
        item.discrepancyStatus,
        item.l1_reviewer_remarks ?? '',
        item.l2_reviewer_remarks ?? '',
        item.highlighted_to_investor ? 'Yes' : 'No',
        item.category === 'manual' ? '(n/a)' : item.varianceCategory ?? '',
      ].join(','),
    );
    const csv = [header.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `discrepancies-${companyId}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const COL_SPAN = 6;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <p className="text-xs text-gray-500">
          {loading ? 'Loading…' : `${items.length} discrepanc${items.length === 1 ? 'y' : 'ies'} found`}
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={async () => {
              try {
                setGenerating(true);
                const resp = await generateDiscrepancies(companyId);
                await refresh();
                const n = typeof resp?.created === 'number' ? resp.created : 0;
                toast.success(n > 0 ? `Generated ${n} discrepancies` : 'No new discrepancies (no qualifying variances or missing paired data).');
              } catch (e) {
                let msg = '';
                if (e && typeof e === 'object' && 'response' in e) {
                  const d = (e as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
                  if (typeof d === 'string') msg = d;
                  else if (Array.isArray(d)) msg = d.map((x) => (typeof x === 'object' && x && 'msg' in x ? String((x as { msg: string }).msg) : String(x))).join('; ');
                }
                toast.error(msg || (e instanceof Error ? e.message : 'Failed to generate discrepancies'));
              } finally {
                setGenerating(false);
              }
            }}
            disabled={generating}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
          >
            Generate discrepancies
          </button>
          <button
            onClick={handleDownloadExcel}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all"
          >
            <Download className="h-4 w-4" /> Download Excel
          </button>
          <button
            onClick={() => setShowAddDialog(true)}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-blue-500 text-white hover:bg-blue-600 transition-all"
          >
            <Plus className="h-4 w-4" /> Add Investor Query
          </button>
        </div>
      </div>

      {!loading && items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 gap-2 bg-white rounded-lg border border-gray-200">
          <AlertTriangle className="h-8 w-8 text-gray-300" />
          <p className="text-sm text-gray-400">No discrepancies found</p>
        </div>
      ) : (
        <div className="overflow-x-auto bg-white rounded-lg border border-gray-200 shadow-sm">
          <table className="w-full">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider w-[38%]">Query</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Type</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">To Be Sent?</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider min-w-[14rem]">Category</th>
                <th className="text-center px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider w-16">Edit</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {grouped.map((group) => (
                <>
                  <tr key={`group-${group.key}`} className="bg-blue-50 border-t border-blue-100">
                    <td
                      colSpan={COL_SPAN}
                      className="px-4 py-2 text-xs font-semibold text-blue-700 uppercase tracking-wider"
                    >
                      {group.label}
                      <span className="ml-2 text-blue-400 font-normal normal-case tracking-normal">
                        ({group.rows.length} {group.rows.length === 1 ? 'discrepancy' : 'discrepancies'})
                      </span>
                    </td>
                  </tr>
                  {group.rows.map((item) => (
                    <tr key={item.id} className="hover:bg-gray-50 transition-colors">
                      <td className="px-4 py-4 text-sm text-gray-900 leading-relaxed">{item.discrepancyText}</td>
                      <td className="px-4 py-4 text-sm text-gray-500">{item.discrepancyType}</td>
                      <td className="px-4 py-4">
                        <select
                          value={item.enabled ? 'Yes' : 'No'}
                          onChange={(e) => {
                            const newValue = e.target.value === 'Yes';
                            if (newValue !== item.enabled) setPendingToggle({ id: item.id, newValue });
                          }}
                          className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium cursor-pointer border-none focus:outline-none focus:ring-2 focus:ring-blue-500 ${enabledBadge(item.enabled)}`}
                        >
                          <option value="Yes">Yes</option>
                          <option value="No">No</option>
                        </select>
                      </td>
                      <td className="px-4 py-4">
                        <select
                          value={item.discrepancyStatus}
                          onChange={(e) => {
                            const newStatus = e.target.value as DiscrepancyStatus;
                            void patchDiscrepancy(item.id, { status: newStatus })
                              .then(() => refresh())
                              .then(() => toast.success(`Status updated to "${newStatus}"`))
                              .catch(() => toast.error('Failed to update status'));
                          }}
                          className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium cursor-pointer border-none focus:outline-none focus:ring-2 focus:ring-blue-500 ${statusBadge[item.discrepancyStatus]}`}
                        >
                          {STATUS_OPTIONS.map((s) => (
                            <option key={s} value={s}>
                              {s.toUpperCase()}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-4 text-sm">
                        {item.category === 'manual' ? (
                          <span className="text-xs text-gray-400" title="Categories apply to system-generated financial discrepancies only">
                            —
                          </span>
                        ) : (
                          (() => {
                            const base = varianceCategoriesForDiscrepancyType(item.discrepancyType);
                            const opts =
                              item.varianceCategory && !base.includes(item.varianceCategory)
                                ? [item.varianceCategory, ...base]
                                : [...base];
                            if (opts.length === 0) {
                              return (
                                <span className="text-xs text-gray-400" title="Unknown metric type — add standard type slug to map categories">
                                  —
                                </span>
                              );
                            }
                            return (
                              <select
                                value={item.varianceCategory ?? ''}
                                onChange={(e) => {
                                  const v = e.target.value.trim() || null;
                                  void patchDiscrepancy(item.id, { variance_category: v })
                                    .then(() => refresh())
                                    .then(() => toast.success('Category saved'))
                                    .catch(() => toast.error('Failed to save category'));
                                }}
                                className="w-full max-w-[min(100%,20rem)] text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white text-gray-900"
                              >
                                <option value="">Select category…</option>
                                {opts.map((o) => (
                                  <option key={o} value={o}>
                                    {o}
                                  </option>
                                ))}
                              </select>
                            );
                          })()
                        )}
                      </td>
                      <td className="px-4 py-4 text-center">
                        <button
                          onClick={() => openEdit(item)}
                          className="text-gray-400 hover:text-blue-600 transition-colors"
                          title={`Edit — reviewer remarks & investor highlight. Source: ${item.category === 'manual' ? 'Manual' : 'System (financial)'}`}
                        >
                          <Pencil className="h-4 w-4" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <AlertDialog open={!!pendingToggle} onOpenChange={(open) => !open && setPendingToggle(null)}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle className="text-gray-900">Confirm Change</AlertDialogTitle>
            <AlertDialogDescription className="text-gray-500">
              Are you sure you want to {pendingToggle?.newValue ? 'enable' : 'disable'} this discrepancy?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="border-gray-300 text-gray-700 hover:bg-gray-50">No</AlertDialogCancel>
            <AlertDialogAction onClick={confirmToggle} className="bg-blue-500 text-white hover:bg-blue-600">
              Yes
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={!!editingItem} onOpenChange={(open) => !open && setEditingItem(null)}>
        <DialogContent className="bg-white rounded-lg p-6 w-full max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold text-gray-900">Edit Discrepancy</DialogTitle>
            <DialogDescription className="text-gray-500">
              Update discrepancy details and status.
            </DialogDescription>
          </DialogHeader>
          {editingItem && (
            <form
              className="mt-2"
              onSubmit={(e) => {
                e.preventDefault();
                saveEdit();
              }}
            >
              <div className="max-h-[min(70vh,36rem)] overflow-y-auto space-y-4 pr-1">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm text-gray-900 font-medium">{editingItem.discrepancyType || 'Discrepancy'}</div>
                  <div className="text-xs text-gray-500">Source: {editingItem.category === 'manual' ? 'Manual' : 'System (financial)'}</div>
                </div>
                {editingItem.entity_id !== null && entityMap[editingItem.entity_id] && (
                  <div className="text-xs text-gray-500">
                    Entity: <span className="font-medium text-gray-700">{entityMap[editingItem.entity_id]}</span>
                  </div>
                )}

                <div>
                  <Label className="text-xs uppercase tracking-wider text-gray-500">Type</Label>
                  <Input
                    value={editForm.discrepancyType}
                    onChange={(e) => setEditForm((prev) => ({ ...prev, discrepancyType: e.target.value }))}
                    placeholder="e.g. COGS, EBITDA"
                    className="mt-1"
                  />
                </div>

                <div>
                  <Label className="text-xs uppercase tracking-wider text-gray-500">Category</Label>
                  {editingItem.category === 'manual' ? (
                    <p className="mt-1 text-xs text-gray-400">Not applicable for manual investor queries.</p>
                  ) : (
                    (() => {
                      const base = varianceCategoriesForDiscrepancyType(editForm.discrepancyType);
                      const chosen = editForm.varianceCategory;
                      const opts =
                        chosen && !base.includes(chosen) ? [chosen, ...base] : [...base];
                      if (opts.length === 0) {
                        return (
                          <p
                            className="mt-1 text-xs text-gray-400"
                            title="Unknown metric type — use a slug such as revenue, ebitda, pbt, pat, cash, or debt."
                          >
                            No preset categories for this type.
                          </p>
                        );
                      }
                      return (
                        <select
                          value={editForm.varianceCategory}
                          onChange={(e) => setEditForm((prev) => ({ ...prev, varianceCategory: e.target.value }))}
                          className="mt-1 w-full text-sm border border-gray-300 rounded-lg px-3 py-2 bg-white text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
                        >
                          <option value="">Select category…</option>
                          {opts.map((o) => (
                            <option key={o} value={o}>
                              {o}
                            </option>
                          ))}
                        </select>
                      );
                    })()
                  )}
                </div>

                <div>
                  <Label className="text-xs uppercase tracking-wider text-gray-500">Query Text</Label>
                  <textarea
                    value={editForm.discrepancyText}
                    onChange={(e) => setEditForm((prev) => ({ ...prev, discrepancyText: e.target.value }))}
                    rows={3}
                    className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    placeholder="Describe the discrepancy..."
                  />
                </div>

                <fieldset className="rounded-lg border border-gray-200 bg-gray-50/50 p-4 space-y-4">
                  <legend className="text-xs font-semibold uppercase tracking-wider text-gray-600 px-1">Reviewer</legend>

                  <div className="flex items-start gap-3 rounded-lg border border-gray-200 bg-white p-3">
                    <Checkbox
                      id="edit-highlighted-investor"
                      checked={editForm.highlighted_to_investor}
                      onCheckedChange={(v) =>
                        setEditForm((prev) => ({ ...prev, highlighted_to_investor: v === true }))
                      }
                    />
                    <div>
                      <Label htmlFor="edit-highlighted-investor" className="text-sm font-medium text-gray-900 cursor-pointer">
                        Highlighted to investor
                      </Label>
                      <p className="text-xs text-gray-500 mt-0.5">Call this item out to the investor when relevant.</p>
                    </div>
                  </div>

                  <div>
                    <Label htmlFor="edit-l1-remarks" className="text-xs uppercase tracking-wider text-gray-500">
                      L1 reviewer remarks
                    </Label>
                    <textarea
                      id="edit-l1-remarks"
                      name="l1_reviewer_remarks"
                      value={editForm.l1_reviewer_remarks}
                      onChange={(e) => setEditForm((prev) => ({ ...prev, l1_reviewer_remarks: e.target.value }))}
                      rows={2}
                      className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                      placeholder="L1 reviewer notes…"
                    />
                  </div>

                  <div>
                    <Label htmlFor="edit-l2-remarks" className="text-xs uppercase tracking-wider text-gray-500">
                      L2 reviewer remarks
                    </Label>
                    <textarea
                      id="edit-l2-remarks"
                      name="l2_reviewer_remarks"
                      value={editForm.l2_reviewer_remarks}
                      onChange={(e) => setEditForm((prev) => ({ ...prev, l2_reviewer_remarks: e.target.value }))}
                      rows={2}
                      className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                      placeholder="L2 reviewer notes…"
                    />
                  </div>
                </fieldset>

                <div>
                  <Label className="text-xs uppercase tracking-wider text-gray-500">Status</Label>
                  <Select value={editForm.discrepancyStatus} onValueChange={(v) => setEditForm((prev) => ({ ...prev, discrepancyStatus: v as DiscrepancyStatus }))}>
                    <SelectTrigger className="mt-1">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {STATUS_OPTIONS.map((s) => (
                        <SelectItem key={s} value={s}>
                          {s}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <DialogFooter className="mt-4 gap-2 sm:gap-0">
                <button
                  type="button"
                  onClick={() => setEditingItem(null)}
                  className="px-4 py-2 rounded-lg font-medium text-sm border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 rounded-lg font-medium text-sm bg-blue-500 text-white hover:bg-blue-600 transition-all"
                >
                  Save
                </button>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
        <DialogContent className="bg-white rounded-lg p-6 w-full max-w-lg">
          <DialogHeader>
            <DialogTitle className="text-lg font-bold text-gray-900">Add Investor Query</DialogTitle>
            <DialogDescription className="text-gray-500">
              Create a manual discrepancy that will not be deleted by regeneration.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label className="text-xs uppercase tracking-wider text-gray-500">
                Entity <span className="text-red-500">*</span>
              </Label>
              <select
                value={addForm.entityId}
                onChange={(e) => setAddForm((prev) => ({ ...prev, entityId: e.target.value }))}
                className="mt-1 w-full text-sm border border-gray-300 rounded-lg px-3 py-2 bg-white text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">Select entity…</option>
                {entities.map((e) => (
                  <option key={e.id} value={String(e.id)}>
                    {e.name}
                  </option>
                ))}
              </select>
              {entities.length === 0 && (
                <p className="mt-1 text-xs text-amber-600">No entities found. Add entities in the Org Chart tab first.</p>
              )}
            </div>

            <div>
              <Label className="text-xs uppercase tracking-wider text-gray-500">
                Type <span className="text-red-500">*</span>
              </Label>
              <Input value={addForm.discrepancyType} onChange={(e) => setAddForm((prev) => ({ ...prev, discrepancyType: e.target.value }))} placeholder="e.g. COGS, EBITDA, Revenue" className="mt-1" />
            </div>
            <div>
              <Label className="text-xs uppercase tracking-wider text-gray-500">
                Query Text <span className="text-red-500">*</span>
              </Label>
              <textarea
                value={addForm.discrepancyText}
                onChange={(e) => setAddForm((prev) => ({ ...prev, discrepancyText: e.target.value }))}
                rows={3}
                className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Describe the discrepancy..."
              />
            </div>

            <div className="flex items-start gap-3 rounded-lg border border-gray-200 p-3">
              <Checkbox
                id="add-highlighted-investor"
                checked={addForm.highlighted_to_investor}
                onCheckedChange={(v) => setAddForm((prev) => ({ ...prev, highlighted_to_investor: v === true }))}
              />
              <div>
                <Label htmlFor="add-highlighted-investor" className="text-sm font-medium text-gray-900 cursor-pointer">
                  Highlighted to investor
                </Label>
                <p className="text-xs text-gray-500 mt-0.5">Optional — call out to investor when relevant.</p>
              </div>
            </div>

            <div>
              <Label className="text-xs uppercase tracking-wider text-gray-500">L1 reviewer remarks</Label>
              <textarea
                value={addForm.l1_reviewer_remarks}
                onChange={(e) => setAddForm((prev) => ({ ...prev, l1_reviewer_remarks: e.target.value }))}
                rows={2}
                className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Optional"
              />
            </div>

            <div>
              <Label className="text-xs uppercase tracking-wider text-gray-500">L2 reviewer remarks</Label>
              <textarea
                value={addForm.l2_reviewer_remarks}
                onChange={(e) => setAddForm((prev) => ({ ...prev, l2_reviewer_remarks: e.target.value }))}
                rows={2}
                className="mt-1 w-full px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Optional"
              />
            </div>
          </div>
          <DialogFooter className="mt-4">
            <button onClick={() => setShowAddDialog(false)} className="px-4 py-2 rounded-lg font-medium text-sm border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-all">
              Cancel
            </button>
            <button onClick={handleAddManual} className="px-4 py-2 rounded-lg font-medium text-sm bg-blue-500 text-white hover:bg-blue-600 transition-all">
              Add Query
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
