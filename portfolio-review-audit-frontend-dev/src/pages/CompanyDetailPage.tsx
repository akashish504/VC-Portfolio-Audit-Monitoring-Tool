import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Building2 } from 'lucide-react';
import { toast } from 'sonner';

import type { CompanyReviewStage } from '@/constants/statusEnums';
import { getPortfolioCompany, getPortfolioCompanyByCompanyId } from '@/api/portfolio';
import { fetchReviewCycles } from '@/api/reviewCycleAdjustments';
import { pickDefaultCycle } from '@/api/dashboard';
import { CompanyStageConfirmDialog } from '@/components/company/CompanyStageConfirmDialog';
import { applyCompanyStageChange, type CompanyStageConfirmState } from '@/components/company/companyStageChange';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { CompanyOrgChart } from '@/components/company/CompanyOrgChart';
import { DiscrepancyDashboard } from '@/components/company/DiscrepancyDashboard';
import { CompanyEmailThreads } from '@/components/company/CompanyEmailThreads';
import { CompanyEmailDraft } from '@/components/company/CompanyEmailDraft';
import { CompanyAuditLogs } from '@/components/company/CompanyAuditLogs';
import { CompanyFiles } from '@/components/company/CompanyFiles';
import { CompanyQualitativeReport } from '@/components/company/CompanyQualitativeReport';
import {
  normalizeReviewStage,
  reviewStageDisplayLabel,
} from '@/constants/auditStatus';

export default function CompanyDetailPage() {
  const { companyId } = useParams<{ companyId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const locationState = location.state as { tab?: string; hasEntities?: boolean } | undefined;
  const rawTab = locationState?.tab;
  /** When navigated from an entity-less placeholder row, restrict to org-chart only. */
  const entitiesExist = locationState?.hasEntities !== false;
  /** Map removed tabs to the new unified discrepancy dashboard. */
  const initialTab =
    rawTab === 'snowflake-data' || rawTab === 'financials' ? 'discrepancies' : rawTab;
  const [activeTab, setActiveTab] = useState<string>(initialTab || 'org-chart');
  const [pendingStage, setPendingStage] = useState<CompanyStageConfirmState | null>(null);
  const [savingStage, setSavingStage] = useState(false);
  const [company, setCompany] = useState<{
    id: number;
    companyId: string;
    name: string;
    stage: CompanyReviewStage;
    contactName?: string;
    reviewCycleId?: string | null;
    fyEnd?: string | null;
    fyEndDate?: string | null;
    hasOrphanFiles?: boolean;
  } | null>(null);
  const [rcCycles, setRcCycles] = useState<{ id: string; label: string; createdAt: string }[]>([]);
  const [selectedCycleId, setSelectedCycleId] = useState<string | null>(null);
  /** Stable business company id, used to re-resolve the per-cycle portfolio company when the cycle changes. */
  const [businessId, setBusinessId] = useState<string | null>(null);
  /** The cycle selection (id, or '' for "all/latest") currently reflected in `company`; guards the refetch effect against loops. */
  const [loadedCycleKey, setLoadedCycleKey] = useState<string | null>(null);
  /** True while swapping `company` to a different cycle's row. */
  const [cycleSwitching, setCycleSwitching] = useState(false);
  /** True when the company has no portfolio record in the selected cycle. */
  const [cycleMissing, setCycleMissing] = useState(false);
  const [loading, setLoading] = useState(true);

  const mapCompany = (c: Awaited<ReturnType<typeof getPortfolioCompany>>) => ({
    id: c.id,
    companyId: c.company_id,
    name: c.name,
    stage: normalizeReviewStage(c.review_stage),
    contactName: c.contact_name || undefined,
    reviewCycleId: c.review_cycle_id ?? null,
    fyEnd: c.fy_end ?? null,
    fyEndDate: c.fy_end_date ?? null,
    hasOrphanFiles: c.has_orphan_files ?? false,
  });

  useEffect(() => {
    if (initialTab) setActiveTab(initialTab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialTab]);

  useEffect(() => {
    const load = async () => {
      if (!companyId) return;
      setLoading(true);
      try {
        const isNumeric = /^[0-9]+$/.test(companyId);
        const c = isNumeric ? await getPortfolioCompany(Number(companyId)) : await getPortfolioCompanyByCompanyId(companyId);
        setCompany(mapCompany(c));
        setBusinessId(c.company_id);
        setCycleMissing(false);
        // The company's own cycle is the authoritative initial selection; prefer it over any
        // default the (concurrent) cycle-list effect may have already set.
        const initialCycle = c.review_cycle_id ?? null;
        setSelectedCycleId((prev) => initialCycle ?? prev ?? null);
        // Record which cycle selection the freshly-loaded company reflects so the
        // refetch effect doesn't immediately re-load the same row.
        setLoadedCycleKey(initialCycle);
      } catch {
        setCompany(null);
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [companyId]);


  useEffect(() => {
    const loadCycles = async () => {
      try {
        const cycles = await fetchReviewCycles();
        setRcCycles(cycles);
        // If company had no assigned cycle, fall back to the default (last completed) cycle.
        setSelectedCycleId((prev) => prev ?? pickDefaultCycle(cycles) ?? null);
      } catch {
        // ignore
      }
    };
    void loadCycles();
  }, []);

  // When the user picks a different review cycle, re-resolve the portfolio company for
  // that (company, cycle) pair. Each cycle is a distinct portfolio_company row, so this
  // swaps `company.id` and every tab (keyed on it) re-fetches the cycle's data.
  useEffect(() => {
    if (!businessId) return;
    const key = selectedCycleId ?? '';
    if (key === (loadedCycleKey ?? '')) return;
    let cancelled = false;
    setCycleSwitching(true);
    setCycleMissing(false);
    getPortfolioCompanyByCompanyId(businessId, selectedCycleId || undefined)
      .then((c) => {
        if (cancelled) return;
        setCompany(mapCompany(c));
      })
      .catch(() => {
        if (cancelled) return;
        // No portfolio record exists for this company in the chosen cycle.
        // Keep the previous `company` so the header + cycle dropdown stay rendered
        // (letting the user switch back); the body shows an empty-state instead.
        setCycleMissing(true);
      })
      .finally(() => {
        if (cancelled) return;
        setLoadedCycleKey(selectedCycleId ?? null);
        setCycleSwitching(false);
      });
    return () => {
      cancelled = true;
    };
  }, [businessId, selectedCycleId, loadedCycleKey]);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <p className="text-sm text-gray-500">Loading…</p>
      </div>
    );
  }

  if (!company) {
    return (
      <div className="h-full flex items-center justify-center">
        <p className="text-sm text-gray-500">Company not found</p>
      </div>
    );
  }

  const confirmStageChange = async () => {
    if (!pendingStage || !company) return;
    const snapshot = pendingStage;
    setPendingStage(null);
    setSavingStage(true);
    try {
      await applyCompanyStageChange(snapshot);
      setCompany((prev) => (prev ? { ...prev, stage: normalizeReviewStage(snapshot.to) } : prev));
      toast.success(`Stage updated to "${reviewStageDisplayLabel(snapshot.to)}"`);
    } catch {
      toast.error('Failed to update stage');
    } finally {
      setSavingStage(false);
    }
  };

  const from = (location.state as any)?.from as { kind?: string; fileId?: string } | undefined;
  const backLabel = from?.kind === 'file' && from.fileId ? 'Back to File' : 'Back to Portfolio';
  const backAction = () => {
    if (from?.kind === 'file' && from.fileId) {
      navigate(`/file-tagging/${from.fileId}`);
      return;
    }
    navigate('/');
  };

  return (
    <div className="h-full flex flex-col overflow-hidden relative">
      <div className="px-6 py-4 border-b border-gray-200 bg-white shrink-0">
        <button onClick={backAction} className="text-xs text-gray-500 hover:text-gray-900 flex items-center gap-1 mb-2 transition-all">
          <ArrowLeft className="h-3 w-3" /> {backLabel}
        </button>
        {from?.kind === 'file' && from.fileId ? (
          <div className="mb-3 text-[11px] text-gray-500">
            You came from a file view. Click <span className="font-medium text-gray-700">{backLabel}</span> to return.
          </div>
        ) : null}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Building2 className="h-5 w-5 text-blue-500" />
            <div>
              <h1 className="text-lg font-bold text-gray-900">{company.name}</h1>
              <div className="flex items-center gap-3 mt-1">
                {company.contactName && <span className="text-xs text-gray-500">Contact: {company.contactName}</span>}
              </div>
            </div>
          </div>

          {rcCycles.length > 0 && (
            <div className="flex flex-col items-end gap-1">
              <span className="text-[10px] text-gray-400 uppercase tracking-wider">
                Review Cycle{cycleSwitching ? ' · loading…' : ''}
              </span>
              <select
                value={selectedCycleId ?? ''}
                onChange={(e) => setSelectedCycleId(e.target.value || null)}
                disabled={cycleSwitching}
                className="px-3 py-1.5 border border-blue-200 rounded-lg text-xs font-medium text-blue-700 bg-blue-50 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400 cursor-pointer disabled:opacity-60 disabled:cursor-wait"
              >
                <option value="" disabled>Select cycle</option>
                {rcCycles.map((c) => (
                  <option key={c.id} value={c.id}>{c.label}</option>
                ))}
              </select>
            </div>
          )}
        </div>
      </div>

      <CompanyStageConfirmDialog
        open={!!pendingStage}
        state={pendingStage}
        loading={savingStage}
        onOpenChange={(open) => {
          if (!open) setPendingStage(null);
        }}
        onConfirm={confirmStageChange}
      />

      {cycleMissing ? (
        <div className="flex-1 flex items-center justify-center bg-gray-50 px-6 text-center">
          <p className="text-sm text-gray-500">
            {company.name} has no data for the{' '}
            <span className="font-medium text-gray-700">
              {rcCycles.find((c) => c.id === selectedCycleId)?.label ?? 'selected'}
            </span>{' '}
            review cycle. Pick a different cycle above.
          </p>
        </div>
      ) : (
      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col overflow-hidden">
        <div className="px-6 pt-3 pb-3 bg-white shrink-0">
          {!entitiesExist && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 mb-2">
              No entities found for this company. Upload an org chart or create entities below to begin the review process.
            </p>
          )}
          <TabsList className="bg-gray-100 rounded-lg p-1 h-auto flex flex-wrap gap-1">
            {(entitiesExist
              ? [
                  { value: 'org-chart', label: 'Org Chart' },
                  { value: 'files', label: 'Files' },
                  { value: 'qualitative-report', label: 'Compliance Section' },
                  { value: 'discrepancies', label: 'Discrepancy Dashboard' },
                  { value: 'email-draft', label: 'Email Draft & Sending' },
                  { value: 'email-threads', label: 'Email Threads' },
                  { value: 'audit-logs', label: 'Audit Logs' },
                ]
              : [{ value: 'org-chart', label: 'Org Chart' }]
            ).map((tab) => (
              <TabsTrigger
                key={tab.value}
                value={tab.value}
                className="rounded-md px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700 data-[state=active]:bg-white data-[state=active]:shadow-sm data-[state=active]:text-gray-900"
              >
                <span className="relative inline-flex items-center gap-1">
                  {tab.label}
                  {tab.value === 'files' && company.hasOrphanFiles && (
                    <span className="absolute -top-1 -right-2.5 h-2 w-2 rounded-full bg-red-500" />
                  )}
                </span>
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        <div className="flex-1 overflow-auto bg-gray-50">
          <TabsContent value="org-chart" className="h-full mt-0">
            <CompanyOrgChart companyId={company.id} companyName={company.name} />
          </TabsContent>
          <TabsContent value="discrepancies" className="h-full mt-0">
            <DiscrepancyDashboard companyId={company.id} />
          </TabsContent>
          <TabsContent value="email-draft" className="h-full mt-0">
            <CompanyEmailDraft companyId={company.id} />
          </TabsContent>
          <TabsContent value="email-threads" className="h-full mt-0">
            <CompanyEmailThreads companyId={company.id} />
          </TabsContent>
          <TabsContent value="files" className="h-full mt-0">
            <CompanyFiles
              companyId={company.id}
              companyName={company.name}
              reviewCycleId={company.reviewCycleId}
              initialFyEnd={company.fyEnd}
              initialFyEndDate={company.fyEndDate}
              onOrphanResolved={() => {
                setCompany((prev) => prev ? { ...prev, hasOrphanFiles: false } : prev);
              }}
            />
          </TabsContent>
          <TabsContent value="qualitative-report" className="h-full mt-0">
            <CompanyQualitativeReport companyId={company.id} />
          </TabsContent>
          <TabsContent value="audit-logs" className="h-full mt-0">
            <CompanyAuditLogs companyId={String(company.id)} />
          </TabsContent>
        </div>
      </Tabs>
      )}
    </div>
  );
}

