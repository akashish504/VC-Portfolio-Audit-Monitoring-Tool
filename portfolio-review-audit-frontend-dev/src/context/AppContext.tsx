import React, { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import type { ReviewCompanyEntry, ReviewCycle, ReviewCycleLog, ReviewStage } from '@/types/reviewCycle';
import {
  clearReviewCyclesCache,
  deleteReviewCycleApi,
  fetchReviewCycles,
} from '@/api/reviewCycleAdjustments';
import { listPortfolioCompanies, patchPortfolioCompany } from '@/api/portfolio';
import { listReviewCycleAudit } from '@/api/audit';
import { listParameterThresholds } from '@/api/settings';
import { getFinancialParameterLabel, isConfigurableFinancialThresholdKey } from '@/constants/financialParameterLabels';
import { companies as mockCompanies, reconciliationData, type Company, type EmailThread, type ReconciliationField } from '@/data/mockData';

interface AppState {
  // Mock/demo state (used by Pipeline/Workspace/Communications + CompanyFinancials)
  companies: Company[];
  updateCompanyStatus: (companyId: string, status: Company['status']) => void;
  emails: EmailThread[];
  addEmail: (email: EmailThread) => void;

  fieldThresholds: Record<string, number>;
  absoluteThresholds: Record<string, number>;

  reconciliationDataState: Record<string, ReconciliationField[]>;
  updateReconciliationValue: (
    portfolioCompanyId: number,
    entityId: string,
    fieldName: string,
    column: 'Source_Value' | 'Extracted_Value',
    value: number,
  ) => void;

  selectedCompanyId: string | null;
  setSelectedCompanyId: (id: string | null) => void;
  rcCycles: ReviewCycle[];
  rcEntries: ReviewCompanyEntry[];
  rcLogs: ReviewCycleLog[];
  rcDataLoading: boolean;
  rcDataError: string | null;
  refreshReviewCycleData: () => Promise<void>;
  ensureReviewCycleDataLoaded: () => Promise<void>;
  addOrUpdateRCEntries: (
    cycleId: string,
    entries: Omit<ReviewCompanyEntry, 'id' | 'reviewCycleId' | 'updatedAt'>[],
  ) => Promise<void>;
  updateRCEntryStage: (entryId: string, stage: ReviewStage) => Promise<void>;
  deleteReviewCycle: (cycleId: string) => Promise<void>;
}

const AppContext = createContext<AppState | null>(null);

export const useAppState = () => {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useAppState must be used within AppProvider');
  return ctx;
};

export const AppProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  // ---- Mock/demo state ----
  const [companies, setCompanies] = useState<Company[]>(mockCompanies);
  const [emails, setEmails] = useState<EmailThread[]>([]);
  const [fieldThresholds, setFieldThresholds] = useState<Record<string, number>>({});
  const [absoluteThresholds, setAbsoluteThresholds] = useState<Record<string, number>>({});
  const [reconciliationDataState, setReconciliationDataState] = useState<Record<string, ReconciliationField[]>>(
    reconciliationData,
  );

  const [rcCycles, setRcCycles] = useState<ReviewCycle[]>([]);
  const [rcEntries, setRcEntries] = useState<ReviewCompanyEntry[]>([]);
  const [rcLogs, setRcLogs] = useState<ReviewCycleLog[]>([]);
  const [rcDataLoading, setRcDataLoading] = useState(true);
  const [rcDataError, setRcDataError] = useState<string | null>(null);
  const [selectedCompanyId, setSelectedCompanyId] = useState<string | null>(null);
  const rcLoadedOnceRef = useRef(false);
  const rcInFlightRef = useRef<Promise<void> | null>(null);

  const refreshReviewCycleData = useCallback(async () => {
    clearReviewCyclesCache();
    setRcDataLoading(true);
    setRcDataError(null);
    try {
      const [cycles, companiesPage, auditPage] = await Promise.all([
        fetchReviewCycles(),
        listPortfolioCompanies({ limit: 500, offset: 0 }),
        listReviewCycleAudit({ limit: 200, offset: 0 }),
      ]);
      setRcCycles(cycles);
      const entries: ReviewCompanyEntry[] = (companiesPage.items ?? [])
        .filter((c) => !!c.review_cycle_id)
        .map((c) => ({
          id: `pc-${c.id}`,
          reviewCycleId: c.review_cycle_id as string,
          updatedAt: new Date().toISOString(),
          portfolioCompanyId: c.id,
          companyName: c.name,
          stage: (c.review_stage as ReviewStage) || 'Not applicable',
          contactName: c.contact_name || '',
          contactEmail: c.contact_email_id || '',
        }));
      setRcEntries(entries);
      const logs: ReviewCycleLog[] = (auditPage.items ?? []).map((r) => ({
        id: r.id,
        action: r.action || 'Audit',
        timestamp: r.occurred_at || r.updated_at,
        user: r.user_id || 'unknown',
        details: r.summary || r.action || '',
        reviewCycleId: r.review_cycle_id || undefined,
      }));
      setRcLogs(logs);
      rcLoadedOnceRef.current = true;
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to load review cycle data';
      setRcDataError(msg);
      console.error(e);
    } finally {
      setRcDataLoading(false);
    }
  }, []);

  const ensureReviewCycleDataLoaded = useCallback(async () => {
    // Prevent repeated calls across route changes/rerenders.
    if (rcLoadedOnceRef.current) return;
    if (rcInFlightRef.current) return rcInFlightRef.current;
    rcInFlightRef.current = (async () => {
      try {
        await refreshReviewCycleData();
      } finally {
        rcInFlightRef.current = null;
      }
    })();
    return rcInFlightRef.current;
  }, [refreshReviewCycleData]);

  useEffect(() => {
    // Metric thresholds loaded for discrepancy / variance tagging (paths stay parameter-* on API).
    const loadThresholds = async () => {
      try {
        const page = await listParameterThresholds({ limit: 500, offset: 0 });
        const nextPct: Record<string, number> = {};
        const nextAbs: Record<string, number> = {};
        const legacyHumanizeKey = (k: string) =>
          String(k || '')
            .trim()
            .replace(/_/g, ' ')
            .replace(/\s+/g, ' ')
            .replace(/\b\w/g, (c) => c.toUpperCase());
        for (const r of page.items ?? []) {
          if (!isConfigurableFinancialThresholdKey(r.key)) continue;
          const v = r.value || {};
          const pct =
            typeof (v as any).percent_threshold === 'number'
              ? (v as any).percent_threshold
              : typeof (v as any).threshold_percent === 'number'
                ? (v as any).threshold_percent / 100
                : 0.005;
          const abs = typeof (v as any).absolute_threshold === 'number' ? (v as any).absolute_threshold : 0;
          nextPct[r.key] = pct;
          nextAbs[r.key] = abs;
          // Backward compatible: allow lookups by legacy humanized labels and current static display labels.
          const legacyLabel = legacyHumanizeKey(r.key);
          const displayLabel = getFinancialParameterLabel(r.key);
          if (!(legacyLabel in nextPct)) nextPct[legacyLabel] = pct;
          if (!(legacyLabel in nextAbs)) nextAbs[legacyLabel] = abs;
          if (displayLabel !== legacyLabel) {
            if (!(displayLabel in nextPct)) nextPct[displayLabel] = pct;
            if (!(displayLabel in nextAbs)) nextAbs[displayLabel] = abs;
          }
        }
        setFieldThresholds(nextPct);
        setAbsoluteThresholds(nextAbs);
      } catch {
        // Keep safe defaults if API isn't available.
        setFieldThresholds({});
        setAbsoluteThresholds({});
      }
    };
    void loadThresholds();
  }, []);

  const addEmail = useCallback((email: EmailThread) => {
    setEmails((prev) => [email, ...prev]);
  }, []);

  const updateCompanyStatus = useCallback((companyId: string, status: Company['status']) => {
    setCompanies((prev) => prev.map((c) => (c.id === companyId ? { ...c, status, entityStatus: status } : c)));
  }, []);

  const updateReconciliationValue = useCallback(
    (
      portfolioCompanyId: number,
      entityId: string,
      fieldName: string,
      column: 'Source_Value' | 'Extracted_Value',
      value: number,
    ) => {
      const key = String(portfolioCompanyId);
      setReconciliationDataState((prev) => {
        const rows = prev[key] ?? [];
        const next = rows.map((r) => {
          const matchesEntity = (r.entityId ?? key) === entityId;
          if (!matchesEntity) return r;
          if (r.Field_Name !== fieldName) return r;
          return { ...r, [column]: value } as ReconciliationField;
        });
        return { ...prev, [key]: next };
      });
    },
    [],
  );

  const addOrUpdateRCEntries = async (
    cycleId: string,
    entries: Omit<ReviewCompanyEntry, 'id' | 'reviewCycleId' | 'updatedAt'>[],
  ) => {
    // Persist stage + cycle assignment on the canonical PortfolioCompany record.
    await Promise.all(
      entries.map((e) =>
        patchPortfolioCompany(e.portfolioCompanyId, {
          review_cycle_id: cycleId,
          review_stage: e.stage,
        }),
      ),
    );
    await refreshReviewCycleData();
  };

  const updateRCEntryStage = async (entryId: string, stage: ReviewStage) => {
    const entry = rcEntries.find((e) => e.id === entryId);
    if (!entry) return;
    await patchPortfolioCompany(entry.portfolioCompanyId, {
      review_stage: stage,
    });
    await refreshReviewCycleData();
  };

  const deleteReviewCycle = async (cycleId: string) => {
    await deleteReviewCycleApi(cycleId);
    // Clear assignment from any companies linked to this cycle.
    const companiesPage = await listPortfolioCompanies({ limit: 500, offset: 0 });
    const toClear = (companiesPage.items ?? []).filter((c) => c.review_cycle_id === cycleId);
    await Promise.all(
      toClear.map((c) =>
        patchPortfolioCompany(c.id, { review_cycle_id: null, review_stage: null }),
      ),
    );
    await refreshReviewCycleData();
  };

  return (
    <AppContext.Provider
      value={{
        companies,
        updateCompanyStatus,
        emails,
        addEmail,
        fieldThresholds,
        absoluteThresholds,
        reconciliationDataState,
        updateReconciliationValue,
        selectedCompanyId,
        setSelectedCompanyId,
        rcCycles,
        rcEntries,
        rcLogs,
        rcDataLoading,
        rcDataError,
        refreshReviewCycleData,
        ensureReviewCycleDataLoaded,
        addOrUpdateRCEntries,
        updateRCEntryStage,
        deleteReviewCycle,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

